"""The loop archives a terminal card a week after its issue closed, and only the cards that earn it."""
import datetime as dt
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
REPO = "sbihaiko/agent-squad"
NOW = dt.datetime(2026, 11, 16, 12, tzinfo=dt.UTC)


def _module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ba = _module("board_archive")


def card(issue, column="Closed", days=10, repo=REPO, state="closed"):
    closed_at = (NOW - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"issue": issue, "repo": repo, "column": column, "state": state, "closed_at": closed_at,
            "item": f"PVTI_{issue}"}


@pytest.fixture
def no_github(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the selection called GitHub")
    monkeypatch.setattr(subprocess, "run", refuse)


def select(cards, protected=frozenset(), days=7):
    return [c["issue"] for c in ba.select(cards, repo=REPO, now=NOW, after_days=days, protected=protected)]


def test_selects_only_terminal_closed_cards_older_than_seven_days(no_github):
    board = [
        card(1),
        card(2, column="Abandoned"),
        card(3, days=6),
        card(4, days=7),
        card(5, column="Todo"),
        card(6, state="open"),
    ]
    assert select(board) == [1, 2]


def test_a_card_of_another_repository_is_never_selected(no_github):
    assert select([card(1), card(2, repo="sbihaiko/other")]) == [1]


def test_a_child_of_an_open_spec_is_never_selected(no_github):
    assert select([card(1), card(2)], protected={2}) == [1]


def test_age_bound_is_a_profile_setting_of_seven_days_by_default(monkeypatch):
    monkeypatch.delenv("SG_ARCHIVE_AFTER_DAYS", raising=False)
    assert ba.after_days() == 7
    monkeypatch.setenv("SG_ARCHIVE_AFTER_DAYS", "3")
    assert ba.after_days() == 3
    assert select([card(1, days=4)], days=ba.after_days()) == [1]


def test_writes_are_paced_and_stop_on_a_rate_limit_naming_each_archived_card(no_github):
    writes, sleeps, said = [], [], []

    def write(item):
        writes.append(item)
        if item == "PVTI_2":
            raise ba.RateLimited("secondary rate limit")

    done = ba.archive([card(1), card(2), card(3)], write=write, pace=2.0, sleep=sleeps.append, say=said.append)
    assert done == [1]
    assert writes == ["PVTI_1", "PVTI_2"]
    assert sleeps == [2.0]
    assert said == ["ARCHIVED #1", "ARCHIVE STOPPED: secondary rate limit; 1 archived, 2 left for the next pass"]


def test_an_archived_card_is_no_card_to_the_readers(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    issue_state = _module("issue_state")
    binding = issue_state.BoardBinding("sbihaiko", 5, REPO, state_dir=tmp_path)
    binding.status_field = lambda: {"id": "F", "opt": {}}
    base = "https://api.github.com/repos/sbihaiko/agent-squad"
    binding.api = lambda *args, **kwargs: f"1\tTodo\t{base}\t\n2\tClosed\t{base}\t2026-11-01T00:00:00Z\n"
    assert binding._read_all_live() == {"1": "Todo"}


def test_archive_write_reads_a_rate_limit_answer_as_a_stop(monkeypatch):
    answer = subprocess.CompletedProcess([], 1, stdout="", stderr="was submitted too quickly: secondary rate limit")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: answer)
    with pytest.raises(ba.RateLimited):
        ba.archive_item("PVTI_1")
