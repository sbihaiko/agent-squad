"""The run that holds an issue is named on the issue, through the seam (#241).

While a run holds an issue the seam answers the run's id and start time for it; when the run ends the
holder is cleared through the seam, and the clear is proven by the next read. The carrier is a stub gh
that keeps the two Run fields per card, the way the board does: a PATCH sets them, the items read with
the fields asked for answers them.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_holder", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

FIELDS = [
    {"id": "F_STATUS", "name": "Status", "options": [{"id": "O_IP", "name": {"raw": "In Progress"}}]},
    {"id": "F_RUN", "name": "Run"},
    {"id": "F_START", "name": "Run started"},
]


class Board:
    """A stub gh over one card, #241 as ITEM1. `drop` makes a PATCH answer ok and never land."""

    def __init__(self, drop=False):
        self.values = {}
        self.drop = drop
        self.patches = []

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        out = ""
        if "items/ITEM1" in s:
            body = json.loads(input)
            self.patches.append(body)
            if not self.drop:
                for f in body["fields"]:
                    self.values[f["id"]] = f["value"]
            out = '{"id":"ITEM1"}'
        elif "/fields?" in s:
            out = json.dumps(FIELDS)
        elif "/items?" in s:
            assert "--jq" in cmd
            fields = [{"id": i, "name": n, "value": self.values.get(i)} for i, n in (("F_RUN", "Run"), ("F_START", "Run started")) if "fields[]=" in s]
            item = {"id": "ITEM1", "content": {"number": 241, "repository_url": "https://api.github.com/repos/x/y"}, "fields": fields}
            jq = cmd[cmd.index("--jq") + 1]
            out = subprocess.run(["jq", "-r", jq], input=json.dumps([item]), capture_output=True, text=True, check=True).stdout
        return subprocess.CompletedProcess(cmd, 0, out, "")


def seam(tmp_path, board):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board, env={"SG_BOARD_READ_BACKOFF": "0"})


def test_a_held_issue_names_its_run_and_start_time(tmp_path):
    state = seam(tmp_path, Board())
    assert state.holder(241) is None, "an issue no run holds names no holder"

    held = state.hold(241, "run-abc", "2026-10-13T08:00:00Z")

    assert held == {"run": "run-abc", "started": "2026-10-13T08:00:00Z"}
    assert state.holder(241) == {"run": "run-abc", "started": "2026-10-13T08:00:00Z"}
    assert state.holders() == {"241": {"run": "run-abc", "started": "2026-10-13T08:00:00Z"}}


def test_a_released_issue_is_proven_clear_by_the_next_read(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.hold(241, "run-abc", "2026-10-13T08:00:00Z")

    assert state.release(241) is None
    assert board.patches[-1]["fields"] == [{"id": "F_RUN", "value": None}, {"id": "F_START", "value": None}], "a clear is null, never an empty string (#101)"
    assert state.holder(241) is None


def test_a_release_the_read_does_not_carry_is_named(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.hold(241, "run-abc", "2026-10-13T08:00:00Z")
    board.drop = True

    with pytest.raises(issue_state.NotCarried, match="issue 241.*run-abc"):
        state.release(241)
