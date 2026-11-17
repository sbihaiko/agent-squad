"""The web board groups its cards in one swimlane per spec (SPEC 889, #890).

A card carries its parent spec as `pa`, served on its row, so the page builds the lanes with no GitHub read. Loop bugs
sit in a "Loop bugs" lane first (ADR-0146), other cards with no spec in a "No spec" lane last, and a lane with a running
card sorts before one without. The selector switches between Spec (the default) and None (today's board). The board has
no wave lane, no wave chip and no wave filter: a `wave:NN` label never makes a lane (SPEC 1965, #1988).

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


def _build(groups, **o):
    opts = {"fs": [], "lc": {}, "hideDone": False, "closed": "Closed", "sum": SUM, **o}
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

def test_the_page_draws_the_spec_lanes_with_no_lane_mode_control(hub):
    """The Swimlanes control left the page at the owner's request, and the lanes are the model it always drew: no page
    statement reads a lane mode, and the board draws its lanes unconditionally (SPEC 1965)."""
    page = hub.PAGE
    for gone in ("<select id=lmode", "<option value=wave>", "bo.lmode"):
        assert gone not in page, f"the page still draws {gone}"
    assert "const model=buildBoard(" in page, "the board does not draw its lanes"
    assert "const lanesHtml=laneHtml(model)" in page, "the lanes are drawn behind a mode test"


def test_the_hub_has_no_wave_lane_chip_or_filter(hub):
    """SPEC 1965, #1988: the hub drops the wave lane, the wave chip and the wave filter."""
    page, rule = hub.PAGE, LANES_JS.read_text()
    for gone in ("<option value=wave>", "<details id=fw", "waveOptions(", "waveChip("):
        assert gone not in page, f"the hub still draws {gone}"
    assert "data-k=wave" not in page, "a card still wears the wave chip"
    for gone in ("waveOptions", "waveChip", "waveLane", "laneWaveOf", 'mode==="wave"'):
        assert gone not in rule, f"the lane rule still carries {gone}"


def test_a_wave_label_never_makes_a_lane(hub):
    groups = [{"name": "Todo", "items": [
        _row(10, "Todo", ["wave:02"], pa=889), _row(11, "Todo", ["wave:01"], pa=889), _row(12, "Todo", [], pa=889),
    ]}]
    model = _build(groups)
    assert [ln["kind"] for ln in model["lanes"]] == ["s"], model["lanes"]
    assert [r["n"] for r in model["lanes"][0]["all"]] == [10, 11, 12]


def test_the_lane_counts_the_cards_a_run_holds():
    """Owner, 2026-11-02: the lane bar grows a share for the cards a run claims: the closed green read through, so the bar reads
    closed then in flight, both out of the lane's own total."""
    g = [{"name": "Todo", "end": False, "n": 0, "items": [_card(10, st="Todo")]},
         {"name": "Doing", "end": False, "n": 0, "items": [_card(11, st="Doing"), _card(12, st="Doing")]}]
    lane = _build(g, doing="Doing")["lanes"][0]
    assert (lane["closed"], lane["total"], lane["doing"]) == (0, 3, 2), lane


def test_a_lane_with_no_doing_card_draws_no_held_share():
    g = [{"name": "Todo", "end": False, "n": 0, "items": [_card(10, st="Todo")]},
         {"name": "Closed", "end": True, "n": 0, "items": [_card(11, st="Closed", s="CLOSED")]}]
    lane = _build(g, doing="Doing")["lanes"][0]
    assert (lane["doing"], lane["closed"]) == (0, 1), lane


def test_the_lane_counts_the_cards_in_review():
    """Owner, 2026-11-02: a lane bar carries a third share for the cards in review, after the closed and claiming shares."""
    g = [{"name": "Doing", "end": False, "n": 0, "items": [_card(11, st="Doing")]},
         {"name": "Code Review", "end": False, "n": 0, "items": [_card(12, st="Code Review"), _card(13, st="Code Review")]},
         {"name": "Closed", "end": True, "n": 0, "items": [_card(14, st="Closed", s="CLOSED")]},
         {"name": "Todo", "end": False, "n": 0, "items": [_card(15, st="Todo")]}]
    lane = _build(g, doing="Doing", review="Code Review")["lanes"][0]
    assert (lane["closed"], lane["total"], lane["doing"], lane["review"]) == (1, 5, 1, 2), lane


def test_a_lane_with_no_card_in_review_draws_no_review_share():
    g = [{"name": "Doing", "end": False, "n": 0, "items": [_card(11, st="Doing")]},
         {"name": "Todo", "end": False, "n": 0, "items": [_card(10, st="Todo")]}]
    lane = _build(g, doing="Doing", review="Code Review")["lanes"][0]
    assert lane["review"] == 0, lane


def test_page_draws_the_review_share_after_the_held_share(hub):
    page = hub.PAGE
    assert "/*BOARD_REVIEW*/" not in page, "the Code Review name did not reach the page"
    bar = page[page.index("class=bar title="):page.index("class=bar title=") + 600]
    assert "i class=rv" in bar, bar
    assert bar.index("i class=dn") < bar.index("i class=rv"), "the review share is drawn before the held share"
    css = (SCRIPTS / "board-lanes.css").read_text()
    rule = css[css.index("#bbody .lh .bar i.rv{"):]
    rule = rule[:rule.index("}")]
    assert "background:var(--ok)" in rule, rule
    held = css[css.index("#bbody .lh .bar i.dn{"):]
    held = held[:held.index("}")]
    assert "opacity:.45" in held and "opacity:.25" in rule, (held, rule)


def test_page_draws_the_doing_share_after_the_closed_share(hub):
    page = hub.PAGE
    assert "/*BOARD_DOING*/" not in page, "the Doing name did not reach the page"
    bar = page[page.index("class=bar title="):page.index("class=bar title=") + 400]
    assert "i class=dn" in bar and "left:${pct(l)}%" in bar, bar
    css = (SCRIPTS / "board-lanes.css").read_text()
    rule = css[css.index("#bbody .lh .bar i.dn{"):]
    rule = rule[:rule.index("}")]
    assert "background:var(--ok)" in rule, rule
    # A claimed card is not concrete work yet, so its share reads through the lane bar instead of painting over it.
    assert "opacity:" in rule and "opacity:1" not in rule, rule


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
    {"name": "Closed", "end": True, "n": 2, "items": []},
]
SUM = {
    "specs": [{"n": 889, "st": "Doing", "closed": 0, "total": 2}, {"n": 41, "st": "Closed", "closed": 1, "total": 2}],
    "waves": [],
}


def test_loop_bugs_lane_comes_first_and_no_spec_lane_last(hub):
    model = _build(GROUPS)
    ids = [lane["id"] for lane in model["lanes"]]
    assert ids[0] == "bugs", f"Loop bugs is not first: {ids}"
    assert ids[-1] == "none", f"No spec is not last: {ids}"
    bugs = next(ln for ln in model["lanes"] if ln["id"] == "bugs")
    assert bugs["title"] == "Loop bugs"
    assert [r["n"] for r in bugs["all"]] == [895]


def test_a_child_whose_spec_card_reads_todo_sits_in_that_spec_lane(hub):
    """#1431: the janitor returns a reopened spec's card to Todo, so the board draws the spec's children in its lane. A spec
    card in Todo is a lane like one in Doing, and a ticket of it never falls to No spec."""
    g = [{"name": "Todo", "items": [_row(1367, "Todo", ["spec"], sp=True), _row(1373, "Todo", [], pa=1367)]},
         {"name": "Closed", "end": True, "n": 0, "items": []}]
    summary = {"specs": [{"n": 1367, "st": "Todo", "closed": 0, "total": 1}], "waves": []}
    model = _build(g, sum=summary)
    ids = [lane["id"] for lane in model["lanes"]]
    assert "s1367" in ids, f"the spec card in Todo drew no lane: {ids}"
    assert "none" not in ids, f"the child fell to No spec: {ids}"
    lane = next(ln for ln in model["lanes"] if ln["id"] == "s1367")
    assert [r["n"] for r in lane["all"]] == [1373], lane
    assert lane["title"] == "card 1367", lane


def test_spec_lane_carries_number_title_column_and_closed_progress(hub):
    model = _build(GROUPS)
    lane = next(ln for ln in model["lanes"] if ln["id"] == "s889")
    assert lane["num"] == 889
    assert lane["title"] == "card 889"
    assert lane["st"] == "Doing", "the header does not name the spec card's column"
    assert (lane["closed"], lane["total"]) == (0, 2), "progress is not closed tickets over all tickets"


def test_a_lane_with_a_running_card_sorts_before_one_without(hub):
    """A closed spec draws no lane (owner, 2026-10-31: a finished spec floods the board), so one open lane is the set."""
    model = _build(GROUPS)
    spec_ids = [ln["id"] for ln in model["lanes"] if ln["kind"] == "s"]
    assert spec_ids == ["s889"], f"the open lane is not the only one: {spec_ids}"


def test_a_collapsed_spec_lane_keeps_its_count_per_column(hub):
    lane = next(ln for ln in _build(GROUPS, lc={"s889": True})["lanes"] if ln["id"] == "s889")
    assert lane["collapsed"] is True
    assert lane["counts"] == {"Todo": 1, "Doing": 1}


def test_spec_mode_draws_no_spec_cards_as_cards_only_in_their_lane(hub):
    """A card of a closed spec still shows: the open one falls to No spec, the closed one is history and draws nothing."""
    model = _build(GROUPS)
    seen = [r["n"] for ln in model["lanes"] for r in ln["all"]]
    assert sorted(seen) == [42, 890, 895, 899, 900], "a card appears in no lane or in two"
    assert 889 not in seen and 41 not in seen, "a spec card is drawn as a card, not as a lane header"
    assert 42 in [r["n"] for ln in model["lanes"] if ln["id"] == "none" for r in ln["all"]], "an open card of a closed spec is lost"


def test_a_card_shows_a_loop_bug_chip_or_a_bug_chip_as_text(hub):
    page = hub.PAGE
    assert '<span class="bugc loop">loop-bug</span>' in page, "a loop-bug card shows no loop-bug chip"
    assert '<span class="bugc">bug</span>' in page, "a bug card shows no bug chip"


# ---- the board answer carries the open rows and the summaries, never a closed row (#972) ----

def _serve(hub, tmp_path, monkeypatch, items):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {
        "t": 1.0, "rt": 1.0, "err": None, "lc": {}, "prs": [], "errs": [], "cases": [], "items": items,
    })


def _entry(rows, **key):
    return next(r for r in rows if all(r[k] == v for k, v in key.items()))


def _ends_fixture():
    return [
        _card(889, st="Doing", labels=["spec"], sp=True),
        _card(890, st="Closed", pa=889, labels=["wave:01"]),
        _card(891, st="Abandoned", pa=889),
        _card(892, st="Todo", pa=889, labels=["wave:01"]),
        _card(41, st="Closed", labels=["spec"], sp=True),
        _card(43, st="Closed", pa=41),
        _card(44, st="Todo", pa=41),
    ]


def test_the_board_answer_holds_no_closed_or_abandoned_row(hub, tmp_path, monkeypatch):
    _serve(hub, tmp_path, monkeypatch, _ends_fixture())
    out = hub.board()
    served = sorted(r["n"] for g in out["groups"] for r in g["items"])
    assert served == [44, 889, 892], f"a closed or abandoned card reached the board answer: {served}"
    ends = {g["name"]: g for g in out["groups"] if g.get("end")}
    assert (ends["Closed"]["items"], ends["Closed"]["n"]) == ([], 3), "the Closed count or its rows are wrong"
    assert (ends["Abandoned"]["items"], ends["Abandoned"]["n"]) == ([], 1), "the Abandoned count or its rows are wrong"


def test_the_board_answer_carries_one_summary_per_spec_and_per_wave(hub, tmp_path, monkeypatch):
    _serve(hub, tmp_path, monkeypatch, _ends_fixture())
    sums = hub.board()["sum"]
    # `far` is the furthest column the spec's tickets reached, a count beside the spec and never its own column (#1906);
    # an Abandoned ticket names no furthest column, and a closed spec's tickets count by its own summary alone.
    assert _entry(sums["specs"], n=889) == {"n": 889, "st": "Doing", "closed": 1, "total": 3, "far": "Closed", "far_n": 1}
    assert _entry(sums["specs"], n=41) == {"n": 41, "st": "Closed", "closed": 1, "total": 2, "far": None, "far_n": 0}
    assert _entry(sums["waves"], spec=889, wave=1) == {"spec": 889, "wave": 1, "closed": 1, "total": 2}
    assert _entry(sums["waves"], spec=889, wave=None) == {"spec": 889, "wave": None, "closed": 0, "total": 1}


def test_the_no_spec_summary_counts_no_spec_tickets_and_no_spec_card(hub, tmp_path, monkeypatch):
    _serve(hub, tmp_path, monkeypatch, [
        _card(889, st="Doing", labels=["spec"], sp=True),
        _card(41, st="Closed", labels=["spec"], sp=True),
        _card(46, st="Todo"),
    ])
    assert _entry(hub.board()["sum"]["waves"], spec=None, wave=None) == {"spec": None, "wave": None, "closed": 0, "total": 1}


def test_a_ticket_of_a_closed_spec_counts_in_the_no_spec_summary(hub, tmp_path, monkeypatch):
    _serve(hub, tmp_path, monkeypatch, [
        _card(41, st="Closed", labels=["spec"], sp=True),
        _card(44, st="Todo", pa=41),
    ])
    assert _entry(hub.board()["sum"]["waves"], spec=None, wave=None) == {"spec": None, "wave": None, "closed": 0, "total": 1}


# ---- the No spec lane's bar (owner): open no-spec cards plus the last seven days closed ----

def test_the_no_spec_lane_takes_its_count_from_the_summary(hub):
    model = _build(GROUPS, sum=dict(SUM, none={"closed": 3, "total": 4}))
    none = next(ln for ln in model["lanes"] if ln["id"] == "none")
    assert (none["closed"], none["total"]) == (3, 4)


def _lane_header(hub, lane):
    import re
    page = hub.PAGE
    body = re.search(r"const laneHtml=m=>\{.*?\n return h\+\"</div>\"\};", page, re.S).group(0)
    stubs = ('const esc=s=>String(s),colOf=()=>"",CN={},eye=()=>"",SLOTS=new Map(),cards=()=>"",bo={},f="",'
             'BOARD_VOCAB={todo:"Todo",doing:"Doing",review:"Code Review"};')
    model = {"lanes": [dict(lane, collapsed=True, counts={}, cells={})], "vc": [], "cols": []}
    return _node(stubs + body + f"console.log(JSON.stringify(laneHtml({json.dumps(model)})));")


def test_the_no_spec_lane_draws_the_spec_bar(hub):
    html = _lane_header(hub, {"id": "none", "kind": "none", "title": "No spec", "closed": 3, "total": 4})
    assert '<span class="lf lf-bar" style="justify-content:flex-start"><span class=bar title="3 of 4 closed"><i style="width:75%"></i></span></span>' in html
    assert '<span class=pc>3/4</span>' in html
    spec = _lane_header(hub, {"id": "s9", "kind": "s", "num": 9, "title": "t", "closed": 1, "total": 2})
    assert "<span class=pc>1/2</span>" in spec
    bugs = _lane_header(hub, {"id": "bugs", "kind": "bugs", "title": "Loop bugs", "closed": 0, "total": 1})
    assert "class=bar" not in bugs


# ---- spec lanes read closest to delivery first, No spec last (#1157) ----

def _order_groups():
    return [
        {"name": "Backlog", "end": False, "items": [
            _row(20, "Backlog", [], sp=True), _row(201, "Backlog", [], pa=20), _row(202, "Backlog", [], pa=20)]},
        {"name": "Todo", "end": False, "items": [
            _row(30, "Todo", [], sp=True), _row(301, "Todo", [], pa=30), _row(302, "Todo", [], pa=30),
            _row(303, "Todo", [], pa=30), _row(304, "Todo", [], pa=30),
            _row(40, "Todo", [], sp=True), _row(900, "Todo", [])]},
        {"name": "Doing", "end": False, "items": [_row(305, "Doing", [], pa=30)]},
        {"name": "Closed", "end": True, "items": [_row(403, "Closed", [], pa=40), _row(404, "Closed", [], pa=40)]},
    ]


def test_spec_lanes_read_closest_to_delivery_first_with_no_spec_last():
    """A finished spec (all tickets closed) leads, a started spec follows, an unstarted spec comes after, No spec is last."""
    groups = _order_groups()
    model = _build(groups, sum={})
    assert [ln["id"] for ln in model["lanes"]] == ["s40", "s30", "s20", "none"]


def test_started_specs_tie_break_by_tickets_in_flight_then_number():
    groups = [
        {"name": "Todo", "end": False, "items": [
            _row(81, "Todo", [], sp=True), _row(62, "Todo", [], sp=True),
            _row(75, "Todo", [], sp=True), _row(63, "Todo", [], sp=True),
            _row(634, "Todo", [], pa=63)]},
        {"name": "Backlog", "end": False, "items": [_row(752, "Backlog", [], pa=75)]},   # not in flight: Todo is (#1254)
        {"name": "Doing", "end": False, "items": [
            _row(812, "Doing", [], pa=81), _row(622, "Doing", [], pa=62),
            _row(632, "Doing", [], pa=63), _row(633, "Doing", [], pa=63)]},
        {"name": "Closed", "end": True, "items": [
            _row(811, "Closed", [], pa=81), _row(621, "Closed", [], pa=62), _row(751, "Closed", [], pa=75),
            _row(631, "Closed", [], pa=63)]},
    ]
    model = _build(groups, sum={})
    assert [ln["id"] for ln in model["lanes"]] == ["s62", "s81", "s75", "s63"]
    flipped = [{**g, "items": list(reversed(g["items"]))} for g in reversed(groups)]
    assert [ln["id"] for ln in _build(flipped, sum={})["lanes"]] == ["s62", "s81", "s75", "s63"], "the order moves with the payload's row order"
