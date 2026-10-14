"""Issue #27: a retired profile completes the loop while it migrates."""

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"


def run_shell_test(name: str) -> None:
    result = subprocess.run(
        ["bash", str(SCRIPTS / name)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.heavy
def test_retired_profile_migration_cycle():
    run_shell_test("test-retired-settings.sh")


@pytest.mark.heavy
def test_address_seam_accepts_a_retired_profile():
    run_shell_test("test-address.sh")
