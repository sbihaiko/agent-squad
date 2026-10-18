"""A spec's column is a fold over its sub-issues (the board table's rule); a closed spec whose child reopens keeps its column."""
import importlib.util
import itertools
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location("spec_fold", SCRIPTS / "spec_fold.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# the child columns, as the board names them: a worked table, never recomputed from the fold's own sets. None is no write:
# below Doing the spec keeps the column the owner gave it (#550)
CASES = [
    ([], None),
    (["Todo"], None),
    (["Backlog", "Todo"], None),
    (["Backlog"], None),
    ([""], None),
    (["Blocked", "Todo"], None),
    (["In Progress"], "Doing"),
    (["Code Complete", "Todo"], "Doing"),
    (["Testing", "Done"], "Doing"),
    (["Done", "In Progress"], "Doing"),
    (["Done"], "Closed"),
    (["Done", "Done"], "Closed"),
    (["Done", "Abandoned"], "Closed"),
    (["Abandoned", "Done", "Abandoned"], "Closed"),
    (["Abandoned"], "Abandoned"),
    (["Abandoned", "Abandoned"], "Abandoned"),
    (["Abandoned", "Todo"], None),
    (["Abandoned", "In Progress"], "Doing"),
    (["Landing", "Todo"], "Doing"),
    (["Merging", "Todo"], "Doing"),   # the live board's name for the column before the Pushing rename (#548)
    (["Pushing", "Todo"], "Doing"),
    (["Closed", "Closed", "Todo"], "Doing"),   # a delivered child is past Doing: the spec's work is under way
    (["Done", "Todo"], "Doing"),
    (["Closed", "Abandoned", "Backlog"], "Doing"),
]


@pytest.mark.parametrize("children,column", CASES)
def test_the_column_is_the_fold_of_the_children(children, column):
    assert _load().fold(children)[0] == column


def test_every_combination_of_child_columns_folds_forward_or_writes_nothing():
    fold = _load().fold
    for size in range(0, 4):
        for kids in itertools.product(["Backlog", "Todo", "In Progress", "Done", "Abandoned"], repeat=size):
            assert fold(list(kids))[0] in {None, "Doing", "Closed", "Abandoned"}


@pytest.mark.parametrize("current", ["Backlog", "Todo", "Doing", ""])
def test_a_spec_whose_children_are_below_doing_keeps_the_owners_column(current):
    assert _load().fold(["Todo", "Backlog"], current) == (None, False)


@pytest.mark.parametrize("current", ["Closed", "Abandoned"])
def test_a_terminal_spec_whose_child_reopens_to_todo_keeps_its_column_and_is_tagged(current):
    assert _load().fold(["Todo", "Abandoned"], current) == (current, True)


@pytest.mark.parametrize("current", ["Closed", "Abandoned"])
def test_a_terminal_spec_whose_child_reopens_keeps_its_column_and_is_tagged(current):
    column, reverted = _load().fold(["Done", "In Progress"], current)
    assert (column, reverted) == (current, True)


def test_a_spec_that_moves_forward_is_not_tagged():
    assert _load().fold(["Done"], "Doing") == ("Closed", False)
    assert _load().fold(["Done"], "Closed") == ("Closed", False)


def test_the_fold_takes_columns_only_and_makes_no_call():
    source = (SCRIPTS / "spec_fold.py").read_text()
    assert "subprocess" not in source and "gh_" not in source and "urllib" not in source
