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
    tabs = hub.PAGE[hub.PAGE.index("<div class=rkt id=flowtabs>"):hub.PAGE.index("<div id=flowchips>")]
    # Re-anchored by the owner (2026-11-02): the burn-down tab is gone, so the charts are the two the owner keeps; Runs is a top tab (#1272).
    assert [re.sub(r"<[^>]*>", "", x) for x in re.findall(r"<button[^>]*>.*?</button>", tabs)] == ["Cumulative Flow Diagram", "Lead Time"]
    assert "id=flowv" not in hub.PAGE, "the View selector is gone: a chart is a tab"
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


def test_the_lead_chart_shares_its_plot_frame_with_hub_clustering_and_page(hub, board):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})

    payload = hub.flow_lead()
    lead = payload["lead"]
    chart = lead["control"]["jira"]["all"]

    assert payload["plot_frame"] == {"width": 672, "height": 212}
    assert chart["plot_frame"] == payload["plot_frame"]
    assert "flowW-2*ltP" not in _flow_block(hub), "the page selects its own plot frame"
    assert "flowH-2*ltP" not in _flow_block(hub), "the page selects its own plot frame"
    assert "clusters=" not in _flow_block(hub), "the page computes clusters"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_lead_page_draws_the_plot_frame_supplied_by_the_hub(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["jira"]["all"]
    chart["plot_frame"] = {"width": 700, "height": 240}

    out = _lt_chart(hub, tmp_path, "lt-supplied-frame.js", chart)

    assert 'viewBox="0 0 828 368"' in out
    assert '<line x1=64 y1=304 x2=764 y2=304' in out


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
    # Re-anchored by #1145: the rolling window is stated once, in the head line, so the line names no window size.
    for name in ("Rolling mean", "Standard deviation", "Past month", "Log scale", "Timeframe"):
        assert name in hub.PAGE, f"the page names no {name!r}"


def test_the_legend_carries_short_names_and_values_never_prose(hub):
    """Re-anchored by #1304: the legend keeps short names and values; explanations move out of the reading path."""
    for prose in ("85% of closed cards finished within this time", "one slow card lifts it",
                  "half the cards that arrived finish within this time", "the usual spread",
                  "the proof's time inside this window"):
        assert prose not in hub.PAGE, f"the legend still carries the prose sentence {prose!r}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_legend_has_no_prose_sentences_and_reference_lines_carry_end_labels(hub, board, tmp_path):
    """#1304: the chart answers "how long do bugs take" without a prose legend; each reference line labels itself at its
    end; the standard-deviation band hides by default; a cluster's count sits clear of its ring with a leader line."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in range(1, 13)})
    chart = hub.flow_lead()["lead"]["control"]["jira"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-readable.js", chart)
    for legend in re.findall(r'<p class="flg ltlg">(.*?)</p>', out):
        assert "<em>" not in legend, "the legend still carries a prose sentence"
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    for label in ("Average", "Median", "p85"):
        assert re.search(rf'<text [^>]*text-anchor=end[^>]*>{label} [^<]+</text>', plot), \
            f"the {label} reference line carries no end label"
    assert "<polygon" not in plot, "the standard-deviation band draws by default"
    circle = re.search(r"<circle [^>]*>", plot).group(0)
    count = re.search(r"<text ([^>]*)>12</text>", plot)
    assert count, "the cluster ring draws no count"
    assert float(_svg_attr(count.group(1), "y")) < float(_svg_attr(circle, "cy")) - float(_svg_attr(circle, "r")), \
        "the count sits inside the ring instead of clear above it"
    leader = next((ln for ln in re.findall(r"<line [^>]*>", plot)
                   if _svg_attr(ln, "x1") == _svg_attr(circle, "cx") and
                   float(_svg_attr(ln, "y1")) == pytest.approx(float(_svg_attr(circle, "cy"))
                                                               - float(_svg_attr(circle, "r")))), None)
    assert leader, "the count has no leader line back to its ring"


def _lt_chart(hub, tmp_path, name, chart, zr=None, coverage=None):
    """The lead chart's markup for one control chart; `zr` is the drag zoom the page holds, or none."""
    zoom = f"LT.zr={json.dumps(zr)}" if zr else "LT.zr=null"
    return _render(hub, tmp_path, name, f"({zoom},ltChart({json.dumps(chart)},{json.dumps(coverage)}))")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_first_screen_carries_two_controls_and_the_rest_sit_behind_more(hub, board, tmp_path):
    """#1145: Timeframe and Type are the only controls on the first screen. Measurement and Display, and the coverage
    line, the plotted-cards note and the ordering sentence, sit behind one More disclosure.

    Re-anchored by line (#1147): the zoom control is a chip drawn only while a zoom is active, so it is absent from the
    default screen and the View group appears after Display only in a zoomed view.
    """
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    lead = _flow(hub)["lead"]
    out = _lt_chart(hub, tmp_path, "lt-two-controls.js", lead["control"]["jira"]["all"], coverage=lead["coverage"])
    first, sep, rest = out.partition("<details class=ltmore>")
    assert sep, "the first screen carries no More disclosure"
    more = rest[:rest.index("</details>")]
    assert "id=ltr" in first and "id=lty" in first, "Timeframe and Type are not on the first screen"
    for later in ("id=lta", "id=ltb", "id=lts"):
        assert later not in first, f"the first screen still carries {later}"
        assert later in more, f"More does not hold {later}"
    assert "id=ltz" not in out, "the zoom chip is drawn only while a zoom is active"
    order = ["Measurement", "id=lta", "id=ltb", "Display", "id=lts"]
    marks = [more.index(mark) for mark in order]
    assert marks == sorted(marks), f"the disclosure does not keep the shipped order: {order}"
    zoomed = _lt_chart(hub, tmp_path, "lt-two-controls-zoom.js", lead["control"]["jira"]["all"],
                       zr=["2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00"], coverage=lead["coverage"])
    zfirst, _, zrest = zoomed.partition("<details class=ltmore>")
    assert "id=ltz" not in zfirst, "the zoom chip is never on the first screen"
    zmore = zrest[:zrest.index("</details>")]
    order = ["Display", "id=lts", "View", "id=ltz"]
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
    p85 = next(e for e in re.findall(r"<span>(.*?)</span>", "".join(re.findall(r'<p class="flg ltlg">(.*?)</p>', out))) if "p85" in e)
    assert re.search(r'title="1 closed as not planned: #2"[^>]*>excluded 1<', p85), "the total and its named cards sit beside p85"
    assert "window 20 issues" in head
    assert out.count("20 issues") == 1, "the rolling window is stated twice"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_zoom_chip_names_the_range_and_is_drawn_only_while_zoomed(hub, board, tmp_path):
    """#1147: one chip, absent on the default screen; while zoomed it names the view it returns to and clears the zoom.

    Re-anchored by line: the old guard asserted a disabled button on the default screen; the chip now replaces it, and
    the "Back to ..." wording lives in the chip.
    """
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["jira"]["all"]
    home = _lt_chart(hub, tmp_path, "lt-zoom-home.js", chart)
    zoomed = _lt_chart(hub, tmp_path, "lt-zoom-held.js", chart, zr=["2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00"])
    assert "id=ltz" not in home, "the zoom control is absent on the default screen"
    assert "disabled" not in home, "no disabled button sits on the default screen"
    assert re.search(r"<span class=fchip id=ltz>Back to All <button id=ltzc[^>]*>Clear</button></span>", zoomed), \
        "the chip names the view it returns to and carries its clear action"
    assert len(re.findall(r"id=ltz\b", zoomed)) == 1, "the zoom is one chip, never two controls"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_clustered_view_offers_a_fit_button_that_names_its_range(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z")})
    out = _lt_chart(hub, tmp_path, "lt-fit.js", _flow(hub)["lead"]["control"]["jira"]["all"])
    assert "Fit to data: Oct 2 09:00 to Oct 2 10:00 UTC" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_fit_button_appears_only_when_there_is_a_span_to_fit(hub, board, tmp_path):
    """#1147: Fit sits beside the zoom control only when the cards have a span to fit."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-29T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["jira"]["all"]
    assert chart["fit"] is None, "the spread cards fill the whole view: there is no span to fit"
    out = _lt_chart(hub, tmp_path, "lt-no-fit.js", chart)
    assert "id=ltfit" not in out, "Fit is drawn with no span to fit"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_one_exclusion_total_is_shown_and_the_plotted_sample_is_stated(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _todo(2), 3: _todo(3)})
    lead = hub.flow_lead(ty="task")["lead"]
    chart = lead["control"]["jira"]["all"]
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
    span = hub.lead_zoom("tf=all&scale=jira&a=Doing")
    assert span["excluded_total"] == 1
    out = _render(hub, tmp_path, "lt-span-exclusion.js", f"(LT.zc={json.dumps(span)},flowLead({json.dumps(lead)}))")
    assert out.count("excluded") == 1, "the page shows the exclusion more than once"
    assert "excluded 1" in out, "the one total is the base lead, not the chosen span"
    # Re-anchored (#1248): card 2 never entered Doing, so it is named as a missing start, with its number.
    assert 'title="1 never entered the start column: #2"' in out, "the reasons do not sit in the total's hover"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_legend_splits_series_from_summary_and_the_band_stays_hidden_until_asked(hub, board, tmp_path):
    """#1304: the series legend names only the drawn marks; statistics sit in one summary row; the standard-deviation
    band hides by default and appears with its legend entry only when the display asks for it."""
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-03T09:00:00Z")})
    chart = _flow(hub)["lead"]["control"]["jira"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-legend.js", chart)
    series, summary = re.findall(r'<p class="flg ltlg">(.*?)</p>', out)
    entries = re.findall(r"<span>(.*?)</span>", series)
    assert [re.sub(r"<[^>]+>", "", e) for e in entries] == ["Rolling mean"], entries
    assert "Standard deviation" not in series, "the band names itself while it hides"
    assert all(entry.count("<i") == 1 for entry in entries), "an entry has no marker or two"
    for label in ("Average", "Median", "p85"):
        assert label in re.sub(r"<[^>]+>", "", summary), f"the summary row names no {label}"
    assert "Gate time" not in series + summary, "gate time stays a text metric, not a legend entry"
    on = _render(hub, tmp_path, "lt-legend-band.js", f"(LT.band=true,ltChart({json.dumps(chart)},null))")
    assert "<polygon" in on, "the band does not draw when the display asks for it"
    series_on = re.findall(r'<p class="flg ltlg">(.*?)</p>', on)[0]
    band = next((e for e in re.findall(r"<span>(.*?)</span>", series_on) if "Standard deviation" in e), None)
    assert band and band.count("<i") == 1, "the band's label is split from its marker"


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
    m = re.search(rf'(?:^|\s){re.escape(name)}=(?:"([^"]*)"|\'([^\']*)\'|(\S+))', tag)
    return next((v for v in m.groups() if v is not None), None) if m else None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_each_reference_line_labels_itself_at_its_end_and_the_summary_row_carries_each_value(hub, board, tmp_path):
    """Re-anchored by #1304: a reference line carries its name and value at its own right end, and the summary row
    repeats each value once; the value never sits over the dots."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in range(1, 4)})
    chart = hub.flow_lead()["lead"]["control"]["jira"]["all"]
    out = _lt_chart(hub, tmp_path, "lt-no-stat-in-plot.js", chart)
    plot = re.search(r"<svg [^>]*data-lt[^>]*>(.*?)</svg>", out, re.S).group(1)
    for label, key in (("Average", "average"), ("Median", "median"), ("p85", "p85")):
        text = next((body for _, body in _svg_texts(plot) if body.startswith(label + " ")), None)
        assert text == f"{label} {chart[key]['text']}", f"the {label} reference line carries no end label of its own"
    summary = re.findall(r'<p class="flg ltlg">(.*?)</p>', out)[1]
    for key in ("average", "median", "p85"):
        assert f"<b>{chart[key]['text']}</b>" in summary, f"the summary row does not carry {key}'s value"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_each_mark_takes_its_own_role_and_the_legend_states_it(hub, board, tmp_path):
    """Re-anchored by #1304: the band hides by default, so the role check draws it by asking for it."""
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in (1, 2)})
    chart = hub.flow_lead()["lead"]["control"]["jira"]["all"]
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
    swatches = re.findall(r'<i style="background:([^;"]+)', series)
    assert [_resolved(v, tokens) for v in swatches] == [
        _resolved("var(--chart-bar)", tokens), _resolved("var(--chart-series-3)", tokens)], \
        "the series legend paint differs from its mark"
    swatches = re.findall(r'<i style="background:([^;"]+)', summary)
    assert [_resolved(v, tokens) for v in swatches] == [
        _resolved("var(--chart-series-8)", tokens), _resolved("var(--chart-ok)", tokens),
        _resolved("var(--chart-series-5)", tokens)], "the summary row paint differs from its line"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cluster_ring_holds_its_count_and_draws_it_clear_above_with_a_leader_line(hub, board, tmp_path):
    """#1304: the count never hides inside an overlapping ring: it sits above the ring with a leader line."""
    lt = _load("board_leadtime_ring", SCRIPTS / "board_leadtime.py")
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in range(1, 13)})
    chart = hub.flow_lead()["lead"]["control"]["jira"]["all"]
    assert len(chart["clusters"]) == 1 and len(chart["clusters"][0]["dots"]) == 12
    out = _lt_chart(hub, tmp_path, "lt-ring.js", chart)
    circle = re.search(r"<circle [^>]*>", out).group(0)
    radius = float(_svg_attr(circle, "r"))
    fs = float(re.search(r"--fs-body:(\d+)px", STYLESHEET.read_text()).group(1))
    assert fs == lt.RING_TEXT_PX, "the ring geometry and the page body size have drifted apart"
    box_w, box_h = len("12") * lt.AVG_GLYPH_EM * fs, lt.LINE_EM * fs
    assert 2 * radius >= (box_w ** 2 + box_h ** 2) ** 0.5, "the ring does not hold the count's box"
    count = re.search(r"<text ([^>]*)>12</text>", out)
    assert count, "the cluster ring draws no count"
    attrs = count.group(1)
    assert "font-size" not in attrs, "the count sets a size of its own instead of inheriting the box"
    top = float(_svg_attr(circle, "cy")) - radius
    assert float(_svg_attr(attrs, "y")) < top, "the count does not sit clear above the ring"
    leader = next((ln for ln in re.findall(r"<line [^>]*>", out)
                   if _svg_attr(ln, "x1") == _svg_attr(circle, "cx") and float(_svg_attr(ln, "y1")) == pytest.approx(top)
                   and _svg_attr(ln, "x2") == _svg_attr(circle, "cx")), None)
    assert leader, "the count has no leader line back to its ring"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cluster_table_takes_the_page_table_rule(hub, board, tmp_path):
    _seed(hub, {n: _done(n, "2026-10-02T09:00:00Z") for n in (1, 2)})
    chart = hub.flow_lead()["lead"]["control"]["jira"]["all"]
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
    out = _lt_chart(hub, tmp_path, "lt-body-size.js", _flow(hub)["lead"]["control"]["jira"]["all"])
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
        "const COLORS={},COLNAME={issue_closed:'Closed'};", "const esc=s=>String(s);", "const ls={};", block,
        'LT.tf="30";LT.log=false;',   # re-anchored (#1247): the visit's choice, the month on the Jira scale, set by hand
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


def _drawn_lead_svg(hub, board, tmp_path):
    _seed(hub, {1: _done(1, "2026-10-02T09:00:00Z"), 2: _done(2, "2026-10-09T09:00:00Z")})
    lead = hub.flow_lead()["lead"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={},COLNAME={issue_closed:'Closed'};", "const esc=s=>String(s);", "const ls={};", block,
        'LT.tf="30";LT.log=false;',   # re-anchored (#1247): the visit's choice, the month on the Jira scale, set by hand
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
    titles = [(attrs, body) for attrs, body in _svg_texts(svg) if "elapsed time" in body]
    assert len(titles) == 1
    attrs, body = titles[0]
    assert body == "elapsed time (linear)", "the full axis title is drawn"
    assert _svg_attr(attrs, "x") == left, "the title starts at the plot's left edge"
    assert _svg_attr(attrs, "text-anchor") == "start"
    assert 0 < float(_svg_attr(attrs, "y")) < float(re.search(r'viewBox="0 0 \S+ (\S+)"', svg).group(1))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_moving_the_axis_title_leaves_the_other_chart_text_in_place(hub, board, tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 10, tzinfo=tz)
    monkeypatch.setattr(hub, "dt", types.SimpleNamespace(**{**vars(hub.dt), "datetime": Frozen}))
    svg = _drawn_lead_svg(hub, board, tmp_path)
    others = [(attrs, body) for attrs, body in _svg_texts(svg) if "elapsed time" not in body]
    tick_labels = [(attrs, body) for attrs, body in others if _svg_attr(attrs, "x") == "57"]
    assert len(tick_labels) == 6, "the six y ticks keep their place beside the axis"
    assert all(_svg_attr(attrs, "text-anchor") == "end" for attrs, _ in tick_labels)
    x_labels = [(attrs, body) for attrs, body in others if _svg_attr(attrs, "text-anchor") == "middle"]
    assert len(x_labels) == 7, "the seven x ticks keep their place under the axis"
    base = float(re.search(r"<line x1=\S+ y1=(\S+) x2=\S+ y2=\1 stroke=var\(--chart-grid\)>", svg).group(1))
    assert {float(_svg_attr(attrs, "y")) for attrs, _ in x_labels} == {base + 17}
    summary = [(attrs, body) for attrs, body in _svg_texts(svg) if body.startswith(("Average", "Median", "p85"))]
    assert len(summary) == 3, "each reference line labels itself at its end (#1304)"
    assert all(_svg_attr(attrs, "text-anchor") == "end" for attrs, _ in summary), "an end label does not sit at the line's end"


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
    assert titles and f"WIP {view['reads'][1]['wip']}" in titles[0], "the day hover reads the hub's record for that day"
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
    lead = _lt_chart(hub, tmp_path, "lt-ink.js", _flow(hub)["lead"]["control"]["jira"]["all"])
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
    for name, svg, title in (("flow", cfd, "cards"), ("lead", lead, "elapsed time")):
        texts = re.findall(r"<text ([^>]*)>([^<]*)</text>", svg)
        assert texts, name
        assert all("font-size" not in attrs for attrs, _ in texts), f"{name} text sets its own size"
        axis = [(a, b) for a, b in texts if not re.fullmatch(r"\d+", b)
                and not b.startswith(("Average ", "Median ", "p85 "))]   # #1304: end labels and counts are annotations
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
    # Re-anchored (#1247): the page no longer restores a stored choice, so the harness sets the visit's choice itself: the
    # All timeframe on the Jira scale, the view these drawings were written against.
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};",
                         block or _flow_block(hub).split("const drawFlow")[0], 'LT.tf="all";LT.log=false;',
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

    chart = lead["control"]["jira"]["all"]
    assert sum(band["hours"] for dot in chart["dots"] for band in dot["stack"] if band["column"] == "Gating") == \
        pytest.approx(chart["gate"]["hours"]), "gate time is not the Gating band's total"
    assert chart["gate"]["hours"] == pytest.approx(18.0)
    assert chart["gate"]["text"] == "18h"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_draws_the_stack_and_gate_time_in_the_boards_palette_roles(hub, tmp_path):
    gated = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Todo"},
             {"t": "2026-10-01T15:00:00Z", "type": "status", "status": "Gating"},
             {"t": "2026-10-02T09:00:00Z", "type": "status", "status": "Closed"},
             {"t": "2026-10-02T09:00:00Z", "type": "closed"}]
    board_path = hub._hist.history_path(hub.BOARD, hub.REPO)
    item = _card(1, st="Closed", s="CLOSED", c="2026-10-02T09:00:00Z")
    hub._hist.record_issue(board_path, 1, OPENED, OPENED, gated, True, now=time.time(), keys=hub.bar_keys(item))
    hub.board_cache["items"] = [item]
    chart = hub.flow_lead(ty=None)["lead"]["control"]["jira"]["all"]

    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        f"const COLORS={hub.BOARD_COLORS_JSON},COLNAME={hub.BOARD_NAMES_JSON};", "const esc=s=>String(s);",
        "const ls={};", block, 'LT.tf="all";LT.log=false;',
        f"process.stdout.write(flowLead({json.dumps(hub.flow_lead(ty=None)['lead'])}));",
    ])
    path = tmp_path / "lt-stack-roles.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    out = result.stdout

    roles = json.loads(hub.BOARD_COLORS_JSON)
    bands = re.findall(r"<rect [^>]*>.*?</rect>", out)
    gating = next(band for band in bands if "Gating" in band)
    todo = next(band for band in bands if "Todo" in band)
    assert _paint(gating, "fill") == roles["Gating"], "the Gating band does not take the column's palette role"
    assert _paint(todo, "fill") == roles["Todo"], "the Todo band does not take the column's palette role"
    assert all(re.fullmatch(r"var\(--chart-[\w-]+\)", _paint(band, "fill") or "") for band in bands), \
        "a band paints a colour outside the chart roles"
    assert "Gate time" not in "".join(re.findall(r'<p class="flg ltlg">(.*?)</p>', out)), \
        "gate time is not a legend entry: zero has no counterpart on a log axis (#1304)"
    note = re.search(r'<p class="fnote ltkm">(.*?)</p>', out).group(1)
    assert f"Gate time {chart['gate']['text']}" in note, "gate time is not a text metric beside the survival reading"
