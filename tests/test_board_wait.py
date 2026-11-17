"""The Wait field names what a card waits for (#1902, ADR-0144, ADR-0170).

The rule answers one closed value per card. The writer sends the value only where it changes, at most one batch per call.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


board_wait = _load("board_wait_under_test", SCRIPTS / "board_wait.py")


def ledger(st, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_STATE=str(st), SG_REPO="a/b")
    return subprocess.run([sys.executable, str(SCRIPTS / "issue_ledger.py"), *args], capture_output=True, text=True, env=env, timeout=15)


def card(**facts):
    base = {"record": True, "labels": [], "run_live": False, "queued": None}
    base.update(facts)
    return base


class Binding:
    """A stub carrier. `carried` maps an issue to the Wait name it holds; `items` maps an issue to its item id."""

    def __init__(self, carried, items):
        self.carried = dict(carried)
        self.item_map = items
        self.calls = []

    def read_waits(self):
        return dict(self.carried)

    def items(self):
        return dict(self.item_map)

    def write_wait(self, item_id, name):
        self.calls.append((item_id, name))


def test_the_wait_field_holds_the_twelve_names_in_order():
    assert board_wait.WAITS == (
        "none", "run start", "run slot", "gate slot", "wave entry", "review slot", "fix round",
        "live run", "owner", "dependency", "not startable", "unknown")


def test_an_unread_record_answers_unknown_and_names_the_record_not_none():
    answer = board_wait.wait_of(card(record=False))

    assert answer.name == "unknown" and answer.missing == "record"


def test_a_card_with_no_queue_answers_none_with_no_missing_input():
    answer = board_wait.wait_of(card(queued=None))

    assert answer.name == "none" and answer.missing is None


def test_a_card_with_no_queued_key_answers_unknown_and_names_the_queue_input():
    facts = card()
    del facts["queued"]

    answer = board_wait.wait_of(facts)

    assert answer.name == "unknown" and answer.missing == "queued"


def test_a_queued_work_wait_answers_its_own_name():
    assert board_wait.wait_of(card(queued="gate slot")).name == "gate slot"


def test_a_live_run_answers_live_run_before_a_queued_wait():
    answer = board_wait.wait_of(card(run_live=True, queued="review slot"))

    assert answer.name == "live run"


def test_an_owner_freeze_answers_owner_and_never_a_queued_work_wait():
    answer = board_wait.wait_of(card(labels=["frozen:owner"], queued="gate slot"))

    assert answer.name == "owner"


def test_a_dependency_freeze_answers_dependency_and_never_a_queued_work_wait():
    answer = board_wait.wait_of(card(labels=["frozen:dependency"], queued="fix round"))

    assert answer.name == "dependency"


def test_a_not_startable_label_answers_not_startable_and_never_a_work_wait():
    answer = board_wait.wait_of(card(labels=["not-startable"], queued="run start"))

    assert answer.name == "not startable"


def test_the_writer_sends_no_write_for_a_value_the_card_already_carries():
    binding = Binding(carried={"7": "gate slot"}, items={"7": "I7"})

    written = board_wait.sync_waits(binding, {"7": card(queued="gate slot")})

    assert written == {} and binding.calls == []


def test_the_writer_sends_the_changed_value_and_names_the_held_card_only_by_wait():
    binding = Binding(carried={}, items={"8": "I8"})

    written = board_wait.sync_waits(binding, {"8": card(labels=["frozen:owner"], queued="gate slot")})

    assert binding.calls == [("I8", "owner")]
    assert written["8"].name == "owner"


def test_the_writer_sends_at_most_one_batch_per_call():
    items = {str(n): f"I{n}" for n in range(1, 6)}
    cards = {str(n): card(queued="run start") for n in range(1, 6)}
    binding = Binding(carried={}, items=items)

    written = board_wait.sync_waits(binding, cards, batch=2)

    assert len(binding.calls) == 2 and len(written) == 2


def test_the_writer_skips_a_card_that_is_not_on_the_board():
    binding = Binding(carried={}, items={})

    assert board_wait.sync_waits(binding, {"9": card(queued="run start")}) == {}
    assert binding.calls == []


def test_the_writer_reports_an_unknown_with_the_missing_input_it_names():
    binding = Binding(carried={}, items={"10": "I10"})

    written = board_wait.sync_waits(binding, {"10": card(record=False)})

    assert written["10"].name == "unknown" and written["10"].missing == "record"
    assert binding.calls == [("I10", "unknown")]


def test_a_settled_dispatch_write_reaches_doing_with_run_start_until_the_first_call(tmp_path):
    assert ledger(tmp_path, "board", "a/b#7", "run-1", "Doing", "/req/1").stdout.strip() == "recorded"
    assert ledger(tmp_path, "boardw", "a/b#7", "run-1", "Doing", "autobug dispatch").stdout.strip() == "written"
    board = json.loads(ledger(tmp_path, "read", "a/b#7", "board").stdout)

    column, wait = board_wait.dispatch_state(board, stood="Todo", called=False)

    assert column == "Doing" and wait.name == "run start" and wait.missing is None


def test_the_first_call_clears_run_start_and_never_moves_the_column(tmp_path):
    ledger(tmp_path, "board", "a/b#7", "run-1", "Doing", "/req/1")
    ledger(tmp_path, "boardw", "a/b#7", "run-1", "Doing", "autobug dispatch")
    board = json.loads(ledger(tmp_path, "read", "a/b#7", "board").stdout)

    column, wait = board_wait.dispatch_state(board, stood="Todo", called=True)

    assert column == "Doing" and wait.name == "none" and wait.missing is None


def test_a_pending_write_holds_the_card_where_it_stood_and_names_the_refusal(tmp_path):
    ledger(tmp_path, "board", "a/b#7", "run-1", "Doing", "/req/1")
    board = json.loads(ledger(tmp_path, "read", "a/b#7", "board").stdout)

    column, wait = board_wait.dispatch_state(board, stood="Todo", called=False)

    assert column == "Todo" and wait.name == "unknown" and wait.missing == "the card write is pending"


def test_a_dropped_write_holds_the_card_where_it_stood_and_names_the_refusal(tmp_path):
    ledger(tmp_path, "board", "a/b#7", "run-1", "Doing", "/req/1")
    ledger(tmp_path, "boardx", "a/b#7", "run-1", "Doing")
    board = json.loads(ledger(tmp_path, "read", "a/b#7", "board").stdout)

    column, wait = board_wait.dispatch_state(board, stood="Todo", called=False)

    assert column == "Todo" and wait.name == "unknown" and wait.missing == "the card write is dropped"


def test_a_card_with_no_board_fact_holds_where_it_stood_and_names_the_missing_fact():
    column, wait = board_wait.dispatch_state(None, stood="Todo", called=False)

    assert column == "Todo" and wait.name == "unknown" and wait.missing == "no board fact"
