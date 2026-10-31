"""A spec whose card is closed or abandoned draws no swimlane: its tickets are done, so the lane would be empty.

The board drew one lane per spec it could find, and the spec map was built from every column, so every spec that ever
shipped came back as a 0/0 lane marked Closed, and the board filled with them: the live board showed 20 finished specs
above the work in flight. A lane belongs to work still on the board (#890), so the spec map reads the open columns only.
"""
import json
import shutil
import subprocess

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANES_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "board-lanes.js"
NODE = shutil.which("node")


def _row(n, col, pa=None, sp=False):
    row = {"n": n, "t": f"card {n}", "l": (["spec"] if sp else []), "c": None}
    if sp:
        row["sp"] = True
    if pa is not None:
        row["pa"] = pa
    return row


GROUPS = [
    {"name": "Todo", "items": [_row(899, "Todo", pa=889)]},
    {"name": "Doing", "items": [_row(889, "Doing", sp=True), _row(890, "Doing", pa=889)]},
    {"name": "Closed", "items": [_row(41, "Closed", sp=True), _row(43, "Closed", pa=41)]},
    {"name": "Abandoned", "items": [_row(492, "Abandoned", sp=True), _row(494, "Abandoned", pa=492)]},
]


def _build(**o):
    if NODE is None:
        pytest.skip("node is required")
    opts = {"mode": "spec", "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed",
            "abandoned": "Abandoned", **o}
    src = LANES_JS.read_text() + f"\nconsole.log(JSON.stringify(buildBoard({json.dumps(GROUPS)},{json.dumps(opts)})));"
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_a_closed_spec_draws_no_lane():
    ids = [ln["id"] for ln in _build()["lanes"]]
    assert "s41" not in ids, f"a closed spec drew a lane: {ids}"


def test_an_abandoned_spec_draws_no_lane():
    ids = [ln["id"] for ln in _build()["lanes"]]
    assert "s492" not in ids, f"an abandoned spec drew a lane: {ids}"


def test_progress_reads_the_spec_sub_issue_count_not_the_served_rows():
    """A closed ticket carries no parent on the board, so counting rows would answer 0 of 2: the spec card's own
    sub-issue count is the completeness (#890)."""
    groups = [
        {"name": "Doing", "items": [{"n": 889, "t": "SPEC 889", "l": ["spec"], "sp": True, "sc": "26/34"}]},
        {"name": "Doing", "items": [_row(890, "Doing", pa=889)]},
    ]
    if NODE is None:
        pytest.skip("node is required")
    opts = {"mode": "spec", "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed", "abandoned": "Abandoned"}
    src = LANES_JS.read_text() + f"\nconsole.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));"
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    lane = next(ln for ln in json.loads(p.stdout)["lanes"] if ln["id"] == "s889")
    assert (lane["closed"], lane["total"]) == (26, 34), lane


def test_a_spec_with_open_cards_still_draws_its_lane():
    lane = next((ln for ln in _build()["lanes"] if ln["id"] == "s889"), None)
    assert lane is not None, "the open spec lost its lane"
    assert lane["total"] == 2, lane
