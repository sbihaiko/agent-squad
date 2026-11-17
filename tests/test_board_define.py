"""The live board is an instance of the board definition (ADR-0141): the drift between a board snapshot and the definition
is named option by option, and the dry run reads the fields once and sends no write. A fixture snapshot, never the live board."""

from __future__ import annotations

import json
import shutil
import subprocess
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
        "drift Run: field missing",
        "drift Run started: field missing",
        "plan Status: add Doing",
        "plan Kind: create Bug",
        "plan Outcome: create Merged",
        "plan Run: create text field",
        "plan Run started: create text field",
    ]


NUMBER_DEFINITION = {
    **DEFINITION,
    "fieldNames": {**DEFINITION["fieldNames"], "gateTries": "Gate Tries"},
    "gateTries": {"type": "number"},
}


def test_a_number_field_the_definition_names_is_reported_and_planned():
    """The board carries the attempt count as a number, so the applier creates one with that type, never a text field."""
    lines = board_define.report(board_define.diff(NUMBER_DEFINITION, board_define.snapshot(FIELDS)))
    assert "drift Gate Tries: field missing" in lines
    assert "plan Gate Tries: create number field" in lines


def test_a_number_field_the_board_carries_is_not_reported():
    fields = FIELDS + [{"node_id": "F_GT", "name": "Gate Tries", "data_type": "number"}]
    lines = board_define.report(board_define.diff(NUMBER_DEFINITION, board_define.snapshot(fields)))
    assert not [line for line in lines if "Gate Tries" in line], lines


def test_a_holder_field_the_definition_types_is_planned_once():
    """scalars() and HOLDER_FIELDS name their fields apart. A holder field typed in the definition is created as text by
    the holder row, so the scalars list must not also name it: two plan lines would be a double create in --apply."""
    definition = {**DEFINITION, "fieldNames": {**DEFINITION["fieldNames"], "run": "Run"}, "run": {"type": "text"}}
    rows = board_define.diff(definition, board_define.snapshot(FIELDS))

    assert [name for name, *_ in rows].count("Run") == 1, rows
    assert board_define.report(rows).count("plan Run: create text field") == 1, board_define.report(rows)


class Capture:
    """A binding that records every mutation body, so the created field's type is read from the write itself."""

    base = "/users/o/projectsV2/4"

    def __init__(self, fields):
        self.fields = fields
        self.bodies = []

    def api(self, *a, inp=None, timeout=None):
        if a[0].endswith("/fields?per_page=100"):
            return json.dumps(self.fields)
        if inp:
            self.bodies.append(json.loads(inp))
        return "{}"


def test_apply_creates_the_number_field_with_its_type(tmp_path, monkeypatch):
    binding = Capture(FIELDS)
    path = tmp_path / "board.json"
    path.write_text(json.dumps(NUMBER_DEFINITION))
    monkeypatch.setattr(board_define, "board_state", lambda **kw: type("S", (), {"binding": binding})())
    assert board_define.main(["--definition", str(path), "--apply", "o", "4"]) == 0
    inputs = [body["variables"]["input"] for body in binding.bodies if "createProjectV2Field" in body["query"]]
    assert {i["name"]: i.get("dataType") for i in inputs}["Gate Tries"] == "NUMBER", inputs


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


def project_item(number, reason):
    """One item as the REST items read answers it: a closed issue of a/x carrying the Status Done."""
    return {"id": f"PVT_{number}", "content_type": "Issue",
            "fields": [{"name": "Status", "value": {"name": {"raw": "Done"}}}],
            "content": {"number": number, "repository_url": "https://api.github.com/repos/a/x", "title": f"t{number}",
                        "state": "closed", "state_reason": reason, "labels": [],
                        "sub_issues_summary": {}, "created_at": "2026-10-01T00:00:00Z"}}


ABANDONED_STATUS = [{**STATUS[0], "options": [*STATUS[0]["options"],
    {"id": "ab", "name": {"raw": "Abandoned"}, "color": "GRAY", "description": {"raw": ""}}]}]


class ReadThroughJq(Board):
    """The applier's read as it really runs: cards()'s own jq over the REST items, so the planner sees the row the
    carrier's answer produces, never a row the test shaped."""

    def __init__(self, cards, items):
        super().__init__(cards)
        self.items = items
        self.names = {o["id"]: o["name"]["raw"] for o in ABANDONED_STATUS[0]["options"]}

    def api(self, *a, inp=None, timeout=None):
        if a[0] == "--paginate":
            program = a[a.index("--jq") + 1]
            return subprocess.run([shutil.which("jq"), "-cr", program], input=json.dumps(self.items),
                                  capture_output=True, text=True, check=True).stdout
        if a[0].endswith("/fields?per_page=100"):
            return json.dumps(ABANDONED_STATUS)
        return super().api(*a, inp=inp)


def test_the_applier_read_carries_the_close_reason_so_migration_plans_abandoned(monkeypatch, tmp_path, capsys):
    """#921: the close reason rides the applier's own items read, so `--migrate` moves a card closed as NOT_PLANNED to
    Abandoned, never to Closed."""
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    board = ReadThroughJq([card(1, "a/x", 5, "Done"), card(2, "a/x", 6, "Done")],
                          [project_item(5, "not_planned"), project_item(6, "completed")])
    assert run(board, monkeypatch, tmp_path, "--migrate") == 0
    out = capsys.readouterr().out
    assert "MOVE a/x#5 Done -> Abandoned" in out and "MOVE a/x#6 Done -> Closed" in out, out


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


# #2154: the live board carries the version 1 names. A card holds an option id, so the field's option id is the card's
# column: the rename changes the option's name in place and the card answers the new name, without a move.
RENAME_STATUS = [{"id": 9, "node_id": "F_S", "name": "Status", "data_type": "single_select", "options": [
    {"id": "bl", "name": {"raw": "Backlog"}, "color": "GRAY", "description": {"raw": ""}},
    {"id": "cr", "name": {"raw": "Code Review"}, "color": "ORANGE", "description": {"raw": ""}},
    {"id": "cc", "name": {"raw": "Code Complete"}, "color": "ORANGE", "description": {"raw": ""}},
    {"id": "cl", "name": {"raw": "Closed"}, "color": "GREEN", "description": {"raw": ""}}]}]


class RenameBoard:
    """A board whose cards carry an option id: a field call renames the option, and the card's column is the option's name.

    `lost` drops that many cards from every read after the first, so a count that falls is measured, never assumed."""

    base = "/users/o/projectsV2/4"

    def __init__(self, cards, lost=0):
        self.cards = [dict(iid=i, repo=r, number=n, oid=o) for i, r, n, o in cards]
        self.fields = json.loads(json.dumps(RENAME_STATUS))
        self.names = {o["id"]: o["name"]["raw"] for o in self.fields[0]["options"]}
        self.writes, self.lost, self.reads = [], lost, 0

    def api(self, *a, inp=None, timeout=None):
        if a[0].endswith("/fields?per_page=100"):
            return json.dumps(self.fields)
        if a[0] == "--paginate":
            self.reads += 1
            shown = self.cards[:-self.lost] if self.lost and self.reads > 1 else self.cards
            return "\n".join(json.dumps({"iid": c["iid"], "repo": c["repo"], "number": c["number"],
                                         "st": self.names[c["oid"]], "s": "open", "l": []}) for c in shown)
        body = json.loads(inp)
        self.writes.append(body)
        if "updateProjectV2Field" in body["query"]:
            for o in body["variables"]["input"]["singleSelectOptions"]:
                self.names[o["id"]] = o["name"]
        return "{}"


def test_rename_carries_every_option_id_and_every_card_keeps_its_column(monkeypatch, tmp_path, capsys):
    board = RenameBoard([("1", "a/x", 5, "cr"), ("2", "a/x", 6, "cc"), ("3", "b/y", 7, "cl")])
    assert run(board, monkeypatch, tmp_path, "--rename", "--apply") == 0

    (body,) = board.writes
    sent = body["variables"]["input"]["singleSelectOptions"]
    assert body["variables"]["input"]["fieldId"] == "F_S" and "updateProjectV2Field" in body["query"]
    assert [(o["id"], o["name"]) for o in sent] == [
        ("bl", "Backlog"), ("cr", "Review"), ("cc", "Landing"), ("cl", "Closed")]
    names = [board.names[c["oid"]] for c in board.cards]
    assert names == ["Review", "Landing", "Closed"]
    assert "Code Review" not in names and "Code Complete" not in names
    out = capsys.readouterr().out
    assert "done: 3 card(s), 3 before the call; every card kept its column" in out, out


def test_a_second_rename_call_sends_the_names_the_board_already_carries(monkeypatch, tmp_path):
    board = RenameBoard([("1", "a/x", 5, "cr"), ("2", "a/x", 6, "cc")])
    assert run(board, monkeypatch, tmp_path, "--rename", "--apply") == 0
    first = json.dumps(board.writes[0], sort_keys=True)
    assert run(board, monkeypatch, tmp_path, "--rename", "--apply") == 0
    assert json.dumps(board.writes[1], sort_keys=True) == first
    assert board.names == {"bl": "Backlog", "cr": "Review", "cc": "Landing", "cl": "Closed"}


def test_rename_fails_the_proof_when_the_card_count_falls(monkeypatch, tmp_path, capsys):
    board = RenameBoard([("1", "a/x", 5, "cr"), ("2", "a/x", 6, "cc")], lost=1)
    assert run(board, monkeypatch, tmp_path, "--rename", "--apply") == 1
    err = capsys.readouterr().err
    assert "count" in err.lower(), err
