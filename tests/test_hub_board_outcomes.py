"""The outcome of a closed item, from the shipped definition (ADR-0141).

The outcome set is the shipped definition's, read through board_columns; the rule lives there. The board carried it as
one filter of the Kinds view, which the owner took out (2026-10-16): the mapping is read here, off the module, and no
longer off a board payload. Fixture style follows tests/test_hub_board_cases.py.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
OUTCOMES = ["Merged", "Abandoned", "Failed", "Reverted"]   # the shipped definition's outcomes, in its order


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cols():
    return _load("board_columns_outcomes", SCRIPTS / "board_columns.py")


def test_the_outcome_set_is_the_shipped_definitions(cols):
    assert cols.shipped_outcomes() == OUTCOMES


def test_a_merged_issue_an_abandoned_one_and_a_failed_test_never_read_as_one_thing(cols):
    """The column decides an issue, the label decides a test case, and the three never collapse into one outcome."""
    merged = cols.outcome_of(True, column="Closed")
    abandoned = cols.outcome_of(True, column="Abandoned")
    failed = cols.outcome_of(True, column="Closed", labels=["outcome:failed"])
    assert {merged, abandoned, failed} == {"Merged", "Abandoned", "Failed"}


def test_an_open_item_carries_no_outcome(cols):
    assert cols.outcome_of(False, column="Todo") is None


def test_an_item_whose_column_the_set_does_not_name_is_reported_not_guessed(cols):
    """A label outside the set comes back as written, which is how the caller reports a gap instead of inventing one."""
    assert cols.outcome_of(True, column="Closed", labels=["outcome:shelved"]) == "shelved"


def test_an_outcome_the_set_does_not_name_is_refused(cols):
    with pytest.raises(ValueError):
        cols.filter_groups([], outcome="shelved")
