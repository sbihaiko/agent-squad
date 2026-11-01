"""Guards for the stuck-call monitor: the monitor reads run records and never calls an agent."""
import json
import os

from squad import monitor, record


def rec(tmp_path, *events):
    r = record.Record(tmp_path / "record.jsonl")
    for kind, fields in events:
        r.append(kind, **fields)
    return tmp_path / "record.jsonl"


START = ("call_start", dict(node="a", entry=1, item=None, session="s1", pid=os.getpid(), limit=10, started_at=1000.0))
END = ("outcome", dict(node="a", entry=1, outcome="pass", reasons=[], artifact=None, spend=0.1, turns=1, cap=1.0, elapsed=1.0))


def test_a_live_call_past_its_limit_is_one_stuck_finding(tmp_path):
    p = rec(tmp_path, START)
    f = monitor.scan(p, now=1025.0)
    assert [(x["finding"], x["node"], x["over_limit_s"]) for x in f] == [("stuck", "a", 15.0)]


def test_a_finding_names_the_chain_the_call_started_with(tmp_path):
    """SPEC 1035: the alert repeats the chain the record states at the call; an old call reads `unknown`."""
    started = ("call_start", dict(START[1], chain=["claude-opus-5-5", "claude-sonnet-5-5"], chain_id="h1"))
    fresh = monitor.scan(rec(tmp_path / "fresh", started), now=1025.0)
    assert fresh[0]["chain"] == {"ids": ["claude-opus-5-5", "claude-sonnet-5-5"], "sha256": "h1"}
    old = monitor.scan(rec(tmp_path / "old", START), now=1025.0)
    assert old[0]["chain"] == {"ids": "unknown", "sha256": "unknown"}


def test_a_dead_process_with_no_outcome_is_abandoned(tmp_path):
    p = rec(tmp_path, START)
    f = monitor.scan(p, now=1001.0, alive=lambda pid: False)
    assert [x["finding"] for x in f] == ["abandoned"]


def test_a_live_call_inside_its_limit_and_a_finished_call_give_no_finding(tmp_path):
    assert monitor.scan(rec(tmp_path, START), now=1005.0) == []
    done = tmp_path / "b"
    done.mkdir()
    assert monitor.scan(rec(done, START, END), now=9999.0, alive=lambda pid: False) == []


def test_a_sweep_writes_findings_to_a_log_and_nothing_else(tmp_path):
    (tmp_path / "run1").mkdir()
    rec(tmp_path / "run1", START)
    log = tmp_path / "monitor.log"
    found = monitor.sweep(tmp_path, log, now=2000.0)
    assert len(found) == 1 and [json.loads(ln)["finding"] for ln in log.read_text().splitlines()] == ["stuck"]


def test_the_monitor_imports_no_adapter():
    src = open(monitor.__file__).read()
    assert "adapter" not in src.replace("agent", "")


def test_the_engine_writes_a_call_start_before_each_call(tmp_path):
    import anyio

    from squad.adapter import ScriptedAdapter
    from squad.engine import Engine
    from squad.graph import from_doc
    from tests.flows import agent, doc
    eng = Engine(from_doc(doc([agent("a", output_key="x")], ["a"])), ScriptedAdapter({"a": {"output": {"x": 1}}}), tmp_path)
    anyio.run(eng.run)
    kinds = [e["event"] for e in record.read(tmp_path / "record.jsonl")]
    assert kinds.index("call_start") < kinds.index("outcome")
