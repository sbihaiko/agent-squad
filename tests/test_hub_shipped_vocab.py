"""The hub draws the shipped vocabulary; pull requests sit beside the axis (#247, spec #132).

The board's columns are the published definition's statuses, in its order, with their colours. A card's column is the
one the table names for its Status (old names are read too). A pull request is a lane ahead of the axis with a badge,
never a column. A spec is an issue with the spec label; the Specs filter shows those items, and no column holds them.
"""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def table():
    return _module("board_columns_shipped_view", SCRIPTS / "board_columns.py")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/agent-squad")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_shipped_vocab", HUB_PY)


def _item(n, status, state="open", labels=()):
    return {"n": n, "t": f"card {n}", "s": state, "st": status, "l": list(labels)}


CARDS = [  # (item, the column the table names, the freeze marker)
    (_item(1, "In Progress"), "Doing", None),
    (_item(2, "Doing"), "Doing", None),
    (_item(3, "Open Specs", labels=["spec"]), "Backlog", None),
    (_item(4, "Blocked"), "Backlog", "frozen:owner"),
    (_item(5, "Code Complete"), "Code Complete", None),
    (_item(6, "Landing"), "Code Complete", None),
    (_item(7, "Merging"), "Code Complete", None),
    (_item(8, None), "Backlog", None),
    (_item(9, "Todo"), "Todo", None),
    (_item(10, "Done", state="closed"), "Closed", None),
    (_item(11, "Abandoned", state="closed"), "Abandoned", None),
    (_item(12, "Todo", state="closed"), "Closed", None),
    (_item(13, "Doing", labels=["frozen:owner"]), "Doing", "frozen:owner"),
    (_item(14, "Code Review"), "Code Review", None),   # the child's review holds it (#603)
]


@pytest.fixture
def board(hub, monkeypatch):
    prs = [{"n": 101, "t": "conflict", "u": "4", "ms": "dirty", "ci": ["success"], "rollup": "pass", "h": "a" * 40},
           {"n": 102, "t": "red", "u": "3", "ms": "clean", "ci": ["failure"], "rollup": "fail", "h": "b" * 40},
           {"n": 103, "t": "green", "u": "2", "ms": "clean", "ci": ["success"], "rollup": "pass", "h": "c" * 40},
           {"n": 104, "t": "approved", "u": "1", "ms": "clean", "ci": ["success"], "rollup": "pass", "h": "d" * 40}]
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "err": None, "items": [c[0] for c in CARDS], "prs": prs,
                                             "lc": {}})
    monkeypatch.setattr(hub, "summary_lines", lambda n, sha9: [f"#{n} {sha9} CI=pass APPROVE"] if n == 104 else [])
    monkeypatch.setattr(hub, "pr_state", lambda x: ["idle", "awaiting review"])
    monkeypatch.setattr(hub, "gate_strip", lambda x, t=None: None)
    return hub.board()


def test_the_board_columns_are_the_shipped_statuses_in_order_with_their_colours(table, hub, board):
    shipped = [s["name"] for s in table.shipped_columns()]
    assert [g["name"] for g in board["groups"]] == shipped, "the board's columns are not the definition's, in its order"
    assert hub.ORDER == shipped
    flow = json.loads(re.search(r"FLOW=(\[[^;]*?\]),", hub.PAGE).group(1))
    assert flow == shipped, "the page's flow is not the definition's order"
    colours = json.loads(re.search(r"const COLORS=(\{.*?\});", hub.PAGE).group(1))
    assert list(colours) == shipped + [lane["name"] for lane in table.LANES]
    for column in table.view_columns():
        assert re.fullmatch(r"var\(--chart-[\w-]+\)", colours[column["name"]]), \
            f"{column['name']} copies a palette value instead of reading a chart role"


@pytest.mark.parametrize("item,column,freeze", CARDS, ids=[f"#{c[0]['n']}-{c[1]}" for c in CARDS])
def test_each_card_sits_in_the_column_the_table_names(board, item, column, freeze):
    where = [(g["name"], x) for g in board["groups"] for x in g["items"] if x["n"] == item["n"]]
    ends = {g["name"]: g["n"] for g in board["groups"] if g.get("end")}
    if column in ends:
        assert where == [], f"#{item['n']} ({item['st']}) is served as a row: a closed row never is"
        assert ends[column] == [c[1] for c in CARDS].count(column), f"the {column} count is wrong: {ends}"
        return
    assert [w[0] for w in where] == [column], f"#{item['n']} ({item['st']}) is not in {column}"
    assert where[0][1].get("fz") == freeze, f"#{item['n']} wears the wrong freeze marker"


def test_the_open_lane_is_drawn_beside_the_axis_with_its_badge(table, hub, board):
    lanes = {lane["name"] for lane in table.LANES}
    assert lanes == {"Open PRs"}, "the table does not name the one open lane"
    assert not lanes & {g["name"] for g in board["groups"]}, "a lane is drawn as a column"
    assert not lanes & set(hub.ORDER), "a lane is in the board's column order"
    flow = json.loads(re.search(r"FLOW=(\[[^;]*?\]),", hub.PAGE).group(1))
    assert not lanes & set(flow), "a lane is on the axis"
    assert board["prs"]["name"] in lanes and "prev" not in board, "the open lane is served, and no closed lane is"
    assert {p["n"]: p.get("pb") for p in board["prs"]["items"]} == \
        {101: "conflict", 102: "checks red", 103: "green", 104: "approved"}
    assert all(p["kind"] == "pr" for p in board["prs"]["items"]), "a pull-request row carries no kind"
    page = hub.PAGE
    assert re.search(r"all=\[b\.prs,\.\.\.b\.groups\.sort\(", page), "the lane is not drawn ahead of the axis"
    assert "class=pbg" in page or 'class="pbg' in page, "the page does not draw the pull request's badge"
    assert re.search(r"isPR\(g\.name\)\?\" prs lane\"", page), "the lane does not wear its own look"


def test_the_specs_come_from_the_items_and_no_column_holds_them(table, hub, board):
    rows = {x["n"]: x for g in board["groups"] for x in g["items"]}
    assert [n for n, x in rows.items() if x.get("sp")] == [3], "only the spec-labelled item is a spec"
    assert "Open Specs" not in hub.ORDER and "Open Specs" not in [g["name"] for g in board["groups"]]
    page = hub.PAGE
    assert '<details id=fs' not in page, "the removed Specs filter is still on the page (SPEC 1965)"
    assert "specOptions(boardGroups" in page, "the spec reading is not built from the items"
    assert "boardOk=" in page, "the filter rule left the shared board code"


def test_the_hub_holds_no_column_name_of_its_own(table):
    source = HUB_PY.read_text()
    names = [c["name"] for c in table.view_columns()] + [lane["name"] for lane in table.LANES] + list(table.SHIPPED_NAME)
    for name in names:
        assert f'"{name}"' not in source and f"'{name}'" not in source, f"hub.py names {name!r} itself"
