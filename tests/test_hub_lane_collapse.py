"""A swimlane collapses to one line of column counts and remembers it (SPEC 889, #891).

The rule is `board-lanes.js`, run in node as the page carries it: a lane that is done starts collapsed, a user's choice
(`bo.lc`) wins over that default, hide-done drops only the lanes whose every ticket is closed, and a cell shows three
cards, every one of them, with no "+N more" control (`bo.cx` is kept for the saved record). Collapse and open state live in `bo`, which
the page keeps in local storage, so they survive a reload.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
LANES_JS = SCRIPTS / "board-lanes.js"
NODE = shutil.which("node")


def _node(script):
    if NODE is None:
        pytest.skip("node is required")
    src = LANES_JS.read_text() + "\n" + script
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _row(n, col, labels=(), pa=None, sp=False, ho=None):
    row = {"n": n, "t": f"card {n}", "l": list(labels), "c": None}
    if sp:
        row["sp"] = True
    if pa is not None:
        row["pa"] = pa
    if ho:
        row["ho"] = ho
    return row


# Spec 10 is done (every ticket closed); spec 20 is open with two cards in two columns; spec 30 has none.
GROUPS = [
    {"name": "Todo", "end": False, "items": [_row(11, "Todo", pa=20), _row(12, "Todo", pa=20)]},
    {"name": "Doing", "end": False, "items": [_row(10, "Doing", sp=True), _row(20, "Doing", sp=True), _row(30, "Doing", sp=True),
                                _row(21, "Doing", pa=20)]},
    {"name": "Closed", "end": True, "items": [_row(13, "Closed", pa=10), _row(14, "Closed", pa=10)]},
]


def _lanes(opts, groups=GROUPS):
    base = {"mode": "spec", "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed"}
    return _node(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps({**base, **opts})})));")


def _lane(model, lane_id):
    return next(ln for ln in model["lanes"] if ln["id"] == lane_id)


# ---- a closed status is history, never a column, in the summary or the search (#969) ----

def test_a_collapsed_lane_summary_counts_open_statuses_only():
    mixed = [
        {"name": "Todo", "end": False, "items": [_row(15, "Todo", pa=10)]},
        {"name": "Doing", "end": False, "items": [_row(10, "Doing", sp=True), _row(16, "Doing", pa=10)]},
        {"name": "Closed", "end": True, "items": [_row(13, "Closed", pa=10), _row(14, "Closed", pa=10)]},
    ]
    lane = _lane(_lanes({"lc": {"s10": True}}, groups=mixed), "s10")
    assert lane["collapsed"] is True
    assert "Closed" not in lane["counts"], lane["counts"]
    assert lane["counts"] == {"Todo": 1, "Doing": 1}, lane["counts"]


def test_the_search_matches_open_cards_only():
    model = _node(
        f"console.log(JSON.stringify(buildBoard({json.dumps(GROUPS)},{{mode:'spec',fw:[],fs:[],lc:{{}},hideDone:false,"
        "closed:'Closed',search:true,text:x=>String(x.t).includes('card 13')})));")
    shown = [r["n"] for ln in model["lanes"] for r in ln["vis"]]
    assert shown == [], f"a closed card matched the search: {shown}"


def test_a_closed_card_never_enters_a_lane_the_board_draws():
    model = _lanes({})
    drawn = [r["n"] for ln in model["lanes"] for r in ln["vis"]]
    assert 13 not in drawn and 14 not in drawn, drawn
    assert "Closed" not in model["vc"]


# ---- the default and the user's choice ----

def test_a_done_lane_starts_collapsed_and_an_open_one_does_not():
    model = _lanes({})
    assert _lane(model, "s10")["done"] is True
    assert _lane(model, "s10")["collapsed"] is True, "a done lane does not start collapsed"
    assert _lane(model, "s20")["collapsed"] is False


def test_the_users_choice_wins_over_the_done_default():
    model = _lanes({"lc": {"s10": False, "s20": True}})
    assert _lane(model, "s10")["collapsed"] is False, "an expanded done lane was collapsed again"
    assert _lane(model, "s20")["collapsed"] is True, "a collapsed open lane was expanded again"


def test_a_collapsed_lane_keeps_its_count_per_column():
    model = _lanes({"lc": {"s20": True}})
    lane = _lane(model, "s20")
    assert lane["counts"] == {"Todo": 2, "Doing": 1}, "the collapsed line does not count each column"


def test_a_column_is_drawn_only_while_an_expanded_lane_holds_a_card_in_it():
    model = _lanes({"lc": {"s20": True}})
    assert model["vc"] == [], "a column held only by a collapsed lane is still drawn"
    hidden = {c["name"]: c["count"] for c in model["hc"]}
    assert hidden == {"Todo": 2, "Doing": 1}, "the hidden columns are not named with their counts, or a closed status is named"
    assert len(_lanes({"lc": {"s20": False}})["vc"]) >= 2, "an expanded lane does not draw its columns"


# ---- hide done ----

def test_hide_done_drops_only_the_lanes_whose_every_ticket_is_closed():
    model = _lanes({"hideDone": True})
    ids = [ln["id"] for ln in model["lanes"]]
    assert "s10" not in ids, "a done lane is still shown"
    assert "s20" in ids and "s30" in ids, "an open lane is hidden with the done ones"


# ---- the cell shows every card ----

def test_a_cell_shows_every_card_and_no_more_control():
    """The owner reads the whole lane at once: a cell has no cap, open or not (#891)."""
    rows = [{"n": n} for n in range(1, 8)]
    for opened in ("false", "true"):
        view = _node(f"console.log(JSON.stringify(cellView({json.dumps(rows)},{opened})));")
        assert [r["n"] for r in view["shown"]] == list(range(1, 8)) and view["more"] == 0


# ---- the page's controls and their state ----

def test_the_page_has_collapse_all_expand_all_and_hide_done(hub):
    page = hub.PAGE
    assert 'id=lcall' in page and ">Collapse all</button>" in page
    assert 'id=lexp' in page and ">Expand all</button>" in page
    assert 'id=lhd' in page and "Hide done lanes" in page


def test_a_header_click_toggles_its_lane_and_the_state_is_saved(hub):
    page = hub.PAGE
    assert 'closest("#bbody .lh")' in page, "the lane header has no click"
    assert 'bo.lc[' in page and "saveB()" in page, "the collapse state is not written to bo"


def test_a_more_click_opens_only_its_cell_and_is_saved(hub):
    page = hub.PAGE
    assert 'class=more' in page, "the more control is missing"
    assert 'closest("#bbody .more")' in page and "bo.cx[" in page, "a more click does not open its own cell"


def test_collapse_state_is_kept_in_the_saved_board_record(hub):
    page = hub.PAGE
    assert "bo=ls.bo||{}" in page or "const bo=ls.bo||{}" in page, "bo is not read back from local storage"
    assert "localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,bo})" in page


@pytest.fixture(scope="module")
def hub():
    import importlib.util
    import os
    import tempfile
    for k, v in {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": tempfile.mkdtemp(),
                 "SG_HUB_DIR": tempfile.mkdtemp()}.items():
        os.environ.setdefault(k, v)
    spec = importlib.util.spec_from_file_location("hub_lane_collapse", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
