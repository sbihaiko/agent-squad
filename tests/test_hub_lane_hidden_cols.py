"""A column can stay hidden in the swimlanes, drawn as its total only; Closed and Abandoned start hidden.

The swimlanes drew every column that held a card, so Closed took a quarter of the board for history. The board model
takes the user's hidden set: a hidden column is never drawn, and the hidden pill names it with its whole total. A lane
opens and closes with an eased height, and a lane waiting on the board read shows a loading mark in its middle.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LANES_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "board-lanes.js"
LANES_CSS = ROOT / "contrib" / "squad-goal" / "scripts" / "board-lanes.css"
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
NODE = shutil.which("node")

GROUPS = [
    {"name": "Todo", "items": [{"n": 1, "t": "a", "l": []}]},
    {"name": "Doing", "items": [{"n": 2, "t": "b", "l": []}]},
    {"name": "Closed", "items": [{"n": i, "t": "c", "l": []} for i in range(10, 15)]},
    {"name": "Abandoned", "items": [{"n": 20, "t": "d", "l": []}]},
]


def _build(**o):
    if NODE is None:
        pytest.skip("node is required")
    opts = {"mode": "spec", "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed", **o}
    src = LANES_JS.read_text() + f"\nconsole.log(JSON.stringify(buildBoard({json.dumps(GROUPS)},{json.dumps(opts)})));"
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_a_hidden_column_is_not_drawn_and_shows_its_total():
    m = _build(hidden=["Closed", "Abandoned"])
    assert m["vc"] == ["Todo", "Doing"], m["vc"]
    hc = {x["name"]: x for x in m["hc"]}
    assert hc["Closed"]["count"] == 5 and hc["Closed"]["user"] is True, hc
    assert hc["Abandoned"]["count"] == 1, hc


def test_an_unhidden_column_is_drawn_again():
    m = _build(hidden=[])
    assert "Closed" in m["vc"], m["vc"]


def test_the_page_hides_closed_and_abandoned_by_default():
    src = HUB_PY.read_text()
    assert "const hidCols=()=>bo.hidden??[COLNAME.issue_closed,COLNAME.abandoned" in src
    assert "hidden:hidCols()" in src, "the board model never takes the hidden set"


def test_the_column_header_and_the_hidden_pill_toggle_a_column():
    src = HUB_PY.read_text()
    assert 'class=lcolh' in src and "${eye({name:c})}" in src, "a lane column header has no eye"
    assert 'data-eye="${esc(x.name)}"' in src, "a hidden column in the pill cannot be shown again"


def test_a_lane_opens_and_closes_eased_and_shows_a_loading_mark():
    src, css = HUB_PY.read_text(), LANES_CSS.read_text()
    assert "easing:\"ease-in-out\"" in src, "the lane height is not eased"
    assert "class=lload" in src, "a lane waiting on the read shows no loading mark"
    assert "@keyframes lspin" in css and "prefers-reduced-motion" in css


def test_a_read_in_flight_is_shared_so_slow_routes_never_fill_the_connections():
    src = HUB_PY.read_text()
    assert "if(!live.has(k))live.set(k,f(u,o).finally(()=>live.delete(k)))" in src, "a timer can stack reads of one URL"
    assert src.index("window.fetch=(u,o)") < src.index("async function pollBoard"), "the guard loads after a poller"
