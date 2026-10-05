"""Guards for ticket #29: ADR-0108 (run spend, caps, funding, the optional floor)."""
import pytest

from squad import record
from squad.adapter import ScriptedAdapter
from squad.budget import Budget
from squad.engine import Engine
from squad.graph import from_doc, validate
from tests.flows import agent, doc, run_engine
from tests.helpers import five

pytestmark = pytest.mark.anyio


def outs(path, node=None):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == "outcome" and (node is None or e["node"] == node)]


async def test_run_spend_sums_measured_results_only(tmp_path):
    d = doc([agent("a", output_key="x"), agent("b", output_key="y")], ["a", "b"])
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.25}, "b": {"output": {"y": 1}, "spend": 0.5}}, tmp_path)
    await eng.run()
    assert eng.budget.run_spend == pytest.approx(0.75)
    assert eng.facts.read("run_spend") == pytest.approx(0.75) and eng.facts.read("run_spend_remaining") == pytest.approx(9.25)
    assert [e["spend"] for e in outs(tmp_path)][:2] == [0.25, 0.5]


async def test_unknown_spend_is_never_counted_as_zero(tmp_path):
    d = doc([agent("a", output_key="x", deadline=1), agent("b", output_key="y")], ["a", "b"], cap=2.0)
    d["nodes"][1]["next"] = {"deadline": "continue"}
    eng = run_engine(d, {"a": {"delay": 5, "output": {"x": 1}}, "b": {"output": {"y": 1}, "spend": 0.1}}, tmp_path, time_scale=0.05)
    await eng.run()
    killed = outs(tmp_path, "a")[0]
    assert killed["outcome"] == "deadline" and killed["spend"] is None and killed["cap"] == 1.0
    assert eng.budget.run_spend == pytest.approx(0.1)           # the fact leaves the unknown call out
    assert eng.budget.unmeasured == pytest.approx(1.0)          # funding keeps its grant reserved
    assert eng.budget.available() == pytest.approx(2.0 - 0.1 - 1.0)


async def test_an_entry_the_run_cannot_fund_is_not_started_and_the_node_folds_fail(tmp_path):
    d = doc([agent("a", output_key="x"), agent("b", output_key="y")], ["a", "b"], cap=0.5)
    d["nodes"][1]["budget"]["spend"]["max"] = 0.45
    d["nodes"][2]["budget"]["spend"] = {"min": 0.3, "max": 0.45}
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.4}, "b": {"output": {"y": 1}, "spend": 0.1}})
    eng = Engine(from_doc(d), adapter, tmp_path)
    v = await eng.run()
    b = outs(tmp_path, "b")[0]
    assert b["outcome"] == "fail" and b["reasons"] == ["run_spend_cap"] and b["spend"] is None
    assert [c.node for c in adapter.calls] == ["a"]  # b never started: no session id
    assert v.verdict == "failed" and "run_spend_cap" in v.reasons


async def test_the_engine_passes_the_lesser_of_the_node_cap_and_the_run_remainder(tmp_path):
    d = doc([agent("a", output_key="x"), agent("b", output_key="y")], ["a", "b"], cap=1.3)
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.6}, "b": {"output": {"y": 1}, "spend": 0.1}})
    await Engine(from_doc(d), adapter, tmp_path).run()
    assert [round(c.max_spend, 6) for c in adapter.calls] == [1.0, 0.7]
    assert outs(tmp_path, "b")[0]["cap"] == pytest.approx(0.7)


async def test_a_cap_ends_a_node_through_its_artifact_with_a_reason(tmp_path):
    d = doc([agent("a", output_key="x")], ["a"])
    for stop, reason in (("turn_cap", "node_turn_cap"), ("spend_cap", "node_spend_cap")):
        t = tmp_path / stop
        eng = run_engine(d, {"a": {"output": None, "spend": 0.2, "stop_reason": stop}}, t)
        await eng.run()
        e = outs(t, "a")[0]
        assert e["outcome"] == "fail" and e["reasons"] == [reason]
    t = tmp_path / "valid"
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.2, "stop_reason": "turn_cap"}}, t)
    v = await eng.run()
    e = outs(t, "a")[0]
    assert e["outcome"] == "pass" and e["reasons"] == [] and e["diagnostic"]["cap"] == "node_turn_cap" and v.verdict == "completed"


async def test_a_spend_cap_hit_under_a_small_remainder_names_the_run_cap(tmp_path):
    d = doc([agent("a", output_key="x"), agent("b", output_key="y")], ["a", "b"], cap=1.2)
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.6}, "b": {"output": None, "spend": 0.6, "stop_reason": "spend_cap"}}, tmp_path)
    await eng.run()
    assert outs(tmp_path, "b")[0]["reasons"] == ["run_spend_cap"]


async def test_spend_past_a_cap_by_one_response_is_recorded_as_measured(tmp_path):
    d = doc([agent("a", output_key="x")], ["a"], cap=2.0)
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 1.4}}, tmp_path)  # node cap is 1.0
    await eng.run()
    e = outs(tmp_path, "a")[0]
    assert e["spend"] == 1.4 and e["cap"] == 1.0 and eng.budget.run_spend == pytest.approx(1.4)


async def test_the_optional_spend_floor_gate_stops_the_run_with_its_reason(tmp_path):
    d = doc([agent("a", output_key="x"), {"name": "floor", "kind": "gate", "check": "spend-floor", "params": {"min_remaining_usd": 1.0}, "next": {"fail": "exit:fail"}},
             agent("b", output_key="y")], ["a", "floor", "b"], cap=1.5)
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.9}})
    v = await Engine(from_doc(d), adapter, tmp_path).run()
    floor = outs(tmp_path, "floor")[0]
    assert floor["outcome"] == "fail" and floor["reasons"] == ["spend-floor:spend_floor"]
    assert v.verdict == "failed" and [c.node for c in adapter.calls] == ["a"]


async def test_the_floor_gate_passes_when_the_remainder_is_above_its_threshold(tmp_path):
    d = doc([agent("a", output_key="x"), {"name": "floor", "kind": "gate", "check": "spend-floor", "params": {"min_remaining_usd": 1.0}, "next": {"fail": "exit:fail"}}],
            ["a", "floor"], cap=5.0)
    v = await run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.9}}, tmp_path).run()
    assert v.verdict == "completed"


def test_a_run_cap_below_an_agent_spend_minimum_is_refused_at_load():
    d = five()
    d["run"]["spend_cap"] = 0.1
    assert any("run spend cap" in str(e) for e in validate(d))


def test_the_reservation_never_exceeds_the_remainder():
    b = Budget(1.0)
    r1 = b.reserve(0.8, 0.1)
    assert r1.amount == 0.8
    assert b.reserve(0.8, 0.3) is None       # 0.2 left is below this node's minimum
    r2 = b.reserve(0.8, 0.1)
    assert r2.amount == pytest.approx(0.2) and b.reserved <= 1.0
    b.settle(r1, None)                        # a call with no result keeps its grant reserved
    assert b.available() == pytest.approx(0.0) and b.run_spend == 0.0
    b.settle(r2, 0.15)
    assert b.run_spend == pytest.approx(0.15)


@pytest.mark.anyio
async def test_an_output_cap_stop_is_a_fail_with_the_reason_node_output_cap_and_measured_spend(tmp_path):
    from tests.flows import agent, doc, run_engine
    d = doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a"])
    eng = run_engine(d, {"a": {"output": None, "spend": 0.05, "stop_reason": "output_cap"}}, tmp_path)
    v = await eng.run()
    out = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "a"][0]
    assert out["outcome"] == "fail" and out["reasons"] == ["node_output_cap"] and out["spend"] == 0.05 and v.verdict == "failed"
