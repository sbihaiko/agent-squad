"""The TASKS header opens two diagrams: the cumulative flow bands and the lead-time control chart (SPEC 111, #712).

The hub computes every number with board_cfd and board_leadtime, from the history store's column intervals (#1024); the
page draws them and does no counting. Both diagrams ride the board read the hub already makes: no GitHub call, no timer,
no process of their own.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
OPENED = "2026-10-01T09:00:00Z"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _chart_days_end_in_utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_flow_charts")
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
        yield _load("hub_flow_charts", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    return tmp_path


def _card(n, st="Todo", s="OPEN", c=None, o=OPENED, labels=()):
    return {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": s, "c": c, "st": st, "k": None, "l": list(labels),
            "si": {"total": None, "completed": None}, "o": o}


def _life(opened, *steps):
    """A card created in Todo at `opened`, then each (instant, status) step; a step into Closed also closes the card."""
    events = [{"t": opened, "type": "added"}, {"t": opened, "type": "status", "status": "Todo"}]
    for at, status in steps:
        events.append({"t": at, "type": "status", "status": status})
        if status == "Closed":
            events.append({"t": at, "type": "closed"})
    return events


def _todo(n):
    return _card(n, st="Todo", s="OPEN"), _life(OPENED)


def _done(n, closed_at):
    return _card(n, st="Closed", s="CLOSED", c=closed_at), _life(OPENED, (closed_at, "Closed"))


def _seed(hub, cards):
    """The board's items and the history store's records for `cards`, a map of number to (item, events) (#1023)."""
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    for n, (item, events) in cards.items():
        hub._hist.record_issue(path, n, OPENED, OPENED, events, True, now=time.time(), keys=hub.bar_keys(item))
    hub.board_cache["items"] = [item for item, _ in cards.values()]


def _intervals(hub):
    store = hub._hist.load_store(hub._hist.history_path(hub.BOARD, hub.REPO))
    return [hub._hist.intervals(rec["events"]) for rec in store.values()]


def _count(view, name, day):
    band = next(b for b in view["bands"] if b["name"] == name)
    return band["count"][view["days"].index(day)]


STORAGE = "const localStorage={getItem:()=>null,setItem:()=>{}};"   # the node harness holds no page key, so the flow filters start at their defaults (#1356)


def _flow_block(hub):
    """The page's diagram script, between its two markers."""
    m = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", hub.PAGE, re.S)
    assert m, "the page carries no flow-charts block"
    return m.group(1)


def _flow(hub):
    """Both chart routes' answers, under the keys the tests read."""
    return {"cfd": hub.flow_cfd(ty=None)["cfd"], "lead": hub.flow_lead(ty=None)["lead"]}


def test_tasks_header_has_flow_and_lead_time_buttons_and_keeps_its_controls(hub):
    # Re-anchored to the owner-approved mock (#889): Flow and Lead time sit on the Flow charts tab, not in the board's toolbar.
    tabs = hub.PAGE[hub.PAGE.index("<div class=rkt id=flowtabs>"):hub.PAGE.index("<div id=flowchips>")]
    # Re-anchored by the owner (2026-11-02): the burn-down tab is gone, so the charts are the two the owner keeps; Runs is a top tab (#1272).
    assert [re.sub(r"<[^>]*>", "", x) for x in re.findall(r"<button[^>]*>.*?</button>", tabs)] == ["Cumulative Flow", "Lead Time"]
    assert "id=flowv" not in hub.PAGE, "the View selector is gone: a chart is a tab"
    assert 'flowOpen=async k=>{FLOWK=k;$("#flowov").hidden=false;flowTabs();' in hub.PAGE, "the lit tab follows the chart drawn"
    head = hub.PAGE[hub.PAGE.index("<div id=bhead>"):hub.PAGE.index("<div id=bcases>", hub.PAGE.index("<div id=bhead>"))]
    assert "<span id=bt class=m></span>" in head
    assert "id=bflow" not in head and "id=blead" not in head
    assert '<details id=fs' in head and '<details id=fk' in head, "the header lost the filter bar"
    assert 'id=bsp' not in head and 'id=bz' not in head and 'id=bx' not in head, "a removed control is still in the header"


def _visit(hub, tmp_path, name, stored, steps):
    """The page's tab and chart-pick code run under node over `stored` localStorage: each step is one JS call, then
    the charts opened, the chart open now, and the stored text after the steps."""
    page = hub.PAGE
    pick = re.search(r"^const flowPick=[^\n]*", page, re.M)

    def line(pattern):
        m = re.search(pattern, page, re.M | re.S)
        assert m, f"the page has no {pattern!r}"
        return m.group(0)

    drawing = "\n".join([
        f"const localStorage={{hub:{json.dumps(stored)}}};",
        'let TAB="board",pins=new Set(),q="",per=10,src="",FLOWK=null;',
        'const sTheme=null,setTheme=()=>{},drawMain=()=>{},document={querySelectorAll:()=>[]};',
        'const location={hash:""},history={replaceState(){}};',
        'const el={"#flowov":{hidden:true}};const $=s=>el[s]||(el[s]={classList:{toggle(){}}});',
        'const ls=JSON.parse(localStorage.hub||"{}");const opened=[];',
        'const flowOpen=k=>{FLOWK=k;$("#flowov").hidden=false;opened.push(k)};',
        line(r"^const save=[^\n]*"), line(r"^const TABS=[^\n]*"), line(r"^const HASH=[^\n]*"),
        line(r"^function setTab\(.*?drawMain\(\)\}"),
        pick.group(0) if pick else "",
        *steps,
        'console.log(JSON.stringify({opened,FLOWK,stored:localStorage.hub}));',
    ])
    path = tmp_path / name
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_first_visit_to_flow_opens_cumulative_flow(hub, tmp_path):
    out = _visit(hub, tmp_path, "flow-first-visit.js", "{}", ['setTab("flow")'])
    assert out["opened"] == ["cfd"] and out["FLOWK"] == "cfd"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_flow_reopens_the_chart_last_picked_after_a_reload(hub, tmp_path):
    picked = _visit(hub, tmp_path, "flow-pick-lead.js", "{}", ['flowPick("lead")'])
    again = _visit(hub, tmp_path, "flow-reload.js", picked["stored"], ['setTab("flow")'])
    assert again["opened"] == ["lead"] and again["FLOWK"] == "lead"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_entering_flow_twice_in_a_row_reopens_the_chart_picked_in_between(hub, tmp_path):
    out = _visit(hub, tmp_path, "flow-reenter.js", "{}",
                 ['setTab("flow")', 'flowPick("lead")', 'setTab("board")', 'setTab("flow")'])
    assert out["opened"] == ["cfd", "lead"] and out["FLOWK"] == "lead"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_stored_pick_that_names_no_chart_opens_cumulative_flow(hub, tmp_path):
    out = _visit(hub, tmp_path, "flow-unknown-pick.js", '{"flowk":"burn"}', ['setTab("flow")'])
    assert out["opened"] == ["cfd"]


def test_the_top_bar_gains_no_tab_when_a_chart_opens(hub):
    top = hub.PAGE[hub.PAGE.index("<div class=tabs role=tablist>"):]
    top = top[:top.index("</div>")]
    assert re.findall(r"data-t=(\w+)", top) == ["board", "runs", "flow", "prof", "set"], "a chart is a tab of the top bar"


def test_cfd_bands_follow_the_hub_order(hub, board):
    _seed(hub, {1: _todo(1), 2: _done(2, "2026-10-02T09:00:00Z")})
    cfd = _flow(hub)["cfd"]
    assert [b["name"] for b in cfd["bands"]] == list(hub.ORDER), "the drawn bands name every column, in the hub's order"
    assert cfd["start"] == "2026-09-30", "the history opens on the empty day before the first card"
    assert "history from " in hub.PAGE


def test_a_card_labelled_moved_is_drawn_in_no_band(hub, board):
    """#1663: a card moved to another board is marked `moved` and is no part of this board's flow, so no band counts it."""
    moved = _card(2, st="Todo", labels=("moved",))
    _seed(hub, {1: _todo(1), 2: (moved, _life(OPENED) + [{"t": OPENED, "type": "label", "name": "moved"}])})
    cfd = _flow(hub)["cfd"]
    day = cfd["days"][-1]
    assert sum(cfd["ranges"]["all"]["reads"][-1]["counts"][k]["count"] for k in range(len(cfd["bands"]))) == 1, day
    assert _count(cfd, hub.COLUMN["issue_todo"], day) == 1


def test_cfd_counts_per_day_are_the_cards_in_each_column(hub, board):
    _seed(hub, {1: _todo(1), 2: _todo(2), 3: _done(3, "2026-10-01T15:00:00Z")})
    cfd = _flow(hub)["cfd"]
    assert _count(cfd, hub.COLUMN["issue_todo"], "2026-10-01") == 2
    assert _count(cfd, hub.COLUMN["issue_closed"], "2026-10-01") == 1


def test_another_repositorys_history_is_not_drawn(hub, board):
    hub._hist.record_issue(hub._hist.history_path(hub.BOARD, "x/other"), 1, OPENED, OPENED, _life(OPENED), True,
                           now=time.time())
    hub.board_cache["items"] = [_todo(1)[0]]
    cfd = _flow(hub)["cfd"]
    assert cfd["start"] is None
    assert cfd["days"] == []


def test_no_history_says_no_sample_and_draws_no_band(hub, board):
    cfd = _flow(hub)["cfd"]
    assert cfd["start"] is None
    assert cfd["days"] == []
    assert cfd["bands"] == [] or all(b["count"] == [] for b in cfd["bands"])
    assert "the flow history has no sample yet" in hub.PAGE


def test_lead_time_summary_measure_window_and_excluded_count(hub, board):
    _seed(hub, {
        1: _done(1, "2026-10-01T15:00:00Z"),
        2: _done(2, "2026-10-02T09:00:00Z"),
        3: _done(3, "2026-10-04T09:00:00Z"),
        4: _todo(4),
    })
    lead = _flow(hub)["lead"]
    assert lead["measure"] == "Todo -> closed", "re-anchored (#1247): the view measures from Todo"
    assert lead["window"] == {"size": 20, "unit": "issues"}
    assert lead["summary"]["average"]["text"] == "1d 10h"
    assert lead["summary"]["median"]["text"] == "1d"
    assert lead["summary"]["minimum"]["text"] == "6h"
    assert lead["summary"]["maximum"]["text"] == "3d"
    assert lead["excluded"] == []  # re-anchored (#1250): an open card is censored, never an exclusion
    assert lead["excluded_total"] == 0


def test_a_card_closed_as_not_planned_is_no_delivery_and_the_chart_names_it(hub, board):
    """#921: a card closed as NOT_PLANNED, or as a duplicate, with Status Closed draws Abandoned, is no lead-time dot, and
    its count is named beside the chart's excluded total."""
    item2, events2 = _done(2, "2026-10-02T09:00:00Z")
    item3, events3 = _done(3, "2026-10-03T09:00:00Z")
    item4, events4 = _done(4, "2026-10-04T09:00:00Z")
    _seed(hub, {
        1: _done(1, "2026-10-01T15:00:00Z"),
        2: ({**item2, "cr": "not_planned"}, events2),
        3: ({**item3, "cr": "not_planned"}, events3),
        4: ({**item4, "cr": "duplicate"}, events4),
    })

    lead = _flow(hub)["lead"]
    groups = hub.board()["groups"]

    assert [p["n"] for p in lead["series"]] == [1]
    assert lead["summary"]["median"]["text"] == "6h"
    assert lead["excluded"] == [{"reason": "not_planned", "count": 3}]
    assert lead["excluded_total"] == 3
    abandoned = next(g for g in groups if g["name"] == "Abandoned")
    assert abandoned["items"] == [] and abandoned["n"] == 3, "a card closed as not planned is counted Abandoned, never Closed"
    closed = next(g for g in groups if g["name"] == hub.COLUMN["issue_closed"])
    assert closed["items"] == [] and closed["n"] == 1


def test_a_quiet_day_keeps_each_card_in_its_column(hub, board):
    _seed(hub, {1: _done(1, "2026-10-03T09:00:00Z")})
    cfd = _flow(hub)["cfd"]
    assert _count(cfd, hub.COLUMN["issue_todo"], "2026-10-02") == 1, "a day with no move carries the card where it stood"
    assert _count(cfd, hub.COLUMN["issue_closed"], "2026-10-02") == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_lead_time_chart_names_the_cards_closed_as_not_planned(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        'process.stdout.write(flowLead({type:"task",measure:"on board -> closed",window:{size:20,unit:"issues"},'
        'excluded:[{reason:"not_planned",count:3}],excluded_total:3,'
        'excluded_reasons:{not_planned:"closed as not planned"},series:[],summary:null}));',
    ])
    path = tmp_path / "flow-lead-not-planned.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "3 closed as not planned" in result.stdout


def test_opening_the_diagrams_makes_no_gh_call(hub, board, monkeypatch):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    calls = []

    def gh_fails(cmd, **kw):
        calls.append(cmd)
        raise FileNotFoundError("gh is not on PATH")

    monkeypatch.setattr(hub.subprocess, "run", gh_fails)
    flow = _flow(hub)
    assert calls == []
    assert flow["cfd"]["bands"] and flow["lead"]["summary"] is not None
    assert 'id=flowov hidden' in hub.PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_one_point_run_is_drawn_as_a_visible_mark(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        'process.stdout.write(flowCFD({days:["a","b","c"],ymax:4,yticks:[0,2,4],'
        'xticks:[{i:0,day:"a"},{i:2,day:"c"}],start:"2026-10-01",'
        'bands:[{name:"Todo",lower:[0,null,0],upper:[2,null,1]}]}));',
    ])
    path = tmp_path / "flow-one.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    polygons = [[float(p.split(",")[0]) for p in m.split()] for m in re.findall(r'points="([^"]*)"', result.stdout)]
    assert len(polygons) == 2
    for xs in polygons:
        assert max(xs) - min(xs) > 0, "a one-point run has width: it is a mark, not a zero-width line"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_splits_each_band_at_a_gap(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        'process.stdout.write(flowCFD({days:["a","b","c","d","e"],ymax:4,yticks:[0,2,4],'
        'xticks:[{i:0,day:"a"},{i:4,day:"e"}],start:"2026-10-01",'
        'bands:[{name:"Todo",lower:[0,0,null,0,0],upper:[2,3,null,1,2]}]}));',
    ])
    path = tmp_path / "flow-cfd.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    polygons = [[float(p.split(",")[0]) for p in m.split()] for m in re.findall(r'points="([^"]*)"', result.stdout)]
    assert len(polygons) == 2, "one polygon per run of sampled days, never one across the gap"
    x = {i: 36 + i * (800 - 2 * 36) / 4 for i in range(5)}
    assert all(v in (x[0], x[1]) for v in polygons[0]) and all(v in (x[3], x[4]) for v in polygons[1])


def test_page_draws_the_hub_numbers_and_does_no_arithmetic(hub, board):
    block = _flow_block(hub)
    for forbidden in ("reduce(", "Math.", "+=", "-=", "fmean(", "median(", "average(", "saveB(", "bo.", "#bbody"):
        assert forbidden not in block, f"the flow script names {forbidden}"


def test_drawn_cfd_and_lead_time_equal_what_board_cfd_and_board_leadtime_return(hub, board):
    cfd_mod = _load("board_cfd_check", SCRIPTS / "board_cfd.py")
    _seed(hub, {1: _done(1, "2026-10-01T15:00:00Z"), 2: _todo(2)})

    flow = _flow(hub)
    direct = cfd_mod.cfd(_intervals(hub), hub.ORDER, zone="UTC", now=datetime.now(UTC))
    assert len(direct["points"]) == len(flow["cfd"]["days"])
    for band in flow["cfd"]["bands"]:
        for i, point in enumerate(direct["points"]):
            assert band["upper"][i] - band["lower"][i] == point["bands"][band["name"]]

    assert flow["lead"]["summary"]["average"]["hours"] == 6.0
    assert flow["lead"]["excluded"] == []  # re-anchored (#1250): an open card is censored, never an exclusion


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_page_script_parses(hub, tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", hub.PAGE, re.S)
    assert scripts
    for i, text in enumerate(scripts):
        path = tmp_path / f"hub-page-{i}.js"
        path.write_text(text)
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def _stack(hub):
    """Five open Todo cards and three Closed cards: eight cards in the stack on every day from the first."""
    _seed(hub, {**{n: _todo(n) for n in range(1, 6)},
                **{n: _done(n, "2026-10-01T15:00:00Z") for n in range(6, 9)}})


def test_the_cfd_serves_numbered_y_ticks_and_dated_x_ticks(hub, board):
    """#896 (1): the Cumulative flow chart's y axis is numbered from zero to past the tallest stack, and its x axis
    names the dates of the history."""
    _stack(hub)
    cfd = _flow(hub)["cfd"]
    ticks = cfd["yticks"]
    assert ticks[0] == 0 and all(isinstance(v, int) for v in ticks)
    assert ticks == sorted(set(ticks)), f"the y ticks rise: {ticks}"
    assert len(ticks) >= 3, f"a numbered y axis carries more than its foot and top: {ticks}"
    assert ticks[-1] == cfd["ymax"] >= 8, f"the axis reaches past the tallest stack of 8: {ticks}"
    assert cfd["xticks"][0] == {"i": 0, "day": cfd["days"][0]} and cfd["days"][0] == "2026-09-30"   # the empty day before the first card
    assert cfd["xticks"][-1] == {"i": len(cfd["days"]) - 1, "day": cfd["days"][-1]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cfd_draws_labelled_axes_with_numbered_ticks_and_dated_ticks(hub, board, tmp_path):
    """#896 (1): the page draws both axes, one labelled tick per number the hub served and one per date."""
    _stack(hub)
    cfd = _flow(hub)["cfd"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        f"process.stdout.write(flowCFD({json.dumps(cfd)}));",
    ])
    path = tmp_path / "flow-cfd-axis.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    labels = re.findall(r"<text[^>]*>([^<]*)</text>", result.stdout)
    for v in cfd["yticks"]:
        assert str(v) in labels, f"the y tick {v} carries no label: {labels}"
    for t in cfd["xticks"]:
        assert t["day"] in labels, f"the x tick {t['day']} carries no label: {labels}"
    assert "cards" in labels and f"day ({cfd['zone']})" in labels, f"an axis carries no name: {labels}"
    ticks = re.findall(r"<line ", result.stdout)
    assert len(ticks) == 2 + len(cfd["yticks"]) + len(cfd["xticks"]), "one mark per tick between the two axes"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_band_drawn_has_a_legend_entry_in_the_hub_order(hub, board, tmp_path):
    """#896 (2): the bands the chart draws and the legend's entries are one list, in the drawn order."""
    _stack(hub)
    cfd = _flow(hub)["cfd"]
    names = [b["name"] for b in cfd["bands"]]
    assert names == list(hub.ORDER)
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        f"process.stdout.write(flowCFD({json.dumps(cfd)}));",
    ])
    path = tmp_path / "flow-cfd-legend.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    legend = re.findall(r"<span class=flg><i[^>]*></i>([^<]*)</span>", result.stdout)
    assert legend == names, "every band drawn has exactly one legend entry, in the drawn order"
    assert len(re.findall(r"<polygon ", result.stdout)) == len(names), "the legend counts the bands the chart draws"


# SPEC 1356: the cumulative flow takes a Type and a set of visible states. The three bands stack from the last at the
# foot, so a hidden middle band drops its cards from the two above it and leaves no gap.
CFD_STACK = {
    "days": ["2026-10-01"], "zone": "UTC", "start": "2026-10-01",
    "ymax": 3, "yticks": [0, 1, 2, 3], "xticks": [{"i": 0, "day": "2026-10-01"}], "coverage": None,
    "bands": [
        {"name": "Todo", "count": [1], "held": [0]},
        {"name": "Doing", "count": [1], "held": [0]},
        {"name": "Closed", "count": [1], "held": [0]}],
    "reads": [{"day": "2026-10-01", "counts": [{"name": "Todo", "count": 1}, {"name": "Doing", "count": 1},
                                             {"name": "Closed", "count": 1}], "wip": 2, "throughput": 0}],
    "last": {"day": "2026-10-01", "wip": 2, "throughput": 0},
}



def _polygons(markup):
    return [(m.group(2), [tuple(map(float, p.split(","))) for p in m.group(1).split()])
            for m in re.finditer(r'<polygon points="([^"]*)"[^>]*><title>([^<]*)</title>', markup)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cumulative_flow_offers_four_types_and_one_state_checkbox_per_band(hub, board, tmp_path):
    """#1356: the control row carries the four types and one checkbox per board-defined column; it opens on the tasks
    default the chart route always sent, so the page's first request is the request it always made."""
    out = _render(hub, tmp_path, "flow-cfd-controls.js", f"flowCFD({json.dumps(CFD_STACK)})")
    types = re.search(r"<select id=cfdy>(.*?)</select>", out)
    assert types, "the cumulative flow draws no Type control"
    assert re.findall(r"<option value=(all|spec|task|bug)([^>]*)>([^<]*)</option>", types.group(1)) == [
        ("all", "", "All"), ("spec", "", "Specs"), ("task", " selected", "Tasks"), ("bug", "", "Bugs")], \
        "the Type control offers All, Specs, Tasks and Bugs, and opens on Tasks"
    states = re.findall(r'<input type=checkbox data-state="([^"]+)"([^>]*)>', out)
    assert [name for name, _ in states] == ["Todo", "Doing", "Closed"], "one checkbox per band, in the drawn order"
    assert all(" checked" in attrs for _, attrs in states), "every state shows at first"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_hiding_a_middle_state_drops_its_band_and_restacks_without_a_gap(hub, board, tmp_path):
    """#1356: hiding a middle band removes it from the plot and the legend, and the bands above it drop by its count."""
    shown = _render(hub, tmp_path, "flow-cfd-all.js", f"flowCFD({json.dumps(CFD_STACK)})")
    hidden = _render(hub, tmp_path, "flow-cfd-hidden.js", f"(CF.hidden=['Doing'],flowCFD({json.dumps(CFD_STACK)}))")

    full = dict(_polygons(shown))
    assert sorted(full) == ["Closed", "Doing", "Todo"]
    assert len({y for points in full.values() for _, y in points}) == 4, "the drawn stack has four levels with every band"

    kept = dict(_polygons(hidden))
    assert sorted(kept) == ["Closed", "Todo"], "the hidden band is not drawn"
    levels = {y for points in kept.values() for _, y in points}
    assert len(levels) == 3, f"the remaining bands re-stack with no gap: {sorted(levels)}"
    # A reference computation from the fixture's counts and the page's own layout, never the fixture's lower/upper: the
    # closed band keeps the floor, the todo band sits on top of it, and no level repeats a hidden band's.
    page = hub.PAGE
    flow_h = int(re.search(r"flowW=\d+,flowH=(\d+)", page).group(1))
    flow_p = int(re.search(r"const flowP=(\d+)", page).group(1))
    scale = (flow_h - 2 * flow_p) / CFD_STACK["ymax"]
    def level(v):
        return flow_h - flow_p - v * scale
    todo, closed = kept["Todo"], kept["Closed"]
    assert sorted({y for _, y in closed}) == pytest.approx(sorted([level(0), level(1)])), \
        "the closed band does not sit on the floor across its own count"
    assert sorted({y for _, y in todo}) == pytest.approx(sorted([level(1), level(2)])), \
        "the todo band does not re-stack on the closed one: the hidden band's count is dropped from the stack"
    assert max(y for _, y in todo) == min(y for _, y in closed), "the band above the hidden one sits on the band below it"
    assert max(y for _, y in closed) == max(y for _, y in full["Closed"]), "the foot of the stack does not move"

    legend = re.findall(r"<span class=flg><i[^>]*></i>([^<]*)</span>", hidden)
    assert legend == ["Todo", "Closed"], "the legend lists only the visible states"
    assert "Doing 1" not in hidden, "the day hover names only the visible states"
    assert "WIP 2 (whole board)" in hidden and "WIP <b>2</b> (whole board)" in hidden, \
        "the hover and the notes label WIP as the whole board's, never the drawn subset's"
    assert re.search(r'<input type=checkbox data-state="Doing"[^>]*>', hidden).group(0).find(" checked") < 0, \
        "the hidden state's checkbox reads unchecked"


def _capturing_store(initial):
    """The page's own key, captured: `getItem` answers the seed, `setItem` records each write, and the harness prints it."""
    return ("const STORE=" + json.dumps(initial) + ";"
            "const localStorage={getItem:k=>k in STORE?STORE[k]:null,setItem:(k,v)=>{STORE[k]=String(v)}};")


def _stale_render(hub, tmp_path, name, block=None):
    """A render whose stored hidden set names a state the payload does not carry; answers the markup and the page key."""
    seeded = {"squad-flow": json.dumps({"ty": "task", "hidden": ["Some Lane"]})}
    expression = ("(()=>{const markup=flowCFD(" + json.dumps(CFD_STACK) +
                  ");return JSON.stringify({markup,key:STORE['squad-flow']});})()")
    out = _render(hub, tmp_path, name, expression, block=block, storage=_capturing_store(seeded))
    return json.loads(out)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_hidden_state_the_board_definition_does_not_name_never_shows(hub, board, tmp_path):
    """#1356: a stored state the payload does not carry adds no checkbox, changes no band, and is pruned from the page
    key, so the saved set only ever holds board-defined states — proven by the key the page's own save wrote."""
    plain = _render(hub, tmp_path, "flow-cfd-plain.js", f"flowCFD({json.dumps(CFD_STACK)})")
    stale = _stale_render(hub, tmp_path, "flow-cfd-stale.js")
    assert "Some Lane" not in stale["markup"], "an unknown state is never drawn, named or offered"
    assert "Some Lane" not in plain
    def boxes(markup):
        return re.findall(r'<input type=checkbox data-state="([^"]+)"', markup)
    assert boxes(stale["markup"]) == ["Todo", "Doing", "Closed"] and boxes(stale["markup"]) == boxes(plain), \
        "an unknown hidden state adds no checkbox: the control set is the plain render's exactly"
    assert dict(_polygons(stale["markup"])).keys() == dict(_polygons(plain)).keys(), \
        "an unknown hidden state changes no band"
    assert json.loads(stale["key"]) == {"ty": "task", "hidden": []}, \
        "the pruned set is written back to the page key, not only held in memory"
    # The check bites: with the page's save removed, the same render leaves the stale name in the key.
    save_line = next(line for line in hub.PAGE.splitlines() if line.startswith("const flowSave="))
    block = _flow_block(hub).split("const drawFlow")[0].replace(save_line, "const flowSave=()=>{};")
    assert "const flowSave=()=>{};" in block, "the harness did not replace the page's save line"
    unsaved = _stale_render(hub, tmp_path, "flow-cfd-stale-nosave.js", block=block)
    assert json.loads(unsaved["key"]) == {"ty": "task", "hidden": ["Some Lane"]}, \
        "the assertion reads the page's own write: a render that prunes without saving leaves the stale name"


# SPEC 889 #900: the lead-time diagram is the Jira-style control chart, computed by the hub (board_leadtime).
TIMEFRAME_KEYS = ["12h", "1", "2", "3", "7", "14", "30", "all"]   # 24, 48 and 72 hours replaced the 3- and 6-month presets (2026-11-01); 12 hours leads (#2029)


def test_the_lead_chart_shares_its_plot_frame_with_hub_clustering_and_page(hub, board):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})

    payload = hub.flow_lead()
    lead = payload["lead"]
    chart = lead["control"]["all"]

    assert payload["plot_frame"] == {"width": 672, "height": 212}
    assert chart["plot_frame"] == payload["plot_frame"]
    assert "flowW-2*ltP" not in _flow_block(hub), "the page selects its own plot frame"
    assert "flowH-2*ltP" not in _flow_block(hub), "the page selects its own plot frame"
    assert "clusters=" not in _flow_block(hub), "the page computes clusters"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_lead_page_draws_the_plot_frame_supplied_by_the_hub(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    chart["plot_frame"] = {"width": 700, "height": 240}

    out = _lt_chart(hub, tmp_path, "lt-supplied-frame.js", chart)

    # Re-anchored (#1332): the layout reserves a right gutter sized to the longest reference tag, outside the plot.
    assert re.search(r'viewBox="0 0 \d+ 368"', out), "the viewBox does not carry the supplied frame's height"
    assert '<line x1=64 y1=304 x2=764 y2=304' in out


def test_the_control_chart_is_served_for_every_timeframe_on_the_log_axis(hub, board):
    hub.board_cache["items"] = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z")]

    control = _flow(hub)["lead"]["control"]

    assert list(control) == TIMEFRAME_KEYS, "the payload takes no scale: one chart per timeframe"
    assert all(chart["axis"] == "log" for chart in control.values()), "a view is served on any axis but log"


def test_a_control_chart_dot_carries_the_issue_title_and_its_close_date(hub, board):
    item, events = _done(7, "2026-10-02T09:00:00Z")
    _seed(hub, {7: ({**item, "t": "ship the chart"}, events)})

    dot = _flow(hub)["lead"]["control"]["all"]["dots"][0]

    assert dot["n"] == 7
    assert dot["title"] == "ship the chart"
    assert dot["closed"] == "2026-10-02"


def test_the_control_chart_names_its_lines_and_offers_no_scale_control(hub):
    # Re-anchored by #1355: the lead chart always draws a log axis, so the page carries no Scale control, no stored
    # log-scale flag and no scale value in the route's query, and its change handler has no scale branch. A hidden or
    # renamed scale switch would have to keep one of these four to work, and each is asserted absent.
    for name in ("Rolling mean", "Rolling spread ±1 SD", "Past month", "Timeframe"):
        assert name in hub.PAGE, f"the page names no {name!r}"
    assert "id=lts" not in hub.PAGE and ">Scale <" not in hub.PAGE and "Log scale" not in hub.PAGE, \
        "the page still offers a Scale control"
    assert "LT.log" not in hub.PAGE, "the page still reads a stored log-scale flag"
    ltq = next(line for line in hub.PAGE.splitlines() if line.startswith("const ltQ="))
    assert "scale=" not in ltq, "the lead chart's query still carries a scale value"
    change = hub.PAGE[hub.PAGE.index('$("#flowb").onchange'):hub.PAGE.index('$("#flowb").onclick')]
    assert "lts" not in change and "scale" not in change, "the lead chart's onchange still has a scale branch"


def test_the_legend_carries_short_names_and_values_never_prose(hub):
    """Re-anchored by #1304: the legend keeps short names and values; explanations move out of the reading path."""
    for prose in ("85% of closed cards finished within this time", "one slow card lifts it",
                  "half the cards that arrived finish within this time", "the usual spread",
                  "the proof's time inside this window"):
        assert prose not in hub.PAGE, f"the legend still carries the prose sentence {prose!r}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_legend_has_no_prose_sentences_and_reference_lines_carry_end_labels(hub, board, tmp_path):
    """#1304: the chart answers "how long do bugs take" without a prose legend; a cluster is one dot, with no count text
    in the plot (#1331).

    Re-anchored by #1332: a reference no longer labels itself inside the plot; its value sits in the reserved right
    gutter, outside the plot's right edge. Re-anchored by #1353: the ±1 SD band draws by default, so the plot carries
    its polygon before the reader asks for it."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in range(1, 13)})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-readable.js", chart)
    for legend in re.findall(r'<p class="flg ltlg">(.*?)</p>', out):
        assert "<em>" not in legend, "the legend still carries a prose sentence"
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    right = 64 + chart["plot_frame"]["width"]
    gutter = [attrs for attrs, _ in _svg_texts(plot) if _has_class(attrs, "lttg")]
    assert len(gutter) == 3, "the three reference tags are drawn by default"
    assert all(float(_svg_attr(attrs, "x")) > right for attrs in gutter), \
        "a reference tag sits inside the plot instead of the reserved right gutter"
    assert not [body for attrs, body in _svg_texts(plot)
                if body.startswith(("Average ", "Median ", "p85 ")) and not _has_class(attrs, "lttg")], \
        "a reference still labels itself inside the plot"
    assert "<polygon" in plot, "the standard-deviation band does not draw by default"
    assert not re.search(r"<text[^>]*>\s*\d+\s*</text>", plot), "the plot still draws a count"
    circle = re.search(r"<circle [^>]*>", plot).group(0)
    assert float(_svg_attr(circle, "r")) == chart["clusters"][0]["r"], "the dot does not take the payload's fixed radius"


def _lt_chart(hub, tmp_path, name, chart, zr=None, coverage=None):
    """The lead chart's markup for one control chart; `zr` is the drag zoom the page holds, or none."""
    zoom = f"LT.zr={json.dumps(zr)}" if zr else "LT.zr=null"
    return _render(hub, tmp_path, name, f"({zoom},ltChart({json.dumps(chart)},{json.dumps(coverage)}))")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_first_screen_carries_the_filters_and_toggles_and_the_rest_sit_behind_more(hub, board, tmp_path):
    """#1145: the Timeframe and Type selects and the Median and p85 toggles are the controls on the first screen.
    Measurement and Display, and the coverage line, the plotted-cards note and the ordering sentence, sit behind one
    More disclosure. #1355: the chart always draws a log axis, so no Scale control renders on either screen.

    Re-anchored by line (#1147): the zoom control is a chip drawn only while a zoom is active, so it is absent from the
    default screen and the View group appears after Display only in a zoomed view.
    """
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    lead = _flow(hub)["lead"]
    out = _lt_chart(hub, tmp_path, "lt-two-controls.js", lead["control"]["all"], coverage=lead["coverage"])
    first, sep, rest = out.partition("<details class=ltmore>")
    assert sep, "the first screen carries no More disclosure"
    more = rest[:rest.index("</details>")]
    # Re-anchored (#1332): the two reference toggles sit beside the filters, where the reading starts; the measurement
    # and the band stay behind More. Re-anchored by #1355: the chart always draws a log axis, so the first screen carries
    # ltr, lty, ltmd and ltp85 and no Scale control.
    for shown in ("id=ltr", "id=lty", "id=ltmd", "id=ltp85"):
        assert shown in first, f"{shown} is not on the first screen"
    assert "id=lts" not in out, "a Scale control renders"
    assert ">Scale <" not in out, "a Scale control renders"
    for later in ("id=lta", "id=ltb", "id=ltbd"):
        assert later not in first, f"the first screen still carries {later}"
        assert later in more, f"More does not hold {later}"
    assert "id=ltz" not in out, "the zoom chip is drawn only while a zoom is active"
    order = ["Measurement", "id=lta", "id=ltb", "Display", "id=ltbd"]
    marks = [more.index(mark) for mark in order]
    assert marks == sorted(marks), f"the disclosure does not keep the shipped order: {order}"
    zoomed = _lt_chart(hub, tmp_path, "lt-two-controls-zoom.js", lead["control"]["all"],
                       zr=["2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00"], coverage=lead["coverage"])
    zfirst, _, zrest = zoomed.partition("<details class=ltmore>")
    assert "id=ltz" not in zfirst, "the zoom chip is never on the first screen"
    zmore = zrest[:zrest.index("</details>")]
    order = ["Display", "id=ltbd", "View", "id=ltz"]
    marks = [zmore.index(mark) for mark in order]
    assert marks == sorted(marks), f"the zoomed disclosure does not keep the shipped order: {order}"
    assert "Set the controls in order: filters, then measurement, then display, then view." in more
    assert "cards read" in more, "the coverage line does not sit behind More"
    assert "issue plotted" in more and "issue plotted" not in first, "the plotted-cards note does not sit behind More"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_head_line_states_the_window_and_the_excluded_total_once(hub, board, tmp_path):
    """#1145: the head line states the window once, and the exclusion reasons move into a hover on its one total.

    Re-anchored (#1248): the one total moved from the head line to sit beside the percentile, and its hover names the
    cards; an open card is censored, never excluded (#1250), so a card closed as not planned carries the exclusion."""
    item, events = _done(2, "2026-10-03T09:00:00Z")
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: ({**item, "cr": "not_planned"}, events), 3: _todo(3)})
    lead = hub.flow_lead(ty="task")["lead"]
    out = _render(hub, tmp_path, "lt-head-excluded.js", f"flowLead({json.dumps(lead)})")
    head = re.search(r"<p class=fhead>(.*?)</p>", out).group(1)
    assert "excluded" not in head, "the head line still states the exclusion"
    assert out.count("excluded") == 1, "the page states the exclusion more than once"
    # Re-anchored (#1334): the one exclusion total sits in the header's summary row, not beside p85.
    # Re-anchored (#1354): the total states its reason beside its count, so no bare count is served.
    summary_row = re.findall(r"<p class=fhead>(.*?)</p>", out)[1]
    assert re.search(r'title="1 closed as not planned: #2"[^>]*>excluded 1: 1 closed as not planned<', summary_row), \
        "the one total and its named cards sit in the header's summary row"
    assert "window 20 issues" in head
    assert out.count("20 issues") == 1, "the rolling window is stated twice"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_zoom_chip_names_the_range_and_is_drawn_only_while_zoomed(hub, board, tmp_path):
    """#1147: one chip, absent on the default screen; while zoomed it names the view it returns to and clears the zoom.

    Re-anchored by line: the old guard asserted a disabled button on the default screen; the chip now replaces it, and
    the "Back to ..." wording lives in the chip.
    """
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    home = _lt_chart(hub, tmp_path, "lt-zoom-home.js", chart)
    zoomed = _lt_chart(hub, tmp_path, "lt-zoom-held.js", chart, zr=["2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00"])
    assert "id=ltz" not in home, "the zoom control is absent on the default screen"
    assert "disabled" not in home, "no disabled button sits on the default screen"
    assert re.search(r"<span class=fchip id=ltz>Back to All <button id=ltzc[^>]*>Clear</button></span>", zoomed), \
        "the chip names the view it returns to and carries its clear action"
    assert len(re.findall(r"id=ltz\b", zoomed)) == 1, "the zoom is one chip, never two controls"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_clustered_view_offers_a_range_control_that_prints_its_range(hub, board, tmp_path):
    # Re-anchored (#1334): the Fit button became the Fit data / Full window range control, printing the range it shows.
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-fit.js", chart)
    assert re.search(r"<span class=fchip id=ltrng><button id=ltfit[^>]*>Fit data</button>"
                     r"<button id=ltfull[^>]*>Full window</button> " + re.escape(chart["range"]["text"]) + "</span>", out), \
        "the range control does not name both ranges and print the one it shows"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_fit_button_appears_only_when_there_is_a_span_to_fit(hub, board, tmp_path):
    """#1147: Fit sits beside the zoom control only when the cards have a span to fit."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-29T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    assert chart["fit"] is None, "the spread cards fill the whole view: there is no span to fit"
    out = _lt_chart(hub, tmp_path, "lt-no-fit.js", chart)
    assert "id=ltfit" not in out, "Fit is drawn with no span to fit"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_one_exclusion_total_is_shown_and_the_plotted_sample_is_stated(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _todo(2), 3: _todo(3)})
    lead = hub.flow_lead(ty="task")["lead"]
    chart = lead["control"]["all"]
    assert chart["excluded_total"] == sum(chart["excluded"].values()) == 0, "the total is the rows the chart counted"  # re-anchored (#1250): an open card is censored, never an exclusion
    out = _render(hub, tmp_path, "lt-exclusion.js", f"flowLead({json.dumps(lead)})")
    assert out.count("excluded") == 1, "the page shows the exclusion more than once"
    assert "1 issue plotted" in out


def test_the_caption_names_the_start_column_the_bar_chose(hub, board):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    span = "from=2026-09-01T00:00:00Z&to=2026-12-01T00:00:00Z"
    from urllib.parse import quote
    default = hub.lead_zoom(span)["text"]
    chosen = hub.lead_zoom(f"{span}&a={quote(hub.ORDER[0])}")["text"]
    assert chosen != default
    assert f"{hub.ORDER[0]} -> closed" in chosen


def test_the_caption_names_the_end_column_the_bar_chose(hub, board):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    span = "from=2026-09-01T00:00:00Z&to=2026-12-01T00:00:00Z"
    from urllib.parse import quote
    default = hub.lead_zoom(span)["text"]
    end = hub.ORDER[1]
    chosen = hub.lead_zoom(f"{span}&b={quote(end)}")["text"]
    assert chosen != default
    assert f"opened -> {end}" in chosen


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_chosen_span_shows_its_own_exclusions_in_the_one_total(hub, board, tmp_path):
    _seed(hub, {
        1: (_card(1, st="Closed", s="CLOSED", c="2026-10-05T00:00:00Z"),
            _life("2026-10-01T00:00:00Z", ("2026-10-04T00:00:00Z", "Doing"), ("2026-10-05T00:00:00Z", "Closed"))),
        2: _done(2, "2026-10-03T09:00:00Z"),
    })
    lead = hub.flow_lead(ty="task")["lead"]
    span = hub.lead_zoom("tf=all&a=Doing")
    assert span["excluded_total"] == 1
    out = _render(hub, tmp_path, "lt-span-exclusion.js", f"(LT.zc={json.dumps(span)},flowLead({json.dumps(lead)}))")
    assert out.count("excluded") == 1, "the page shows the exclusion more than once"
    assert "excluded 1" in out, "the one total is the base lead, not the chosen span"
    # Re-anchored (#1248): card 2 never entered Doing, so it is named as a missing start, with its number.
    assert 'title="1 never entered the start column: #2"' in out, "the reasons do not sit in the total's hover"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_legend_splits_series_from_summary_and_the_band_shows_until_hidden(hub, board, tmp_path):
    """#1304: the series legend names only the drawn marks; statistics sit in one summary row.

    Re-anchored by #1332: the series legend names every mark the plot draws — the card, the cluster and the rolling
    mean — and the band carries its own name, "Rolling spread ±1 SD"; the head line is the one place that states the
    window, so the legend never restates it from a second field. Re-anchored by #1353: the band draws by default, so
    its entry is in the first drawing; the display may still ask for it, and the entry follows the drawn mark.
    """
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-03T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-legend.js", chart)
    series, summary = re.findall(r'<p class="flg ltlg">(.*?)</p>', out)
    entries = re.findall(r"<span>(.*?)</span>", series)
    assert [re.sub(r"<[^>]+>", "", e) for e in entries] == ["Card", "Cluster", "Rolling mean", "Rolling spread ±1 SD"], entries
    assert str(chart["window"]["size"]) not in re.sub(r"<[^>]+>", "", series), \
        "the series legend restates the window the head line already names"
    assert all(entry.count("<i") == 1 for entry in entries), "an entry has no marker or two"
    for label in ("Average", "Median", "p85"):
        assert label in re.sub(r"<[^>]+>", "", summary), f"the summary row names no {label}"
    assert "Gate time" not in series + summary, "gate time stays a text metric, not a legend entry"
    on = _render(hub, tmp_path, "lt-legend-band.js", f"(LT.band=true,ltChart({json.dumps(chart)},null))")
    assert "<polygon" in on, "the band does not draw when the display asks for it"
    series_on = re.findall(r'<p class="flg ltlg">(.*?)</p>', on)[0]
    band = next((e for e in re.findall(r"<span>(.*?)</span>", series_on) if "Rolling spread ±1 SD" in e), None)
    assert band and band.count("<i") == 1, "the band's label is split from its marker"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_band_shows_by_default_and_holds_the_readers_choice_across_redraws(hub, board, tmp_path):
    """#1353, SPEC 1327: the ±1 SD band is on when the page loads, carries its legend entry "Rolling spread ±1 SD", and
    the Band box starts checked; a reader who unchecks it keeps the band off on the next redraw."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-03T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-band-default.js", chart)
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    assert "<polygon" in plot, "the standard-deviation band does not draw by default"
    series = re.findall(r'<p class="flg ltlg">(.*?)</p>', out)[0]
    assert "Rolling spread ±1 SD" in re.sub(r"<[^>]+>", "", series), \
        "the series legend does not name the band it draws"
    assert "id=ltbd checked" in out, "the Band checkbox does not start checked"
    redraw = _render(hub, tmp_path, "lt-band-redraw.js",
                     f"(LT.band=false,ltChart({json.dumps(chart)},null)+ltChart({json.dumps(chart)},null))")
    plots = re.findall(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", redraw, re.S)
    assert len(plots) == 2, "the harness drew no second chart"
    assert all("<polygon" not in p for p in plots), "the band returns after the reader unchecked it"
    assert "id=ltbd checked" not in redraw, "the Band checkbox forgets the reader's choice on a redraw"


def test_a_legend_entry_stays_on_one_line(hub):
    assert re.search(r"\.ltlg span\{[^}]*white-space:nowrap", hub.PAGE), "a legend entry may wrap across lines"


# SPEC 1116 #1146: no statistic is drawn inside the plot, and each mark takes its own role.
STYLESHEET = SCRIPTS / "web-tokens.css"


def _tokens():
    root = re.search(r":root\{(.*?)\}", STYLESHEET.read_text(), re.S).group(1)
    return dict(re.findall(r"--([\w-]+):([^;}]+)", root))


def _resolved(value, tokens):
    """Follow var() chains to the literal colour a role paints; a cycle is a failure."""
    seen = set()
    while True:
        m = re.fullmatch(r"var\(--([\w-]+)\)", value.strip())
        if not m:
            return value.strip()
        assert m.group(1) not in seen, f"a token cycle at --{m.group(1)}"
        seen.add(m.group(1))
        value = tokens[m.group(1)]


def _paint(tag, name):
    m = re.search(rf'(?:^|\s){re.escape(name)}=(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag)
    return next((v for v in m.groups() if v is not None), None) if m else None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_each_reference_line_labels_itself_at_its_end_and_the_summary_row_carries_each_value(hub, board, tmp_path):
    """Re-anchored by #1304: a reference carries its value, and the summary row repeats each value once; the value never
    sits over the dots. Re-anchored by #1332: the value sits in the reserved right gutter, outside the plot."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in range(1, 4)})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    out = _render(hub, tmp_path, "lt-no-stat-in-plot.js", f"(LT.md=true,LT.p85=true,ltChart({json.dumps(chart)},null))")
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    right = 64 + chart["plot_frame"]["width"]
    tags = [(attrs, body) for attrs, body in _svg_texts(plot) if _has_class(attrs, "lttg")]
    assert len(tags) == 3, "the gutter does not carry one tag per drawn reference"
    assert sorted(body for _, body in tags) == sorted(f'{chart[k]["label"]} {chart[k]["text"]}'
                                                      for k in ("average", "median", "p85")), \
        "a gutter tag neither names its reference nor carries its value, so grayscale cannot tell them apart"
    assert all(float(_svg_attr(attrs, "x")) > right for attrs, _ in tags), \
        "a reference tag sits inside the plot instead of the reserved right gutter"
    refs = {re.search(r"<title>(\S+) ", tag).group(1): tag
            for tag in re.findall(r"<line [^>]*><title>(?:Average|Median|p85) [^<]*</title></line>", plot)}
    assert len(refs) == 3, "a reference line's title does not name it, so the swatch check would assert nothing"
    gutter = [ln for ln in re.findall(r"<line [^>]*>", plot) if float(_paint(ln, "x1")) > right]
    for name, tag in refs.items():
        assert _paint(tag, "stroke-dasharray") in [_paint(ln, "stroke-dasharray") for ln in gutter], \
            f"the gutter draws no swatch in {name}'s own pattern"
    summary = re.findall(r'<p class="flg ltlg">(.*?)</p>', out)[1]
    for key in ("average", "median", "p85"):
        assert f"<b>{chart[key]['text']}</b>" in summary, f"the summary row does not carry {key}'s value"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_each_mark_takes_its_own_role_and_the_legend_states_it(hub, board, tmp_path):
    """Re-anchored by #1304: the role check draws the band by asking for it. Re-anchored by #1353: it also draws by
    default, and the state names it so this check reads the same markup either way."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in (1, 2)})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    out = _render(hub, tmp_path, "lt-roles.js", f"(LT.band=true,ltChart({json.dumps(chart)},null))")
    tokens = _tokens()
    trail = re.search(r"<polyline [^>]*>", out).group(0)
    band = re.search(r"<polygon [^>]*>", out).group(0)
    average = re.search(r"<line [^>]*><title>Average [^<]*</title>", out).group(0)
    ring = re.search(r"<circle [^>]*>", out).group(0)
    ink = _resolved("var(--chart-ink)", tokens)
    trail_col = _resolved(_paint(trail, "stroke"), tokens)
    band_col = _resolved(_paint(band, "fill"), tokens)
    average_col = _resolved(_paint(average, "stroke"), tokens)
    assert _resolved(_paint(ring, "stroke"), tokens) == ink, "a dot or ring is not the neutral chart ink"
    assert trail_col == _resolved("var(--chart-bar)", tokens), "the trend line does not take the chart accent"
    assert band_col == _resolved("var(--chart-series-3)", tokens), "the band does not keep its own chart fill"
    assert average_col == _resolved("var(--chart-series-8)", tokens), "the average line does not take the chart grey"
    assert float(_paint(band, "fill-opacity")) > 0.16, "the band is no stronger than before"
    assert len({ink, trail_col, band_col, average_col}) == 4, "two marks share one role"
    series, summary = re.findall(r'<p class="flg ltlg">(.*?)</p>', out)
    # Re-anchored (#1332): the legend's swatches are styled by class, so each class's paint is read from its rule.
    assert re.findall(r"<i class=(\w+)>", series) == ["lthollow", "ltfilled", "ltroll", "ltband"], \
        "the series legend does not name every drawn mark with its own swatch"
    css = hub.PAGE
    def painted(cls):
        rule = re.search(rf"\.ltlg i\.{cls}\{{([^}}]*)\}}", css)
        assert rule, f"the {cls} swatch has no rule"
        return {_resolved(v, tokens) for v in re.findall(r"var\(--[\w-]+\)", rule.group(1))}
    assert painted("ltroll") == {_resolved("var(--chart-bar)", tokens)}, "the trend swatch differs from its line"
    assert painted("ltband") == {_resolved("var(--chart-series-3)", tokens)}, "the band swatch differs from its fill"
    assert re.findall(r"<i class=(\w+)>", summary) == ["ltsolid", "ltdash", "ltdot"], \
        "the summary row carries no swatch for each reference pattern"
    assert painted("ltsolid") == {_resolved("var(--chart-series-8)", tokens)}
    assert painted("ltdash") == {_resolved("var(--chart-ok)", tokens)}
    assert painted("ltdot") == {_resolved("var(--chart-series-5)", tokens)}, "the summary row paint differs from its line"
    assert painted("lthollow") != painted("ltfilled"), \
        "the hollow and filled swatches resolve to one paint, so grayscale cannot tell a card from a cluster"


def _lead_chart_css(page):
    """Every style rule that paints a lead-chart element: a #flowb subtree, an .lt* class, or the chart svg."""
    css = "".join(re.findall(r"<style>(.*?)</style>", page, re.S))
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    return "".join(body for sel, body in rules if re.search(r"#flowb|\.lt\w|svg\[data-lt\]", sel))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_chart_token_the_lead_chart_paints_is_declared(hub, board, tmp_path):
    """#1332: no colour literal is in the chart code, and every role the lead chart names is a hub web token."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in range(1, 4)})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    out = _render(hub, tmp_path, "lt-tokens.js",
                  f"(LT.band=true,LT.md=true,LT.p85=true,ltChart({json.dumps(chart)},null))")
    css = _lead_chart_css(hub.PAGE)
    for declaration in ("touch-action:pan-y", "repeating-linear-gradient", "cursor:pointer"):
        assert declaration in css, f"the lead-chart rule body {declaration!r} was not collected"
    tokens = _tokens()
    for value in set(re.findall(r"var\(--chart-[\w-]+\)", out + css)):
        name = re.fullmatch(r"var\(--([\w-]+)\)", value).group(1)
        assert name in tokens, f"the lead chart paints var(--{name}), which no hub token declares"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", out + css), "the lead chart carries a colour literal"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_three_reference_lines_show_by_default_and_their_patterns_differ(hub, board, tmp_path):
    """#1332: the average, the median and p85 all draw at first; each is a toggle. All three are 1px and
    carry distinct patterns (solid, dashed, dotted), and the rolling mean is the stronger 3px line."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-03T09:00:00Z"),
                3: _done(3, "2026-10-05T09:00:00Z")})
    chart = hub.flow_lead()["lead"]["control"]["all"]

    def refs(markup):
        return {re.search(r"<title>(\w+) ", tag).group(1): tag
                for tag in re.findall(r"<line [^>]*><title>(?:Average|Median|p85) [^<]*</title></line>", markup)}

    out = _lt_chart(hub, tmp_path, "lt-reference-default.js", chart)
    default = refs(out)
    assert list(default) == ["Average", "Median", "p85"], "the average, the median and p85 draw at first"
    first = out.partition("<details class=ltmore>")[0]
    assert "id=ltmd" in first and "id=ltp85" in first, "the median and p85 toggles are not on the first screen"
    for toggle in ("ltmd", "ltp85"):
        assert re.search(rf"<input[^>]*id={toggle}[^>]*checked", first), f"the {toggle} toggle opens checked"

    on = _render(hub, tmp_path, "lt-reference-on.js", f"(LT.md=true,LT.p85=true,ltChart({json.dumps(chart)},null))")
    shown = refs(on)
    assert set(shown) == {"Average", "Median", "p85"}, "a toggle does not draw its reference line"
    painted = {}
    for name, tag in shown.items():
        width = _paint(tag, "stroke-width")
        assert width is not None, f"the {name} reference line drops its stroke-width"
        painted[name] = (width, _paint(tag, "stroke-dasharray"))
    assert painted["Average"] == ("1", None), f"the average reference is not one solid 1px line: {painted['Average']}"
    assert painted["Median"] == ("1", "6 3"), f"the median reference is not one long-dash 1px line: {painted['Median']}"
    assert painted["p85"] == ("1", "1 3"), f"the p85 reference is not one dotted 1px line: {painted['p85']}"
    assert len(set(painted.values())) == 3, f"two references paint alike, so grayscale cannot tell them apart: {painted}"
    width = lambda tag: re.search(r"stroke-width=([^\s>]+)", tag).group(1)   # noqa: E731
    roll = re.search(r"<polyline [^>]*>", on).group(0)
    assert width(roll) == "3", "the rolling mean is not the stronger 3px line"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_titles_both_axes_outside_the_plot(hub, board, tmp_path):
    """#1332: "Lead time" sits above the y axis with its scale named, and "Closed date" sits below the x axis."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-axis-titles.js", chart)
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    texts = _svg_texts(plot)
    y_title = next(attrs for attrs, body in texts if body == "Lead time (log)")
    x_title = next(attrs for attrs, body in texts if body == "Closed date")
    frame_top = float(re.search(r"<line x1=\S+ y1=(\S+)", plot).group(1))
    base = float(re.search(r'<line x1=\S+ y1=(\S+) x2=\S+ y2=\1 stroke=var\(--chart-grid\)>', plot).group(1))
    assert float(_svg_attr(y_title, "y")) < frame_top, "the y axis title sits inside the plot"
    assert float(_svg_attr(x_title, "y")) > base, "the x axis title does not sit below the axis"
    assert _svg_attr(y_title, "x") == _svg_attr(x_title, "x") == re.search(r"<line x1=(\S+)", plot).group(1), \
        "an axis title does not start at the plot's left edge"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_drag_maps_the_pointer_through_the_plot_and_never_attributes_the_gutter(hub, board, tmp_path):
    """#1332: the reserved right gutter sits outside the plot, so the drag reads the pointer in SVG user units and clamps
    it to the plot's own edges; a drag to the plot's right edge lands on that edge, and the zoom stays wired."""
    page = hub.PAGE
    # The layout values are the page's own: the left margin and the plot frame the chart payload supplies, and the
    # viewBox the rendered chart draws, so a layout change moves this test with it instead of leaving it pinned.
    lt_p = int(re.search(r"const ltP=(\d+);", page).group(1))
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    vb_width = int(re.search(r'viewBox="0 0 (\d+) ', _lt_chart(hub, tmp_path, "lt-pointer-frame.js", chart)).group(1))
    left, right = lt_p, lt_p + chart["plot_frame"]["width"]
    assert vb_width > right, "the viewBox reserves no gutter past the plot for the drag to ignore"
    block = f"const ltP={lt_p},LT_FRAME={{width:{chart['plot_frame']['width']}}};let LT={{}};" + \
        page[page.index("const ltPx"):page.index('$("#flowb").onpointerdown')]
    svg = (f"{{getBoundingClientRect:()=>({{left:0,width:{2 * vb_width}}}),"
           f"viewBox:{{baseVal:{{width:{vb_width}}}}}}}")
    unlaid = "{getBoundingClientRect:()=>({left:0,width:0}),viewBox:{baseVal:{width:0}}}"
    out = _render(hub, tmp_path, "lt-pointer.js",
                  f"JSON.stringify([[0,{vb_width // 2},{vb_width - 128},{2 * right},{2 * vb_width}]"
                  f".map(px=>ltClamp(ltPx({{clientX:px}},{svg}))),[ltPx({{clientX:100}},{unlaid})]])",
                  block=block)
    mapped, unlaid_x = json.loads(out)
    assert mapped == pytest.approx([left, vb_width / 4, (vb_width - 128) / 2, right, right]), \
        "a drag does not land on the user-space x it points at, or the reserved gutter bends it"
    assert unlaid_x == [None], "an svg with no laid-out width still names an x, so the drag collapses to the left edge"
    drag = page[page.index('$("#flowb").onpointerdown'):page.index('$("#flowb").onchange')]
    assert "ltPx(e,s)" in drag and "ltZoom(" in drag, "the drag-to-zoom is no longer wired"
    assert drag.count("if(p==null)return") >= 2, "a pointer handler maps a null x, so an unlaid-out svg still drags"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_right_gutter_tags_never_overlap_when_the_values_are_close(hub, board, tmp_path):
    """#1332: the reference tags sit in the reserved right gutter, outside the plot, and their baselines are moved apart
    by one body line so no tag covers another — on one value, on values a few pixels apart, and on a block that runs past
    the floor. A frame too short for the block spreads the tags evenly across it instead of overlapping them."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    assert len({chart[k]["text"] for k in ("average", "median", "p85")}) == 1, "the fixture does not put the values on one"
    lt_p = int(re.search(r"const ltP=(\d+);", hub.PAGE).group(1))
    gap = int(re.search(r"const ltGap=(\d+);", hub.PAGE).group(1))
    frame, bottom = chart["plot_frame"], lt_p + chart["plot_frame"]["height"]

    def tags_for(chart_in, name, frame_in=None):
        frame_in = frame_in or chart_in["plot_frame"]
        out = _render(hub, tmp_path, name, f"(LT.md=true,LT.p85=true,ltChart({json.dumps(chart_in)},null))")
        plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
        tags = re.findall(r"<text class=lttg [^>]*x=(\S+) y=(\S+) ", plot)
        assert len(tags) == 3, "the gutter does not carry one tag per drawn reference"
        assert all(float(x) > lt_p + frame_in["width"] for x, _ in tags), "a tag sits inside the plot"
        return sorted(float(y) for _, y in tags)   # the baseline each tag paints its text on

    def apart(chart_in, shift, name):
        c = json.loads(json.dumps(chart_in))
        for key, step in (("median", shift), ("p85", 2 * shift)):
            c[key]["y"] = c["average"]["y"] + step / frame["height"]
        heights = tags_for(c, name)
        assert all(b - a >= gap for a, b in zip(heights, heights[1:], strict=False)), \
            f"two close tags overlap at {shift}px: {heights}"
        return heights

    apart(chart, 0, "lt-gutter-coincident.js")
    apart(chart, 4, "lt-gutter-4px.js")     # closer than one body line
    apart(chart, 7, "lt-gutter-7px.js")     # still closer than one body line
    low = json.loads(json.dumps(chart))
    for key, step in (("average", 0), ("median", 2), ("p85", 4)):
        low[key]["y"] = step / frame["height"]
    heights = tags_for(low, "lt-gutter-floor.js")
    assert all(b - a >= gap for a, b in zip(heights, heights[1:], strict=False)), \
        f"the block that runs past the floor overlaps: {heights}"
    assert heights[0] >= lt_p, f"the block that slides up leaves the plot's top: {heights}"
    assert heights[-1] == pytest.approx(bottom), f"the block sits above the plot's floor instead of at it: {heights}"

    for h, step in ((30, 30 / 2), (48, gap)):   # 30 is shorter than the block's two gaps; 48 just holds it
        near = json.loads(json.dumps(chart))
        near["plot_frame"] = {"width": frame["width"], "height": h}
        for key, shift in (("average", 0), ("median", 2), ("p85", 4)):
            near[key]["y"] = shift / h
        heights = tags_for(near, f"lt-gutter-frame-{h}.js", near["plot_frame"])
        assert heights[0] >= lt_p and heights[-1] <= lt_p + h + 1e-9, \
            f"a tag leaves a {h}-unit frame: {heights}"
        assert all(b - a == pytest.approx(step) for a, b in zip(heights, heights[1:], strict=False)), \
            f"a {h}-unit frame does not space its tags at {step}: {heights}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_gutter_is_sized_from_the_longest_reference_tag(hub, board, tmp_path):
    """#1332: the reserved gutter and the viewBox are sized from the longest tag, so a long value such as
    "Median 1w 2d 3h" stays inside the drawing instead of running past the viewBox's right edge."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = json.loads(json.dumps(hub.flow_lead()["lead"]["control"]["all"]))
    for key in ("average", "median", "p85"):
        chart[key]["text"] = "1w 2d 3h"
    out = _render(hub, tmp_path, "lt-gutter-width.js", f"(LT.md=true,LT.p85=true,ltChart({json.dumps(chart)},null))")
    vb = int(re.search(r'viewBox="0 0 (\d+) ', out).group(1))
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    tags = [(attrs, body) for attrs, body in _svg_texts(plot) if _has_class(attrs, "lttg")]
    assert len(tags) == 3, "the gutter does not carry one tag per drawn reference"
    right = int(re.search(r"const ltP=(\d+);", hub.PAGE).group(1)) + chart["plot_frame"]["width"]
    for attrs, body in tags:
        end = float(_svg_attr(attrs, "x")) + 8.5 * len(body)   # a loose estimate for one body-size glyph
        assert end <= vb, f"the tag {body!r} runs past the viewBox at {end} of {vb}"
        assert float(_svg_attr(attrs, "x")) > right, "a tag sits inside the plot"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_lone_card_draws_a_hollow_dot_and_a_cluster_draws_one_filled_dot(hub, board, tmp_path):
    """#1331: a lone card is a hollow dot (ink ring, background fill); a cluster is one outlined filled dot of the one
    fixed radius the payload serves; the plot draws no stacked bar and no count."""
    lt = _load("board_leadtime_dots", SCRIPTS / "board_leadtime.py")
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-09T09:00:00Z"),
                3: _done(3, "2026-10-09T09:00:00Z")})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    assert sorted(c["count"] for c in chart["clusters"]) == [1, 2]
    out = _lt_chart(hub, tmp_path, "lt-dots.js", chart)
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    circles = re.findall(r"<circle [^>]*>", plot)
    assert len(circles) == 2, "one mark per cluster, never one per card"
    lone, cluster = sorted(circles, key=lambda tag: float(_svg_attr(tag, "r")))
    assert float(_svg_attr(lone, "r")) == lt.DOT_RADIUS_PX
    assert float(_svg_attr(cluster, "r")) == lt.CLUSTER_RADIUS_PX
    assert _paint(lone, "fill") == "var(--chart-bg)", "a lone card draws no hollow dot"
    assert _paint(cluster, "fill") == "var(--chart-ink)", "a cluster draws no filled dot"
    assert _paint(cluster, "stroke") == "var(--chart-ink)", "a cluster's dot carries no outline"
    assert "<rect" not in plot, "the plot still draws a stacked bar"
    assert not re.search(r"<text[^>]*>\s*\d+\s*</text>", plot), "the plot still draws a count"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_drawn_marks_are_small_and_agree_with_the_payload(hub, board, tmp_path):
    """#1432: the page draws a lone card at 3 px, a cluster at 5 px and the outline at 1.5 px, the sizes the payload
    serves. The figures are literals, so a later change that grows a mark fails here."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-09T09:00:00Z"),
                3: _done(3, "2026-10-09T09:00:00Z")})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    assert chart["mark"] == {"dot_radius": 3, "cluster_radius": 5, "outline": 1.5}
    out = _lt_chart(hub, tmp_path, "lt-small-marks.js", chart)
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    lone, cluster = sorted(re.findall(r"<circle [^>]*>", plot), key=lambda tag: float(_svg_attr(tag, "r")))
    assert float(_svg_attr(lone, "r")) == 3, "a lone card does not draw 3 px"
    assert float(_svg_attr(cluster, "r")) == 5, "a cluster does not draw 5 px"
    assert float(_svg_attr(lone, "stroke-width")) == 1.5, "a lone card's outline is not 1.5 px"
    assert float(_svg_attr(cluster, "stroke-width")) == 1.5, "a cluster's outline is not 1.5 px"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_applies_the_payloads_outline_rather_than_a_literal(hub, board, tmp_path):
    """#1432: the outline is the payload's own `mark.outline`, never a number the view keeps. `_lt_chart` serializes the
    chart it is handed into the JS it runs, so the mutated 2.75 reaches `ltChart`; a hard-coded 1.5 fails here."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    chart["mark"]["outline"] = 2.75

    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", _lt_chart(hub, tmp_path, "lt-outline.js", chart), re.S).group(1)
    widths = [float(_svg_attr(tag, "stroke-width")) for tag in re.findall(r"<circle [^>]*>", plot)]
    assert widths and all(width == 2.75 for width in widths), f"the page ignores the payload's outline: {widths}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_chart_without_mark_keeps_the_outline_it_had(hub, board, tmp_path):
    """#1432: `mark` is new on the wire, so an older or cached chart carries none. The page then keeps its 1.5 px ring
    instead of drawing `stroke-width=undefined`, which the browser drops. The two payloads are drawn side by side: the
    widths must differ, so a view that hard-codes 1.5 for every chart fails on the one that names 2.75."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    named = hub.flow_lead()["lead"]["control"]["all"]
    named["mark"]["outline"] = 2.75
    bare = {k: v for k, v in named.items() if k != "mark"}

    def widths(expression, name):
        plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", _lt_chart(hub, tmp_path, name, expression), re.S).group(1)
        return [_svg_attr(tag, "stroke-width") for tag in re.findall(r"<circle [^>]*>", plot)]

    kept, named_widths = widths(bare, "lt-no-mark.js"), widths(named, "lt-named-mark.js")
    assert kept and all(width == "1.5" for width in kept), f"a chart without `mark` loses the ring: {kept}"
    assert named_widths and all(width == "2.75" for width in named_widths), f"a chart with `mark` loses its outline: {named_widths}"
    assert kept != named_widths, "the fallback and the payload's own outline are one code path"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_dot_tooltip_names_the_mark_its_lead_its_dates_and_the_stage_strip(hub, board, tmp_path):
    """#1331: the tooltip names the card or the cluster and shows the lead time, the closed dates and each dot's column
    stack; the cluster carries its count, cards, date range and lead-time range in the payload."""
    gated = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Todo"},
             {"t": "2026-10-01T15:00:00Z", "type": "status", "status": "Gating"},
             {"t": "2026-10-02T09:00:00Z", "type": "status", "status": "Closed"},
             {"t": "2026-10-02T09:00:00Z", "type": "closed"}]
    item = _card(1, st="Closed", s="CLOSED", c="2026-10-02T09:00:00Z")
    _seed(hub, {1: (item, gated), 2: _done(2, "2026-10-02T09:00:00Z")})
    chart = hub.flow_lead(ty=None)["lead"]["control"]["all"]
    cluster = chart["clusters"][0]
    assert cluster["count"] == 2 and cluster["cards"] == [1, 2]
    assert cluster["closed"]["text"] == "Oct 2"

    out = _lt_chart(hub, tmp_path, "lt-tooltip.js", chart)
    title = re.search(r"<circle[^>]*><title>([^<]*)</title>", out).group(1)
    assert title.startswith("2 issues: #1, #2 · lead "), title
    assert "closed Oct 2" in title
    assert "Gating 18h" in title and "Todo 6h" in title, "the stage strip names each dot's column hours"


def test_the_zoom_recomputes_the_clusters_from_the_same_rule(hub, board):
    """#1331: a drag zoom re-requests the chart, and the clusters recompute from the caller's plot size and the one
    screen-space rule; two closes the wide view merges split inside a zoom that spreads them."""
    def same_day(n, opened, closed):
        return (_card(n, st="Closed", s="CLOSED", c=closed, o=opened),
                _life(opened, (closed, "Closed")))
    _seed(hub, {1: same_day(1, "2026-10-02T08:00:00Z", "2026-10-02T09:00:00Z"),
                2: same_day(2, "2026-10-02T20:00:00Z", "2026-10-02T21:00:00Z")})

    wide = hub.lead_zoom("from=2026-09-01T00:00:00Z&to=2026-11-01T00:00:00Z")
    assert [c["count"] for c in wide["clusters"]] == [2], "the wide view merges the two closes"
    zoom = hub.lead_zoom("from=2026-10-02T00:00:00Z&to=2026-10-04T00:00:00Z")
    assert sorted(c["count"] for c in zoom["clusters"]) == [1, 1], "the same rule at the zoom's scale splits them"
    assert zoom["plot_frame"] == wide["plot_frame"] == {"width": 672, "height": 212}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cluster_table_takes_the_page_table_rule(hub, board, tmp_path):
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in (1, 2)})
    chart = hub.flow_lead()["lead"]["control"]["all"]
    out = _render(hub, tmp_path, "lt-cluster-table.js", f"(LT.pick=0,ltChart({json.dumps(chart)},null))")
    table = re.search(r"<table class=([^>]+)>", out)
    assert table, "the cluster table is not drawn"
    classes = table.group(1).split()
    models = re.search(r"<table class=(\w+)><thead><tr><th>Model</th>", hub.PAGE)
    assert models, "the page has no Models table"
    shared = models.group(1)
    assert shared in classes, f"the cluster table does not take the Models table's rule: {classes}"
    rule = re.search(rf"\.{re.escape(shared)}\{{([^}}]*)\}}", hub.PAGE)
    assert rule and "border-collapse:collapse" in rule.group(1), "the shared table rule does not style a table"


def test_every_word_in_the_chart_box_takes_the_page_body_size(hub):
    """#1144: the chart box is one body-size block; nothing in it is set at the page minimum."""
    box = re.search(r"#flowb\{[^}]*\}", hub.PAGE)
    assert box, "the chart box has no rule"
    assert "font-size:var(--fs-body)" in box.group(0), "the chart box does not take the page's body size"
    assert "var(--fs-caption)" not in _flow_block(hub), "a chart label is still set at the page minimum"
    stat = re.search(r"#flowb \.flstat\{[^}]*\}", hub.PAGE)
    assert stat and "font-size" not in stat.group(0), "the chart status line sets its own size"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_svg_text_sets_no_size_of_its_own_and_inherits_the_box(hub, board, tmp_path):
    """#1144: the SVG's ticks and axis title take the box size, never a size of their own."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    out = _lt_chart(hub, tmp_path, "lt-body-size.js", _flow(hub)["lead"]["control"]["all"])
    texts = re.findall(r"<text [^>]*>", out)
    assert texts, "the chart draws no text"
    assert all("font-size" not in text for text in texts), "an SVG text sets its own size instead of inheriting the box's"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_plot_height_follows_the_window_between_a_floor_and_a_ceiling(hub, tmp_path):
    """#1144: the plot height is layout, a function of the window height with a floor and a ceiling."""
    def plot_height(px):
        return int(_render(hub, tmp_path, f"plot-height-{px}.js", f"String(flowHeight({px}))"))

    floor, middle, ceiling = plot_height(200), plot_height(900), plot_height(4000)

    assert plot_height(300) == floor, "a window below the floor does not hold the floor"
    assert plot_height(4000) == plot_height(9000) == ceiling, "a tall window leaves the ceiling"
    assert floor < middle < ceiling, "a taller window does not draw a taller plot"


def test_the_plot_height_is_computed_beside_the_width_from_the_window(hub):
    """#1144: the height is layout, computed where the width is computed today, never a series."""
    draw = next(line for line in hub.PAGE.splitlines() if line.startswith("const drawFlow="))
    assert "flowW=" in draw, "the width is no longer computed in drawFlow"
    assert "flowH=flowHeight(window.innerHeight)" in draw, "the plot height is not computed beside the width"


def test_the_refresh_time_is_a_quiet_status_line(hub):
    block = _flow_block(hub)
    assert "<p class=flstat role=status>data read" in block, "the refresh time is not a status line"
    assert "<p class=fnote>data read" not in block, "the refresh time is still page chatter"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_control_chart_draws_one_mark_per_cluster_on_the_date_axis(hub, board, tmp_path, monkeypatch):
    class Frozen(datetime):   # the page draws the 30-day timeframe: pin "now" so the cards stay inside it
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 10, tzinfo=tz)
    monkeypatch.setattr(hub, "dt", types.SimpleNamespace(**{**vars(hub.dt), "datetime": Frozen}))
    _seed(hub, {
        1: _done(1, "2026-10-02T09:00:00Z"),
        2: _done(2, "2026-10-02T09:00:00Z"),
        3: _done(3, "2026-10-09T09:00:00Z"),
    })
    lead = hub.flow_lead()["lead"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={issue_closed:'Closed'};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        'LT.tf="30";',   # re-anchored (#1247): the visit's choice, the month view, set by hand
        f"process.stdout.write(flowLead({json.dumps(lead)}));",
    ])
    path = tmp_path / "flow-control.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    circles = re.findall(r"<circle [^>]*>", result.stdout)
    clusters = lead["control"]["all"]["clusters"]
    assert len(circles) == len(clusters) == 2, "cards 1 and 2 share a close: one cluster; card 3 is its own mark"
    assert "2 issues" in result.stdout


def _drawn_lead_svg(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-09T09:00:00Z")})
    lead = hub.flow_lead()["lead"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={issue_closed:'Closed'};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        'LT.tf="30";',   # re-anchored (#1247): the visit's choice, the month view, set by hand
        f"process.stdout.write(flowLead({json.dumps(lead)}));",
    ])
    path = tmp_path / "flow-lead-axis.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _svg_attr(attrs, name):
    m = re.search(rf"(?:^|\s){name}=(\S+)", attrs)
    return m.group(1) if m else None


def _svg_texts(svg):
    return [(attrs, body) for attrs, body in re.findall(r"<text ([^>]*)>([^<]*)</text>", svg)]


def _has_class(attrs, name):
    """True when an element's class attribute carries `name` as one exact token, never as a substring of another."""
    m = re.search(r"(?:^|\s)class=([^\s>]+)", attrs)
    return bool(m and name in m.group(1).split())


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_lead_time_axis_title_is_drawn_from_the_plot_left_edge(hub, board, tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 10, tzinfo=tz)
    monkeypatch.setattr(hub, "dt", types.SimpleNamespace(**{**vars(hub.dt), "datetime": Frozen}))
    svg = _drawn_lead_svg(hub, board, tmp_path)
    left = next(re.search(r"x1=(\S+)", line).group(1)
                for line in re.findall(r"<line [^>]*>", svg)
                if re.search(r"x1=(\S+) y1=\S+ x2=\1 ", line))
    titles = [(attrs, body) for attrs, body in _svg_texts(svg) if body.startswith("Lead time")]
    assert len(titles) == 1
    attrs, body = titles[0]
    # Re-anchored (#1332): the y axis is titled above it, outside the plot, with its scale named.
    assert body == "Lead time (log)", "the full axis title is drawn"
    assert _svg_attr(attrs, "x") == left, "the title starts at the plot's left edge"
    assert _svg_attr(attrs, "text-anchor") == "start"
    frame_top = float(re.search(r"<line x1=\S+ y1=(\S+)", svg).group(1))
    assert 0 < float(_svg_attr(attrs, "y")) < frame_top, "the y axis title does not sit above the plot"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_moving_the_axis_title_leaves_the_other_chart_text_in_place(hub, board, tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 10, tzinfo=tz)
    monkeypatch.setattr(hub, "dt", types.SimpleNamespace(**{**vars(hub.dt), "datetime": Frozen}))
    svg = _drawn_lead_svg(hub, board, tmp_path)
    others = [(attrs, body) for attrs, body in _svg_texts(svg) if not body.startswith(("Lead time", "Closed date"))]
    tick_labels = [(attrs, body) for attrs, body in others if _svg_attr(attrs, "x") == "57"]
    # Re-anchored (#1355): the y axis is always log, so its three round ticks name an hour, a day and a week.
    assert len(tick_labels) == 3, "the log y ticks keep their place beside the axis"
    assert [body for _, body in tick_labels] == ["1h", "1d", "7d"]
    assert all(_svg_attr(attrs, "text-anchor") == "end" for attrs, _ in tick_labels)
    x_labels = [(attrs, body) for attrs, body in others if _svg_attr(attrs, "text-anchor") == "middle"]
    assert len(x_labels) == 7, "the seven x ticks keep their place under the axis"
    base = float(re.search(r"<line x1=\S+ y1=(\S+) x2=\S+ y2=\1 stroke=var\(--chart-grid\)>", svg).group(1))
    assert {float(_svg_attr(attrs, "y")) for attrs, _ in x_labels} == {base + 17}
    # Re-anchored (#1332): only the average is drawn by default, and its value sits in the right gutter, never in the plot.
    tags = [(attrs, body) for attrs, body in others if _has_class(attrs, "lttg")]
    assert len(tags) == 3, "the average, the median and p85 each draw their gutter tag at first"
    assert all(_svg_attr(attrs, "text-anchor") == "start" for attrs, _ in tags), "a gutter tag is not left-aligned"
    right = max(float(x) for x in re.findall(r'<line x1=\S+ y1=\S+ x2=(\S+) y2=\S+ stroke=var\(--chart-grid\)>', svg))
    assert all(float(_svg_attr(attrs, "x")) > right for attrs, _ in tags), "a reference tag sits inside the plot"


# SPEC 889 #901: the cumulative flow has a range selector and a hover that names each band's count for the day.
CFD_RANGE_KEYS = ["7", "30", "90", "all"]


def test_the_cfd_serves_a_view_for_each_range_ending_on_the_last_day(hub, board):
    _seed(hub, {1: _todo(1)})

    cfd = _flow(hub)["cfd"]
    ranges = cfd["ranges"]
    today = datetime.now(UTC).date().isoformat()

    assert list(ranges) == CFD_RANGE_KEYS
    assert len(ranges["7"]["days"]) == 7
    assert ranges["7"]["days"][-1] == today, "the window ends on the last day of the history"
    assert len(ranges["30"]["days"]) == 30 and len(cfd["days"]) > 30, "a window cuts the history to its last days"
    assert ranges["all"]["days"] == cfd["days"]


def test_each_range_serves_wip_and_throughput_per_day_from_board_cfd(hub, board):
    cfd_mod = _load("board_cfd_range_check", SCRIPTS / "board_cfd.py")
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _todo(2)})

    view = _flow(hub)["cfd"]["ranges"]["all"]
    direct = cfd_mod.cfd(_intervals(hub), hub.ORDER, zone="UTC", now=datetime.now(UTC))

    # #1149: the per-day reading is one record per day, never a series the page indexes.
    assert "wip" not in view and "throughput" not in view, "the payload still serves a per-day series for the page to index"
    assert [r["day"] for r in view["reads"]] == view["days"]
    assert [r["wip"] for r in view["reads"]] == [p["wip"] for p in direct["points"]]
    assert [r["throughput"] for r in view["reads"]] == [p["throughput"] for p in direct["points"]], "the first day has no throughput"
    assert view["reads"][0]["counts"] == [{"name": b["name"], "count": b["count"][0]} for b in view["bands"]]


def test_each_range_serves_the_last_days_wip_and_throughput(hub, board):
    """#1149: the hub chooses the last day and serves its WIP and throughput; the page prints them."""
    cfd_mod = _load("board_cfd_last_check", SCRIPTS / "board_cfd.py")
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _todo(2)})

    cfd = _flow(hub)["cfd"]
    last = cfd_mod.cfd(_intervals(hub), hub.ORDER, zone="UTC", now=datetime.now(UTC))["points"][-1]
    for key, view in cfd["ranges"].items():
        assert view["last"] == {"day": last["day"], "wip": last["wip"], "throughput": last["throughput"]}, key


def test_an_empty_history_serves_no_last_day(hub, board):
    _seed(hub, {})
    view = _flow(hub)["cfd"]["ranges"]["7"]
    assert view["last"] is None and view["reads"] == []


def _trapped(view):
    """A node expression for `view` whose per-day series throw when read: a render that reads one fails (#1149)."""
    trap = '{get(){throw new Error("the page read a per-day series")}}'
    return ("(()=>{const v=" + json.dumps(view) + ";"
            f'for(const b of v.bands)Object.defineProperty(b,"count",{trap});'
            f'Object.defineProperty(v,"wip",{trap});Object.defineProperty(v,"throughput",{trap});'
            "return flowCFD(v)})()")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_flow_page_prints_the_served_last_day_and_reads_no_series(hub, board, tmp_path):
    """#1149: the page prints the hub's last-day WIP and throughput, and draws with every per-day series booby-trapped."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _todo(2)})
    view = _flow(hub)["cfd"]["ranges"]["all"]
    view["last"] = {"day": view["last"]["day"], "wip": 417, "throughput": 23}   # values no series holds: the page prints, never derives
    out = _render(hub, tmp_path, "flow-cfd-last.js", _trapped(view))
    notes = re.findall(r"<p class=fnote>(.*?)</p>", out)
    assert len(notes) == 1, f"the flow view carries more than one line of notes: {notes}"
    assert "WIP <b>417</b>" in notes[0] and "throughput <b>23</b>" in notes[0], notes[0]
    assert view["last"]["day"] in notes[0], "the line names the day the hub chose"
    titles = [t for t in re.findall(r"<title>([^<]*)</title>", out) if t.startswith("2026-10-02")]
    read = next(r for r in view["reads"] if r["day"] == "2026-10-02")
    assert titles and f"WIP {read['wip']}" in titles[0], "the day hover reads the hub's record for that day"
    view["last"] = {"day": view["last"]["day"], "wip": 0, "throughput": None}
    assert "throughput <b>none</b>" in _render(hub, tmp_path, "flow-cfd-none.js", _trapped(view))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_flow_legend_comes_before_its_one_line_of_notes(hub, board, tmp_path):
    """#1149, re-anchors the notes under the plot: the legend first, then one notes line that holds the history start, the
    coverage and the held stripe; nothing follows it."""
    _stack(hub)
    out = _render(hub, tmp_path, "flow-cfd-order.js", f"flowCFD({json.dumps(_flow(hub)['cfd']['ranges']['all'])})")
    after = out.split("</svg>", 1)[1]
    legend = re.match(r"<p>(<span class=flg>.*?)</p>", after)
    assert legend, f"the legend does not follow the plot: {after[:120]}"
    rest = after[legend.end():]
    note = re.fullmatch(r"<p class=fnote>(.*?)</p>", rest)
    assert note, f"the legend is not followed by exactly one notes line: {rest}"
    for words in ("history from ", "cards read ", "darker stripe"):
        assert words in note.group(1), f"the notes line lost {words!r}"
    assert "<p class=fnote>" not in out.split("<svg", 1)[0], "a note sits above the plot"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_axis_text_takes_the_page_size_and_the_muted_ink(hub, board, tmp_path):
    """SPEC 1123 #1141 re-anchors #1149's ink: a chart's axis text inherits the box size and takes `mute`, so the flow
    and the lead-time views paint one axis style."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    cfd = _render(hub, tmp_path, "flow-cfd-ink.js", f"flowCFD({json.dumps(_flow(hub)['cfd']['ranges']['all'])})")
    lead = _lt_chart(hub, tmp_path, "lt-ink.js", _flow(hub)["lead"]["control"]["all"])
    def axis_texts(markup):
        """#1304: reference end labels and cluster counts are annotations in their mark's colour, not axis text."""
        return [(a, b) for a, b in re.findall(r"<text ([^>]*)>([^<]*)</text>", markup)
                if not re.fullmatch(r"\d+", b) and not b.startswith(("Average ", "Median ", "p85 "))]

    def fills(markup):
        texts = axis_texts(markup)
        assert texts and all("font-size" not in a for a, _ in texts), "an SVG text sets its own size"
        return {re.search(r'fill="?([^" >]+)', a).group(1) for a, _ in texts}
    assert fills(cfd) == fills(lead) == {"var(--chart-axis)"}, (fills(cfd), fills(lead))
    assert _resolved("var(--chart-axis)", _tokens()) == _resolved("var(--mute)", _tokens()), "axis text is not mute"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_family_draws_one_grid_axis_and_title_treatment(hub, board, tmp_path, monkeypatch):
    """SPEC 1123 #1141: the flow chart and the lead-time plot share the grid and frame in `line`, axis text in `mute` at
    body size, and a title inside the plot at the left edge."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 10, tzinfo=tz)
    monkeypatch.setattr(hub, "dt", types.SimpleNamespace(**{**vars(hub.dt), "datetime": Frozen}))
    tokens = _tokens()
    lead = _drawn_lead_svg(hub, board, tmp_path)
    _stack(hub)
    cfd = _render(hub, tmp_path, "flow-family.js", f"flowCFD({json.dumps(_flow(hub)['cfd']['ranges']['all'])})")
    assert _resolved("var(--chart-grid)", tokens) == _resolved("var(--line)", tokens), "the grid is not line"
    assert "#flowb" in hub.PAGE and "font-size:var(--fs-body)" in re.search(r"#flowb\{[^}]*\}", hub.PAGE).group(0)
    for name, svg, title in (("flow", cfd, "cards"), ("lead", lead, "Lead time")):
        texts = re.findall(r"<text ([^>]*)>([^<]*)</text>", svg)
        assert texts, name
        assert all("font-size" not in attrs for attrs, _ in texts), f"{name} text sets its own size"
        axis = [(a, b) for a, b in texts if not re.fullmatch(r"\d+", b)]   # #1332: reference tags are axis ink too
        assert {re.search(r'fill="?([^" >]+)', " " + attrs).group(1) for attrs, _ in axis} == {"var(--chart-axis)"}, \
            f"{name} axis text does not take mute"
        grid = [ln for ln in re.findall(r"<line [^>]*>", svg) if "stroke=var(--chart-grid)" in ln]
        vert = next(ln for ln in grid if re.search(r"x1=(\S+) y1=\S+ x2=\1 ", ln))
        horiz = next(ln for ln in grid if re.search(r"y1=(\S+) .*y2=\1", ln))
        left, frame_top = re.search(r"x1=(\S+) y1=(\S+)", vert).groups()
        frame_base = re.search(r"y1=(\S+)", horiz).group(1)
        found = [(attrs, body) for attrs, body in texts if body.startswith(title)]
        assert len(found) == 1, f"{name} draws no single {title!r} title"
        attrs, _ = found[0]
        assert _svg_attr(attrs, "x") == left, f"{name} title does not start at the plot's left edge"
        assert _svg_attr(attrs, "text-anchor") == "start"
        # Re-anchored (#1332): the lead chart titles its y axis above the plot, the flow chart inside it.
        if name == "lead":
            assert float(_svg_attr(attrs, "y")) < float(frame_top), f"{name} title does not sit above its axis"
        else:
            assert float(frame_top) < float(_svg_attr(attrs, "y")) < float(frame_base), \
                f"{name} title does not sit inside the plot"


def test_each_band_serves_its_count_per_day(hub, board):
    _seed(hub, {1: _todo(1), 2: _done(2, "2026-10-01T15:00:00Z")})

    view = _flow(hub)["cfd"]["ranges"]["all"]

    assert _count(view, hub.COLUMN["issue_todo"], "2026-10-01") == 1
    assert _count(view, hub.COLUMN["issue_closed"], "2026-10-01") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cfd_names_a_range_selector_and_a_day_hover_with_counts(hub, board, tmp_path):
    _seed(hub, {1: _todo(1), 2: _done(2, "2026-10-01T15:00:00Z")})
    view = _flow(hub)["cfd"]["ranges"]["all"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        f"process.stdout.write(flowCFD({json.dumps(view)}));",
    ])
    path = tmp_path / "flow-cfd-range.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '<select id=cfdr' in result.stdout
    for value in ("7", "30", "90", "all"):
        assert f'value={value}' in result.stdout
    titles = re.findall(r"<title>([^<]*)</title>", result.stdout)
    day_titles = [t for t in titles if t.startswith("2026-10-01")]
    assert day_titles, f"no hover names the day: {titles}"
    assert "WIP" in day_titles[0] and "throughput" in day_titles[0]
    assert f"{hub.COLUMN['issue_closed']} 1" in day_titles[0], "the hover names each band's count"


# SPEC 889 #900: the drag-to-zoom route. The hub recomputes the control chart for the dragged range.
def test_the_leadtime_route_recomputes_the_chart_for_a_dragged_range(hub, board):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-09T09:00:00Z")})

    chart = hub.lead_zoom("from=2026-10-05T00:00:00.000Z&to=2026-10-12T00:00:00.000Z")

    assert [dot["n"] for dot in chart["dots"]] == [2]
    assert chart["axis"] == "log"
    assert chart["range"]["start"].startswith("2026-10-05")


def test_the_leadtime_route_refuses_a_missing_or_empty_range(hub, board):
    assert "error" in hub.lead_zoom("from=2026-10-05T00:00:00Z")
    assert "error" in hub.lead_zoom("from=2026-10-05T00:00:00Z&to=2026-10-05T00:00:00Z")
    assert "error" in hub.lead_zoom("from=nonsense&to=2026-10-05T00:00:00Z")


def test_the_server_answers_the_leadtime_route_and_the_page_calls_it(hub):
    source = HUB_PY.read_text()
    assert 'self.path.startswith("/api/leadtime")' in source
    assert "/api/leadtime?" in hub.PAGE
    assert "Back to " in hub.PAGE


def test_the_board_answer_carries_no_flow_data(hub, board):
    served = hub.board()
    assert "flow" not in served
    assert "lead" not in served


def test_each_chart_has_its_own_route_and_the_page_fetches_it_on_open(hub):
    source = HUB_PY.read_text()
    assert 'self.path.startswith("/api/chart/cfd")' in source
    assert 'self.path.startswith("/api/chart/lead")' in source
    assert "fetch(`/api/chart/${k}?${q}`)" in hub.PAGE   # the bar's values ride on the route (#904)
    assert "b.flow" not in hub.PAGE, "the board poll still hands the chart its data"


@pytest.mark.parametrize("chart, compute", [("cfd", "_cfd_payload"), ("lead", "_lead_payload")])
def test_a_chart_route_answers_from_its_cache_and_keeps_its_read_instant(hub, board, monkeypatch, chart, compute):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    real = getattr(hub, compute)

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(hub, compute, slow)
    route = getattr(hub, f"flow_{chart}")
    before = time.time()
    first = route()
    started = time.monotonic()
    second = route()
    answered_in = time.monotonic() - started

    assert answered_in < 0.1, f"the second read recomputed instead of answering from the cache: {answered_in:.2f}s"
    assert second["t"] == first["t"], "the cached answer carries the read's instant, not the answer's"
    assert before <= first["t"] <= time.time() - 0.4, "the data instant is when the chart read its data, before the slow part"
    assert chart in first


def test_the_board_poll_never_computes_chart_data(hub, board, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the board poll computed chart data")

    monkeypatch.setattr(hub, "_cfd_payload", boom)
    monkeypatch.setattr(hub, "_lead_payload", boom)
    assert "groups" in hub.board()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_drag_maps_its_plot_fractions_to_the_dates_of_the_range(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", STORAGE, block,
        "const r=ltSpan({start:'2026-10-01T00:00:00+00:00',end:'2026-10-11T00:00:00+00:00'},0.5,1);",
        "process.stdout.write(JSON.stringify(r));",
    ])
    path = tmp_path / "flow-zoom.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["2026-10-06T00:00:00.000Z", "2026-10-11T00:00:00.000Z"]


def test_a_chart_is_a_sibling_tab_with_no_close_and_no_title_of_its_own(hub):
    """The owner: Runs, Cumulative Flow Diagram and Lead Time are sibling tabs, so a chart draws no title bar and no close button."""
    assert "<button id=flowx" not in hub.PAGE, "a chart tab still carries a close button"
    assert "<b id=flowt>" not in hub.PAGE, "a chart tab still carries its own title"


def test_a_live_run_click_never_leaves_the_tab_the_user_is_on(hub):
    """The owner: a second click on a live run moved the page from the board to the charts tab; the user changes tabs."""
    assert 'setTab("flow",{runs:true})' not in hub.PAGE


def _render(hub, tmp_path, name, expression, block=None, storage=None):
    # Re-anchored (#1247): the page no longer restores a stored choice, so the harness sets the visit's choice itself: the
    # All timeframe on the log axis, the view these drawings were written against.
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", storage or STORAGE,
                         block or _flow_block(hub).split("const drawFlow")[0], 'LT.tf="all";',
                         f"process.stdout.write({expression});"])
    path = tmp_path / name
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_cfd_draws_each_bands_held_share_names_its_zone_and_states_its_coverage(hub, board, tmp_path):
    """#1024 (2, 3): a band's held cards sit at its base as a darker stripe, the day labels name the zone the day ends in,
    and the history's coverage is stated under the chart."""
    _stack(hub)
    cfd = _flow(hub)["cfd"]
    for band in cfd["bands"]:
        band["held"] = [1 if band["name"] == "Todo" else 0 for _ in cfd["days"]]
    out = _render(hub, tmp_path, "flow-cfd-held.js", f"flowCFD({json.dumps(cfd)})")
    labels = re.findall(r"<text[^>]*>([^<]*)</text>", out)
    assert "<title>Todo held</title>" in out, "the held share of the Todo band is drawn"
    assert f"day ({cfd['zone']})" in labels, f"the day axis names its zone: {labels}"
    assert "cards read" in out, "the chart states its coverage"


def test_the_lead_chart_states_its_coverage_and_marks_approximate_and_unknown_starts(hub, board, tmp_path):
    """#1024 (3): an approximate or unknown-first card is drawn dashed, and the chart states its coverage."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    lead = hub.flow_lead(ty="task")["lead"]
    dot = lead["control"]["all"]["clusters"][0]["dots"][0]
    assert {"flowing", "held", "approx", "unknown_first"} <= set(dot), dot
    assert abs(dot["flowing"] + dot["held"] - dot["hours"]) < 1e-9, "flowing and held make the card's whole time"
    out = _render(hub, tmp_path, "flow-lead-coverage.js", f"flowLead({json.dumps(lead)})")
    assert "cards read" in out, "the lead chart states its coverage"
    assert not re.search(r"<circle[^>]*stroke-dasharray", out), "a card with a verified start is drawn solid"
    dot["approx"] = True
    out = _render(hub, tmp_path, "flow-lead-approx.js", f"flowLead({json.dumps(lead)})")
    assert re.search(r'<circle[^>]*stroke-dasharray="3 2"', out), "an approximate start is dashed"
    assert "start approximate" in out


# SPEC 1218 #1256: the lead chart breaks each card's life into its columns, and reports gate time.
def test_the_lead_chart_gives_a_gating_interval_its_own_band(hub, board):
    gated = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Todo"},
             {"t": "2026-10-01T15:00:00Z", "type": "status", "status": "Gating"},
             {"t": "2026-10-02T09:00:00Z", "type": "status", "status": "Closed"},
             {"t": "2026-10-02T09:00:00Z", "type": "closed"}]
    item = _card(1, st="Closed", s="CLOSED", c="2026-10-02T09:00:00Z")
    _seed(hub, {1: (item, gated), 2: _done(2, "2026-10-03T09:00:00Z")})

    lead = _flow(hub)["lead"]
    row = next(s for s in lead["series"] if s["n"] == 1)
    plain = next(s for s in lead["series"] if s["n"] == 2)

    assert {band["column"]: band["hours"] for band in row["stack"]} == {"Todo": 6.0, "Gating": 18.0}
    assert sum(band["hours"] for band in row["stack"]) == pytest.approx(row["hours"]), \
        "the stack disagrees with the measure beside it"
    assert row["gate_hours"] == pytest.approx(18.0)
    assert "Gating" not in [band["column"] for band in plain["stack"]], "a card that never gated draws no band"
    assert plain["gate_hours"] == 0.0

    chart = lead["control"]["all"]
    assert sum(band["hours"] for dot in chart["dots"] for band in dot["stack"] if band["column"] == "Gating") == \
        pytest.approx(chart["gate"]["hours"]), "gate time is not the Gating band's total"
    assert chart["gate"]["hours"] == pytest.approx(18.0)
    assert chart["gate"]["text"] == "18h"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_stack_lives_in_the_dot_tooltip_and_gate_time_stays_a_text_metric(hub, tmp_path):
    """Re-anchored (#1331): the plot draws no stacked bar; each dot's column stack is its tooltip's stage strip."""
    gated = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Todo"},
             {"t": "2026-10-01T15:00:00Z", "type": "status", "status": "Gating"},
             {"t": "2026-10-02T09:00:00Z", "type": "status", "status": "Closed"},
             {"t": "2026-10-02T09:00:00Z", "type": "closed"}]
    board_path = hub._hist.history_path(hub.BOARD, hub.REPO)
    item = _card(1, st="Closed", s="CLOSED", c="2026-10-02T09:00:00Z")
    hub._hist.record_issue(board_path, 1, OPENED, OPENED, gated, True, now=time.time(), keys=hub.bar_keys(item))
    hub.board_cache["items"] = [item]
    chart = hub.flow_lead(ty=None)["lead"]["control"]["all"]

    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        f"const COLORS={hub.BOARD_COLORS_JSON},COLNAME={hub.BOARD_NAMES_JSON};", "const esc=s=>String(s);",
        "const ls={};", STORAGE, block, 'LT.tf="all";',
        f"process.stdout.write(flowLead({json.dumps(hub.flow_lead(ty=None)['lead'])}));",
    ])
    path = tmp_path / "lt-stack-roles.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    out = result.stdout

    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    assert "<rect" not in plot, "the plot still draws a stacked bar"
    title = re.search(r"<circle[^>]*><title>([^<]*)</title>", out).group(1)
    assert "Gating 18h" in title and "Todo 6h" in title, "the stage strip does not name each column's hours"
    assert "Gate time" not in "".join(re.findall(r'<p class="flg ltlg">(.*?)</p>', out)), \
        "gate time is not a legend entry: zero has no counterpart on a log axis (#1304)"
    note = re.search(r'<p class="fnote ltkm">(.*?)</p>', out).group(1)
    assert f"Gate time {chart['gate']['text']}" in note, "gate time is not a text metric beside the survival reading"


# SPEC 1327 #1334: the header states the summary in two rows, and the range control switches x from the one payload.
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_header_states_the_summary_in_two_rows(hub, board, tmp_path):
    item, events = _done(3, "2026-10-04T09:00:00Z")
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-03T09:00:00Z"),
                3: ({**item, "cr": "not_planned"}, events)})
    lead = hub.flow_lead(ty="task")["lead"]
    out = _render(hub, tmp_path, "lt-two-row-head.js", f"flowLead({json.dumps(lead)})")

    header = out.split("<p class=fhead><label>Timeframe")[0]   # the header rows, before the control row
    rows = re.findall(r"<p class=fhead>(.*?)</p>", header)
    assert len(rows) == 2, f"the header is not two rows: {rows}"
    first, second = rows
    assert "window 20 issues" in first, "the first row names the window"
    assert "min <b>1d</b>" in second and "max <b>2d</b>" in second, "the second row states the summary"
    assert "2 issues plotted" in second, "the second row states the plotted count"
    assert re.search(r'title="1 closed as not planned: #3"[^>]*>excluded 1: 1 closed as not planned<', second), \
        "the second row carries the one exclusion total with its reason (#1354)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_fit_data_and_full_window_switch_the_x_range_from_the_one_payload(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]

    full = _lt_chart(hub, tmp_path, "lt-range-full.js", chart)
    assert "id=ltfit" in full and "id=ltfull" in full, "the range control names both ranges"
    assert chart["range"]["text"] in full, "the full window range is not printed"

    fitted = _render(hub, tmp_path, "lt-range-fit.js", f'(LT.win="fit",ltChart({json.dumps(chart)},null))')
    assert chart["fit"]["text"] in fitted, "the fit range is not printed"
    assert chart["range"]["text"] not in fitted

    for control in ("ltfit", "ltfull"):
        handler = next(line for line in hub.PAGE.splitlines() if f'e.target.id==="{control}"' in line)
        assert "ltZoom" not in handler and "fetch(" not in handler, \
            f"the {control} control makes a second request instead of redrawing the held payload"
    fit_line = next(line for line in hub.PAGE.splitlines() if 'e.target.id==="ltfit"' in line)
    assert 'LT.win="fit"' in fit_line
    full_line = next(line for line in hub.PAGE.splitlines() if 'e.target.id==="ltfull"' in line)
    assert 'LT.win="full"' in full_line


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_zoom_with_cards_draws_when_the_timeframe_holds_none(hub, board, tmp_path):
    """#1334: the chart actually drawn decides emptiness, so a drag zoom with dots draws over an empty timeframe."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    lead = hub.flow_lead(ty="task")["lead"]
    zoom = lead["control"]["all"]
    lead["control"]["all"] = {**zoom, "dots": [], "clusters": []}
    lead["summary"] = None

    out = _render(hub, tmp_path, "lt-zoom-empty-timeframe.js",
                  f"(LT.zc={json.dumps(zoom)},flowLead({json.dumps(lead)}))")

    assert "<circle" in out, "the zoom chart's dots are not drawn over an empty timeframe"
    assert "no closed card yet" not in out, "the empty timeframe hides the zoom chart it did not draw"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_view_chip_prints_the_range_when_a_payload_carries_no_text(hub, board, tmp_path):
    """#1334: the printed range comes from the range's ends when a payload carries no ready-made text."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["all"]
    chart["range"] = {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-05T00:00:00+00:00"}

    out = _render(hub, tmp_path, "lt-range-derived.js", f"ltChart({json.dumps(chart)},null)")

    assert "Oct 1 to Oct 5 UTC" in out, "the chip prints no range for a payload without range.text"
    assert "undefined" not in re.sub(r"<title>.*?</title>", "", out), "the printed range is undefined"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_fit_band_lines_up_with_the_marks_when_one_dot_has_no_fit_position(hub, board, tmp_path):
    """#1334: the drawn band takes the same x source as the marks; a dot without xf keeps the window x."""
    now = datetime.now(UTC)
    rows = [{"n": n, "t": f"card {n}", "o": (now - timedelta(hours=hours + 8)).isoformat(),
             "c": (now - timedelta(hours=hours)).isoformat(), "column": "Done", "hours": 8.0,
             "flowing": 8.0, "held": 0.0, "approx": False, "unknown_first": False}
            for n, hours in enumerate([5, 4, 3], start=1)]
    chart = hub._leadtime.control_chart(rows, timeframe=30, now=now)
    assert chart["fit"] is not None, "the fixture does not cluster into a fit range"
    chart["dots"][0].pop("xf", None)

    out = _render(hub, tmp_path, "lt-band-fit-mixed.js",
                  f'(LT.band=true,LT.win="fit",ltChart({json.dumps(chart)},null))')

    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    polygon = re.search(r'<polygon points="([^"]*)"', plot)
    band_x = [float(p.split(",")[0]) for p in polygon.group(1).split()][:len(chart["dots"])]
    roll = re.search(r'<polyline [^>]*points="([^"]*)"', plot)
    roll_x = [float(p.split(",")[0]) for p in roll.group(1).split()]
    assert band_x == pytest.approx(roll_x), \
        "the fit band is drawn from a different x source than the marks it bounds"
