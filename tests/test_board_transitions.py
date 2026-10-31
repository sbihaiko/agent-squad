"""The board writer records each proved card move in its repository's transition record (#897)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import shell_suites

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


@pytest.mark.heavy
def test_a_proved_move_is_one_transition_per_repository() -> None:
    r = shell_suites.run(SCRIPTS / "test-board-transitions.sh")
    assert r.returncode == 0, r.stdout + r.stderr
