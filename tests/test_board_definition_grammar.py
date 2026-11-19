"""The shipped definition states its version and each column's fact as one closed expression (#1901).

A reader evaluates the expression. A version or an expression the reader does not know keeps the card's last written
column and names the unknown. A definition with no version reads as the current version, with today's behaviour.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
DEFINITION = SCRIPTS / "board-definition.json"

# Today's column rule, stated as facts: the expression in the definition must answer the same column.
TODAYS = [
    ({}, "Todo", None),
    ({"run_live": True}, "Doing", None),
    ({"review_wait": True}, "Reviewing", None),
    ({"wave_branch": True}, "Batching", None),
    ({"gating": True}, "Testing", None),
    ({"closed": True}, "Closed", None),
    ({"in_dev": True}, "Closed", None),
    ({"abandoned": True}, "Abandoned", None),
    ({"spec": True}, "Backlog", None),
    ({"owner": True}, "Backlog", "frozen:owner"),
    ({"gated": True}, "Backlog", "frozen:dependency"),
    ({"spec": True, "child_live": True}, "Todo", None),
    ({"abandoned": True, "gating": True}, "Abandoned", None),
    ({"in_dev": True, "gating": True}, "Closed", None),
    ({"review_wait": True, "wave_branch": True}, "Batching", None),
    ({"run_live": True, "review_wait": True}, "Reviewing", None),
    ({"owner": True, "run_live": True}, "Doing", "frozen:owner"),
]


def _load():
    spec = importlib.util.spec_from_file_location("board_columns_grammar", SCRIPTS / "board_columns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def table():
    return _load()


def _data():
    return json.loads(DEFINITION.read_text())


def test_the_shipped_definition_names_its_version_and_each_column_an_expression(table):
    data = _data()

    assert data["definition_version"] == table.CURRENT_DEFINITION_VERSION == 3
    for status in data["statuses"]:
        table.parse_expression(status["fact"])
        assert isinstance(status["description"], str) and status["description"], status["name"]


def test_each_column_resolves_todays_rule_through_its_expression(table):
    for facts, column, freeze in TODAYS:
        assert table.resolve_column(facts)[:2] == (column, freeze), facts
        assert table.column_for(facts) == (column, freeze), facts


def test_the_hub_read_and_the_writer_reach_the_same_column(table):
    for status in (c["name"] for c in table.shipped_columns()):
        assert table.view_column(status, False) == (status, None), status
    assert table.view_column("Closed", True) == ("Closed", None)
    assert table.view_column("Closed", True, reason="not_planned") == ("Abandoned", None)
    assert table.view_column("Doing", False, ["frozen:owner"]) == ("Doing", "frozen:owner")


def test_a_version_the_reader_does_not_know_keeps_the_last_written_column(table):
    text = DEFINITION.read_text().replace('"definition_version": 3', '"definition_version": 4', 1)
    column, freeze, unknown = table.resolve_column({"run_live": True}, written="Reviewing", text=text)

    assert (column, freeze) == ("Reviewing", None), "the reader guesses a column it does not know"
    assert unknown and "4" in unknown
    assert table.column_for({"run_live": True}, written="Reviewing", text=text) == ("Reviewing", None)
    assert table.view_column("Doing", False, text=text) == ("Doing", None)


def test_an_expression_outside_the_grammar_is_unknown_and_the_written_column_stands(table):
    data = _data()
    for status in data["statuses"]:
        if status["name"] == "Testing":
            status["fact"] = {"sometimes": "gating"}
    text = json.dumps(data)
    column, _, unknown = table.resolve_column({"gating": True}, written="Batching", text=text)

    assert column == "Batching" and unknown and "Testing" in unknown
    assert table.column_for({"gating": True}, written="Batching", text=text) == ("Batching", None)
    assert table.view_column("Reviewing", False, text=text) == ("Reviewing", None)


def test_a_definition_with_no_version_reads_as_the_current_version(table):
    data = _data()
    del data["definition_version"]
    text = json.dumps(data)
    for facts, column, freeze in TODAYS:
        assert table.resolve_column(facts, text=text)[:2] == (column, freeze), facts
        assert table.column_for(facts, text=text) == (column, freeze), facts


def test_a_version_1_definition_reads_its_old_names_as_reviewing_and_batching(table):
    """#2190: version 1 named the columns `Code Review` and `Code Complete`; only the version 1 path renames them."""
    data = _data()
    data["definition_version"] = 1
    for status in data["statuses"]:
        status["name"] = {"Reviewing": "Code Review", "Batching": "Code Complete"}.get(status["name"], status["name"])
    names = [c["name"] for c in table.shipped_columns(json.dumps(data))]

    assert "Reviewing" in names and "Batching" in names, names
    assert "Code Review" not in names and "Code Complete" not in names, names
    for facts, column, freeze in TODAYS:
        assert table.resolve_column(facts, text=json.dumps(data))[:2] == (column, freeze), facts


def test_the_alias_map_holds_no_old_name(table):
    """#2190: the boards carry no old option, so an old spelling is no longer a column the map resolves."""
    assert table.canonical("Code Complete") == "Code Complete"
    assert table.canonical("Code Review") == "Code Review"


def test_the_grammar_holds_a_fact_name_all_any_and_not_and_nothing_else(table):
    table.parse_expression("run_live")
    table.parse_expression({"all": ["run_live", {"not": "closed"}]})
    table.parse_expression({"any": [{"not": {"all": ["spec", "owner"]}}, "gated"]})
    for bad in (None, 1, [], {}, {"sometimes": "run_live"}, {"all": []}, {"all": "run_live"},
                {"not": ["run_live"]}, {"all": ["run_live"], "not": "closed"}, "missing_status"):
        with pytest.raises(table.UnknownDefinition):
            table.parse_expression(bad)
