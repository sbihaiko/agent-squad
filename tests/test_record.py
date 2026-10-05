"""Guards for ticket #25: ADR-0102 rule 8, ADR-0105 rule 7, ADR-0030 (atomic write)."""
import json
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from squad import record
from tests.helpers import DESIGN

START = dict(run_id="r1", graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
OUTCOME = dict(node="a", entry=1, outcome="pass", reasons=[], artifact="a.json", spend=0.1, turns=3, cap=1.0, elapsed=2.0)


def test_append_writes_one_event_with_its_record_position(tmp_path):
    r = record.Record(tmp_path / "run" / "record.jsonl")
    assert r.append("run_start", **START)["pos"] == 1
    assert r.append("outcome", **OUTCOME)["pos"] == 2
    assert [e["pos"] for e in record.read(r.path)] == [1, 2]


def test_every_event_kind_keeps_its_contract_fields(tmp_path):
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    out = r.append("outcome", **OUTCOME)
    for f in ("node", "entry", "outcome", "reasons", "artifact", "spend", "turns", "cap", "elapsed", "pos"):
        assert f in out
    assert set(record.REQUIRED) >= {"run_start", "outcome", "proposed", "validated", "applied", "rejected",
                                    "spawn_requested", "spawn_accepted", "spawn_rejected", "resume", "verdict"}


def test_a_progress_event_carries_where_its_call_is_and_refuses_without_it(tmp_path):
    """A call in flight records where it has got to (ticket #101). The reader refuses one that names no turn."""
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    ev = {"node": "actor", "entry": 1, "item": None, "session": "s1", "messages": 4, "context_tokens": 61209, "tool": "Bash"}
    assert r.append("progress", **ev)["pos"] == 2
    assert "progress" in record.REQUIRED and set(record.REQUIRED["progress"]) == set(ev)
    with pytest.raises(record.RecordError, match="lacks messages"):
        r.append("progress", **{k: v for k, v in ev.items() if k != "messages"})


def test_the_reader_rejects_an_event_that_lacks_a_required_field(tmp_path):
    p = tmp_path / "r.jsonl"
    bad = {k: v for k, v in OUTCOME.items() if k != "reasons"}
    p.write_text(json.dumps({"event": "outcome", "pos": 1, **bad}) + "\n")
    with pytest.raises(record.RecordError, match="lacks reasons"):
        record.read(p)
    r = record.Record(tmp_path / "w.jsonl")
    with pytest.raises(record.RecordError):
        r.append("outcome", **bad)
    assert r.events == [] and not (tmp_path / "w.jsonl").exists()


def test_the_reader_rejects_a_position_gap_an_unknown_kind_and_a_bad_outcome(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({"event": "run_start", "pos": 2, **START}) + "\n")
    with pytest.raises(record.RecordError, match="pos"):
        record.read(p)
    p.write_text(json.dumps({"event": "mystery", "pos": 1}) + "\n")
    with pytest.raises(record.RecordError, match="unknown event kind"):
        record.read(p)
    p.write_text(json.dumps({"event": "outcome", "pos": 1, **{**OUTCOME, "outcome": "maybe"}}) + "\n")
    with pytest.raises(record.RecordError, match="not one of"):
        record.read(p)


def test_a_write_interrupted_before_the_rename_leaves_the_old_file(tmp_path, monkeypatch):
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    before = r.path.read_text()

    def boom(src, dst):
        raise KeyboardInterrupt("killed mid-write")
    monkeypatch.setattr(record.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        r.append("outcome", **OUTCOME)
    assert r.path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["r.jsonl"]
    assert len(r.events) == 1


def test_a_writer_killed_mid_run_leaves_only_whole_events(tmp_path):
    """ADR-0030 rule 5: kill the generator while it writes and read what is left."""
    path = tmp_path / "r.jsonl"
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(DESIGN.parent.parent)!r})
        from squad.record import Record
        r = Record({str(path)!r})
        r.append("run_start", **{START!r})
        print("ready", flush=True)
        while True:
            r.append("outcome", **{OUTCOME!r})
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    time.sleep(0.4)
    p.send_signal(signal.SIGKILL)
    p.wait()
    events = record.read(path)
    assert len(events) >= 2 and events[0]["event"] == "run_start"
    assert [e["pos"] for e in events] == list(range(1, len(events) + 1))


def test_a_record_reopens_and_continues_its_positions(tmp_path):
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    r2 = record.Record(tmp_path / "r.jsonl")
    assert r2.append("outcome", **OUTCOME)["pos"] == 2


def test_the_router_fixture_reads_clean():
    events = record.read(DESIGN / "router.record.jsonl")
    assert events[0]["event"] == "run_start" and events[-1]["event"] == "verdict"


def test_one_outcome_event_per_node_exit_holds_the_fold_fields(tmp_path):
    """ADR-0105 rule 7: an outcome event holds node, entry, outcome, reasons list, artifact, position."""
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    ev = r.append("outcome", **{**OUTCOME, "reasons": ["loop_max_exhausted"]})
    assert ev["reasons"] == ["loop_max_exhausted"] and ev["entry"] == 1 and ev["pos"] == 2
