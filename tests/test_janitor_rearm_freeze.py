"""An owner rearm lifts the freeze its run's needs-human wrote, and the next janitor pass keeps it off (#1562).

A run that ends needs-human with nothing landed folds its issue to `Blocked`; in the shipped vocabulary `Blocked` is no
column, so the fold writes `frozen:owner` instead. That was final: the fold read the run record, which is immutable
history, so the owner's `issue_ledger.py rearm` changed nothing the pass consulted, and the label came back within one
pass. The frontier skips a frozen issue, so no newer run could ever exist to outrank the old one, and the issue was
stuck for good. ADR-0029 makes the owner the next actor on a needs-human run; a contract that cannot be exercised is a
defect.

The floor is the verdict's instant: a rearm newer than the verdict that stopped the issue lifts the freeze, and one
older than it does not, because that rearm answered an earlier run.

The cases drive janitor.py end to end with a stub `gh` on PATH and a temp state dir. No case touches the network, the
live loop state or the live board.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from tests.test_janitor_card_done_base import SCRIPTS, N, janitor, sandbox, writes

HOUR = 3600


def _needs_human(tmp_path, verdict_ts: float) -> None:
    """The same sandbox, with its run ended needs-human at `verdict_ts` and nothing landed.

    The freeze write is the one call this file proves, so it takes the door the write goes through (`ghcall.sh`) and the
    queue directory a failed dispatch writes into. Neither is in the shared fixture, which reads cards and never labels one.
    """
    for name in ("ghcall.sh", "startability.sh", "skiplabels.sh"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    (tmp_path / "st/cq").mkdir(exist_ok=True)
    run = tmp_path / "co/runs/run-stacked"
    (run / "record.jsonl").write_text(
        '{"event":"run_start","run_id":"run-stacked","ts":"2026-10-13T08:12:00Z"}\n'
        f'{{"event":"verdict","verdict":"needs-human","reasons":["the owner must act"],"exit_code":3,"ts":{verdict_ts!r}}}\n'
    )
    old = os.stat(run).st_mtime - HOUR
    for p in [run / "record.jsonl", run]:
        os.utime(p, (old, old))


def _rearm(tmp_path) -> None:
    """The owner's act, at its own instant: the ledger writes `time.time()`."""
    subprocess.run([sys.executable, str(tmp_path / "issue_ledger.py"), "rearm", f"x/y#{N}"],
                   env={**os.environ, "SG_STATE": str(tmp_path / "st")}, check=True, capture_output=True)


@pytest.mark.heavy
def test_a_needs_human_run_freezes_its_issue(tmp_path):
    """The fold still holds an issue whose run needs a human and whose owner never rearmed it."""
    sandbox(tmp_path, "echo 0")
    _needs_human(tmp_path, time.time() - 2 * HOUR)
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert "frozen:owner" in writes(tmp_path), f"a needs-human run left the issue unfrozen: {writes(tmp_path)}"


@pytest.mark.heavy
def test_a_rearm_newer_than_the_run_keeps_the_freeze_off(tmp_path):
    """The owner's act is newer than the verdict it answers, so the run no longer holds the issue, and no freeze is written."""
    sandbox(tmp_path, "echo 0")
    _needs_human(tmp_path, time.time() - 2 * HOUR)
    _rearm(tmp_path)
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert "frozen:owner" not in writes(tmp_path), f"the pass re-froze an issue the owner rearmed: {writes(tmp_path)}"


@pytest.mark.heavy
def test_a_rearm_older_than_the_verdict_does_not_lift_the_freeze(tmp_path):
    """A rearm answers the run it was written after: a run that ended needs-human later holds the issue again."""
    sandbox(tmp_path, "echo 0")
    _rearm(tmp_path)
    _needs_human(tmp_path, time.time() + HOUR)
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert "frozen:owner" in writes(tmp_path), f"a run newer than the rearm was excused: {writes(tmp_path)}"
