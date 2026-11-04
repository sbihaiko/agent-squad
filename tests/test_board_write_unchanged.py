"""A board write is sent only when its value changes (#327).

The shell suites count every PATCH against a stubbed gh: a card move to the column the card already holds, and the janitor's
Run-field write over a Doing card whose holder and start are unchanged. A changed value is still written and proven by a read.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests import shell_suites

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


def _suite(name: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return shell_suites.run(SCRIPTS / name, extra_env=extra_env)


@pytest.mark.heavy
def test_a_move_to_the_column_the_card_holds_sends_no_patch() -> None:
    r = _suite("test-board-move-unchanged.sh")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.heavy
def test_the_janitor_writes_run_fields_only_when_the_holder_changes() -> None:
    r = _suite("test-janitor-run-fields-unchanged.sh")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.heavy
def test_the_move_suite_runs_without_the_loops_exported_variables() -> None:
    """env.sh exports SG_MOVE_BY=loop (#861), and a gate the live loop runs inherits it: the suite then reads a dispatch
    context where it asserts that a move with none records none (#864). The suite is handed those variables on purpose."""
    r = _suite("test-board-move-unchanged.sh", extra_env={"SG_MOVE_BY": "loop", "SG_CHECKOUT": "/live/loop/checkout"})
    assert r.returncode == 0, r.stdout + r.stderr
