"""ADR-0124: the page chrome. The side panel starts hidden, a bare page opens in Edit mode, a node press opens the panel and a second press closes it."""
import json

from tests.dashboard_helpers import PAGE_SRC
from tests.helpers import DESIGN
from tests.test_dashboard_page import drive, needs_chrome


def test_every_control_that_changes_the_graph_is_marked_edit_only():
    src = PAGE_SRC()
    for ident in ("b-save", "b-export", "b-save-layout", "b-layout", "b-review", "addbar"):
        tag = next(t for t in src.split("<") if f'id="{ident}"' in t)
        assert "edit-only" in tag, f"{ident} must be hidden outside Edit mode"


def test_the_left_panel_holds_no_tool_buttons():
    src = PAGE_SRC()
    left = src[src.index('<div id="left">'):src.index('<div id="graph">')]
    assert "<button" not in left and 'class="tools"' not in left, "tools live in the top bar and on the canvas"


@needs_chrome
def test_the_panel_starts_hidden_a_bare_page_is_in_edit_mode_and_a_node_press_toggles_the_panel(tmp_path):
    text = (DESIGN / "five-phases.graph.json").read_text()
    steps = (f'put("f-graph",{json.dumps(text)},"five-phases.graph.json");await wait();'
             'const b=document.body, start={noside:b.classList.contains("noside"),edit:b.classList.contains("edit")};'
             'const g=[...document.querySelectorAll(".card[data-name]")][0];'
             'const press=()=>{g.dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));window.dispatchEvent(new PointerEvent("pointerup"));};'
             'press();await wait();const open1=!b.classList.contains("noside");'
             'await new Promise(r=>setTimeout(r,500));'
             'press();await wait();const open2=!b.classList.contains("noside");'
             'document.title="RESULT "+JSON.stringify({start,open1,open2});')
    got = drive(tmp_path, steps)
    assert got["start"] == {"noside": True, "edit": True}
    assert got["open1"] is True and got["open2"] is False
