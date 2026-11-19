"""A column can stay hidden in the swimlanes, drawn as its total only; a closed status is never a column (#969).

The board model takes the user's hidden set: a hidden open column is never drawn, and the hidden pill names it with its
whole total. A closed or abandoned status (`end`, set by the board's role) is history: it is never drawn, never in the
pill, and a saved hidden set drops it on load. A lane opens and closes with an eased height, and a lane waiting on the
board read shows a loading mark in its middle.
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
    {"name": "Todo", "end": False, "items": [{"n": 1, "t": "a", "l": []}]},
    {"name": "Doing", "end": False, "items": [{"n": 2, "t": "b", "l": []}]},
    {"name": "Closed", "end": True, "items": [{"n": i, "t": "c", "l": []} for i in range(10, 15)]},
    {"name": "Abandoned", "end": True, "items": [{"n": 20, "t": "d", "l": []}]},
]


def _build(groups=GROUPS, **o):
    if NODE is None:
        pytest.skip("node is required")
    opts = {"mode": "spec", "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed", **o}
    src = LANES_JS.read_text() + f"\nconsole.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));"
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _node(script):
    if NODE is None:
        pytest.skip("node is required")
    p = subprocess.run([NODE, "-"], input=LANES_JS.read_text() + "\n" + script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_a_hidden_open_column_is_not_drawn_and_shows_its_total():
    m = _build(hidden=["Doing"])
    assert m["vc"] == ["Todo"], m["vc"]
    hc = {x["name"]: x for x in m["hc"]}
    assert hc["Doing"]["count"] == 1 and hc["Doing"]["user"] is True, hc


def test_a_hidden_column_counts_work_and_never_a_spec_card():
    """A spec is a lane, never a work card (@2466): the pill's total agrees with the column a reader sees drawn."""
    groups = [
        {"name": "Todo", "end": False, "items": [{"n": 5, "t": "a spec", "l": [], "sp": True}]},
        {"name": "Doing", "end": False, "items": [{"n": 2, "t": "a card", "l": []}, {"n": 6, "t": "b spec", "l": [], "sp": True}]},
    ]
    m = _build(groups, hidden=["Todo", "Doing"])
    hc = {x["name"]: x for x in m["hc"]}
    assert hc["Todo"]["count"] == 0, f"a spec card counted as work: {hc}"
    assert hc["Doing"]["count"] == 1, f"the count is not the work cards alone: {hc}"


def test_a_closed_status_is_never_a_column_even_when_nothing_is_hidden():
    m = _build(hidden=[])
    assert m["vc"] == ["Todo", "Doing"], m["vc"]
    assert m["cols"] == ["Todo", "Doing"], m["cols"]
    assert all(x["name"] not in ("Closed", "Abandoned") for x in m["hc"]), m["hc"]


def test_a_closed_status_is_never_in_the_hidden_pill_even_when_the_saved_set_names_it():
    m = _build(hidden=["Closed", "Abandoned"])
    assert m["vc"] == ["Todo", "Doing"], m["vc"]
    assert m["hc"] == [], m["hc"]


def test_a_saved_hidden_set_drops_a_closed_status_on_load():
    kept = _node(f"console.log(JSON.stringify(keptHidden(['Closed','Doing','Abandoned'],{json.dumps(GROUPS)})));")
    assert kept == ["Doing"], kept


def test_the_page_names_no_closed_column_as_hidden_by_default():
    src = HUB_PY.read_text()
    assert "const hidCols=()=>bo.hidden??[];" in src, "a closed status is still a default hidden column"
    assert "COLNAME.issue_closed,COLNAME.abandoned].filter(Boolean)" not in src
    assert "hidden:hidCols()" in src, "the board model never takes the hidden set"


def test_the_hidden_strip_draws_each_closed_status_as_a_text_total_with_no_eye():
    src = HUB_PY.read_text()
    line = next(ln for ln in src.splitlines() if ln.startswith("const endLines="))
    assert "data-eye" not in line and "eye(" not in line, line
    assert "fmtN(g.n)" in line, "the closed total is not the count the column header reads"


def test_the_column_header_and_the_hidden_pill_toggle_a_column():
    src = HUB_PY.read_text()
    assert 'class=lcolh' in src and "${eye({name:c})}" in src, "a lane column header has no eye"
    assert 'data-eye="${esc(x.name)}"' in src, "a hidden column in the pill cannot be shown again"


def test_a_lane_waits_with_a_sentence_and_no_spinner_or_looping_animation():
    src, css = HUB_PY.read_text(), LANES_CSS.read_text()
    assert "Loading the lane contents." in src
    assert "<i></i><span>loading…</span>" not in src
    assert "@keyframes" not in css and "animation:" not in css
    assert "lspin" not in css and "lload i" not in css
    assert "matchMedia(\"(prefers-reduced-motion: reduce)\")" not in src


def test_a_read_in_flight_is_shared_so_slow_routes_never_fill_the_connections():
    src = HUB_PY.read_text()
    assert "if(!live.has(k))live.set(k,f(u,o).finally(()=>live.delete(k)))" in src, "a timer can stack reads of one URL"
    assert src.index("window.fetch=(u,o)") < src.index("async function pollBoard"), "the guard loads after a poller"
