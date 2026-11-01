"""The flow history keeps each card's kind, spec and wave, and the cumulative flow and the control chart filter by the bar's values (SPEC 889, #904).

The sample names a card's kind (issue or bug), spec and wave in `cells`. A sample written before that carries no `cells`: it answers no
spec and no wave, and the chart says so. The bar's values are the keys the board filter reads: a spec's number or `none`,
a Waves key `spec/wave` (#980), a kind `issue`, `bug` or `pr`, an empty list for all. The board reads every card and
the page filters it; the two chart routes take the bar's values in their query.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
FLOW_PY = SCRIPTS / "board_flow_history.py"
CFD_PY = SCRIPTS / "board_cfd.py"
OPENED = "2026-10-01T09:00:00Z"
COLUMNS = ["Todo", "Doing", "Closed"]


def _load(name, path):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _epoch(day):
    return int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp())


def _row(n, st="Todo", pa=None, labels=()):
    row = {"n": n, "t": f"card {n}", "s": "OPEN", "c": None, "st": st, "l": list(labels), "o": OPENED}
    if pa is not None:
        row["pa"] = pa
    return row


def _sample(day, cells):
    """A sample as board_flow_history writes it: the cells, and the counts, frozen and none they add up to."""
    counts, frozen, none = {}, 0, 0
    for kind, _spec, _wave, column, fz, n in cells:
        if column is None:
            none += n
        else:
            counts.setdefault(kind, {})[column] = counts.get(kind, {}).get(column, 0) + n
        if fz:
            frozen += n
    return {"t": _epoch(day), "counts": counts, "frozen": frozen, "none": none, "cells": [list(c) for c in cells]}


def _legacy(day, counts):
    """A sample written before the change: no cells."""
    return {"t": _epoch(day), "counts": counts, "frozen": 0, "none": 0}


@pytest.fixture(scope="module")
def flow():
    return _load("board_flow_history_filters", FLOW_PY)


@pytest.fixture(scope="module")
def cfd():
    return _load("board_cfd_filters", CFD_PY).cfd


# -- the sample format -------------------------------------------------------------------------------------------------

def test_a_sample_names_each_cards_kind_spec_and_wave(flow):
    s = flow.sample([_row(1, pa=5, labels=["wave:04", "bug"]), _row(2, labels=["loop-bug"]), _row(3)], now=_epoch(20))
    assert ["bug", "5", "4", "Todo", 0, 1] in s["cells"], "a bug-labelled card is the bar's kind bug"
    assert ["bug", "none", "none", "Todo", 0, 1] in s["cells"], "a loop bug is a bug too"
    assert ["issue", "none", "none", "Todo", 0, 1] in s["cells"]


def test_a_card_that_changes_spec_on_the_same_day_writes_a_new_sample(flow, tmp_path):
    path = tmp_path / "hub-flow.x_y.jsonl"
    flow.append(path, [_row(1, pa=5)], now=_epoch(20))
    flow.append(path, [_row(1, pa=6)], now=_epoch(20) + 60)
    last = flow.samples(path)[-1]
    assert ["issue", "6", "none", "Todo", 0, 1] in last["cells"], "equal counts with other cells is a new sample"


def test_a_card_with_no_spec_and_no_wave_is_none(flow):
    s = flow.sample([_row(1)], now=_epoch(20))
    assert ["issue", "none", "none", "Todo", 0, 1] in s["cells"]


def test_the_lowest_wave_label_names_the_wave_without_leading_zeros(flow):
    s = flow.sample([_row(1, labels=["wave:10", "wave:02"])], now=_epoch(20))
    assert ["issue", "none", "2", "Todo", 0, 1] in s["cells"]


def test_a_card_in_no_column_has_no_column_name_in_its_cell(flow):
    s = flow.sample([_row(1, st="Nowhere")], now=_epoch(20))
    assert ["issue", "none", "none", None, 0, 1] in s["cells"]


def test_the_cells_add_up_to_the_counts_the_flow_already_keeps(flow):
    rows = [_row(1, pa=5, labels=["wave:1"]), _row(2), _row(3, st="Nowhere")]
    s = flow.sample(rows, now=_epoch(20))
    in_columns = sum(c[5] for c in s["cells"] if c[3] is not None)
    assert in_columns == sum(n for by in s["counts"].values() for n in by.values())
    assert sum(c[5] for c in s["cells"] if c[3] is None) == s["none"]


def test_the_spec_and_wave_keys_are_the_values_the_board_filter_offers(flow):
    assert flow.spec_key({"pa": None}) == "none"
    assert flow.spec_key({"pa": 5}) == "5"
    assert flow.wave_key(["bug"]) == "none"
    assert flow.wave_key(["wave:04"]) == "4"
    assert flow.kind_key({"l": ["bug"]}) == "bug"
    assert flow.kind_key({"l": []}) == "issue"


# -- the cumulative flow's filter --------------------------------------------------------------------------------------

def test_a_spec_filter_keeps_only_that_specs_cards(cfd):
    samples = [_sample(20, [["issue", "5", "none", "Todo", 0, 2], ["issue", "6", "none", "Todo", 0, 3]])]
    assert cfd(samples, COLUMNS, spec=["5"])["points"][0]["bands"]["Todo"] == 2


def test_a_wave_filter_keeps_only_that_waves_cards(cfd):
    samples = [_sample(20, [["issue", "none", "4", "Todo", 0, 2], ["issue", "none", "5", "Todo", 0, 3],
                            ["issue", "7", "4", "Todo", 0, 8]])]
    assert cfd(samples, COLUMNS, wave=["none/4"])["points"][0]["bands"]["Todo"] == 2, "a Waves key is spec/wave (#980)"
    assert cfd(samples, COLUMNS, wave=["7/4"])["points"][0]["bands"]["Todo"] == 8


def test_a_kind_filter_takes_a_list_of_the_bars_kinds(cfd):
    samples = [_sample(20, [["issue", "none", "none", "Todo", 0, 2], ["bug", "none", "none", "Todo", 0, 3],
                            ["pr", "none", "none", "Todo", 0, 5]])]
    assert cfd(samples, COLUMNS, kind=["bug"])["points"][0]["bands"]["Todo"] == 3
    assert cfd(samples, COLUMNS, kind=["issue", "bug"])["points"][0]["bands"]["Todo"] == 5
    assert cfd(samples, COLUMNS, kind=[])["points"][0]["bands"]["Todo"] == 10


def test_spec_and_wave_filters_combine_with_and(cfd):
    samples = [_sample(20, [
        ["issue", "5", "4", "Todo", 0, 1],
        ["issue", "5", "none", "Todo", 0, 2],
        ["issue", "6", "4", "Todo", 0, 4],
        ["bug", "5", "4", "Todo", 0, 8],
    ])]
    assert cfd(samples, COLUMNS, spec=["5"], wave=["5/4"])["points"][0]["bands"]["Todo"] == 9
    assert cfd(samples, COLUMNS, spec=["5", "6"], wave=["5/4", "6/4"])["points"][0]["bands"]["Todo"] == 13
    assert cfd(samples, COLUMNS, spec=["5"], wave=["5/4"], kind=["bug"])["points"][0]["bands"]["Todo"] == 8


def test_an_empty_filter_list_keeps_every_card(cfd):
    samples = [_sample(20, [["issue", "5", "4", "Todo", 0, 1], ["issue", "none", "none", "Todo", 0, 6]])]
    assert cfd(samples, COLUMNS, spec=[], wave=[])["points"][0]["bands"]["Todo"] == 7


def test_a_filter_takes_the_frozen_and_no_column_cards_of_the_kept_cards(cfd):
    samples = [_sample(20, [
        ["issue", "5", "none", "Todo", 1, 2],
        ["issue", "6", "none", "Todo", 0, 1],
        ["issue", "5", "none", None, 0, 1],
    ])]
    bands = cfd(samples, COLUMNS, spec=["5"])["points"][0]["bands"]
    assert bands["Todo"] == 2
    assert bands["frozen"] == 2
    assert bands["No column"] == 1


def test_the_kind_filter_still_holds_with_cells(cfd):
    samples = [_sample(20, [["issue", "none", "none", "Todo", 0, 2], ["pr", "none", "none", "Todo", 0, 3]])]
    assert cfd(samples, COLUMNS, kind="pr")["points"][0]["bands"]["Todo"] == 3


def test_a_sample_without_cells_answers_no_spec_and_no_wave(cfd):
    legacy = [_legacy(20, {"card": {"Todo": 4}})]
    assert cfd(legacy, COLUMNS, spec=["5"])["points"][0]["bands"]["Todo"] == 0
    assert cfd(legacy, COLUMNS, wave=["none/none"])["points"][0]["bands"]["Todo"] == 4
    assert cfd(legacy, COLUMNS, wave=["5/none"])["points"][0]["bands"]["Todo"] == 0
    assert cfd(legacy, COLUMNS)["points"][0]["bands"]["Todo"] == 4


def test_a_day_read_from_a_sample_without_cells_is_flagged(cfd):
    samples = [_legacy(20, {"card": {"Todo": 4}}), _sample(21, [["issue", "5", "none", "Todo", 0, 1]])]
    points = cfd(samples, COLUMNS)["points"]
    assert points[0].get("legacy") is True
    assert not points[1].get("legacy")


def test_cells_and_counts_give_the_same_bands_when_nothing_is_filtered(cfd):
    with_cells = _sample(20, [["issue", "5", "4", "Todo", 1, 2], ["issue", "none", "none", "Doing", 0, 1],
                              ["issue", "none", "none", None, 0, 3]])
    without = {k: v for k, v in with_cells.items() if k != "cells"}
    assert cfd([with_cells], COLUMNS)["points"][0]["bands"] == cfd([without], COLUMNS)["points"][0]["bands"]


# -- the hub: the board read takes the bar's values, and the page carries them ---------------------------------------

@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_flow_filters")
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
        yield _load("hub_flow_filters", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub, "CHARTS", {})
    return tmp_path


def _item(hub_mod, n, st, pa=None, labels=(), closed_at=None):
    row = {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": "CLOSED" if closed_at else "OPEN", "c": closed_at,
           "st": st, "k": None, "l": list(labels), "si": {"total": None, "completed": None}, "o": OPENED}
    if pa is not None:
        row["pa"] = pa
    return row


def _write_history(hub_mod, items, day=20):
    flow_mod = hub_mod._flow
    flow_mod.append(flow_mod.history_path(hub_mod.BOARD, hub_mod.REPO), items,
                    now=datetime(2026, 10, day, 12, tzinfo=UTC).timestamp())


def _backlog_counts(cfd):
    return {b["name"]: b for b in cfd["cfd"]["bands"]}


def test_the_flow_history_keeps_each_cards_spec_and_wave(hub, board):
    items = [_item(hub, 1, hub.COLUMN["unscheduled"], pa=5, labels=["wave:4"])]
    _write_history(hub, items)
    line = hub._flow.history_path(hub.BOARD, hub.REPO).read_text().splitlines()[-1]
    assert ["issue", "5", "4", hub.COLUMN["unscheduled"], 0, 1] in json.loads(line)["cells"]


def test_the_chart_route_filters_the_cumulative_flow_by_the_bar_values(hub, board):
    backlog = hub.COLUMN["unscheduled"]
    items = [_item(hub, 1, backlog, pa=5, labels=["wave:4"]),
             _item(hub, 2, backlog, labels=["bug"]),
             _item(hub, 3, backlog, pa=6, labels=["wave:4", "bug"])]
    hub.board_cache["items"] = items
    _write_history(hub, items)

    def last(query):
        return _backlog_counts(hub.flow_cfd(*hub.board_filters(query)))[backlog]["count"][-1]

    assert last("") == 3
    assert last("fs=5") == 1
    assert last("fw=5/4,6/4") == 2, "a Waves key is spec/wave, as the bar sends it (#980)"
    assert last("fw=5/4&fs=5") == 1
    assert last("fw=none/none") == 1
    assert last("fk=bug") == 2
    assert last("fk=bug&fs=6") == 1
    assert last("fk=issue") == 1


def test_the_control_chart_takes_the_same_filters(hub, board):
    closed = hub.COLUMN["issue_closed"]
    items = [_item(hub, 1, closed, pa=5, labels=["wave:2"], closed_at="2026-10-02T15:00:00Z"),
             _item(hub, 2, closed, pa=6, labels=["bug"], closed_at="2026-10-03T15:00:00Z")]
    hub.board_cache["items"] = items

    def dots(query):
        return sorted(d["n"] for d in hub.flow_lead(*hub.board_filters(query))["lead"]["control"]["jira"]["all"]["dots"])

    assert dots("") == [1, 2]
    assert dots("fs=5") == [1]
    assert dots("fw=5/2") == [1]
    assert dots("fk=bug") == [2]
    assert dots("fk=pr") == [], "the charts count issues: a PR is no row of the flow history or the lead time"


def test_the_drag_zoom_takes_the_same_filters(hub, board):
    closed = hub.COLUMN["issue_closed"]
    hub.board_cache["items"] = [_item(hub, 1, closed, pa=5, closed_at="2026-10-02T15:00:00Z"),
                                _item(hub, 2, closed, labels=["bug"], closed_at="2026-10-03T15:00:00Z")]
    span = "from=2026-09-01T00:00:00&to=2026-11-01T00:00:00"
    assert sorted(d["n"] for d in hub.lead_zoom(span)["dots"]) == [1, 2]
    assert [d["n"] for d in hub.lead_zoom(span + "&fk=bug")["dots"]] == [2]
    assert [d["n"] for d in hub.lead_zoom(span + "&fs=5")["dots"]] == [1]


def test_the_bar_query_names_waves_specs_and_kinds(hub):
    assert hub.board_filters("fw=5/4,none/none&fs=5&fk=bug,pr") == (["5/4", "none/none"], ["5"], ["bug", "pr"])
    assert hub.board_filters("") == ([], [], [])


def test_the_board_route_takes_no_filter_it_does_not_apply(hub):
    """The board filters on the page (boardOk, buildBoard): the served board is every card, and its route reads no bar value."""
    import inspect
    assert not inspect.signature(hub.board).parameters
    served = next(line for line in HUB_PY.read_text().splitlines() if 'startswith("/api/board")' in line)
    nxt = HUB_PY.read_text().splitlines()[HUB_PY.read_text().splitlines().index(served) + 1]
    assert "board_filters" not in nxt


# -- the page: a filter change redraws both charts with the route's filtered answer ----------------------------------

def _page_line(hub, prefix):
    return next(line for line in hub.PAGE.splitlines() if line.startswith(prefix))


def _page_harness(hub, answers, drive):
    """The page's own bar and chart lines, run in node over stub elements. `fetch` answers from `answers`, keyed by URL,
    with the hub's own route payloads; the script `drive` acts on the page and prints JSON."""
    lines = [_page_line(hub, p) for p in ("const barQuery=", "const flowOpen=", "const ltQ=", "const ltZoom=", "const ltGet=",
                                          "const barMoved=", "const pickFilter=")]
    stubs = f"""
const ANSWERS={json.dumps(answers)},FETCHED=[],DRAWN=[],SAVED=[],POLLED=[];
const el=()=>({{hidden:true,handlers:{{}},addEventListener(t,f){{this.handlers[t]=f}}}});
const EL={{"#flowov":el(),"#fwl":el(),"#fsl":el(),"#fkl":el()}};
const $=s=>EL[s];
const fetch=async url=>{{FETCHED.push(url);if(!(url in ANSWERS))throw new Error("no answer for "+url);return {{json:async()=>ANSWERS[url]}}}};
let bo={{}},FLOWK=null,FLOWD={{}},LT={{tf:"30",log:false,pick:null,zc:null,zr:null,tok:0,a:"",b:""}};
const drawFlow=()=>DRAWN.push(JSON.parse(JSON.stringify(FLOWD[FLOWK]||null)));
const saveB=()=>SAVED.push(JSON.stringify(bo)),pollBoard=()=>POLLED.push(1);
const settle=()=>new Promise(r=>setTimeout(r,0));
"""
    script = stubs + "\n".join(lines) + "\n(async()=>{" + drive + "})().catch(e=>{console.error(e.stack);process.exit(1)});"
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _bar(fw="", fs="", fk=""):
    return f"fw={fw}&fs={fs}&fk={fk}"


def test_a_kind_pick_redraws_the_open_cumulative_flow_with_the_filtered_route(hub, board):
    backlog = hub.COLUMN["unscheduled"]
    items = [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"]), _item(hub, 3, backlog, labels=["bug"])]
    hub.board_cache["items"] = items
    _write_history(hub, items)
    answers = {f"/api/chart/cfd?{q}": hub.flow_cfd(*hub.board_filters(q)) for q in (_bar(), _bar(fk="bug"))}
    out = _page_harness(hub, answers, """
pickFilter("fkl","fk");
await flowOpen("cfd");await settle();
EL["#fkl"].handlers.change({target:{value:"bug",checked:true}});await settle();await settle();
console.log(JSON.stringify({fetched:FETCHED,bo,last:DRAWN[DRAWN.length-1],saved:SAVED.length,polled:POLLED.length}));""")
    assert out["fetched"] == [f"/api/chart/cfd?{_bar()}", f"/api/chart/cfd?{_bar(fk='bug')}"]
    assert out["bo"]["fk"] == ["bug"] and out["saved"] == 1 and out["polled"] == 1, "the pick is stored as the board's own"
    count = {b["name"]: b for b in out["last"]["cfd"]["bands"]}[backlog]["count"][-1]
    assert count == 2, "the open chart is redrawn from the filtered route"


def test_a_spec_clear_redraws_the_open_control_chart_and_its_chosen_span(hub, board):
    closed = hub.COLUMN["issue_closed"]
    hub.board_cache["items"] = [_item(hub, 1, closed, pa=5, closed_at="2026-10-02T15:00:00Z"),
                                _item(hub, 2, closed, closed_at="2026-10-03T15:00:00Z")]
    q5, qall = _bar(fs="5"), _bar()
    span = "&scale=jira&a=Todo&b="
    answers = {f"/api/chart/lead?{q5}": hub.flow_lead(*hub.board_filters(q5)),
               f"/api/chart/lead?{qall}": hub.flow_lead(*hub.board_filters(qall)),
               f"/api/leadtime?tf=30{span}&{qall}": {"dots": [{"n": 1}, {"n": 2}]}}
    out = _page_harness(hub, answers, """
bo.fs=["5"];pickFilter("fsl","fs");LT.a="Todo";
await flowOpen("lead");await settle();
EL["#fsl"].handlers.click({target:{closest:s=>s==="[data-clear]"},preventDefault(){}});await settle();await settle();
console.log(JSON.stringify({fetched:FETCHED,fs:bo.fs,zc:LT.zc,lead:FLOWD.lead}));""")
    assert out["fs"] == []
    assert f"/api/chart/lead?{qall}" in out["fetched"], "the clear reads the control chart anew"
    assert f"/api/leadtime?tf=30{span}&{qall}" in out["fetched"], "a chosen span is read anew with the bar's values"
    assert sorted(d["n"] for d in out["lead"]["lead"]["control"]["jira"]["all"]["dots"]) == [1, 2]
    assert out["zc"] == {"dots": [{"n": 1}, {"n": 2}]}


def test_an_answer_for_an_old_bar_value_is_never_drawn(hub, board):
    backlog = hub.COLUMN["unscheduled"]
    items = [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])]
    hub.board_cache["items"] = items
    _write_history(hub, items)
    answers = {f"/api/chart/cfd?{q}": hub.flow_cfd(*hub.board_filters(q)) for q in (_bar(), _bar(fk="bug"))}
    out = _page_harness(hub, answers, """
pickFilter("fkl","fk");
const first=flowOpen("cfd");
EL["#fkl"].handlers.change({target:{value:"bug",checked:true}});
await first;await settle();await settle();
console.log(JSON.stringify({last:DRAWN[DRAWN.length-1]}));""")
    count = {b["name"]: b for b in out["last"]["cfd"]["bands"]}[backlog]["count"][-1]
    assert count == 1, "the answer read for all kinds landed after the pick and was drawn"


def test_the_bar_has_a_kinds_control_beside_waves_and_specs(hub):
    assert "<details id=fk class=fsel><summary>Kinds: all</summary><div class=fpop id=fkl></div></details>" in hub.PAGE
    assert 'pickFilter("fkl","fk")' in hub.PAGE
    assert 'fetch("/api/board")' in hub.PAGE, "the board read carries no bar value: the page filters the board"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_board_keeps_a_card_by_its_kind(hub):
    lanes = (SCRIPTS / "board-lanes.js").read_text()
    script = lanes + """
const rows=[{n:1,l:[]},{n:2,l:["bug"]},{n:3,l:["loop-bug"]},{n:4,kind:"pr",l:[]}];
const groups=[{name:"Todo",items:rows.slice(0,3)}];
console.log(JSON.stringify({kinds:rows.map(kindKey),bug:rows.filter(x=>boardOk(x,[],[],["bug"])).map(x=>x.n),
 lanes:buildBoard(groups,{mode:"spec",fw:[],fs:[],fk:["issue"],closed:"Closed"}).lanes.flatMap(l=>l.vis.map(x=>x.n))}));"""
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["kinds"] == ["issue", "bug", "bug", "pr"]
    assert out["bug"] == [2, 3]
    assert out["lanes"] == [1]


def _flow_block(hub):
    m = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", hub.PAGE, re.S)
    assert m, "the page carries no flow-charts block"
    return m.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_says_which_days_answer_no_spec_and_no_wave(hub, board, tmp_path):
    history = hub._flow.history_path(hub.BOARD, hub.REPO)
    history.write_text(json.dumps({"t": _epoch(20), "counts": {"issue": {hub.COLUMN["unscheduled"]: 2}},
                                   "frozen": 0, "none": 0}) + "\n")
    cfd = hub.flow_cfd()["cfd"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", block,
                         f"process.stdout.write(flowCFD({json.dumps(cfd)}));"])
    path = tmp_path / "flow-cfd-legacy.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "answer no spec and no wave" in result.stdout
