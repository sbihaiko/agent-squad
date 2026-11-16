"""A group's columns are lanes of a card grid, and each lane takes the width of its card count (#1994).

The kanban drew one card per line in every column, and every visible column took the same width, so a group of nine
cards in Doing filled the screen while an empty Todo held as much room. A group (a spec, Loop bugs or No spec) now
draws one lane per visible column: the cards of one lane fill a grid, the lane takes the weight of its card count,
and the lane names its column and count. The rule lives in the page's rule layer (`board-lanes.js`) and its look in
`board-lanes.css`; ADR-0137 keeps the domain rules out of the view, and no browser runs here: the rule text runs
under node, and the stylesheet is read as text.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
LANES_JS = SCRIPTS / "board-lanes.js"
LANES_CSS = SCRIPTS / "board-lanes.css"
MOBILE = SCRIPTS / "hub-mobile.html"
NODE = shutil.which("node")

CARD_MIN_PX = 340
COL_MIN_PX = 320


def _css():
    return re.sub(r"/\*.*?\*/", "", LANES_CSS.read_text(), flags=re.S)


def _rule(selector):
    """One rule of the stylesheet, without the whitespace around its declarations."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", _css())
    assert m, f"no rule for {selector}"
    return re.sub(r"\s+", "", m.group(1))


def _lanes_rule(script):
    if NODE is None:
        pytest.skip("node is required")
    p = subprocess.run([NODE, "-"], input=LANES_JS.read_text() + "\n" + script,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


MODEL = {"vc": ["Todo", "Doing", "Code Review"], "cols": ["Todo", "Doing", "Code Review"],
         "lanes": [{"id": "s12", "kind": "s", "num": 12, "title": "Short title", "closed": 1, "total": 3,
                    "doing": 1, "review": 0, "st": "Todo", "collapsed": False,
                    "counts": {"Todo": 3, "Doing": 2, "Code Review": 0},
                    "cells": {"Todo": [{"n": 1, "t": "a"}, {"n": 2, "t": "b"}],
                              "Doing": [{"n": 3, "t": "c"}], "Code Review": []}},
                   {"id": "none", "kind": "none", "title": "No spec", "closed": 0, "total": 0,
                    "doing": 0, "review": 0, "st": "", "collapsed": False,
                    "counts": {"Todo": 1, "Doing": 7, "Code Review": 0},
                    "cells": {"Todo": [{"n": 4, "t": "d"}], "Doing": [{"n": 5, "t": "e"}], "Code Review": []}}]}


def _drawn(model=None):
    """The lane markup the page builds for this model, with the board and the browser stubbed out."""
    body = re.search(r"const laneHtml=m=>\{.*?\n return h\+\"</div>\"\};", HUB_PY.read_text(), re.S).group(0)
    stubs = ('const esc=s=>String(s),colOf=()=>"var(--s1)",CN={},eye=()=>"",SLOTS=new Map(),cards=()=>"",bo={},f="",'
             'BOARD_VOCAB={todo:"Todo",doing:"Doing",review:"Code Review"};')
    html = _lanes_rule(stubs + body + f"console.log(JSON.stringify(laneHtml({json.dumps(model or MODEL)})));")
    return html


def test_a_lane_fills_a_grid_of_cards():
    """The cards of one column are a grid, so a wide lane shows several cards on one line."""
    assert "grid-template-columns:repeat(auto-fill,minmax(340px,1fr))" in _rule("#bbody .cell")


def _cards_per_line(lane_px):
    """How many cards of the lane grid fit on one line at this lane width, read from the drawn rule."""
    rule = _rule("#bbody .cell")
    card = int(re.search(r"minmax\((\d+)px,", rule).group(1))
    gap = int(re.search(r"gap:(\d+)px", rule).group(1))
    pad = int(re.search(r"padding:(\d+)px", rule).group(1))
    inner = lane_px - 2 * pad
    return max(1, (inner + gap) // (card + gap))


def test_a_wide_lane_shows_four_cards_per_line_and_a_narrow_one_shows_one():
    assert _cards_per_line(1500) == 4
    assert _cards_per_line(699) == 1


def test_a_column_takes_the_weight_of_its_card_count():
    """The weight is the card count clamped to 1..4, so one card and no card take the same single share."""
    out = _lanes_rule("console.log(JSON.stringify([0,1,2,3,4,9].map(colWeight)));")
    assert out == [1, 1, 2, 3, 4, 4]


def test_a_column_track_never_goes_below_320_px():
    tracks = _lanes_rule("console.log(JSON.stringify(colTracks([0,1,2,9])));")
    assert tracks == "minmax(320px,1fr) minmax(320px,1fr) minmax(320px,2fr) minmax(320px,4fr)"
    assert "grid-template-columns:var(--tracks" in _rule("#bbody .lgrid")


def test_the_board_hands_one_track_list_to_the_grid():
    """The counts are the board's own, summed over the drawn lanes, so every lane keeps the same column edges."""
    grid = re.search(r'<div class=lgrid style="([^"]*)">', _drawn()).group(1)
    assert "--tracks:minmax(320px,4fr) minmax(320px,4fr) minmax(320px,1fr)" in grid, grid


def test_each_lane_names_its_column_and_its_count():
    """The label counts the group's own cards in that column, so No spec's Doing says 7 and not the board's total."""
    html = _drawn()
    label = r'<div class=cell data-lane="%s" data-col="%s"[^>]*><div class=clh><span>(.*?)</span><b>(.*?)</b></div>'
    assert re.search(label % ("s12", "Todo"), html, re.S).groups() == ("Todo", "3")
    assert re.search(label % ("none", "Doing"), html, re.S).groups() == ("Doing", "7")


def test_a_lane_wears_a_tint_of_its_column_colour():
    assert "background:color-mix(insrgb,var(--cc," in _rule("#bbody .cell")
    cell = re.search(r'<div class=cell data-lane="s12" data-col="Todo" style="([^"]*)"', _drawn()).group(1)
    assert "--cc:" in cell, cell


def test_two_lanes_are_separated_by_a_divider():
    assert "border-right:1pxsolidvar(--sep)" in _rule("#bbody .cell")


def test_a_card_title_clamps_to_two_lines_with_the_whole_title_in_its_tooltip():
    clamp = _rule("#bbody .cell .it .tt")
    assert "-webkit-line-clamp:2" in clamp and "-webkit-box-orient:vertical" in clamp and "overflow:hidden" in clamp
    page = HUB_PY.read_text()
    line = next(x for x in page.splitlines() if "<span class=tt" in x)
    assert re.search(r'<span class=tt title="\$\{esc\(tt\)\}">\$\{esc\(tt\)\}</span>', line), line[-400:]


def test_a_card_top_border_keeps_the_issue_colour():
    """The lane's tint is the column's, but the card still says which issue it is by its top border."""
    page = HUB_PY.read_text()
    rule = re.sub(r"\s+", "", re.search(r"#bbody \.it\{([^}]*)\}", page).group(1))
    assert "border-top:3pxsolidvar(--lc," in rule
    line = next(x for x in page.splitlines() if "class=it data-n=" in x)
    assert "--lc:${cardColor(SLOTS,x)}" in line, line[-300:]


def test_the_group_header_spans_the_whole_width_above_the_lanes():
    assert "grid-column:1/-1" in _rule("#bbody .lh")


def test_the_mobile_page_keeps_one_card_per_line():
    mobile = MOBILE.read_text()
    assert "auto-fill" not in mobile, "the phone page takes no grid of cards"
    card = re.search(r"\.card\{([^}]*)\}", mobile).group(1)
    assert "display:block" in card and "width:100%" in card, card
