"""The live board is an instance of the board definition (ADR-0141): the drift between a board snapshot and the definition
is named option by option, and the dry run reads the fields once and sends no write. A fixture snapshot, never the live board."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts"))
import board_define  # noqa: E402

DEFINITION = {
    "fieldNames": {"statuses": "Status", "kinds": "Kind", "freeze": "Freeze", "outcomes": "Outcome"},
    "statuses": [{"name": "Todo", "color": "BLUE"}, {"name": "Doing", "color": "YELLOW"}],
    "kinds": [{"name": "Bug", "color": "RED"}],
    "freeze": [{"name": "Owner", "color": "ORANGE"}],
    "outcomes": [{"name": "Merged", "color": "GREEN"}],
}
FIELDS = [
    {"node_id": "F_S", "name": "Status", "data_type": "single_select", "options": [
        {"id": "a", "name": {"raw": "Todo"}, "color": "GRAY", "description": {"raw": ""}},
        {"id": "b", "name": {"raw": "Blocked"}, "color": "RED", "description": {"raw": ""}}]},
    {"node_id": "F_F", "name": "Freeze", "data_type": "single_select", "options": [
        {"id": "c", "name": {"raw": "Owner"}, "color": "ORANGE", "description": {"raw": ""}}]},
]


def test_drift_names_each_difference_by_name():
    lines = board_define.report(board_define.diff(DEFINITION, board_define.snapshot(FIELDS)))
    assert lines == [
        "drift Status: missing Doing",
        "drift Status: not in the definition Blocked",
        "drift Kind: field missing",
        "drift Outcome: field missing",
        "plan Status: add Doing",
        "plan Kind: create Bug",
        "plan Outcome: create Merged",
    ]


class Binding:
    base = "/users/o/projectsV2/4"

    def __init__(self):
        self.calls = []

    def api(self, *a, inp=None, timeout=None):
        self.calls.append(a)
        return json.dumps(FIELDS)


@pytest.mark.parametrize("flags", [[], ["--check"]])
def test_dry_run_reads_once_and_sends_no_mutation(tmp_path, monkeypatch, capsys, flags):
    path = tmp_path / "board.json"
    path.write_text(json.dumps(DEFINITION))
    binding = Binding()
    monkeypatch.setattr(board_define, "board_state", lambda **kw: type("S", (), {"binding": binding})())
    rc = board_define.main(["--definition", str(path), *flags, "o", "4"])
    assert binding.calls == [("/users/o/projectsV2/4/fields?per_page=100",)]
    assert rc == (1 if flags else 0)
    assert "dry run: nothing written" in capsys.readouterr().out


STATUS = [{"id": 7, "node_id": "F_S", "name": "Status", "data_type": "single_select", "options": [
    {"id": "t", "name": {"raw": "Todo"}, "color": "BLUE", "description": {"raw": ""}},
    {"id": "d", "name": {"raw": "Doing"}, "color": "YELLOW", "description": {"raw": ""}},
    {"id": "c", "name": {"raw": "Closed"}, "color": "PURPLE", "description": {"raw": ""}},
    {"id": "x", "name": {"raw": "Done"}, "color": "GREEN", "description": {"raw": ""}},
    {"id": "p", "name": {"raw": "In Progress"}, "color": "YELLOW", "description": {"raw": ""}}]}]
LIVE = {"statuses": [{"name": n, "color": "GRAY"} for n in ("Todo", "Doing", "Closed")],
        "fieldNames": DEFINITION["fieldNames"], "kinds": [], "freeze": [], "outcomes": []}


class Board:
    """A fake Project shared by two repositories: a PATCH moves a card unless the card is in `deaf`."""
    base = "/users/o/projectsV2/4"

    def __init__(self, cards, deaf=()):
        self.cards, self.deaf, self.writes = {c["iid"]: dict(c) for c in cards}, set(deaf), []
        self.names = {o["id"]: o["name"]["raw"] for o in STATUS[0]["options"]}

    def api(self, *a, inp=None, timeout=None):
        if a[0].endswith("/fields?per_page=100"):
            return json.dumps(STATUS)
        if a[0] == "--paginate":
            return "\n".join(json.dumps(c) for c in self.cards.values())
        self.writes.append((a, json.loads(inp) if inp else None))
        if a[:2] == ("-X", "PATCH") and int(a[2].rsplit("/", 1)[1]) not in self.deaf:
            self.cards[int(a[2].rsplit("/", 1)[1])]["st"] = self.names[json.loads(inp)["fields"][0]["value"]]
        return "{}"


def card(iid, repo, number, st, s="closed"):
    return {"iid": iid, "repo": repo, "number": number, "st": st, "s": s, "l": []}


def run(board, monkeypatch, tmp_path, *flags):
    path = tmp_path / "board.json"
    path.write_text(json.dumps(LIVE))
    monkeypatch.setattr(board_define, "board_state", lambda **kw: type("S", (), {"binding": board})())
    return board_define.main(["--definition", str(path), *flags, "o", "4"])


def test_migrate_dry_run_plans_the_moves_and_writes_nothing(monkeypatch, tmp_path, capsys):
    board = Board([card(1, "a/x", 5, "Done"), card(2, "b/y", 5, "In Progress", s="open"), card(3, "a/x", 6, "Todo", s="open")])
    assert run(board, monkeypatch, tmp_path, "--migrate") == 0
    out = capsys.readouterr().out
    assert "MOVE a/x#5 Done -> Closed" in out and "MOVE b/y#5 In Progress -> Doing" in out and "a/x#6" not in out
    assert board.writes == []


def test_migrate_apply_moves_each_card_and_the_read_proves_it(monkeypatch, tmp_path, capsys):
    board = Board([card(1, "a/x", 5, "Done"), card(2, "b/y", 5, "In Progress", s="open")])
    assert run(board, monkeypatch, tmp_path, "--migrate", "--apply") == 0
    assert {c["st"] for c in board.cards.values()} == {"Closed", "Doing"}
    assert "done: 2 of 2 move(s) carried" in capsys.readouterr().out


def test_migrate_names_a_move_the_read_does_not_carry(monkeypatch, tmp_path, capsys):
    board = Board([card(1, "a/x", 5, "Done"), card(2, "a/x", 6, "Done")], deaf={2})
    assert run(board, monkeypatch, tmp_path, "--migrate", "--apply") == 1
    assert "NOT-CARRIED a/x#6: wanted Closed, carries 'Done'" in capsys.readouterr().err


def test_contract_refuses_while_a_card_carries_an_old_option(monkeypatch, tmp_path, capsys):
    board = Board([card(1, "a/x", 5, "Done"), card(2, "a/x", 6, "Closed")])
    assert run(board, monkeypatch, tmp_path, "--contract", "--apply") == board_define.REFUSED
    assert "Done: 1" in capsys.readouterr().err
    assert board.writes == []


def test_contract_drops_the_old_options_and_keeps_each_named_option_id(monkeypatch, tmp_path):
    board = Board([card(1, "a/x", 5, "Closed"), card(2, "a/x", 6, "Doing", s="open")])
    assert run(board, monkeypatch, tmp_path, "--contract", "--apply") == 0
    (args, body), = board.writes
    kept = body["variables"]["input"]["singleSelectOptions"]
    assert args[0] == "graphql" and body["variables"]["input"]["fieldId"] == "F_S"
    assert [(o["id"], o["name"]) for o in kept] == [("t", "Todo"), ("d", "Doing"), ("c", "Closed")]
