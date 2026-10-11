"""Hub board cards: an open blocker is named on the card; a closed one is not (#1506).

Also covers the docked strip's column widths: a visible column with no cards
collapses narrow so the populated columns take the freed space (#1791).
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
    assert not re.search(r"\d+px", _rule(hub, "body.dockb #bbody>.col.empty")), \
        "the collapse rule carries a magic number instead of the named constant"


def test_the_populated_columns_keep_their_proportions_when_one_collapses(hub):
    assert "flex:1 1 0" in _rule(hub, "body.dockb #bbody>.col")
    assert "flex:1.15 1 0" in _rule(hub, "body.dockb #bbody>.col.prs")     # the PR column's extra weight
    assert "flex:0 0 var(--col-empty-w)" in _rule(hub, "body.dockb #bbody>.col.empty")


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
    poll = page[page.index("async function pollBoard()"): page.index("const col=")]
    assert "#bbody" in poll and "location.reload" not in poll
    assert re.search(r"setInterval\(pollBoard,", page), "a card moving or a run ending must repaint the strip on its own"
