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
# a spec reads Todo while any child is in flight, and keeps the owner's column when none is (#1131). The children never give
# a spec a terminal column: with no ticket in flight the spec's own issue state answers, and an open issue answers none (#1906)
CASES = [
    ([], None),
    (["Todo"], "Todo"),
    (["Backlog", "Todo"], "Todo"),
    (["Backlog"], None),
    ([""], None),
    (["Blocked", "Todo"], "Todo"),
    (["Blocked"], None),
    (["In Progress"], "Todo"),
    (["Doing"], "Todo"),
    (["Code Complete", "Todo"], "Todo"),
    (["Code Review", "Todo"], "Todo"),   # a child whose review holds it is in flight (#603)
    (["Code Review", "Done"], "Todo"),
    (["Done", "In Progress"], "Todo"),
    (["Done"], None),
    (["Done", "Done"], None),
    (["Done", "Abandoned"], None),
    (["Abandoned", "Done", "Abandoned"], None),
    (["Abandoned"], None),
    (["Abandoned", "Abandoned"], None),
    (["Abandoned", "Todo"], "Todo"),
    (["Abandoned", "In Progress"], "Todo"),
    (["Landing", "Todo"], "Todo"),
    (["Merging", "Todo"], "Todo"),
    (["Pushing", "Todo"], "Todo"),
    (["Testing", "Todo"], "Todo"),
    (["Landing"], None),
    (["Merging"], None),
    (["Pushing"], None),
    (["Testing"], None),
    (["Closed", "Closed", "Todo"], "Todo"),   # one open child keeps the spec in Todo
    (["Done", "Todo"], "Todo"),
    (["Closed", "Abandoned", "Backlog"], None),
]


@pytest.mark.parametrize("children,column", CASES)
def test_the_column_is_the_fold_of_the_children(children, column):
    assert _load().fold(children, spec=True)[0] == column


def test_every_combination_of_child_columns_folds_forward_or_writes_nothing():
    fold = _load().fold
    for size in range(0, 4):
        for kids in itertools.product(["Backlog", "Todo", "In Progress", "Done", "Abandoned"], repeat=size):
            assert fold(list(kids), spec=True)[0] in {None, "Todo", "Closed", "Abandoned"}


# a spec's own issue state, as `own`: the column the issue earns (Closed or Abandoned), or None while it is open. The state
# answers only when no ticket is in flight: a moved ticket still reads the queue column (#1906)
OWN_CASES = [
    ([], None, None),
    ([], "Closed", "Closed"),
    ([], "Abandoned", "Abandoned"),
    (["Done"], None, None),
    (["Done"], "Closed", "Closed"),
    (["Done", "Abandoned"], "Closed", "Closed"),
    (["Abandoned"], "Abandoned", "Abandoned"),
    (["Backlog", "Done"], "Closed", "Closed"),
    (["Todo"], "Closed", "Todo"),
    (["In Progress"], "Abandoned", "Todo"),
]


@pytest.mark.parametrize("children,own,column", OWN_CASES)
def test_a_spec_with_no_sub_issue_in_flight_follows_its_own_issue_state(children, own, column):
    assert _load().fold(children, spec=True, own=own)[0] == column


def test_a_spec_with_no_sub_issue_in_flight_never_folds_to_the_queue():
    """The fold answers when no sub-issue is open, and its answer is never the column a dispatcher reads (#1906)."""
    fold = _load().fold
    for size in range(0, 4):
        for kids in itertools.product(["Backlog", "Done", "Abandoned", "Closed"], repeat=size):
            assert fold(list(kids), spec=True)[0] != "Todo", kids


def test_a_spec_whose_own_issue_is_open_never_keeps_a_terminal_column():
    """Every sub-issue closed and the spec's own issue still open: the card follows the issue, so the terminal column it
    carries is the one to undo, never one the children give it (ADR-0143 rule 2, #1906)."""
    assert _load().fold(["Done", "Done"], "Closed", spec=True) == ("Closed", True)
    assert _load().fold([], "Abandoned", spec=True) == ("Abandoned", True)


@pytest.mark.parametrize("current", ["Backlog", "Todo", "Doing", ""])
def test_a_spec_whose_children_are_all_queued_keeps_the_owners_column(current):
    assert _load().fold(["Backlog", "Backlog"], current, spec=True) == (None, False)


@pytest.mark.parametrize("owner", ["Todo", "Backlog"])
def test_a_spec_with_a_ticket_in_flight_reads_todo_whatever_column_it_left(owner):
    """The fold is the spec card's only writer (ADR-0143): a ticket in flight lifts the card to Todo; it never follows the
    ticket into a work column, and the move back there is the loop undoing its own write (#1131)."""
    assert _load().fold(["Todo", "Backlog"], "Doing", owner, spec=True) == ("Todo", False)


def test_the_return_to_the_owners_column_needs_a_card_in_doing_and_a_column_to_return_to():
    fold = _load().fold
    assert fold(["Backlog"], "Todo", "Backlog", spec=True) == (None, False), "a card below Doing keeps its column"
    assert fold(["Backlog"], "Doing", "", spec=True) == (None, False), "with no owner column there is nothing to return to"
    assert fold(["Backlog"], "Doing", "Closed", spec=True) == (None, False), "a terminal column is never a return"
    assert fold(["Backlog"], "In Progress", "Todo", spec=True) == ("Todo", False), "no child is in flight, so the card returns"
    assert fold(["In Progress"], "In Progress", "Backlog", spec=True) == ("Todo", False), "a live child still reads Todo"


@pytest.mark.parametrize("current", ["Closed", "Abandoned"])
def test_a_terminal_spec_whose_child_reopens_to_todo_keeps_its_column_and_is_tagged(current):
    assert _load().fold(["Todo", "Abandoned"], current, spec=True) == (current, True)


@pytest.mark.parametrize("current", ["Closed", "Abandoned"])
def test_a_terminal_spec_whose_child_reopens_keeps_its_column_and_is_tagged_again(current):
    column, reverted = _load().fold(["Done", "In Progress"], current, spec=True)
    assert (column, reverted) == (current, True)


def test_a_spec_that_follows_its_own_issue_is_not_tagged():
    """The card reaches the terminal column from its own issue, never from its children (#1906)."""
    assert _load().fold(["Done"], "Doing", spec=True, own="Closed") == ("Closed", False)
    assert _load().fold(["Done"], "Closed", spec=True, own="Closed") == ("Closed", False)


def test_a_non_spec_parent_still_folds_to_the_work_column():
    """A run parent is a roll-up, not a spec: the Doing lift is still its rule (#942)."""
    assert _load().fold(["In Progress"])[0] == "Doing"
    assert _load().fold(["Todo"])[0] is None


def test_the_fold_takes_columns_only_and_makes_no_call():
    source = (SCRIPTS / "spec_fold.py").read_text()
    assert "subprocess" not in source and "gh_" not in source and "urllib" not in source
