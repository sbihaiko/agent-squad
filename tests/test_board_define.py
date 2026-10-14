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
