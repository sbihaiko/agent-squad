"""A router's Bash time, split by command class (#1991).

The adapter names each Bash call's class (gate, suite, git, gh or other) in its progress events, with the call's
elapsed seconds at its end. The report sums those end events over a window, and reads the child-gate history for
the gate-slot wait, so the wait and the suite run time stay apart.
"""
from types import SimpleNamespace

import pytest

from squad import bash_time
from squad.sdk_adapter import _Progress


class ToolUseBlock:
    def __init__(self, id, command):
        self.name, self.id, self.input = "Bash", id, {"command": command}


class Message:
    def __init__(self, *blocks):
        self.content, self.usage = list(blocks), None


@pytest.mark.parametrize("command, expected", [
    ("bash contrib/squad-goal/scripts/child-gate.sh tests/test-x.sh", "gate"),
    ("bash contrib/squad-goal/scripts/fast-gate.sh", "gate"),
    ("python3 -m pytest tests/test_x.py -q -p no:deepeval", "suite"),
    ("bash contrib/squad-goal/scripts/test-child-gate.sh", "suite"),
    ("git status --short", "git"),
    ("cd /tmp/wt && git diff --stat", "git"),
    ("rtk git log -5", "git"),
    ("git commit -m 'run pytest first'", "git"),
    ("gh api repos/sbihaiko/agent-squad/issues/1991/comments", "gh"),
    ("ls -la", "other"),
    (None, "other"),
])
def test_a_bash_command_has_one_class(command, expected):
    assert bash_time.classify(command) == expected


def test_a_bash_call_writes_its_class_at_start_and_its_elapsed_at_end():
    events = []
    req = SimpleNamespace(node="route.router.1", entry=1, item=None, session_id="s1")
    prog = _Progress(req, lambda kind, **f: events.append({"event": kind, **f}), 10)
    prog.take(Message(ToolUseBlock("b1","bash contrib/squad-goal/scripts/child-gate.sh tests/test-x.sh")))
    prog.tool_done("b1")
    assert [(e["tool"], e["command_class"], e["phase"]) for e in events] == [
        ("Bash", "gate", "start"), ("Bash", "gate", "end")]
    assert events[1]["elapsed"] >= 0


RUN_NOW = 1_000_000.0


def _line(**fields):
    import json
    return json.dumps({"event": "progress", **fields})


def test_the_report_sums_end_events_by_class_over_24_hours(tmp_path):
    run = tmp_path / "run-a"
    run.mkdir()
    rows = [
        dict(ts=RUN_NOW - 100, node="route.router.1", phase="end", command_class="gate", elapsed=300.0, tool="Bash"),
        dict(ts=RUN_NOW - 50, node="route.router.1", phase="end", command_class="suite", elapsed=40.0, tool="Bash"),
        dict(ts=RUN_NOW - 30, node="route.router.1", phase="end", command_class="git", elapsed=2.5, tool="Bash"),
        dict(ts=RUN_NOW - 20, node="route.router.1", phase="end", command_class="gh", elapsed=1.5, tool="Bash"),
        dict(ts=RUN_NOW - 10, node="route.router.1", phase="start", command_class="gate", elapsed=None, tool="Bash"),
        dict(ts=RUN_NOW - 90000, node="route.router.1", phase="end", command_class="gate", elapsed=999.0, tool="Bash"),
        dict(ts=RUN_NOW - 10, node="actor", phase="end", command_class="other", elapsed=500.0, tool="Bash"),
        dict(ts=RUN_NOW - 10, node="route.router.1", phase=None, command_class=None, elapsed=None, tool="Read"),
    ]
    (run / "record.jsonl").write_text("\n".join(_line(**r) for r in rows) + "\n")
    out = bash_time.report(tmp_path, now=RUN_NOW, hours=24, node_prefix="route.router")
    assert out["seconds"] == {"gate": 300.0, "suite": 40.0, "git": 2.5, "gh": 1.5}
    assert out["calls"] == {"gate": 1, "suite": 1, "git": 1, "gh": 1}
    assert out["largest"] == "gate"
    assert out["total_seconds"] == 344.0


def test_the_report_reads_the_loop_layout_below_one_root(tmp_path):
    """The loop writes <gui>/runs/<run>/record.jsonl; one --runs-dir over the parent finds every run below it."""
    run = tmp_path / "gui-1" / "runs" / "run-a"
    run.mkdir(parents=True)
    (run / "record.jsonl").write_text(_line(
        ts=RUN_NOW - 10, node="route.router.1", phase="end", command_class="gate", elapsed=300.0, tool="Bash") + "\n")
    out = bash_time.report(tmp_path, now=RUN_NOW, hours=24, node_prefix="route.router")
    assert out["seconds"] == {"gate": 300.0}


def test_the_report_splits_gate_wait_from_gate_run_time(tmp_path):
    history = tmp_path / "child-suite-history"
    fin_in, start_in = RUN_NOW - 200, RUN_NOW - 260
    history.write_text(
        f"{fin_in}\t{start_in}\t60\t1.0\t1.2\t0\tsuite.sh\tabc\n"
        f"{RUN_NOW - 90000}\t{RUN_NOW - 90060}\t500\t1.0\t1.0\t0\tsuite.sh\tabc\n")
    out = bash_time.report(tmp_path, now=RUN_NOW, hours=24, node_prefix="route.router", history=history)
    assert out["gate_wait"] == {"runs": 1, "wait_seconds": 60.0, "run_seconds": 60.0}
