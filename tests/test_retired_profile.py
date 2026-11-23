"""Issue #27: a retired profile completes the loop while it migrates."""

import pathlib

import pytest

from tests import shell_suites

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"


def run_shell_test(name: str) -> None:
    result = shell_suites.run(SCRIPTS / name)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.heavy
def test_retired_profile_migration_cycle():
    run_shell_test("test-retired-settings.sh")


@pytest.mark.heavy
def test_address_seam_accepts_a_retired_profile():
    run_shell_test("test-address.sh")


@pytest.mark.heavy
@pytest.mark.slow(reason="runs the address shell suite, a real script whose wall time is the machine's")
def test_the_address_suite_runs_without_the_loops_exported_defaults(monkeypatch):
    """env.sh exports its own defaults (SG_WAVE_GATES, SG_FRONTIER_SKIP_LABELS, SG_RUN_REVIEW), and a gate the live loop
    runs inherits them, so a profile's `:=` default lost to the loop's value where the suite proves it wins (#864). The
    suite is handed a clean environment."""
    monkeypatch.setenv("SG_WAVE_GATES", "bash /live/loop/fast-gate.sh")
    monkeypatch.setenv("SG_FRONTIER_SKIP_LABELS", "spec,wontfix")
    monkeypatch.setenv("SG_RUN_REVIEW", "/bin/true")
    run_shell_test("test-address.sh")
