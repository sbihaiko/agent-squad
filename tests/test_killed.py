"""Guards for the killed-run verdict: a run whose process is gone keeps a verdict in its own record."""
import json
import subprocess
import sys

from squad import killed, record
from squad import verdict as V

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
