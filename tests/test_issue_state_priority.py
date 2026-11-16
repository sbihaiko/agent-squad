"""The batched board read returns the Priority of each card in one items read (#1977, ADR-0144).

The carrier is a stub gh that answers one Priority single-select per card, the way the board does.
A card with no value is absent from the answer, and a Priority the board does not carry raises.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_priority", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

FIELDS = [
    {"id": "F_STATUS", "name": "Status", "options": [{"id": "O_TODO", "name": {"raw": "Todo"}}]},
    {"id": "F_PRIO", "name": "Priority", "dataType": "SINGLE_SELECT", "options": [{"id": "O_URG", "name": "urgent"}]},
]


class Board:
    """A stub gh over cards that carry a Priority name, or no value (None)."""

    def __init__(self, values, fields=FIELDS):
        self.values = values
        self.fields = fields

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        if "/fields?" in s:
            out = json.dumps(self.fields)
        elif "/items?" in s:
            items = [{"id": f"I{n}", "content": {"number": n, "repository_url": "https://api.github.com/repos/x/y"},
                      "fields": [{"id": "F_PRIO", "name": "Priority", "value": {"name": v} if v else None}]}
                     for n, v in self.values.items()]
            jq = cmd[cmd.index("--jq") + 1]
            out = subprocess.run(["jq", "-r", jq], input=json.dumps(items), capture_output=True, text=True, check=True).stdout
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def binding(tmp_path, board):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board,
                                   env={"SG_BOARD_READ_BACKOFF": "0"}).binding


def test_the_batched_read_returns_the_priority_of_each_card(tmp_path):
    board = Board({1255: "urgent", 1256: None, 1257: "low"})

    assert binding(tmp_path, board).read_priorities() == {"1255": "urgent", "1257": "low"}


def test_the_batched_read_is_one_items_call(tmp_path):
    calls = []
    board = Board({1: "urgent", 2: "high"})

    def recording(cmd, **kw):
        calls.append(" ".join(cmd))
        return board(cmd, **kw)

    binding(tmp_path, recording).read_priorities()

    assert len([c for c in calls if "/items?" in c]) == 1


def test_a_board_without_a_priority_field_raises(tmp_path):
    board = Board({1: "urgent"}, fields=[FIELDS[0]])

    with pytest.raises(RuntimeError, match="Priority"):
        binding(tmp_path, board).read_priorities()
