"""The session overlay (#898): one button in the same corner of every hub-served page opens an overlay on the project's
session. The pages are read as served; the overlay's rules are pure functions of `hub-overlay.js`, run under node with no
DOM. The send rule and the project route are in test_session_send.py.
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
OVERLAY = SCRIPTS / "hub-overlay.js"
NODE = shutil.which("node")
MARK = "hub-overlay.js (#898)"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_overlay_pages", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.loop_caps = lambda: None
    return mod


def run_js(expr):
    script = f"{OVERLAY.read_text()}\nconst SQ=module.exports;\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ---------------------------------------------------------------- where the button is

def test_the_profile_hub_page_carries_the_overlay_once(hub):
    page = hub.served_page()
    assert page.count(MARK) == 1
    assert 'window.SQUAD_OV_SOURCES' in page


def test_the_mobile_page_carries_the_overlay_once(hub):
    assert hub.MOBILE_PAGE.count(MARK) == 1


def test_the_index_carries_the_overlay_and_reads_each_repositorys_sessions():
    spec = importlib.util.spec_from_file_location("hubmux_overlay", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    page = mux.root_page({}, "squad hub").decode()
    assert page.count(MARK) == 1
    assert 'fetch("/api/hub"' in page


def test_a_run_dashboard_through_the_hub_gains_the_overlay(hub):
    body = hub.with_overlay(b"<html><body>run dashboard page</body></html>", dash=True)
    assert MARK.encode() in body and body.startswith(b"<html><body>run dashboard page")
    assert b'split("/dash/")' in body


def test_the_workflow_editor_carries_no_overlay():
    editor = (ROOT / "dashboard" / "agent-squad-run-dashboard.html").read_text()
    assert "hub-overlay" not in editor and "Terminal - Claude CLI" not in editor


def test_the_overlay_names_no_api_route_the_hub_would_rewrite():
    """The mux adds the profile to every quoted /api/ path; the overlay builds its routes from the page's source."""
    src = OVERLAY.read_text()
    assert not re.search(r"""["'`]/api/""", src)
    assert "https://" not in src and "http://" not in src.replace("http://www.w3.org/2000/svg", "")


# ---------------------------------------------------------------- the button

def test_the_button_is_a_red_lens_in_a_metal_ring_named_chat_with_the_ai():
    src = OVERLAY.read_text()
    assert 'aria-label="Terminal - Claude CLI"' in src
    assert "radialGradient" in src and "sq-ov-lens" in src and "sq-ov-ring" in src
    button = re.search(r"<button id=\"sq-ov-btn\".*?</button>", src, re.S).group(0)
    assert "?" not in re.sub(r"<[^>]*>", "", button)


@pytest.mark.node_bound
def test_the_lens_center_moves_by_a_damped_spring_and_never_jumps():
    path = run_js("""(()=>{let s={x:0,y:0,vx:0,vy:0};const xs=[];
      for(let i=0;i<120;i++){s=SQ.springStep(s,{x:4,y:0},1/60);xs.push(s.x);} return xs;})()""")
    steps = [b - a for a, b in zip([0] + path[:-1], path, strict=True)]
    assert max(abs(d) for d in steps) < 1.0
    assert abs(path[-1] - 4) < 0.05


@pytest.mark.node_bound
def test_the_lens_target_is_clamped_inside_the_lens_and_returns_to_center_after_three_seconds():
    far, idle, near = run_js("""[SQ.lensTarget({x:5000,y:0},{x:0,y:0},1000,0),
      SQ.lensTarget({x:5000,y:0},{x:0,y:0},4000,0), SQ.lensTarget({x:10,y:0},{x:0,y:0},100,0)]""")
    assert abs((far["x"] ** 2 + far["y"] ** 2) ** 0.5 - run_js("SQ.LENS_R")) < 1e-6
    assert idle == {"x": 0, "y": 0}
    assert 0 < near["x"] < far["x"]


@pytest.mark.node_bound
def test_the_lens_stays_still_under_reduced_motion_or_a_hidden_tab():
    assert run_js("[SQ.motionOn(false,false),SQ.motionOn(true,false),SQ.motionOn(false,true)]") == [True, False, False]


def test_the_lens_moves_by_animation_frames_and_no_css_transition():
    src = OVERLAY.read_text()
    assert "requestAnimationFrame" in src
    lens_css = re.findall(r"#sq-ov-lens[^{]*\{[^}]*\}", src)
    assert lens_css and not any("transition" in c for c in lens_css)


# ---------------------------------------------------------------- the session

@pytest.mark.node_bound
def test_two_sessions_ask_the_owner_to_pick_and_one_opens_at_once():
    assert run_js("[SQ.pickNeeded([{sid:'a'}]),SQ.pickNeeded([{sid:'a'},{sid:'b'}]),SQ.pickNeeded([])]") == [False, True, False]


def test_the_overlay_reads_the_terminals_routes_and_polls_while_open():
    src = OVERLAY.read_text()
    assert '"/project"' in src and '"/conversation?sid="' in src and '"/send"' in src
    assert "setInterval" in src and "POLL_MS=3000" in src
    assert "textContent" in src and "innerHTML" not in src.split("// dom:begin")[1]


@pytest.mark.node_bound
def test_with_send_off_the_field_is_disabled_and_names_the_setting():
    off, on = run_js("[SQ.sendState({send:false,why:'Sending from the tailnet is off: set SQUAD_TAILNET_TERMINALS_SEND'}),"
                     "SQ.sendState({send:true,why:''})]")
    assert off["disabled"] is True and "SQUAD_TAILNET_TERMINALS_SEND" in off["note"]
    assert on["disabled"] is False


# ---------------------------------------------------------------- the composer

@pytest.mark.node_bound
def test_enter_sends_shift_enter_breaks_a_line_and_send_is_off_while_empty():
    assert run_js("[SQ.composerKey('Enter',false,false),SQ.composerKey('Enter',true,false),"
                  "SQ.composerKey('Enter',false,true),SQ.composerKey('a',false,false)]") == ["send", "newline", "none", "none"]
    assert run_js("[SQ.canSend('',true),SQ.canSend('  ',true),SQ.canSend('hi',true),SQ.canSend('hi',false)]") == [False, False, True, False]


def test_the_composer_has_a_plus_menu_an_auto_growing_field_and_an_icon_only_send():
    src = OVERLAY.read_text()
    assert 'id="sq-ov-plus"' in src and ">Attach screenshot<" in src and ">Page context<" in src
    assert 'id="sq-ov-text"' in src and "scrollHeight" in src
    send = re.search(r'<button id="sq-ov-send".*?</button>', src, re.S).group(0)
    assert 'aria-label="Send"' in send and re.sub(r"<[^>]*>", "", send).strip() == ""
    assert send.index("disabled") > 0


@pytest.mark.node_bound
def test_the_page_context_is_drafted_as_editable_text():
    text = run_js("SQ.draft({screen:'Hub | p1',route:'/p1/',selection:['Ticket: #12'],filters:['spec=889'],errors:['load failed']})")
    for part in ("Hub | p1", "/p1/", "Ticket: #12", "spec=889", "load failed"):
        assert part in text
    assert len(run_js("SQ.draft({screen:'x'.repeat(9000),route:'/',selection:[],filters:[],errors:[]})")) <= 4000


def test_opening_the_overlay_leaves_the_field_empty_and_sends_nothing():
    """The page context is fetched on demand from the + menu: opening the panel never types for the owner (owner, 2026-11-01)."""
    src = OVERLAY.read_text()
    dom = src.split("// dom:begin")[1]
    open_fn = re.search(r"function openOverlay\(\)\{.*?\n\}", dom, re.S).group(0)
    assert "draftContext" not in dom and "send(" not in open_fn
    assert "window.squadPageContext" in src and 'data-act="ctx"' in src


# ---------------------------------------------------------------- the dock

@pytest.mark.node_bound
def test_the_docked_width_is_clamped_to_360_70_percent_and_a_480_board():
    out = run_js("[SQ.clampWidth(100,1600),SQ.clampWidth(5000,1600),SQ.clampWidth(5000,1000),SQ.clampWidth(500,1600)]")
    assert out == [360, 1120, 520, 500]


@pytest.mark.node_bound
def test_arrow_keys_move_the_handle_by_24_px_and_a_double_click_resets():
    assert run_js("[SQ.keyWidth(500,'ArrowLeft',1600),SQ.keyWidth(500,'ArrowRight',1600),SQ.keyWidth(360,'ArrowRight',1600),"
                  "SQ.keyWidth(500,'Home',1600)]") == [524, 476, 360, 500]
    assert run_js("SQ.DEFAULT_W") == 440


@pytest.mark.node_bound
def test_a_sheet_stacks_left_of_the_dock_or_opens_over_it_when_the_board_would_fall_under_480():
    assert run_js("[SQ.sheetMode(1600,440,420),SQ.sheetMode(1200,440,420)]") == ["stack", "over"]


@pytest.mark.node_bound
def test_the_phone_is_full_screen_with_no_dock():
    assert run_js("[SQ.layout(390,true),SQ.layout(700,true),SQ.layout(1400,true),SQ.layout(1400,false)]") == \
        ["phone", "float", "docked", "float"]


def test_the_dock_persists_its_width_and_the_handle_is_a_keyboard_separator():
    src = OVERLAY.read_text()
    assert "localStorage" in src and "dblclick" in src
    assert 'role="separator"' in src and 'aria-orientation="vertical"' in src


def test_the_header_keeps_the_title_on_one_line_and_its_buttons_from_shrinking():
    src = OVERLAY.read_text()
    assert re.search(r"#sq-ov-title\{[^}]*white-space:nowrap[^}]*text-overflow:ellipsis", src)
    assert re.search(r"#sq-ov header button\{[^}]*flex:none", src)
    for label in ("Dock right", "Undock", "Close"):
        assert f'aria-label="{label}"' in src


def test_the_feed_keeps_the_latest_turns_and_folds_the_older_ones():
    """The panel reads like the terminal: the last twelve turns stand, the older ones fold behind one line (owner, 2026-11-01)."""
    out = run_js("""(() => {
      const turns = Array.from({length: 20}, (_, i) => ({id: 't'+i, role: i % 2 ? 'answer' : 'owner', text: 'x'+i}));
      const a = SQ.splitEarlier(turns), b = SQ.splitEarlier(turns.slice(0, 12)), c = SQ.splitEarlier(turns, 3);
      return {earlier: a.earlier.length, shown: a.shown.map(t => t.id)[0], short: b.earlier.length,
              keep3: [c.earlier.length, c.shown.length]};
    })()""")
    assert out == {"earlier": 8, "shown": "t8", "short": 0, "keep3": [17, 3]}, out

def test_the_reader_can_step_the_text_size_and_the_choice_outlives_the_page():
    """Owner, 2026-11-01: the feed needed its own size control. Two header buttons step one CSS variable, the page keeps the
    choice, and every text rule reads the variable so one step moves the whole talk."""
    src = pathlib.Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/hub-overlay.js"
    js = src.read_text()
    assert 'id="sq-ov-fsdown"' in js and 'id="sq-ov-fsup"' in js
    assert "--sq-fs:13px" in js
    assert js.index("--sq-fs") < js.index(".sq-txt{white-space:pre-wrap")
    assert "font-size:var(--sq-fs)" in js
    assert "localStorage.setItem(FS_KEY" in js and "localStorage.getItem(FS_KEY)" in js
    assert "FS_MIN" in js and "FS_MAX" in js

def test_the_talk_wears_the_terminal_font():
    """Owner, 2026-11-01: the chat read in a UI face while the terminal beside it read in monospace. One stack serves both."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/hub-overlay.js").read_text()
    assert "--sq-mono:ui-monospace" in js
    body = js[js.index(".sq-txt{"):js.index(".sq-txt{") + 200]
    assert "font-family:var(--sq-mono)" in body, body

def test_the_talk_is_markdown_built_as_nodes_never_as_html():
    """Owner, 2026-11-01: the talk must read as markdown. Bold, italic, inline code and a link are built from text nodes, so a
    message that carries markup can never run as HTML."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/hub-overlay.js").read_text()
    assert "function inline(el, text)" in js
    assert "document.createTextNode" in js
    for tag in ('"b"', '"i"', '"code"', '"a"'):
        assert "document.createElement(" + tag + ")" in js, tag
    assert "innerHTML" not in js[js.index("function inline(el, text)"):js.index("function richText(text)")]
    body = js[js.index("function richText(text) {"):js.index("const CLAMP")]
    assert body.count("inline(") >= 4, "a heading, a bullet, a numbered line and a paragraph all read markdown"
    assert "document.createTextNode" in js
