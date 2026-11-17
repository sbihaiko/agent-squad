"""The backfill sets the Priority of each open card that has none (#1982, ADR-0144).

The carrier is a stub gh over a board of cards and a list of open issues. Each PATCH it answers moves the stored value,
so a second run reads what the first one wrote.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="the stub board filters its items with jq")

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


issue_state = _load("issue_state_backfill", SCRIPTS / "issue_state.py")
priority_backfill = _load("priority_backfill", SCRIPTS / "priority_backfill.py")

OPTIONS = ["urgent", "high", "normal", "low"]
FIELDS = [{"id": "F_PRIO", "name": "Priority", "dataType": "SINGLE_SELECT",
           "options": [{"id": f"O_{name}", "name": name} for name in OPTIONS]}]


class Board:
    """A stub gh. `cards` maps an issue number to its Priority name, or None for no value.
    `issues` maps an issue number to (state, labels). A refused write is the `refuse_write_at`-th PATCH, counted from 0."""

    def __init__(self, cards, issues, refuse_write_at=None):
        self.cards = dict(cards)
        self.issues = issues
        self.refuse_write_at = refuse_write_at
        self.patches = []

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        if "-X" in cmd and "PATCH" in cmd:
            url = cmd[cmd.index("PATCH") + 1]
            number = int(url.rsplit("/", 1)[1].lstrip("I"))
            body = json.loads(input)
            self.patches.append({"url": url, "fields": body["fields"]})
            if self.refuse_write_at is not None and len(self.patches) - 1 == self.refuse_write_at:
                return subprocess.CompletedProcess(cmd, 1, "", "HTTP 422: refused")
            self.cards[number] = body["fields"][0]["value"][2:]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "/fields?" in s:
            out = json.dumps(FIELDS)
        elif "/issues?" in s:
            rows = [{"number": n, "state": st, "labels": [{"name": label} for label in labels]}
                    for n, (st, labels) in self.issues.items()]
            if "state=open" in s:
                rows = [r for r in rows if r["state"] == "open"]
            jq = cmd[cmd.index("--jq") + 1]
            out = subprocess.run(["jq", "-r", jq], input=json.dumps(rows), capture_output=True, text=True, check=True).stdout
        elif "/items?" in s:
            items = [{"id": f"I{n}", "content": {"number": n, "repository_url": "https://api.github.com/repos/x/y"},
                      "fields": [{"id": "F_PRIO", "name": "Priority", "value": {"name": v} if v else None}]}
                     for n, v in self.cards.items()]
            jq = cmd[cmd.index("--jq") + 1]
            out = subprocess.run(["jq", "-r", jq], input=json.dumps(items), capture_output=True, text=True, check=True).stdout
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def backfill(tmp_path, board):
    binding = issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board,
                                      env={"SG_BOARD_READ_BACKOFF": "0"}).binding
    return priority_backfill.backfill(binding)


def test_an_open_bug_with_no_value_gets_high(tmp_path):
    board = Board({1: None}, {1: ("open", ["bug"])})

    assert backfill(tmp_path, board) == {"1": "high"}
    assert board.cards[1] == "high"


def test_an_open_loop_bug_with_no_value_gets_urgent(tmp_path):
    board = Board({2: None}, {2: ("open", ["loop-bug:landing"])})

    assert backfill(tmp_path, board) == {"2": "urgent"}
    assert board.cards[2] == "urgent"


def test_another_open_card_with_no_value_gets_low(tmp_path):
    board = Board({3: None, 4: None}, {3: ("open", ["spec"]), 4: ("open", [])})

    assert backfill(tmp_path, board) == {"3": "low", "4": "low"}


def test_a_card_with_a_value_keeps_it(tmp_path):
    board = Board({5: "urgent", 6: None}, {5: ("open", ["bug"]), 6: ("open", ["bug"])})

    assert backfill(tmp_path, board) == {"6": "high"}
    assert board.cards[5] == "urgent"


def test_a_second_run_writes_nothing(tmp_path):
    board = Board({1: None, 2: None}, {1: ("open", ["bug"]), 2: ("open", ["loop-bug"])})
    backfill(tmp_path, board)
    patches_after_first = len(board.patches)

    assert backfill(tmp_path, board) == {}
    assert len(board.patches) == patches_after_first


def test_the_backfill_moves_no_column(tmp_path):
    board = Board({1: None}, {1: ("open", ["bug"])})

    backfill(tmp_path, board)

    assert board.patches and all(
        [f["id"] for f in p["fields"]] == ["F_PRIO"] for p in board.patches)


def test_a_run_after_an_interruption_completes_the_remaining_cards(tmp_path):
    cards = {1: None, 2: None, 3: None}
    issues = {n: ("open", ["bug"]) for n in cards}
    board = Board(cards, issues, refuse_write_at=1)

    with pytest.raises(RuntimeError):
        backfill(tmp_path, board)
    assert [n for n, v in board.cards.items() if v] == [1]

    board.refuse_write_at = None
    assert backfill(tmp_path, board) == {"2": "high", "3": "high"}
    assert all(board.cards[n] == "high" for n in cards)


def test_a_closed_issue_is_not_touched(tmp_path):
    board = Board({1: None}, {1: ("closed", ["bug"])})

    assert backfill(tmp_path, board) == {}
    assert board.patches == []


def family(spec, kids):
    """The spec and its children as the writer reads them: {number: {body, sub_issues, labels, state}}."""
    by = {spec: {"body": "", "sub_issues": sorted(kids), "labels": [], "state": "open"}}
    for n, (state, labels) in kids.items():
        by[n] = {"body": f"Parent: #{spec}", "sub_issues": [], "labels": labels, "state": state}
    return by


def propagate(tmp_path, monkeypatch, board, spec, version, value, by):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    state = issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board,
                                    env={"SG_BOARD_READ_BACKOFF": "0"})
    return priority_backfill.propagate(state, "x/y", spec, version, value, by)


def test_a_spec_change_to_high_sets_high_on_each_open_task_child(tmp_path, monkeypatch):
    board = Board({1: "low", 2: None}, {})
    by = family(9, {1: ("open", []), 2: ("open", [])})

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "high", by) == {"1": "recorded", "2": "recorded"}
    assert board.cards == {1: "high", 2: "high"}


def test_a_spec_change_to_low_leaves_a_bug_child_at_high(tmp_path, monkeypatch):
    board = Board({3: None}, {})
    by = family(9, {3: ("open", ["bug"])})

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "low", by) == {"3": "recorded"}
    assert board.cards[3] == "high"


def test_a_spec_change_never_sets_a_loop_bug_child_below_urgent(tmp_path, monkeypatch):
    board = Board({4: "high"}, {})
    by = family(9, {4: ("open", ["bug", "loop-bug"])})

    propagate(tmp_path, monkeypatch, board, 9, 1, "low", by)

    assert board.cards[4] == "urgent"


def test_a_retry_of_the_same_event_writes_nothing(tmp_path, monkeypatch):
    board = Board({1: None}, {})
    by = family(9, {1: ("open", [])})
    propagate(tmp_path, monkeypatch, board, 9, 1, "high", by)
    patches = len(board.patches)

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "high", by) == {"1": "duplicate"}
    assert len(board.patches) == patches


def test_the_writer_refuses_a_stale_event(tmp_path, monkeypatch):
    board = Board({1: None}, {})
    by = family(9, {1: ("open", [])})
    propagate(tmp_path, monkeypatch, board, 9, 2, "high", by)

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "low", by) == {"1": "stale"}
    assert board.cards[1] == "high"
    assert len(board.patches) == 1


def test_the_writer_refuses_a_child_with_an_ambiguous_parent(tmp_path, monkeypatch):
    board = Board({1: None, 5: None}, {})
    by = family(9, {1: ("open", []), 5: ("open", [])})
    by[5]["body"] = "Parent: #9\nParent: #8"

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "high", by) == {"1": "recorded", "5": "ambiguous"}
    assert board.cards[5] is None


def test_a_reopened_child_keeps_its_priority(tmp_path, monkeypatch):
    board = Board({6: "urgent"}, {})

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "low", family(9, {6: ("closed", ["bug"])})) == {}
    assert board.patches == []

    board = Board({6: "urgent"}, {6: ("open", ["bug"])})
    assert backfill(tmp_path, board) == {}
    assert board.cards[6] == "urgent"


def test_a_corrupt_ledger_holds_the_child_and_writes_nothing(tmp_path, monkeypatch):
    board = Board({1: None}, {})
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "x__y__1.json").write_text("{")

    assert propagate(tmp_path, monkeypatch, board, 9, 1, "high", family(9, {1: ("open", [])})) == {"1": "held"}
    assert board.patches == []


def test_no_priority_write_moves_a_column(tmp_path, monkeypatch):
    board = Board({1: None, 2: None}, {})
    by = family(9, {1: ("open", []), 2: ("open", ["bug", "loop-bug"])})

    propagate(tmp_path, monkeypatch, board, 9, 1, "high", by)

    assert board.patches and all(
        [f["id"] for f in p["fields"]] == ["F_PRIO"] for p in board.patches)


@pytest.mark.parametrize("labels, spec, want", [
    ([], "high", "high"),
    (["bug"], "low", "high"),
    (["bug", "loop-bug"], "normal", "urgent"),
    ([], "low", "low"),
    ([], None, "low"),
])
def test_a_new_child_starts_at_its_spec_or_its_own_floor(labels, spec, want):
    assert priority_backfill.birth(labels, spec) == want


def test_the_cli_takes_the_spec_priority_for_a_new_child():
    out = subprocess.run([sys.executable, str(SCRIPTS / "priority_backfill.py"), "--spec", "high", "task"],
                         capture_output=True, text=True, check=True).stdout
    assert out.split() == ["high"]
