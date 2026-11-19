"""The item's kind is carried on the item, through the seam (#243).

An item carries the repository's own normalized kind -- `bug`, `task`, `spec`, `test case` -- and the seam
answers it and writes it. The carrier (today the board's Kind field) is the binding's: a caller names the
kind and never the field or its option ids. A kind the binding cannot carry is a named gap, raised before
any write, never a silent drop. The carrier is a stub gh that keeps one Kind value per card, the way the
board does: a PATCH sets an option id, the items read answers the option's name.
"""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_kind", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

OPTIONS = {"O_BUG": "Bug", "O_TASK": "Task"}


class Board:
    """A stub gh over one card, #243 as ITEM1. `drop` makes a PATCH answer ok and never land."""

    def __init__(self, drop=False, options=OPTIONS):
        self.value = None
        self.drop = drop
        self.options = options
        self.patches = []
        self.item_reads = 0

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        out = ""
        if "items/ITEM1" in s:
            body = json.loads(input)
            self.patches.append(body)
            if not self.drop:
                self.value = body["fields"][0]["value"]
            out = '{"id":"ITEM1"}'
        elif "/fields?" in s:
            out = json.dumps([
                {"id": "F_STATUS", "name": "Status", "options": []},
                {"id": "F_KIND", "name": "Kind", "options": [{"id": i, "name": {"raw": n}} for i, n in self.options.items()]},
            ])
        elif "/items?" in s:
            self.item_reads += 1
            fields = []
            if "F_KIND" in s and self.value:
                fields = [{"id": "F_KIND", "name": "Kind", "value": {"id": self.value, "name": {"raw": self.options[self.value]}}}]
            item = {"id": "ITEM1", "content": {"number": 243, "repository_url": "https://api.github.com/repos/x/y"}, "fields": fields}
            out = subprocess.run(["jq", "-r", cmd[cmd.index("--jq") + 1]], input=json.dumps([item]), capture_output=True, text=True, check=True).stdout
        return subprocess.CompletedProcess(cmd, 0, out, "")


def seam(tmp_path, board):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board, env={"SG_BOARD_READ_BACKOFF": "0"})


def test_the_seam_writes_a_kind_by_its_name_and_reads_it_back(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    assert state.kind(243) == ""
    assert state.set_kind(243, "bug") == "bug"
    assert state.kind(243) == "bug"
    assert board.patches == [{"fields": [{"id": "F_KIND", "value": "O_BUG"}]}]


def test_the_seam_answers_every_kind_in_one_read(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.set_kind(243, "task")
    board.item_reads = 0
    assert state.kinds() == {"243": "task"}
    assert board.item_reads == 1


def test_a_kind_the_binding_cannot_carry_is_a_named_gap_and_nothing_is_written(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    with pytest.raises(issue_state.KindGap) as gap:
        state.set_kind(243, "spec")
    assert "spec" in str(gap.value) and "bug" in str(gap.value)
    assert board.patches == []


def test_the_gaps_of_the_binding_are_reported_by_kind(tmp_path):
    assert seam(tmp_path / "a", Board()).kind_gaps() == ["spec", "test case"]
    assert seam(tmp_path / "b", Board(options={"O_BUG": "Bug", "O_TASK": "Task", "O_SPEC": "Spec", "O_TC": "Test case"})).kind_gaps() == []


def test_a_kind_write_no_read_carries_is_not_a_written_kind(tmp_path):
    state = seam(tmp_path, Board(drop=True))
    with pytest.raises(issue_state.NotCarried):
        state.set_kind(243, "bug")


def test_a_shell_caller_reads_the_kinds_through_the_door(tmp_path, monkeypatch, capsys):
    state = seam(tmp_path, Board())
    state.set_kind(243, "bug")
    monkeypatch.setattr(issue_state, "board_state", lambda **kw: state)
    assert issue_state.main(["issue_state.py", "--kinds"]) == 0
    assert capsys.readouterr().out == "243\tbug\n"


def test_a_shell_caller_reads_the_gaps_through_the_door(tmp_path, monkeypatch, capsys):
    state = seam(tmp_path, Board())
    monkeypatch.setattr(issue_state, "board_state", lambda **kw: state)
    assert issue_state.main(["issue_state.py", "--kind-gaps"]) == 0
    assert capsys.readouterr().out == "spec\ntest case\n"


def test_fresh_hub_board_cache_serves_status_and_kind_without_a_board_read(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "hub-board.json"
    cache.write_text(json.dumps({"t": time.time(), "repo": "x/y", "items": [
        {"n": 854, "st": "Todo", "s": "open", "k": "bug", "r": "x/y"}
    ]}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_STATE", str(tmp_path))   # main reads SG_STATE: the test sets it, never the caller's env
    monkeypatch.setenv("SG_OWNER", "o")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_REPO", "x/y")
    calls = []
    def unexpected(self, *args, **kwargs):
        calls.append(args)
        raise RuntimeError("unexpected board read")
    monkeypatch.setattr(issue_state.BoardBinding, "api", unexpected)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    assert issue_state.main(sys.argv) == 0
    assert capsys.readouterr().out == "854\tTodo\n"
    assert calls == []
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--kinds"])
    assert issue_state.main(sys.argv) == 0
    assert capsys.readouterr().out == "854\tbug\n"
    assert calls == []


@pytest.mark.heavy   # 0.6 s here, past the 2 s line on the CI runner: the live-read fallback and its imports (#2474)
@pytest.mark.parametrize("cache_state", ["missing", "stale"])
def test_missing_or_stale_hub_board_cache_falls_back_to_live_read(tmp_path, monkeypatch, cache_state):
    cache = tmp_path / "hub-board.json"
    if cache_state == "stale":
        cache.write_text(json.dumps({"t": 1, "repo": "x/y", "items": []}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_STATE", str(tmp_path))   # main reads SG_STATE: the test sets it, never the caller's env
    monkeypatch.setenv("SG_OWNER", "o")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_REPO", "x/y")
    calls = []
    def read(self, *args, **kwargs):
        calls.append(args)
        raise RuntimeError("stubbed live read")
    monkeypatch.setattr(issue_state.BoardBinding, "api", read)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    with pytest.raises(RuntimeError):
        issue_state.main(sys.argv)
    assert calls
