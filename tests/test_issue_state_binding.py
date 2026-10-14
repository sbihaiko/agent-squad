"""The issue-state seam proves a state write through its binding's read."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)


class FakeBinding:
    name = "fake"

    def __init__(self, carried="Todo"):
        self.carried = carried
        self.writes = []

    def read(self, issue):
        return self.carried

    def write(self, issue, state):
        self.writes.append((issue, state))


def test_state_seam_reads_what_its_binding_carries():
    binding = FakeBinding("Todo")

    assert issue_state.State(binding).read(236) == "Todo"


def test_state_seam_rejects_a_write_the_following_read_does_not_carry():
    binding = FakeBinding("Todo")

    with pytest.raises(issue_state.NotCarried, match="issue 236.*Testing.*Todo"):
        issue_state.State(binding).write(236, "Testing")

    assert binding.writes == [(236, "Testing")]
