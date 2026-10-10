"""The each node (CONTEXT: Each)."""
import anyio
import pytest

from squad.record import read
from tests.flows import agent, doc, run_engine

WI = {"name": "work_items", "type": "int", "instrument": "code: length of decompose.work_items", "source_kind": "artifact"}


def plan(n):
    return {"output": {"work_items": [{"id": i} for i in range(n)]}, "spend": 0.1}


def each_doc(mode="sequence", join="all", max_parallel=2, each_deadline=30, cap=10.0, item_deadline=30):
    nodes = [agent("decompose", output_key="work_items", next={"fail": "exit:fail"}),
             agent("worker", deadline=item_deadline, next={"fail": "exit:fail"}),
             {"name": "execute", "kind": "each", "items": "work_items", "mode": mode, "max_parallel": max_parallel,
              "join": join, "children": ["worker"], "deadline": each_deadline}]
    return doc(nodes, ["decompose", "execute"], facts=[WI], cap=cap)


def outcomes(tmp_path, node):
    return [e for e in read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == node]


@pytest.mark.anyio
async def test_each_runs_the_children_once_per_item_and_numbers_items_from_one(tmp_path):
    eng = run_engine(each_doc(), {"decompose": plan(3), "worker": {"output": {"ok": 1}, "spend": 0.1}}, tmp_path,
                     adapter={"repeat_last": True})
    v = await eng.run()
    assert v.verdict == "completed"
    assert [e["item"] for e in outcomes(tmp_path, "worker")] == [1, 2, 3]
    assert outcomes(tmp_path, "execute")[0]["outcome"] == "pass"


@pytest.mark.anyio
async def test_the_join_is_fail_then_deadline_then_needs_human_then_pass(tmp_path):
    steps = [{"output": {"status": "needs-human", "reasons": ["ask"]}}, {"output": {"ok": 1}},
             {"output": {"status": "fail", "reasons": ["bad"]}}]
    eng = run_engine(each_doc(), {"decompose": plan(3), "worker": steps}, tmp_path)
    v = await eng.run()
    assert outcomes(tmp_path, "execute")[0]["outcome"] == "fail"
    assert v.verdict == "failed"


@pytest.mark.anyio
async def test_needs_human_beats_pass_in_the_join(tmp_path):
    steps = [{"output": {"ok": 1}}, {"output": {"status": "needs-human", "reasons": ["ask"]}}]
    eng = run_engine(each_doc(), {"decompose": plan(2), "worker": steps}, tmp_path)
    v = await eng.run()
    assert outcomes(tmp_path, "execute")[0]["outcome"] == "needs-human"
    assert v.verdict == "needs-human"


@pytest.mark.anyio
async def test_first_failure_stops_the_items_after_the_first_failing_one(tmp_path):
    steps = [{"output": {"ok": 1}}, {"output": {"status": "fail", "reasons": ["bad"]}}, {"output": {"ok": 1}}]
    eng = run_engine(each_doc(join="first_failure"), {"decompose": plan(3), "worker": steps}, tmp_path)
    await eng.run()
    assert [e["item"] for e in outcomes(tmp_path, "worker")] == [1, 2]
    assert outcomes(tmp_path, "execute")[0]["outcome"] == "fail"


@pytest.mark.anyio
async def test_first_failure_cancels_running_siblings_in_parallel_mode(tmp_path):
    steps = [{"output": {"status": "fail", "reasons": ["bad"]}}, {"output": {"ok": 1}, "delay": 5}]
    eng = run_engine(each_doc(mode="parallel", join="first_failure"), {"decompose": plan(2), "worker": steps}, tmp_path)
    with anyio.fail_after(3):
        await eng.run()
    w = {e["item"]: e for e in outcomes(tmp_path, "worker")}
    assert w[1]["outcome"] == "fail" and w[2]["reasons"] == ["cancelled"]


@pytest.mark.anyio
async def test_parallel_items_run_together_up_to_max_parallel(tmp_path):
    eng = run_engine(each_doc(mode="parallel", max_parallel=2),
                     {"decompose": plan(4), "worker": {"output": {"ok": 1}, "delay": 0.3, "spend": 0.1}}, tmp_path,
                     adapter={"repeat_last": True})
    t0 = anyio.current_time()
    v = await eng.run()
    took = anyio.current_time() - t0
    assert v.verdict == "completed"
    assert 0.5 < took < 1.1      # four 0.3 s items, two at a time


@pytest.mark.anyio
async def test_the_run_limit_caps_max_parallel(tmp_path):
    d = each_doc(mode="parallel", max_parallel=8)
    d["run"]["max_parallel"] = 1
    eng = run_engine(d, {"decompose": plan(3), "worker": {"output": {"ok": 1}, "delay": 0.2, "spend": 0.1}}, tmp_path,
                     adapter={"repeat_last": True})
    t0 = anyio.current_time()
    await eng.run()
    assert anyio.current_time() - t0 > 0.55


@pytest.mark.anyio
async def test_an_item_gets_the_lesser_of_its_deadline_and_the_remaining_each_time(tmp_path):
    eng = run_engine(each_doc(each_deadline=2, item_deadline=30),
                     {"decompose": plan(1), "worker": {"output": {"ok": 1}, "delay": 5}}, tmp_path, time_scale=0.1)
    await eng.run()
    w = outcomes(tmp_path, "worker")[0]
    assert w["outcome"] == "deadline"
    assert w["elapsed"] < 1


@pytest.mark.anyio
async def test_two_parallel_items_reserve_atomically_against_a_small_remainder(tmp_path):
    # cap 1.5, each item reserves up to 1.0: the second item cannot be funded at the same time.
    eng = run_engine(each_doc(mode="parallel", max_parallel=2, cap=1.6),
                     {"decompose": plan(2), "worker": {"output": {"ok": 1}, "delay": 0.2, "spend": 0.3}}, tmp_path,
                     adapter={"repeat_last": True})
    await eng.run()
    w = {e["item"]: e for e in outcomes(tmp_path, "worker")}
    assert sum(1 for e in w.values() if "run_spend_cap" in e["reasons"]) + sum(1 for e in w.values() if e["outcome"] == "pass") == 2
    assert eng.budget.run_spend <= 1.6 + 1e-9


@pytest.mark.anyio
async def test_an_items_fact_that_is_unavailable_fails_the_each(tmp_path):
    d = each_doc()
    eng = run_engine(d, {"decompose": {"output": {"other": 1}, "spend": 0.1}}, tmp_path)
    v = await eng.run()
    assert v.verdict == "failed"
