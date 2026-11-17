"""The hub's own Wait read keeps the loop's rule, the missing input and a live run's name (#2046, #1895).

The hub answers the same Wait value the loop writes. An unread input answers unknown and names what is missing.
A live run keeps its name beside the Wait.
"""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"


def card(**facts):
    base = {"record": True, "labels": [], "run_live": False, "queued": None}
    base.update(facts)
    return base


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_card_wait", SCRIPTS / "hub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hub_answers_the_same_wait_value_the_loop_writes(hub):
    board_wait = hub.board_wait
    for facts in (card(queued="gate slot"), card(labels=["frozen:owner"], queued="gate slot"), card(queued=None)):
        assert hub.card_wait(facts)["wait"] == board_wait.wait_of(facts).name


def test_the_hub_names_the_wait_field_the_loop_writes(hub):
    assert hub.issue_state.WAIT == "Wait"


def test_an_unread_record_answers_unknown_and_names_the_record(hub):
    answer = hub.card_wait(card(record=False))

    assert answer["wait"] == "unknown" and answer["missing"] == "record"


def test_a_live_run_keeps_its_name_beside_its_wait(hub):
    answer = hub.card_wait(card(run_live=True, run="run-abc"))

    assert answer["wait"] == "live run" and answer["run"] == "run-abc"


def test_a_live_run_with_an_unread_record_names_the_missing_input_and_keeps_the_run(hub):
    answer = hub.card_wait(card(run_live=True, record=False, run="run-abc"))

    assert answer == {"wait": "unknown", "missing": "record", "run": "run-abc"}


def test_the_hub_shows_the_carried_wait_value_the_loop_wrote(hub):
    answer = hub.card_wait(card(queued="gate slot"), carried="review slot")

    assert answer["wait"] == "review slot"


def test_an_unread_record_keeps_unknown_even_when_the_field_carries_a_value(hub):
    answer = hub.card_wait(card(record=False), carried="gate slot")

    assert answer == {"wait": "unknown", "missing": "record", "run": None}


def test_a_card_between_the_dispatch_and_the_first_call_reads_run_start(hub):
    answer = hub.card_wait(card(board={"state": "written", "column": "Doing"}, column="Todo", called=False))

    assert answer["wait"] == "run start" and answer["missing"] is None


def test_the_trip_shows_doing_with_run_start_and_then_doing_with_no_reason(hub):
    board = {"state": "written", "column": "Doing"}
    before = hub.card_wait(card(board=dict(board), column="Doing", called=False), carried="run start")
    after = hub.card_wait(card(board=dict(board), column="Doing", called=True), carried="none")

    assert [before["wait"], after["wait"]] == ["run start", "none"]
    assert board["column"] == "Doing", "the first call clears the reason and moves no column"


@pytest.mark.parametrize("state", ["pending", "dropped"])
def test_a_pending_or_dropped_write_answers_unknown_with_the_refusal(hub, state):
    answer = hub.card_wait(card(board={"state": state, "column": "Doing"}, column="Todo", called=False))

    assert answer["wait"] == "unknown" and answer["missing"] == f"the card write is {state}"
