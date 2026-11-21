"""The mobile board groups each carousel column by spec (#893).

A column's visible cards form swimlanes: one lane per spec, headed by its number and progress, and a "No spec" lane last.
The page has no wave lane, no wave chip and no wave filter: a `wave:NN` label never makes a lane (SPEC 1965, #1988).
The lane rule is one pure function in `hub-mobile-gesture.js`, run under node with no DOM. The page is checked as text:
the header that taps a lane shut, the saved collapse on the device, and the closed column's name the hub serves.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
LANES_JS = SCRIPTS / "board-lanes.js"
HUB_PY = SCRIPTS / "hub.py"
PAGE = SCRIPTS / "hub-mobile.html"
NODE = shutil.which("node")


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


BOARD = [
    {"name": "Backlog", "items": [{"n": 12, "t": "twelve", "pa": 10, "l": []}]},
    {"name": "Todo", "items": [
        {"n": 10, "t": "Spec ten", "sp": True, "l": ["spec"]},
        {"n": 11, "t": "eleven", "pa": 10, "l": []},
        {"n": 13, "t": "thirteen", "l": []},
        {"n": 14, "t": "fourteen", "pa": 20, "l": []},
    ]},
    {"name": "Closed", "end": True, "n": 1, "items": []},
]
SUM = {
    "specs": [
        {"n": 10, "st": "Todo", "closed": 1, "total": 3},
        {"n": 20, "st": None, "closed": 0, "total": 1},
        {"n": 41, "st": "Closed", "closed": 2, "total": 2},
    ],
    "waves": [
        {"spec": 10, "wave": None, "closed": 1, "total": 3},
        {"spec": 20, "wave": None, "closed": 0, "total": 1},
        {"spec": None, "wave": None, "closed": 0, "total": 1},
    ],
}


def lanes(cards, lc=None, groups=BOARD, summary=SUM):
    args = json.dumps({"groups": groups, "cards": cards, "lc": lc or {}, "sum": summary})
    return run_js(GESTURE_JS.read_text(), f"columnLanes({args})")


TODO_CARDS = [BOARD[1]["items"][i] for i in range(4)]


@pytest.mark.node_bound
def test_a_column_groups_its_cards_by_spec_in_spec_number_order():
    out = lanes(TODO_CARDS)
    assert [ln["id"] for ln in out["lanes"]] == ["10", "20", "none"]
    assert [c["n"] for c in out["lanes"][0]["cards"]] == [11]   # the spec's own card heads the lane and is not drawn


@pytest.mark.node_bound
def test_the_no_spec_lane_comes_last_with_its_cards_and_its_title():
    out = lanes(TODO_CARDS)
    last = out["lanes"][-1]
    assert last["id"] == "none" and last["num"] is None and last["title"] == "No spec"
    assert [c["n"] for c in last["cards"]] == [13]


@pytest.mark.node_bound
def test_a_spec_card_heads_its_lane_and_is_never_drawn():
    lane = lanes(TODO_CARDS)["lanes"][0]
    assert lane["title"] == "SPEC 10"
    assert lane["head"]["n"] == 10 and lane["head"]["sp"] is True
    assert all(c["n"] != 10 for c in lane["cards"]), "the spec card is drawn as a card"


@pytest.mark.node_bound
def test_a_spec_lane_names_its_spec():
    lane = lanes(TODO_CARDS)["lanes"][1]
    assert lane["id"] == "20" and lane["num"] == 20 and lane["title"] == "SPEC 20"


WAVE_BOARD = [
    {"name": "Todo", "items": [
        {"n": 40, "t": "Spec forty", "sp": True, "l": ["spec"]},
        {"n": 41, "t": "a", "pa": 40, "l": ["wave:01"]},
        {"n": 42, "t": "b", "pa": 40, "l": ["wave:02"]},
        {"n": 43, "t": "c", "pa": 50, "l": ["wave:01"]},
        {"n": 44, "t": "d", "l": ["wave:02"]},
    ]},
]
WAVE_CARDS = WAVE_BOARD[0]["items"]


@pytest.mark.node_bound
def test_a_wave_label_never_makes_a_lane():
    out = lanes(WAVE_CARDS, groups=WAVE_BOARD)
    titles = [ln["title"] for ln in out["lanes"]]
    assert titles == ["SPEC 40", "SPEC 50", "No spec"]
    assert [c["n"] for c in out["lanes"][0]["cards"]] == [41, 42]
    assert [c["n"] for c in out["lanes"][1]["cards"]] == [43]
    assert [ln["sid"] for ln in out["lanes"]] == ["SPEC 40", "SPEC 50", "No spec"]


@pytest.mark.node_bound
def test_a_spec_lane_takes_its_own_colour_and_two_specs_differ():
    """The page colors a lane as it colors a card: specColor over the board's spec slots, by the lane's spec number."""
    args = json.dumps({"groups": WAVE_BOARD, "cards": WAVE_CARDS, "lc": {}})
    rules = re.search(r"// <card-rules>.*?// </card-rules>", LANES_JS.read_text(), re.S).group(0)
    colour = run_js(GESTURE_JS.read_text() + "\n" + rules,
                    f"(() => {{ const m = columnLanes({args}), s = specSlots(m.lanes.map(l => l.num).filter(n => n != null));"
                    " return Object.fromEntries(m.lanes.map(l => [l.title, l.num == null ? null : specColor(s, l.num)])); })()")
    assert colour["SPEC 40"] != colour["SPEC 50"]
    assert colour["No spec"] is None


@pytest.mark.node_bound
def test_the_progress_counts_the_spec_children_on_the_whole_board_not_the_column():
    lane = lanes(TODO_CARDS)["lanes"][0]
    assert lane["total"] == 3 and lane["closed"] == 1


@pytest.mark.node_bound
def test_a_spec_card_with_no_child_in_the_column_still_draws_its_lane():
    cards = [BOARD[1]["items"][0]]
    out = lanes(cards)
    assert [ln["id"] for ln in out["lanes"]] == ["10"] and out["lanes"][0]["head"]["n"] == 10 and out["flat"] == []


@pytest.mark.node_bound
def test_a_column_with_no_visible_card_has_no_lane():
    assert lanes([])["lanes"] == [] and lanes([])["flat"] == []


@pytest.mark.node_bound
def test_a_lane_whose_spec_is_done_starts_collapsed_and_its_cards_leave_the_flat_list():
    done = [
        {"name": "Todo", "items": [{"n": 30, "t": "s", "sp": True, "l": []}]},
        {"name": "Closed", "end": True, "n": 1, "items": []},
    ]
    summary = {"specs": [{"n": 30, "st": "Todo", "closed": 1, "total": 1}], "waves": []}
    out = lanes(done[0]["items"], groups=done, summary=summary)
    assert out["lanes"][0]["collapsed"] is True and out["flat"] == []


@pytest.mark.node_bound
def test_an_open_ticket_of_a_closed_spec_lands_in_no_spec():
    out = lanes([{"n": 16, "t": "sixteen", "pa": 41, "l": []}])
    assert [ln["id"] for ln in out["lanes"]] == ["none"], out["lanes"]


@pytest.mark.node_bound
def test_a_saved_collapse_wins_over_the_default_and_the_flat_list_follows_it():
    out = lanes(TODO_CARDS, lc={"10": True, "none": False})
    by_id = {ln["id"]: ln for ln in out["lanes"]}
    assert by_id["10"]["collapsed"] is True and by_id["none"]["collapsed"] is False
    assert [c["n"] for c in out["flat"]] == [14, 13]


@pytest.mark.node_bound
def test_the_flat_list_is_the_cards_of_the_open_lanes_in_lane_order():
    out = lanes(TODO_CARDS)
    assert [c["n"] for c in out["flat"]] == [11, 14, 13]


def test_the_mobile_header_names_the_spec_in_its_colour():
    html = PAGE.read_text()
    assert "l.sid" in html and "l.wl" not in html and "specColor(mSlots(), l.num)" in html


def test_the_page_draws_a_tappable_lane_header_with_its_collapse_kept_on_the_device():
    html = PAGE.read_text()
    assert "columnLanes(" in html
    assert "lane-h" in html and "dataset.lane" in html
    assert "bo.mlc" in html and "saveB()" in html


def test_the_board_answer_marks_no_row_done_because_it_serves_no_closed_row():
    assert 'row["dn"]' not in HUB_PY.read_text()
    assert "/*BOARD_NAMES*/" not in PAGE.read_text()


@pytest.mark.node_bound
def test_a_lane_holding_an_open_ticket_is_never_done():
    summary = {"specs": [{"n": 41, "st": "Closed", "closed": 2, "total": 2}],
               "waves": [{"spec": None, "wave": None, "closed": 3, "total": 3}]}
    lane = lanes([{"n": 16, "t": "sixteen", "pa": 41, "l": []}], summary=summary)["lanes"][0]
    assert lane["id"] == "none" and lane["collapsed"] is False, lane
