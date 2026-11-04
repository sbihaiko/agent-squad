"""The Gate Tries number field is projected through the seam (#1255, ADR-0144, ADR-0149, ADR-0170).

The journal is the source and the card's `Gate Tries` field is its projection. The field only rises: a
rearm sets a floor, never a reset, and it is written only when the value changes. The carrier is a stub
gh that keeps the number field per card, the way the board does: a PATCH sets it, the items read with
the field asked for answers it.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_gate_tries", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

FIELDS = [
    {"id": "F_STATUS", "name": "Status", "options": [{"id": "O_IP", "name": {"raw": "Gating"}}]},
    {"id": "F_TRIES", "name": "Gate Tries", "dataType": "NUMBER"},
]


class Board:
    """A stub gh over one card, #1255 as ITEM1. `lag` makes a PATCH answer ok and land only on the next read;
    `fail_patch` makes the PATCH refuse, the way a stale field id does."""

    def __init__(self, lag=False):
        self.values = {}
        self.lag = lag
        self.fail_patch = False
        self.pending = None
        self.patches = []

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        out = ""
        if "items/ITEM1" in s:
            if self.fail_patch:
                return subprocess.CompletedProcess(cmd, 1, "", "Validation Failed: the field is not part of the project")
            body = json.loads(input)
            self.patches.append(body)
            if self.lag:
                self.pending = body
            else:
                for f in body["fields"]:
                    self.values[f["id"]] = f["value"]
            out = '{"id":"ITEM1"}'
        elif "/fields?" in s:
            out = json.dumps(FIELDS)
        elif "/items?" in s:
            if self.pending is not None:
                for f in self.pending["fields"]:
                    self.values[f["id"]] = f["value"]
                self.pending = None
            assert "--jq" in cmd
            asked = [i for i in ("F_STATUS", "F_TRIES") if f"fields[]={i}" in s]
            names = {"F_STATUS": "Status", "F_TRIES": "Gate Tries"}
            fields = [{"id": i, "name": names[i], "value": self.values.get(i)} for i in asked]
            item = {"id": "ITEM1", "content": {"number": 1255, "repository_url": "https://api.github.com/repos/x/y"}, "fields": fields}
            jq = cmd[cmd.index("--jq") + 1]
            out = subprocess.run(["jq", "-r", jq], input=json.dumps([item]), capture_output=True, text=True, check=True).stdout
        return subprocess.CompletedProcess(cmd, 0, out, "")


def seam(tmp_path, board):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board,
                                   env={"SG_BOARD_READ_BACKOFF": "0"})


def test_a_card_with_no_attempt_carries_zero(tmp_path):
    assert seam(tmp_path, Board()).binding.gate_tries(1255) == 0


def test_the_count_is_written_and_proven_by_the_read(tmp_path):
    board = Board()
    state = seam(tmp_path, board)

    assert state.set_gate_tries(1255, 2) == 2
    assert board.patches[-1] == {"fields": [{"id": "F_TRIES", "value": 2}]}
    assert board.values["F_TRIES"] == 2


def test_the_field_is_written_only_when_the_value_changes(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.set_gate_tries(1255, 1)
    writes = len(board.patches)

    assert state.set_gate_tries(1255, 1) == 1
    assert len(board.patches) == writes, "an unchanged value must send no board write (ADR-0144)"


def test_a_rearm_never_lowers_the_field(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.set_gate_tries(1255, 5)
    writes = len(board.patches)

    assert state.set_gate_tries(1255, 1) == 5, "a rearm sets a floor, never a reset (ADR-0149)"
    assert len(board.patches) == writes


def test_a_write_the_read_does_not_carry_yet_is_given_the_settle_bound(tmp_path):
    """The board is eventually consistent: a number the next read has not carried is waited for, never
    rewritten, so one projected value sends one PATCH (#403)."""
    board = Board(lag=True)
    state = seam(tmp_path, board)

    assert state.set_gate_tries(1255, 3) == 3
    assert len(board.patches) == 1


def test_the_field_is_found_by_the_definitions_name(tmp_path):
    assert seam(tmp_path, Board()).binding.number_field()["id"] == "F_TRIES"


def test_a_refused_patch_drops_the_cached_field_id(tmp_path):
    """The id is cached a day; a PATCH the field refuses -- a field renamed or recreated within the
    day -- drops the cache, so the next call re-reads the id instead of PATCHing a missing field."""
    board = Board()
    binding = seam(tmp_path, board).binding
    assert binding.number_field()["id"] == "F_TRIES"
    caches = list(tmp_path.glob("rest-number-field-*.json"))
    assert caches, "the field id is cached for the day"

    board.fail_patch = True
    with pytest.raises(RuntimeError):
        binding.write_gate_tries(1255, 2)
    assert not list(tmp_path.glob("rest-number-field-*.json")), \
        "a refused PATCH invalidates the cached id, not only an empty one"
