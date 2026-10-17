"""An issue a live run holds with no child is back in the queue (#480).

A dispatch hands one run several issues, and every card goes to Doing. The router may choose a subset, and the engine may
reject a spawn it cannot fund (`spend_over_remainder`). An issue with no accepted child is worked by nobody, so once the
run's spawn batch is decided its card goes back to Todo and its dispatch marker goes, and a later run can take it. A
marker written after that decision belongs to a newer dispatch and stays.

These cases drive janitor.py end to end with the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh
and a temp state dir. No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests.test_janitor_card_done_base import janitor, sandbox, writes

HELD, FREE = 2030, 2031  # the child the engine accepted, and the issue whose spawn it rejected


def live_run(tmp_path: Path, *, decided: bool = True, marker_age: float = 3600) -> Path:
    """One live run (no verdict, a fresh record) that holds #2030 and #2031. Its spawn batch accepts the child of #2030
    and rejects the one of #2031; `decided` adds the child's call_start after the batch."""
    sandbox(tmp_path, "echo 0")
    run = tmp_path / "co/runs/run-stacked"
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {HELD}:\n\nCHILD — GitHub issue(s) {FREE}:\n")
    now = time.time()
    ev = [
        {"event": "run_start", "run_id": "run-stacked", "ts": now - 600},
        {"event": "outcome", "node": "router", "outcome": "pass", "reasons": [], "ts": now - 120},
        {"event": "spawn_requested", "parent": "router", "requested": {"prompt": f"Fix GitHub issue #{HELD}"}, "ts": now - 119},
        {"event": "spawn_accepted", "child": "route.router.1", "parent": "router", "request_key": 3,
         "definition": {"prompt": f"Fix GitHub issue #{HELD}"}, "ts": now - 119},
        {"event": "spawn_requested", "parent": "router", "requested": {"prompt": f"Fix GitHub issue #{FREE}"}, "ts": now - 118},
        {"event": "spawn_rejected", "parent": "router", "reason": "spend_over_remainder", "request_key": 5, "ts": now - 118},
    ]
    if decided:
        ev.append({"event": "call_start", "node": "route.router.1", "ts": now - 117})
    (run / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    auto = tmp_path / "st/auto"
    for n in (HELD, FREE):
        m = auto / f"disp-{n}"
        m.write_text("run-stacked\n")
        os.utime(m, (now - marker_age, now - marker_age))
    return auto


@pytest.mark.heavy
def test_an_issue_whose_spawn_was_rejected_goes_back_to_the_queue(tmp_path):
    """The measured defect (#480): the rejected issue stayed in Doing, its marker set, with no child working it."""
    auto = live_run(tmp_path)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Todo {FREE}" in log, f"the unheld issue did not go back to the queue: {log}"
    assert f"In Progress {FREE}" not in log, f"the unheld issue stayed in progress: {log}"
    assert not (auto / f"disp-{FREE}").exists(), "the dispatch marker held an issue no child works"
    assert f"ADD o 4 In Progress {HELD}" in log, f"the held issue left in progress: {log}"
    assert (auto / f"disp-{HELD}").exists(), "the marker of the held issue went"


@pytest.mark.heavy
def test_an_undecided_spawn_batch_keeps_every_issue_in_progress(tmp_path):
    """Before the batch ends, a spawn not yet answered may still be accepted: nothing is released."""
    auto = live_run(tmp_path, decided=False)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"Todo {FREE}" not in log, f"an issue was released before the spawn batch ended: {log}"
    assert (auto / f"disp-{FREE}").exists(), "the marker went before the spawn batch ended"


@pytest.mark.heavy
def test_a_marker_written_after_the_decision_belongs_to_a_newer_dispatch(tmp_path):
    """A later dispatch claimed the issue again: its marker is newer than this run's decision and stays."""
    auto = live_run(tmp_path, marker_age=10)
    janitor(tmp_path)
    assert (auto / f"disp-{FREE}").exists(), "the janitor dropped the marker of a newer dispatch"
