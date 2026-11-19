"""The Todo card limit holds the Backlog-to-Todo move only (#2437, ADR-0148).

The shell suite drives board.py through its command door with a stubbed gh: a full Todo refuses a card that leaves Backlog
for Todo and names the limit, while a bug whose card is added in Todo still enters a full Todo at once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import shell_suites

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


@pytest.mark.heavy
def test_the_todo_limit_holds_backlog_to_todo_moves_only() -> None:
    r = shell_suites.run(SCRIPTS / "test-board-todo-limit.sh")
    assert r.returncode == 0, r.stdout + r.stderr
