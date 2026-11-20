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
    "swipe down": ('__dispatch("touchstart", { target: $("sheet-content").querySelector(".grab"), touches: [{ clientX: 200, clientY: 600 }] });'
                   '__dispatch("touchend", { changedTouches: [{ clientX: 200, clientY: 720 }] });'),
    "board update": f'__state.board = [{EMPTY_BOARD}]; await loadBoard(true);',
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
    assert out == {"pushed": 1, "state": {"tab": "work", "layer": "sheet", "depth": 1, "n": 7},
                   "layers": ["sheet"]}, out


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
def test_a_popstate_onto_the_page_entry_closes_every_layer_and_pops_nothing_extra(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        openCard(7);
        history.replaceState({}, "");
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
    """, history_state={"layer": "psheet", "depth": 1}, history_below=True, board=[BOARD_ONE])
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
    """, history_state={"layer": "sheet", "depth": 1, "n": 7}, history_below=True, board=[BOARD_ONE])
    assert out["restored"] == {"hidden": False, "layers": ["sheet"], "n": 7, "index": 1}, out
    assert out["after"] == {"hidden": True, "layers": [], "index": 0} and out["pending"] == 0, out


MORE_TAP = 'const moreTap = d => document.querySelector(\'[data-panel="more"]\').onclick({ target: { closest: () => ({ dataset: d }) } });'

LAYER_OPEN = {
    "run": 'openRun("r1", "/run/r1", "Run one");',
    "cost": 'moreTap({ more: "cost" });',
    "lead": 'moreTap({ more: "lead" });',
}

LAYER_BACK = {
    "run": '$("runview-back").onclick();',
    "cost": 'moreTap({ back: "" });',
    "lead": 'moreTap({ back: "" });',
}

# A screen opens inside the tab whose list it came from (SPEC 1596): the owner stands on that tab before the open.
LAYER_TAB_OPEN = {"run": 'show("runs");', "cost": "", "lead": ""}

LAYER_HIDDEN = {
    "run": '$("runview").hidden',
    "cost": 'document.querySelector(\'[data-screen="cost"]\').hidden',
    "lead": 'document.querySelector(\'[data-screen="lead"]\').hidden',
}

RESTORE_STATE = {
    "run": {"tab": "runs", "layer": "run", "depth": 1, "id": "r1", "url": "/run/r1", "name": "Run one"},
    "cost": {"tab": "now", "layer": "cost", "depth": 1},
    "lead": {"tab": "now", "layer": "lead", "depth": 1},
}


@pytest.mark.node_bound
@pytest.mark.parametrize("layer", list(LAYER_OPEN))
def test_each_layer_pushes_one_entry_on_open_and_pops_it_on_close(hub, layer):
    out = run_page(hub.MOBILE_PAGE, f"""
        {MORE_TAP}
        await loadBoard();
        {LAYER_TAB_OPEN[layer]}
        const base = history.index;
        {LAYER_OPEN[layer]}
        const opened = [history.index - base, layers.slice(), {LAYER_HIDDEN[layer]}];
        {LAYER_BACK[layer]}
        __popAll();
        return {{ opened, after: [history.index - base, layers.slice(), {LAYER_HIDDEN[layer]}], pending: __pending() }};
    """, board=[BOARD_ONE])
    assert out == {"opened": [1, [layer], False], "after": [0, [], True], "pending": 0}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("layer", list(LAYER_OPEN))
def test_forward_reopens_the_layer_its_entry_names(hub, layer):
    out = run_page(hub.MOBILE_PAGE, f"""
        {MORE_TAP}
        await loadBoard();
        {LAYER_TAB_OPEN[layer]}
        const base = history.index;
        {LAYER_OPEN[layer]}
        {LAYER_BACK[layer]}
        __popAll();
        const closed = [history.index - base, layers.slice(), {LAYER_HIDDEN[layer]}];
        history.forward();
        __popAll();
        return {{ closed, after: [history.index - base, layers.slice(), {LAYER_HIDDEN[layer]}], pending: __pending() }};
    """, board=[BOARD_ONE])
    assert out == {"closed": [0, [], True], "after": [1, [layer], False], "pending": 0}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("layer", list(RESTORE_STATE))
def test_a_reload_with_a_layer_open_restores_it_without_a_second_push(hub, layer):
    out = run_page(hub.MOBILE_PAGE, f"""
        {MORE_TAP}
        await loadBoard();
        const restored = [history.index, history.entries.length, layers.slice(), {LAYER_HIDDEN[layer]}, history.state.layer, __pending()];
        {LAYER_BACK[layer]}
        __popAll();
        return {{ restored, after: [history.index, layers.slice(), {LAYER_HIDDEN[layer]}, history.state.layer || null] }};
    """, history_state=RESTORE_STATE[layer], history_below=True, board=[BOARD_ONE])
    # Usage and lead time stand on More (SPEC 1596): the chain above them is restored with them, each on its own entry, so
    # closing the top leaves both the screen under it and the entry that names it: the next Back is the browser's own.
    nested = layer in ("cost", "lead")
    assert out == {"restored": [2, 3, ["more", layer], False, layer, 0] if nested
                             else [1, 2, [layer], False, layer, 0],
                   "after": [1, ["more"], True, "more"] if nested
                            else [0, [], True, None]}, out


OWNER_TAB = {"run": "runs", "cost": "now", "lead": "now"}


@pytest.mark.node_bound
def test_a_cold_load_of_a_run_hash_restores_its_address_and_name(hub):
    """The address carries the whole screen (SPEC 1596, rule 2): a run reloaded from its hash draws its own title and page."""
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        return { tab: tabActive, hidden: $("runview").hidden, src: $("runframe").src, name: $("runview-name").textContent };
    """, hash="#/runs/run=r1?url=%2Frun%2Fr1&name=Run%20one", board=[BOARD_ONE])
    assert out == {"tab": "runs", "hidden": False, "src": "/run/r1", "name": "Run one"}, out


@pytest.mark.node_bound
def test_a_cold_load_of_a_removed_chat_hash_lands_on_a_tab_the_page_has(hub):
    """The chat address left the page (#2490): a reload of it lands on Now and opens no session screen."""
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        return { tab: tabActive, term: !!$("termview"), chat: !!document.querySelector('[data-panel="terminals"]') };
    """, hash="#/chat/session=s1?name=Session%20one", board=[BOARD_ONE])
    assert out == {"tab": "now", "term": False, "chat": False}, out


@pytest.mark.node_bound
@pytest.mark.parametrize("layer", list(LAYER_OPEN))
def test_a_tab_change_keeps_the_layer_and_its_entry(hub, layer):
    """A tab left keeps its screens (SPEC 1596, rule 2): the tab that owns the screen returns to it, and one pop closes it."""
    owner = OWNER_TAB[layer]
    out = run_page(hub.MOBILE_PAGE, f"""
        {MORE_TAP}
        await loadBoard();
        {LAYER_TAB_OPEN[layer]}
        const base = history.index;
        {LAYER_OPEN[layer]}
        show("specs");
        const away = [tabActive, layers.slice(), {LAYER_HIDDEN[layer]}];
        show("{owner}"); await __tick(20);
        const back = [tabActive, layers.slice(), {LAYER_HIDDEN[layer]}, history.index - base];
        {LAYER_BACK[layer]}
        __popAll();
        return {{ away, back, after: [history.index - base, layers.slice()], pending: __pending() }};
    """, board=[BOARD_ONE])
    assert out["away"] == ["specs", [], True], out
    assert out["back"] == [owner, [layer], False, 1], out
    assert out["after"] == [0, []] and out["pending"] == 0, out


@pytest.mark.node_bound
def test_nested_layers_close_top_first(hub):
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        const closed = [];
        for (const name of ["sheet", "psheet"]) {
          const hide = LAYER[name].hide;
          LAYER[name].hide = () => { closed.push(name); hide(); };
        }
        openCard(7);
        $("profile").onclick();
        $("sh-close").onclick();
        __popAll();
        return { closed, layers: layers.slice(), pending: __pending() };
    """, board=[BOARD_ONE])
    assert out == {"closed": ["psheet", "sheet"], "layers": [], "pending": 0}, out


def test_the_layers_open_and_close_by_call_never_by_a_watch_on_hidden(hub):
    page = hub.MOBILE_PAGE
    block = page[page.index("const LAYER = {"):page.index("const tabs = ")]
    assert "function openLayer(" in page and "function closeLayer(" in page, "the layers have a semantic open and close call"
    assert "MutationObserver" not in block, "a layer never opens or closes on a watch of its hidden attribute"


def test_each_full_screen_layer_keeps_a_bottom_close_on_the_one_close_function(hub):
    page = hub.MOBILE_PAGE
    dots = '<svg class="ico" viewBox="0 0 12 12" aria-hidden="true" focusable="false">' \
           '<path d="M2 6h.01M6 6h.01M10 6h.01"/></svg>'
    assert '<div id="sheet-actions"><button id="sh-backact" data-actback type="button">Back</button>' \
           f'<button id="sh-dots" type="button" aria-label="More actions" aria-haspopup="true">{dots}</button>' \
           '<button id="sh-close" type="button">Close</button>' in page, \
        "the card sheet keeps its action row: Back, the dots menu, then the secondary"
    assert '<div id="psw-footer"><button id="ps-backact" data-actback type="button">Back</button>' \
           '<button id="pswx" type="button">Close</button></div>' in page, "the project sheet keeps its bottom Back and Close"
