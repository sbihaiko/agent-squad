"""The web board groups its cards in one swimlane per spec (SPEC 889, #890).

A card carries its parent spec as `pa`, served on its row, so the page builds the lanes with no GitHub read. Loop bugs
sit in a "Loop bugs" lane first (ADR-0146), other cards with no spec in a "No spec" lane last, and a lane with a running
card sorts before one without. The selector switches between Spec (the default), Wave and None (today's board).

The lane rule is `board-lanes.js`, a text the hub inlines into the page. These tests run that text in node, exactly as
the page carries it, and read the served page for the selector.
"""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
LANES_JS = SCRIPTS / "board-lanes.js"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_swimlanes")
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir()
    mp.setenv("SG_REPO", "x/y")
    mp.setenv("SG_OWNER", "x")
    mp.setenv("SG_PROJECT", "4")
    mp.setenv("SG_CHECKOUT", str(base / "checkout"))
    mp.setenv("SG_HUB_DIR", str(base / "hub"))
    mp.setenv("SG_STATE", str(base / "state"))
    mp.delenv("SG_BOARD_CACHE", raising=False)
    try:
        spec = importlib.util.spec_from_file_location("hub_swimlanes", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        mp.undo()


def _card(n, st="Todo", labels=(), s="OPEN", pa=None, sp=False):
    row = {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": s, "c": None, "st": st, "k": None, "l": list(labels),
           "si": {"total": None, "completed": None}, "o": "2026-10-01T09:00:00Z"}
    if pa is not None:
        row["pa"] = pa
    if sp:
        row["l"] = list(labels) + ["spec"]
    return row


def _node(script):
    """`board-lanes.js` then `script`, run by node; the script prints one JSON value."""
    if NODE is None:
        pytest.skip("node is required")
    src = LANES_JS.read_text() + "\n" + script
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _build(groups, mode="spec", **o):
    opts = {"mode": mode, "fw": [], "fs": [], "lc": {}, "hideDone": False, "closed": "Closed", **o}
    return _node(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));")


# ---- the server serves the parent spec on the row ----

def test_served_row_carries_its_parent_spec(hub, tmp_path, monkeypatch):
    cache = tmp_path / "hub-board.json"
    monkeypatch.setattr(hub, "BOARD", cache)
    monkeypatch.setattr(hub, "board_cache", {
        "t": 1.0, "rt": 1.0, "err": None, "lc": {}, "prs": [], "errs": [], "cases": [],
        "items": [_card(889, st="Doing", labels=["spec"], sp=True), _card(890, st="Todo", pa=889)],
    })
    out = hub.board()
    rows = {r["n"]: r for g in out["groups"] for r in g["items"]}
    assert rows[890]["pa"] == 889, "the child row does not carry its parent spec"
    assert "pa" not in rows[889], "a spec card has no parent spec of its own"


# ---- the selector and the page ----

def test_page_offers_spec_wave_and_none_with_spec_as_the_default(hub):
    page = hub.PAGE
    assert '<select id=lmode' in page, "the lane selector is missing"
    for value, label in (("spec", "Spec"), ("wave", "Wave"), ("none", "None")):
        assert f'<option value={value}' in page and f'>{label}</option>' in page, f"{label} is not an option"
    assert 'bo.lmode||"spec"' in page, "Spec is not the default lane mode"
    assert 'bo.lmode==="none"' in page, "None does not draw today's board"


def test_page_inlines_the_lane_rule(hub):
    assert "/*BOARD_LANES*/" not in hub.PAGE
    assert "function buildBoard" in hub.PAGE or "const buildBoard" in hub.PAGE


# ---- the lanes, in node over the page's own rule ----

def _row(n, col, labels, pa=None, sp=False, ho=None):
    row = {"n": n, "t": f"card {n}", "l": list(labels) + (["spec"] if sp and "spec" not in labels else []),
           "sp": sp or None, "c": None}
    if pa is not None:
        row["pa"] = pa
    if ho:
        row["ho"] = ho
    return {k: v for k, v in row.items() if v is not None}


GROUPS = [
    {"name": "Todo", "items": [
        _row(895, "Todo", ["bug", "loop-bug"]),
        _row(899, "Todo", [], pa=889),
        _row(900, "Todo", ["bug"]),
    ]},
    {"name": "Doing", "items": [
        _row(889, "Doing", ["spec"], sp=True),
        _row(890, "Doing", [], pa=889, ho="run-a"),
        _row(42, "Doing", [], pa=41),
    ]},
    {"name": "Closed", "items": [
        _row(41, "Closed", ["spec"], sp=True),
        _row(43, "Closed", [], pa=41),
    ]},
]


def test_loop_bugs_lane_comes_first_and_no_spec_lane_last(hub):
    model = _build(GROUPS)
    ids = [lane["id"] for lane in model["lanes"]]
    assert ids[0] == "bugs", f"Loop bugs is not first: {ids}"
    assert ids[-1] == "none", f"No spec is not last: {ids}"
    bugs = next(l for l in model["lanes"] if l["id"] == "bugs")
    assert bugs["title"] == "Loop bugs"
    assert [r["n"] for r in bugs["all"]] == [895]


def test_spec_lane_carries_number_title_column_and_closed_progress(hub):
    model = _build(GROUPS)
    lane = next(l for l in model["lanes"] if l["id"] == "s889")
    assert lane["num"] == 889
    assert lane["title"] == "card 889"
    assert lane["st"] == "Doing", "the header does not name the spec card's column"
    assert (lane["closed"], lane["total"]) == (0, 2), "progress is not closed tickets over all tickets"


def test_a_lane_with_a_running_card_sorts_before_one_without(hub):
    model = _build(GROUPS)
    spec_ids = [l["id"] for l in model["lanes"] if l["kind"] == "s"]
    assert spec_ids == ["s889", "s41"], f"the running lane does not come first: {spec_ids}"


def test_wave_mode_groups_by_wave_with_no_wave_last(hub):
    groups = [{"name": "Todo", "items": [
        _row(10, "Todo", ["wave:02"]), _row(11, "Todo", ["wave:01"]), _row(12, "Todo", []),
    ]}]
    model = _build(groups, mode="wave")
    assert [l["id"] for l in model["lanes"]] == ["w1", "w2", "nowave"]
    assert model["lanes"][0]["title"] == "Wave 1"
    assert model["lanes"][-1]["title"] == "No wave"


def test_spec_mode_draws_no_spec_cards_as_cards_only_in_their_lane(hub):
    model = _build(GROUPS)
    seen = [r["n"] for l in model["lanes"] for r in l["all"]]
    assert sorted(seen) == [42, 43, 890, 895, 899, 900], "a card appears in no lane or in two"
    assert 889 not in seen and 41 not in seen, "a spec card is drawn as a card, not as a lane header"


def test_a_card_shows_a_loop_bug_chip_or_a_bug_chip_as_text(hub):
    page = hub.PAGE
    assert '<span class="bugc loop">loop-bug</span>' in page, "a loop-bug card shows no loop-bug chip"
    assert '<span class="bugc">bug</span>' in page, "a bug card shows no bug chip"
