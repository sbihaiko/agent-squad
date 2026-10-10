"""Tickets #34 and #35: the adaptation applier and its replay."""
import copy

import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.flows import agent, doc, scope_gate

FLAG = {"name": "flag", "type": "bool", "instrument": "code: test flag", "source_kind": "artifact"}
ADAPT = {"kinds": ["set_model", "set_effort", "narrow_tools", "shrink_stalls", "shrink_spend"], "models": ["sonnet", "haiku"],
         "efforts": ["low", "high"], "tools": ["Read"]}
OK = {"output": {"x": 1}, "spend": 0.1}


def graph(loop_adapt=None, proposer_sees=("outcome:gate",), may_propose=True, late=False):
    nodes = [agent("prop", output_key="x", may_propose=may_propose, adapt={"kinds": ["set_model"], "models": ["sonnet"], "sees": list(proposer_sees)},
                   tools=["Read"], next={"fail": "exit:fail"}),
             agent("target", output_key="x", effort="high", tools=["Read", "Edit"], adapt={**ADAPT, "tools": ["Read"]}, next={"fail": "exit:fail"}),
             scope_gate("gate", fail="exit:fail"),
             {"name": "work", "kind": "loop", "max": {"value": 3, "min": 1}, "children": ["target", "gate"],
              **({"adapt": loop_adapt} if loop_adapt else {})}]
    return doc(nodes, ["work", "prop"] if late else ["prop", "work"], facts=[FLAG])


def prop(*ps, **out):
    return {"output": {"x": 1, "proposals": list(ps), **out}, "spend": 0.1}


def run(tmp_path, d, script, **kw):
    eng = Engine(from_doc(d), ScriptedAdapter(script, repeat_last=True), tmp_path, **kw)
    return eng


def ev(tmp_path, kind):
    return [e for e in read(tmp_path / "record.jsonl") if e["event"] == kind]


def p(kind, value, target="target", facts=()):
    return {"kind": kind, "target": target, "value": value, "facts": list(facts)}


@pytest.mark.anyio
async def test_each_kind_is_applied_and_logged_as_proposed_validated_applied(tmp_path):
    g = ("outcome:gate",)
    eng = run(tmp_path, graph(late=True), {"prop": prop(p("set_model", "haiku", facts=g), p("set_effort", "low", facts=g), p("narrow_tools", ["Read"], facts=g),
                                                p("shrink_stalls", 5, facts=g), p("shrink_spend", 0.5, facts=g)), "target": OK})
    await eng.run()
    kinds = [e["event"] for e in read(tmp_path / "record.jsonl") if e["event"] in ("proposed", "validated", "applied", "rejected")]
    assert kinds == ["proposed", "validated", "applied"] * 5
    a = eng.eff.agent(eng.nodes["target"])
    assert (a["model"], a["effort"], a["tools"], a["stalls_max"], a["spend_max"]) == ("haiku", "low", ["Read"], 5, 0.5)
    e = ev(tmp_path, "applied")[0]
    assert e["old"] == "sonnet" and e["requested"] == "haiku" and e["proposer"] == "prop" and e["session"]
    assert e["facts"][0]["name"] == "outcome:gate" and {"instrument", "source", "timestamp"} <= set(e["facts"][0])


@pytest.mark.anyio
async def test_shrink_loop_max_changes_the_loop_at_its_next_entry(tmp_path):
    d = graph(loop_adapt={"kinds": ["shrink_loop_max"]})
    eng = run(tmp_path, d, {"prop": prop(p("shrink_loop_max", 2, target="work")), "target": OK})
    await eng.run()
    assert eng.eff.loop_max(eng.nodes["work"]) == 2


@pytest.mark.anyio
async def test_a_seventh_kind_is_rejected_whole_with_one_event_and_no_outcome(tmp_path):
    eng = run(tmp_path, graph(), {"prop": prop(p("set_deadline", 5)), "target": OK})
    before = copy.deepcopy(eng.nodes)
    v = await eng.run()
    r = ev(tmp_path, "rejected")
    assert [x["reason"] for x in r] == ["unknown_kind"] and v.verdict == "completed"
    assert eng.nodes == before and not ev(tmp_path, "applied")
    assert [e["node"] for e in ev(tmp_path, "outcome")].count("prop") == 1


@pytest.mark.parametrize("proposal,reason", [
    (p("set_model", "opus"), "model_outside_range"),
    (p("set_effort", "max"), "effort_outside_range"),
    (p("narrow_tools", ["Read", "Bash"]), "tool_outside_range"),
    (p("narrow_tools", ["Read", "Edit"]), "tool_outside_range"),
    (p("shrink_stalls", 99), "stalls_max_outside_range"),
    (p("shrink_stalls", 0), "stalls_max_outside_range"),
    (p("shrink_spend", 5.0), "spend_max_outside_range"),
    (p("shrink_spend", 0.001), "spend_max_outside_range"),
    (p("shrink_stalls", "five"), "proposal_malformed"),
    (p("set_model", "haiku", target="gate"), "gate_target"),
    (p("set_model", "haiku", target="nowhere"), "unknown_target"),
    (p("shrink_loop_max", 1, target="work"), "kind_not_allowed_on_target"),
    (p("shrink_loop_max", 1), "kind_does_not_fit_target"),
    (p("set_model", "haiku", facts=("flag",)), "fact_not_seen:flag"),
    (p("set_model", "haiku", facts=("tier",)), "fact_not_seen:tier"),
    ("not a dict", "proposal_malformed"),
])
@pytest.mark.anyio
async def test_a_bad_proposal_writes_one_rejected_event_and_changes_nothing(tmp_path, proposal, reason):
    eng = run(tmp_path, graph(), {"prop": prop(proposal), "target": OK})
    await eng.run()
    assert [x["reason"] for x in ev(tmp_path, "rejected")] == [reason]
    assert not ev(tmp_path, "applied") and eng.eff.over == {}


@pytest.mark.anyio
async def test_a_node_that_may_not_propose_is_rejected(tmp_path):
    eng = run(tmp_path, graph(may_propose=False), {"prop": prop(p("set_model", "haiku")), "target": OK})
    await eng.run()
    assert [x["reason"] for x in ev(tmp_path, "rejected")] == ["proposer_not_declared"]


@pytest.mark.anyio
async def test_an_unavailable_cited_fact_rejects_the_proposal(tmp_path):
    eng = run(tmp_path, graph(), {"prop": prop(p("set_model", "haiku", facts=("outcome:gate",))), "target": OK})
    await eng.run()     # `prop` runs before `gate`, so outcome:gate has no value yet
    assert [x["reason"] for x in ev(tmp_path, "rejected")] == ["fact_unavailable:outcome:gate"]


@pytest.mark.anyio
async def test_a_good_proposal_beside_a_bad_one_applies_only_the_good_one(tmp_path):
    eng = run(tmp_path, graph(), {"prop": prop(p("set_model", "opus"), p("set_model", "haiku")), "target": OK})
    await eng.run()
    assert len(ev(tmp_path, "rejected")) == 1 and len(ev(tmp_path, "applied")) == 1


@pytest.mark.anyio
async def test_a_change_takes_effect_at_the_next_entry_and_never_mid_session(tmp_path):
    # `prop` runs before `target` here, so target's first call already sees the change; its request shows the new model.
    eng = run(tmp_path, graph(), {"prop": prop(p("set_model", "haiku")), "target": OK})
    await eng.run()
    call = [c for c in eng.adapter.calls if c.node == "target"][0]
    assert call.model == "haiku"
    first = [c for c in eng.adapter.calls if c.node == "prop"][0]
    assert first.model == "sonnet"      # the proposer's own call was made before its proposal applied


@pytest.mark.anyio
async def test_a_proposal_does_not_change_the_running_loop_entry(tmp_path):
    nodes = [agent("prop", output_key="x", may_propose=True, adapt={"kinds": ["set_model"], "models": ["sonnet"], "sees": []},
                   next={"fail": "exit:fail"}),
             agent("spin", output_key="x", next={"fail": "repeat"}),
             {"name": "work", "kind": "loop", "max": {"value": 3, "min": 1}, "children": ["prop", "spin"],
              "adapt": {"kinds": ["shrink_loop_max"]}}]
    d = doc(nodes, ["work"], facts=[FLAG])
    script = {"spin": [{"output": {"x": 1, "status": "fail"}}, {"output": {"x": 1}}],
              "prop": prop({"kind": "shrink_loop_max", "target": "work", "value": 1, "facts": []})}
    eng = run(tmp_path, d, script)
    v = await eng.run()
    assert [c.node for c in eng.adapter.calls].count("spin") == 2 and v.verdict == "completed"   # entry 1 kept its maximum of 3
    assert eng.eff.loop_max(eng.nodes["work"]) == 1                                              # the next entry gets 1


@pytest.mark.anyio
async def test_a_replayed_proposal_changes_the_effective_node_never_the_graph_or_its_hash(tmp_path):
    d = graph()
    d["nodes"].append({"name": "ask", "kind": "wait", "owner": "r", "deadline": 100, "default": "pass"})
    next(n for n in d["nodes"] if n["name"] == "main")["children"].append("ask")
    eng = run(tmp_path, d, {"prop": prop(p("set_model", "haiku"), p("shrink_stalls", 4)), "target": OK})
    first = await eng.run()
    assert first.exit_code == 10
    doc_before, hash_before = copy.deepcopy(eng.g.doc), eng.g.hash
    eng2 = run(tmp_path, d, {"prop": OK, "target": OK})
    v = await eng2.resume(answer="pass")
    a = eng2.eff.agent(eng2.nodes["target"])
    assert (a["model"], a["stalls_max"]) == ("haiku", 4)
    assert eng2.g.doc == doc_before and eng2.g.hash == hash_before and v.verdict == "completed"
    assert eng2.adapter.calls == []           # nothing ran again: prop, target and gate are all recorded


@pytest.mark.anyio
async def test_a_resume_from_a_node_does_not_replay_what_that_node_applied(tmp_path):
    d = graph()
    d["nodes"].append({"name": "ask", "kind": "wait", "owner": "r", "deadline": 100, "default": "pass"})
    next(n for n in d["nodes"] if n["name"] == "main")["children"].append("ask")
    eng = run(tmp_path, d, {"prop": prop(p("shrink_stalls", 4)), "target": OK})
    await eng.run()
    eng2 = run(tmp_path, d, {"prop": prop(p("shrink_stalls", 4)), "target": OK})
    v = await eng2.resume(from_node="prop", answer="pass")
    assert v.verdict == "completed" and not ev(tmp_path, "rejected")
    assert len(ev(tmp_path, "applied")) == 2
