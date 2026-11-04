"""No test writes to the SG_STATE the suite inherited (#1328).

A gate the loop runs inherits the live loop's SG_STATE. conftest points SG_STATE at a fresh tmp dir for every test, so a test
that passes no state dir of its own writes nothing into the live loop's api.log or cooldown. This guard runs inside one test:
the inherited value cannot be put back for the test's own run, so it asserts that the value the test sees is its own tmp dir
and not the one the session started with (ORIGINAL_SG_STATE, captured by conftest at import).
"""
import os
from pathlib import Path

from tests import conftest


def test_sg_state_inside_a_test_is_its_own_tmp_dir_not_the_inherited_one(tmp_path_factory):
    state = os.environ.get("SG_STATE", "")
    basetemp = tmp_path_factory.getbasetemp()
    assert state, "SG_STATE is unset inside a test: the suite must give each test its own state dir"
    assert basetemp in Path(state).resolve().parents, f"SG_STATE {state} is not under the pytest tmp root {basetemp}"
    original = getattr(conftest, "ORIGINAL_SG_STATE", None)
    if original:
        assert state != original, "SG_STATE inside a test is the inherited live state dir"
