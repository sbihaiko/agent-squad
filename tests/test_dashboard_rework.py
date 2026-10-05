"""Issue #134, ADR-0125 amendment: a redo is its own card; the dashboard says which attempt passed and which was superseded, with no edge."""
import re

from tests.dashboard_helpers import ROOT, run_node


def _spawn(n, task, prompt="issues/10-x.md"):
    return {"event": "spawn_accepted", "child": f"route.router.{n}", "envelope": "route", "parent": "router", "depth": 1,
            "definition": {"prompt": f"Work {prompt}", "task": task, "skill": {"name": "tdd", "version": "1"}, "model": "m", "effort": "medium", "after": []}}


def _out(n, outcome="pass"):
    return {"event": "outcome", "node": f"route.router.{n}", "outcome": outcome, "reasons": [], "spend": 0.1, "turns": 3, "elapsed": 5.0}


def _land(n, outcome="pass", reasons=(), held_out=None):
    return {"event": "land", "node": f"route.router.{n}", "outcome": outcome, "reasons": list(reasons), "effect": {"head": "h", "stderr": "CONFLICT", "held_out": held_out}}


def _kids(events):
    return {c["name"]: c for c in run_node([{"op": "children", "events": events}])[0]}


# run 60, issue 10: child 1 passed its call and failed to land (merge_conflict); child 3 redid the issue and passed.
RUN60 = [_spawn(1, "10"), _spawn(2, "11", "issues/11-y.md"), _spawn(3, "10"), _out(1), _land(1, "fail", ["merge_conflict"]), _out(2), _land(2), _out(3), _land(3)]


def test_a_redo_that_passed_reads_passed_after_two_attempts():
    k = _kids(RUN60)["route.router.3"]
    assert (k["attempt"], k["outcome"]) == (2, "pass") and k["rework"] == "passed after 2 attempts"


def test_the_failed_attempt_is_still_failed_and_reads_superseded_by_the_redo_that_passed():
    k = _kids(RUN60)["route.router.1"]
    assert k["outcome"] == "fail" and k["superseded_by"] == 3 and k["rework"] == "superseded by 3 · issue 10, which passed"


def test_a_task_that_passed_first_time_has_no_rework_mark():
    k = _kids(RUN60)["route.router.2"]
    assert (k["attempt"], k["rework"], k["superseded_by"]) == (None, None, None)


def test_a_failed_attempt_is_not_superseded_when_no_later_attempt_passed():
    ev = [_spawn(1, "10"), _spawn(2, "10"), _out(1, "fail"), _out(2, "fail")]
    k = _kids(ev)
    assert k["route.router.1"]["superseded_by"] is None and k["route.router.1"]["rework"] is None
    assert k["route.router.2"]["rework"] == "attempt 2, failed"


def test_a_redo_still_running_reads_attempt_two_not_passed():
    k = _kids([_spawn(1, "10"), _spawn(2, "10"), _out(1, "fail")])["route.router.2"]
    assert k["rework"] == "attempt 2"


def test_the_task_key_names_the_attempts_and_the_prompt_issue_is_the_fallback():
    ev = [_spawn(1, None, "issues/10-x.md"), _spawn(2, None, "issues/10-x.md"), _out(1, "fail"), _out(2)]
    k = _kids(ev)
    assert k["route.router.2"]["rework"] == "passed after 2 attempts" and k["route.router.1"]["superseded_by"] == 2


def test_the_same_task_in_two_dispatches_is_not_one_task():
    a, b = _spawn(1, "10"), {**_spawn(2, "10"), "envelope": "other", "child": "other.router.1"}
    k = _kids([a, b, _out(1, "fail"), {**_out(2), "node": "other.router.1"}])
    assert k["route.router.1"]["superseded_by"] is None and k["other.router.1"]["rework"] is None


def test_a_redo_whose_held_out_test_failed_did_not_pass_and_supersedes_nothing():
    ev = [_spawn(1, "10"), _spawn(2, "10"), _out(1), _land(1, "fail", ["merge_conflict"]), _out(2), _land(2, held_out="fail")]
    k = _kids(ev)
    assert k["route.router.2"]["rework"] == "attempt 2, held-out test failed" and k["route.router.1"]["superseded_by"] is None


def test_an_empty_task_key_falls_back_to_the_prompt_issue_and_never_groups_unrelated_spawns():
    ev = [_spawn(1, "", "issues/10-x.md"), _spawn(2, "", "issues/11-y.md"), _out(1, "fail"), _out(2)]
    k = _kids(ev)
    assert k["route.router.1"]["superseded_by"] is None and k["route.router.2"]["rework"] is None
    ev = [_spawn(1, "", "no issue here"), _spawn(2, "", "none either"), _out(1, "fail"), _out(2)]
    assert all(c["rework"] is None for c in _kids(ev).values())


def test_a_collapsed_failed_group_says_how_many_of_its_children_were_redone():
    ev = [_spawn(n, str(n)) for n in (1, 2, 3)] + [_spawn(n, str(n - 3)) for n in (4, 5)]
    ev += [_out(n, "fail") for n in (1, 2, 3)] + [_out(n) for n in (4, 5)]
    kids = list(_kids(ev).values())
    s = run_node([{"op": "splitkids", "children": kids, "expanded": []}])[0]
    assert [g["name"] for g in s["groups"]] == ["route · 3 failed · 2 redone"]


def test_the_record_gives_no_link_between_attempts_and_the_page_draws_no_edge_for_a_redo():
    k = _kids(RUN60)["route.router.3"]
    assert not [key for key in k if re.search(r"redo|retry|replac", key)]
    graph_js = (ROOT / "dashboard" / "page" / "graph.js").read_text()
    drawn = [line for line in graph_js.splitlines() if "superseded_by" in line or "rework" in line]
    assert drawn and not [line for line in drawn if "<line" in line or "marker-end" in line]
