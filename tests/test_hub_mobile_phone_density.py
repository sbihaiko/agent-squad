"""The phone at phone density (#1142): one column at reading size, the shared chips and state words, every fixed edge
clearing the notch and the home indicator, and every dismissal and the one write gesture inside the thumb zone.

The styles are read from the page's own stylesheet and the tokens it inlines, comments stripped. The gesture rules run
under node alone, with no DOM and no server.
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
HUB_PY = SCRIPTS / "hub.py"
PAGE = SCRIPTS / "hub-mobile.html"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
TOKENS = SCRIPTS / "web-tokens.css"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_phone_density", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _css():
    html = PAGE.read_text()
    return re.sub(r"/\*.*?\*/", "", html[:html.index("</style>")], flags=re.S)


def _decls(selector, css=None):
    """Every declaration block whose selector list names `selector`, joined with ';'."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css if css is not None else _css()):
        if selector in [s.strip() for s in m.group(1).split(",")]:
            out.append(m.group(2))
    return ";\n".join(out).strip()


def _tokens():
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", TOKENS.read_text()))


def _resolved(value, tokens=None):
    """One token reference to its declared value: `var(--x)` reads the token file, twice over at most."""
    tokens = tokens if tokens is not None else _tokens()
    for _ in range(2):
        m = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        if not m or m.group(1) not in tokens:
            break
        value = tokens[m.group(1)]
    return value.strip()


def _value(decls, prop):
    m = re.search(rf"(?:^|;){re.escape(prop)}:([^;]+)", decls)
    assert m, f"no {prop} declaration"
    return m.group(1)


def _padding(value, tokens=None):
    return [_resolved(v, tokens) for v in value.split()]


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# --- one column at reading size ------------------------------------------------------------------------------------

def test_the_phone_holds_one_column_at_body_size_and_a_caption_at_13px():
    tokens = _tokens()
    assert "flex-direction:column" in _decls("#cards"), "the board draws more than one column"
    assert tokens["--cols-phone"] == "1", "the phone grid does not hold one column"
    css = re.sub(r"/\*.*?\*/", "", PAGE.read_text()[:PAGE.read_text().index("</style>")])
    body = re.search(r"body\{([^}]*)\}", css).group(1).replace(" ", "")
    assert "font:var(--fs-body)" in body, "the content is not drawn at the body size"
    assert tokens["--fs-body"] == "15px" and tokens["--fs-caption"] == "13px", "the body and caption sizes moved"


def test_a_card_is_ten_by_twelve_with_eight_between_cards():
    tokens = _tokens()
    gap = _resolved(_value(_decls("#cards"), "gap"), tokens)
    padding = _padding(_value(_decls(".card"), "padding"), tokens)
    assert gap == "8px", f"the cards sit {gap} apart, not 8 px"
    assert padding == ["10px", "12px"], f"a card is padded {padding}, not 10 by 12"


def test_the_card_chip_and_the_sheet_chip_share_one_geometry():
    tokens = _tokens()
    for selector in (".card .lchip", "#sh-chips .chip"):
        decls = _decls(selector)
        assert "min-height:var(--chip-h)" in decls, f"{selector} does not take the chip height"
        assert "font-size:var(--fs-caption)" in decls, f"{selector} is not drawn at the caption size"
        assert "border-radius:var(--radius-lg)" in decls, f"{selector} is not drawn as a chip"
    assert tokens["--chip-h"] == "20px", "the chip height token moved"


def test_the_breadcrumb_reads_at_the_caption_size():
    assert "font-size:var(--fs-caption)" in _decls("#bread"), "the breadcrumb names a size no token declares"


# --- the shared chips and state words ------------------------------------------------------------------------------

def test_the_phone_run_row_draws_the_shared_state_word_and_mark(hub):  # noqa: F811
    mobile = hub.MOBILE_PAGE
    for r in hub.RUN_STATES:
        assert hub.run_state_css(r) in mobile, f"the phone does not draw the shared rule for {r['state']}"
    assert "dataset.tone" not in mobile and "dataset.mk" not in mobile, "the phone keeps a second paint table of its own"
    assert 'className = "run-state b " + String(r.state || "").replace(/ /g, "-")' in mobile
    assert "state.textContent = r.badge" in mobile


# --- the thumb zone ------------------------------------------------------------------------------------------------

def test_the_close_gesture_starts_on_the_grab_handle():
    src = GESTURE_JS.read_text()
    assert run_js(src, "inThumbZone(900, 1000)") is True
    assert run_js(src, "inThumbZone(599, 1000)") is False
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:true,handle:true})') == {"act": "close"}
    assert run_js(src, 'gestureAct({owner:"sheet",dx:0,dy:90,atTop:true,handle:false,thumb:true})') is None


def test_the_left_edge_swipe_is_the_browser_back_and_no_page_handler():
    src = GESTURE_JS.read_text()
    assert run_js(src, 'runsGesture({x0:10,dx:80,dy:4,atTop:false,thumb:true})') is None
    assert run_js(src, 'runsGesture({x0:10,dx:80,dy:4,atTop:false,thumb:false})') is None


def test_the_move_gesture_starts_in_the_thumb_zone():
    html = PAGE.read_text()
    assert "const thumb = inThumbZone(p.clientY, window.innerHeight)" in html
    assert 'pm: !!(!handle && thumb && card && card.dataset.pm === "1"' in html, "a card outside the thumb zone arms the one write"
    assert "handle = !!(e.target.closest && e.target.closest(\".grab\"))" in html, "the grab handle is not the promotion band's exclusion (#1454)"


def test_every_dismissal_control_sits_in_a_bottom_footer():
    html = PAGE.read_text()
    assert re.search(r'<div id="sheet-actions">.*?id="sh-close"', html, re.S), "the sheet's close is not in its footer"
    assert re.search(r'<div id="psw-footer">.*?id="pswx"', html, re.S), "the project sheet's close is not in its footer"
    # Back is one control at the top of the screen, so no footer repeats it (SPEC 2498, #2502).
    assert not re.search(r'<div id="runview-actions">.*?data-actback', html, re.S), "the run view's footer repeats Back"
    assert not re.search(r'<div id="lead-actions">.*?data-actback', html, re.S), "the lead chart's footer repeats Back"


def test_a_sheet_scrolls_its_content_and_keeps_its_footer_still():
    for selector in ("#sheet", "#psheet"):
        assert "display:flex" in _decls(selector) and "flex-direction:column" in _decls(selector), selector
    for selector in ("#sheet-content", "#psw-content"):
        assert "overflow:auto" in _decls(selector), f"{selector} is not the sheet's scroller"
    for selector in (("#sheet-actions", "#psw-footer", "#runview-actions")):
        assert "flex:none" in _decls(selector), f"{selector} scrolls away with the content"


# --- the fixed edges ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("selector", ["header", "#sheet", "#psheet", "nav", "#undo", "#runview-head",
                                      "#runview-actions"])
def test_a_fixed_edge_clears_the_notch_and_the_home_indicator(selector):
    decls = _decls(selector)
    assert "env(safe-area-inset-top)" in decls or "env(safe-area-inset-bottom)" in decls, selector
    assert "env(safe-area-inset-left)" in decls and "env(safe-area-inset-right)" in decls, \
        f"{selector} runs into the rounded edge in landscape"


def test_the_page_sizes_to_the_dynamic_viewport():
    assert "min-height:100dvh" in _decls("body"), "the page keeps the old static viewport height"


def test_the_installed_app_names_the_bg_token_as_its_colour(hub):  # noqa: F811
    manifest = json.loads(hub.MANIFEST_JSON)
    bg = re.search(r"--bg:\s*([^;]+);", TOKENS.read_text()).group(1).strip()
    assert manifest["theme_color"] == bg, f"the installed app names {manifest['theme_color']} as its colour, not {bg}"
    assert manifest["background_color"] == bg
