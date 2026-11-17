"""A spec whose card is closed or abandoned draws no swimlane, and the lanes read the board's summaries, never closed rows.

The board answer serves the open rows, one count per closed status, and one summary per spec (#972). A spec lane's
progress comes from that summary, so a closed ticket counts without being served. A closed
spec keeps no lane: its open tickets fall to No spec, read from the spec summary's state. A spec whose tickets are all
closed while its own card is open is a done lane, and Hide done lanes hides it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

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
    {"name": "Todo", "items": [_row(899, "Todo", pa=889), _row(44, "Todo", pa=41), _row(45, "Todo", pa=492)]},
    {"name": "Doing", "items": [_row(889, "Doing", sp=True), _row(890, "Doing", pa=889)]},
    {"name": "Closed", "end": True, "items": []},
    {"name": "Abandoned", "end": True, "items": []},
]
SUM = {
    "specs": [
        {"n": 889, "st": "Doing", "closed": 1, "total": 3},
        {"n": 41, "st": "Closed", "closed": 1, "total": 2},
        {"n": 492, "st": "Abandoned", "closed": 0, "total": 1},
    ],
    "waves": [],
}
DONE_GROUPS = [
    GROUPS[0],
    {"name": "Doing", "items": [*GROUPS[1]["items"], _row(700, "Doing", sp=True)]},
    *GROUPS[2:],
]
DONE_SUM = {**SUM, "specs": [*SUM["specs"], {"n": 700, "st": "Doing", "closed": 2, "total": 2}]}


def _run(script):
    if NODE is None:
        pytest.skip("node is required")
    p = subprocess.run([NODE, "-"], input=LANES_JS.read_text() + "\n" + script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _build(groups=GROUPS, summary=SUM, **o):
    opts = {"fs": [], "lc": {}, "hideDone": False, "closed": "Closed",
            "abandoned": "Abandoned", "sum": summary, **o}
    return _run(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));")


def test_a_closed_spec_draws_no_lane():
    ids = [ln["id"] for ln in _build()["lanes"]]
    assert "s41" not in ids, f"a closed spec drew a lane: {ids}"


def test_an_open_ticket_of_a_closed_or_abandoned_spec_falls_to_no_spec():
    lane = next(ln for ln in _build()["lanes"] if ln["id"] == "none")
    assert sorted(r["n"] for r in lane["all"]) == [44, 45], lane


def test_a_spec_lane_counts_its_closed_tickets_from_the_summary():
    lane = next(ln for ln in _build()["lanes"] if ln["id"] == "s889")
    assert (lane["closed"], lane["total"]) == (1, 3), "the closed ticket is not counted: it is served by no row"


FAR_SUM = {"specs": [{"n": 889, "st": "Doing", "closed": 1, "total": 3, "far": "Code Complete", "far_n": 2}], "waves": []}


def test_a_spec_lane_draws_the_furthest_column_its_tickets_reached_as_a_count():
    """#1906: the tickets' furthest column stands beside the spec as a count, and the spec's own column stays its own."""
    lane = next(ln for ln in _build(summary=FAR_SUM)["lanes"] if ln["id"] == "s889")
    head = _run(f"console.log(JSON.stringify(laneHead({json.dumps(lane)},"
                "{todo:\"Todo\",doing:\"Doing\",review:\"Code Review\"})));")
    cells = {c["k"]: c for row in head["rows"] for c in row}
    assert ["furthest", "Code Complete 2"] in cells["counts"]["items"], cells["counts"]
    assert cells["chip"]["text"] == "Doing", "the spec's own column is drawn as its column, never the tickets'"


def test_a_spec_lane_with_no_ticket_in_flight_draws_no_furthest_count():
    summary = {"specs": [{"n": 889, "st": "Backlog", "closed": 0, "total": 0}], "waves": []}
    lane = next(ln for ln in _build(summary=summary)["lanes"] if ln["id"] == "s889")
    head = _run(f"console.log(JSON.stringify(laneHead({json.dumps(lane)},"
                "{todo:\"Todo\",doing:\"Doing\",review:\"Code Review\"})));")
    cells = {c["k"]: c for row in head["rows"] for c in row}
    assert [n for n, _ in cells["counts"]["items"]] == ["Todo", "Doing", "Code Review"], cells["counts"]


def test_progress_reads_the_spec_sub_issue_count_not_the_served_rows():
    """A closed ticket carries no parent on the board, so counting rows would answer 0 of 2: the spec card's own
    sub-issue count is the completeness (#890)."""
    groups = [
        {"name": "Doing", "items": [{"n": 889, "t": "SPEC 889", "l": ["spec"], "sp": True, "sc": "26/34"}]},
        {"name": "Doing", "items": [_row(890, "Doing", pa=889)]},
    ]
    lane = next(ln for ln in _build(groups=groups, summary={"specs": [], "waves": []})["lanes"] if ln["id"] == "s889")
    assert (lane["closed"], lane["total"]) == (26, 34), lane


def test_a_spec_with_open_cards_still_draws_its_lane():
    lane = next((ln for ln in _build()["lanes"] if ln["id"] == "s889"), None)
    assert lane is not None, "the open spec lost its lane"
    assert lane["total"] == 3, lane


def test_a_spec_whose_tickets_are_all_closed_is_a_done_lane():
    lane = next(ln for ln in _build(groups=DONE_GROUPS, summary=DONE_SUM)["lanes"] if ln["id"] == "s700")
    assert lane["done"] is True and lane["collapsed"] is True, lane


def test_hide_done_lanes_hides_a_spec_whose_tickets_are_all_closed():
    ids = [ln["id"] for ln in _build(groups=DONE_GROUPS, summary=DONE_SUM, hideDone=True)["lanes"]]
    assert "s700" not in ids and "s889" in ids, ids


def test_spec_options_leave_out_a_finished_spec():
    opts = _run(f"console.log(JSON.stringify(specOptions({json.dumps(GROUPS)},{json.dumps(SUM)})));")
    assert opts == ["889"], opts


def test_a_lane_holding_an_open_ticket_is_never_done():
    summary = {**SUM, "waves": [{"spec": None, "wave": None, "closed": 3, "total": 3}]}
    lane = next(ln for ln in _build(summary=summary)["lanes"] if ln["id"] == "none")
    assert lane["n"] == 2 and lane["done"] is False, lane
