"""Hub board: an empty visible column collapses to a narrow vertical strip, and its header count is never flush
against the column name (#1809).

The strip is CSS only, so the contract under test is the one the server actually paints: the declarations served in
the page's stylesheet plus the class the page hands a column. Every assertion names the seam it reads.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
STRIP = "body.dockb #bbody>.col.empty"          # the docked strip: the one place the empty state paints (#1791)


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_empty_column_strip", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _css(hub, selector):
    """Every declaration the served page paints for `selector`, joined: one selector may sit in more than one rule."""
    return ";".join(m.group(2).strip() for m in re.finditer(r"([^{}\n;]+)\{([^{}]*)\}", hub.PAGE)
                    if selector in [s.strip() for s in m.group(1).split(",")])


def _prop(decls, name):
    """The value served for `name`, or None when the rule does not set it."""
    m = re.search(rf"(?:^|;)\s*{re.escape(name)}\s*:\s*([^;]+)", decls)
    return m.group(1).strip() if m else None


def test_an_empty_column_is_a_strip_one_line_wide_and_the_wide_columns_take_the_rest(hub):
    root = _css(hub, ":root")
    w = _prop(root, "--col-empty-w")
    assert w, "the strip's width belongs in :root beside --col-w (#1809), not inline in the rule"
    assert re.fullmatch(r"\d+px", w), f"--col-empty-w is a px width, not {w!r}"
    assert 20 <= int(w[:-2]) <= 40, f"an empty column is one line of text wide (~28px), well under the 132px it was: {w}"
    assert _prop(root, "--col-w"), "the width of a column that holds cards is still set in :root"
    rule = _css(hub, STRIP)
    assert "flex:0 0 var(--col-empty-w)" in rule      # the freed width goes to the columns that have cards
    assert "min-width:var(--col-empty-w)" in rule and "max-width:var(--col-empty-w)" in rule
    widths = re.findall(r"(?:^|;)(flex|min-width|max-width|width)\s*:[^;]*", rule)
    assert widths, f"the strip's rule states its geometry: {rule}"
    assert _prop(rule, "flex") == "0 0 var(--col-empty-w)", "the rule reads the geometry out of :root, never a literal"


def test_the_strip_name_reads_bottom_to_top_on_one_line_and_is_never_clipped(hub):
    name = _css(hub, STRIP + ">.ch>span:first-child")
    assert _prop(name, "writing-mode") == "vertical-rl"
    assert _prop(name, "transform") == "rotate(180deg)"          # the collapsed-column reading, bottom to top
    assert _prop(name, "white-space") == "nowrap"                # one line: a long name makes a taller strip
    assert _prop(name, "text-overflow") is None                  # never ellipsized
    assert _prop(name, "overflow") is None and _prop(name, "overflow-x") is None   # never clipped away
    assert _prop(name, "flex") in ("none", "0 0 auto"), "the name keeps its own length instead of shrinking into a clip"


def test_the_count_badge_is_never_flush_against_the_name(hub):
    ch = _css(hub, STRIP + ">.ch")
    assert _prop(ch, "flex-direction") == "column", "the name and the count take a row each, never one line"
    gap = _prop(ch, "gap")
    assert gap and gap != "0", "the two spans keep a gap: the name reads `Todo`, the badge reads `0`"
    assert "justify-content:flex-start" not in ch, "the rule that glued `Todo` and `0` into `Todo0` is gone"


def test_the_count_and_the_eye_fit_inside_the_strip(hub):
    """Measured in the running hub: the 18px badge and the 14px eye side by side are 32px, four more than the strip
    holds, so the eye spilled into the column beside it. Stacked, both stay inside it."""
    last = _css(hub, STRIP + ">.ch>span:last-child")
    assert _prop(last, "flex-direction") == "column", "the count and the eye take a row each, inside the strip"
    assert _prop(last, "flex") == "0 0 auto", "the pair keeps its own size instead of stretching the strip"


def test_the_colour_dot_carries_the_category_colour_in_the_strip(hub):
    """Measured in the running hub: only `#bbody .ah i` sizes the dot, so in the docked header it keeps its
    background colour and paints nothing -- a 0px box. Inside the strip it gets the size the accordion gives it."""
    dot = _css(hub, STRIP + ">.ch i")
    assert _prop(dot, "width") == "8px" and _prop(dot, "height") == "8px", f"the dot the accordion header paints: {dot!r}"
    assert _prop(dot, "border-radius") == "50%"
    assert _prop(dot, "display") == "inline-block"


def test_the_strip_holds_no_card_body_and_reserves_no_height_for_one(hub):
    assert _prop(_css(hub, STRIP + ">.sc"), "display") == "none"   # no cards, no scroll area, no reserved height
    ch = _css(hub, STRIP + ">.ch")
    assert _prop(ch, "flex") == "1 1 auto", "the header fills the strip instead of leaving a body-sized gap"


def test_a_column_that_holds_a_card_paints_exactly_as_before(hub):
    assert "flex:1 1 0" in _css(hub, "body.dockb #bbody>.col") and "min-width:var(--col-w)" in _css(hub, "body.dockb #bbody>.col")
    ch = _css(hub, "body.dockb .ch")
    assert "justify-content:space-between" in ch                  # the wide column keeps its horizontal header
    assert _prop(ch, "writing-mode") is None and _prop(ch, "transform") is None
    assert "flex-wrap" not in ch                                  # ... and still lays the name and count out side by side
    assert _prop(_css(hub, "body.dockb .col>.sc"), "display") is None   # the body of a column with cards is never hidden


def test_the_collapse_never_reaches_the_non_docked_accordion(hub):
    for m in re.finditer(r"([^{}\n;]+)\{", hub.PAGE):
        sel = m.group(1)
        assert not (".acc" in sel and "empty" in sel), f"the accordion has no empty state to collapse: {sel}"
    acc = re.search(r'^return `<div class="acc.*$', hub.PAGE, re.M).group(0)
    assert "empty" not in acc


def test_the_empty_class_means_a_visible_enabled_column_holding_no_card(hub):
    """The class the strip hangs on, run in the browser's own language: zero cards and nothing else puts it there."""
    line = re.search(r"^const colCls=.*$", hub.PAGE, re.M).group(0)
    script = line + "\nconsole.log(JSON.stringify([colCls(false,0),colCls(false,1),colCls(true,0)]))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["col empty", "col", "col prs empty"]


def test_the_class_follows_the_filtered_count_so_a_column_collapses_and_expands_live(hub):
    assert "colCls(isPR(g.name),it.length)" in hub.PAGE, "the class is recomputed from the filtered cards on every poll"
    assert "setInterval(pollBoard,5000)" in hub.PAGE, "the board is re-rendered on the timer, so no reload is needed"
    assert '${f||sel.length?it.length+"/":""}${g.n}' in hub.PAGE, "the badge still says `Todo 0/` while a filter is on"
