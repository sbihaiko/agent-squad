"""The swimlanes are drawn, not only modelled: a card the server served reaches the page's markup.

The lane model is tested in node over `board-lanes.js`, and that passed while the board drew nothing at all: the
renderer called `cards(...)` — a function that answers one HTML string — and joined its answer as if it were a list.
The page's `try { ... } catch (e) {}` swallowed the TypeError, so the board sat empty with no card and no message.
These tests read the page's own renderer text and hold the shape that broke it, and the hub serves a page that
carries the renderer at all.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


def page():
    return HUB_PY.read_text()


def test_the_card_builder_answers_one_string_and_the_lane_never_joins_it():
    """`cards(g, it, shown)` returns `....join("")` already: a second join on its answer throws, and the board is empty.

    The guard is the call itself: the only legal use of `cards(` in the page is its own definition and a template
    placeholder that is interpolated once.
    """
    src = page()
    calls = [m.group(0) for m in re.finditer(r"cards\([^;]*?\)\.join\(", src)]
    assert calls == [], f"cards() answers a string, so it is never joined: {calls}"


def test_the_lane_renderer_is_wired_into_the_board_body():
    """A renderer no one calls is the same empty board: the body takes the lanes when there is a model."""
    src = page()
    assert "const laneHtml=m=>{" in src, "the lane renderer is gone from the page"
    assert "const lanesHtml=laneHtml(model)" in src, "the board draws no lane model"
    assert '$("#bbody").innerHTML=lanesHtml' in src, "the board body never takes the lane markup"
    assert 'if(model){if($("#bbody").dataset.h!==lanesHtml)' in src, \
        "the column markup is written beside the lane markup: the body alternates on every poll"


def test_the_served_page_carries_the_renderer(tmp_path, monkeypatch):
    """What the browser receives, not what the file holds: the inlined page carries both the model and the renderer."""
    import importlib.util
    import sys

    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(ROOT))
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setenv("SG_HUB_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("hub_lane_render", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert "function buildBoard(" in mod.PAGE, "the served page carries no lane model"
    assert "const laneHtml=m=>{" in mod.PAGE, "the served page carries no lane renderer"
    assert "/*BOARD_LANES*/" not in mod.PAGE, "the lane text was never inlined"


def test_a_spec_lane_carries_a_link_to_its_issue_on_github():
    """A spec lane names its number; the reader opens it in one click, and the link is not the lane's collapse toggle."""
    src = page()
    line = next(x for x in src.splitlines() if 'class=ghl' in x)
    assert 'href="https://github.com/${esc(l.r||"__REPO__")}/issues/${l.num}"' in line, line[:200]
    assert re.search(r'target=_blank rel=noopener', line), "the link opens in a new tab"
    assert 'e.target.closest(".ghl"))return' in src, "a click on the link collapses the lane instead of opening the issue"
