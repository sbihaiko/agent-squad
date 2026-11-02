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
from datetime import UTC, datetime
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
    tabs = hub.PAGE[hub.PAGE.index("<div class=rkt id=flowtabs>"):hub.PAGE.index("<div id=main>")]
    # Re-anchored by the owner (2026-11-01): one tab per chart, so the burn-down never draws inside the flow diagram (#1080).
    assert [re.sub(r"<[^>]*>", "", x) for x in re.findall(r"<button[^>]*>.*?</button>", tabs)] == ["Runs", "Cumulative Flow Diagram", "Burn-Down", "Lead Time"]
    assert "id=flowv" not in hub.PAGE, "the View selector is gone: a chart is a tab"
    assert 'id=bburn' in tabs and 'flowOpen("burndown")' in hub.PAGE
    assert 'flowOpen=async k=>{FLOWK=k;$("#flowov").hidden=false;flowTabs();' in hub.PAGE, "the lit tab follows the chart drawn"
    head = hub.PAGE[hub.PAGE.index("<div id=bhead>"):hub.PAGE.index("<div id=bcases>", hub.PAGE.index("<div id=bhead>"))]
    assert "<span id=bt class=m></span>" in head
    assert "id=bflow" not in head and "id=blead" not in head
    assert '<details id=fw' in head and '<details id=fs' in head, "the header lost the filter bar"
    assert 'id=bsp' not in head and 'id=bz' not in head and 'id=bx' not in head, "a removed control is still in the header"


def test_cfd_bands_follow_the_hub_order(hub, board):
    _seed(hub, {1: _todo(1), 2: _done(2, "2026-10-02T09:00:00Z")})
    cfd = _flow(hub)["cfd"]
    assert [b["name"] for b in cfd["bands"]] == list(hub.ORDER), "the drawn bands name every column, in the hub's order"
    assert cfd["start"] == "2026-10-01"
    assert "history from " in hub.PAGE


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
    assert lead["measure"] == "on board -> closed"
    assert lead["window"] == {"size": 20, "unit": "issues"}
    assert lead["summary"]["average"]["text"] == "1d 10h"
    assert lead["summary"]["median"]["text"] == "1d"
    assert lead["summary"]["minimum"]["text"] == "6h"
    assert lead["summary"]["maximum"]["text"] == "3d"
    assert lead["excluded"] == [{"reason": "open", "count": 1}]
    assert lead["excluded_total"] == 1


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
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
        'process.stdout.write(flowLead({type:"task",measure:"on board -> closed",window:{size:20,unit:"issues"},'
        'excluded:[{reason:"not_planned",count:3}],excluded_total:3,series:[],summary:null}));',
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
        "const COLORS={};", "const esc=s=>String(s);", "const ls={};", block,
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
        "const COLORS={};", "const esc=s=>String(s);", "const ls={};", block,
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
    assert flow["lead"]["excluded"] == [{"reason": "open", "count": 1}]


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
    assert cfd["xticks"][0] == {"i": 0, "day": "2026-10-01"}
    assert cfd["xticks"][-1] == {"i": len(cfd["days"]) - 1, "day": cfd["days"][-1]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cfd_draws_labelled_axes_with_numbered_ticks_and_dated_ticks(hub, board, tmp_path):
    """#896 (1): the page draws both axes, one labelled tick per number the hub served and one per date."""
    _stack(hub)
    cfd = _flow(hub)["cfd"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
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
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
        f"process.stdout.write(flowCFD({json.dumps(cfd)}));",
    ])
    path = tmp_path / "flow-cfd-legend.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    legend = re.findall(r"<span class=flg><i[^>]*></i>([^<]*)</span>", result.stdout)
    assert legend == names, "every band drawn has exactly one legend entry, in the drawn order"
    assert len(re.findall(r"<polygon ", result.stdout)) == len(names), "the legend counts the bands the chart draws"


# SPEC 889 #900: the lead-time diagram is the Jira-style control chart, computed by the hub (board_leadtime).
TIMEFRAME_KEYS = ["1", "2", "3", "7", "14", "30", "all"]   # the owner replaced the 3- and 6-month presets with 24, 48 and 72 hours (2026-11-01)


def test_the_control_chart_is_served_for_every_timeframe_and_both_scales(hub, board):
    hub.board_cache["items"] = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z")]

    control = _flow(hub)["lead"]["control"]

    assert set(control) == {"jira", "log"}
    for scale in control.values():
        assert list(scale) == TIMEFRAME_KEYS
    assert control["log"]["all"]["axis"] == "log"
    assert control["jira"]["all"]["axis"] == "linear"


def test_a_control_chart_dot_carries_the_issue_title_and_its_close_date(hub, board):
    item, events = _done(7, "2026-10-02T09:00:00Z")
    _seed(hub, {7: ({**item, "t": "ship the chart"}, events)})

    dot = _flow(hub)["lead"]["control"]["jira"]["all"]["dots"][0]

    assert dot["n"] == 7
    assert dot["title"] == "ship the chart"
    assert dot["closed"] == "2026-10-02"


def test_the_control_chart_names_its_lines_and_controls_the_jira_way(hub):
    for name in ("Rolling average (", "centered)", "Standard deviation", "Past month", "Log scale", "Timeframe"):
        assert name in hub.PAGE, f"the page names no {name!r}"


def test_the_legend_says_what_each_line_answers(hub):
    """Owner, 2026-11-01: a reader asks what p85 means; the legend answers it in place, never by a second line of numbers."""
    for reading in ("85% of closed cards finished within this time", "one slow card lifts it",
                    "half the cards closed faster", "the usual spread"):
        assert reading in hub.PAGE, f"the legend does not say {reading!r}"
    assert "ltV(c.p85)" in hub.PAGE and "ltV(c.median)" in hub.PAGE, "the legend carries no value beside its line"


def _lt_chart(hub, tmp_path, name, chart, zr=None):
    """The lead chart's markup for one control chart; `zr` is the drag zoom the page holds, or none."""
    zoom = f"LT.zr={json.dumps(zr)}" if zr else "LT.zr=null"
    return _render(hub, tmp_path, name, f"({zoom},ltChart({json.dumps(chart)}))")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_controls_are_grouped_by_what_they_do_and_the_page_names_their_order(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    out = _lt_chart(hub, tmp_path, "lt-groups.js", _flow(hub)["lead"]["control"]["jira"]["all"])
    order = ["Filters", "id=ltr", "id=lty", "Measurement", "id=lta", "id=ltb", "Display", "id=lts", "View", "id=ltz"]
    marks = [out.index(mark) for mark in order]
    assert marks == sorted(marks), f"the controls are not grouped in the order named: {order}"
    assert "Set the controls in order: filters, then measurement, then display, then view." in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_zoom_button_names_the_view_it_returns_to_and_is_disabled_in_it(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["jira"]["all"]
    home = _lt_chart(hub, tmp_path, "lt-zoom-home.js", chart)
    zoomed = _lt_chart(hub, tmp_path, "lt-zoom-held.js", chart, zr=["2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00"])
    assert re.search(r"<button id=ltz disabled[^>]*>Back to All</button>", home), "the zoom button is live in the view it names"
    assert re.search(r'<button id=ltz title="[^"]*">Back to All</button>', zoomed), "the zoom button is disabled while zoomed"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_clustered_view_offers_a_fit_button_that_names_its_range(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    out = _lt_chart(hub, tmp_path, "lt-fit.js", _flow(hub)["lead"]["control"]["jira"]["all"])
    assert "Fit to data: Oct 2 09:00 to Oct 2 10:00 UTC" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_one_exclusion_total_is_shown_and_the_plotted_sample_is_stated(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _todo(2), 3: _todo(3)})
    lead = hub.flow_lead(ty="task")["lead"]
    chart = lead["control"]["jira"]["all"]
    assert chart["excluded_total"] == sum(chart["excluded"].values()) == 2, "the total is the rows the chart counted"
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_legend_marks_each_entry_once_and_keeps_the_band_entry_whole(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-03T09:00:00Z")})
    out = _lt_chart(hub, tmp_path, "lt-legend.js", _flow(hub)["lead"]["control"]["jira"]["all"])
    legend = re.search(r'<p class="flg ltlg">(.*?)</p>', out).group(1)
    entries = re.findall(r"<span>(.*?)</span>", legend)
    assert len(entries) == 5, entries
    assert all(entry.count("<i") == 1 for entry in entries), "an entry has no marker or two"
    band = next(entry for entry in entries if "Standard deviation" in entry)
    assert "<i" in band, "the band's label is split from its marker"


def test_a_legend_entry_stays_on_one_line(hub):
    assert re.search(r"\.ltlg span\{[^}]*white-space:nowrap", hub.PAGE), "a legend entry may wrap across lines"


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
        "const COLORS={},COLNAME={issue_closed:'Closed'};", "const esc=s=>String(s);", "const ls={};", block,
        f"process.stdout.write(flowLead({json.dumps(lead)}));",
    ])
    path = tmp_path / "flow-control.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    circles = re.findall(r"<circle [^>]*>", result.stdout)
    clusters = lead["control"]["jira"]["all"]["clusters"]
    assert len(circles) == len(clusters) == 2, "cards 1 and 2 share a close: one cluster; card 3 is its own mark"
    assert "2 issues" in result.stdout


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

    assert view["wip"] == [p["wip"] for p in direct["points"]]
    assert view["throughput"] == [p["throughput"] for p in direct["points"]], "the first day has no throughput"


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
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
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

    chart = hub.lead_zoom("from=2026-10-05T00:00:00.000Z&to=2026-10-12T00:00:00.000Z&scale=log")

    assert [dot["n"] for dot in chart["dots"]] == [2]
    assert chart["scale"] == "log"
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
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
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


def _render(hub, tmp_path, name, expression, block=None):
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={lt:{tf:\"all\"}};",
                         block or _flow_block(hub).split("const drawFlow")[0], f"process.stdout.write({expression});"])
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
    dot = lead["control"]["jira"]["all"]["clusters"][0]["dots"][0]
    assert {"flowing", "held", "approx", "unknown_first"} <= set(dot), dot
    assert abs(dot["flowing"] + dot["held"] - dot["hours"]) < 1e-9, "flowing and held make the card's whole time"
    out = _render(hub, tmp_path, "flow-lead-coverage.js", f"flowLead({json.dumps(lead)})")
    assert "cards read" in out, "the lead chart states its coverage"
    assert not re.search(r"<circle[^>]*stroke-dasharray", out), "a card with a verified start is drawn solid"
    dot["approx"] = True
    out = _render(hub, tmp_path, "flow-lead-approx.js", f"flowLead({json.dumps(lead)})")
    assert re.search(r'<circle[^>]*stroke-dasharray="3 2"', out), "an approximate start is dashed"
    assert "start approximate" in out
