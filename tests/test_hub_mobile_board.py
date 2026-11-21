"""The mobile Board tab (#828): the gesture rules as one client module, the filter rules it shares with the desktop board,
and the page's controls and reads.

The gesture module is run under node alone, with no DOM and no server. The filter rules are the desktop board's own text,
inlined by the hub into both pages, so a test compares the two by their text. The page is checked over HTTP through the hub.
"""
import http.client
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.test_hub_mobile_move import run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
FILTER_JS = SCRIPTS / "board-filter.js"
NODE = shutil.which("node")
ROUTES = {"/api/status", "/api/hub", "/api/board", "/api/issue", "/api/subs", "/api/models", "/api/stats", "/api/runs", "/api/ended", "/api/version",
          "/api/promote", "/api/loop", "/api/profiler", "/api/pending", "/api/slack-link",
          "/api/load", "/api/chart/lead", "/api/leadtime"}
# the owner's move is a write (#829); the Monitor reads the desktop card's route (#983); the lead chart and its zoom read
# the routes the desktop draws (#1333)


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
def test_a_short_or_vertical_move_is_no_column_swipe():
    src = GESTURE_JS.read_text()
    assert run_js(src, 'gestureAct({owner:"card",dx:-80,dy:2,atTop:false,promotable:true})') == {"act": "column", "step": 1}
    assert run_js(src, 'gestureAct({owner:"card",dx:80,dy:2,atTop:false,promotable:true})') is None
    assert run_js(src, 'gestureAct({owner:"header",dx:-10,dy:0,atTop:false})') is None
    assert run_js(src, 'gestureAct({owner:"gap",dx:-60,dy:90,atTop:false})') is None


@pytest.mark.node_bound
def test_a_sheet_swipe_left_moves_to_the_next_card_and_a_pull_down_closes_it_at_the_top():
    src = GESTURE_JS.read_text()
    assert run_js(src, 'gestureAct({owner:"sheet",dx:-80,dy:0,atTop:true,thumb:true})') == {"act": "card", "step": 1}
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:true,handle:true})') == {"act": "close"}
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:false,handle:true})') is None, \
        "a pull down that is still scrolling the sheet must not close it"
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:true,handle:false,thumb:true})') is None, \
        "a pull down from the sheet body is no dismissal, even in the thumb zone (#1454)"


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


def test_the_mobile_page_keeps_the_shared_filter_rules_and_the_desktop_holds_no_copy(hub):
    text = FILTER_JS.read_text()
    assert text in hub.MOBILE_PAGE, "the mobile page does not carry the shared filter rules"
    assert "const specMode=" not in hub.PAGE and "const labelOk=" not in hub.PAGE, "the desktop board holds a copy of the mobile rule"


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
    writes = set(re.findall(r'fetch\("(/api/[^"?]*)"[^)]*method:', hub.MOBILE_PAGE))
    assert writes == {"/api/promote", "/api/profiler"}, f"the mobile page writes other routes: {sorted(writes)}"
    assert 'fetch(path, { method: "POST"' in hub.MOBILE_PAGE and 'p && p.own ? "/api/loop" : "/" + loopSlug + "/api/loop"' in hub.MOBILE_PAGE, \
        "the loop page's write is the one project's own loop route (#1599)"
    # Four writes: the owner's move, the run screen's pause or resume, which posts the run's own control route through
    # the hub's forward route (#831), the loop switch (#923, ADR-0162), and the Loop tab's CPU sampling switch, which
    # posts the profiler route (#1754). The composer's send, its card answer, its photo and its recording left the page
    # with the chat (#2490).
    # Each carries the word once: a route
    # reached through a variable path, or a method set after the call's own `)`, carries it once too often, whatever the
    # shape of the call (#829).
    assert 'fetch(runURLs(id).control, { method: "POST"' in hub.MOBILE_PAGE, "the run screen's write is not the run's control route"
    for gone in ('"/api/sessions/send"', '"/api/sessions/attach', '"/api/sessions/voice', '"/api/sessions/card/answer"'):
        assert gone not in hub.MOBILE_PAGE, f"the phone page still names {gone}"
    assert hub.MOBILE_PAGE.count("method:") == 4, "the mobile page holds a fifth write, or a method outside the four fetches"


def test_the_sheet_links_to_the_issue_on_github(hub):
    body = hub.MOBILE_PAGE
    assert "https://github.com/" in body and "/issues/" in body
    assert "__REPO__" not in body


# --- the ticket sheet on the phone (#919) ------------------------------------------------------------------------

SHEET_FIELDS = ("Column", "Labels", "Blocked by", "Blocks", "Runs", "PR · CI", "Opened")


def test_the_phone_sheet_carries_every_card_field(hub):
    """The sheet reads the card data the board read already holds, so every field is drawn from the row (#919)."""
    body = hub.MOBILE_PAGE
    for ident in ("sh-chips", "sh-meta", "sh-pos", "sh-set", "sh-prev", "sh-todo", "sh-last"):
        assert f'id="{ident}"' in body, f"the phone sheet has no #{ident}"
    for field in SHEET_FIELDS:
        assert f'"{field}"' in body, f"the phone sheet shows no {field!r} row"
    assert "ROWS.map(" in body, "the sheet draws the rows it names, never a subset of them"


def test_the_phone_sheet_takes_about_sixty_percent_of_the_screen(hub):
    """The sheet takes about 60% of the height, so the selected card scrolls above it (#919)."""
    m = re.search(r"#sheet\{[^}]*\}", hub.MOBILE_PAGE)
    assert m, "the phone sheet has no style rule"
    rule = m.group(0)
    assert "60vh" in rule and "82vh" not in rule, f"the sheet does not take 60% of the screen: {rule}"


def test_the_phone_sheet_renders_githubs_html_and_never_frames_it(hub):
    body = hub.MOBILE_PAGE
    assert "innerHTML = d.html" in body, "the phone body is GitHub's rendered HTML the route serves"
    assert "d.last" in body, "the last comment is read with the body"
    sheet = body[body.index('id="sheet"'):body.index('id="runview"')]
    assert "<iframe" not in sheet, "the sheet holds no frame"


def test_the_phone_sheet_shows_the_spec_chip_and_no_wave_chip(hub):
    body = hub.MOBILE_PAGE
    assert "chip spec" in body, "the title carries the spec chip"
    assert "chip wave" not in body and "laneWaveOf" not in body, "the wave chip is gone (SPEC 1965, #1988)"
    assert "x.sp" in body, "the spec chip reads the card's own fact, never a label rule of the view"


def test_the_phone_todo_button_shows_only_for_a_promotable_card(hub):
    body = hub.MOBILE_PAGE
    assert '$("sh-todo").hidden = !x.pm' in body, "the → Todo button reads the served promotable fact"
    assert "stageMove(x" in body, "→ Todo starts the card's own move, the one write the page already makes"


def test_the_phone_sheet_names_the_position_and_steps_with_chips_and_keys(hub):
    body = hub.MOBILE_PAGE
    assert '(at + 1) + " of " + set.length' in body, "the sheet names where the card stands in its selection"
    assert 'data-i="${i}"' in body, "the number chips pick a card"
    assert '$("sh-prev").onclick' in body and '$("sh-next").onclick' in body, "the previous and next buttons step"
    assert 'e.key === "ArrowLeft"' in body and 'e.key === "ArrowRight"' in body, "the arrow keys step"
    assert 'e.key === "j"' in body and 'e.key === "k"' in body, "j and k step"


def test_escape_or_a_swipe_down_closes_the_phone_sheet_and_clears_the_selection(hub):
    body = hub.MOBILE_PAGE
    assert 'e.key === "Escape"' in body, "Escape closes the phone sheet"
    assert '$("sh-set").innerHTML = ""' in body, "a closed sheet clears the selection it drew"
    assert 'gestureAct({ owner: t.owner, dx, dy, atTop: t.atTop' in body, "a swipe down goes through the gesture rules"
    assert 'handle: t.handle' in body, "a swipe down carries the grab handle it started on (#1454)"
    assert run_js(GESTURE_JS.read_text(), 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:true,handle:true})') == {"act": "close"}


def test_every_phone_sheet_target_is_at_least_44px(hub):
    body = hub.MOBILE_PAGE
    rule = re.search(r"#sh-nav[^{]*\{[^}]*\}", body)
    assert rule and "min-height:44px" in rule.group(0), "a target in the sheet's number row is smaller than 44px"
    chips = re.search(r"#sh-set[^{]*button[^{]*\{[^}]*\}", body)
    assert chips and "min-height:44px" in chips.group(0), "a number chip is smaller than 44px"
    todo = re.search(r"#sh-todo\{[^}]*\}", body)
    assert todo and "min-height:44px" in todo.group(0), "the → Todo button is smaller than 44px"


def test_a_blocker_in_the_phone_sheet_opens_that_ticket_in_the_same_sheet(hub):
    body = hub.MOBILE_PAGE
    assert 'class="shb" data-n=' in body, "a blocker link carries the number it opens"
    assert "openCard(n)" in body, "a blocker link opens that ticket in the same sheet, never a new tab"


HR_BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
            "groups": [{"name": "Doing", "n": 4, "items": [
                {"n": 1, "t": "one", "l": [], "hr": "run-a"},
                {"n": 2, "t": "two", "l": []},
                {"n": 3, "t": "three", "l": [], "hr": "run-a"},
                {"n": 4, "t": "four", "l": [], "hr": "run-b"}]},
                {"name": "Todo", "n": 0, "items": []}]}


@pytest.mark.node_bound
def test_a_blocker_to_a_closed_card_the_board_does_not_hold_opens_its_sheet(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        openCard(99);
        return { open: !$("sheet").hidden, n: offBoard && offBoard.n };
    """, board=[HR_BOARD])
    assert out == {"open": True, "n": 99}, out


@pytest.mark.node_bound
def test_the_phone_sheet_steps_the_cards_of_the_run_the_opened_card_holds(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        openCard(3);
        const out = { dataHr: $("cards").querySelector('[data-role="card"][data-n="3"]').dataset.hr || null,
                      first: [$("sh-pos").textContent, shown[view.card].n] };
        step({ act: "card", step: 1 });
        out.atEnd = shown[view.card].n;
        step({ act: "card", step: -1 });
        out.back = [shown[view.card].n, $("sh-pos").textContent];
        step({ act: "close" });
        openCard(2);
        out.noRun = $("sh-pos").textContent;
        return out;
    """, board=[HR_BOARD])
    assert out["dataHr"] == "run-a", "the card the phone draws carries no run id"
    assert out["first"] == ["2 of 2", 3], "the sheet steps the run's cards, not the whole column"
    assert out["atEnd"] == 3, "next stops at the last card the run holds"
    assert out["back"] == [1, "1 of 2"], "previous steps to the run's first card"
    assert out["noRun"] == "2 of 4", "a card with no run keeps the whole column as its selection"


def test_the_selected_phone_card_scrolls_above_the_sheet(hub):
    body = hub.MOBILE_PAGE
    assert 'scrollIntoView({ block: "start" })' in body, "the opened card is scrolled above the sheet"


# --- the phone's composer left the page (#2490) ------------------------------------------------------------------

def test_the_phone_page_holds_no_composer(hub):
    """The chat, its composer, its photo input and its send left the page with their rule module (#2490)."""
    body = hub.MOBILE_PAGE
    for name in ("termview-text", "termview-send", "termview-file", "termview-attach", "termview-attach-img",
                 "termview-attach-name", "termview-attach-x"):
        assert f'id="{name}"' not in body, f"the phone page still holds #{name}"
    assert not re.search(r"(?<![\w-])type=[\"']?file\b", body), "the phone page still holds a file input"
    assert "/api/sessions/send" not in body, "the phone page still names the send route"
    assert "/api/sessions/attach" not in body, "the phone page still names the attach route"


@pytest.fixture
def served(hub, monkeypatch):
    monkeypatch.setattr(hub, "run_list", lambda: [])
    monkeypatch.setattr(hub, "_loop_on", lambda pid_file=None: False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def test_the_content_security_line_admits_no_preview(served):
    c = http.client.HTTPConnection("127.0.0.1", served, timeout=10)
    c.request("GET", "/mobile/", headers={"Host": f"127.0.0.1:{served}"})
    r = c.getresponse()
    csp = r.getheader("Content-Security-Policy") or ""
    r.read()
    c.close()
    assert r.status == 200
    assert csp == ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self'"), csp
    assert "*" not in csp and "http:" not in csp and "https:" not in csp and "data:" not in csp
