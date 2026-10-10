"""Guards for ticket #28: the walking skeleton on a scripted adapter."""
import json

import pytest

from squad import record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from tests.flows import BAD, GOOD, actor_gate_loop, agent, doc, run_engine
from tests.gitrepo import make_repo
from tests.helpers import five

pytestmark = pytest.mark.anyio


async def test_actor_gate_loop_fails_once_then_passes_on_the_recheck(tmp_path):
    eng = run_engine(actor_gate_loop(), {"actor": [BAD, GOOD]}, tmp_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("completed", 0)
    outs = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome"]
    assert [(e["node"], e["entry"], e["outcome"]) for e in outs] == [
        ("actor", 1, "pass"), ("gate", 1, "fail"), ("actor", 2, "pass"), ("gate", 2, "pass"), ("work", 1, "pass"), ("main", 1, "pass")]
    assert outs[1]["reasons"] == ["scope:generated_file:gen.lock"] and outs[1]["routed"] == "repeat"


async def test_loop_exhaustion_is_fail_with_the_reason_loop_max_exhausted(tmp_path):
    eng = run_engine(actor_gate_loop(2), {"actor": [BAD, BAD]}, tmp_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("failed", 1) and "loop_max_exhausted" in v.reasons
    events = record.read(tmp_path / "record.jsonl")
    work = [e for e in events if e["event"] == "outcome" and e["node"] == "work"][0]
    assert work["outcome"] == "fail" and work["reasons"][0] == "loop_max_exhausted" and "scope:generated_file:gen.lock" in work["reasons"]
    assert [e["entry"] for e in events if e["event"] == "outcome" and e["node"] == "actor"] == [1, 2]  # no third entry


async def test_a_loop_counts_only_entries_that_fail_the_same_way(tmp_path):
    """ADR-0134: a changed failure restarts the streak, so five entries run on `max` 2 while the failure keeps changing, and two alike end it."""
    other = {"output": {"diff": {"files": [{"path": "gen2.lock", "generated": True}]}}, "spend": 0.1}
    eng = run_engine(actor_gate_loop(2), {"actor": [BAD, other, BAD, other, GOOD]}, tmp_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("completed", 0)
    events = record.read(tmp_path / "record.jsonl")
    assert [e["entry"] for e in events if e["event"] == "outcome" and e["node"] == "actor"] == [1, 2, 3, 4, 5]
    eng = run_engine(actor_gate_loop(2), {"actor": [BAD, other, other, GOOD]}, tmp_path / "again")
    v = await eng.run()
    assert v.verdict == "failed" and "loop_max_exhausted" in v.reasons          # entries 2 and 3 failed alike
    events = record.read(tmp_path / "again" / "record.jsonl")
    assert [e["entry"] for e in events if e["event"] == "outcome" and e["node"] == "actor"] == [1, 2, 3]


async def test_a_when_is_read_on_every_entry_and_a_skip_is_recorded(tmp_path):
    d = doc([agent("a", output_key="x", when={"fact": "flag", "op": "eq", "value": True})], ["a"])
    v = await run_engine(d, {"a": {"output": {"x": 1}}}, tmp_path).run()
    ev = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "a"][0]
    assert ev["outcome"] == "pass" and ev["reasons"] == ["skipped", "fact_unavailable:flag"]
    assert v.verdict == "completed"


async def test_a_when_false_skips_the_node_and_no_call_is_made(tmp_path):
    d = five()
    d["run"]["spend_cap"] = 50
    adapter = ScriptedAdapter({"scout": {"output": {"size_estimate": {"files": 1, "added_lines": 3}, "paths": ["a.py"]}, "spend": 0.05}}, default={"output": {"x": 1}})
    eng = Engine(from_doc(d), adapter, tmp_path / "run", options={"stop_after": "spec"}, target=make_repo(tmp_path / "repo"))
    v = await eng.run()
    called = [c.node for c in adapter.calls]
    assert called == ["scout"]  # tier is trivial: spec is skipped by its when
    spec = [e for e in record.read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome" and e["node"] == "spec"][0]
    assert spec["reasons"] == ["skipped"] and v.verdict == "stopped"


async def test_next_words_continue_drops_a_fail_and_exit_fail_ends_the_loop(tmp_path):
    a = agent("a", output_key="x", next={"fail": "continue"})
    b = agent("b", output_key="y")
    d = doc([a, b], ["a", "b"])
    v = await run_engine(d, {"a": {"output": {"status": "fail", "x": 1}, "spend": 0.1}, "b": {"output": {"y": 1}, "spend": 0.1}}, tmp_path).run()
    assert v.verdict == "completed"  # a's fail was dropped by continue
    t2 = tmp_path / "two"
    a2 = agent("a", output_key="x", next={"fail": "exit:fail"})
    d2 = doc([a2, b], ["a", "b"])
    eng = run_engine(d2, {"a": {"output": {"status": "fail", "x": 1}, "spend": 0.1}}, t2)
    v2 = await eng.run()
    assert v2.verdict == "failed" and [c.node for c in eng.adapter.calls] == ["a"]


async def test_every_entry_starts_a_fresh_session_and_the_artifact_decides_the_outcome(tmp_path):
    a = agent("a", output_key="x")
    d = doc([a, {"name": "l", "kind": "loop", "max": {"value": 3, "min": 1}, "children": ["a"]}], ["l"])
    eng = run_engine(d, {"a": [{"output": {"nope": 1}, "spend": 0.1}, {"output": None, "spend": 0.1}, {"output": {"x": 1}, "spend": 0.1}]}, tmp_path)
    v = await eng.run()
    assert v.verdict == "completed"
    assert len({c.session_id for c in eng.adapter.calls}) == 3
    outs = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "a"]
    assert [e["reasons"] for e in outs] == [["artifact_missing"], ["artifact_missing"], []]


async def test_a_needs_human_answer_without_the_output_key_is_still_needs_human(tmp_path):    # #158
    a = agent("a", output_key="spawns")
    eng = run_engine(doc([a], ["a"]), {"a": [{"output": {"children": [], "status": "needs-human", "reasons": ["map not ready"]}, "spend": 0.1}]}, tmp_path)
    v = await eng.run()
    assert v.verdict == "needs-human" and v.exit_code == 10 and "map not ready" in v.reasons


async def test_an_agent_that_yields_needs_human_ends_the_run_needs_human(tmp_path):
    d = doc([agent("a", output_key="x")], ["a"])
    v = await run_engine(d, {"a": {"output": {"x": 1, "status": "needs-human", "reasons": ["question-unanswered"]}, "spend": 0.1}}, tmp_path).run()
    assert (v.verdict, v.exit_code) == ("needs-human", 10) and v.reasons == ["question-unanswered"]


async def test_artifacts_are_files_named_by_the_outcome_event_and_nothing_else_carries_context(tmp_path):
    d = actor_gate_loop()
    eng = run_engine(d, {"actor": [GOOD]}, tmp_path)
    await eng.run()
    ev = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "actor"][0]
    assert json.loads((tmp_path / ev["artifact"]).read_text())["diff"]["files"][0]["path"] == "a.py"
    assert "actor" in eng.adapter.calls[0].prompt


async def test_the_scripted_adapter_makes_no_live_call_and_no_spend_outside_the_script(tmp_path):
    d = actor_gate_loop()
    eng = run_engine(d, {"actor": [GOOD]}, tmp_path)
    await eng.run()
    assert eng.budget.run_spend == pytest.approx(0.1) and eng.budget.reserved == 0


async def test_a_run_writes_one_run_start_and_one_verdict_event(tmp_path):
    eng = run_engine(actor_gate_loop(), {"actor": [GOOD]}, tmp_path)
    await eng.run()
    kinds = [e["event"] for e in record.read(tmp_path / "record.jsonl")]
    assert kinds[0] == "run_start" and kinds[-1] == "verdict" and kinds.count("verdict") == 1


async def test_an_agent_outcome_records_the_model_the_effort_and_the_tokens_when_known(tmp_path):
    a = agent("a", output_key="x")
    d = doc([a], ["a"])
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1, "turns": 2, "usage": {"tokens_in": 120, "tokens_out": 30}}})
    eng = Engine(from_doc(d), adapter, tmp_path / "run", target=make_repo(tmp_path / "repo"))
    await eng.run()
    ev = [e for e in record.read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome"][0]
    assert ev["tokens_in"] == 120 and ev["tokens_out"] == 30
    assert ev["model"] == adapter.calls[0].model and ev["effort"] == adapter.calls[0].effort


async def test_an_outcome_with_unknown_usage_carries_no_token_field_and_still_reads(tmp_path):
    a = agent("a", output_key="x")
    d = doc([a], ["a"])
    eng = Engine(from_doc(d), ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1, "turns": 2}}), tmp_path / "run", target=make_repo(tmp_path / "repo"))
    await eng.run()
    ev = [e for e in record.read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome"][0]
    assert "tokens_in" not in ev and "tokens_out" not in ev and ev["model"]


async def test_a_loop_a_gate_and_the_dispatch_node_record_their_own_elapsed_wall_time(tmp_path):
    """A loop, a gate, or a dispatch node never calls the model itself, but it still takes real
    wall time to run its children and land their work. The record puts `elapsed` on every outcome
    event, not only on an agent's: found while checking the dashboard with the owner, a loop and
    a gate node showed null and the dashboard read it as "—" for a node that plainly ran."""
    eng = run_engine(actor_gate_loop(), {"actor": [GOOD]}, tmp_path)
    v = await eng.run()
    assert v.verdict == "completed"
    outs = {e["node"]: e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome"}
    for name in ("gate", "work", "main"):      # "gate" is a gate node, "work" and "main" are loops
        assert outs[name]["elapsed"] is not None and outs[name]["elapsed"] >= 0


async def test_loop_exhaustion_still_records_elapsed_on_its_failing_exit(tmp_path):
    eng = run_engine(actor_gate_loop(2), {"actor": [BAD, BAD]}, tmp_path)
    await eng.run()
    work = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "work"][0]
    assert work["outcome"] == "fail" and work["elapsed"] is not None and work["elapsed"] >= 0


def test_a_childs_own_out_of_budget_claim_is_not_recorded_as_a_cap():    # #169: no cap fired at $0.36 of $1.50; the model wrote the reason itself
    from squad import session
    res = type("R", (), {"output": {"status": "fail", "reasons": ["out_of_budget"]}})()
    assert session.agent_outcome({}, res, None) == ("fail", ["child_said_out_of_budget"], None)
