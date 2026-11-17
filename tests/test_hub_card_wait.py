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
