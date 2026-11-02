"""A run past thirty minutes is taken over, never restarted from the queue (ADR-0166).

Rule C reaps a zombie run and frees its dispatch markers, so its issues go back to Todo and a fresh child starts the same
work with none of the earlier context. Rule T ends a run with no verdict that is past the takeover bound and keeps its
issues claimed: the dispatch marker stays, a `taken-<run>` marker names the issues a hand fix owes, and no card goes back
to Todo.

These cases drive janitor.py end to end with the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh
and a temp state dir. No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests.test_janitor_card_done_base import N, janitor, sandbox, writes

RUN = "run-stacked"


def live_run(tmp_path: Path, age: float) -> Path:
    """One run with no verdict for #2030, started `age` seconds ago, its record fresh (so rule C reads it as live)."""
    sandbox(tmp_path, "echo 0", live=True)
    run = tmp_path / "co/runs" / RUN
    now = time.time()
    (run / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": RUN, "ts": now - age}) + "\n")
    os.utime(run / "record.jsonl", (now, now))
    return run


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
