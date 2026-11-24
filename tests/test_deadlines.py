"""Guards for deadlines, sessions and teardown."""
import json
import os
import signal
import sys
import time

import anyio
import pytest

from squad import procs, record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.procs import CommandAdapter
from tests.flows import agent, doc, run_engine
from tests.gitrepo import sibling_repo
from tests.helpers import KIND_CHAINS, router

pytestmark = pytest.mark.anyio


def outs(path, node=None):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == "outcome" and (node is None or e["node"] == node)]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_deadline_gives_the_outcome_deadline_with_no_extra_exit_code(tmp_path):
    d = doc([agent("a", output_key="x", deadline=1)], ["a"])
    eng = run_engine(d, {"a": {"delay": 5, "output": {"x": 1}}}, tmp_path, time_scale=0.05)
    v = await eng.run()
    e = outs(tmp_path, "a")[0]
    assert e["outcome"] == "deadline" and e["reasons"] == ["deadline"] and e["spend"] is None
    assert (v.verdict, v.exit_code) == ("failed", 1) and "deadline" in v.reasons


async def test_a_repeat_after_a_deadline_starts_a_fresh_session(tmp_path):
    a = agent("a", output_key="x", deadline=1, next={"deadline": "repeat"})
    d = doc([a, {"name": "l", "kind": "loop", "max": {"value": 2, "min": 1}, "children": ["a"]}], ["l"])
    eng = run_engine(d, {"a": [{"delay": 5, "output": {"x": 1}}, {"output": {"x": 2}, "spend": 0.1}]}, tmp_path, time_scale=0.05)
    v = await eng.run()
    assert v.verdict == "completed"
    assert [e["outcome"] for e in outs(tmp_path, "a")] == ["deadline", "pass"]
    ids = [c.session_id for c in eng.adapter.calls]
    assert len(ids) == 2 and ids[0] != ids[1]


async def test_the_run_default_deadline_applies_where_a_node_declares_none(tmp_path):
    n = agent("a", output_key="x")
    del n["deadline"]
    d = doc([n], ["a"])
    d["run"]["deadline"] = 100
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.1}}, tmp_path, time_scale=0.5)
    await eng.run()
    assert eng.adapter.calls[0].deadline == pytest.approx(50, abs=0.5)


async def test_a_node_deadline_overrides_the_run_default(tmp_path):
    d = doc([agent("a", output_key="x", deadline=40)], ["a"])
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.1}}, tmp_path)
    await eng.run()
    assert eng.adapter.calls[0].deadline == pytest.approx(40, abs=0.5)


async def test_a_run_deadline_already_spent_gives_deadline_without_a_call(tmp_path):
    d = doc([agent("a", output_key="x", deadline=1)], ["a"])
    d["run"]["deadline"] = 1
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}}})
    eng = Engine(from_doc(d), adapter, tmp_path, time_scale=0.0)
    v = await eng.run()
    assert adapter.calls == [] and outs(tmp_path, "a")[0]["reasons"] == ["deadline", "run_deadline"]
    assert v.verdict == "failed"


async def test_a_subagent_stays_inside_its_session_and_is_never_a_node(tmp_path):
    d = doc([agent("a", output_key="x")], ["a"])
    eng = run_engine(d, {"a": {"output": {"x": 1}, "spend": 0.3, "subagents": 3}}, tmp_path)
    await eng.run()
    nodes = {e["node"] for e in outs(tmp_path)}
    assert nodes == {"a", "main"}
    assert outs(tmp_path, "a")[0]["diagnostic"]["subagents"] == 3
    assert eng.budget.run_spend == pytest.approx(0.3)   # the subagent's spend is already inside the parent result


async def test_the_deadline_event_is_written_before_the_teardown_ends(tmp_path):
    seen = {}

    class Slow:
        async def call(self, req):
            try:
                await anyio.sleep(10)
            finally:
                with anyio.CancelScope(shield=True):
                    seen["events_at_teardown_start"] = [e["outcome"] for e in record.Record(tmp_path / "record.jsonl").events if e["event"] == "outcome"]
                    await anyio.sleep(0.2)
                    seen["teardown_done"] = True
    d = doc([agent("a", output_key="x", deadline=1)], ["a"])
    eng = Engine(from_doc(d), Slow(), tmp_path, time_scale=0.05)
    await eng.run()
    assert seen["events_at_teardown_start"] == ["deadline"] and seen["teardown_done"]


async def test_a_fake_child_is_stopped_at_the_deadline_and_leaves_no_orphan(tmp_path):
    pidfile = tmp_path / "pids.json"
    code = ("import json,os,subprocess,sys,time\n"
            "g=subprocess.Popen(['sleep','60'])\n"
            f"open({str(pidfile)!r},'w').write(json.dumps([os.getpid(), g.pid]))\n"
            "time.sleep(60)\n")
    adapter = CommandAdapter(lambda req: [sys.executable, "-c", code], teardown=2.0)
    d = doc([agent("a", output_key="x", deadline=1)], ["a"])
    t0 = time.monotonic()
    eng = Engine(from_doc(d), adapter, tmp_path, time_scale=0.5)
    v = await eng.run()
    pids = json.loads(pidfile.read_text())
    assert len(pids) == 2
    for _ in range(40):
        if not any(alive(p) for p in pids):
            break
        await anyio.sleep(0.1)
    assert not any(alive(p) for p in pids), "a child or grandchild outlived the deadline"
    events = outs(tmp_path, "a")
    assert len(events) == 1 and events[0]["outcome"] == "deadline"
    assert v.verdict == "failed" and time.monotonic() - t0 < 8


def test_a_group_signal_the_os_refuses_for_a_gone_group_is_not_an_error(monkeypatch):
    # macOS answers EPERM, not ESRCH, for a group whose members are all zombies; the group is ours, so EPERM means it is gone
    def refuse(pgid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "killpg", refuse)
    procs._signal_group(type("P", (), {"pid": 1})(), signal.SIGKILL)


async def test_a_child_that_finishes_returns_its_json_result(tmp_path):
    code = "import json; print(json.dumps({'output': {'x': 1}, 'spend': 0.2, 'turns': 2}))"
    d = doc([agent("a", output_key="x")], ["a"])
    eng = Engine(from_doc(d), CommandAdapter(lambda req: [sys.executable, "-c", code]), tmp_path)
    v = await eng.run()
    e = outs(tmp_path, "a")[0]
    assert v.verdict == "completed" and e["spend"] == 0.2 and e["turns"] == 2


async def test_an_adapter_error_is_a_fail_with_unknown_spend(tmp_path):
    d = doc([agent("a", output_key="x", next={})], ["a"])
    eng = run_engine(d, {"a": {"error": "boom"}}, tmp_path)
    await eng.run()
    e = outs(tmp_path, "a")[0]
    assert e["outcome"] == "fail" and e["reasons"] == ["adapter_error"] and e["spend"] is None
    assert eng.budget.unmeasured > 0


async def test_a_null_run_deadline_is_valid_and_a_node_deadline_still_stops_its_call(tmp_path):
    d = doc([agent("a", output_key="x", deadline=1)], ["a"])
    d["run"]["deadline"] = None
    assert from_doc(d)        # the validator accepts a null run deadline and the node deadline above it
    eng = run_engine(d, {"a": {"delay": 5, "output": {"x": 1}}}, tmp_path, time_scale=0.05)
    v = await eng.run()
    assert outs(tmp_path, "a")[0]["outcome"] == "deadline" and v.verdict == "failed"
    start = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "call_start"][0]
    assert start["limit"] is not None


async def test_a_null_run_deadline_with_no_node_deadline_runs_to_its_end_and_an_option_may_set_one(tmp_path):
    n = agent("a", output_key="x")
    n.pop("deadline")
    d = doc([n], ["a"])
    d["run"]["deadline"] = None
    eng = run_engine(d, {"a": {"output": {"x": 1}}}, tmp_path)
    assert (await eng.run()).verdict == "completed"
    assert [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "call_start"][0]["limit"] is None
    eng2 = run_engine(d, {"a": {"output": {"x": 1}}}, tmp_path / "o", options={"deadline": 60})
    assert eng2.run_cfg["deadline"] == 60


# #2840: a wave child's call limit is the smaller of its own deadline and the time left to its wave's age cap. The loop's
# wave.sh writes one absolute epoch in a ceiling file; the engine option `wave_ceiling` names the file, and one engine
# helper bounds both an agent call and a dispatch child, less an entry reserve, so a call that ends at the cap can still
# enter its wave. A missing file changes nothing.
def call_starts(path, node):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == "call_start" and e["node"] == node]


def long_doc():
    d = doc([agent("a", output_key="x", deadline=7200)], ["a"])
    d["run"]["deadline"] = None
    return d


async def test_a_wave_ceiling_bounds_a_call_below_its_node_deadline(tmp_path):
    ceiling = tmp_path / "wave-ceiling-run"
    ceiling.write_text(f"{int(time.time()) + 3600}\n")
    eng = run_engine(long_doc(), {"a": {"output": {"x": 1}, "spend": 0.1}}, tmp_path / "r", options={"wave_ceiling": str(ceiling)})
    assert (await eng.run()).verdict == "completed"
    start = call_starts(tmp_path / "r", "a")[0]
    assert 0 < start["limit"] <= 3600 and start["limit_by"] == "wave_ceiling"
    assert eng.adapter.calls[0].deadline <= 3600
    assert record.read(tmp_path / "r" / "record.jsonl")[0]["engine_options"]["wave_ceiling"] == str(ceiling)


async def test_no_ceiling_file_leaves_the_call_limit_as_it_was(tmp_path):
    eng = run_engine(long_doc(), {"a": {"output": {"x": 1}, "spend": 0.1}}, tmp_path / "r",
                     options={"wave_ceiling": str(tmp_path / "absent")})
    assert (await eng.run()).verdict == "completed"
    start = call_starts(tmp_path / "r", "a")[0]
    assert start["limit"] == pytest.approx(7200, abs=1) and "limit_by" not in start


async def test_a_spent_wave_ceiling_ends_the_call_as_a_deadline_without_a_call(tmp_path):
    ceiling = tmp_path / "wave-ceiling-run"
    ceiling.write_text(f"{int(time.time()) - 10}\n")
    eng = run_engine(long_doc(), {"a": {"output": {"x": 1}}}, tmp_path / "r", options={"wave_ceiling": str(ceiling)})
    v = await eng.run()
    assert eng.adapter.calls == [] and v.verdict == "failed"
    assert outs(tmp_path / "r", "a")[0]["reasons"] == ["deadline", "wave_ceiling"]


async def test_a_wave_ceiling_bounds_a_dispatch_child_below_its_request_deadline(tmp_path):
    ceiling = tmp_path / "wave-ceiling-run"
    ceiling.write_text(f"{int(time.time()) + 3600}\n")
    d = router()
    d["run"]["deadline"] = 21600
    child = {"prompt": "Run tdd on the task.", "skill": "tdd", "kind": "coding", "effort": "medium", "tools": ["Read", "Edit"],
             "stalls": 4, "spend": 1.0, "deadline": 7200, "workspace": "worktrees/child"}
    script = {"router": {"output": {"spawns": [child]}, "spend": 0.1},
              "route.router.1": {"output": {"diff": {"files": [{"path": "a.py"}]}, "ac_commands": ["true"]}, "spend": 0.2}}
    eng = Engine(from_doc(d), ScriptedAdapter(script), tmp_path / "r", target=sibling_repo(tmp_path), kind_chains=KIND_CHAINS,
                 options={"wave_ceiling": str(ceiling)})
    await eng.run()
    start = call_starts(tmp_path / "r", "route.router.1")[0]
    assert 0 < start["limit"] <= 3600 and start["limit_by"] == "wave_ceiling"
