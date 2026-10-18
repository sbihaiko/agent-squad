"""The column table reads its columns from a copy of the shipped definition, pinned by hash (ADR-0141)."""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PINNED = "59eeb76bda53f9696dbed1964e4e13e70a83e3b55841a42dce8dce7155890418"
SHIPPED = ["Backlog", "Todo", "Doing", "Code Complete", "Pushing", "Testing", "Closed", "Abandoned"]


def _load(directory):
    spec = importlib.util.spec_from_file_location("board_columns_pin", directory / "board_columns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def columns():
    return _load(SCRIPTS)


def test_the_pinned_copy_names_the_eight_shipped_columns_in_order(columns):
    assert columns.PIN_SHA256 == PINNED
    assert [c["name"] for c in columns.shipped_columns()] == SHIPPED
    assert [c["name"] for c in columns.shipped_columns() if c["terminal"]] == ["Closed", "Abandoned"]


def test_an_edited_pinned_copy_is_refused(tmp_path):
    for name in ("board_columns.py", "board-definition.json"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    path = tmp_path / "board-definition.json"
    path.write_text(path.read_text().replace("Backlog", "Icebox"))
    with pytest.raises(ValueError, match="pinned copy"):
        _load(tmp_path).shipped_columns()


def test_a_published_definition_that_differs_is_reported(columns):
    pinned = (SCRIPTS / "board-definition.json").read_text()
    assert columns.pin_drift(pinned) == []
    report = columns.pin_drift(pinned.replace("Backlog", "Icebox"))
    assert len(report) == 1 and "published definition differs from the pinned copy" in report[0]


def test_the_pin_command_exits_one_on_a_mismatch(tmp_path):
    published = tmp_path / "published.json"
    published.write_text((SCRIPTS / "board-definition.json").read_text())

    def run():
        return subprocess.run([sys.executable, str(SCRIPTS / "board_pin.py"), "--published", str(published)],
                              capture_output=True, text=True)

    assert run().returncode == 0
    published.write_text(published.read_text().replace("Backlog", "Icebox"))
    done = run()
    assert done.returncode == 1 and "differs from the pinned copy" in done.stdout


@pytest.mark.parametrize("facts, column, freeze", [
    ({"spec": True}, "Backlog", None),
    ({"spec": True, "child_live": True}, "Doing", None),
    ({"spec": True, "child_live": False}, "Backlog", None),
    ({}, "Todo", None),
    ({"run_live": True}, "Doing", None),
    ({"review_wait": True}, "Doing", None),
    ({"wave_branch": True}, "Code Complete", None),
    ({"landing": True}, "Pushing", None),
    ({"dev_ci": True}, "Testing", None),
    ({"dev_ci": True, "closed": True}, "Closed", None),
    ({"closed": True}, "Closed", None),
    ({"abandoned": True}, "Abandoned", None),
    ({"owner": True}, "Backlog", "frozen:owner"),
    ({"owner": True, "wave_branch": True}, "Code Complete", "frozen:owner"),
    ({"owner": True, "run_live": True}, "Doing", "frozen:owner"),
    ({"gated": True}, "Backlog", "frozen:dependency"),
    ({"gated": True, "wave_branch": True}, "Code Complete", "frozen:dependency"),
])
def test_each_card_lands_in_the_column_the_table_names(columns, facts, column, freeze):
    assert columns.column_for(facts) == (column, freeze)
    assert column in SHIPPED and column != "Blocked"
