"""The column table reads its columns from a copy of the shipped definition, pinned by hash (ADR-0141)."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PINNED = "f9d402b6f93a59dc1ca8165af5ec17161ddbb3670b55c23820dfcf241daf1281"
SHIPPED = ["Backlog", "Todo", "Doing", "Review", "Landing", "Gating", "Closed", "Abandoned"]


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


def test_gating_sits_between_landing_and_closed_with_its_fact_and_actor(columns):
    """A gate or a proof holds the card (spec #1218): the column waits between the wave branch and dev."""
    by = {c["name"]: c for c in columns.shipped_columns()}
    names = list(by)
    assert names.index("Landing") + 1 == names.index("Gating") == names.index("Closed") - 1
    assert by["Gating"]["description"] == "A gate or proof waits for a slot or runs for the wave; a red or unproven attempt is a marker, not a move"
    assert by["Gating"]["fact"] == {"all": ["gating", {"not": {"any": ["abandoned", {"any": ["closed", "in_dev"]}]}}]}   # the fact is an expression (#1901)
    assert by["Gating"]["actor"] == "The gate" and not by["Gating"]["terminal"]


def test_the_definition_names_the_priority_field_with_four_coloured_options(columns):
    definition = json.loads((SCRIPTS / "board-definition.json").read_text())

    assert definition["priorityField"] == "Priority"
    assert [(p["name"], p["color"]) for p in definition["priorities"]] == [
        ("urgent", "RED"), ("high", "ORANGE"), ("normal", "YELLOW"), ("low", "BLUE")]


def test_gating_takes_a_colour_no_other_status_uses(columns):
    statuses = json.loads((SCRIPTS / "board-definition.json").read_text())["statuses"]
    colours = [s["color"] for s in statuses]

    assert {s["name"]: s["color"] for s in statuses}["Gating"] == "PINK"
    assert colours.count("PINK") == 1, "another status wears the Gating colour"


def test_the_rules_block_says_an_attempt_is_a_marker_not_a_move(columns):
    rules = json.loads((SCRIPTS / "board-definition.json").read_text())["rules"]

    assert "An attempt is a marker, not a move: the card leaves Gating only forward." in rules


def test_the_definition_names_the_gate_tries_number_field(columns):
    definition = json.loads((SCRIPTS / "board-definition.json").read_text())

    assert definition["fieldNames"]["gateTries"] == "Gate Tries"
    assert definition["gateTries"] == {"type": "number"}


def test_review_sits_between_doing_and_landing_with_its_fact_and_actor(columns):
    """The child's code is done and its review or a fix round holds it (#603): Doing no longer names the review wait."""
    by = {c["name"]: c for c in columns.shipped_columns()}
    names = list(by)
    assert names.index("Doing") + 1 == names.index("Review") == names.index("Landing") - 1
    assert by["Review"]["description"] == "The child's code is done; its review or a fix round holds it"
    assert by["Review"]["fact"] == {"all": ["review_wait", {"not": {"any": ["abandoned", {"any": ["closed", "in_dev"]}, "gating", "wave_branch"]}}]}
    assert by["Review"]["actor"] == "The review" and not by["Review"]["terminal"]
    assert "review" not in by["Doing"]["description"].lower()


def test_the_hash_the_pinned_copy_and_the_published_copy_are_one_step_apart(columns):
    """The hash and the copy move in one step: neither answers for a definition the other does not carry, and a published
    copy that still lacks Gating is reported against the pinned copy rather than adopted."""
    pinned = (SCRIPTS / "board-definition.json").read_text()

    assert columns.PIN_SHA256 == hashlib.sha256(pinned.encode()).hexdigest()
    assert columns.pin_drift(pinned) == []
    behind = json.loads(pinned)
    behind["statuses"] = [s for s in behind["statuses"] if s["name"] != "Gating"]
    report = columns.pin_drift(json.dumps(behind))
    assert len(report) == 1 and "published definition differs from the pinned copy" in report[0]


def test_a_pinned_copy_one_step_behind_its_hash_is_refused(tmp_path):
    for name in ("board_columns.py", "board-definition.json"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    path = tmp_path / "board-definition.json"
    behind = json.loads(path.read_text())
    behind["statuses"] = [s for s in behind["statuses"] if s["name"] != "Gating"]
    path.write_text(json.dumps(behind))

    with pytest.raises(ValueError, match="pinned copy"):
        _load(tmp_path).shipped_columns()


def test_an_edited_pinned_copy_is_refused(tmp_path):
    for name in ("board_columns.py", "board-definition.json"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    path = tmp_path / "board-definition.json"
    path.write_text(path.read_text().replace("Backlog", "Icebox"))
    with pytest.raises(ValueError, match="pinned copy"):
        _load(tmp_path).shipped_columns()


def test_a_reader_that_started_before_a_publish_re_reads_the_pin(tmp_path):
    """The hub loads its modules once at start, so a publish of board_columns.py and board-definition.json leaves a running
    reader with the old pin and the new copy. The copy on disk matches the pin beside it, so the running reader follows the
    publish and reports no stale pin for a file that matches (#2141)."""
    for name in ("board_columns.py", "board-definition.json"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    running = _load(tmp_path)
    published = (tmp_path / "board-definition.json").read_text().replace("Backlog", "Icebox")   # the publish moves the
    (tmp_path / "board-definition.json").write_text(published)                                 # definition and its pin
    py = tmp_path / "board_columns.py"
    py.write_text(py.read_text().replace(PINNED, hashlib.sha256(published.encode()).hexdigest()))

    assert [c["name"] for c in running.shipped_columns()] == ["Icebox" if n == "Backlog" else n for n in SHIPPED]


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
    ({"spec": True, "child_live": True}, "Todo", None),
    ({"spec": True, "child_live": False}, "Backlog", None),
    ({}, "Todo", None),
    ({"run_live": True}, "Doing", None),
    ({"review_wait": True}, "Review", None),
    ({"review_wait": True, "run_live": True}, "Review", None),
    ({"review_wait": True, "wave_branch": True}, "Landing", None),
    ({"wave_branch": True}, "Landing", None),
    ({"landing": True, "wave_branch": True}, "Landing", None),
    ({"dev_ci": True, "wave_branch": True}, "Landing", None),
    ({"dev_ci": True, "closed": True}, "Closed", None),
    ({"in_dev": True, "dev_ci": True}, "Closed", None),
    ({"in_dev": True, "owner": True}, "Closed", "frozen:owner"),
    ({"closed": True}, "Closed", None),
    ({"abandoned": True}, "Abandoned", None),
    ({"owner": True}, "Backlog", "frozen:owner"),
    ({"owner": True, "wave_branch": True}, "Landing", "frozen:owner"),
    ({"owner": True, "run_live": True}, "Doing", "frozen:owner"),
    ({"gated": True}, "Backlog", "frozen:dependency"),
    ({"gated": True, "wave_branch": True}, "Landing", "frozen:dependency"),
])
def test_each_card_lands_in_the_column_the_table_names(columns, facts, column, freeze):
    assert columns.column_for(facts) == (column, freeze)
    assert column in SHIPPED and column != "Blocked"
