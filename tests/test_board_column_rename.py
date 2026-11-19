"""#2315: three board columns take new names, Review to Reviewing, Landing to Batching, Gating to Testing.

The rename is expand and contract. The published code reads and writes a board that still carries the old options, and
the same code reads and writes it after the live migration renames them in place. An old name reads as the new column
everywhere a column is read: a live read, the card history and the column limits."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


table = _load("board_columns")
migrate = _load("board_migrate")
history = _load("board_history")
issue_state = _load("issue_state")

RENAMED = {"Review": "Reviewing", "Landing": "Batching", "Gating": "Testing"}
ORDER = ["Backlog", "Todo", "Doing", "Reviewing", "Batching", "Testing", "Closed", "Abandoned"]


def test_the_definition_names_the_new_columns_in_the_same_order():
    data = json.loads((SCRIPTS / "board-definition.json").read_text())
    assert [s["name"] for s in data["statuses"]] == ORDER
    assert data["definition_version"] == 3
    assert [s["name"] for s in table.shipped_columns()] == ORDER


@pytest.mark.parametrize("old,new", sorted(RENAMED.items()))
def test_an_old_name_reads_as_the_new_column(old, new):
    assert table.canonical(old) == new
    assert table.view_column(old, False) == (new, None), "a live read of the old option draws the new column"


def test_a_version_1_name_reads_as_the_new_column():
    assert table.recorded_column("Code Review") == "Reviewing"
    assert table.recorded_column("Code Complete") == "Batching"


@pytest.mark.parametrize("old,new", sorted(RENAMED.items()))
def test_a_writer_of_the_new_name_sends_the_old_option_id(old, new):
    assert table.option_id(new, {old: "id-1"}) == "id-1", "the board before the migration"
    assert table.option_id(new, {new: "id-1"}) == "id-1", "the board after the migration"
    assert table.option_name(new, {old: "id-1"}) == old


def test_a_card_only_moves_forward_across_both_spellings():
    assert table.past("Gating", "Batching")
    assert table.past("Testing", "Landing")
    assert not table.past("Batching", "Testing")


def test_the_migration_renames_the_three_options_in_place():
    for old, new in RENAMED.items():
        assert migrate.RENAMES[old] == new
    assert migrate.RENAMES["Code Review"] == "Reviewing" and migrate.RENAMES["Code Complete"] == "Batching"
    assert "Testing" not in migrate.OLD_OPTIONS, "Testing is a shipped column now, never a retired option"
    opts = [{"id": "a", "name": "Doing"}, {"id": "b", "name": "Review"}, {"id": "c", "name": "Landing"},
            {"id": "d", "name": "Gating"}, {"id": "e", "name": "Closed"}]
    out, changed = migrate.renamed(opts)
    assert changed
    assert [(o["id"], o["name"]) for o in out] == [("a", "Doing"), ("b", "Reviewing"), ("c", "Batching"),
                                                  ("d", "Testing"), ("e", "Closed")]
    assert migrate.renamed(out) == (out, False), "a second rename changes nothing"


def test_the_history_reads_an_old_event_as_the_new_column():
    events = [{"t": "2026-11-01T00:00:00Z", "type": "added"},
              {"t": "2026-11-01T00:00:01Z", "type": "status", "status": "Review"},
              {"t": "2026-11-01T01:00:00Z", "type": "status", "status": "Landing"},
              {"t": "2026-11-01T02:00:00Z", "type": "status", "status": "Gating"},
              {"t": "2026-11-01T03:00:00Z", "type": "status", "status": "Testing"}]
    columns = [r["column"] for r in history.intervals(events) if r["column"]]   # the add alone draws no column
    assert columns == ["Reviewing", "Batching", "Testing"], columns


class _Binding:
    name = "fake"

    def __init__(self, carried):
        self.carried = carried

    def read(self, issue):
        return self.carried

    def write(self, issue, state):
        pass


def test_the_seam_proves_a_write_across_the_two_spellings():
    """A field cache a day old names the old option, while the live read answers the new name: one column."""
    assert issue_state.State(_Binding("Batching")).write(5, "Landing") == "Batching"


def test_a_column_limit_reads_under_either_name():
    from squad import board_limits
    assert board_limits.limit("Review") == board_limits.limit("Reviewing") == 4
    assert set(board_limits.DEFAULTS) == {"Backlog", "Todo", "Doing", "Reviewing", "Batching", "Testing"}   # Backlog joined the store (#2432)
