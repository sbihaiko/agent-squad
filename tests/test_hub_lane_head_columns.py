"""A spec lane's header is a table read down the column (#1158).

The header's rows, facts and tracks are the rule layer's (`board-lanes.js`, ADR-0137): `laneHead` answers the rows of one
lane header, `laneHeadCols` answers the one track list every lane takes. These tests run that text in node, exactly as the
page carries it, and read the header the page draws. Column positions are asserted as the track each fact sits on in one
shared grid, not as measured pixels: no browser runs here.
"""
import importlib.util
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
NODE = shutil.which("node")
ROWS = [["id", "title", "chip", "gh", "sync"], ["bar", "frac", "counts"]]
SYNC_TITLE = "re-read only SPEC"

LANE_A = {"kind": "s", "num": 12, "title": "Short title", "closed": 2, "total": 3, "doing": 0, "review": 0,
          "st": "Todo", "counts": {"Todo": 5, "Doing": 1, "Code Review": 1}}
LANE_B = {"kind": "s", "num": 7, "title": "A much longer spec title that wraps across the title column",
          "closed": 0, "total": 7, "doing": 0, "review": 0, "st": "Backlog", "counts": {"Doing": 1}}
LANE_WAVE = {"kind": "sw", "num": 1158, "sid": "SPEC 1158", "wl": "Wave 2", "title": "SPEC 1158 · Wave 2",
             "closed": 0, "total": 0, "doing": 0, "review": 0, "st": "", "counts": {}}


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_lane_head")
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir()
    mp.setenv("SG_REPO", "x/y")
    mp.setenv("SG_OWNER", "x")
    mp.setenv("SG_PROJECT", "4")
    mp.setenv("SG_CHECKOUT", str(base / "checkout"))
    mp.setenv("SG_HUB_DIR", str(base / "hub"))
    mp.setenv("SG_STATE", str(base / "state"))
    try:
        spec = importlib.util.spec_from_file_location("hub_lane_head", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod.PAGE
    finally:
        mp.undo()


def _node(script):
    if NODE is None:
        pytest.skip("node is required")
    src = LANES_JS.read_text() + "\n" + script
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _header_rule(lanes):
    """The rows of each lane's header and the one track list, from the rule layer."""
    return _node(
        "const o={todo:\"Todo\",doing:\"Doing\",review:\"Code Review\"};"
        f"const lanes={json.dumps(lanes)};"
        "console.log(JSON.stringify({cols:laneHeadCols,headers:lanes.map(l=>laneHead(l,o))}));"
    )


def _drawn_headers(page, lanes):
    """The lane headers the page draws for these lanes, and the grid they sit on."""
    body = re.search(r"const laneHtml=m=>\{.*?\n return h\+\"</div>\"\};", page, re.S).group(0)
    stubs = ('const esc=s=>String(s),colOf=()=>"",CN={},eye=()=>"",SLOTS=new Map(),cards=()=>"",bo={},f="",'
             'BOARD_VOCAB={todo:"Todo",doing:"Doing",review:"Code Review"};')
    model = {"lanes": [dict(lane, collapsed=True, cells={}) for lane in lanes], "vc": [], "cols": []}
    html = _node(stubs + body + f"console.log(JSON.stringify(laneHtml({json.dumps(model)})));")
    grid = re.search(r'<div class=lgrid style="([^"]*)">', html)
    headers = re.findall(r'<header class=lh[^>]*>(.*?)</header>', html, re.S)
    return grid, headers


def _two_row_layout(header):
    """A drawn header read by layout row: row 1 holds the id, title and Sync facts, row 2 the bar, fraction and counts (#2026)."""
    assert "<span class=lh-r1>" in header and "<span class=lh-r2>" in header, header
    row1, row2 = header.split("<span class=lh-r2>", 1)
    row1 = row1.split("<span class=lh-r1>", 1)[1]
    keys = lambda s: re.findall(r'<span class="lf lf-(\w+)', s)  # noqa: E731
    return [keys(row1), keys(row2)]


def test_every_lane_header_takes_the_same_two_rows():
    out = _header_rule([LANE_A, LANE_B])
    for header in out["headers"]:
        assert [[c["k"] for c in row] for row in header["rows"]] == ROWS, header


def test_a_spec_lane_head_draws_in_two_rows(page):
    """#2026: a spec lane head takes two rows, not three, on a board 1100 px or wider."""
    _, headers = _drawn_headers(page, [LANE_A, LANE_B])
    assert len(headers) == 2, headers
    for header in headers:
        assert _two_row_layout(header) == [["id", "title", "chip", "gh", "sync"], ["bar", "frac", "counts"]], header


def test_a_zero_count_is_an_en_dash_and_the_three_counts_always_stand():
    out = _header_rule([LANE_A, LANE_B])
    items_a = out["headers"][0]["rows"][1][2]["items"]
    items_b = out["headers"][1]["rows"][1][2]["items"]
    assert items_a == [["Todo", "5"], ["Doing", "1"], ["Code Review", "1"]], items_a
    assert items_b == [["Todo", "–"], ["Doing", "1"], ["Code Review", "–"]], items_b


def test_the_fraction_and_the_chip_hold_their_own_text():
    out = _header_rule([LANE_A, LANE_B])
    cells = [{c["k"]: c for row in h["rows"] for c in row} for h in out["headers"]]
    assert cells[0]["frac"]["text"] == "2/3" and cells[1]["frac"]["text"] == "0/7"
    assert cells[0]["chip"]["text"] == "Todo" and cells[1]["chip"]["text"] == "Backlog"


def test_a_wave_lane_name_spans_the_whole_header_and_is_never_clipped(page):
    out = _header_rule([LANE_WAVE])
    row1 = out["headers"][0]["rows"][0]
    assert row1[0]["full"] is True and row1[1]["text"] == "", row1
    _, headers = _drawn_headers(page, [LANE_WAVE])
    assert "lf lf-id full" in headers[0], headers[0]
    assert "SPEC 1158</span> · Wave 2" in headers[0], headers[0]


def test_the_counts_draw_one_line_in_fixed_order_joined_by_a_middle_dot(page):
    _, headers = _drawn_headers(page, [LANE_A, LANE_B])
    counts = headers[1].split('<span class="lf lf-counts"', 1)[1]
    assert "<span>Todo <b>–</b></span> · <span>Doing <b>1</b></span> · <span>Code Review <b>–</b></span>" in counts, counts
    assert "class=cnt" not in headers[1]


def test_the_title_keeps_its_whole_text_in_a_tooltip(page):
    _, headers = _drawn_headers(page, [LANE_B])
    assert 'title="A much longer spec title that wraps across the title column"' in headers[0], headers[0]


def test_the_title_css_ends_a_long_title_in_an_ellipsis_on_one_line():
    css = LANES_CSS.read_text()
    rule = re.search(r"#bbody \.lh \.ltt\{[^}]*\}", css).group(0)
    assert "text-overflow:ellipsis" in rule and "white-space:nowrap" in rule, rule


def test_the_header_rows_set_no_fixed_gap_between_their_items():
    css = LANES_CSS.read_text()
    for selector in (r"#bbody \.lh\{[^}]*\}", r"#bbody \.lh \.lh-r1,#bbody \.lh \.lh-r2\{[^}]*\}"):
        rule = re.search(selector, css).group(0)
        assert "column-gap" not in rule and "gap:" not in rule.replace("row-gap:", ""), rule


def test_the_page_draws_every_header_from_the_rule_layer_on_one_grid(page):
    assert "laneHead(l," in page, "the header no longer takes its facts from the rule layer"
    assert "--lh-cols" not in page, "the header still sets a fixed column track list"
    assert "${cl?m.cols.filter" not in page, "the counts are still drawn only for a collapsed lane"


def test_a_spec_lane_header_carries_its_own_sync_press(page):
    """#1667: a spec is an issue on the same board, so its header takes the same Sync press its tickets do -- re-reading
    one card from GitHub never means opening the spec's sheet first."""
    out = _header_rule([LANE_A, LANE_WAVE])
    cells = [{c["k"]: c for row in h["rows"] for c in row} for h in out["headers"]]
    assert cells[0]["sync"]["on"] is True and cells[0]["sync"]["n"] == 12, cells[0]["sync"]
    assert cells[1]["sync"]["on"] is False, "a wave lane names no one issue to re-read"
    _, headers = _drawn_headers(page, [LANE_A, LANE_WAVE])
    assert '<button class=syncb data-n="12"' in headers[0], headers[0]
    assert SYNC_TITLE in headers[0], headers[0]
    assert "syncb" not in headers[1], headers[1]


def test_the_spec_sync_press_repaints_the_board_because_a_spec_is_no_row(page):
    """The press rewrites the row it began on; a spec header is not a row, so the handler must repaint the board rather
    than reach for a `.it` element that is not there."""
    handler = re.search(r'document\.addEventListener\("click",async e=>\{const b=e\.target\.closest\("#bbody \.syncb"\).*?\}\);', page).group(0)
    assert 'card=b.closest(".it")' in handler and "if(!card)pollBoard()" in handler, handler
