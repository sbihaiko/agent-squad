"""The mobile board groups each carousel column by spec (#893).

A column's visible cards form swimlanes: one lane per spec, headed by its number and progress, and a "No spec" lane last.
The lane rule is one pure function in `hub-mobile-gesture.js`, run under node with no DOM. The page is checked as text:
the header that taps a lane shut, the saved collapse on the device, and the closed column's name the hub serves.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
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
    {"name": "Closed", "items": [{"n": 15, "t": "fifteen", "pa": 10, "l": [], "dn": True}]},
]


def lanes(cards, lc=None, groups=BOARD):
    args = json.dumps({"groups": groups, "cards": cards, "lc": lc or {}})
    return run_js(GESTURE_JS.read_text(), f"columnLanes({args})")


TODO_CARDS = [BOARD[1]["items"][i] for i in range(4)]


@pytest.mark.node_bound
def test_a_column_groups_its_cards_by_spec_and_wave_in_spec_number_order():
    out = lanes(TODO_CARDS)
    assert [ln["id"] for ln in out["lanes"]] == ["10|none", "20|none", "none|none"]
    assert [c["n"] for c in out["lanes"][0]["cards"]] == [10, 11]


@pytest.mark.node_bound
def test_the_no_spec_lane_comes_last_with_its_cards_and_its_title():
    out = lanes(TODO_CARDS)
    last = out["lanes"][-1]
    assert last["id"] == "none|none" and last["num"] is None and last["title"] == "No spec · No wave"
    assert [c["n"] for c in last["cards"]] == [13]


@pytest.mark.node_bound
def test_a_spec_card_heads_its_lane_and_stays_a_card_first():
    lane = lanes(TODO_CARDS)["lanes"][0]
    assert lane["title"] == "SPEC 10 · No wave"
    assert lane["cards"][0]["n"] == 10 and lane["cards"][0]["sp"] is True


@pytest.mark.node_bound
def test_a_spec_lane_with_no_wave_names_its_spec_and_no_wave():
    lane = lanes(TODO_CARDS)["lanes"][1]
    assert lane["id"] == "20|none" and lane["num"] == 20 and lane["title"] == "SPEC 20 · No wave"


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
def test_two_specs_with_the_same_wave_draw_two_lanes_named_by_spec_and_wave():
    out = lanes(WAVE_CARDS, groups=WAVE_BOARD)
    titles = [ln["title"] for ln in out["lanes"]]
    assert titles == ["SPEC 40 · Wave 1", "SPEC 40 · Wave 2", "SPEC 50 · Wave 1", "No spec · Wave 2"]
    assert [c["n"] for c in out["lanes"][0]["cards"]] == [40, 41]
    assert [ln["sid"] for ln in out["lanes"]] == ["SPEC 40", "SPEC 40", "SPEC 50", "No spec"]


@pytest.mark.node_bound
def test_a_spec_keeps_one_colour_index_across_its_waves_and_two_specs_differ():
    by_title = {ln["title"]: ln for ln in lanes(WAVE_CARDS, groups=WAVE_BOARD)["lanes"]}
    assert by_title["SPEC 40 · Wave 1"]["pi"] == by_title["SPEC 40 · Wave 2"]["pi"]
    assert by_title["SPEC 40 · Wave 1"]["pi"] != by_title["SPEC 50 · Wave 1"]["pi"]
    assert by_title["No spec · Wave 2"]["pi"] is None


@pytest.mark.node_bound
def test_the_progress_counts_the_spec_children_on_the_whole_board_not_the_column():
    lane = lanes(TODO_CARDS)["lanes"][0]
    assert lane["total"] == 3 and lane["closed"] == 1


@pytest.mark.node_bound
def test_a_spec_card_with_no_child_in_the_column_still_draws_its_lane():
    cards = [BOARD[1]["items"][0]]
    out = lanes(cards)
    assert [ln["id"] for ln in out["lanes"]] == ["10|none"] and out["flat"][0]["n"] == 10


@pytest.mark.node_bound
def test_a_column_with_no_visible_card_has_no_lane():
    assert lanes([])["lanes"] == [] and lanes([])["flat"] == []


@pytest.mark.node_bound
def test_a_lane_whose_spec_is_done_starts_collapsed_and_its_cards_leave_the_flat_list():
    done = [
        {"name": "Todo", "items": [{"n": 30, "t": "s", "sp": True, "l": []}]},
        {"name": "Closed", "items": [{"n": 32, "t": "b", "pa": 30, "l": [], "dn": True}]},
    ]
    out = lanes(done[0]["items"], groups=done)
    assert out["lanes"][0]["collapsed"] is True and out["flat"] == []


@pytest.mark.node_bound
def test_a_saved_collapse_wins_over_the_default_and_the_flat_list_follows_it():
    out = lanes(TODO_CARDS, lc={"10|none": True, "none|none": False})
    by_id = {ln["id"]: ln for ln in out["lanes"]}
    assert by_id["10|none"]["collapsed"] is True and by_id["none|none"]["collapsed"] is False
    assert [c["n"] for c in out["flat"]] == [14, 13]


@pytest.mark.node_bound
def test_the_flat_list_is_the_cards_of_the_open_lanes_in_lane_order():
    out = lanes(TODO_CARDS)
    assert [c["n"] for c in out["flat"]] == [10, 11, 14, 13]


def test_the_mobile_header_names_the_spec_in_its_colour_and_the_wave_after_it():
    html = PAGE.read_text()
    assert "l.sid" in html and "l.wl" in html and "l.pi" in html


def test_the_page_draws_a_tappable_lane_header_with_its_collapse_kept_on_the_device():
    html = PAGE.read_text()
    assert "columnLanes(" in html
    assert "lane-h" in html and "dataset.lane" in html
    assert "bo.mlc" in html and "saveB()" in html


def test_the_hub_marks_each_closed_row_and_the_page_names_no_column():
    text = HUB_PY.read_text()
    assert 'row["dn"] = True' in text and 'COLUMN["issue_closed"]' in text
    assert "/*BOARD_NAMES*/" not in PAGE.read_text()
