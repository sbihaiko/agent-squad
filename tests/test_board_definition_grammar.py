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
    ({"review_wait": True}, "Review", None),
    ({"wave_branch": True}, "Landing", None),
    ({"gating": True}, "Gating", None),
    ({"closed": True}, "Closed", None),
    ({"in_dev": True}, "Closed", None),
    ({"abandoned": True}, "Abandoned", None),
    ({"spec": True}, "Backlog", None),
    ({"owner": True}, "Backlog", "frozen:owner"),
    ({"gated": True}, "Backlog", "frozen:dependency"),
    ({"spec": True, "child_live": True}, "Todo", None),
    ({"abandoned": True, "gating": True}, "Abandoned", None),
    ({"in_dev": True, "gating": True}, "Closed", None),
    ({"review_wait": True, "wave_branch": True}, "Landing", None),
    ({"run_live": True, "review_wait": True}, "Review", None),
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

    assert data["definition_version"] == table.CURRENT_DEFINITION_VERSION == 2
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
    text = DEFINITION.read_text().replace('"definition_version": 2', '"definition_version": 3', 1)
    column, freeze, unknown = table.resolve_column({"run_live": True}, written="Code Review", text=text)

    assert (column, freeze) == ("Code Review", None), "the reader guesses a column it does not know"
    assert unknown and "3" in unknown
    assert table.column_for({"run_live": True}, written="Code Review", text=text) == ("Code Review", None)
    assert table.view_column("Doing", False, text=text) == ("Doing", None)


def test_an_expression_outside_the_grammar_is_unknown_and_the_written_column_stands(table):
    data = _data()
    for status in data["statuses"]:
        if status["name"] == "Gating":
            status["fact"] = {"sometimes": "gating"}
    text = json.dumps(data)
    column, _, unknown = table.resolve_column({"gating": True}, written="Code Complete", text=text)

    assert column == "Code Complete" and unknown and "Gating" in unknown
    assert table.column_for({"gating": True}, written="Code Complete", text=text) == ("Code Complete", None)
    assert table.view_column("Code Review", False, text=text) == ("Review", None)


def test_a_definition_with_no_version_reads_as_the_current_version(table):
    data = _data()
    del data["definition_version"]
    text = json.dumps(data)
    for facts, column, freeze in TODAYS:
        assert table.resolve_column(facts, text=text)[:2] == (column, freeze), facts
        assert table.column_for(facts, text=text) == (column, freeze), facts


def test_the_grammar_holds_a_fact_name_all_any_and_not_and_nothing_else(table):
    table.parse_expression("run_live")
    table.parse_expression({"all": ["run_live", {"not": "closed"}]})
    table.parse_expression({"any": [{"not": {"all": ["spec", "owner"]}}, "gated"]})
    for bad in (None, 1, [], {}, {"sometimes": "run_live"}, {"all": []}, {"all": "run_live"},
                {"not": ["run_live"]}, {"all": ["run_live"], "not": "closed"}, "missing_status"):
        with pytest.raises(table.UnknownDefinition):
            table.parse_expression(bad)
