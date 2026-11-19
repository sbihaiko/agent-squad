"""A group's columns are lanes of a card grid, and each lane takes the width of its card count (#1994).

The kanban drew one card per line in every column, and every visible column took the same width, so a group of nine
cards in Doing filled the screen while an empty Todo held as much room. A group (a spec, Loop bugs or No spec) now
draws one lane per visible column: the cards of one lane fill a grid, the lane takes the weight of its card count,
and the lane names its column and count. The rule lives in the page's rule layer (`board-lanes.js`) and its look in
`board-lanes.css`; ADR-0137 keeps the domain rules out of the view, and no browser runs here: the rule text runs
under node, and the stylesheet is read as text.
"""
import copy
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
# The lane rule the page shipped before #2018, copied from 90422e0b5c77c1e521653638c690222d7792dcfd: the card's
# 340 px floor is fixed, so a lane narrower than 340 px plus its padding and divider lets its card run past the border.
BASE_CELL_RULE = (
    "#bbody .cell{min-width:0;min-height:48px;display:grid;"
    "grid-template-columns:repeat(auto-fill,minmax(340px,1fr));align-items:stretch;align-content:start;gap:8px;"
    "padding:8px;background:color-mix(in srgb,var(--cc,var(--mute)) 6%,var(--panel));"
    "border-right:1px solid var(--sep);border-bottom:1px solid var(--line)}"
)
EPS = 1e-6   # the track widths are fractional; a card that fills its lane equals the lane inside this


def _css():
    return re.sub(r"/\*.*?\*/", "", LANES_CSS.read_text(), flags=re.S)


def _rule_in(text, selector):
    """One rule of this stylesheet text, without the whitespace around its declarations."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
    assert m, f"no rule for {selector}"
    return re.sub(r"\s+", "", m.group(1))


def _rule(selector):
    return _rule_in(LANES_CSS.read_text(), selector)


def _rule_opt(text, selector):
    """One rule of this page text, or an empty string when the page holds no rule for the selector."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
    return re.sub(r"\s+", "", m.group(1)) if m else ""


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
    count = re.search(r"^function colCount\(.*$", HUB_PY.read_text(), re.M).group(0)   # the header count, against a limit (#2287)
    stubs = ('const esc=s=>String(s),colOf=()=>"var(--s1)",CN={},eye=()=>"",SLOTS=new Map(),cards=()=>"",bo={},f="",'
             'COLS={},BOARD_VOCAB={todo:"Todo",doing:"Doing",review:"Code Review"};')
    html = _lanes_rule(stubs + count + "\n" + body + f"console.log(JSON.stringify(laneHtml({json.dumps(model or MODEL)})));")
    return html


def _board(groups, **o):
    """The board model `buildBoard` builds for these groups, with the page's options at their defaults unless `o` sets one."""
    opts = {"mode": "spec", "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed", "hidden": [], **o}
    return _lanes_rule(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));")


def test_a_lane_fills_a_grid_of_cards():
    """The cards of one column are a grid, so a wide lane shows several cards on one line.

    The card's floor yields to the lane (`minmax(min(340px,100%),1fr)`), so a lane narrower than one card still
    holds its card inside its own border (#2018)."""
    assert "grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr))" in _rule("#bbody .cell")


def _cards_per_line(lane_px):
    """How many cards of the lane grid fit on one line at this lane width, read from the drawn rule.

    No browser runs in this suite (ADR-0137: the rule layer is read as text here), so this derives the count from
    every declaration that takes room from the lane: the page's border-box rule, the lane's padding on both sides,
    its right border, the card's minimum and the gap. A change to any of them moves the count and fails the test.
    """
    page = HUB_PY.read_text()
    assert "*{box-sizing:border-box}" in page, "the page does not size its boxes by the border"
    rule = _rule("#bbody .cell")
    card = int(re.search(r"minmax\((?:min\()?(\d+)px,", rule).group(1))   # the floor, inside `min(...)` when it yields to the lane
    gap = int(re.search(r"gap:(\d+)px", rule).group(1))
    pad = int(re.search(r"padding:(\d+)px", rule).group(1))
    border = int(re.search(r"border-right:(\d+)px", rule).group(1))
    inner = lane_px - 2 * pad - border
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


def _grid(model=None):
    return re.search(r'<div class=lgrid style="([^"]*)">', _drawn(model)).group(1)


def test_the_board_hands_one_track_list_to_the_grid():
    """The lanes share one grid, so every lane keeps the same column edges."""
    grid = _grid()
    assert "--tracks:minmax(320px,4fr) minmax(320px,4fr) minmax(320px,1fr)" in grid, grid


def test_a_collapsed_lanes_cards_never_widen_a_column():
    """The tracks count the drawn lanes only: a collapsed lane draws no cell, so its cards give no column any width."""
    collapsed = copy.deepcopy(MODEL)
    collapsed["lanes"][0]["collapsed"] = True   # s12 holds Todo 3 and Doing 2, and draws none of them
    grid = _grid(collapsed)
    assert "--tracks:minmax(320px,1fr) minmax(320px,4fr) minmax(320px,1fr)" in grid, grid
    assert "<span>Todo</span><b>4</b>" in _drawn(collapsed), "the header does not count the whole board"


def test_a_column_with_no_card_is_hidden_whether_the_user_hid_it_or_not():
    """Owner 2026-11-19: an empty column takes no slot. It replaces the rule that a visible empty column kept its place
    (#1994), so neither the drawn set nor the pill names a column that holds no work card."""
    model = _board([
        {"name": "Todo", "end": False, "items": []},
        {"name": "Doing", "end": False, "items": [{"n": 5, "t": "e", "l": []}]},
        {"name": "Closed", "end": True, "items": []},
    ])
    assert model["vc"] == ["Doing"], model["vc"]
    assert model["hc"] == [], "an empty column the user hid is named in the pill"
    both = _board([
        {"name": "Todo", "end": False, "items": []},
        {"name": "Doing", "end": False, "items": [{"n": 5, "t": "e", "l": []}]},
    ], hidden=["Todo", "Doing"])
    assert both["vc"] == [], "a column the user left visible at zero cards is drawn"
    assert [x["name"] for x in both["hc"]] == ["Doing"], both["hc"]


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
    assert "line-clamp:2" in clamp and "max-height:3.1em" in clamp, "the clamp has no fallback where -webkit-line-clamp is unknown"
    page = HUB_PY.read_text()
    line = next(x for x in page.splitlines() if "<span class=tt" in x)
    assert r"""<span class=tt title="${esc(tt).replace(/"/g,"&quot;")}">${esc(tt)}</span>""" in line, line[-400:]


def test_a_title_with_a_quote_cannot_break_its_tooltip():
    """The tooltip is a quoted attribute: `esc` leaves a quote alone, so the title passes through the quote escape."""
    page = HUB_PY.read_text()
    esc = next(x for x in page.splitlines() if x.startswith("const esc="))
    out = _lanes_rule(esc + "\nconsole.log(JSON.stringify(esc('a \"quoted\" <b> & title')));")
    assert out == 'a "quoted" &lt;b&gt; &amp; title', out
    line = next(x for x in page.splitlines() if "<span class=tt" in x)
    assert '.replace(/"/g,"&quot;")' in line, line[-400:]


def test_a_narrow_board_wraps_its_lanes_instead_of_scrolling_sideways():
    """The weighted tracks never go below 320 px, so a board of many columns wraps its lanes on a narrow screen."""
    assert re.search(r"@media \(max-width:1100px\)\{#bbody \.lgrid\{grid-template-columns:repeat\(auto-fit,minmax\(360px,1fr\)\)\}\}",
                     _css()), "a narrow board overflows the page sideways"


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


def _track_widths(tracks, board_px):
    """The width each column track takes on a board of this width, from the track list the page draws.

    A track's floor is its `minmax` minimum; the room left over is shared by the tracks' weights, as the grid does.
    """
    parts = [(int(a), int(b)) for a, b in re.findall(r"minmax\((\d+)px,(\d+)fr\)", tracks)]
    assert parts, tracks
    mins = [a for a, _ in parts]
    if sum(mins) >= board_px:
        return mins
    unit = (board_px - sum(mins)) / sum(b for _, b in parts)
    return [a + b * unit for a, b in parts]


def _lane_box(css_text):
    """The card grid of a lane, read from its rule: the card's floor, whether the floor yields to the lane, whether
    the lane may take a track narrower than its content (`min-width:0`), and the room its padding and divider take."""
    rule = _rule_in(css_text, "#bbody .cell")
    m = re.search(r"grid-template-columns:repeat\(auto-fill,minmax\((min\()?(\d+)px,(?:100%\),)?1fr\)\)", rule)
    assert m, rule
    pad = int(re.search(r"padding:(\d+)px", rule).group(1))
    border = int(re.search(r"border-right:(\d+)px", rule).group(1))
    return int(m.group(2)), bool(m.group(1)), "min-width:0;" in rule, pad, border


def _card_min_width():
    """The floor a card's own rule gives it outside the lane grid: a card wider than its lane overflows it too."""
    found = [int(x) for text in (LANES_CSS.read_text(), HUB_PY.read_text())
             for sel in ("#bbody .cell .it", "#bbody .it")
             for x in re.findall(r"min-width:(\d+)px", _rule_opt(text, sel))]
    return max(found, default=0)


def _lane_card_extent(css_text, lane_px):
    """The width a lane's card takes from the lane's left edge, both paddings and the divider included.

    No browser runs in this suite (ADR-0137: the rule layer is read as text here), so this reads the lane's own
    rule. The page sizes every box by its border, so the lane's content box is its track width less both paddings
    and the divider. `minmax(min(340px,100%),1fr)` makes the card yield to that content box; a bare
    `minmax(340px,1fr)` holds its floor and runs past the lane when the floor is wider than the content box
    (#2018). A lane with no content box holds no card, so this fails there instead of reporting a fitting card.
    """
    floor, yields, min0, pad, border = _lane_box(css_text)
    assert not yields or min0, "the lane takes its own track width only with `min-width:0`; without it its content widens the track"
    inner = lane_px - 2 * pad - border
    assert inner > 0, f"the lane of {lane_px:.0f} px has no room for a card ({inner:.0f} px inside its padding)"
    card = min(floor, inner) if yields else floor   # the lane grid's floor, which yields to the content box
    card = max(card, _card_min_width())             # a card's own min-width never yields
    return card + 2 * pad + border


def _no_spec_lane_widths(board_px=2000):
    counts = [3, 6, 9, 2, 1]   # Backlog, Todo, Doing, Code Review, Code Complete: the No spec group's drawn cards
    tracks = _lanes_rule(f"console.log(JSON.stringify(colTracks({json.dumps(counts)})));")
    return _track_widths(tracks, board_px)


def test_the_last_lane_of_a_group_stays_inside_the_groups_right_edge():
    """#2018: at about 2000 px the No spec group drew five lanes, and the card of its last lane (Code Complete)
    ran past the group's right border while Backlog and Doing stayed inside. The card's 340 px floor plus the
    lane's padding and divider is 357 px, wider than the 320 px floor a lane's track can take, and the last lane
    is the one whose weight leaves it under 357 px. The card's extent stays inside the lane, and the lanes' right
    edges stay inside the board."""
    widths = _no_spec_lane_widths()
    extent = _lane_card_extent(LANES_CSS.read_text(), widths[-1])
    assert extent <= widths[-1] + EPS, (f"the last lane's card is {extent - widths[-1]:.0f} px past the lane's right "
                                       f"edge ({extent:.0f} px of card for a lane of {widths[-1]:.0f} px)")
    assert sum(widths) <= 2000 + EPS, f"the group's right edge is {sum(widths):.0f} px past the board"


def test_the_lane_check_catches_the_fixed_floor_that_overflows_2018():
    """The check above guards #2018 only if it fails on the lane rule that shipped before the fix: a fixed 340 px
    floor is 357 px with the lane's padding, past a 320 px track and past the last lane's own width too."""
    widths = _no_spec_lane_widths()
    base = _lane_card_extent(BASE_CELL_RULE, widths[-1])
    assert base > widths[-1] + EPS, ("the lane check cannot see the fixed floor that ran the last lane's card past "
                                     f"the group's border ({base:.0f} px of card for a lane of {widths[-1]:.0f} px)")
