"""Every terminal entry carries its condition in a comment (#245, spec #132): the composer in board_columns.py.

The janitor's behavior over a stubbed board is `contrib/squad-goal/scripts/test-janitor-terminal-comment.sh`."""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


@pytest.fixture(scope="module")
def columns():
    spec = importlib.util.spec_from_file_location("board_columns_terminal", SCRIPTS / "board_columns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_closed_names_the_merged_pull_request_and_the_test_on_the_merge(columns):
    text = columns.terminal_comment("Closed", {"base": "main", "pr": "301", "sha": "abcdef1234567890", "test": "test (3.11): success"})
    assert text.startswith(columns.TERMINAL_TAG)
    assert "#301" in text and "abcdef1" in text and "test (3.11): success" in text


def test_closed_with_no_check_on_the_merge_says_so(columns):
    text = columns.terminal_comment("Closed", {"base": "main", "pr": "301", "sha": "abcdef1234567890", "test": ""})
    assert "no check ran on merge commit abcdef1" in text


def test_closed_with_no_merged_pull_request_names_who_closed_it(columns):
    text = columns.terminal_comment("Closed", {"base": "main", "closed_by": "rafael"})
    assert "@rafael" in text and "no merged pull request" in text


def test_abandoned_names_who_decided_and_why(columns):
    text = columns.terminal_comment("Abandoned", {"who": "rafael", "why": "labelled wontfix"})
    assert text.startswith(columns.TERMINAL_TAG) and "@rafael" in text and "wontfix" in text


@pytest.mark.parametrize("column, facts", [
    ("Closed", {}),
    ("Closed", {"base": "main"}),
    ("Abandoned", {"why": "labelled wontfix"}),
    ("Abandoned", {"who": "rafael"}),
    ("Abandoned", None),
    ("Doing", {"who": "rafael", "why": "x"}),
])
def test_a_condition_the_facts_do_not_state_is_no_comment(columns, column, facts):
    assert columns.terminal_comment(column, facts) is None


@pytest.mark.parametrize("reason", ["not_planned", "NOT_PLANNED", "duplicate", "DUPLICATE"])
def test_closed_never_says_completed_for_an_issue_closed_as_not_planned(columns, reason):
    """#517: a not-planned issue moved into Closed by hand got "closed it as completed", a false record. A duplicate is
    no delivery either. The carrier answers lowercase, and the GraphQL enum's uppercase spelling is read too (#921)."""
    assert columns.terminal_comment("Closed", {"base": "dev", "closed_by": "rafael", "reason": reason}) is None


def test_a_reopened_reason_is_no_not_planned_verdict(columns):
    """#921: `reopened` is a real carrier value and names no close verdict, so the closed-by branch still states it."""
    text = columns.terminal_comment("Closed", {"base": "dev", "closed_by": "rafael", "reason": "reopened"})
    assert text is not None and "@rafael" in text
