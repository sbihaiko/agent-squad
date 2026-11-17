"""A loop pass writes each open card's Wait in bounded batches (#2045, #1902, ADR-0144, ADR-0170)."""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


board_wait = _load("board_wait_pass_under_test", SCRIPTS / "board_wait.py")


def card(**facts):
    base = {"record": True, "labels": [], "run_live": False, "queued": None}
    base.update(facts)
    return base


class Binding:
    """A stub carrier. A write moves the issue's carried value, as the board does for the next read."""

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
        issue = next(i for i, item in self.item_map.items() if item == item_id)
        self.carried[issue] = name


def test_one_pass_writes_the_wait_of_every_open_card():
    binding = Binding(carried={}, items={"1": "I1", "2": "I2", "3": "I3"})
    cards = {
        "1": card(queued="run start"),
        "2": card(labels=["frozen:owner"], queued="gate slot"),
        "3": card(record=False),
    }

    board_wait.wait_pass(binding, cards)

    assert sorted(binding.calls) == [("I1", "run start"), ("I2", "owner"), ("I3", "unknown")]


def test_a_pass_sends_only_the_cards_whose_wait_changed():
    binding = Binding(carried={"1": "run start"}, items={"1": "I1", "2": "I2"})
    cards = {"1": card(queued="run start"), "2": card(queued="gate slot")}

    board_wait.wait_pass(binding, cards)

    assert binding.calls == [("I2", "gate slot")]


def test_a_pass_sends_its_writes_in_batches_and_each_batch_holds_at_most_the_bound():
    items = {str(n): f"I{n}" for n in range(1, 8)}
    binding = Binding(carried={}, items=items)
    cards = {str(n): card(queued="run start") for n in range(1, 8)}

    batches = board_wait.wait_pass(binding, cards, batch=3)

    assert [len(b) for b in batches] == [3, 3, 1]
    assert len(binding.calls) == 7


def test_a_second_pass_over_the_same_facts_sends_no_write():
    items = {"1": "I1", "2": "I2", "3": "I3"}
    binding = Binding(carried={}, items=items)
    cards = {
        "1": card(queued="run start"),
        "2": card(labels=["frozen:owner"], queued="gate slot"),
        "3": card(record=False),
    }
    board_wait.wait_pass(binding, cards)
    writes_after_first = len(binding.calls)

    second = board_wait.wait_pass(binding, cards)

    assert len(binding.calls) == writes_after_first
    assert second == []
