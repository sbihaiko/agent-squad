"""The phone's full-screen layers keep one history entry each (#1450).

The card sheet and the project sheet each own one entry: opening pushes it, closing pops it, and Back closes the top layer
without leaving the page. Every close path runs the one close function, and the page is booted under the stub DOM of
test_hub_mobile_move, which counts the entries the page pushes and pops.
"""
import importlib.util
import os
import pathlib
import tempfile

import pytest

from tests.test_hub_mobile_move import BOARD_ONE, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

EMPTY_BOARD = ('{ t: 2, err: null, labels: [], lc: {}, cases: [], flow: null, '
               'groups: [{ name: "Backlog", n: 0, items: [] }, { name: "Todo", n: 0, items: [] }] }')

CARD_CLOSES = {
    "close button": '$("sh-close").onclick();',
    "escape": '__dispatch("keydown", { key: "Escape" });',
    "swipe down": ('__dispatch("touchstart", { target: $("sheet"), touches: [{ clientX: 200, clientY: 600 }] });'
                   '__dispatch("touchend", { changedTouches: [{ clientX: 200, clientY: 720 }] });'),
    "tab change": 'show("runs");',
    "board update": f'__state.board = [{EMPTY_BOARD}]; await loadBoard();',
}

PROJECT_CLOSES = {
    "close button": '$("pswx").onclick();',
    "escape": '__dispatch("keydown", { key: "Escape" });',
    "project switch": '$("pswl").onclick({ target: { closest: () => ({}) } });',
}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_layers", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.node_bound
def test_opening_the_card_sheet_pushes_one_entry(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        const base = history.index;
        openCard(7);
        openCard(7);
        return { pushed: history.index - base, state: history.state, layers: layers.slice() };
    """, board=[BOARD_ONE])
    assert out == {"pushed": 1, "state": {"layer": "sheet", "n": 7}, "layers": ["sheet"]}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("close", list(CARD_CLOSES.values()), ids=list(CARD_CLOSES))
def test_every_card_sheet_close_path_pops_its_one_entry(hub, close):
    out = run_page(hub.MOBILE_PAGE, f"""
        await loadBoard();
        const base = history.index;
        openCard(7);
        const opened = [history.index - base, layers.slice()];
        {close}
        __popAll();
        return {{ opened, after: history.index - base, layers: layers.slice(), hidden: $("sheet").hidden, pending: __pending() }};
    """, board=[BOARD_ONE])
    assert out == {"opened": [1, ["sheet"]], "after": 0, "layers": [], "hidden": True, "pending": 0}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("close", list(PROJECT_CLOSES.values()), ids=list(PROJECT_CLOSES))
def test_every_project_sheet_close_path_pops_its_one_entry(hub, close):
    out = run_page(hub.MOBILE_PAGE, f"""
        await loadBoard();
        const base = history.index;
        $("profile").onclick();
        const opened = [history.index - base, layers.slice()];
        {close}
        __popAll();
        return {{ opened, after: history.index - base, layers: layers.slice(), hidden: $("psheet").hidden, pending: __pending() }};
    """, board=[BOARD_ONE])
    assert out == {"opened": [1, ["psheet"]], "after": 0, "layers": [], "hidden": True, "pending": 0}, out


@pytest.mark.node_bound
def test_a_synthetic_popstate_closes_the_top_layer_and_pops_nothing_extra(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        openCard(7);
        const before = history.index;
        __dispatch("popstate", {});
        return { hidden: $("sheet").hidden, layers: layers.slice(), moved: history.index - before, pending: __pending() };
    """, board=[BOARD_ONE])
    assert out == {"hidden": True, "layers": [], "moved": 0, "pending": 0}, out


@pytest.mark.node_bound
def test_back_closes_the_top_layer_and_never_leaves_the_page(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        openCard(7);
        const before = history.index;
        history.back();
        __popAll();
        return { hidden: $("sheet").hidden, layers: layers.slice(), moved: history.index - before, pending: __pending() };
    """, board=[BOARD_ONE])
    assert out == {"hidden": True, "layers": [], "moved": -1, "pending": 0}, out


@pytest.mark.node_bound
def test_closing_a_layer_also_closes_the_layers_above_it(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        const base = history.index;
        openCard(7);
        $("profile").onclick();
        const opened = [history.index - base, layers.slice()];
        $("sh-close").onclick();
        __popAll();
        return { opened, after: history.index - base, layers: layers.slice(), pending: __pending() };
    """, board=[BOARD_ONE])
    assert out == {"opened": [2, ["sheet", "psheet"]], "after": 0, "layers": [], "pending": 0}, out


@pytest.mark.node_bound
def test_back_from_a_project_sheet_restored_open_on_load_closes_it_before_navigating(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        const restored = { hidden: $("psheet").hidden, layers: layers.slice(), index: history.index };
        history.back();
        __popAll();
        return { restored, after: { hidden: $("psheet").hidden, layers: layers.slice(), index: history.index }, pending: __pending() };
    """, history_state={"layer": "psheet"}, board=[BOARD_ONE])
    assert out["restored"] == {"hidden": False, "layers": ["psheet"], "index": 1}, out
    assert out["after"] == {"hidden": True, "layers": [], "index": 0} and out["pending"] == 0, out


@pytest.mark.node_bound
def test_back_from_a_card_sheet_restored_open_on_load_closes_it_before_navigating(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        const restored = { hidden: $("sheet").hidden, layers: layers.slice(), n: shown[view.card].n, index: history.index };
        history.back();
        __popAll();
        return { restored, after: { hidden: $("sheet").hidden, layers: layers.slice(), index: history.index }, pending: __pending() };
    """, history_state={"layer": "sheet", "n": 7}, board=[BOARD_ONE])
    assert out["restored"] == {"hidden": False, "layers": ["sheet"], "n": 7, "index": 1}, out
    assert out["after"] == {"hidden": True, "layers": [], "index": 0} and out["pending"] == 0, out


def test_the_layers_open_and_close_by_call_never_by_a_watch_on_hidden(hub):
    page = hub.MOBILE_PAGE
    block = page[page.index("const LAYER = {"):page.index("const tabs = ")]
    assert "function openLayer(" in page and "function closeLayer(" in page, "the layers have a semantic open and close call"
    assert "MutationObserver" not in block, "a layer never opens or closes on a watch of its hidden attribute"


def test_each_full_screen_layer_keeps_a_bottom_close_on_the_one_close_function(hub):
    page = hub.MOBILE_PAGE
    assert '<div id="sheet-actions"><button id="sh-close" type="button">Close</button>' in page, "the card sheet keeps its bottom Close"
    assert '<div id="psw-footer"><button id="pswx" type="button">Close</button></div>' in page, "the project sheet keeps its bottom Close"
