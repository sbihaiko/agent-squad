"""A board write is sent only when its value changes (#327).

The shell suites count every PATCH against a stubbed gh: a card move to the column the card already holds, and the janitor's
Run-field write over a Doing card whose holder and start are unchanged. A changed value is still written and proven by a read.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


def _suite(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPTS / name)], capture_output=True, text=True, timeout=120)


def test_a_move_to_the_column_the_card_holds_sends_no_patch() -> None:
    r = _suite("test-board-move-unchanged.sh")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.heavy
def test_the_janitor_writes_run_fields_only_when_the_holder_changes() -> None:
    r = _suite("test-janitor-run-fields-unchanged.sh")
    assert r.returncode == 0, r.stdout + r.stderr
