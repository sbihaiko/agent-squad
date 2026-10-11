"""Hub board cards: an open blocker is named on the card; a closed one is not (#1506).

Also covers the docked strip's column widths: a visible column with no cards
collapses narrow so the populated columns take the freed space (#1791).

And one colour per board column (#1793): the table below is the decision, so
the tests read it as a literal and measure every entry against the chip, rather
than restating whatever the page happens to hold.
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


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_blockers", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _item(n, title, status, blockers=None, state="open"):
    it = {"n": n, "t": title, "s": state, "st": status, "l": ["gui"]}
    if blockers is not None:
        it["b"] = blockers
    return it


def test_card_with_open_blocker_exposes_blocked_by_numbers(hub):
    hub.board_cache["items"] = [
        _item(1506, "Hub: a board card shows the issues blocking it", "Blocked", [1496]),
        _item(1500, "already unblocked", "Todo", []),
        _item(1496, "the blocker", "In Progress"),
    ]
    data = hub.board()
    blocked = next(x for g in data["groups"] if g["name"] == "Blocked" for x in g["items"] if x["n"] == 1506)
    clear = next(x for g in data["groups"] if g["name"] == "Todo" for x in g["items"] if x["n"] == 1500)
    assert blocked["b"] == [1496]
    assert not clear.get("b")


def test_closed_blocker_is_dropped_so_the_card_has_no_row(hub):
    nodes = [
        {"number": 1506, "blockedBy": {"nodes": [{"number": 1496, "state": "CLOSED"}]}},
        {"number": 1507, "blockedBy": {"nodes": [{"number": 1498, "state": "OPEN"}]}},
        {"number": 1508, "blockedBy": {"nodes": []}},
    ]
    items = [_item(1506, "was blocked", "Todo"), _item(1507, "still blocked", "Blocked"), _item(1508, "never blocked", "Todo")]
    hub.apply_open_blockers(items, nodes)
    by = {i["n"]: i for i in items}
    assert "b" not in by[1506] or by[1506]["b"] == []
    assert by[1507]["b"] == [1498]
    assert "b" not in by[1508] or by[1508]["b"] == []


def test_page_renders_blocked_by_links_and_omits_an_empty_row(hub):
    page = hub.PAGE
    cards = page[page.index("const cards="): page.index("const vis=")]
    assert "blocked by" in cards
    assert 'href="https://github.com/__REPO__/issues/${' in cards or 'href="https://github.com/__REPO__/issues/' in cards
    assert "x.b" in cards
    assert re.search(r"x\.b\s*&&\s*x\.b\.length|x\.b\|\|\[\]\)\.length", cards)


def test_blocker_line_stays_inside_a_300px_card(hub):
    css = hub.PAGE[hub.PAGE.index("<style>"): hub.PAGE.index("</style>")]
    blk = re.search(r"#bbody \.it \.blk\{[^}]+\}", css)
    assert blk, "card blocker line (.blk) missing"
    rule = blk.group(0)
    assert "overflow:hidden" in rule and "text-overflow:ellipsis" in rule
    assert "white-space:nowrap" in rule
    assert "#side{width:300px" in css


def _css(hub):
    return hub.PAGE[hub.PAGE.index("<style>"): hub.PAGE.index("</style>")]


def _rule(hub, selector):
    """The declarations of one CSS rule, read off the page the hub serves."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", _css(hub))
    assert m, "rule %s is missing from the docked board's CSS" % selector
    return m.group(1)


def _strip(hub):
    """The client-side render of the docked strip: from the visible columns to the painted HTML."""
    page = hub.PAGE
    return page[page.index("const vis="): page.index("const html=")]


def _col_class(hub, pr, cards):
    """The class the board really puts on a column, run by node from the page's own function."""
    js = re.search(r"^const colCls=.*$", hub.PAGE, re.M)
    assert js, "the board has no column-class function (colCls) to mark an empty column"
    script = "%s\nconsole.log(colCls(%s, %s))" % (js.group(0), json.dumps(pr), json.dumps(cards))
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_column_with_no_cards_collapses_and_a_populated_one_does_not(hub):
    assert "empty" in _col_class(hub, False, 0)
    assert "empty" not in _col_class(hub, False, 3)
    assert "empty" in _col_class(hub, True, 0)      # the PR column collapses too when it has no PRs
    assert "empty" not in _col_class(hub, True, 4)
    assert "prs" in _col_class(hub, True, 0)        # and stays a PR column while it is narrow


def test_the_collapse_reads_the_count_left_by_the_label_filter(hub):
    assert re.search(r"colCls\(isPR\(g\.name\),\s*it\.length\)", _strip(hub)), \
        "the collapse must read the filtered list (it), not the unfiltered one (g.items)"


def test_the_collapsed_width_is_a_named_constant(hub):
    css = _css(hub)
    cfg = re.search(r":root\{[^}]*--col-w:(\d+)px[^}]*--col-empty-w:(\d+)px[^}]*\}", css)
    assert cfg, "the strip's column widths need one config block naming them (--col-w, --col-empty-w)"
    assert css.count("--col-empty-w:") == 1, "the narrow width is declared more than once"
    assert "flex:0 0 var(--col-empty-w)" in _rule(hub, "body.dockb #bbody>.col.empty")
    assert not re.search(r"(?:flex|min-width|max-width|width):[^;]*\d+px", _rule(hub, "body.dockb #bbody>.col.empty")), \
        "the collapse rule carries a magic number instead of the named constant"


def test_the_populated_columns_keep_their_proportions_when_one_collapses(hub):
    assert "flex:1 1 0" in _rule(hub, "body.dockb #bbody>.col")
    assert "flex:1.15 1 0" in _rule(hub, "body.dockb #bbody>.col.prs")     # the PR column's extra weight
    assert "flex:0 0 var(--col-empty-w)" in _rule(hub, "body.dockb #bbody>.col.empty")
    css = _css(hub)
    assert css.index("body.dockb #bbody>.col.empty{") > css.index("body.dockb #bbody>.col.prcol{"), \
        "an empty PR column has to collapse too: the collapse rule must come after the PR column's weight"


def test_the_narrow_column_keeps_its_header_readable_and_its_border(hub):
    empty = _rule(hub, "body.dockb #bbody>.col.empty")
    assert "border-right:1px solid var(--line)" in empty, "the narrow column lost its separating border"
    ch = _rule(hub, "body.dockb #bbody>.col.empty>.ch")
    assert "flex-wrap:wrap" in ch, "the name and the count must fall onto two rows instead of clipping"
    assert "overflow:hidden" not in ch and "text-overflow:ellipsis" not in ch
    css = _css(hub)
    assert not re.search(r"\.col\.empty[^{]*\.n\{[^}]*display:none", css), "the count is hidden"
    assert not re.search(r"\.col\.empty[^{]*\.eye\{[^}]*display:none", css), "the eye is hidden"
    w = int(re.search(r"--col-empty-w:(\d+)px", css).group(1))
    assert w >= 110, "narrower than the widest header token (OPEN PRS is 8 chars at 13px monospace)"


def test_a_strip_of_only_empty_columns_stays_narrow_and_is_never_squeezed_to_zero(hub):
    empty = _rule(hub, "body.dockb #bbody>.col.empty")
    assert "min-width:var(--col-empty-w)" in empty and "max-width:var(--col-empty-w)" in empty
    css = _css(hub)
    narrow = int(re.search(r"--col-empty-w:(\d+)px", css).group(1))
    full = int(re.search(r"--col-w:(\d+)px", css).group(1))
    assert 0 < narrow < full, "collapsing must free width, so it can never add a scrollbar the populated strip did not have"
    assert "overflow-x:auto" in _rule(hub, "body.dockb #bbody")


def test_a_column_hidden_by_the_eye_is_absent_from_the_strip_rather_than_narrow(hub):
    assert re.search(r"const vis=all\.filter\(g=>!bottom\|\|!hid\.has\(g\.name\)\)", hub.PAGE)
    assert "vis.map(one)" in hub.PAGE        # only visible columns are ever laid out


def test_a_filter_change_recomputes_the_collapse_without_a_reload(hub):
    page = hub.PAGE
    handler = re.search(r'\$\("#lfl"\)\.onchange=[^\n]*', page).group(0)
    assert "pollBoard()" in handler and "location.reload" not in handler
    poll = page[page.index("async function pollBoard()"): page.index("finally{lim.busy=0}}")]
    assert 'if($("#bbody").dataset.h!==html)' in poll and "location.reload" not in poll
    assert re.search(r"setInterval\(pollBoard,", page), "a card moving or a run ending must repaint the strip on its own"


# ---- one colour per board column (#1793) -----------------------------------------------------------

# The decision, not a starting point: the seven Project statuses and the two PR columns, each with the
# one colour the column header's dot, the card's left border and the hidden: strip's number all wear.
NAMED = {
    "No status": "#f5b041", "Todo": "#c9d1d9", "In Progress": "#e8d44d", "Code Complete": "#b087f9",
    "Testing": "#2dd4bf", "Blocked": "#ff6b6b", "Done": "#3ecf8e", "Open PRs": "#5aa9ff", "Closed PRs": "#8b9bb3",
}
RING = ["#a3e635", "#d8b4fe", "#ff9ecd", "#7dd3fc"]   # reserved for the columns the Project gained later
CHIP_BG = "#2a303b"        # the chip the numbers sit on: what every column colour is measured against
CHIP_LABEL = "#939cab"     # the name beside the number inside a chip


def _board_js(hub):
    """The board's own script, from the colour table to the end of one paint."""
    page = hub.PAGE
    return page[page.index("const COLORS="): page.index("}catch(e){}finally{lim.busy=0}}")]


def _col(hub, expr):
    """An expression evaluated against the page's own colour table, run by node."""
    src = "\n".join(re.search(rf"^const {n}=.*$", hub.PAGE, re.M).group(0) for n in ("COLORS", "RING", "colOf"))
    out = subprocess.run(["node", "-"], input=src + f"\nconsole.log(JSON.stringify({expr}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _mute(hub):
    return re.search(r"--mute:(#[0-9a-f]{6})", _css(hub)).group(1)


def _lum(hexv):
    """WCAG relative luminance of a #rrggbb colour."""
    chan = [int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chan]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_the_dot_the_card_border_and_the_strip_read_one_resolution_point(hub):
    board = _board_js(hub)
    assert board.count("const COLORS=") == 1, "the colour table is declared more than once"
    assert re.search(r"background:\$\{colOf\(g\.name,\w+\)\}", board), \
        "the column header's dot must ask colOf for its colour"
    assert re.search(r"--cc:\$\{colOf\(g\.name,\w+\)\}", board), \
        "the card's left border must ask colOf for its colour"
    assert re.search(r'<b style="color:\$\{colOf\(g\.name,\w+\)\}"', board), \
        "the hidden: strip's number must ask colOf for its colour"
    cols = re.search(r"--cc:\$\{colOf\(g\.name,(\w+)\)\}", board).group(1)
    assert board.count('style="--cc:${colOf(g.name,' + cols + ')}"') == 2, \
        "both layouts put the column's colour on the column itself, for the PR chrome to read"
    assert not re.search(r"col\[", board), "a second map of column colours is one waiting to drift from this one"
    assert '||"var(--mute)"' not in board, "an unnamed column drawn in the colour of a caption"


def test_the_pr_columns_wear_their_own_colour_not_the_run_token(hub):
    css = _css(hub)
    pr = css[css.index(".prs{"): css.index("body.dockb #bbody>.col.empty")]
    assert "var(--run)" not in pr, "the PR columns still borrow the run state's teal: the archive looks like the live queue"
    assert "2dd4bf" not in pr, "a second literal for a column colour, next to the map that decides it"
    assert "border-left:4px solid var(--cc)" in _rule(hub, "#bbody .prs .it"), \
        "a PR card's left border is the column's colour like every other card's"
    for rule in ("#bbody .acc.prs", "#bbody .prs>.ah,#bbody .prs>.ch", "body.dockb #bbody>.col.prs",
                 "body.dockb #bbody>.col.prcol"):
        assert "var(--cc)" in _rule(hub, rule), f"{rule} does not read the column's colour"


def test_no_column_colour_is_written_a_second_time_in_the_board_script(hub):
    board = _board_js(hub)
    for value in list(NAMED.values()) + RING:
        assert board.count(value) == 1, f"{value} appears {board.count(value)} times in the board's script"


def test_the_named_columns_carry_exactly_the_decided_colours(hub):
    got = _col(hub, "Object.fromEntries(Object.keys(COLORS).map(n=>[n,colOf(n,[])]))")
    assert got == NAMED, "the nine named columns carry colours the table does not"
    assert got["Open PRs"] != got["Closed PRs"], "the archive must not look like the live queue"
    assert _mute(hub) not in (got["Open PRs"], got["Closed PRs"]), "a muted PR column is a caption, not a column"


def test_the_pr_pair_keeps_the_blue_and_loses_the_chroma(hub):
    open_pr, closed_pr = _col(hub, 'colOf("Open PRs",[])'), _col(hub, 'colOf("Closed PRs",[])')
    def hue(h):
        return [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    live, retired = hue(open_pr), hue(closed_pr)
    assert live[2] > live[0], "an open PR is the bluest of the pair"
    assert retired[2] > retired[0] and max(retired) - min(retired) < max(live) - min(live), \
        "a closed PR is the same object as an open one, retired: same hue, less chroma"


def test_a_user_column_takes_the_ring_colour_its_alphabetical_position_gives_it(hub):
    cols = ["Stale", "Waiting on review"]
    assert _col(hub, f'colOf("Stale",{json.dumps(cols)})') == RING[0]
    assert _col(hub, f'colOf("Waiting on review",{json.dumps(cols)})') == RING[1]
    assert _col(hub, f'colOf("Stale",{json.dumps(list(reversed(cols)))})') == RING[0], \
        "the order the caller passes the columns in must not decide their colours"
    assert _col(hub, f'colOf("Stale",{json.dumps(["Testing"] + cols)})') == RING[0], \
        "a named column in the list is not a user column and takes no ring slot"


def test_the_ring_cycles_and_stays_reserved(hub):
    assert _col(hub, "RING") == RING, "the reserved ring changed shape"
    five = ["Aaa", "Bbb", "Ccc", "Ddd", "Eee"]
    assert _col(hub, f'colOf("Eee",{json.dumps(five)})') == RING[0], "the ring must cycle"
    assert not set(_col(hub, "RING")) & set(NAMED.values()), "a ring colour is a named column's colour"
    assert _mute(hub) not in _col(hub, "RING"), "--mute is never a column colour"


def test_a_column_added_later_leaves_the_ones_already_there_alone(hub):
    before, after = ["Alpha", "Beta"], ["Alpha", "Beta", "Zulu"]
    was = [_col(hub, f"colOf({json.dumps(n)},{json.dumps(before)})") for n in before]
    now = [_col(hub, f"colOf({json.dumps(n)},{json.dumps(after)})") for n in before]
    assert now == was, "a column added later restyled the ones already there"


def test_every_column_colour_reaches_aa_against_the_chip(hub):
    colours = _col(hub, "Object.values(COLORS)") + _col(hub, "RING") + [CHIP_LABEL]
    for c in colours:
        assert _ratio(c, CHIP_BG) >= 4.5, f"{c} is {_ratio(c, CHIP_BG):.2f}:1 on the chip: under AA"


def test_the_chip_label_uses_the_readable_mute(hub):
    chip = _rule(hub, ".hidbar .chip")
    assert "background:" + CHIP_BG in chip, "the measurement's background is not the chip's own"
    assert f"color:{CHIP_LABEL}" in chip, "the name inside a chip must be readable at 10px"
    assert _ratio(CHIP_LABEL, CHIP_BG) >= 4.5 > _ratio(_mute(hub), CHIP_BG), \
        "the chip's label moved off --mute because --mute is under AA"
