"""The journal-versus-history guard (#1430): a gate attempt ahead of dev leaves a Gating stretch on the board.

The guard reads the attempt journal and the history store only, so it answers the same after a restart and on a re-read.
A card whose record holds no Gating interval covering the attempt's own instant is named: a gate that never wrote its
column cannot be measured, and that is a defect, never a warning.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import board_gate_check  # noqa: E402
import board_history  # noqa: E402
import gate_attempts  # noqa: E402

CARD = 57
T0 = "2026-11-05T10:00:00Z"
T1 = "2026-11-05T10:10:00Z"
T2 = "2026-11-05T10:20:00Z"
T3 = "2026-11-05T11:00:00Z"


def _epoch(text: str) -> int:
    from datetime import UTC, datetime
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).timestamp())


def _store(tmp_path: Path, statuses) -> dict:
    """One card's history: added to the board, then each status move in order. load_store reads the file back, so the
    guard answers from the store a restart would read."""
    path = tmp_path / "hub-history.o_r.jsonl"
    events = [{"t": T0, "type": "added"}]
    for at, status in statuses:
        events.append({"t": at, "type": "status", "status": status})
    board_history.record_issue(path, CARD, T0, T1, events, True, _epoch(T1))
    return board_history.load_store(path)


def _attempt(tmp_path: Path, at: str, card: int = CARD, tip: str = "tip1") -> Path:
    journal = tmp_path / "hub-flow.o_r.jsonl"
    gate_attempts.record_attempt(journal, card, tip, "pass", "", now=_epoch(at), wave="W1")
    return journal


def test_an_attempt_inside_a_gating_stretch_passes(tmp_path):
    journal = _attempt(tmp_path, "2026-11-05T10:25:00Z")
    store = _store(tmp_path, [(T1, "Code Complete"), (T2, "Gating"), (T3, "Closed")])
    ok, problems = board_gate_check.gating_recorded(journal, store)
    assert ok and problems == []


def test_an_attempt_whose_card_never_read_gating_is_named(tmp_path):
    journal = _attempt(tmp_path, "2026-11-05T10:25:00Z")
    store = _store(tmp_path, [(T1, "Code Complete")])
    ok, problems = board_gate_check.gating_recorded(journal, store)
    assert not ok
    assert len(problems) == 1 and f"#{CARD}" in problems[0] and "Gating" in problems[0], problems


def test_the_guard_answers_the_same_on_a_reread_and_a_restart(tmp_path):
    journal = _attempt(tmp_path, "2026-11-05T10:25:00Z")
    _store(tmp_path, [(T1, "Code Complete")])
    store_path = tmp_path / "hub-history.o_r.jsonl"
    first = board_gate_check.gating_recorded(journal, board_history.load_store(store_path))
    second = board_gate_check.gating_recorded(journal, board_history.load_store(store_path))
    assert first == second and not first[0], (first, second)


def test_an_attempt_after_the_card_left_gating_is_named(tmp_path):
    """The stretch is closed by the card's next column: an attempt that lands well after the Gating interval ended has no
    stretch of its own. The board's poll may record the entry late, so only a lag within the settle bound is covered."""
    journal = _attempt(tmp_path, "2026-11-05T09:50:00Z")
    store = _store(tmp_path, [(T1, "Code Complete"), (T2, "Gating"), (T3, "Closed")])
    ok, problems = board_gate_check.gating_recorded(journal, store)
    assert not ok and f"#{CARD}" in problems[0], problems


def test_an_attempt_on_a_card_already_in_dev_is_not_a_defect(tmp_path):
    """A card Closed before the attempt is in dev: the gate re-ran on landed work, and no Gating stretch is expected."""
    journal = _attempt(tmp_path, "2026-11-05T11:30:00Z")
    store = _store(tmp_path, [(T1, "Code Complete"), (T2, "Gating"), (T3, "Closed")])
    ok, problems = board_gate_check.gating_recorded(journal, store)
    assert ok and problems == []


def test_a_wave_level_attempt_names_no_card_and_is_skipped(tmp_path):
    journal = _attempt(tmp_path, "2026-11-05T10:25:00Z", card=0)
    store = _store(tmp_path, [])
    ok, problems = board_gate_check.gating_recorded(journal, store)
    assert ok and problems == []
