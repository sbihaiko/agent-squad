"""Specs on the phone (SPEC 1596, #1604): each open spec with its tickets' progress by column, the count done and a legend,
computed from the board the phone already reads. The rules are `hub-mobile-specs.js`, run under node alone with no DOM.
Every expected value below is written out from the fixture board, never recomputed by the module under test.

The board is the shape the hub serves: each group carries the definition's own name and its `end` flag, and the payload
names the delivered column as `done`. The module names no board column of its own (ADR-0137).
"""
import json
import pathlib
import shutil

import pytest

import tests.hub_mobile_stub as move
from tests.hub_mobile_stub import hub, run_js  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "hub-mobile-specs.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

BOARD = {"done": "Closed", "groups": [
    {"name": "Backlog", "end": False, "color": "#b1", "items": []},
    {"name": "Todo", "end": False, "color": "#b2", "items": [
        {"n": 1540, "t": "Phone hub redesign", "sp": True, "r": "agent-squad"},
        {"n": 1541, "t": "Frame 12", "pa": 1540, "r": "agent-squad"},
        {"n": 1542, "t": "Frame 15", "pa": 1540, "r": "agent-squad"},
        {"n": 1500, "t": "Old spec", "sp": True, "r": "agent-squad"},
    ]},
    {"name": "Doing", "end": False, "color": "#b3", "items": [{"n": 1544, "t": "Frame 14", "pa": 1540, "r": "agent-squad"}]},
    {"name": "Code Complete", "end": False, "color": "#b4", "items": [{"n": 1545, "t": "Frame A", "pa": 1540, "r": "agent-squad"}]},
    {"name": "Closed", "end": True, "color": "#b5", "items": [
        {"n": 1546, "t": "Chart", "pa": 1540, "r": "agent-squad"},
        {"n": 1547, "t": "Legend", "pa": 1540, "r": "agent-squad"},
        {"n": 1548, "t": "Ring", "pa": 1540, "r": "agent-squad"},
        {"n": 1599, "t": "Closed spec", "sp": True, "r": "agent-squad"},
    ]},
    {"name": "Abandoned", "end": True, "color": "#b6", "items": [{"n": 1550, "t": "Dropped", "pa": 1540, "r": "agent-squad"}]},
]}


def js(expr):
    return run_js(SPECS_JS.read_text(), expr.replace("BOARD", json.dumps(BOARD)))


def test_each_open_spec_counts_its_tickets_by_column():
    got = js("specProgress(BOARD)")
    spec = [s for s in got if s["n"] == 1540]
    assert len(spec) == 1, got
    assert spec[0]["title"] == "Phone hub redesign"
    assert spec[0]["repo"] == "agent-squad"
    assert spec[0]["done"] == 3 and spec[0]["total"] == 8
    assert spec[0]["pct"] == 38
    assert spec[0]["count"] == "3/8 · 38%"
    assert [(c["col"], c["n"]) for c in spec[0]["cols"]] == [
        ("Todo", 2), ("Doing", 1), ("Code Complete", 1), ("Closed", 3), ("Abandoned", 1)]
    assert spec[0]["legend"] == "Todo 2 · Doing 1 · Code Complete 1 · Closed 3 · Abandoned 1"
    assert [round(b["w"], 1) for b in spec[0]["bar"]] == [25.0, 12.5, 12.5, 37.5, 12.5]


def test_a_spec_in_a_terminal_group_leaves_the_list():
    got = js("specProgress(BOARD)")
    assert [s["n"] for s in got] == [1540, 1500], got


def test_the_done_count_is_the_column_the_board_names():
    # The same fixture with the delivered column named by the board, not by the module: a board that names another column
    # moves both the open filter and the done count, so the module holds no column rule of its own (ADR-0137).
    board = json.loads(json.dumps(BOARD))
    board["done"] = "Abandoned"
    got = run_js(SPECS_JS.read_text(), "specProgress(BOARD)".replace("BOARD", json.dumps(board)))
    spec = next(s for s in got if s["n"] == 1540)
    assert spec["done"] == 1 and spec["total"] == 8
    assert spec["count"] == "1/8 · 13%"


def test_the_module_names_no_board_column(hub):  # noqa: F811
    text = SPECS_JS.read_text()
    names = [c["name"] for c in hub._columns_module.shipped_columns()]
    quoted = [n for n in names if f'"{n}"' in text or f"'{n}'" in text]
    assert quoted == [], f"the specs module names board columns of its own: {quoted}"


@pytest.mark.node_bound
def test_the_specs_tab_draws_one_row_per_open_spec_with_its_count(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const rows = $("specs-list").children;
      return { rows: rows.length, head: rows[0].children[0].textContent, count: rows[0].children[2].textContent,
               state: $("specs-state").textContent };
    """, board=BOARD, routes={"/api/board": [{"body": json.dumps(BOARD)}]})
    assert out == {"rows": 2, "head": "agent-squad · SPEC 1540 Phone hub redesign", "count": "3/8 · 38%", "state": ""}, out


@pytest.mark.node_bound
def test_each_column_of_a_specs_bar_draws_in_its_own_colour(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const rects = $("specs-list").children[0].children[1].children;
      return { fills: rects.map(r => r.getAttribute("fill")) };
    """, board=BOARD, routes={"/api/board": [{"body": json.dumps(BOARD)}]})
    assert out == {"fills": ["#b2", "#b3", "#b4", "#b5", "#b6"]}, out
    assert len(set(out["fills"])) == 5, out
