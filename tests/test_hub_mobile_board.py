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
          "/api/sessions", "/api/sessions/card", "/api/sessions/card/answer", "/api/sessions/conversation", "/api/sessions/send",
          "/api/sessions/attach", "/api/sessions/image", "/api/sessions/clip", "/api/sessions/voice", "/api/promote", "/api/loop", "/api/profiler",
          "/api/load", "/api/chart/lead", "/api/leadtime"}
# the owner's move is a write (#829); the Monitor reads the desktop card's route (#983); the phone's photo takes the one
# attach write (#1155); a bubble's thumbnail reads the image route the overlay already serves (#1602); the phone's voice
# reads one clip and writes one recording (#1603); the lead chart and its zoom read the routes the desktop draws (#1333)


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
    assert writes == {"/api/promote", "/api/sessions/send", "/api/sessions/card/answer", "/api/profiler"},f"the mobile page writes other routes: {sorted(writes)}"
    assert 'fetch(path, { method: "POST"' in hub.MOBILE_PAGE and 'p && p.own ? "/api/loop" : "/" + loopSlug + "/api/loop"' in hub.MOBILE_PAGE, \
        "the loop page's write is the one project's own loop route (#1599)"
    # Eight writes: the owner's move, the run screen's pause or resume, which posts the run's own control route through
    # the hub's forward route (#831), the session composer's send (#834), the loop switch (#923, ADR-0162), the answer
    # to a session's question card (#1093), the composer's one photo (#1155), the composer's one recording (#1603), and
    # the Loop tab's CPU sampling switch, which posts the profiler route (#1754).
    # Each carries the word once: a route
    # reached through a variable path, or a method set after the call's own `)`, carries it once too often, whatever the
    # shape of the call (#829).
    assert 'fetch(runURLs(id).control, { method: "POST"' in hub.MOBILE_PAGE, "the run screen's write is not the run's control route"
    assert 'fetch("/api/sessions/attach?" + q, { method: "POST"' in hub.MOBILE_PAGE, "the phone's photo is not the one attach route"
    assert 'fetch("/api/sessions/voice?" + q, { method: "POST"' in hub.MOBILE_PAGE, "the phone's recording is not the one voice route"
    # The session overlay (#898) rides on the page and posts six routes through the source the page names: its send route,
    # its screenshot route (#903), the one attach route a staged file takes (#1154), and its hosted terminal's attach,
    # input and start routes (#1216). Each carries the word once.
    overlay = hub.OVERLAY_JS.count("method:")
    assert overlay == 6 and 'fetch(s.src.api + "/send", { method: "POST"' in hub.OVERLAY_JS and 'fetch(s.src.api + "/screenshot?sid="' in hub.OVERLAY_JS
    assert 'fetch(s.src.api + "/attach?" + q, { method: "POST"' in hub.OVERLAY_JS, "the overlay's attach write is not the one attach route"
    assert '"/terminals/attach", { method: "POST"' in hub.OVERLAY_JS and '"/terminals/write", { method: "POST"' in hub.OVERLAY_JS
    assert '"/terminals/start", { method: "POST"' in hub.OVERLAY_JS
    assert hub.MOBILE_PAGE.count("method:") == 8 + overlay, "the mobile page holds a ninth write, or a method outside the eight fetches"


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
    sheet = body[body.index('id="sheet"'):body.index('id="termview"')]
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


# --- the phone's one photo (#1155) -------------------------------------------------------------------------------

SID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
CID = "11111111-1111-4111-8111-111111111111"
CAP = 4000
EMPTY = {"body": json.dumps({"turns": [], "next": None}), "status": 200}


def reply(status):
    return {"body": json.dumps({"status": status, "reason": ""}), "status": 200}


def routes(*answers):
    return {"/api/sessions/conversation": [EMPTY],
            "/api/sessions/attach": list(answers) or [reply("sent")]}


PHOTO_PRELUDE = """
  globalThis.crypto = { randomUUID: () => "__CID__" };
  const revoked = [];
  globalThis.URL = { createObjectURL: () => "blob:hub/photo", revokeObjectURL: u => revoked.push(u) };
  const seen = [];
  const realFetch = fetch;
  fetch = (path, opts) => { seen.push(String(path)); return realFetch(path, opts); };
  const pick = async () => {
    const picker = $("termview-file");
    picker.files = [{ name: "camera.jpg", arrayBuffer: async () => new Uint8Array([255, 216, 255, 65]).buffer }];
    picker.listeners.change[0]({ target: picker });
    await __tick(20);
  };
  const queryOf = url => {
    const out = {};
    for (const part of (url.split("?")[1] || "").split("&").filter(Boolean)) {
      const [k, v] = part.split("=");
      out[decodeURIComponent(k)] = decodeURIComponent((v || "").replace(/\\+/g, " "));
    }
    return out;
  };
""".replace("__CID__", CID)


def test_the_composer_has_one_image_input_that_offers_the_camera_and_the_library(hub):
    inputs = re.findall(r"<input\b[^>]*>", hub.MOBILE_PAGE)
    files = [tag for tag in inputs if re.search(r"(?<![\w-])type=[\"']?file\b", tag)]
    assert len(files) == 1, f"the phone composer holds {len(files)} file inputs, not one"
    assert re.search(r"(?<![\w-])accept=[\"']image/\*[\"']", files[0]), files[0]
    assert not re.search(r"(?<![\w-])capture\b", files[0]), "a capture attribute skips the library"


def test_the_picker_is_a_label_for_that_one_input(hub):
    page = hub.MOBILE_PAGE
    ident = re.search(r"(?<![\w-])id=[\"']?([\w-]+)", re.search(r"<input\b[^>]*type=[\"']?file\b[^>]*>", page).group(0)).group(1)
    assert re.search(rf"<label\b[^>]*\bfor=[\"']?{re.escape(ident)}[\"']?[^>]*>[^<]+</label>", page)


@pytest.mark.node_bound
def test_a_picked_image_previews_from_the_hub_and_sends_to_the_open_session(hub):
    out = run_page(hub.MOBILE_PAGE, PHOTO_PRELUDE + """
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      const chip = { hidden: $("termview-attach").hidden, src: $("termview-attach-img").src,
                     name: $("termview-attach-name").textContent };
      $("termview-text").value = "Look at this";
      await $("termview-send").onclick();
      const url = seen.find(u => u.startsWith("/api/sessions/attach")) || "";
      const post = __state.posts.find(p => p.path === "/api/sessions/attach") || null;
      return { chip, status: $("termview-sent").textContent, text: $("termview-text").value,
               chipAfter: $("termview-attach").hidden, revoked,
               post: post && { method: post.opts.method, type: post.opts.headers["Content-Type"],
                               bytes: Array.from(new Uint8Array(post.opts.body)), query: queryOf(url) },
               textSends: __state.posts.filter(p => p.path === "/api/sessions/send").length };
    """.replace("__SID__", SID), routes=routes())
    assert out["chip"] == {"hidden": False, "src": "blob:hub/photo", "name": "camera.jpg"}, "the photo has no hub preview"
    assert out["post"] == {"method": "POST", "type": "application/octet-stream", "bytes": [255, 216, 255, 65],
                           "query": {"kind": "image", "sid": SID, "client_id": CID, "text": "Look at this"}}
    assert out["textSends"] == 0, "the photo took a second upload path"
    assert out["status"] == "Sent: the session's inbox accepted the message and the photo"
    assert out["text"] == "" and out["chipAfter"] is True and out["revoked"] == ["blob:hub/photo"]


@pytest.mark.node_bound
def test_removing_the_picked_photo_clears_the_chip_and_sends_nothing(hub):
    out = run_page(hub.MOBILE_PAGE, PHOTO_PRELUDE + """
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      $("termview-attach-x").onclick();
      const cleared = $("termview-attach").hidden;
      $("termview-send").onclick();
      await __tick(10);
      return { cleared, revoked,
               attach: __state.posts.filter(p => p.path === "/api/sessions/attach").length,
               textSends: __state.posts.filter(p => p.path === "/api/sessions/send").length };
    """.replace("__SID__", SID), routes=routes())
    assert out["cleared"] is True, "a removed photo keeps its chip"
    assert out["revoked"] == ["blob:hub/photo"]
    assert out["attach"] == 0 and out["textSends"] == 0, "a removed photo reached the session"


@pytest.mark.node_bound
def test_a_photo_read_that_settles_after_a_switch_leaves_the_open_chip_alone(hub):
    """A read in flight belongs to the session that picked it (#1155): a slow read for A that settles while B is open
    must not repaint B's chip, and the read still lands on A's own draft."""
    out = run_page(hub.MOBILE_PAGE, PHOTO_PRELUDE + """
      const img = $("termview-attach-img");
      let writes = 0, cur = "";
      Object.defineProperty(img, "src", { configurable: true, get: () => cur, set: v => { writes++; cur = v; } });
      let release;
      const gate = new Promise(r => { release = r; });
      const pickSlow = () => {
        const picker = $("termview-file");
        picker.files = [{ name: "a.jpg", arrayBuffer: async () => { await gate; return new Uint8Array([1, 2, 3]).buffer; } }];
        picker.listeners.change[0]({ target: picker });
      };
      openConversation("__A__", "a");
      await __tick(10);
      pickSlow();
      await __tick(10);
      openConversation("__B__", "b");
      await __tick(10);
      await pick();
      const painted = writes;
      release();
      await __tick(20);
      const after = writes;
      openConversation("__A__", "a");
      await __tick(10);
      return { painted, after, aHidden: $("termview-attach").hidden, aName: $("termview-attach-name").textContent };
    """.replace("__A__", SID).replace("__B__", OTHER), routes=routes())
    assert out["after"] == out["painted"], f"a read for another session repainted the open chip: {out}"
    assert out["aHidden"] is False and out["aName"] == "a.jpg", f"A's own read did not land: {out}"


@pytest.mark.node_bound
def test_an_uncertain_photo_send_keeps_its_photo_and_reuses_its_request(hub):
    """An uncertain send is no answer (#1155): the photo stays, and the retry is the same request under the same client
    id, so the hub answers one message and never a text-only send that lost the photo."""
    answers = routes(reply("uncertain"), reply("sent"))
    answers["/api/sessions/send"] = [reply("sent")]
    out = run_page(hub.MOBILE_PAGE, PHOTO_PRELUDE + """
      let n = 0; crypto.randomUUID = () => "cid-" + (++n);
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      $("termview-text").value = "Look at this";
      await $("termview-send").onclick();
      const once = { hidden: $("termview-attach").hidden, name: (termDrafts.get("__SID__").photo || {}).name || null,
                     said: $("termview-sent").textContent, cid: termDrafts.get("__SID__").cid };
      await $("termview-send").onclick();
      const urls = seen.filter(u => u.startsWith("/api/sessions/attach"));
      return { once, said: $("termview-sent").textContent, hiddenAfter: $("termview-attach").hidden,
               cids: urls.map(u => queryOf(u).client_id), texts: urls.map(u => queryOf(u).text),
               bodies: __state.posts.filter(p => p.path === "/api/sessions/attach").map(p => Array.from(new Uint8Array(p.opts.body))),
               textSends: __state.posts.filter(p => p.path === "/api/sessions/send").length };
    """.replace("__SID__", SID), routes=answers)
    assert out["once"]["hidden"] is False and out["once"]["name"] == "camera.jpg", f"an uncertain send dropped the photo: {out}"
    assert out["once"]["said"].startswith("Uncertain"), out
    assert len(out["cids"]) == 2 and out["cids"][0] == out["cids"][1] == out["once"]["cid"], f"the retry had a new request id: {out}"
    assert out["texts"] == ["Look at this", "Look at this"], out
    assert out["bodies"] == [[255, 216, 255, 65], [255, 216, 255, 65]], out
    assert out["textSends"] == 0, "the retry lost the photo and took the plain text route"
    assert out["hiddenAfter"] is True, "a sent photo keeps its chip"


@pytest.mark.node_bound
@pytest.mark.parametrize("given", [CAP, CAP + 5])
def test_a_photo_send_carries_at_most_the_phones_text_cap(hub, given):
    """The textarea's maxlength is the phone's one text cap (#1006): the send clamps to it too, so a photo message never
    carries more than 4000 characters, and a message at the cap is carried whole (#1155)."""
    out = run_page(hub.MOBILE_PAGE, PHOTO_PRELUDE + f"""
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      $("termview-text").value = "x".repeat({given});
      await $("termview-send").onclick();
      const url = seen.find(u => u.startsWith("/api/sessions/attach")) || "";
      return {{ text: queryOf(url).text, sent: $("termview-sent").textContent }};
    """.replace("__SID__", SID), routes=routes())
    assert out["text"] == "x" * CAP, f"{given} characters reached the attach route"


def test_the_text_cap_and_the_plain_text_send_are_unchanged(hub):
    page = hub.MOBILE_PAGE
    assert 'id="termview-text" maxlength="4000" rows="3"' in page
    assert 'fetch("/api/sessions/send", { method: "POST"' in page, "the plain text no longer takes the send route"


@pytest.fixture
def served(hub, monkeypatch):
    monkeypatch.setattr(hub, "run_list", lambda: [])
    monkeypatch.setattr(hub, "_loop_on", lambda pid_file=None: False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def test_the_content_security_line_admits_the_hub_preview_and_nothing_more(served):
    c = http.client.HTTPConnection("127.0.0.1", served, timeout=10)
    c.request("GET", "/mobile/", headers={"Host": f"127.0.0.1:{served}"})
    r = c.getresponse()
    csp = r.getheader("Content-Security-Policy") or ""
    r.read()
    c.close()
    assert r.status == 200
    assert csp == ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self' blob:"), csp
    assert "*" not in csp and "http:" not in csp and "https:" not in csp and "data:" not in csp
