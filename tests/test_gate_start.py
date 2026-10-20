"""A gate that runs reads as running (#680).

An agent call writes `call_start`, so the dashboard marks it "Running now". A gate wrote only its outcome, so for the
462 s `final_gate` ran in run-20261020-192307-95bcfe the dashboard marked nothing. The engine now writes `gate_start`
before a gate runs its check, and the dashboard reads a `gate_start` with no outcome as in flight, as it reads a
`call_start`. An old record with no `gate_start` reads as before. `call_start` keeps its meaning: an agent call.
"""
import anyio

from squad import record
from tests.dashboard_helpers import run_node
from tests.flows import GOOD, actor_gate_loop, run_engine


def test_the_engine_writes_a_gate_start_before_the_gate_outcome(tmp_path):
    eng = run_engine(actor_gate_loop(1), {"actor": GOOD}, tmp_path)
    anyio.run(eng.run)
    ev = record.read(tmp_path / "record.jsonl")
    starts = [e for e in ev if e["event"] == "gate_start"]
    assert [(e["node"], e["entry"]) for e in starts] == [("gate", 1)]
    s = starts[0]
    assert isinstance(s["pid"], int) and isinstance(s["started_at"], float) and "item" in s and "limit" in s
    out = next(e for e in ev if e["event"] == "outcome" and e["node"] == "gate")
    assert s["pos"] < out["pos"]
    assert not any(e["event"] == "call_start" and e["node"] == "gate" for e in ev)   # call_start stays an agent call


DOC = {"schema_version": 1, "name": "t", "root": "main",
       "nodes": [{"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["g"]},
                 {"name": "g", "kind": "gate", "check": "ac-verify", "params": {"commands_from": "spec"}}]}
START = {"event": "run_start", "pos": 1, "ts": 990.0, "run_id": "r", "graph_name": "t", "graph_hash": "h", "engine_options": {},
         "root": "main", "plan": []}
GATE = {"event": "gate_start", "pos": 2, "ts": 1000.0, "node": "g", "entry": 1, "item": None, "pid": 1, "limit": None, "started_at": 1000.0}
DONE = {"event": "outcome", "pos": 3, "ts": 1462.0, "node": "g", "entry": 1, "outcome": "fail", "reasons": ["ac-verify:ac_failed"],
        "artifact": None, "spend": None, "turns": None, "cap": None, "elapsed": 462.0}


def _stats(events, now):
    return run_node([{"op": "stats", "events": events, "doc": DOC, "shown": True, "now": now}])[0]


def test_a_gate_with_a_start_and_no_outcome_reads_running():
    s = _stats([START, GATE], 1100.0)
    g = s["nodes"]["g"]
    assert g["running_for"] == 100.0 and g["started"] is True
    assert [c["node"] for c in s["live"]["in_progress"]] == ["g"]
    assert run_node([{"op": "state", "ns": g}])[0] == "running"


def test_a_gate_with_its_outcome_reads_its_outcome():
    g = _stats([START, GATE, DONE], 1500.0)["nodes"]["g"]
    assert g["running_for"] is None and g["outcome"] == "fail"


def test_an_old_record_with_no_gate_start_reads_as_before():
    s = _stats([START, {**DONE, "pos": 2}], 1500.0)
    assert s["nodes"]["g"]["running_for"] is None and s["nodes"]["g"]["started"] is False
