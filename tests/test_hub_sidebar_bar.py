"""The Monitor bar folds away with one chevron (#1159).

The bar keeps its place at the window's left, so a close slides it left into a slim rail at the left edge, and the board
takes the freed width. The chevron rides the bar's leading edge and mirrors with the state; it never leaves the bar. The
page is read as served; the state rule and the toggle block run under node with a small document stub.
"""
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
TOKENS = ROOT / "contrib/squad-goal/scripts/web-tokens.css"
NODE = shutil.which("node")
ENV_KEYS = ("SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_CHECKOUT", "SG_HUB_DIR", "SG_STATE", "SG_SHARED")


@pytest.fixture(scope="module")
def page():
    before = {k: os.environ.get(k) for k in ENV_KEYS}
    os.environ.update({"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4",
                       "SG_CHECKOUT": tempfile.mkdtemp(), "SG_HUB_DIR": tempfile.mkdtemp(),
                       "SG_STATE": tempfile.mkdtemp(), "SG_SHARED": tempfile.mkdtemp()})
    try:
        spec = importlib.util.spec_from_file_location("hub_sidebar_bar", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod.PAGE
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _css(page):
    m = re.search(r"/\* SIDEBAR-CSS\{ \*/.*?/\* \}SIDEBAR-CSS \*/", page, re.S)
    assert m, "the page carries no sidebar bar CSS block"
    return m.group(0)


def _rule(page, selector):
    m = re.search(r"(?:^|[\n}])" + re.escape(selector) + r"\{([^}]*)\}", _css(page))
    assert m, f"the sidebar bar CSS carries no rule for {selector}"
    return m.group(1)


def _button(page):
    m = re.search(r"<button\b[^>]*\bid=sbtn\b.*?</button>", page, re.S)
    assert m, "the page carries no sidebar toggle"
    return m.group(0)


def _block(page):
    m = re.search(r"^// SIDEBAR\{.*?^// \}SIDEBAR$", page, re.M | re.S)
    assert m, "the page carries no sidebar toggle script"
    return m.group(0)


def _state_rule(page):
    m = re.search(r"<script id=sideRule>(.*?)</script>", page, re.S)
    assert m, "the page carries no sidebar state rule"
    return m.group(1)


def _boot(page):
    m = re.search(r"<script id=sbboot>(.*?)</script>", page, re.S)
    assert m, "the page carries no sidebar boot script"
    return m.group(1)


def _run_node(src):
    if NODE is None:
        pytest.skip("node is required")
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ---------------------------------------------------------------- the control

def test_one_real_button_names_the_bar_and_reports_its_state(page):
    button = _button(page)
    assert re.search(r"\btype=button\b", button)
    assert re.search(r"\baria-controls=side\b", button)
    assert re.search(r"\baria-expanded=true\b", button)
    assert re.search(r'aria-label="Collapse the Monitor bar"', button)
    assert page.count("id=sbtn") == 1
    assert page.index("<aside id=side>") < page.index("id=sbtn") < page.index("</aside>")


def test_the_control_keeps_the_two_px_focus_ring(page):
    assert re.search(r"#sbtn:focus-visible\{[^}]*outline:var\(--focus-w\) solid var\(--focus\)", _css(page))
    assert "--focus-w:2px" in TOKENS.read_text()


def test_the_chevron_points_left_while_open_and_right_while_shut(page):
    assert "M8 2L4 6l4 4" in _button(page)          # the path draws a left chevron
    assert re.search(r"html\.sbshut #sbtn svg\{[^}]*scaleX\(-1\)", _css(page))
    assert "rotate(180deg)" not in page              # the strip guard refuses that string


# ---------------------------------------------------------------- the slide

def test_the_bar_stays_at_the_left_and_slides_left_when_shut(page):
    css = _css(page)
    assert page.index("<aside id=side>") < page.index("<main id=content>")
    assert not re.search(r"(?<![\w-])order:", css)         # the bar keeps its place in the row
    assert "#side{width:280px" in page                     # the open bar keeps its third of the window
    assert re.search(r"#side\{[^}]*position:relative", css)
    assert re.search(r"#side\{[^}]*overflow:hidden", css)  # the body is clipped, never a second column
    assert re.search(r"html\.sbshut #side\{[^}]*width:28px", css)
    assert re.search(r"#sbbody\{[^}]*transition:transform var\(--motion-duration-enter\) var\(--motion-entry-easing\)", css)
    assert re.search(r"html\.sbshut #sbbody\{[^}]*translateX\(-100%\)[^}]*var\(--motion-exit-easing\)", css)
    assert re.search(r"#sbtn\{[^}]*right:4px", css)        # on the bar's leading edge
    assert "html.sbshut #sbtn{" not in css                 # the chevron never moves on its own


def test_the_board_takes_the_freed_width(page):
    assert re.search(r"#side\{width:280px[^}]*flex:none", page)
    assert "#content{flex:1;min-width:0;overflow:auto}" in page


def test_the_slide_uses_shared_entry_and_exit_motion_tokens(page):
    css = _css(page)
    tokens = TOKENS.read_text()
    assert "--motion-duration-enter:200ms" in tokens and "--motion-duration-exit:150ms" in tokens
    assert re.search(r"#side\{[^}]*transition:width var\(--motion-duration-enter\) var\(--motion-entry-easing\)", css)
    assert re.search(r"#sbbody\{[^}]*transition:transform var\(--motion-duration-enter\) var\(--motion-entry-easing\)", css)
    assert re.search(r"html\.sbshut #side\{[^}]*var\(--motion-exit-easing\)", css)
    assert re.search(r"html\.sbshut #sbbody\{[^}]*var\(--motion-exit-easing\)", css)


def test_reduced_motion_shows_and_hides_the_bar_with_no_slide(page):
    css = _css(page)
    reduced = next(line for line in TOKENS.read_text().splitlines()
                   if line.startswith("@media (prefers-reduced-motion: reduce)"))
    assert "#side" in reduced and "#sbbody" in reduced
    animated = {s.strip() for s in re.findall(r"([^{}]+)\{[^{}]*\btransition\s*:(?!\s*none)", css)}
    assert animated == {"#side", "#sbbody"}, animated


# ---------------------------------------------------------------- the rail

def test_a_shut_bar_leaves_one_rail_at_the_left_edge_and_nothing_else(page):
    css = _css(page)
    assert re.search(r"html\.sbshut #side\{[^}]*width:28px", css)
    assert re.search(r"#sbtn\{[^}]*right:4px", css)
    assert "sbshut #win::after" not in css          # the bar itself is the rail, never a second layer
    assert page.count("<aside id=side>") == 1
    inner = re.search(r"<aside id=side>(.*?)</aside>", page, re.S).group(1)
    # the rail holds the chevron; the body is the only other child, and the shut state clips it
    assert re.match(r"\s*<button\b[^>]*id=sbtn\b.*?</button>\s*<div id=sbbody>.*</div>\s*$", inner, re.S), inner[:200]


def test_the_toggle_is_shown_only_on_a_desktop_width(page):
    css = _css(page)
    assert "#sbtn{display:none}" in css
    assert re.search(r"@media \(min-width:601px\)\{.*?#sbtn\{display:flex", css, re.S)
    assert re.search(r"@media \(max-width:600px\)\{.*?#side\{width:auto", page, re.S)


# ---------------------------------------------------------------- the state

def test_a_saved_shut_bar_is_shut_before_the_first_paint(page):
    rule, boot = _state_rule(page), _boot(page)
    cases = [('{"side":"shut"}', True, ["sbshut"]), ('{"side":"open"}', True, []),
             ('{"side":"shut"}', False, []), ("not json", True, []), (None, True, [])]
    for stored, wide, want in cases:
        src = (
            f"const localStorage={{hub:{json.dumps(stored)}}};const classes=new Set();"
            f"globalThis.matchMedia=()=>({{matches:{json.dumps(wide)}}});"
            "globalThis.document={documentElement:{classList:{add:c=>classes.add(c),contains:c=>classes.has(c)}}};"
            f"{rule}\n{boot}\nconsole.log(JSON.stringify([...classes]));"
        )
        assert _run_node(src) == want, (stored, wide)


def _stub(stored, wide):
    return (
        f'let ls={json.dumps({"side": stored} if stored else {})};let saved=null;const save=()=>{{saved=JSON.stringify(ls)}};'
        f"const WIDE={json.dumps(wide)};const classes=new Set();const attrs={{}};const listeners={{}};"
        "const bodyEl={inert:false};"
        "const sbtn={setAttribute:(k,v)=>{attrs[k]=v},addEventListener:(t,f)=>{listeners[t]=f}};"
        "const wide={matches:WIDE,addEventListener:(t,f)=>{listeners.wide=f}};"
        "const $=s=>s==='#sbtn'?sbtn:bodyEl;globalThis.matchMedia=()=>wide;"
        "globalThis.document={documentElement:{classList:{toggle:(c,f)=>{f?classes.add(c):classes.delete(c)},"
        "contains:c=>classes.has(c)}}};"
    )


def test_the_toggle_folds_the_bar_persists_the_choice_and_leaves_inner_arrows_alone(page):
    rule, block = _state_rule(page), _block(page)
    assert "details" not in block and ".open" not in block and "[open]" not in block
    src = (
        _stub(None, True) + rule + "\n" + block +
        "\nlisteners.click();const shut={classes:[...classes],expanded:attrs['aria-expanded'],label:attrs['aria-label'],inert:bodyEl.inert,saved};"
        "listeners.click();"
        "console.log(JSON.stringify({shut,back:[...classes],expandedBack:attrs['aria-expanded'],labelBack:attrs['aria-label'],inertBack:bodyEl.inert,saved:saved}));"
    )
    out = _run_node(src)
    assert out["shut"]["classes"] == ["sbshut"]
    assert out["shut"]["expanded"] == "false" and out["shut"]["label"] == "Open the Monitor bar"
    assert out["shut"]["inert"] is True
    assert json.loads(out["shut"]["saved"])["side"] == "shut"
    assert out["back"] == [] and out["expandedBack"] == "true" and out["inertBack"] is False
    assert out["labelBack"] == "Collapse the Monitor bar"
    assert json.loads(out["saved"])["side"] == "open"


def test_a_phone_width_never_shows_the_bar_shut(page):
    rule, block = _state_rule(page), _block(page)
    src = _stub("shut", False) + rule + "\n" + block + "\nconsole.log(JSON.stringify([...classes]));"
    assert _run_node(src) == []

def test_the_page_never_declares_the_same_id_twice(page):
    """One id, one element: a bar body named like a settings panel let the panel's script fill the bar (#1159)."""
    markup = re.sub(r"<script\b.*?</script>", "", page, flags=re.S)   # a template string is not the document
    ids = re.findall(r"\bid=([\w-]+)", markup)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"the page declares these ids twice: {dupes}"

def test_the_bar_body_scrolls_inside_the_bar(page):
    """A body taller than the window scrolls, and the bar never clips it: the bar is a column and the body takes the rest."""
    side, body = _rule(page, "#side"), _rule(page, "#sbbody")
    assert "display:flex" in side and "flex-direction:column" in side, side
    assert "overflow:hidden" in side, "the bar's own overflow stays hidden: the body is what scrolls"
    assert "flex:1 1 auto" in body and "min-height:0" in body, body
    assert "overflow:auto" in body, body
    assert "height:100%" not in body, "a percentage height on the body resolves against an auto-height bar and never scrolls"


def test_a_shut_bar_hides_its_body_so_no_clipped_text_shows_in_the_rail():
    """#3036: the body of a shut bar is wider than its 28 px rail; it slides away and is hidden, so no clipped word
    shows in the rail and no hidden link takes the focus."""
    page = (Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub_theme_page.py").read_text()
    rule = re.search(r"html\.sbshut #sbbody\{([^}]*)\}", page)
    assert rule and "visibility:hidden" in rule.group(1), rule and rule.group(1)
