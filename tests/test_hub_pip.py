"""Hub pip (#1782): with the board maximized the run pane is out of sight, so a small floating window over the board
shows ONE card of the run panel's dashboard. These pin the parts that carry a rule — it belongs to the maximize mode,
one frame per selected run (never re-sourced by a collapse, a drag or a resize), the clamp that keeps it on the board,
the header's pointer-capture drag, the wheel that never reaches the board, and the messages shown when there is no run
or when the dashboard has no single-card mode."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
PIP_FROM = "// ---- the pip over the maximized board (#1782)"
PIP_TO = "// ---- the pip ends here"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_pip", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _css(hub, selector):
    m = re.search(r"(?:^|[\n}])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", hub.PAGE)
    assert m, f"no CSS rule for {selector}"
    return m.group(1)


def _board(hub):
    return hub.PAGE[hub.PAGE.index("<div id=board>"):hub.PAGE.index("<div id=foot")]


def _pip_slice(hub):
    return hub.PAGE[hub.PAGE.index(PIP_FROM):hub.PAGE.index(PIP_TO)]


def _live_rules(hub):
    """The page's own rule for which surface holds the one live frame: the harness runs it, never a copy of it."""
    return hub.PAGE[hub.PAGE.index("let focus="):hub.PAGE.index("const snapText=")]


# ---------------------------------------------------------------- it is the board's own floating window

def test_the_window_is_a_floating_card_of_the_board(hub):
    css = _css(hub, "#pip")
    assert "position:absolute" in css and "z-index:10" in css          # measured against the board, never the viewport
    assert "position:relative" in _css(hub, "#board")                   # the board is what `absolute` is measured against


def test_the_window_sits_above_the_headers_and_below_the_filter_popover(hub):
    assert "z-index:10" in _css(hub, "#pip")                            # above the board header (2) and the column headers
    assert "z-index:20" in _css(hub, "#lfl")                            # below the filter popover: it can never be trapped behind the window
    assert not re.search(r"#pip[^\n{]*\{[^}]*z-index:(19|[2-9]\d)", hub.PAGE)


def test_it_belongs_to_the_maximize_mode_so_nobody_closes_it_twice(hub):
    css = _css(hub, "body:not(.dockb.max) #pip")
    assert "visibility:hidden" in css and "display:none" not in css     # gone from the split, and its frame is never detached


def test_the_window_sits_between_the_header_and_the_columns_so_the_keyboard_reaches_it_in_that_order(hub):
    board = _board(hub)
    assert board.index("<div id=bhead>") < board.index('<div id="pip"') < board.index("<div id=bbody>")
    assert 'id="pipf"' in board                                         # the frame is the page's own element, next to the header it belongs to
    assert " src=" not in re.search(r"<iframe[^>]*>", hub.PAGE).group(0)   # no src in the markup: the frame is sourced once, by the script


def test_the_collapsed_window_is_a_header_bar_that_keeps_its_place(hub):
    css = _css(hub, "#pip.off #pipb")
    assert "height:0" in css and "visibility:hidden" in css             # the header alone; the frame stays mounted
    assert "position" not in css                                        # no snapping back


def test_the_window_never_swallows_the_board_it_floats_over(hub):
    assert "overscroll-behavior:contain" in _css(hub, "#pip")
    assert "touch-action:none" in _css(hub, "#piph")
    assert re.search(r'\$\("#pip[hb]?"\)\.addEventListener\("wheel"', _pip_slice(hub))
    assert "{passive:false}" in _pip_slice(hub)                         # a wheel over the chrome does nothing; over the card it is the card's


def test_both_window_controls_are_focusable_titled_buttons(hub):
    board = _board(hub)
    assert re.search(r'<button id=pipx title="[^"]+"', board)           # collapse / expand
    assert re.search(r'<button id=pipo title="[^"]+"', board)           # close
    assert re.search(r'<div id="piph" tabindex="0"', board)             # the header itself is a keyboard stop


# ---------------------------------------------------------------- the card the window holds

def test_the_card_is_the_dashboards_own_single_card_mode_not_a_second_rendering(hub):
    slice_ = _pip_slice(hub)
    assert "?card=" in slice_                                           # the URL names the card the dashboard must render
    assert "createElement" not in slice_                                # the frame is the page's own, built in the markup


def test_the_window_follows_the_run_the_panel_has_focused(hub):
    assert "focused()" in _pip_slice(hub)                               # the pane that follows its run is the run the window shows


def test_a_dashboard_without_the_mode_degrades_to_a_message_never_an_empty_frame(hub):
    assert re.search(r'addEventListener\("message"', _pip_slice(hub))   # the dashboard answers when it rendered the card
    assert "no single-card mode" in _pip_slice(hub)


def test_the_run_less_hub_shows_the_hubs_own_message_in_the_window(hub):
    assert "No open runs." in _pip_slice(hub)
    assert "PIP_NORUNS" in hub.PAGE[hub.PAGE.index("function drawMain"):hub.PAGE.index("function refresh")]   # the same words, one source


def test_the_frame_is_sourced_once_per_run_switch_and_never_by_a_repaint(hub):
    slice_ = _pip_slice(hub)
    assert slice_.count(".src=url") == 1                                # one place sets the URL
    assert slice_.count('.src="about:blank"') == 1                      # and one place lets it go
    assert re.search(r"if\(url!==pipSrc\)", slice_)                     # and only when the selected run changed
    assert "liveSync()" in hub.PAGE[hub.PAGE.index("function refresh"):hub.PAGE.index("function applyFilter")]   # the run switch repaints it
    assert "pipDraw()" in hub.PAGE[hub.PAGE.index("function liveSync"):hub.PAGE.index("// ---- the live panes end here")]   # through the one place that moves the live frame


# ---------------------------------------------------------------- the window's own geometry and gestures

_NODE_PRE = r"""
const __el={},__made=[],__handlers={},__timers=[],__focus=[],__srcLog=[];
let __srcWrites=0,__board={w:1200,h:800,head:34};
const __rect=id=>id==="#bhead"?{width:520,height:__board.head}:{width:__board.w,height:__board.h};
const __mk=id=>({id,innerHTML:"",textContent:"",title:"",hidden:false,style:{},attrs:{},classes:new Set(),dataset:{},
  classList:{add:c=>__el[id].classes.add(c),remove:c=>__el[id].classes.delete(c),
    toggle:(c,on)=>{const v=on===undefined?!__el[id].classes.has(c):!!on;v?__el[id].classes.add(c):__el[id].classes.delete(c);return v},
    contains:c=>__el[id].classes.has(c)},
  setAttribute(k,v){__el[id].attrs[k]=v},getAttribute(k){return __el[id].attrs[k]},
  addEventListener(t,f){__handlers[id+":"+t]=f},removeEventListener(t,f){delete __handlers[id+":"+t]},
  focus(){__focus.push(id)},setPointerCapture(){},getBoundingClientRect(){return __rect(id)}});
const $=s=>__el[s]||(__el[s]=__mk(s));
const __body=__mk("body");__el["body"]=__body;
globalThis.document={body:__body,createElement:t=>{__made.push(t);return __mk("new:"+__made.length)}};
globalThis.localStorage={hub:"{}"};
globalThis.addEventListener=(t,f)=>{__handlers["win:"+t]=f};
globalThis.setTimeout=(f,ms)=>{__timers.push({f,ms});return __timers.length};
globalThis.clearTimeout=()=>{};
const esc=t=>String(t).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const name=r=>r.name||("run "+String(r.id).slice(-6));
const bo={},saveB=()=>{};
let runs=[{id:"run-1",port:"9001",state:"running",name:"build #11"}],slots=["run-1"];
Object.defineProperty($("#pipf"),"src",{get:()=>__srcLog[__srcLog.length-1]||"",set:v=>{__srcLog.push(v);if(v&&v!=="about:blank")__srcWrites++}});
"""

_NODE_POST = r"""
const px=v=>Number(String(v||"").replace("px",""));
const snap=()=>({src:$("#pipf").src,writes:__srcWrites,frames:__made.filter(t=>t==="iframe").length,
  x:px($("#pip").style.left),y:px($("#pip").style.top),w:px($("#pip").style.width),h:px($("#pip").style.height),
  head:$("#pipt").textContent,msg:$("#pipm").textContent,off:__el["#pip"].classes.has("off"),
  noframe:__el["#pip"].classes.has("noframe"),saved:JSON.parse(JSON.stringify(bo.pip||{}))});
const steps={};
steps.init=snap();
bo.max=true;pipDraw();steps.maximized=snap();
$("#pipx").onclick();steps.collapsed=snap();
$("#pipx").onclick();steps.expanded=snap();
/* a drag: the pointer capture the board's own grip uses */
$("#piph").onpointerdown({preventDefault(){},currentTarget:$("#piph"),pointerId:7,clientX:500,clientY:700});
steps.dragBody=[...__body.classes];
__handlers["#piph:pointermove"]({buttons:1,clientX:-900,clientY:-900});
steps.dragOut=snap();
__handlers["#piph:pointermove"]({buttons:1,clientX:520,clientY:300});   /* well inside the board: the shift arrow below moves 64px and must not hit the clamp */
steps.dragIn=snap();
__handlers["#piph:pointerup"]({});
steps.dragEnd={body:[...__body.classes],saved:JSON.parse(JSON.stringify(bo.pip||{}))};
/* the keyboard: the focused header moves the window */
__handlers["#piph:keydown"]({key:"ArrowLeft",shiftKey:false,preventDefault(){},stopPropagation(){}});
steps.arrow16=snap();
__handlers["#piph:keydown"]({key:"ArrowDown",shiftKey:true,preventDefault(){},stopPropagation(){}});
steps.arrow64=snap();
__handlers["#piph:keydown"]({key:"Escape",shiftKey:false,preventDefault(){},stopPropagation(){}});
steps.escCollapsed=snap();
__handlers["#piph:keydown"]({key:"Escape",shiftKey:false,preventDefault(){},stopPropagation(){}});
steps.escFocus=[...__focus];
$("#pipx").onclick();   /* back to the full card: the resize steps below read its full height */
/* the left bar moves to another run: one new frame source, never a second frame */
runs=[...runs,{id:"run-2",port:"9002",state:"running",name:"fix PR #1202"}];slots=["run-2"];pipDraw();
steps.switched=snap();
pipDraw();
steps.repainted=snap();
/* a smaller window: the remembered place is clamped back onto the board */
__board={w:300,h:200,head:34};
__handlers["win:resize"]();
steps.small=snap();
__board={w:1200,h:800,head:34};
__handlers["win:resize"]();
steps.back=snap();
/* no run selected: the hub's own message, and no frame behind it */
runs=[];slots=[];pipDraw();steps.norun=snap();
/* a dashboard older than the mode: the frame never answers, and the window says so */
runs=[{id:"run-3",port:"9003",state:"running",name:"panel #1179"}];slots=["run-3"];pipDraw();
steps.waiting=snap();
__timers[__timers.length-1].f();
steps.unsupported=snap();
/* the dashboard answers: the frame is shown again */
__handlers["win:message"]({data:{squadCard:"groups"}});
steps.supported=snap();
console.log("RESULT"+JSON.stringify(steps));
"""


def _pip_states(hub):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    script = _NODE_PRE + _live_rules(hub) + _pip_slice(hub) + _NODE_POST
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.split("RESULT")[-1])


def test_the_default_place_is_the_bottom_right_inset_twelve(hub):
    st = _pip_states(hub)
    assert (st["maximized"]["x"], st["maximized"]["y"], st["maximized"]["w"], st["maximized"]["h"]) == (1200 - 360 - 12, 800 - 300 - 12, 360, 300)
    assert st["maximized"]["y"] >= 34                                   # the header's measured height, never a constant zero


def test_a_remembered_place_never_lands_off_the_board(hub):
    st = _pip_states(hub)
    assert st["dragOut"]["x"] == 0 and st["dragOut"]["y"] == 34         # dragged past the top-left: clamped to the board and below the header
    assert (st["small"]["w"], st["small"]["h"]) == (240, 160)           # a smaller board shrinks the window to the minimum before clamping
    assert (st["small"]["x"], st["small"]["y"]) == (300 - 240, 200 - 160)
    assert (st["back"]["x"], st["back"]["y"]) == (300 - 240, 200 - 160)  # a bigger board never moves it back out


def test_the_header_drag_uses_the_boards_own_grip_pattern(hub):
    st = _pip_states(hub)
    assert st["dragBody"] == ["dragging"]                               # the body flag the board's grip uses, which turns every iframe off
    assert st["dragEnd"]["body"] == []
    assert st["dragEnd"]["saved"]["x"] == st["dragIn"]["x"]             # the place is saved when the drag ends, and only then
    slice_ = _pip_slice(hub)
    assert "setPointerCapture" in slice_ and "lostpointercapture" in slice_


def test_the_keyboard_moves_the_header_sixteen_pixels_or_sixty_four_with_shift(hub):
    st = _pip_states(hub)
    assert st["arrow16"]["x"] == st["dragIn"]["x"] - 16
    assert st["arrow64"]["y"] == st["arrow16"]["y"] + 64


def test_escape_collapses_the_window_and_a_second_one_hands_the_focus_back(hub):
    st = _pip_states(hub)
    assert st["escCollapsed"]["off"] is True
    assert st["escFocus"] == ["#bz"]                                    # the maximize control, never a restore of the split from inside


def test_the_window_holds_a_dashboard_only_while_it_is_the_live_surface(hub):
    st = _pip_states(hub)
    assert st["init"]["frames"] == 0                                    # the frame is the page's own element, never created here
    assert st["init"]["writes"] == 0 and st["init"]["src"] == ""        # in the split the run pane is live: the window opens no stream
    assert st["maximized"]["writes"] == 1                               # maximizing sources it once
    assert st["collapsed"]["writes"] == 1 and st["collapsed"]["src"] == "about:blank"   # a collapsed window holds no dashboard
    assert st["expanded"]["writes"] == 2                                # expanding connects again
    assert (st["collapsed"]["h"], st["expanded"]["h"]) == (34, 300)
    assert st["switched"]["writes"] == 4                                # the Escape collapse and its expand cost one more connection, then one new source for the next run
    assert st["repainted"]["writes"] == 4                               # a repaint of the same run never re-sources it
    assert st["small"]["writes"] == 4 and st["back"]["writes"] == 4


def test_the_window_reads_the_run_and_the_card_it_shows(hub):
    st = _pip_states(hub)
    assert "build #11" in st["maximized"]["head"] and "running" in st["maximized"]["head"]
    assert "Groups" in st["maximized"]["head"]
    assert "fix PR #1202" in st["switched"]["head"]                      # it follows the left bar's selection


def test_no_run_selected_shows_the_hubs_message_and_no_frame(hub):
    st = _pip_states(hub)
    assert "No open runs." in st["norun"]["msg"] and st["norun"]["noframe"] is True


def test_a_dashboard_without_the_card_mode_says_so_instead_of_an_empty_frame(hub):
    st = _pip_states(hub)
    assert st["waiting"]["noframe"] is False                             # the frame is given its chance to answer first
    assert st["unsupported"]["noframe"] is True
    assert "no single-card mode" in st["unsupported"]["msg"]
    assert st["supported"]["noframe"] is False and st["supported"]["msg"] == ""   # the dashboard answered: the card is shown
