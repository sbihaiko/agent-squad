"""Specs on the phone (SPEC 1596, #1604): each open spec with its tickets' progress by column, the count done and a legend,
computed from the board the phone already reads. The rules are `hub-mobile-specs.js`, run under node alone with no DOM.
Every expected value below is written out from the fixture board, never recomputed by the module under test.
"""
import json
import pathlib
import shutil

import pytest

import tests.test_hub_mobile_move as move
from tests.test_hub_mobile_move import hub, run_js  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "hub-mobile-specs.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

BOARD = {"groups": [
    {"name": "Todo", "items": [
        {"n": 1540, "t": "Phone hub redesign", "sp": True, "r": "agent-squad"},
        {"n": 1541, "t": "Frame 12", "pa": 1540, "r": "agent-squad"},
        {"n": 1542, "t": "Frame 15", "pa": 1540, "r": "agent-squad"},
        {"n": 1500, "t": "Old spec", "sp": True, "r": "agent-squad"},
    ]},
    {"name": "Doing", "items": [{"n": 1544, "t": "Frame 14", "pa": 1540, "r": "agent-squad"}]},
    {"name": "Code Complete", "items": [{"n": 1545, "t": "Frame A", "pa": 1540, "r": "agent-squad"}]},
    {"name": "Done", "items": [
        {"n": 1546, "t": "Chart", "pa": 1540, "r": "agent-squad"},
        {"n": 1547, "t": "Legend", "pa": 1540, "r": "agent-squad"},
        {"n": 1548, "t": "Ring", "pa": 1540, "r": "agent-squad"},
        {"n": 1599, "t": "Closed spec", "sp": True, "r": "agent-squad"},
    ]},
]}


def js(expr):
    import json
    return run_js(SPECS_JS.read_text(), expr.replace("BOARD", json.dumps(BOARD)))


def test_each_open_spec_counts_its_tickets_by_column():
    got = js("specProgress(BOARD)")
    spec = [s for s in got if s["n"] == 1540]
    assert len(spec) == 1, got
    assert spec[0]["title"] == "Phone hub redesign"
    assert spec[0]["repo"] == "agent-squad"
    assert spec[0]["done"] == 3 and spec[0]["total"] == 7
    assert spec[0]["pct"] == 43
    assert spec[0]["count"] == "3/7 · 43%"
    assert [(c["col"], c["n"]) for c in spec[0]["cols"]] == [("Todo", 2), ("Doing", 1), ("Code Complete", 1), ("Done", 3)]
    assert spec[0]["legend"] == "Todo 2 · Doing 1 · Code Complete 1 · Done 3"
    assert [round(b["w"], 1) for b in spec[0]["bar"]] == [28.6, 14.3, 14.3, 42.9]


def test_a_spec_whose_own_card_is_done_leaves_the_list():
    got = js("specProgress(BOARD)")
    assert [s["n"] for s in got] == [1540, 1500], got


@pytest.mark.node_bound
def test_the_specs_tab_draws_one_row_per_open_spec_with_its_count(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const rows = $("specs-list").children;
      return { rows: rows.length, head: rows[0].children[0].textContent, count: rows[0].children[2].textContent,
               state: $("specs-state").textContent };
    """, board=BOARD, routes={"/api/board": [{"body": json.dumps(BOARD)}]})
    assert out == {"rows": 2, "head": "agent-squad · SPEC 1540 Phone hub redesign", "count": "3/7 · 43%", "state": ""}, out
