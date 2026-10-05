"""Ticket #96: a selected node shows its own available-vs-used harness (tools, skills, models, caps), not the global snapshot.

A dispatch child's available harness is its resolved `definition` (squad/envelope.py); its ceiling is the dispatch
envelope's `allow` set (ADR-0113 rule 2). A plain agent declares its own. A gate or a loop carries no harness, said plainly
never an empty list. The fixture is the real four-wave run: four children, each with a real resolved harness and a real outcome.
"""
import json

import pytest

from tests.dashboard_helpers import ROOT, run_node

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"


def _run():
    doc = json.loads((FIX / "graph.json").read_text())
    events = run_node([{"op": "record", "text": (FIX / "run" / "record.jsonl").read_text()}])[0]["events"]
    return doc, events


def _harness(doc, events, name):
    return run_node([{"op": "harness", "doc": doc, "events": events, "name": name}])[0]


def _outcomes(events, name):
    return [e for e in events if e["event"] == "outcome" and e["node"] == name]


def test_a_dispatch_child_shows_its_resolved_harness_the_envelope_ceiling_and_what_it_used():
    doc, events = _run()
    h = _harness(doc, events, "route.router.1")
    assert h["kind"] == "child" and h["dispatch"] == "route"
    # Available: the resolved harness of this one child, from its `definition`.
    assert h["available"] == {
        "skill": "tdd@1", "model": "sonnet", "effort": "medium",
        "tools": ["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
        "turns": 60, "spend": 2, "deadline": 900,
    }
    # The ceiling it inherited from the dispatch envelope (ADR-0113 rule 2).
    assert h["allow"] == {
        "tools": ["Read", "Grep", "Glob", "Edit", "Write", "Bash"],
        "skills": ["grill-with-docs", "tdd", "code-review", "research"],
        "models": ["opus", "sonnet", "haiku"],
        "efforts": ["low", "medium", "high"],
        "turns": {"min": 3, "max": 60},
        "spend": {"min": 0.05, "max": 4},
        "deadline": 4800,
    }
    # Used: the measured outcome. The skill invoked is the definition's, since no outcome repeats it.
    assert h["used"] == {
        "skill": "tdd@1", "model": "sonnet", "effort": "medium", "turns": 15,
        "tokens_in": 295533, "tokens_out": 6316,
        "spend": pytest.approx(0.23748419999999998), "elapsed": pytest.approx(168.28470945800655),
    }


def test_a_plain_agent_shows_its_own_declared_harness_and_the_measured_use():
    doc, events = _run()
    h = _harness(doc, events, "router")
    assert h["kind"] == "agent"
    assert h["available"] == {
        "skill": "ask-matt", "model": "opus", "effort": None, "tools": ["Read", "Grep", "Glob"],
        "turns": {"min": 3, "max": 30}, "spend": {"min": 0.05, "max": 2}, "deadline": 600,
    }
    outs = _outcomes(events, "router")
    assert h["used"]["model"] == "opus" and h["used"]["effort"] is None and h["used"]["skill"] == "ask-matt"
    assert h["used"]["turns"] == sum(o["turns"] for o in outs) == 11                       # five waves, one router call each
    assert h["used"]["spend"] == pytest.approx(sum(o["spend"] for o in outs))
    assert h["used"]["tokens_in"] == sum(o["tokens_in"] for o in outs)
    assert h["used"]["tokens_out"] == sum(o["tokens_out"] for o in outs)
    assert h["used"]["elapsed"] == pytest.approx(sum(o["elapsed"] for o in outs))


def test_a_gate_and_a_loop_carry_no_harness_and_say_so_not_an_empty_list():
    doc, events = _run()
    for name, kind in (("final_gate", "gate"), ("main", "loop")):
        assert _harness(doc, events, name) == {"kind": "none", "name": name, "nodeKind": kind}


def test_a_child_with_no_outcome_yet_still_shows_its_resolved_harness_with_unknown_use():
    doc, events = _run()
    cut = [e for e in events if e["pos"] <= 6]                                            # child 1 accepted and started, no outcome
    h = _harness(doc, cut, "route.router.1")
    assert h["kind"] == "child" and h["available"]["skill"] == "tdd@1" and h["available"]["turns"] == 60
    assert h["used"]["model"] is None and h["used"]["turns"] is None and h["used"]["spend"] is None   # nothing ran yet


def test_a_name_outside_the_graph_and_the_record_has_no_harness():
    doc, events = _run()
    assert _harness(doc, events, "ghost") == {"kind": "none", "name": "ghost", "nodeKind": None}
