"""The phone's navigation shell (SPEC 1596, #1597): four tabs on every screen, one stack per tab, two Backs, and the
location mirrored in the URL hash.

The page is booted under the stub DOM of test_hub_mobile_move, which keeps the address of every history entry, so a route
is read from the hash the owner would see in the address bar. No server runs and no rule lives in the test.
"""
import importlib.util
import os
import pathlib
import re
import tempfile

import pytest

from tests.test_hub_mobile_move import BOARD_ONE, route_tabs, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_nav", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TAP = 'const tap = t => document.querySelector(`nav [data-tab="${t}"]`).onclick();'


def test_the_bar_names_four_tabs_in_order(hub):
    tabs = route_tabs(hub.MOBILE_PAGE)
    assert re.findall(r'data-tab="(\w+)"', hub.MOBILE_PAGE) == [tab for tab, _ in tabs]
    for tab, label in tabs:
        assert re.search(rf'<button data-tab="{tab}"[^>]*>{label}</button>', hub.MOBILE_PAGE), tab


def test_the_bar_is_drawn_above_every_screen(hub):
    """A detail covers the page, never the bar: the bar paints over every full-screen screen and every sheet."""
    page = hub.MOBILE_PAGE
    bar = int(re.search(r"\nnav\{[^}]*z-index:(\d+)", page).group(1))
    screens = [int(z) for z in re.findall(r"(?:#[a-z]+|\[data-screen\])\{[^}]*z-index:(\d+)", page)]
    assert screens, "the screens name no z-index, so the bar's place above them is unchecked"
    assert bar > max(screens), f"the tab bar ({bar}) paints under a screen ({max(screens)})"


@pytest.mark.node_bound
def test_every_root_draws_the_bar_and_names_its_tab(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        const seen = {{}};
        for (const t of {[tab for tab, _ in route_tabs(hub.MOBILE_PAGE)]!r}) {{
          tap(t);
          const cur = document.querySelector("nav [aria-current=page]");
          seen[t] = {{ nav: !document.querySelector("nav").hidden, current: cur && cur.dataset.tab, hash: location.hash }};
        }}
        return seen;
    """, board=[BOARD_ONE])
    for tab, _ in route_tabs(hub.MOBILE_PAGE):
        assert out[tab]["nav"] is True, out
        assert out[tab]["current"] == tab, out


@pytest.mark.node_bound
def test_a_detail_keeps_the_bar_and_its_tab(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        const cur = () => (document.querySelector("nav [aria-current=page]") || {{}}).dataset;
        tap("work"); openCard(7);
        const card = {{ nav: !document.querySelector("nav").hidden, tab: cur().tab, open: !$("sheet").hidden }};
        tap("now"); openLayer("lead");
        const lead = {{ nav: !document.querySelector("nav").hidden, tab: cur().tab,
                        open: !document.querySelector('[data-screen="lead"]').hidden }};
        return {{ card, lead }};
    """, board=[BOARD_ONE])
    assert out["card"] == {"nav": True, "tab": "work", "open": True}, out
    assert out["lead"] == {"nav": True, "tab": "now", "open": True}, out


@pytest.mark.node_bound
def test_a_tab_keeps_the_card_it_was_on(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        tap("work"); openCard(7);
        const before = {{ tab: tabActive, open: !$("sheet").hidden, n: shown[view.card].n }};
        tap("runs"); await __tick(10);
        const away = {{ tab: tabActive, open: !$("sheet").hidden, hash: location.hash }};
        tap("work"); await __tick(20);
        const back = {{ tab: tabActive, open: !$("sheet").hidden, n: shown[view.card].n, hash: location.hash }};
        return {{ before, away, back }};
    """, board=[BOARD_ONE])
    assert out["before"] == {"tab": "work", "open": True, "n": 7}, out
    assert out["away"] == {"tab": "runs", "open": False, "hash": "#/runs"}, out
    assert out["back"] == {"tab": "work", "open": True, "n": 7, "hash": "#/work/card=7"}, out


@pytest.mark.node_bound
def test_the_address_mirrors_the_screen_and_a_reload_lands_on_it(hub):
    """Every screen names its tab in the hash; the first screen has no layer segment, a detail names its own."""
    opened = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        tap("work"); openCard(7);
        const card = location.hash;
        $("sh-backtop").onclick(); __popAll();
        const root = location.hash;
        tap("specs");
        const specs = location.hash;
        return {{ card, root, specs }};
    """, board=[BOARD_ONE])
    assert opened == {"card": "#/work/card=7", "root": "#/work", "specs": "#/specs"}, opened
    reloaded = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        return { tab: tabActive, open: !$("sheet").hidden, n: (shown[view.card] || {}).n, hash: location.hash };
    """, board=[BOARD_ONE], hash="#/work/card=7", history_state={"tab": "work", "layer": "sheet", "depth": 1, "n": 7}, history_below=True)
    assert reloaded == {"tab": "work", "open": True, "n": 7, "hash": "#/work/card=7"}, reloaded
    # A saved address with no entry under it (a bookmark, a pasted address) opens the same card sheet on Work (#2499).
    saved = run_page(hub.MOBILE_PAGE, """
        await __tick(20);
        return { tab: tabActive, open: !$("sheet").hidden, n: (shown[view.card] || {}).n, hash: location.hash };
    """, board=[BOARD_ONE], hash="#/work/card=7")
    assert saved == {"tab": "work", "open": True, "n": 7, "hash": "#/work/card=7"}, saved


@pytest.mark.node_bound
def test_the_top_back_names_the_screen_below_it(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        tap("work"); openCard(7);
        const card = {{ open: !$("sheet").hidden, said: document.querySelector('[data-topback="sheet"]').textContent }};
        tap("now"); openLayer("lead");
        const lead = {{ open: !document.querySelector('[data-screen="lead"]').hidden,
                        said: document.querySelector('[data-topback="lead"]').textContent }};
        return {{ card, lead }};
    """, board=[BOARD_ONE])
    assert out["card"] == {"open": True, "said": "Work"}, out
    assert out["lead"] == {"open": True, "said": "Now"}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("back", ['$("sh-backtop").onclick();', '$("sh-backact").onclick();'])
def test_both_backs_call_one_pop(hub, back):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        tap("work");
        const base = history.index;
        openCard(7);
        const opened = [history.index - base, layers.slice(), $("sheet").hidden];
        {back}
        __popAll();
        return {{ opened, after: [history.index - base, layers.slice(), $("sheet").hidden], pending: __pending() }};
    """, board=[BOARD_ONE])
    assert out == {"opened": [1, ["sheet"], False], "after": [0, [], True], "pending": 0}, out


@pytest.mark.node_bound
def test_the_browser_back_matches_the_two_backs(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        tap("work");
        openCard(7);
        const base = history.index;
        history.back();
        __popAll();
        return {{ moved: history.index - base, layers: layers.slice(), hidden: $("sheet").hidden, hash: location.hash }};
    """, board=[BOARD_ONE])
    assert out["moved"] == -1 and out["layers"] == [] and out["hidden"] is True, out
    assert out["hash"] == "#/work", "the browser's Back leaves the address on the root the card stood over"


def test_the_first_button_of_an_action_row_is_a_back(hub):
    page = hub.MOBILE_PAGE
    # Every action row puts its Back where the thumb is (SPEC 1596, rule 2; #1605).
    for row in ("sheet-actions", "psw-footer", "runview-actions", "lead-actions"):
        m = re.search(rf'<div id="{row}">(.*?)</div>', page, re.S)
        assert m, row
        assert re.search(r'^\s*<button[^>]*(?:data-actback|data-back|id="runview-back")', m.group(1)), \
            f"the first button of {row} is no Back: {m.group(1)[:80]}"


@pytest.mark.node_bound
def test_a_tap_on_the_current_tab_returns_to_its_root(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        await loadBoard();
        tap("work");
        openCard(7);
        tap("work");
        __popAll();
        return {{ tab: tabActive, open: !$("sheet").hidden, layers: layers.slice(), hash: location.hash }};
    """, board=[BOARD_ONE])
    assert out == {"tab": "work", "open": False, "layers": [], "hash": "#/work"}, out


@pytest.mark.node_bound
def test_a_second_tap_on_the_current_tab_reaches_the_top(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        {TAP}
        tap("runs");
        window.scrollTo(0, 300);
        const deep = window.scrollY;
        tap("runs");
        return {{ deep, top: window.scrollY }};
    """, board=[BOARD_ONE])
    assert out == {"deep": 300, "top": 0}, out


@pytest.mark.node_bound
def test_the_bar_hides_while_a_text_field_has_focus_and_returns_on_blur(hub):
    out = run_page(hub.MOBILE_PAGE, """
        const bar = document.querySelector("nav"), text = $("search-input");
        __dispatch("focusin", { target: text }); const typing = bar.hidden;
        __dispatch("focusout", { target: text }); const blurred = bar.hidden;
        __dispatch("focusin", { target: $("retry") }); const button = bar.hidden;
        return { typing, blurred, button };
    """, board=[BOARD_ONE])
    assert out == {"typing": True, "blurred": False, "button": False}, out


@pytest.mark.node_bound
def test_a_root_draws_no_back(hub):
    out = run_page(hub.MOBILE_PAGE, """
        return { layers: layers.slice(),
                 backs: [...document.querySelectorAll("[data-topback]")].filter(b => !b.hidden).length,
                 rootBack: !!document.querySelector("[data-actback]") };
    """, board=[BOARD_ONE])
    assert out["layers"] == [] and out["backs"] == 0, out
