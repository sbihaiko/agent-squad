"""The mobile Board tab (#828): the gesture rules as one client module, the filter rules it shares with the desktop board,
and the page's controls and reads.

The gesture module is run under node alone, with no DOM and no server. The filter rules are the desktop board's own text,
inlined by the hub into both pages, so a test compares the two by their text. The page is checked over HTTP through the hub.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
FILTER_JS = SCRIPTS / "board-filter.js"
NODE = shutil.which("node")
ROUTES = {"/api/status", "/api/hub", "/api/board", "/api/issue"}


def run_js(src, expr, prelude=""):
    script = f"{prelude}\n{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_board", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.node_bound
@pytest.mark.parametrize("roles, owner", [
    (["card", "gap"], "card"),
    (["button", "header"], "header"),
    (["gap"], "gap"),
    (["sheet"], "sheet"),
    (["button"], "none"),
    ([], "none"),
])
def test_the_innermost_gesture_element_owns_the_gesture(roles, owner):
    assert run_js(GESTURE_JS.read_text(), f"gestureOwner({json.dumps(roles)})") == owner


@pytest.mark.node_bound
def test_a_horizontal_swipe_on_the_header_or_the_gap_moves_one_column():
    src = GESTURE_JS.read_text()
    assert run_js(src, 'gestureAct({owner:"header",dx:-80,dy:4,atTop:false})') == {"act": "column", "step": 1}
    assert run_js(src, 'gestureAct({owner:"gap",dx:80,dy:-6,atTop:false})') == {"act": "column", "step": -1}


@pytest.mark.node_bound
def test_a_card_or_a_short_or_vertical_move_is_no_column_swipe():
    src = GESTURE_JS.read_text()
    assert run_js(src, 'gestureAct({owner:"card",dx:-80,dy:2,atTop:false})') is None
    assert run_js(src, 'gestureAct({owner:"header",dx:-10,dy:0,atTop:false})') is None
    assert run_js(src, 'gestureAct({owner:"gap",dx:-60,dy:90,atTop:false})') is None


@pytest.mark.node_bound
def test_a_sheet_swipe_left_moves_to_the_next_card_and_a_pull_down_closes_it_at_the_top():
    src = GESTURE_JS.read_text()
    assert run_js(src, 'gestureAct({owner:"sheet",dx:-80,dy:0,atTop:true})') == {"act": "card", "step": 1}
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:true})') == {"act": "close"}
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:false})') is None, \
        "a pull down that is still scrolling the sheet must not close it"


@pytest.mark.node_bound
def test_the_index_stops_at_the_first_and_the_last_entry():
    src = GESTURE_JS.read_text()
    assert run_js(src, "[moveIndex(0,3,-1), moveIndex(2,3,1), moveIndex(1,3,1), moveIndex(0,0,1)]") == [0, 2, 2, 0]


@pytest.mark.node_bound
def test_a_column_swipe_at_the_first_or_last_column_changes_nothing():
    src = GESTURE_JS.read_text()
    first = {"col": 0, "cols": 3, "card": 2, "cards": 4, "sheet": False}
    last = {"col": 2, "cols": 3, "card": 1, "cards": 4, "sheet": False}
    assert run_js(src, f"afterGesture({json.dumps(first)},{{act:'column',step:-1}})") == first
    assert run_js(src, f"afterGesture({json.dumps(last)},{{act:'column',step:1}})") == last


@pytest.mark.node_bound
def test_a_column_swipe_moves_one_column_and_starts_at_its_first_card():
    src = GESTURE_JS.read_text()
    view = {"col": 0, "cols": 3, "card": 2, "cards": 4, "sheet": False}
    assert run_js(src, f"afterGesture({json.dumps(view)},{{act:'column',step:1}})") == \
        {"col": 1, "cols": 3, "card": 0, "cards": 4, "sheet": False}


@pytest.mark.node_bound
def test_the_next_card_stops_at_the_last_card_of_the_column():
    src = GESTURE_JS.read_text()
    view = {"col": 1, "cols": 3, "card": 0, "cards": 2, "sheet": True}
    once = run_js(src, f"afterGesture({json.dumps(view)},{{act:'card',step:1}})")
    assert once["card"] == 1 and once["col"] == 1 and once["sheet"] is True
    assert run_js(src, f"afterGesture({json.dumps(once)},{{act:'card',step:1}})")["card"] == 1


@pytest.mark.node_bound
def test_a_tap_opens_the_sheet_on_that_card_and_the_close_act_shuts_it():
    src = GESTURE_JS.read_text()
    view = {"col": 1, "cols": 3, "card": 0, "cards": 3, "sheet": False}
    opened = run_js(src, f"afterOpen({json.dumps(view)},2)")
    assert opened == {"col": 1, "cols": 3, "card": 2, "cards": 3, "sheet": True}
    assert run_js(src, f"afterGesture({json.dumps(opened)},{{act:'close'}})")["sheet"] is False


def test_the_gesture_module_makes_no_network_call_and_imports_nothing():
    text = GESTURE_JS.read_text()
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import "):
        assert banned not in text, f"the gesture module holds {banned!r}"


@pytest.mark.node_bound
def test_the_label_rule_groups_by_prefix_and_joins_the_groups_by_and():
    src = FILTER_JS.read_text()
    assert run_js(src, '[labelOk([],"any",{l:[]}), labelOk(["wave:1","wave:2"],"any",{l:["wave:2"]}), '
                       'labelOk(["wave:1","wave:2"],"all",{l:["wave:2"]}), labelOk(["a:1","a:2","b"],"any",{l:["a:2"]}), '
                       'labelOk(["a:1","a:2","b"],"any",{l:["a:2","b"]})]') == [True, True, False, False, True]


def test_the_filter_rules_are_one_text_for_both_pages(hub):
    text = FILTER_JS.read_text()
    assert text in hub.PAGE, "the desktop board does not carry the shared filter rules"
    assert text in hub.MOBILE_PAGE, "the mobile page does not carry the shared filter rules"
    assert hub.PAGE.count("const specMode=") == 1, "the desktop board holds its own copy of the Specs rule"


def test_the_gesture_and_filter_are_inlined_not_fetched(hub):
    assert GESTURE_JS.read_text() in hub.MOBILE_PAGE
    assert "/*GESTURE*/" not in hub.MOBILE_PAGE and "/*BOARD_FILTER*/" not in hub.MOBILE_PAGE
    assert "<script src=" not in hub.MOBILE_PAGE


def test_the_board_tab_has_the_column_and_sheet_controls(hub):
    body = hub.MOBILE_PAGE
    panel = body[body.index('data-panel="board"'):body.index('data-panel="runs"')]
    assert "Placeholder" not in panel
    for ident in ("col-prev", "col-next", "dots", "col-count", "bsp", "lf", "lfl", "cards", "sheet", "sh-link",
                  "sh-next", "sh-close"):
        assert f'id="{ident}"' in body, f"the board tab has no #{ident}"


def test_the_mobile_page_reads_only_the_existing_routes(hub):
    found = set(re.findall(r'"(/api/[^"?]*)', hub.MOBILE_PAGE))
    assert found <= ROUTES, f"the mobile page reads routes the hub does not serve: {sorted(found - ROUTES)}"
    assert "POST" not in hub.MOBILE_PAGE and "method:" not in hub.MOBILE_PAGE


def test_the_sheet_links_to_the_issue_on_github(hub):
    body = hub.MOBILE_PAGE
    assert "https://github.com/" in body and "/issues/" in body
    assert "__REPO__" not in body
