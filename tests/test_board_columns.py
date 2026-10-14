"""The board column vocabulary has one host-free source of truth."""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
COLUMNS_PY = SCRIPTS / "board_columns.py"
HUB_PY = SCRIPTS / "hub.py"
RECONCILE_PY = SCRIPTS / "reconcile.py"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_column_declares_its_fact_actor_and_terminal_state():
    columns = _module("board_columns", COLUMNS_PY).COLUMNS
    names = {column["name"] for column in columns}

    assert names == {"No status", "Todo", "Open Specs", "In Progress", "Code Complete", "Testing", "Blocked", "Done", "Open PRs", "Closed PRs"}
    assert all(isinstance(column["fact"], str) and column["fact"] for column in columns)
    assert all(isinstance(column["actor"], str) and column["actor"] for column in columns)
    assert all(isinstance(column["terminal"], bool) for column in columns)
    assert {column["name"] for column in columns if column["terminal"]} == {"Done", "Closed PRs"}


def test_every_column_has_a_distinct_fact():
    columns = _module("board_columns_facts", COLUMNS_PY).COLUMNS
    facts = [column["fact"] for column in columns]

    assert len(facts) == len(set(facts))


def test_hub_reads_its_vocabulary_order_flow_and_colours_from_the_table(monkeypatch):
    monkeypatch.setenv("SG_REPO", "sbihaiko/agent-squad")
    monkeypatch.setenv("SG_OWNER", "sbihaiko")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_HUB_DIR", tempfile.mkdtemp())
    columns = _module("board_columns_hub", COLUMNS_PY).COLUMNS
    hub = _module("hub_columns", HUB_PY)

    page = hub.PAGE
    assert hub.ORDER == [column["name"] for column in sorted(columns, key=lambda column: column["order"])]
    flow = [column["name"] for column in sorted(columns, key=lambda column: column["flow"])]
    assert hub.BOARD_COLUMNS == columns
    assert json.dumps({column["name"]: column["color"] for column in columns}, separators=(",", ":")) in page
    assert json.dumps(flow, separators=(",", ":")) in page
    assert flow == ["Open PRs", "Closed PRs", "Open Specs", "No status", "Blocked", "Todo", "In Progress", "Code Complete", "Testing", "Done"]


def test_reconciler_imports_column_names_from_the_table():
    source = RECONCILE_PY.read_text()
    for name in ("Todo", "In Progress", "Code Complete", "Testing", "Blocked", "Done"):
        assert f'"{name}"' not in source
