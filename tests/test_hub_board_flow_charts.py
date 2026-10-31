"""The TASKS header opens two diagrams: the cumulative flow bands and the lead-time control chart (SPEC 111, #712).

The hub computes every number with board_cfd and board_leadtime; the page draws them and does no counting. Both
diagrams ride the board read the hub already makes: no GitHub call, no timer, no process of their own.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import time
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


def _sample(day, counts, frozen=0, none=0):
    return {"t": int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp()), "counts": counts, "frozen": frozen, "none": none}


def _history(directory, samples, repo="x_y"):
    (directory / f"hub-flow.{repo}.jsonl").write_text("".join(json.dumps(s) + "\n" for s in samples))


def _flow_block(hub):
    """The page's diagram script, between its two markers."""
    m = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", hub.PAGE, re.S)
    assert m, "the page carries no flow-charts block"
    return m.group(1)


def test_tasks_header_has_flow_and_lead_time_buttons_and_keeps_its_controls(hub):
    head = hub.PAGE[hub.PAGE.index("<div id=bhead>"):hub.PAGE.index('<div id="pip"', hub.PAGE.index("<div id=bhead>"))]
    assert re.search(r"<button id=bflow[^>]*>Flow</button>", head)
    assert re.search(r"<button id=blead[^>]*>Lead time</button>", head)
    assert "<b>TASKS <span id=bt class=m></span></b>" in head
    assert '<summary title="filter cards by label">labels</summary>' in head
    assert 'id=bsp' in head
    assert 'id=bz title="maximize the board over the run pane"' in head
    assert 'id=bx title="collapse"' in head


def test_cfd_bands_follow_the_hub_order_and_add_frozen_and_no_column(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 1, hub.COLUMN["issue_closed"]: 2}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert [b["name"] for b in cfd["bands"]] == [*hub.ORDER, "frozen", "No column"], "the drawn bands name every band (#896)"
    assert cfd["start"] == "2026-10-01"
    assert len(cfd["days"]) == 2
    assert "history from " in hub.PAGE


def test_cfd_band_extents_are_the_board_cfd_counts(hub, board):
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}})])
    bands = {b["name"]: b for b in hub.board()["flow"]["cfd"]["bands"]}
    backlog = bands[hub.COLUMN["unscheduled"]]
    closed = bands[hub.COLUMN["issue_closed"]]
    assert backlog["upper"][0] - backlog["lower"][0] == 2
    assert closed["upper"][0] - closed["lower"][0] == 1
    assert closed["lower"][0] == 0


def test_another_repositorys_history_is_not_drawn(hub, board):
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 5}})], repo="x_other")
    cfd = hub.board()["flow"]["cfd"]
    assert cfd["start"] is None
    assert cfd["bands"] == []


def test_no_history_file_says_no_sample_and_draws_no_band(hub, board):
    cfd = hub.board()["flow"]["cfd"]
    assert cfd["start"] is None
    assert cfd["days"] == []
    assert cfd["bands"] == []
    assert "the flow history has no sample yet" in hub.PAGE


def test_lead_time_summary_measure_window_and_excluded_count(hub, board):
    board_cache = hub.board_cache
    board_cache["items"] = [
        _card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-01T15:00:00Z"),
        _card(2, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z"),
        _card(3, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-04T09:00:00Z"),
        _card(4, st="Todo", s="OPEN", c=None),
    ]
    lead = hub.board()["flow"]["lead"]
    assert lead["measure"] == "opened -> closed"
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
    hub.board_cache["items"] = [
        _card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-01T15:00:00Z"),
        {**_card(2, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z"), "cr": "not_planned"},
        {**_card(3, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-03T09:00:00Z"), "cr": "not_planned"},
        {**_card(4, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-04T09:00:00Z"), "cr": "duplicate"},
    ]

    served = hub.board()

    assert [p["n"] for p in served["flow"]["lead"]["series"]] == [1]
    assert served["flow"]["lead"]["summary"]["median"]["text"] == "6h"
    assert served["flow"]["lead"]["excluded"] == [{"reason": "not_planned", "count": 3}]
    assert served["flow"]["lead"]["excluded_total"] == 3
    abandoned = next(g for g in served["groups"] if g["name"] == "Abandoned")
    assert [r["n"] for r in abandoned["items"]] == [2, 3, 4], "a card closed as not planned is drawn Abandoned, never Closed"
    closed = next(g for g in served["groups"] if g["name"] == hub.COLUMN["issue_closed"])
    assert [r["n"] for r in closed["items"]] == [1]


def test_the_closed_band_never_steps_down_where_the_close_reason_began(hub, board):
    """#921 (DEBT 1): the samples written before the close reason counted put a not-planned card in the Closed band.
    The one re-bucket moves that card across in the past samples, so the Closed band never takes a step down at the
    boundary, and a reader never takes the step for a data error."""
    flow = _load("board_flow_history_rebucket", SCRIPTS / "board_flow_history.py")
    closed = hub.COLUMN["issue_closed"]
    _history(board, [_sample(1, {"issue": {closed: 5}}), _sample(2, {"issue": {closed: 5}})])
    rows = [{**_card(n, st=closed, s="CLOSED"), "cr": "completed"} for n in range(1, 5)]
    rows.append({**_card(9, st=closed, s="CLOSED"), "cr": "not_planned"})

    flow.append(board / "hub-flow.x_y.jsonl", rows, now=datetime(2026, 10, 3, 12, tzinfo=UTC).timestamp())

    bands = {b["name"]: b for b in hub.board()["flow"]["cfd"]["bands"]}
    values = [hi - lo for hi, lo in zip(bands[closed]["upper"], bands[closed]["lower"], strict=True)]
    assert values == sorted(values), f"the Closed band steps down at the boundary: {values}"
    abandoned = [hi - lo for hi, lo in zip(bands["Abandoned"]["upper"], bands["Abandoned"]["lower"], strict=True)]
    assert abandoned == [1, 1, 1], "the past samples and the new one count the card in Abandoned"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_lead_time_chart_names_the_cards_closed_as_not_planned(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", block,
        'process.stdout.write(flowLead({measure:"opened -> closed",window:{size:20,unit:"issues"},'
        'excluded:[{reason:"not_planned",count:3}],excluded_total:3,series:[],summary:null}));',
    ])
    path = tmp_path / "flow-lead-not-planned.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "3 closed as not planned" in result.stdout


def test_opening_the_diagrams_makes_no_gh_call(hub, board, monkeypatch):
    board_cache = hub.board_cache
    board_cache["items"] = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z")]
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 1}})])
    calls = []

    def gh_fails(cmd, **kw):
        calls.append(cmd)
        raise FileNotFoundError("gh is not on PATH")

    monkeypatch.setattr(hub.subprocess, "run", gh_fails)
    flow = hub.board()["flow"]
    assert calls == []
    assert flow["cfd"]["bands"] and flow["lead"]["summary"] is not None
    assert 'id=flowov hidden' in hub.PAGE


def _skip(day):
    return {"flow_skip": 2, "t": int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp()), "reason": "items read incomplete"}


def test_a_quiet_day_with_a_whole_read_is_a_point(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 1}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert len(cfd["days"]) == 3, "the calendar runs over the quiet day, so points sit by date"
    for band in cfd["bands"]:
        assert band["upper"][1] is not None and band["lower"][1] is not None, "the quiet day has its own sample: a point"
    assert [b["upper"][1] for b in cfd["bands"] if b["name"] == hub.COLUMN["unscheduled"]] == [2]


def test_a_day_with_no_record_at_all_is_a_gap_and_never_a_carried_value(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 1}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert len(cfd["days"]) == 3
    for band in cfd["bands"]:
        assert band["upper"][1] is None and band["lower"][1] is None, "no record: the hub was down, its count is unknown"


def test_a_skipped_read_is_a_calendar_day_with_no_band_values(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _skip(2),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 1}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert len(cfd["days"]) == 3
    for band in cfd["bands"]:
        assert band["upper"][1] is None and band["lower"][1] is None, "a gap carries no value to interpolate"
        assert band["upper"][0] is not None and band["upper"][2] is not None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_one_point_run_is_drawn_as_a_visible_mark(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={};", "const esc=s=>String(s);", block,
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
        "const COLORS={};", "const esc=s=>String(s);", block,
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
    lt_mod = _load("board_leadtime_check", SCRIPTS / "board_leadtime.py")
    samples = [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 1, hub.COLUMN["issue_closed"]: 2}}),
    ]
    _history(board, samples)
    items = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-01T15:00:00Z"),
             _card(2, st="Todo", s="OPEN", c=None)]
    hub.board_cache["items"] = items

    flow = hub.board()["flow"]
    direct = cfd_mod.cfd(samples, hub.ORDER)
    for band in flow["cfd"]["bands"]:
        for i, point in enumerate(direct["points"]):
            assert band["upper"][i] - band["lower"][i] == point["bands"][band["name"]]

    direct_lead = lt_mod.lead_time([
        {"n": 1, "column": hub.COLUMN["issue_closed"], "c": "2026-10-01T15:00:00Z", "o": OPENED},
        {"n": 2, "column": "Todo", "c": None, "o": OPENED},
    ])
    assert flow["lead"]["summary"]["average"]["hours"] == direct_lead["summary"]["average"]["hours"]
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


def test_the_cfd_serves_numbered_y_ticks_and_dated_x_ticks(hub, board):
    """#896 (1): the Cumulative flow chart's y axis is numbered from zero to past the tallest stack, and its x axis
    names the dates of the history."""
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 5, hub.COLUMN["issue_closed"]: 3}}, frozen=2, none=1),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 4, hub.COLUMN["issue_closed"]: 4}}, frozen=2, none=1),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 3, hub.COLUMN["issue_closed"]: 5}}, frozen=2, none=1),
    ])
    cfd = hub.board()["flow"]["cfd"]
    ticks = cfd["yticks"]
    assert ticks[0] == 0 and all(isinstance(v, int) for v in ticks)
    assert ticks == sorted(set(ticks)), f"the y ticks rise: {ticks}"
    assert len(ticks) >= 3, f"a numbered y axis carries more than its foot and top: {ticks}"
    assert ticks[-1] == cfd["ymax"] >= 11, f"the axis reaches past the tallest stack of 11: {ticks}"
    assert cfd["xticks"] == [{"i": 0, "day": "2026-10-01"}, {"i": 1, "day": "2026-10-02"},
                             {"i": 2, "day": "2026-10-03"}]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cfd_draws_labelled_axes_with_numbered_ticks_and_dated_ticks(hub, board, tmp_path):
    """#896 (1): the page draws both axes, one labelled tick per number the hub served and one per date."""
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 5, hub.COLUMN["issue_closed"]: 3}}, frozen=2, none=1),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 4, hub.COLUMN["issue_closed"]: 4}}, frozen=2, none=1),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 3, hub.COLUMN["issue_closed"]: 5}}, frozen=2, none=1),
    ])
    cfd = hub.board()["flow"]["cfd"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", block,
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
    assert "cards" in labels and "day (UTC)" in labels, f"an axis carries no name: {labels}"
    ticks = re.findall(r"<line ", result.stdout)
    assert len(ticks) == 2 + len(cfd["yticks"]) + len(cfd["xticks"]), "one mark per tick between the two axes"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_band_drawn_has_a_legend_entry_frozen_and_no_column_included(hub, board, tmp_path):
    """#896 (2): the bands the chart draws and the legend's entries are one list, frozen and No column included."""
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 5, hub.COLUMN["issue_closed"]: 3}}, frozen=2, none=1),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 4, hub.COLUMN["issue_closed"]: 4}}, frozen=2, none=1),
    ])
    cfd = hub.board()["flow"]["cfd"]
    names = [b["name"] for b in cfd["bands"]]
    assert names == [*hub.ORDER, "frozen", "No column"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", block,
        f"process.stdout.write(flowCFD({json.dumps(cfd)}));",
    ])
    path = tmp_path / "flow-cfd-legend.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    legend = re.findall(r"<span class=flg><i[^>]*></i>([^<]*)</span>", result.stdout)
    assert legend == names, "every band drawn has exactly one legend entry, in the drawn order"
    assert len(re.findall(r"<polygon ", result.stdout)) == len(names), "the legend counts the bands the chart draws"
    bands = {b["name"]: b for b in cfd["bands"]}
    assert bands["frozen"]["upper"][0] - bands["frozen"]["lower"][0] == 2
    assert bands["No column"]["upper"][0] - bands["No column"]["lower"][0] == 1


# SPEC 889 #900: the lead-time diagram is the Jira-style control chart, computed by the hub (board_leadtime).
TIMEFRAME_KEYS = ["7", "14", "30", "90", "180", "all"]


def test_the_control_chart_is_served_for_every_timeframe_and_both_scales(hub, board):
    hub.board_cache["items"] = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z")]

    control = hub.board()["flow"]["lead"]["control"]

    assert set(control) == {"jira", "log"}
    for scale in control.values():
        assert list(scale) == TIMEFRAME_KEYS
    assert control["log"]["all"]["axis"] == "log"
    assert control["jira"]["all"]["axis"] == "linear"


def test_a_control_chart_dot_carries_the_issue_title_and_its_close_date(hub, board):
    hub.board_cache["items"] = [{**_card(7, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z"),
                                 "t": "ship the chart"}]

    dot = hub.board()["flow"]["lead"]["control"]["jira"]["all"]["dots"][0]

    assert dot["n"] == 7
    assert dot["title"] == "ship the chart"
    assert dot["closed"] == "2026-10-02"


def test_the_control_chart_names_its_lines_and_controls_the_jira_way(hub):
    for name in ("Rolling average (", "centered)", "Standard deviation", "Median", "p85", "Average",
                 "Past month", "Log scale", "Timeframe"):
        assert name in hub.PAGE, f"the page names no {name!r}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_control_chart_draws_one_mark_per_cluster_on_the_date_axis(hub, board, tmp_path):
    hub.board_cache["items"] = [
        _card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z"),
        _card(2, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z"),
        _card(3, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-09T09:00:00Z"),
    ]
    lead = hub.board()["flow"]["lead"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={issue_closed:'Closed'};", "const esc=s=>String(s);", block,
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
    _history(board, [_sample(d, {"issue": {hub.COLUMN["unscheduled"]: 1}}) for d in range(1, 21)])

    ranges = hub.board()["flow"]["cfd"]["ranges"]

    assert list(ranges) == CFD_RANGE_KEYS
    assert len(ranges["7"]["days"]) == 7
    assert ranges["7"]["days"][-1] == "2026-10-20", "the window ends on the last day of the history"
    assert len(ranges["30"]["days"]) == 20, "a window longer than the history is the whole history"
    assert ranges["all"]["days"] == hub.board()["flow"]["cfd"]["days"]


def test_each_range_serves_wip_and_throughput_per_day_from_board_cfd(hub, board):
    cfd_mod = _load("board_cfd_range_check", SCRIPTS / "board_cfd.py")
    samples = [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}}),
               _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 1, hub.COLUMN["issue_closed"]: 3}})]
    _history(board, samples)

    view = hub.board()["flow"]["cfd"]["ranges"]["all"]
    direct = cfd_mod.cfd(samples, hub.ORDER)

    assert view["wip"] == [p["wip"] for p in direct["points"]]
    assert view["throughput"] == [p["throughput"] for p in direct["points"]], "the first day has no throughput"


def test_each_band_serves_its_count_per_day(hub, board):
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}})])

    bands = {b["name"]: b for b in hub.board()["flow"]["cfd"]["ranges"]["all"]["bands"]}

    assert bands[hub.COLUMN["unscheduled"]]["count"] == [2]
    assert bands[hub.COLUMN["issue_closed"]]["count"] == [1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cfd_names_a_range_selector_and_a_day_hover_with_counts(hub, board, tmp_path):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 1, hub.COLUMN["issue_closed"]: 3}}),
    ])
    view = hub.board()["flow"]["cfd"]["ranges"]["all"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={};", "const esc=s=>String(s);", "let CF={r:'all'};", block,
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
    day_titles = [t for t in titles if t.startswith("2026-10-02")]
    assert day_titles, f"no hover names the day: {titles}"
    assert "WIP" in day_titles[0] and "throughput" in day_titles[0]
    assert f"{hub.COLUMN['issue_closed']} 3" in day_titles[0], "the hover names each band's count"
