"""The board column vocabulary has one host-free source of truth."""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
COLUMNS_PY = SCRIPTS / "board_columns.py"
HUB_PY = SCRIPTS / "hub.py"
RECONCILE_PY = SCRIPTS / "reconcile.py"
NAMES = ("No status", "Todo", "Open Specs", "In Progress", "Code Complete", "Testing", "Blocked", "Done",
         "Open PRs", "Closed PRs")


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def columns():
    return _module("board_columns_table", COLUMNS_PY).COLUMNS


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/agent-squad")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_columns", HUB_PY)


def test_each_column_declares_its_fact_actor_and_terminal_state(columns):
    assert {column["name"] for column in columns} == set(NAMES)
    assert all(isinstance(column["fact"], str) and column["fact"] for column in columns)
    assert all(isinstance(column["actor"], str) and column["actor"] for column in columns)
    assert all(isinstance(column["terminal"], bool) for column in columns)
    assert {column["name"] for column in columns if column["terminal"]} == {"Done", "Closed PRs"}


def test_every_column_has_a_distinct_fact(columns):
    facts = [column["fact"] for column in columns]

    assert len(facts) == len(set(facts))


def test_every_column_is_reachable_on_the_board(columns, hub):
    """A column the board never draws, or a fact the script never reads, is a column the table must not carry."""
    names = [column["name"] for column in columns]
    assert set(hub.ORDER) == set(names), "the board's own order leaves a column of the table out"
    flow = json.loads(re.search(r"FLOW=(\[[^;]*?\]),", hub.PAGE).group(1))
    assert set(flow) == set(names), "the flow leaves a column out, so no reader can reach it"
    by_fact = json.loads(re.search(r"const COLNAME=(\{.*?\});", hub.PAGE).group(1))
    assert set(by_fact) == {column["fact"] for column in columns}, \
        "the script reads a column by its fact: a fact it does not know is a column it cannot name"


def test_the_hub_vocabulary_is_the_table(columns, hub):
    assert hub.BOARD_COLUMNS == columns, "the hub holds a vocabulary of its own"
    assert hub.ORDER == [column["name"] for column in sorted(columns, key=lambda column: column["order"])], \
        "the board's order is not the table's"
    colours = re.findall(r'"?([A-Za-z ]+)"?:"(#[0-9a-f]{6})"', re.search(r"const COLORS=\{(.*?)\};", hub.PAGE).group(1))
    assert colours == [(column["name"], column["color"]) for column in columns], \
        "the page's colour map is not the table's, so a column wears a colour of its own"
    assert json.dumps({column["fact"]: column["name"] for column in columns}, separators=(",", ":")) in hub.PAGE, \
        "the page does not read the table's names by fact"


def test_the_hub_names_no_column_literal_of_its_own(columns):
    source = HUB_PY.read_text()

    for column in columns:
        assert f'"{column["name"]}"' not in source, \
            f"hub.py names the column {column['name']!r} itself; the name comes from the table"


def test_the_reconciler_names_no_column_literal_of_its_own(columns):
    source = RECONCILE_PY.read_text()

    for column in columns:
        assert f'"{column["name"]}"' not in source, \
            f"reconcile.py names the column {column['name']!r} itself; the name comes from the table"
