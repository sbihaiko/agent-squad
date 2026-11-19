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
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB_TOKENS = ROOT / "contrib/squad-goal/scripts/web-tokens.css"
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
    blocked = next(x for g in data["groups"] if g["name"] == "Backlog" for x in g["items"] if x["n"] == 1506)   # Blocked is the owner freeze over Backlog (#247)
    assert blocked["fz"] == "frozen:owner"
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
    cards = page[page.index("const cards="): page.index("const fits=")]
    assert "blocked by" in cards
    assert 'href="https://github.com/__REPO__/issues/${' in cards or 'href="https://github.com/__REPO__/issues/' in cards
    assert "x.b" in cards
    assert re.search(r"x\.b\s*&&\s*x\.b\.length|x\.b\|\|\[\]\)\.length", cards)


CARD_STUBS = """
const esc = s => String(s);
const colOf = () => "#000";
const CN = {};
const SLOTS = new Map();
const cardColor = () => "var(--mute)";
const loopBugLabel = x => (x.l || []).find(l => l === "loop-bug" || String(l).startsWith("loop-bug:")) || null;
const tint = () => "";
const gstrip = () => "";
const age = () => "";
const ageC = () => "";
const bo = {labels: []};
"""


def _card_html(hub, card):
    """One card drawn by the page's own renderer, run by node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required")
    src = re.search(r"^const cards=.*$", hub.PAGE, re.M).group(0)
    script = CARD_STUBS + src + "\nconsole.log(JSON.stringify(cards({name: 'Todo'},"
    script += f"[{json.dumps(card)}],[{json.dumps(card)}])));"
    out = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_hub_card_shows_its_open_blockers(hub):
    """SPEC 1965, #1988: a hub card names the open blockers its row carries, with a link to each."""
    html = _card_html(hub, {"n": 1506, "t": "blocked", "l": [], "b": [1496, 1497]})
    assert "blocked by" in html, html
    assert "#1496" in html and "#1497" in html, html


def test_a_hub_card_with_no_open_blocker_shows_no_line(hub):
    html = _card_html(hub, {"n": 1500, "t": "clear", "l": []})
    assert "blocked by" not in html, html


def test_blocker_line_stays_inside_a_300px_card(hub):
    css = hub.PAGE[hub.PAGE.index("<style>"): hub.PAGE.index("</style>")]
    blk = re.search(r"#bbody \.it \.blk\{[^}]+\}", css)
    assert blk, "card blocker line (.blk) missing"
    rule = blk.group(0)
    assert "overflow:hidden" in rule and "text-overflow:ellipsis" in rule
    assert "white-space:nowrap" in rule
    assert "#side{width:280px" in css   # re-anchored to the owner-approved mock (#889): the sidebar is 280 px


def _css(hub):
    return WEB_TOKENS.read_text() + hub.PAGE[hub.PAGE.index("<style>"): hub.PAGE.index("</style>")]


def _rule(hub, selector):
    """The declarations of one CSS rule, read off the page the hub serves."""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", _css(hub))
    assert m, f"rule {selector} is missing from the docked board's CSS"
    return m.group(1)


def _strip(hub):
    """The client-side render of the docked strip: from the derived sets to the painted HTML."""
    page = hub.PAGE
    return page[page.index("const kept="): page.index("const html=")]


def test_the_docked_columns_read_their_width_out_of_one_config_block(hub):
    css = _css(hub)
    assert re.search(r":root\{[^}]*--col-w:(\d+)px[^}]*\}", css), "the docked column's width is a named constant (#1791)"
    assert "col-empty" not in css, "an empty column reserves no width of its own: it is not drawn at all (#1809)"
    rule = _rule(hub, "body.dockb #bbody>.col")
    assert "flex:1 1 0" in rule and "min-width:var(--col-w)" in rule
    assert not re.search(r"(?:flex|min-width|max-width|width):[^;]*\d+px", rule), \
        "the rule carries a magic number instead of the named constant"


def test_the_columns_that_hold_cards_keep_their_proportions(hub):
    assert "flex:1 1 0" in _rule(hub, "body.dockb #bbody>.col")
    assert "flex:1.15 1 0" in _rule(hub, "body.dockb #bbody>.col.prs")     # the PR column's extra weight
    assert "flex:1.15 1 0" in _rule(hub, "body.dockb #bbody>.col.prcol")   # and the archive's
    assert "overflow-x:auto" in _rule(hub, "body.dockb #bbody")


def test_an_auto_hidden_column_reads_the_count_left_by_the_label_filter(hub):
    assert re.search(r"const fits=\{\};all\.forEach\(g=>fits\[g\.name\]=", hub.PAGE)
    assert "fits[g.name].length" in _strip(hub), "the collapse reads the filtered list, not the unfiltered one"


def test_a_column_hidden_by_the_eye_keeps_its_chip_and_never_gets_a_line(hub):
    assert re.search(r"vis=all\.filter\(g=>!g\.end&&\(!bottom\|\|\(!hid\.has\(g\.name\)&&!autoHidden\(g\)\)\)\)", hub.PAGE)
    assert "vis.map(one)" in hub.PAGE        # only the columns that are drawn are laid out
    assert re.search(r"autoHidden=g=>!hid\.has\(g\.name\)", hub.PAGE), \
        "a column the user hid is a chip, never one of the strip's lines (#1809)"


def test_a_board_change_recomputes_the_collapse_without_a_reload(hub):
    """#899: the board repaints from one poll, which recomputes the lanes and the collapse at the bottom of the page; no
    step reloads the page. The filter bar's own handler left the page (SPEC 1965), so the poll is the one road it takes."""
    page = hub.PAGE
    poll = page[page.index("async function pollBoard()"): page.index("finally{lim.busy=0}}")]
    assert 'if($("#bbody").dataset.h!==html)' in poll and "location.reload" not in poll
    assert re.search(r"setInterval\(pollBoard,", page), "a card moving or a run ending must repaint the strip on its own"


# ---- one colour per board column (#1793) -----------------------------------------------------------

# The decision, not a starting point: the seven shipped statuses and the one open-PR lane (#247, #384, #440), each with
# the one colour the column header's dot, the card's left border and the hidden: strip's number all wear.
NAMED = {
    "Backlog": "#90a4ae", "Todo": "#14b8c0", "Doing": "#e8d44d", "Reviewing": "#ff8a5c", "Batching": "#f5b041",
    "Testing": "#f48fb1", "Closed": "#3ecf8e", "Abandoned": "#ff6b6b", "Open PRs": "#5aa9ff",
}
RING = ["#9ccc65", "#d88cd0", "#b99126", "#2cbcd8"]   # reserved for the columns the Project gained later
CHIP_BG = "#2a303b"        # the chip the numbers sit on: what every column colour is measured against


def _board_js(hub):
    """The board's own script, from the colour table to the end of one paint."""
    page = hub.PAGE
    return page[page.index("const COLORS="): page.index("}catch(e){}finally{lim.busy=0}}")]


def _token(hub, name):
    """The hex behind one of the custom properties the page reads from the shared token block."""
    tokens = dict(re.findall(r"--([\w-]+):([^;}]+)", _css(hub)))
    value, seen = tokens[name.removeprefix("--")], set()
    while match := re.fullmatch(r"var\(--([\w-]+)\)", value):
        ref = match.group(1)
        assert ref not in seen, f"a token cycle at --{ref}"
        seen.add(ref)
        assert ref in tokens, f"--{ref} is not declared by the page"
        value = tokens[ref]
    assert re.fullmatch(r"#[0-9a-f]{6}", value), f"--{name} does not resolve to a hex colour: {value}"
    return value


def _resolved(hub, value):
    """One value the page names as a role, resolved against the token block; a list or map resolves item by item."""
    if isinstance(value, str):
        m = re.fullmatch(r"var\((--[\w-]+)\)", value)
        return _token(hub, m.group(1)) if m else value
    if isinstance(value, list):
        return [_resolved(hub, v) for v in value]
    if isinstance(value, dict):
        return {k: _resolved(hub, v) for k, v in value.items()}
    return value


def _col(hub, expr):
    """An expression evaluated against the page's own colour table, run by node, then resolved to the token's value."""
    src = "\n".join(re.search(rf"^const {n}=.*$", hub.PAGE, re.M).group(0) for n in ("COLORS", "RING", "colOf"))
    out = subprocess.run(["node", "-"], input=src + f"\nconsole.log(JSON.stringify({expr}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return _resolved(hub, json.loads(out.stdout))


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
    assert re.search(r"^const hidchip=.*colOf\(g\.name,CN\)", board, re.M), \
        "the hidden: strip's number must ask colOf for the colour of a column the map does not name"
    cols = re.search(r"--cc:\$\{colOf\(g\.name,(\w+)\)\}", board).group(1)
    assert board.count('style="--cc:${colOf(g.name,' + cols + ')}"') == 3, \
        "both layouts put the column's colour on the column itself, for the PR chrome to read, and the hidden: " \
        "strip's line puts it on the tick that stands for the collapsed column (#1809)"
    assert not re.search(r"col\[", board), "a second map of column colours is one waiting to drift from this one"
    assert '||"var(--mute)"' not in board, "an unnamed column drawn in the colour of a caption"


def test_the_pr_columns_wear_their_own_colour_not_the_run_token(hub):
    css = _css(hub)
    pr = css[css.index(".prs{"): css.index("body.dockb .sec{")]
    assert "var(--run)" not in pr, "the PR columns still borrow the run state's teal: the archive looks like the live queue"
    assert "2dd4bf" not in pr, "a second literal for a column colour, next to the map that decides it"
    assert "var(--cc)" not in _rule(hub, "#bbody .prs .it").split("background")[0], \
        "no card carries the column's colour as a border: its lines are its spec's or grey (#917)"
    for rule in ("#bbody .acc.prs", "#bbody .prs>.ah,#bbody .prs>.ch", "body.dockb #bbody>.col.prs",
                 "body.dockb #bbody>.col.prcol"):
        assert "var(--cc)" in _rule(hub, rule), f"{rule} does not read the column's colour"


def test_no_column_colour_is_written_a_second_time_in_the_board_script(hub):
    board = _board_js(hub)
    table = json.loads(re.search(r"const COLORS=(.*?);", board).group(1))
    ring = json.loads(re.search(r"const RING=(\[.*?\]);", board).group(1))
    assert len(set(table.values())) == len(table), "two columns share a colour"
    assert not set(ring) & set(table.values()), "a ring colour is a named column's colour"
    assert not re.search(r"(?<!&)#[0-9a-fA-F]{3,8}\b", re.sub(r"//[^\n]*", "", board)), \
        "a colour literal survives in the board's script: the map no longer names it"


def test_the_named_columns_carry_exactly_the_decided_colours(hub):
    got = _col(hub, "Object.fromEntries(Object.keys(COLORS).map(n=>[n,colOf(n,[])]))")
    assert got == NAMED, "the named columns and lane carry colours the table does not"
    assert len(NAMED) == 9 and got["Open PRs"] == "#5aa9ff", "the open-PR lane is the live queue's own blue"
    assert _mute(hub) not in got.values(), "a muted column is a caption, not a column"


def test_the_open_pr_lane_keeps_the_blue_the_table_gives_it(hub):
    lane = _col(hub, 'colOf("Open PRs",[])')

    assert lane == NAMED["Open PRs"], "the lane wears a colour of its own, not the table's"
    assert int(lane[5:7], 16) > int(lane[1:3], 16), "the lane is a blue: it is the live queue"


def test_a_user_column_takes_the_ring_colour_its_alphabetical_position_gives_it(hub):
    cols = ["Stale", "Waiting on review"]
    assert _col(hub, f'colOf("Stale",{json.dumps(cols)})') == RING[0]
    assert _col(hub, f'colOf("Waiting on review",{json.dumps(cols)})') == RING[1]
    assert _col(hub, f'colOf("Stale",{json.dumps(list(reversed(cols)))})') == RING[0], \
        "the order the caller passes the columns in must not decide their colours"
    assert _col(hub, f'colOf("Stale",{json.dumps(["Doing"] + cols)})') == RING[0], \
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


def test_a_ring_colour_never_matches_a_named_column_colour(hub):
    """Testing took the ring's pink, so that slot moved to amber (#1254). A named column that later lands on a ring colour
    would restyle a user column silently: this guard names the collision where it happens."""
    ring, named = _col(hub, "RING"), _col(hub, "Object.values(COLORS)")

    assert not set(ring) & set(named), "a ring colour is a named column's colour"


def test_every_column_colour_reaches_aa_against_the_chip(hub):
    colours = _col(hub, "Object.values(COLORS)") + _col(hub, "RING")
    for c in colours:
        assert _ratio(c, CHIP_BG) >= 4.5, f"{c} is {_ratio(c, CHIP_BG):.2f}:1 on the chip: under AA"


def test_the_number_is_the_readable_thing_on_the_chip(hub):
    """#1792 supersedes the caption's AA rule: the name is a 10px caption in --mute, and the number
    is the one thing the chip has to make readable. The colours the numbers wear are measured below."""
    chip = _rule(hub, ".hidbar .chip")
    assert _token(hub, "--line") == CHIP_BG, "the chip's background role changed: re-measure it"
    assert "background:var(--line)" in chip, "the measurement's background is not the chip's own"
    assert _ratio(_token(hub, "--fg"), CHIP_BG) >= 4.5, "the chip's names are under AA on the chip"
    for name in ("--err", "--run", "--warn", "--ok", "--acc"):
        assert _ratio(_token(hub, name), CHIP_BG) >= 4.5, f"a number in {name} is under AA on the chip"


# ---- the hidden: strip: a stack of full-height tiles (#1792) ----------------------------------------

STRIP_W = 120        # the strip's outer width: what it is today, and what this slice must not move
CHIP_PAD = 2         # the issue's own arithmetic: name (11px) + number (20px) + 2px + 2px = 35px
ROW_FLOOR = 35       # below this a row is shorter than its content, and the name is clipped first


def _px(decls, prop):
    """One px length declared in a rule, as an int: what the browser will actually lay out."""
    m = re.search(rf"(?:^|;){re.escape(prop)}:(\d+)px", decls)
    assert m, f"{prop} is not declared in {decls!r}"
    return int(m.group(1))


def _js_value(hub, fn, args):
    """One value the board's own script computes, run by node against the page's own code."""
    src = "\n".join(re.search(rf"^const {n}=.*$", hub.PAGE, re.M).group(0)
                    for n in ("COLORS", "RING", "colOf", "fmtN", "hidchip"))
    script = f"const esc=s=>s;const CN={json.dumps(args.get('names', []))};\n{src}\nconsole.log({fn})"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def _fmt(hub, n):
    return _js_value(hub, f"fmtN({n})", {})


def _chip(hub, name, n, names=None):
    return _js_value(hub, f"hidchip({{name:{json.dumps(name)},n:{n}}})", {"names": names or []})


def test_each_row_stacks_the_name_over_the_number_and_centres_both(hub):
    chip = _rule(hub, ".hidbar .chip")
    assert "flex-direction:column" in chip, "the name and the number are still side by side"
    assert "justify-content:space-between" not in chip, "a row spread across the chip is a row, not a tile"
    assert "align-items:center" in chip and "text-align:center" in chip, "the row is not centred"
    assert "<span class=lbl>" in _chip(hub, "Abandoned", 2).split("<b")[0], \
        "the name must come before the number in the chip"


def test_the_rows_divide_the_strip_equally_and_fill_the_board_s_height(hub):
    bar, chip = _rule(hub, ".hidbar"), _rule(hub, ".hidbar .chip")
    assert "align-self:flex-start" not in bar, "a strip hugging its content at the top leaves the rows short"
    assert "align-self:stretch" in bar and "min-height:0" in bar, "the strip must take the board's height"
    assert "flex:1 1 0" in chip and "min-height:0" in chip, "the rows do not share the height equally"
    gap = _px(bar, "gap")
    vert = 2 * int(re.search(r"padding:(\d+)px", bar).group(1))     # padding:8px 10px, top and bottom
    board = 340                                                    # a docked board's height
    def row(n):
        return (board - vert - gap * (n - 1)) / n
    for n in (2, 8):
        assert row(n) * n + gap * (n - 1) + vert == board, f"{n} rows leave a gap at the bottom"
    assert row(2) == pytest.approx(board / 2 - 8 - gap / 2), "two rows are half the strip each"
    assert row(8) == pytest.approx((board - 16 - 7 * gap) / 8), "eight rows are an eighth each"
    assert row(8) >= ROW_FLOOR, "eight rows are shorter than a name and a number, so a name would be cut"


def test_the_strip_is_still_the_last_child_of_the_docked_board_row(hub):
    assert re.search(r'const html=vis\.map\(one\)\.join\(""\)\+\(bottom&&\(hid\.size\|\|autos\.length\|\|endLines\)\?`<div class=hidbar>', hub.PAGE), \
        "the hidden: strip is appended after every visible column, as the row's last child"
    bar = _rule(hub, ".hidbar")
    assert "order:" not in bar and "float" not in bar and "position:absolute" not in bar, \
        "the strip's inside changed; where it sits in the row did not"


def test_the_strip_width_is_pinned_in_both_directions_and_never_scrolls(hub):
    bar, chip = _rule(hub, ".hidbar"), _rule(hub, ".hidbar .chip")
    assert f"min-width:{STRIP_W}px" in bar and f"max-width:{STRIP_W}px" in bar, \
        "the width is not pinned in both directions"
    assert "*{box-sizing:border-box}" in _css(hub), "the pinned width is measured against a content box"
    assert "overflow:hidden" in bar, "a long name can widen the strip"
    assert not re.search(r"overflow(-[xy])?:(auto|scroll)", bar), "the strip must not scroll"
    assert "min-width" not in chip and "width" not in chip, "the chip may not size the strip"
    assert "align-items" not in bar, "the chips stretch to the strip's content box, so they cannot resize it"
    assert "overflow:hidden" in chip, "a chip taller than its row has to clip, not push the strip open"
    lbl = _rule(hub, ".hidbar .chip .lbl")
    assert "max-width:100%" in lbl and "overflow:hidden" in lbl, "a long name must ellipsize inside the width"


def test_the_name_is_the_caption_and_the_number_is_never_the_clipped_one(hub):
    lbl, num = _rule(hub, ".hidbar .chip .lbl"), _rule(hub, ".hidbar .chip b")
    assert "font-size:var(--fs-caption)" in lbl and "line-height:16px" in lbl, "the name is not the 13px floor with a 16px line"
    assert "color:var(--fg)" in lbl, "the name wears the text colour, which clears AA on the chip"
    assert "text-overflow:ellipsis" in lbl and "white-space:nowrap" in lbl, \
        "a long name ellipsizes; it never wraps and never shrinks the font"
    assert "flex:0 1 auto" in lbl and "flex:none" in num, \
        "the name shrinks first and is clipped away; the number is never the element that gets cut"
    assert "font-size:var(--fs-display)" in num and "line-height:20px" in num and "font-weight:600" in num, \
        "the number is the one big thing in the strip"
    assert "font-variant-numeric:tabular-nums" in num, "two counts in a stack must line up"


def test_the_chip_names_the_column_and_the_count_it_stands_for(hub):
    chip = _chip(hub, "Abandoned", 728)
    assert 'class=chip' in chip and 'data-eye="Abandoned"' in chip, "the chip still toggles its column"
    assert 'data-col="Abandoned"' in chip, "the chip does not say which column it counts"
    assert 'title="Abandoned — 728"' in chip, "the full name is not one hover away"
    assert "<span class=lbl>Abandoned</span><b>728</b>" in chip, "the number is not stacked under its name"


def test_counts_of_a_thousand_and_over_carry_a_thousands_separator(hub):
    for n, want in ((0, "0"), (27, "27"), (688, "688"), (999, "999"),
                    (1000, "1,000"), (12345, "12,345")):
        assert _fmt(hub, n) == want, f"{n} renders as {_fmt(hub, n)}"
    assert 'title="Closed — 1,234"' in _chip(hub, "Closed", 1234), \
        "the hover text reads the count the same way the number does"


# ---- the strip's numbers are coloured by category, not by magnitude (#1792) --------------------------

# The decision, not a starting point: the strip's own mapping from column to category, with the hexes
# #1793 owns. Magnitude would light up the two archival piles (688, 522) and bury the Abandoned 2.
HID_COL = {
    "Abandoned": "#ff6b6b",      # --err
    "Backlog": "#f5b041",        # --warn
    "Closed": "#3ecf8e",         # --ok
}


def _hid_rule(hub, name):
    """The colour a category's rule paints its number with, resolved through the page's own tokens."""
    decls = _rule(hub, f'.hidbar .chip[data-col="{name}"] b')
    tokens = dict(re.findall(r"--([a-z-]+):(#[0-9a-f]{6})", re.search(r":root\{[^}]*\}", _css(hub)).group(0)))
    m = re.fullmatch(r"color:(var\(--([a-z-]+)\)|#[0-9a-f]{6})", decls)
    assert m, f"the rule for {name} does not decide the number's colour in one word: {decls!r}"
    return tokens[m.group(2)] if m.group(2) else m.group(1)


def test_every_number_is_painted_by_its_category_and_never_as_a_caption(hub):
    for name, want in HID_COL.items():
        assert _hid_rule(hub, name) == want, f"{name} is painted {_hid_rule(hub, name)}, not {want}"
    fallback = _rule(hub, ".hidbar .chip b")
    assert re.search(r"color:var\(--acc\)", fallback), \
        "a column the table does not name must fall back to --acc"
    assert _token(hub, "--acc") == NAMED["Open PRs"], "the lane's blue and the fallback are not one colour"
    for name in list(HID_COL) + [""]:
        sel = f'.hidbar .chip[data-col="{name}"] b' if name else ".hidbar .chip b"
        assert "var(--mute)" not in _rule(hub, sel), f"{name or 'the fallback'} draws a number in the caption colour"


def test_a_category_is_not_a_magnitude(hub):
    """The closed pile is the biggest number on the board and must not be the loudest."""
    counts = {"Backlog": 688, "Closed": 522, "Abandoned": 2}
    loud = [_hid_rule(hub, c) for c in counts]
    assert len(set(loud)) == len(counts), "two categories share a colour, so the colour says nothing"
    assert _hid_rule(hub, "Abandoned") != _hid_rule(hub, "Backlog"), \
        "the pile nobody is reading and the one that wants eyes wear the same colour"
    for c, n in counts.items():
        assert re.search(r"data-col", _chip(hub, c, n)), "the chip carries no data-col, so no rule can reach it"


def test_a_column_the_map_does_not_name_keeps_the_colour_its_header_dot_wears(hub):
    names = ["Stale", "Waiting on review"]
    assert _col(hub, f'colOf("Stale",{json.dumps(names)})') == RING[0]
    chip = _chip(hub, "Stale", 5, names)
    inline = re.search(r'style="color:([^"]+)"', chip)
    assert inline and _resolved(hub, inline.group(1)) == RING[0], \
        "a user column's number must take the ring colour, which only colOf can give it"
    assert "color:" not in _chip(hub, "Abandoned", 2), \
        "a category with a rule of its own must not also carry an inline colour: that is the second copy"


def test_the_named_columns_still_read_the_one_map(hub):
    """The strip is #1793's third reader: header dot, card border, number — one resolution point."""
    board = _board_js(hub)
    assert re.search(r'<b\$\{ring\?` style="color:\$\{c\}"`', board), \
        "the strip's number must ask colOf for the colour of a column the map does not name"
    assert "colOf(g.name,CN)" in re.search(r"^const hidchip=.*$", board, re.M).group(0), \
        "the strip resolves its colour somewhere other than the one map"
    assert re.search(r"const html=vis\.map\(one\)\.join\(\"\"\)\+\(bottom&&\(hid\.size\|\|autos\.length\|\|endLines\)\?`<div class=hidbar>\$\{all\.filter\(g=>!g\.end&&hid\.has\(g\.name\)\)\.map\(hidchip\)", board), \
        "the strip is not built from the one chip builder"
