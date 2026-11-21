"""The mobile page's text, empty states and touch targets (#915, SPEC 2498 #2501).

Every target the phone draws is at least 44 px tall. An empty column says so; Work holds no filter, so no column note
names one. The profile hub's tabs never wrap, so no tab scrolls sideways at 390 px.

The empty-column note is one pure function in `hub-mobile-gesture.js`, run under node with no DOM. The styles are read
from the page's own stylesheet, comments stripped.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
PAGE = SCRIPTS / "hub-mobile.html"
NODE = shutil.which("node")
TOUCH_PX = 44


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _css():
    html = PAGE.read_text()
    page = html[:html.index("</style>")]
    return re.sub(r"/\*.*?\*/", "", page, flags=re.S)


def _decls(selector):
    """Every declaration block whose selector list names `selector`, joined with ';'."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", _css()):
        if selector in [s.strip() for s in m.group(1).split(",")]:
            out.append(m.group(2))
    return ";".join(out)


@pytest.mark.node_bound
def test_an_empty_column_with_no_filter_set_never_mentions_filters():
    src = GESTURE_JS.read_text()
    assert run_js(src, "emptyColumnNote({ groups: 2, shown: 1, filtered: false })") == "No card in this column."


@pytest.mark.node_bound
def test_no_column_at_all_says_no_column_holds_a_card():
    src = GESTURE_JS.read_text()
    assert run_js(src, "emptyColumnNote({ groups: 0, shown: 0, filtered: false })") == "Loading the board."
    assert run_js(src, "emptyColumnNote({ groups: 3, shown: 0, filtered: false })") == "No column holds a card."


@pytest.mark.parametrize("selector", ["#bhead button", "#col-tabs button", ".lane-h", ".boardlink"])
def test_each_board_control_is_a_44_px_target(selector):
    m = re.search(r"min-height\s*:\s*(\d+)px", _decls(selector))
    assert m, f"{selector} sets no min-height"
    assert int(m.group(1)) >= TOUCH_PX


def test_the_profile_tabs_never_wrap_at_390_px():
    assert "white-space:nowrap" in _decls("nav button").replace(" ", "")
