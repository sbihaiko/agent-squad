"""Where a run's wall time went, and which suites it ran again on one tree (#2205)."""
import json

import pytest

from squad import run_time

T0 = 1_000_000.0


def _record(run, rows):
    run.mkdir(parents=True, exist_ok=True)
    (run / "record.jsonl").write_text("".join(json.dumps({**row, "pos": i + 1}) + "\n" for i, row in enumerate(rows)))


def _gate(event, node, started_at, ts, **fields):
    base = {"node": node, "entry": 1, "item": None}
    if event == "gate_start":
        return {"event": event, **base, "pid": 1, "limit": None, "started_at": started_at, "ts": ts}
    return {"event": event, **base, "source": "check.1", "ts": ts, **fields}


def test_the_report_names_a_suite_run_again_on_an_unchanged_tree(tmp_path, capsys):
    rows = [
        _gate("gate_start", "check_gate", T0 + 10, T0 + 10),
        _gate("gate_result", "check_gate", None, T0 + 40, sha="abc", dirt="d1", suite="fast", outcome="fail"),
        _gate("gate_start", "check_gate", T0 + 50, T0 + 50),
        _gate("gate_result", "check_gate", None, T0 + 80, sha="abc", dirt="d1", suite="fast", outcome="fail"),
        _gate("gate_start", "check_gate", T0 + 90, T0 + 90),
        _gate("gate_result", "check_gate", None, T0 + 100, sha="def", dirt="d2", suite="fast", outcome="pass"),
    ]
    _record(tmp_path, rows)
    assert run_time.report(tmp_path)["repeats"] == [
        {"suite": "fast", "sha": "abc", "dirt": "d1", "count": 2, "seconds": 60.0}]
    run_time.main([str(tmp_path)])
    assert "repeat fast: 2 runs on tree abc, 60 s" in capsys.readouterr().out


def test_the_report_splits_wall_time_into_suite_gate_wait_model_and_idle(tmp_path):
    rows = [
        {"event": "call_start", "node": "route.router.1", "entry": 1, "session": "s", "pid": 1, "limit": None,
         "started_at": T0, "ts": T0},
        {"event": "progress", "node": "route.router.1", "entry": 1, "item": None, "session": "s", "messages": 1,
         "context_tokens": 1, "tool": "Bash", "command_class": "suite", "phase": "start", "elapsed": None, "ts": T0},
        {"event": "progress", "node": "route.router.1", "entry": 1, "item": None, "session": "s", "messages": 2,
         "context_tokens": 1, "tool": "Bash", "command_class": "suite", "phase": "end", "elapsed": 30.0, "ts": T0 + 60},
        {"event": "progress", "node": "route.router.1", "entry": 1, "item": None, "session": "s", "messages": 3,
         "context_tokens": 1, "tool": "Bash", "command_class": "gate", "phase": "end", "elapsed": 10.0, "ts": T0 + 70},
        {"event": "progress", "node": "route.router.1", "entry": 1, "item": None, "session": "s", "messages": 4,
         "context_tokens": 1, "tool": "Bash", "command_class": "git", "phase": "end", "elapsed": 5.0, "ts": T0 + 75},
        {"event": "outcome", "node": "route.router.1", "entry": 1, "outcome": "pass", "reasons": [], "artifact": None,
         "spend": 0.0, "turns": 1, "cap": None, "elapsed": 100.0, "ts": T0 + 100},
        {"event": "verdict", "verdict": "completed", "exit_code": 0, "reasons": [], "remains": [], "folded": {},
         "ts": T0 + 200},
    ]
    _record(tmp_path, rows)
    assert run_time.report(tmp_path)["split"] == {
        "wall": 200.0, "suite": 30.0, "gate_wait": 10.0, "model": 60.0, "idle": 100.0}


def test_a_folder_with_no_record_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        run_time.main([str(tmp_path / "missing")])
