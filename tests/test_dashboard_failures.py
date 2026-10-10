"""Ticket #91: the dashboard shows the children of a dispatch and the cause of a failure on the graph.

The fixture is a real run: four waves, four children landed, the final gate failed on three held-out tests (issues 20, 21, 22).
The page only reads the record. A figure the record lacks reads unknown, never zero. Evidence the record lacks reads "no output".
"""
import copy
import json

import pytest

from tests.dashboard_helpers import PAGE_SRC, ROOT, run_node

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"
BOX = {"w": 200, "h": 60}


def _run():
    doc = json.loads((FIX / "graph.json").read_text())
    events = run_node([{"op": "record", "text": (FIX / "run" / "record.jsonl").read_text()}])[0]["events"]
    return doc, events


def _stats(events, doc, **kw):
    return run_node([{"op": "stats", "events": events, "doc": doc, "shown": True, **kw}])[0]


def _child_outcome(events, name):
    return next(e for e in events if e["event"] == "outcome" and e["node"] == name)


# ---- a reason, split into what a person reads
def test_a_reason_splits_into_gate_code_and_the_thing_it_names():
    cmd = "cp /x/acceptance/test_issue_20.py tests/api/test_zz_heldout_20.py && python3 -m pytest -q"
    r = run_node([{"op": "reason", "text": f"ac-verify:ac_failed:{cmd}"}, {"op": "reason", "text": "check:security_gate:sensitive_path:src/auth/login.py"},
                  {"op": "reason", "text": "merge_conflict"}, {"op": "reason", "text": "after_failed:route.router.1"}])
    assert (r[0]["gate"], r[0]["code"], r[0]["subject"], r[0]["detail"]) == ("ac-verify", "ac_failed", "test_issue_20.py", cmd)
    assert (r[1]["gate"], r[1]["code"], r[1]["subject"]) == ("security_gate", "sensitive_path", "src/auth/login.py")
    assert (r[2]["gate"], r[2]["code"], r[2]["subject"]) == (None, "merge_conflict", "")
    assert (r[3]["gate"], r[3]["code"], r[3]["subject"]) == (None, "after_failed", "route.router.1")     # an underscore marks a code, not a check id


def test_a_card_cause_names_each_code_once_with_a_count():
    reasons = ["ac-verify:ac_failed:a.py", "ac-verify:ac_failed:b.py", "ac-verify:ac_failed:c.py", "deadline"]
    assert run_node([{"op": "cause", "reasons": reasons}, {"op": "cause", "reasons": []}]) == ["ac_failed ×3, deadline", None]


# ---- the four children of the real run
def test_the_real_run_shows_four_children_in_request_order_with_what_their_calls_recorded():
    _, events = _run()
    kids = run_node([{"op": "children", "events": events}])[0]
    assert [c["name"] for c in kids] == [f"route.router.{i}" for i in range(1, 5)] and {c["dispatch"] for c in kids} == {"route"}
    k = kids[0]
    assert (k["outcome"], k["model"], k["effort"], k["tokens_in"], k["tokens_out"], k["turns"]) == ("pass", "sonnet", "medium", 295533, 6316, 15)
    assert k["land"]["outcome"] == "pass" and len(k["land"]["head"]) == 40 and k["checks"] == {"scope_gate": "pass", "security_gate": "pass"}
    assert k["tps"] == pytest.approx(6316 / k["elapsed"]) and k["proposer"] == "router" and k["rejected"] is False


def test_a_node_stats_entries_count_is_how_many_times_it_ran_not_just_its_last_outcome():
    """`route` is a dispatch node that ran once per wave: five entries in this fixture. The card shows
    this as `×N` next to elapsed, so a node that ran five times does not look the same as one that ran
    once (found while checking the dashboard with the owner, 2026-10-01). This fixture predates the
    engine recording elapsed on a dispatch node's own outcome, so `elapsed` here stays null; see
    test_engine_skeleton.py and test_dispatch.py for the live engine guard on that field."""
    doc, events = _run()
    nodes = _stats(events, doc)["nodes"]
    assert nodes["route"]["entries"] == 5 and nodes["route"]["outcome"] == "pass"


def test_a_child_with_no_outcome_yet_has_unknown_figures_never_zero():
    _, events = _run()
    cut = [e for e in events if e["pos"] <= 6]       # child 1 accepted and started, no outcome
    k = run_node([{"op": "children", "events": cut}])[0][0]
    assert k["outcome"] == "started" and k["model"] == "sonnet"          # the request names the model before the call reports it
    assert (k["spend"], k["turns"], k["elapsed"], k["tokens_in"], k["tokens_out"], k["tps"], k["land"]) == (None,) * 7


def test_a_failed_landing_fails_the_child_and_its_conflict_text_is_the_evidence():
    _, events = _run()
    ev = copy.deepcopy(events)
    land = next(e for e in ev if e["event"] == "land" and e["node"] == "route.router.3")
    land.update(outcome="fail", reasons=["merge_conflict"], effect={"name": "merge_land", "head": "abc", "stderr": "CONFLICT (content): Merge conflict in billing/api.py"})
    k = run_node([{"op": "children", "events": ev}])[0][2]
    assert k["outcome"] == "fail" and k["reasons"] == ["merge_conflict"] and k["land"]["outcome"] == "fail"
    assert k["evidence"] == {"merge_conflict": "CONFLICT (content): Merge conflict in billing/api.py"}
    causes = run_node([{"op": "causes", "events": ev}])[0]
    c = next(x for x in causes if x["kind"] == "land")
    assert c["node"] == "route.router.3" and c["reasons"][0]["code"] == "merge_conflict" and "billing/api.py" in c["reasons"][0]["evidence"]


def test_a_request_that_code_rejected_shows_as_a_failed_child_with_its_reason():
    _, events = _run()
    ev = copy.deepcopy(events)
    ev.insert(5, {"event": "spawn_rejected", "request_key": 9, "parent": "router", "envelope": "route", "reason": "max_children_over", "reasons": ["max_children_over"], "pos": 5.5, "ts": 1.0})
    kids = run_node([{"op": "children", "events": ev}])[0]
    bad = kids[-1]
    assert bad["rejected"] is True and bad["outcome"] == "fail" and bad["reasons"] == ["max_children_over"] and bad["spend"] is None and bad["dispatch"] == "route"
    assert any(c["kind"] == "rejected" and c["reasons"][0]["code"] == "max_children_over" for c in run_node([{"op": "causes", "events": ev}])[0])


# ---- where the children sit
def test_children_sit_on_free_cells_right_of_their_dispatch_and_the_layout_is_not_touched():
    doc, events = _run()
    kids = run_node([{"op": "children", "events": events}])[0]
    layout = {n["name"]: {"x": 20 + i * 230, "y": 20 + (i % 3) * 96} for i, n in enumerate(doc["nodes"])}
    before = copy.deepcopy(layout)
    pos = run_node([{"op": "clayout", "children": kids, "layout": layout, "box": BOX, "gap": 30}])[0]
    assert set(pos) == {k["name"] for k in kids} and layout == before
    cells = list(layout.values()) + list(pos.values())
    for i, a in enumerate(cells):
        for b in cells[i + 1:]:
            assert abs(a["x"] - b["x"]) >= 230 or abs(a["y"] - b["y"]) >= 90, (a, b)     # no two cards overlap
    assert all(p["x"] > layout["route"]["x"] for p in pos.values())


# ---- the cause on the failed node, and the gates a dispatch checks
def test_the_failed_final_gate_and_main_carry_the_cause_and_the_failure_list_names_each_issue_once():
    doc, events = _run()
    s = _stats(events, doc)
    assert s["nodes"]["final_gate"]["cause"] == "ac_failed ×3" and s["nodes"]["main"]["cause"] == "ac_failed ×3"
    assert s["nodes"]["router"]["cause"] is None and s["nodes"]["route"]["cause"] is None
    assert len(s["causes"]) == 1                      # main repeats the final gate's reasons: one failure, not two
    c = s["causes"][0]
    assert c["node"] == "final_gate" and [r["subject"] for r in c["reasons"]] == ["test_issue_20.py", "test_issue_21.py", "test_issue_22.py"]
    assert all(r["evidence"] is None for r in c["reasons"])      # this record was made before the engine kept evidence


def test_evidence_in_the_record_reaches_the_failure_list_and_the_node():
    doc, events = _run()
    ev = copy.deepcopy(events)
    gate = next(e for e in ev if e["event"] == "outcome" and e["node"] == "final_gate")
    gate["evidence"] = {gate["reasons"][0]: "tests/api/test_zz_heldout_20.py:144: AttributeError: 'Catalog' has no attribute 'restock'"}
    s = _stats(ev, doc)
    assert "AttributeError" in s["causes"][0]["reasons"][0]["evidence"] and s["causes"][0]["reasons"][1]["evidence"] is None
    assert "AttributeError" in s["nodes"]["final_gate"]["evidence"][gate["reasons"][0]]


def test_a_dispatch_child_names_the_issue_its_prompt_ran():
    """Each child card names the issue it ran, read from its own request prompt, not a new event."""
    _, events = _run()
    kids = run_node([{"op": "children", "events": events}])[0]
    assert [c["issue"] for c in kids] == [19, 20, 21, 22]                    # the four prompts name issues/19..22
    assert run_node([{"op": "issuenumber", "prompt": "Implement the issue in issues/07-rounding-policy.md, in the project"},
                     {"op": "issuenumber", "prompt": "this request names no issue file, and no .md either"}]) == [7, None]


def test_the_root_node_after_a_finished_run_reads_a_definite_state_never_grey():
    """A root or structural node the run has touched is never an ambiguous flat grey."""
    doc, events = _run()
    nodes = _stats(events, doc)["nodes"]
    assert nodes["main"]["outcome"] == "fail"
    assert run_node([{"op": "stateclass", "ns": nodes["main"]}])[0] == "o-fail"
    # a node the run never reached, after the verdict, reads not-run rather than a plain card
    doc2 = copy.deepcopy(doc)
    doc2["nodes"].append({"name": "extra_gate", "kind": "gate", "check": "scope", "params": {"allowed_generated": []}})
    n = _stats(events, doc2)["nodes"]["extra_gate"]
    assert n["not_run"] is True and run_node([{"op": "stateclass", "ns": n}])[0] == "o-not-run"


def test_the_gates_a_dispatch_checks_take_their_state_from_the_children():
    doc, events = _run()
    s = _stats(events, doc)
    for g in ("scope_gate", "security_gate"):
        n = s["nodes"][g]
        assert n["outcome"] == "pass" and n["via"] == {"dispatch": "route", "pass": 4, "fail": 0, "of": 4} and not n.get("not_run")
    ev = copy.deepcopy(events)
    _child_outcome(ev, "route.router.2")["checks"]["security_gate"] = "fail"
    n = _stats(ev, doc)["nodes"]["security_gate"]
    assert n["outcome"] == "fail" and n["via"]["fail"] == 1 and n["via"]["pass"] == 3 and n["cause"] == "check failed"


def test_a_node_that_never_ran_says_so_only_after_the_verdict():
    doc, events = _run()
    doc = copy.deepcopy(doc)
    doc["nodes"].append({"name": "extra_gate", "kind": "gate", "check": "scope", "params": {"allowed_generated": []}})
    assert _stats(events, doc)["nodes"]["extra_gate"]["not_run"] is True
    assert not any(n.get("not_run") for name, n in _stats(events, doc)["nodes"].items() if name != "extra_gate")     # every other node ran or was checked
    live = [e for e in events if e["event"] != "verdict"][:20]
    assert "extra_gate" not in _stats(live, doc)["nodes"]                      # a run in progress: the node may still run


def test_the_hash_mismatch_view_keeps_the_timeline_and_the_verdict_only():
    doc, events = _run()
    m = run_node([{"op": "stats", "events": events, "doc": doc, "shown": False}])[0]
    assert set(m) == {"run_id", "graph_name", "state", "verdict", "timeline"}      # decision #63: no causes, no children without the graph


# ---- the page paints them
def test_the_page_paints_children_causes_not_run_and_evidence_from_the_core():
    src = PAGE_SRC()
    for token in ("core.childPlan", "renderChildren(cards)", "cardLines(ns", "not run", "failuresHtml(st.causes)", "pre class=\"ev\"", "core.reasonParts"):
        assert token in src, token
    assert "S.layout[c.name]" not in src and "S.layout[c]" not in src.split("function renderChildren")[1].split("function renderMap")[0]     # a child never enters the saved layout
