"""The run dashboard shows each dispatch child's review: verdict, elapsed time, the findings of a held child, and a review that runs now (#599)."""
import re

from tests.dashboard_helpers import ROOT, page_template, run_node


def _spawn(n):
    return {"event": "spawn_accepted", "child": f"route.router.{n}", "envelope": "route", "parent": "router", "depth": 1,
            "definition": {"prompt": "Work issues/10-x.md", "task": str(n), "skill": {"name": "tdd", "version": "1"}, "model": "m", "effort": "medium", "after": []}}


def _out(n):
    return {"event": "outcome", "node": f"route.router.{n}", "outcome": "pass", "reasons": [], "spend": 0.1, "turns": 3, "elapsed": 5.0}


def _review(verdict, elapsed=42.5, findings="", code=0):
    return {"command": ["bash", "review.sh"], "elapsed": elapsed, "exit_code": code, "verdict": verdict, "findings": findings, "stderr": ""}


def _round(n, rnd, review):
    return {"event": "review_round", "node": f"route.router.{n}", "round": rnd, "head": "abc", "verdict": review["verdict"], "offered": False, "review": review}


def _land(n, outcome="pass", reasons=(), review=None):
    return {"event": "land", "node": f"route.router.{n}", "outcome": outcome, "reasons": list(reasons), "effect": {"head": "h", **({"review": review, "rounds": 1} if review else {})}}


def _progress(n, elapsed=60.0):
    return {"event": "review_progress", "node": f"route.router.{n}", "gate": "review_gate", "elapsed": elapsed}


def _kids(events):
    return {c["name"]: c for c in run_node([{"op": "children", "events": events}])[0]}


def test_an_approved_child_shows_its_verdict_and_review_time():
    r = _review("approve")
    k = _kids([_spawn(1), _out(1), _round(1, 1, r), _land(1, review=r)])["route.router.1"]["review"]
    assert (k["verdict"], k["elapsed"], k["live"], k["findings"], k["rounds"]) == ("approve", 42.5, False, "", 1)


def test_a_held_child_shows_the_findings_of_its_review():
    r = _review("hold", 7.0, "blocking: the test misses the empty case", 1)
    k = _kids([_spawn(1), _out(1), _round(1, 1, r), _land(1, "fail", ["review_held"], r)])["route.router.1"]
    assert k["review"]["verdict"] == "hold" and k["review"]["findings"] == "blocking: the test misses the empty case" and k["outcome"] == "fail"


def test_a_review_in_flight_reads_as_live_and_a_closed_round_does_not():
    running = _kids([_spawn(1), _out(1), _progress(1, 120.0)])["route.router.1"]["review"]
    assert running["live"] is True and running["verdict"] is None and running["elapsed"] == 120.0
    closed = _kids([_spawn(1), _out(1), _progress(1), _round(1, 1, _review("approve"))])["route.router.1"]["review"]
    assert closed["live"] is False and closed["verdict"] == "approve"


def test_a_child_with_no_review_has_none_and_the_last_round_wins():
    assert _kids([_spawn(1), _out(1), _land(1)])["route.router.1"]["review"] is None
    fix, ok = _review("fix", 3.0, "rename x", 4), _review("approve", 4.0)
    k = _kids([_spawn(1), _out(1), _round(1, 1, fix), _out(1), _round(1, 2, ok), _land(1, review=ok)])["route.router.1"]["review"]
    assert (k["verdict"], k["rounds"]) == ("approve", 2)


def test_the_child_panel_and_the_run_dashboard_page_show_the_review():
    page = page_template()
    panel = (ROOT / "dashboard" / "page" / "panel.js").read_text()
    assert "c.review" in panel and re.search(r"findings", panel)
    assert "reviewGate" in panel   # the dispatch panel names the review gate, with its command and deadline
    assert "childReviewHtml" in page and "Review gate" in page   # both reach the served page
