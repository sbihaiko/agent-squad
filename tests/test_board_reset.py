"""A reset names itself on its issue: one closed label set and one comment shape (#1904)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLUMNS_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "board_columns.py"


def _columns():
    spec = importlib.util.spec_from_file_location("board_columns", COLUMNS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reset_labels_are_the_closed_set():
    columns = _columns()
    assert sorted(columns.RESET_LABEL.values()) == [
        "board-reset:launch-refused",
        "board-reset:no-delivery",
        "board-reset:reopened",
    ]


def test_each_reset_reason_has_a_cause():
    columns = _columns()
    assert set(columns.RESET_CAUSE) == set(columns.RESET_LABEL)


def test_reset_comment_names_columns_cause_time_and_event():
    columns = _columns()
    body = columns.reset_comment("Code Complete", "Todo", "no-landing", "2026-11-17T06:58:36Z", "run-a")
    assert body.splitlines() == [
        "Board reset: the card left Code Complete and reached Todo.",
        "Cause: the run landed nothing.",
        "Time: 2026-11-17T06:58:36Z.",
        "Event: run-a.",
    ]
