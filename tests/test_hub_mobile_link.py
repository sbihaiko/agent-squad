"""The phone's card link (#1459): `#issue=N` opens that card once the board has read.

The rule is one client module, run under node alone with no DOM and no server. It names every result a link can have: the
card is drawn and opens, its lane was folded and opens first, a filter hides it, no lane holds it (a defect), or the board
does not hold it. The page only binds the hash and delegates (ADR-0137). Each acceptance case is driven through the page's
own hash handler, on the page the hub composes, over the stub DOM the board tests share.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

from tests.test_hub_mobile_move import run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
LINK_JS = SCRIPTS / "hub-mobile-link.js"
NODE = shutil.which("node")

BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
         "groups": [{"name": "Doing", "n": 3, "items": [
             {"n": 1, "t": "one", "l": []},
             {"n": 2, "t": "two", "l": []},
             {"n": 3, "t": "three", "l": []}]},
             {"name": "Todo", "n": 1, "items": [{"n": 4, "t": "four", "l": []}]}]}

# A board whose last column is a closed status: the carousel never draws it, so its card is hidden from this view.
CLOSED = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
          "groups": [{"name": "Doing", "n": 1, "items": [{"n": 1, "t": "one", "l": []}]},
                     {"name": "Done", "n": 1, "end": True, "items": [{"n": 5, "t": "five", "l": []}]}]}


def run_js(src, expr, prelude=""):
    script = f"{src}\n{prelude}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def hub():
    import importlib.util
    import os
    import tempfile
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_link", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.node_bound
def test_only_a_hash_that_names_one_issue_is_a_card_link():
    src = LINK_JS.read_text()
    assert run_js(src, '[linkHash("#issue=7"), linkHash("issue=7"), linkHash("#issue="), linkHash("#set-lop"), linkHash("")]') \
        == [7, None, None, None, None]


@pytest.mark.node_bound
def test_the_rule_names_every_result_a_link_can_have():
    src = LINK_JS.read_text()
    prelude = ("const r=o=>linkResult(7,o);"
               "const out=[r({onBoard:true,shown:true,placed:true,collapsed:false}),"
               "r({onBoard:true,shown:true,placed:true,collapsed:true}),"
               "r({onBoard:true,shown:false,placed:false,collapsed:false}),"
               "r({onBoard:false,shown:false,placed:false,collapsed:false}),"
               "r({onBoard:true,shown:true,placed:false,collapsed:false})];")
    assert run_js(src, "out.map(x=>x.why)", prelude) == ["open", "collapsed", "filtered", "missing", "unplaced"]
    said = run_js(src, "out.map(x=>x.say)", prelude)
    assert said[0] == "", "a card the board draws needs no line"
    assert all(s.startswith("#7 ") for s in said[1:]), said
    assert "folded lane" in said[1] and "hidden from this view" in said[2] and "not on the board" in said[3], said
    assert "filter" not in said[2] and "filter" not in said[4], \
        "the view hides a card through more than a label filter, and no lane holding it is no filter's doing: " + str(said)
    assert "lane" in said[4], said[4]


def test_the_link_module_makes_no_network_call_and_imports_nothing():
    text = LINK_JS.read_text()
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import "):
        assert banned not in text, f"the link module holds {banned!r}"


def test_the_link_rule_is_inlined_and_the_page_delegates_to_it(hub):
    assert LINK_JS.read_text() in hub.MOBILE_PAGE, "the mobile page does not carry the link rule"
    assert "/*LINK*/" not in hub.MOBILE_PAGE
    assert 'addEventListener("hashchange", linkOpen)' in hub.MOBILE_PAGE, "the page binds no hash handler"


@pytest.mark.node_bound
def test_a_card_link_at_the_address_opens_that_card_once_the_board_has_read(hub):
    """AC1: the hash is in the address before the page loads, and the card opens by itself after the board's read."""
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        return { open: !$("sheet").hidden, sheet: view.sheet, off: !!offBoard,
                 n: (offBoard || shown[view.card] || {}).n, said: $("bmsg").textContent,
                 drawn: !!$("cards").querySelector('[data-role="card"][data-n="3"]') };
    """, board=[BOARD], hash="#issue=3")
    assert out == {"open": True, "sheet": True, "off": False, "n": 3, "said": "", "drawn": True}, out


@pytest.mark.node_bound
def test_a_card_link_opens_at_once_when_the_board_has_already_loaded(hub):
    """AC1: the link opens its card from the board the page holds. A hash that lands while the owner is on another tab
    opens the card without waiting for a further board read: that read may be slow or may never answer, and the card the
    link names is on the board already."""
    out = run_page(hub.MOBILE_PAGE, """
        show("board"); await __tick(20);         // the board is read, drawn and held
        show("runs");                            // the owner is on another tab
        const realCall = call;
        call = (path, fresh) => String(path).startsWith("/api/board") ? new Promise(() => {}) : realCall(path, fresh);
        location.hash = "#issue=3"; __dispatch("hashchange", {});
        await __tick(10);                        // the read that tab switch started never answers
        return { tab: tabNow, open: !$("sheet").hidden, sheet: view.sheet,
                 n: (offBoard || shown[view.card] || {}).n, said: $("bmsg").textContent,
                 drawn: !!$("cards").querySelector('[data-role="card"][data-n="3"]') };
    """, board=[BOARD])
    assert out == {"tab": "board", "open": True, "sheet": True, "n": 3, "said": "", "drawn": True}, out


@pytest.mark.node_bound
def test_a_link_that_lands_before_the_board_answers_opens_one_sheet_when_it_does(hub):
    """AC1/AC3: a hash that names a card while the first board read is still in flight opens nothing against a board
    the page does not hold. The answer opens the drawn card once, never an off-board sheet that the read repaints."""
    out = run_page(hub.MOBILE_PAGE, """
        show("board"); await __tick(20);
        board = null; shown = []; view = { col: 0, cols: 0, card: 0, cards: 0, sheet: false };
        const realCall = call; let release = null;
        call = (path, fresh) => String(path).startsWith("/api/board")
          ? new Promise(res => { release = () => res(__state.board[0]); }) : realCall(path, fresh);
        loadBoard();                                      // the first board read, still in flight
        await __tick(5);
        location.hash = "#issue=3"; __dispatch("hashchange", {});
        await __tick(5);
        const early = { sheet: view.sheet, hidden: $("sheet").hidden, off: !!offBoard };
        release(); await __tick(10);                      // the board answers
        return { early, sheet: view.sheet, hidden: $("sheet").hidden, off: !!offBoard,
                 n: (offBoard || shown[view.card] || {}).n, said: $("bmsg").textContent,
                 drawn: !!$("cards").querySelector('[data-role="card"][data-n="3"]') };
    """, board=[BOARD])
    assert out["early"] == {"sheet": False, "hidden": True, "off": False}, out
    assert out["sheet"] is True and out["hidden"] is False and out["off"] is False and out["n"] == 3, out
    assert out["said"] == "" and out["drawn"] is True, out


@pytest.mark.node_bound
def test_an_issue_the_board_does_not_hold_names_its_result_and_opens_its_issue(hub):
    """AC2: a missing issue is a named result, never a blank board: the sheet reads the issue on its own."""
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        location.hash = "#issue=99"; __dispatch("hashchange", {});
        await __tick(10);
        return { open: !$("sheet").hidden, off: !!offBoard, n: offBoard && offBoard.n,
                 title: $("sh-title").textContent, said: $("bmsg").textContent };
    """, board=[BOARD])
    assert out["open"] is True and out["off"] is True and out["n"] == 99, out
    assert out["title"] == "#99 ", out
    assert out["said"] == "#99 is not on the board: its issue is read on its own.", out


@pytest.mark.node_bound
def test_a_card_the_filters_hide_names_its_result_and_opens_its_issue(hub):
    """AC2: a filtered card is a named result, never a blank board: the filters are kept, and the issue opens."""
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        bo = { labels: ["wave:1"] };
        show("board"); await __tick(20);
        const kept = columnCards(board.groups[0]).some(x => x.n === 3);   // the page's own filter read
        location.hash = "#issue=3"; __dispatch("hashchange", {});
        await __tick(10);
        return { kept, open: !$("sheet").hidden, off: !!offBoard, n: offBoard && offBoard.n,
                 labels: bo.labels, said: $("bmsg").textContent };
    """, board=[BOARD])
    assert out["kept"] is False, "the filter left the card in its column, so nothing was hidden"
    assert out["open"] is True and out["off"] is True and out["n"] == 3, out
    assert out["labels"] == ["wave:1"], "the link cleared the owner's filter"
    assert out["said"] == "#3 is hidden from this view: its issue is read on its own.", out


@pytest.mark.node_bound
def test_a_card_a_closed_column_hides_names_its_result_and_opens_its_issue(hub):
    """AC2: a card the view does not draw is a named result wherever the drop comes from. A closed status column is no
    carousel column, so the card is hidden from this view like a filtered one, and the board says so."""
    out = run_page(hub.MOBILE_PAGE, """
        show("board"); await __tick(20);
        const facts = linkFacts(5), drawnBefore = !!$("cards").querySelector('[data-role="card"][data-n="5"]');
        location.hash = "#issue=5"; __dispatch("hashchange", {});
        await __tick(10);
        return { facts, drawnBefore, open: !$("sheet").hidden, off: !!offBoard, n: offBoard && offBoard.n,
                 said: $("bmsg").textContent };
    """, board=[CLOSED])
    assert out["facts"]["shown"] is False and out["facts"]["dropped"] == "column", out
    assert out["drawnBefore"] is False, out
    assert out["open"] is True and out["off"] is True and out["n"] == 5, out
    assert out["said"] == "#5 is hidden from this view: its issue is read on its own.", out


@pytest.mark.node_bound
def test_the_rule_names_which_read_hid_the_card(hub):
    """AC2: a label filter, the spec mode and a column the carousel leaves out each drop a card, and the page names
    which read did it, so no line and no caller names the owner's filter for another read's doing."""
    out = run_page(hub.MOBILE_PAGE, """
        show("board"); await __tick(20);
        const clean = String(linkFacts(3).dropped);
        bo = { labels: ["wave:1"] }; show("board"); await __tick(20);
        const byLabel = String(linkFacts(3).dropped);
        bo = { specs: 1 }; show("board"); await __tick(20);
        const bySpec = String(linkFacts(3).dropped);
        return { clean, byLabel, bySpec };
    """, board=[BOARD])
    assert out == {"clean": "", "byLabel": "labels", "bySpec": "spec"}, out


@pytest.mark.node_bound
def test_a_card_in_a_folded_lane_names_its_result_and_opens_the_lane(hub):
    """AC2: a collapsed card is a named result, never a blank board: its lane opens and the card is drawn."""
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        bo = { mlc: { "none": true } };
        show("board"); await __tick(20);
        const drawnBefore = !!$("cards").querySelector('[data-role="card"][data-n="3"]');
        location.hash = "#issue=3"; __dispatch("hashchange", {});
        await __tick(10);
        return { drawnBefore, folded: bo.mlc["none"], sheet: view.sheet, off: !!offBoard,
                 n: (offBoard || shown[view.card] || {}).n, said: $("bmsg").textContent,
                 drawn: !!$("cards").querySelector('[data-role="card"][data-n="3"]') };
    """, board=[BOARD])
    assert out["drawnBefore"] is False and out["folded"] is False, out
    assert out["sheet"] is True and out["off"] is False and out["n"] == 3 and out["drawn"] is True, out
    assert out["said"] == "#3 was in a folded lane: the lane opened.", out


@pytest.mark.node_bound
def test_a_hash_change_after_the_load_opens_the_named_card(hub):
    """AC3: the handler answers every hash change, not only the load: the same board opens two cards in two columns, and a
    hash that stops naming a card closes the sheet the link opened, so the destination the owner asked for is not hidden."""
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        show("board"); await __tick(20);
        location.hash = "#issue=2"; __dispatch("hashchange", {});
        await __tick(10);
        const first = { open: !$("sheet").hidden, n: (offBoard || shown[view.card] || {}).n, col: $("col-name").textContent };
        location.hash = "#issue=4"; __dispatch("hashchange", {});
        await __tick(10);
        const second = { open: !$("sheet").hidden, n: (offBoard || shown[view.card] || {}).n, col: $("col-name").textContent,
                         title: $("sh-title").textContent };
        location.hash = "#set-lop"; __dispatch("hashchange", {});
        await __tick(10);
        return { first, second, sheet: view.sheet, hidden: $("sheet").hidden, said: $("bmsg").textContent };
    """, board=[BOARD])
    assert out["first"] == {"open": True, "n": 2, "col": "Doing"}, out
    assert out["second"] == {"open": True, "n": 4, "col": "Todo", "title": "#4 four"}, out
    assert out["sheet"] is False and out["hidden"] is True, "a hash that names no card left the card's sheet open"
    assert out["said"] == "", "the link's line stayed on the board after the hash named no card"


@pytest.mark.node_bound
def test_a_second_card_link_wins_over_a_read_still_in_flight(hub):
    """AC3, back and forward: a link to another card while the first card's issue is still being read leaves the second
    card on the sheet, never the first card's late read painted over it. Each read is held open by the driver and settled
    in the order the page must survive: the second card's read answers first, and the first card's answers last, so only
    the sheet's own key can keep the stale one off: the order cannot."""
    out = run_page(hub.MOBILE_PAGE, """
        show("board"); await __tick(20);
        const realCall = call, held = {};
        call = (path, fresh) => {
          const m = /[?&]n=(\\d+)/.exec(String(path));
          if (!String(path).startsWith("/api/issue") || !m) return realCall(path, fresh);
          const n = +m[1];                                     // an issue read answers with its own card's title
          return new Promise(res => { (held[n] = held[n] || []).push(() => res({ n, title: n === 2 ? "two" : "four",
                                                                             body: "body of " + n })); });
        };
        location.hash = "#issue=2"; __dispatch("hashchange", {});
        await __tick(5);
        location.hash = "#issue=4"; __dispatch("hashchange", {});
        await __tick(5);
        const asked = Object.keys(held).sort(), pending = Object.values(held).map(h => h.length);
        held[4].forEach(f => f()); await __tick(5);   // every read for card 4 answers
        const mid = { sheet: view.sheet, n: (offBoard || shown[view.card] || {}).n, title: $("sh-title").textContent };
        held[2].forEach(f => f()); await __tick(5);   // every read for card 2 answers last, out of order
        return { asked, pending, mid, sheet: view.sheet, n: (offBoard || shown[view.card] || {}).n,
                 title: $("sh-title").textContent, body: $("sh-body").textContent, said: $("bmsg").textContent };
    """, board=[BOARD])
    assert out["asked"] == ["2", "4"] and out["pending"] == [1, 1], "neither read answered before the driver settled it"
    assert out["mid"] == {"sheet": True, "n": 4, "title": "#4 four"}, out
    assert out["sheet"] is True and out["n"] == 4, out
    assert out["title"] == "#4 four", "card 2's late read painted over the sheet's card: " + out["title"]
    assert out["body"] == "body of 4", "card 2's late read painted its body over the sheet's card: " + out["body"]
    assert out["said"] == "", out


@pytest.mark.node_bound
def test_duplicate_issue_reads_for_a_card_the_sheet_left_never_repaint_it(hub):
    """AC3, back and forward: a link to a card the owner already left leaves two issue reads in flight, and both answer
    after the sheet moved on. Neither paints, however many reads the page fired, because the sheet's own card key is
    what every answer is checked against."""
    out = run_page(hub.MOBILE_PAGE, """
        show("board"); await __tick(20);
        const realCall = call, held = {};
        call = (path, fresh) => {
          const m = /[?&]n=(\\d+)/.exec(String(path));
          if (!String(path).startsWith("/api/issue") || !m) return realCall(path, fresh);
          const n = +m[1];
          return new Promise(res => { (held[n] = held[n] || []).push(() => res({ n, title: "card " + n, body: "body of " + n })); });
        };
        location.hash = "#issue=2"; __dispatch("hashchange", {}); await __tick(5);
        location.hash = "#issue=4"; __dispatch("hashchange", {}); await __tick(5);
        location.hash = "#issue=2"; __dispatch("hashchange", {}); await __tick(5);
        location.hash = "#issue=4"; __dispatch("hashchange", {}); await __tick(5);
        const pending = Object.keys(held).sort().map(k => k + ":" + held[k].length);
        held[4].forEach(f => f()); await __tick(5);
        const mid = { n: (offBoard || shown[view.card] || {}).n, title: $("sh-title").textContent };
        held[2].forEach(f => f()); await __tick(5);   // both reads for card 2 answer after the sheet left it
        return { pending, mid, sheet: view.sheet, n: (offBoard || shown[view.card] || {}).n,
                 title: $("sh-title").textContent, body: $("sh-body").textContent };
    """, board=[BOARD])
    assert out["pending"] == ["2:2", "4:2"], "the page did not fire two reads for each card: " + str(out["pending"])
    assert out["mid"] == {"n": 4, "title": "#4 card 4"}, out
    assert out["sheet"] is True and out["n"] == 4, out
    assert out["title"] == "#4 card 4", "a read for card 2 painted its title over the sheet's card: " + out["title"]
    assert out["body"] == "body of 4", "a read for card 2 painted its body over the sheet's card: " + out["body"]


@pytest.mark.node_bound
def test_a_card_link_that_names_a_missing_issue_still_closes_on_a_hash_that_names_no_card(hub):
    """AC3: the rule holds for the sheet the link opened on its own (a missing issue), not only for a drawn card. The
    board reads again under the open sheet, which is no drawn card and so is re-keyed by no read, and the tab hash still
    closes the sheet the link opened."""
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        location.hash = "#issue=99"; __dispatch("hashchange", {});
        await __tick(10);
        const opened = { open: !$("sheet").hidden, off: !!offBoard, said: $("bmsg").textContent };
        await loadBoard();                        // the board reads again under the link's off-board sheet
        const still = { open: !$("sheet").hidden, off: !!offBoard, n: offBoard && offBoard.n };
        location.hash = "#more"; __dispatch("hashchange", {});
        await __tick(10);
        return { opened, still, sheet: view.sheet, hidden: $("sheet").hidden, said: $("bmsg").textContent };
    """, board=[BOARD])
    assert out["opened"] == {"open": True, "off": True,
                             "said": "#99 is not on the board: its issue is read on its own."}, out
    assert out["still"] == {"open": True, "off": True, "n": 99}, out
    assert out["sheet"] is False and out["hidden"] is True, out
    assert out["said"] == "", out
