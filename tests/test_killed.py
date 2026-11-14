"""Guards for the killed-run verdict: a run whose process is gone keeps a verdict in its own record."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from squad import caps, killed, reap, record
from squad import verdict as V
from squad.procs import Proc

START = dict(run_id="r1", graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
OUTCOME = dict(node="a", entry=1, outcome="pass", reasons=[], artifact="a.json", spend=0.1, turns=3, cap=1.0, elapsed=2.0)


def _stalled_run(tmp_path):
    run = tmp_path / "run-20260101-000000-abc123"
    r = record.Record(run / "record.jsonl")
    r.append("run_start", **START)
    r.append("outcome", **OUTCOME)
    return run


def test_a_killed_run_gets_a_failed_verdict_with_its_reason(tmp_path):
    run = _stalled_run(tmp_path)
    assert killed.mark_killed(run) is True
    events = record.read(run / "record.jsonl")
    assert [e["event"] for e in events] == ["run_start", "outcome", "verdict"]
    assert events[-1]["verdict"] == "failed"
    assert events[-1]["exit_code"] == 1
    assert events[-1]["reasons"] == ["killed/no-process"]
    assert events[-1]["pos"] == 3


def test_the_verdict_keeps_the_format_the_engine_writes(tmp_path):
    run = _stalled_run(tmp_path)
    killed.mark_killed(run, "killed/operator")
    last = json.loads((run / "record.jsonl").read_text().splitlines()[-1])
    assert list(last)[:1] == ["event"] and last["event"] == "verdict"
    assert {k: last[k] for k in ("verdict", "exit_code", "reasons", "remains", "folded")} == \
        {**V.killed("killed/operator").event_fields()}
    assert last["reasons"] == ["killed/operator"]


def test_a_run_that_has_a_verdict_is_left_alone(tmp_path):
    run = _stalled_run(tmp_path)
    assert killed.mark_killed(run) is True
    before = (run / "record.jsonl").read_text()
    assert killed.mark_killed(run) is False
    assert (run / "record.jsonl").read_text() == before


def test_a_folder_with_no_record_is_left_alone(tmp_path):
    assert killed.mark_killed(tmp_path) is False
    assert not (tmp_path / "record.jsonl").exists()


def test_the_write_is_atomic_and_leaves_no_temporary_file(tmp_path):
    run = _stalled_run(tmp_path)
    killed.mark_killed(run)
    assert sorted(p.name for p in run.iterdir()) == ["record.jsonl"]


def test_the_command_line_marks_the_run_and_prints_the_outcome(tmp_path):
    run = _stalled_run(tmp_path)
    p = subprocess.run([sys.executable, "-m", "squad.killed", str(run)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "marked" in p.stdout
    assert record.read(run / "record.jsonl")[-1]["reasons"] == ["killed/no-process"]
    again = subprocess.run([sys.executable, "-m", "squad.killed", str(run)], capture_output=True, text=True)
    assert again.returncode == 0 and "marked" not in again.stdout


SIGNAL_CHILD = """
import sys
from pathlib import Path
import anyio
from tests.flows import agent, doc, run_engine
d = doc([agent("a", output_key="x")], ["a"])
anyio.run(run_engine(d, {"a": {"delay": 60, "output": {"x": 1}}}, Path(sys.argv[1])).run)
"""


def _wait_for(path, needle, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and needle in path.read_text():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.heavy   # a child engine process and a real signal: the wall time is the child's start
def test_a_sigterm_ends_an_engine_and_the_record_names_the_signal(tmp_path):
    run = tmp_path / "run"
    repo = Path(__file__).resolve().parents[1]
    child = subprocess.Popen([sys.executable, "-c", SIGNAL_CHILD, str(run)], cwd=repo)
    try:
        assert _wait_for(run / "record.jsonl", '"call_start"')
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=30) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
    sig = record.read(run / "record.jsonl")[-1]
    assert sig["event"] == "signal" and sig["signal"] == "SIGTERM"
    assert sig["pid"] == child.pid and sig["ppid"] == os.getpid() and isinstance(sig["pgid"], int)
    assert isinstance(sig["ts"], float)
    assert killed.mark_killed(run) is True
    v = record.read(run / "record.jsonl")[-1]
    assert v["event"] == "verdict" and v["verdict"] == "failed" and v["reasons"] == ["killed/signal:SIGTERM"]


def test_the_reaper_keeps_evidence_beside_a_record_it_marks_killed(tmp_path):
    now = time.time()
    run = tmp_path / "runs" / "run-dead"
    rec = record.Record(run / "record.jsonl")
    rec.append("run_start", **START)
    rec.append("outcome", **OUTCOME)
    os.utime(run / "record.jsonl", (now - 7200, now - 7200))
    act = tmp_path / "active-runs.txt"
    act.write_text(f"{run}\n")
    tmp_script = Path(tempfile.gettempdir()) / "orphan-helper.sh"
    table = {
        101: Proc(101, 1, 101, None, "kill -9 4242"),
        102: Proc(102, 55, 55, None, "pkill -f squad"),
        103: Proc(103, 1, 103, None, f"/bin/sh {tmp_script}"),
        104: Proc(104, 1, 104, None, "python3 /Users/x/project/tool.py"),
    }
    done = reap.reap(act, tmp_path, ceiling=caps.Ceiling({k: 1 for k in caps.KINDS}, tmp_path / "leases"), procs=[], now=now, table=table)
    assert done == ["run-dead"]
    ev = json.loads((run / "reap-evidence.json").read_text())
    assert ev["found_gone_at"] == now
    assert [c["pid"] for c in ev["kill_commands"]] == [101, 102]
    assert [c["pid"] for c in ev["temp_orphans"]] == [103]
