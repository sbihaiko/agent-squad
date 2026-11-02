"""A spec lane's header is a table read down the column (#1158).

The header's facts and their shared grid are the rule layer's (`board-lanes.js`, ADR-0137): `laneHead` answers the
ordered facts of one lane header, `laneHeadCols` answers the one track list every lane's header takes. These tests run
that text in node, exactly as the page carries it, and read the page's header for the grid it draws on.
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
NODE = shutil.which("node")
FACTS = ["id", "title", "bar", "frac", "counts", "chip", "gh"]


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


def _header_facts(lanes):
    """The facts of each lane's header, and the one track list, from the rule layer."""
    return _node(
        "const o={todo:\"Todo\",doing:\"Doing\",review:\"Code Review\"};"
        f"const lanes={json.dumps(lanes)};"
        "console.log(JSON.stringify({cols:laneHeadCols,facts:lanes.map(l=>laneHead(l,o))}));"
    )


LANE_A = {"kind": "s", "num": 12, "title": "Short title", "closed": 2, "total": 3, "doing": 0, "review": 0,
          "st": "Todo", "counts": {"Todo": 5, "Doing": 1, "Code Review": 1}}
LANE_B = {"kind": "s", "num": 7, "title": "A much longer spec title that wraps across the title column",
          "closed": 0, "total": 7, "doing": 0, "review": 0, "st": "Backlog", "counts": {"Doing": 1}}


def _cell(fact, key):
    return next(c for c in fact["cells"] if c["k"] == key)


def test_every_lane_header_carries_the_same_facts_in_the_same_order():
    out = _header_facts([LANE_A, LANE_B])
    assert [[c["k"] for c in f["cells"]] for f in out["facts"]] == [FACTS, FACTS], out


def test_a_zero_count_is_an_en_dash_and_the_three_rows_always_stand():
    out = _header_facts([LANE_A, LANE_B])
    rows_a = _cell(out["facts"][0], "counts")["rows"]
    rows_b = _cell(out["facts"][1], "counts")["rows"]
    assert rows_a == [["Todo", "5"], ["Doing", "1"], ["Code Review", "1"]], rows_a
    assert rows_b == [["Todo", "–"], ["Doing", "1"], ["Code Review", "–"]], rows_b


def test_the_fraction_and_the_chip_hold_their_own_text_in_the_same_cell():
    out = _header_facts([LANE_A, LANE_B])
    assert _cell(out["facts"][0], "frac")["text"] == "2/3"
    assert _cell(out["facts"][1], "frac")["text"] == "0/7"
    assert _cell(out["facts"][0], "chip")["text"] == "Todo"
    assert _cell(out["facts"][1], "chip")["text"] == "Backlog"


def test_one_shared_track_per_column_and_a_fixed_progress_bar():
    out = _header_facts([LANE_A, LANE_B])
    cols = out["cols"]
    assert [c["k"] for c in cols] == FACTS, cols
    assert all(c["w"] for c in cols), cols
    bar = next(c for c in cols if c["k"] == "bar")
    assert bar["w"] == "120px", bar
    for fact in out["facts"]:
        assert [c["k"] for c in fact["cells"]] == FACTS, fact
        assert _cell(fact, "bar")["w"] == bar["w"]


def test_a_lane_without_a_bar_still_holds_every_column():
    lane = {"kind": "sw", "sid": 7, "wl": "wave 1", "title": "", "closed": 0, "total": 0, "counts": {}, "st": ""}
    out = _header_facts([LANE_A, lane])
    assert [c["k"] for c in out["facts"][1]["cells"]] == FACTS, out["facts"][1]


def _drawn_headers(page, lanes):
    """The header markup the page draws for these lanes, read back: one grid, the lanes' own header cells in order."""
    import re
    body = re.search(r"const laneHtml=m=>\{.*?\n return h\+\"</div>\"\};", page, re.S).group(0)
    stubs = ('const esc=s=>String(s),colOf=()=>"",CN={},eye=()=>"",SLOTS=new Map(),cards=()=>"",bo={},f="",'
             'BOARD_VOCAB={todo:"Todo",doing:"Doing",review:"Code Review"};')
    model = {"lanes": [dict(lane, collapsed=True, cells={}) for lane in lanes], "vc": [], "cols": []}
    return _node(stubs + body + f"console.log(JSON.stringify(laneHtml({json.dumps(model)})));")


def test_two_lanes_draw_each_column_at_one_position_in_one_shared_grid(page):
    html = _drawn_headers(page, [LANE_A, LANE_B])
    grid = re.search(r'<div class=lgrid style="([^"]*)">', html)
    assert grid, "the lanes are not drawn on a grid"
    assert f"--lh-cols:{' '.join(c['w'] for c in _node('console.log(JSON.stringify(laneHeadCols));'))}" in grid.group(1), grid.group(1)
    headers = re.findall(r'<header class=lh[^>]*>(.*?)</header>', html, re.S)
    assert len(headers) == 2, html
    positions = [{k: i for i, k in enumerate(re.findall(r'<span class="lf lf-(\w+)"', h))} for h in headers]
    assert [list(p) for p in positions] == [FACTS, FACTS], positions
    assert positions[0] == positions[1], "a column sits at a different index in the two lanes"
    assert "style=\"--lh-cols" not in "".join(headers), "a lane header sets its own grid"
    assert "<span>Todo <b>–</b></span>" in headers[1] and "<span>Todo <b>5</b></span>" in headers[0]


def test_the_page_draws_every_header_from_the_rule_layer_on_one_grid(page):
    assert "laneHead(l," in page, "the header no longer takes its facts from the rule layer"
    assert "--lh-cols:${laneHeadCols.map" in page, "the lanes do not share one grid template"
    assert "${cl?m.cols.filter" not in page, "the counts are still drawn only for a collapsed lane"
