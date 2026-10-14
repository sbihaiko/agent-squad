"""Code Complete and Merging come from the pull request's state, read from the loop's own review line (#244)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


@pytest.fixture(scope="module")
def columns():
    spec = importlib.util.spec_from_file_location("board_columns_pr", SCRIPTS / "board_columns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GREEN = "#7 abcdef123 clean CI=success:3 APPROVE"


@pytest.mark.parametrize("line,labels", [
    ("#7 abcdef123 dirty CI=none APPROVE", set()),                   # a conflict
    ("#7 abcdef123 clean CI=failure:1,success:2 APPROVE", set()),    # a red check
    ("#7 abcdef123 clean CI=success:3 CHANGES", set()),              # a review that asks for changes
    ("", set()),                                                     # no review given on this head
    ("#7 abcdef123 blocked CI=success:3 APPROVE", set()),            # GitHub does not call it mergeable
    (GREEN, {"hold"}),                                               # held
    (GREEN, {"needs-human"}),                                        # held
])
def test_a_pull_request_not_mergeable_yet_is_not_green(columns, line, labels):
    assert columns.pr_green(line, labels) is False


def test_a_green_mergeable_unheld_pull_request_is_green(columns):
    assert columns.pr_green(GREEN, {"enhancement"}) is True


def test_a_red_pull_request_puts_the_card_in_code_complete(columns):
    assert columns.pr_column(False, "Doing") == ("Code Complete", None)
    assert columns.pr_column(False, "Code Complete") == ("Code Complete", None)


def test_a_green_pull_request_puts_the_card_in_merging(columns):
    assert columns.pr_column(True, "Code Complete") == ("Merging", None)
    assert columns.pr_column(True, "Merging") == ("Merging", None)


def test_a_pull_request_that_turns_red_keeps_its_card_in_merging_with_a_badge(columns):
    assert columns.pr_column(False, "Merging") == ("Merging", columns.PR_RED_BADGE)
