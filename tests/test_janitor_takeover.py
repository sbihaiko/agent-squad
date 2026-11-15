"""A run with no progress for thirty minutes is taken over, never restarted from the queue (ADR-0166).

Rule C reaps a zombie run and frees its dispatch markers, so its issues go back to Todo and a fresh child starts the same
work with none of the earlier context. Rule T ends a run with no verdict whose record has not moved for the takeover bound
and keeps its issues claimed: the dispatch marker stays, a `taken-<run>` marker names the issues a hand fix owes, and no
card goes back to Todo. A run waiting for a gate slot records no progress but is not stuck: the gate queue is the machine's
time, not the run's, so that wait never reaches the bound (#1191).

These cases drive janitor.py end to end with the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh
and a temp state dir. No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_janitor_card_done_base import N, janitor, sandbox, writes

RUN = "run-stacked"


def live_run(tmp_path: Path, age: float, moved: float | None = None) -> Path:
    """One run with no verdict for #2030, started `age` seconds ago, its record fresh (so rule C reads it as live).
    `moved` adds one progress event that many seconds ago: the record's last motion."""
    sandbox(tmp_path, "echo 0", live=True)
    run = tmp_path / "co/runs" / RUN
    now = time.time()
    events = [{"event": "run_start", "run_id": RUN, "ts": now - age}]
    if moved is not None:
        events.append({"event": "progress", "node": "route.router.1", "entry": 1, "item": None, "session": "s",
                       "messages": 1, "context_tokens": 1, "tool": "Bash", "ts": now - moved})
    (run / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    os.utime(run / "record.jsonl", (now, now))
    return run


def open_call(run: Path, started: float, limit: float | None) -> None:
    """One call open since `started` seconds ago, with the deadline it recorded (`limit`, in seconds; None for no limit).
    A long call writes the `call_start` and then nothing until it ends, so this event is the record's last motion."""
    now = time.time()
    ev = {"event": "call_start", "node": "route.router.1", "entry": 1, "session": "s", "pid": os.getpid(),
          "limit": limit, "started_at": now - started, "ts": now - started}
    with (run / "record.jsonl").open("a") as f:
        f.write(json.dumps(ev) + "\n")
    os.utime(run / "record.jsonl", (now, now))


@pytest.mark.heavy
def test_a_run_whose_open_call_still_has_time_is_not_taken_over(tmp_path):
    """#1842: the bound reads progress, and a call writes none until it ends. A run inside one long call is inside the
    deadline that call recorded: that deadline ends the call, and a takeover before it kills the call short of its cap,
    however long the run has been working."""
    run = live_run(tmp_path, 3300)     # the record's last motion is 3300 s old, well past the 1800 s bound
    open_call(run, started=3300, limit=7200)   # the call's own deadline is 3900 s ahead
    janitor(tmp_path)
    st = tmp_path / "st"
    assert RUN in (st / "active-runs.txt").read_text(), "a run inside its open call's deadline left active-runs"
    assert not (st / "auto" / f"taken-{RUN}").exists(), "a run inside its open call's deadline was taken over"
    assert (st / "auto" / f"disp-{N}").exists(), "the dispatch marker of a run inside its call's deadline went"


@pytest.mark.heavy
def test_a_run_whose_open_call_passed_its_deadline_is_taken_over(tmp_path):
    """#1842: once the open call is past the deadline it recorded, the run is stuck by both measures and still ends."""
    run = live_run(tmp_path, 3300)
    open_call(run, started=3300, limit=600)   # the call's own deadline passed 2700 s ago
    janitor(tmp_path)
    st = tmp_path / "st"
    assert RUN not in (st / "active-runs.txt").read_text(), "a run past its open call's deadline stayed in active-runs"
    assert (st / "auto" / f"taken-{RUN}").exists(), "no taken-over marker names the run past its call's deadline"


@pytest.mark.heavy
def test_a_run_past_the_bound_is_ended_and_its_issues_stay_claimed(tmp_path):
    live_run(tmp_path, 1900)
    janitor(tmp_path)
    st = tmp_path / "st"
    assert RUN not in (st / "active-runs.txt").read_text(), "the run past the bound stayed in active-runs"
    taken = st / "auto" / f"taken-{RUN}"
    assert taken.exists(), "no taken-over marker names the issues a hand fix owes"
    assert RUN in taken.read_text() and str(N) in taken.read_text(), f"the marker does not name the run and its issues: {taken.read_text()}"
    assert (st / "auto" / f"disp-{N}").exists(), "the dispatch marker went, so the issue goes back to the queue"
    assert f"Todo {N}" not in writes(tmp_path), f"the card went back to Todo: {writes(tmp_path)}"
    assert "TAKEN-OVER" in (st / "alerts.log").read_text(), "no alert names the taken-over run"


@pytest.mark.heavy
def test_a_run_under_the_bound_is_untouched(tmp_path):
    live_run(tmp_path, 600)
    janitor(tmp_path)
    st = tmp_path / "st"
    assert RUN in (st / "active-runs.txt").read_text(), "a run under the bound left active-runs"
    assert not (st / "auto" / f"taken-{RUN}").exists(), "a run under the bound was taken over"
    assert (st / "auto" / f"disp-{N}").exists(), "the dispatch marker of a run under the bound went"


@pytest.mark.heavy
def test_a_run_that_moved_its_record_within_the_bound_is_untouched(tmp_path):
    """#1191: the bound measures a run that stopped moving, never one that started long ago and is still working."""
    live_run(tmp_path, 3300, moved=60)
    janitor(tmp_path)
    st = tmp_path / "st"
    assert RUN in (st / "active-runs.txt").read_text(), "a run that moved its record a minute ago left active-runs"
    assert not (st / "auto" / f"taken-{RUN}").exists(), "a run that moved its record was taken over"
    assert (st / "auto" / f"disp-{N}").exists(), "the dispatch marker of a run that is working went"


@pytest.mark.heavy
def test_a_gate_older_than_its_own_bound_no_longer_exempts_the_run(tmp_path):
    """#1390: the exemption is for a gate doing its work or waiting its turn, never for one that has outlived every bound
    the loop gives a gate. Three dead runs sat behind such a process for hours, the janitor logged nothing about them, and
    only a hand edit freed their slots."""
    run = live_run(tmp_path, 3300)
    gate = run / "gate.sh"
    gate.write_text("#!/bin/bash\nsleep 60\n")
    gate.chmod(0o755)
    waiter = subprocess.Popen(["bash", str(gate)], cwd=run.resolve())
    try:
        time.sleep(0.5)
        janitor(tmp_path, SG_GATE_STUCK_SECONDS="0.1")
    finally:
        waiter.kill()
        waiter.wait()
    st = tmp_path / "st"
    assert RUN not in (st / "active-runs.txt").read_text(), "the run behind an overdue gate stayed in active-runs"
    assert (st / "auto" / f"taken-{RUN}").exists(), "no taken-over marker names the run behind an overdue gate"


@pytest.mark.heavy
def test_a_run_waiting_for_a_gate_slot_is_not_taken_over(tmp_path):
    """#1191: a run waiting its turn in the machine-wide gate queue is not stuck. The child's own entry gate blocks on the
    gate lock inside its worktree, so a gate process is alive in the run folder while the record holds still."""
    run = live_run(tmp_path, 3300)
    gate = run / "gate.sh"
    gate.write_text("#!/bin/bash\nsleep 60\n")
    gate.chmod(0o755)
    waiter = subprocess.Popen(["bash", str(gate)], cwd=run.resolve())
    try:
        time.sleep(0.5)     # the pass reads the process table and the cwd of each process: give the waiter a cwd first
        janitor(tmp_path)
    finally:
        waiter.kill()
        waiter.wait()
    st = tmp_path / "st"
    assert RUN in (st / "active-runs.txt").read_text(), "a run waiting for a gate slot left active-runs"
    assert not (st / "auto" / f"taken-{RUN}").exists(), "a run waiting for a gate slot was taken over"
    assert (st / "auto" / f"disp-{N}").exists(), "the dispatch marker of a run waiting for a gate slot went"
