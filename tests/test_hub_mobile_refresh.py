"""A board refresh keeps the open sheet on its issue and column, and redraws board-derived metadata (#1005)."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"
PAGE = (SCRIPTS / "hub-mobile.html").read_text()
GESTURE = (SCRIPTS / "hub-mobile-gesture.js").read_text()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed")


def run_js(expr):
    p = subprocess.run([NODE, "-"], input=f"{GESTURE}\nconsole.log(JSON.stringify({expr}));",
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def anchor(view, cols, id_):
    return run_js(f"anchorView({json.dumps(view)}, {json.dumps(cols)}, {json.dumps(id_)})")


V = {"col": 0, "cols": 2, "card": 1, "cards": 2, "sheet": True}


def test_inserted_card_keeps_the_open_issue():
    cols = [{"name": "Todo", "flat": [{"r": "a/b", "n": 9}, {"r": "a/b", "n": 7}, {"r": "a/b", "n": 8}]}]
    out = anchor(V, cols, {"name": "Todo", "key": "a/b#8"})
    assert (out["col"], out["card"], out["sheet"]) == (0, 2, True)


def test_the_same_number_in_another_repository_is_another_issue():
    cols = [{"name": "Todo", "flat": [{"r": "x/y", "n": 8}, {"r": "a/b", "n": 8}]}]
    assert anchor(V, cols, {"name": "Todo", "key": "a/b#8"})["card"] == 1


def test_column_index_follows_the_column_name():
    cols = [{"name": "New", "flat": [{"r": "", "n": 1}]}, {"name": "Todo", "flat": [{"r": "", "n": 8}]}]
    out = anchor(V, cols, {"name": "Todo", "key": "#8"})
    assert (out["col"], out["card"]) == (1, 0)


def test_a_card_that_moved_column_is_followed():
    cols = [{"name": "Todo", "flat": []}, {"name": "Doing", "flat": [{"r": "", "n": 8}]}]
    out = anchor(V, cols, {"name": "Todo", "key": "#8"})
    assert (out["col"], out["card"], out["sheet"]) == (1, 0, True)


def test_a_card_gone_from_the_board_closes_the_sheet():
    cols = [{"name": "Todo", "flat": [{"r": "", "n": 1}]}]
    assert anchor(V, cols, {"name": "Todo", "key": "#8"})["sheet"] is False


def test_load_board_reanchors_before_it_draws():
    body = re.search(r"async function loadBoard\(\) \{(.*?)\n\}\n", PAGE, re.S).group(1)
    assert "anchorView(" in body and body.index("anchorView(") < body.index("refresh()")


def test_unchanged_key_still_redraws_board_metadata():
    body = re.search(r"function renderSheet\(\) \{(.*?)\n\}\n", PAGE, re.S).group(1)
    head = body[:body.index("issueKey = key;")]
    assert "sheetMeta(" in head and "sheetChips(" in head
