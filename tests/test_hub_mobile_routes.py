"""The phone's one route table (SPEC 2498, #2499): five tabs, one address per screen, Back to the parent, and the saved
addresses of the screens the page dropped.

The page boots under the stub DOM of test_hub_mobile_move, which keeps the address and the state of every history entry,
so a route is read from the hash the owner would see in the address bar. No server runs and no rule lives in the test.
"""
import importlib.util
import os
import pathlib
import re
import tempfile

import pytest

from tests.test_hub_mobile_move import run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
TABS = ["now", "work", "specs", "runs", "spend"]
TAB_NAME = {"now": "Now", "work": "Work", "specs": "Specs", "runs": "Runs", "spend": "Spend"}

# A board where the search names one card and the carousel draws two columns, so a column and a query are facts to leave.
ROUTE_BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
               "groups": [{"name": "Backlog", "n": 2, "items": [
                               {"n": 7, "t": "Fix the phone", "l": ["area:phone"]},
                               {"n": 8, "t": "Draw a chart", "l": ["area:chart"]}]},
                           {"name": "Todo", "n": 1, "items": [
                               {"n": 42, "t": "Land the shell", "l": ["area:phone"]}]}]}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_routes", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_tab_bar_names_the_five_tabs_in_order(hub):
    assert re.findall(r'data-tab="(\w+)"', hub.MOBILE_PAGE) == TABS
    for tab in TABS:
        assert re.search(rf'<button data-tab="{tab}"[^>]*>{TAB_NAME[tab]}</button>', hub.MOBILE_PAGE), tab


def test_the_page_keeps_no_chat_more_or_settings_screen(hub):
    """The chat, More and Settings screens left the page (SPEC 2498): the tab bar and the page draw none of them."""
    page = hub.MOBILE_PAGE
    assert not re.search(r'data-panel="more"', page), "the page still draws a More panel"
    assert not re.search(r'id="now-more"', page), "the Now screen still opens More"
    assert not re.search(r'id="more-settings"', page), "the page still carries a Settings entry"
    assert not re.search(r'data-panel="terminals"', page), "the page still draws a chat screen"


def test_the_spend_tab_draws_the_cost_screen(hub):
    out = run_page(hub.MOBILE_PAGE, """
        return { tab: tabActive, hash: location.hash, cost: !document.querySelector('[data-screen="cost"]').hidden,
                 panel: !document.querySelector('[data-panel="spend"]').hidden };
    """, hash="#/spend", board=[ROUTE_BOARD])
    assert out == {"tab": "spend", "hash": "#/spend", "cost": True, "panel": True}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("hash_,tab,address", [
    ("#/now/usage", "spend", "#/spend"),                       # the cost screen's old address is the Spend tab
    ("#/runs/tokens", "spend", "#/spend"),                     # the Runs Usage view's address
    ("#/chat/session=s1?name=Session%20one", "now", "#/now"),  # the chat screen left (#2490)
    ("#/now/more", "now", "#/now"),                            # More left the page
    ("#/work/settings", "now", "#/now"),                       # Settings left the phone
    ("#/now/session=s2", "now", "#/now"),                      # a saved session address
    ("#/work/card=7", "now", "#/now"),                         # the card sheet's saved address: a tap on Work, not a route
], ids=["usage", "tokens", "chat", "more", "settings", "session", "card"])
def test_each_dropped_address_opens_its_screen_and_replaces_the_entry(hub, hash_, tab, address):
    out = run_page(hub.MOBILE_PAGE, """
        return { tab: tabActive, hash: location.hash, entries: history.entries.length, index: history.index };
    """, hash=hash_, board=[ROUTE_BOARD])
    assert out == {"tab": tab, "hash": address, "entries": 1, "index": 0}, out


@pytest.mark.node_bound
def test_a_saved_issue_link_opens_work_at_the_card(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        const opened = { tab: tabActive, hash: location.hash, open: !$("sheet").hidden,
                         under: history.entries[history.index - 1].url };
        $("sh-backtop").onclick(); __popAll();
        return { opened, back: { hash: location.hash, tab: tabActive, hidden: $("sheet").hidden } };
    """, hash="#issue=7", board=[ROUTE_BOARD])
    assert out["opened"] == {"tab": "work", "hash": "#/work/card=7", "open": True, "under": "#/work"}, out
    assert out["back"] == {"hash": "#/work", "tab": "work", "hidden": True}, out


@pytest.mark.node_bound
def test_a_tab_tap_replaces_the_entry_so_back_never_walks_through_tabs(hub):
    out = run_page(hub.MOBILE_PAGE, """
        const tap = t => document.querySelector(`nav [data-tab="${t}"]`).onclick();
        const base = history.entries.length;
        tap("work"); tap("specs"); tap("runs");
        const walked = { grew: history.entries.length - base, hash: location.hash, index: history.index };
        history.back(); __popAll();
        return { walked, after: { hash: location.hash, tab: tabActive, moved: history.index } };
    """, board=[ROUTE_BOARD])
    assert out["walked"] == {"grew": 0, "hash": "#/runs", "index": 0}, out
    assert out["after"]["moved"] == 0, out["after"]   # Back never lands on a tab the owner already left


@pytest.mark.node_bound
@pytest.mark.parametrize("hash_,layer,label,parent", [
    ("#/now/loops", "loops", "Now", "#/now"),
    ("#/now/loop=agent-squad", "loop", "Loops", "#/now/loops"),
    ("#/runs/run=r1?url=%2Frun%2Fr1&name=Run%20one", "run", "Runs", "#/runs"),
], ids=["loops", "loop", "run"])
def test_a_direct_entry_on_a_detail_route_backs_to_its_parent(hub, hash_, layer, label, parent):
    """A detail entered straight from the address opens with one Back that names its parent, and that Back opens the parent
    even when no entry stands under it: the address becomes the parent, and the page never leaves on a Back. A parent that
    is itself a detail opens over a direct entry of its own, so the chain walks back to the root one Back at a time."""
    out = run_page(hub.MOBILE_PAGE, f"""
        const top = document.querySelector('[data-topback="{layer}"]');
        const shown = {{ visible: !top.hidden, said: top.textContent }};
        top.onclick(); __popAll();
        const first = {{ hash: location.hash, index: history.index, layers: layers.slice() }};
        for (const b of [...document.querySelectorAll("[data-topback]")].filter(x => !x.hidden)) {{ b.onclick(); __popAll(); }}
        return {{ shown, first, root: {{ hash: location.hash, tab: tabActive, index: history.index, layers: layers.slice() }} }};
    """, hash=hash_, board=[ROUTE_BOARD])
    assert out["shown"] == {"visible": True, "said": label}, out
    assert out["first"]["hash"] == parent, out
    assert out["first"]["index"] == 0, out
    tab = parent.split("/")[1]
    assert out["root"] == {"hash": f"#/{tab}", "tab": tab, "index": 0, "layers": []}, out


@pytest.mark.node_bound
def test_the_search_pushes_no_entry_and_back_closes_it_first(hub):
    out = run_page(hub.MOBILE_PAGE, """
        const tap = t => document.querySelector(`nav [data-tab="${t}"]`).onclick();
        tap("work");
        const base = history.entries.length;
        $("work-search").onclick();
        $("search-input").value = "phone"; $("search-input").oninput();
        const opened = { grew: history.entries.length - base, bar: !$("searchbar").hidden };
        popScreen();     // the screen's Back: the search closes first, and the screen stays
        const back = { tab: tabActive, bar: $("searchbar").hidden, hash: location.hash };
        $("work-search").onclick(); $("search-input").value = "phone"; $("search-input").oninput();
        $("search-input").onkeydown({ key: "Escape" });
        return { opened, back, escaped: $("searchbar").hidden };
    """, board=[ROUTE_BOARD])
    assert out["opened"] == {"grew": 0, "bar": True}, out
    assert out["back"] == {"tab": "work", "bar": True, "hash": "#/work"}, out
    assert out["escaped"] is True, out


@pytest.mark.node_bound
def test_each_screen_restores_its_scroll_column_and_search_text(hub):
    out = run_page(hub.MOBILE_PAGE, """
        const tap = t => document.querySelector(`nav [data-tab="${t}"]`).onclick();
        await loadBoard();
        tap("work");
        window.scrollTo(0, 120);
        $("col-next").onclick();
        $("work-search").onclick();
        $("search-input").value = "phone"; $("search-input").oninput();
        const left = { col: view.col, query: searchState.query };
        tap("specs"); await __tick(10);
        tap("work"); await __tick(10);
        return { left, back: { y: window.scrollY, col: view.col, bar: $("searchbar").hidden,
                               text: $("search-input").value,
                               rows: [...$("work-results").children].map(b => b.dataset.n) } };
    """, board=[ROUTE_BOARD])
    assert out["left"] == {"col": 1, "query": "phone"}, out
    assert out["back"]["col"] == 1 and out["back"]["y"] == 120, out
    assert out["back"]["bar"] is False and out["back"]["text"] == "phone", out
    assert out["back"]["rows"] == ["7", "42"], out


def test_one_table_names_every_tab_and_its_root(hub):
    """The page owns one route table: each tab's root panel, and each screen's tab and parent, come from one place."""
    page = hub.MOBILE_PAGE
    assert "const ROUTE = {" in page, "the page has no route table"
    route = page[page.index("const ROUTE = {"):]
    route = route[:route.index("\n};") + 3]
    for tab in TABS:
        assert re.search(rf"\b{tab}:\s*\{{\s*panel:", route), f"the route table has no row for {tab}"
