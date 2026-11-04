"""No test writes to the SG_STATE the suite inherited (#1328).

A gate the loop runs inherits the live loop's SG_STATE. conftest points SG_STATE at a fresh tmp dir for every test, so a test
that passes no state dir of its own writes nothing into the live loop's api.log or cooldown. This guard runs inside one test:
the inherited value cannot be put back for the test's own run, so it asserts that the value the test sees is its own tmp dir
and not the one the session started with (ORIGINAL_SG_STATE, captured by conftest at import).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest

REPO = Path(__file__).resolve().parent.parent


def test_sg_state_inside_a_test_is_its_own_tmp_dir_not_the_inherited_one(tmp_path_factory):
    state = os.environ.get("SG_STATE", "")
    basetemp = tmp_path_factory.getbasetemp()
    assert state, "SG_STATE is unset inside a test: the suite must give each test its own state dir"
    assert basetemp in Path(state).resolve().parents, f"SG_STATE {state} is not under the pytest tmp root {basetemp}"
    original = getattr(conftest, "ORIGINAL_SG_STATE", None)
    if original:
        assert state != original, "SG_STATE inside a test is the inherited live state dir"


def test_no_test_inherits_a_loop_pass_name():
    """A gate run inside a loop pass exports its pass name; no test may carry it into its own calls (#1328)."""
    assert "SG_GH_PASS" not in os.environ


@pytest.mark.heavy
def test_an_inherited_loop_state_and_pass_name_reach_no_import_or_test(tmp_path):
    """A pytest run started with a loop's SG_STATE and SG_GH_PASS in its env writes no api.log into that inherited dir:
    neither an import-time read nor a test body reaches it (#1328)."""
    sentinel = tmp_path / "sentinel-state"
    sentinel.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")}
    env.update(SG_STATE=str(sentinel), SG_GH_PASS="childcc", PROBE_SENTINEL=str(sentinel), PYTHONPATH=str(REPO))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/probe_inherited_state_1328.py", "-p", "no:deepeval", "-p", "no:xdist", "-q"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    assert not (sentinel / "api.log").exists(), (sentinel / "api.log").read_text()
