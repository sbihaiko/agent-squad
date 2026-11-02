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
ROWS = [["id", "title"], ["bar", "frac", "chip", "gh"], ["counts"]]
FIXED = {"bar": "120px", "frac": "4em", "chip": "6.5em", "gh": "16px"}

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


def _layout(header):
    """A drawn header read by layout row: row 1 before the facts row, row 2 in it, row 3 in the counts row."""
    assert '<span class="lh-facts">' in header and '<span class="lh-counts">' in header, header
    head, rest = header.split('<span class="lh-facts">', 1)
    facts, counts = rest.split('<span class="lh-counts">', 1)
    keys = lambda s: re.findall(r'<span class="lf lf-(\w+)', s)  # noqa: E731
    return [keys(head), keys(facts), keys(counts)]


def _widths(header):
    return dict(re.findall(r'<span class="lf lf-(\w+)" style="width:([^";]+)', header))


def test_every_lane_header_takes_the_same_three_rows():
    out = _header_rule([LANE_A, LANE_B])
    for header in out["headers"]:
        assert [[c["k"] for c in row] for row in header["rows"]] == ROWS, header


def test_a_zero_count_is_an_en_dash_and_the_three_counts_always_stand():
    out = _header_rule([LANE_A, LANE_B])
    items_a = out["headers"][0]["rows"][2][0]["items"]
    items_b = out["headers"][1]["rows"][2][0]["items"]
    assert items_a == [["Todo", "5"], ["Doing", "1"], ["Code Review", "1"]], items_a
    assert items_b == [["Todo", "–"], ["Doing", "1"], ["Code Review", "–"]], items_b


def test_the_fraction_and_the_chip_hold_their_own_text():
    out = _header_rule([LANE_A, LANE_B])
    cells = [{c["k"]: c for row in h["rows"] for c in row} for h in out["headers"]]
    assert cells[0]["frac"]["text"] == "2/3" and cells[1]["frac"]["text"] == "0/7"
    assert cells[0]["chip"]["text"] == "Todo" and cells[1]["chip"]["text"] == "Backlog"


def test_the_fixed_facts_share_one_width_list_and_the_bar_is_fixed():
    out = _header_rule([LANE_A, LANE_B])
    widths = {c["k"]: c["w"] for c in out["cols"]}
    assert widths["bar"] == "120px", widths
    for header in out["headers"]:
        for row in header["rows"][1:2]:
            assert {c["k"]: c["w"] for c in row} == {k: widths[k] for k in ("bar", "frac", "chip", "gh")}


def test_a_wave_lane_name_spans_the_whole_header_and_is_never_clipped(page):
    out = _header_rule([LANE_WAVE])
    row1 = out["headers"][0]["rows"][0]
    assert row1[0]["full"] is True and row1[1]["text"] == "", row1
    _, headers = _drawn_headers(page, [LANE_WAVE])
    assert "lf lf-id full" in headers[0], headers[0]
    assert "SPEC 1158</span> · Wave 2" in headers[0], headers[0]


def test_two_lanes_draw_each_fact_at_one_track_of_one_shared_grid(page):
    grid, headers = _drawn_headers(page, [LANE_A, LANE_B])
    assert grid, "the lanes are not drawn on a grid"
    assert "--lh-cols:7em minmax(0,1fr)" in grid.group(1), grid.group(1)
    assert len(headers) == 2, headers
    assert [_layout(h) for h in headers] == [[["id", "title"], ["bar", "frac", "chip", "gh"], ["counts"]]] * 2
    assert _widths(headers[0]) == _widths(headers[1]) == FIXED


def test_the_counts_draw_one_line_in_fixed_order_joined_by_a_middle_dot(page):
    _, headers = _drawn_headers(page, [LANE_A, LANE_B])
    counts = headers[1].split('<span class="lh-counts">', 1)[1]
    assert "<span>Todo <b>–</b></span> · <span>Doing <b>1</b></span> · <span>Code Review <b>–</b></span>" in counts, counts
    assert "class=cnt" not in headers[1]


def test_the_title_keeps_its_whole_text_in_a_tooltip(page):
    _, headers = _drawn_headers(page, [LANE_B])
    assert 'title="A much longer spec title that wraps across the title column"' in headers[0], headers[0]


def test_the_title_css_clamps_to_two_lines_and_states_so(page):
    css = LANES_CSS.read_text()
    rule = re.search(r"#bbody \.lh \.ltt\{[^}]*\}[^\n]*", css).group(0)
    assert "-webkit-line-clamp:2" in rule, rule
    assert "read whole" not in rule, rule


def test_the_facts_row_wraps_on_a_narrow_lane_instead_of_clipping():
    css = LANES_CSS.read_text()
    rule = re.search(r"#bbody \.lh \.lh-facts\{[^}]*\}", css).group(0)
    assert "flex-wrap:wrap" in rule, rule


def test_the_page_draws_every_header_from_the_rule_layer_on_one_grid(page):
    assert "laneHead(l," in page, "the header no longer takes its facts from the rule layer"
    assert "--lh-cols:${laneHeadGrid}" in page, "the lanes do not share one grid template"
    assert "${cl?m.cols.filter" not in page, "the counts are still drawn only for a collapsed lane"
