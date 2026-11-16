"""The flow journal keeps each card's kind, spec and wave, and the cumulative flow and the control chart filter by the bar's values (SPEC 889, #904, #1024).

The journal's sample names a card's kind (issue or bug), spec and wave in `cells`. The bar's values are the keys the board
filter reads: a spec's number or `none`, a Waves key `spec/wave` (#980), a kind `issue`, `bug` or `pr`, a type `task`,
`bug` or `spec`, an empty list for all. The board reads every card and the page filters it; the two chart routes take the
bar's values in their query and answer from the history store's column intervals.
"""
import importlib.util
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
FLOW_PY = SCRIPTS / "board_flow_history.py"
OPENED = "2026-10-01T09:00:00Z"


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


@pytest.fixture(scope="module")
def flow():
    return _load("board_flow_history_filters", FLOW_PY)


# -- the sample format -------------------------------------------------------------------------------------------------

def test_a_sample_names_each_cards_kind_spec_and_wave(flow):
    s = flow.sample([_row(1, pa=5, labels=["wave:04", "bug"]), _row(2, labels=["loop-bug"]), _row(3)], now=_epoch(20))
    assert ["bug", "5", "4", "Todo", 0, 1, "bug"] in s["cells"], "a bug-labelled card is the bar's kind bug"
    assert ["bug", "none", "none", "Todo", 0, 1, "bug"] in s["cells"], "a loop bug is a bug too"
    assert ["issue", "none", "none", "Todo", 0, 1, "task"] in s["cells"]


def test_a_card_that_changes_spec_on_the_same_day_writes_a_new_sample(flow, tmp_path):
    path = tmp_path / "hub-flow.x_y.jsonl"
    flow.append(path, [_row(1, pa=5)], now=_epoch(20))
    flow.append(path, [_row(1, pa=6)], now=_epoch(20) + 60)
    last = flow.samples(path)[-1]
    assert ["issue", "6", "none", "Todo", 0, 1, "task"] in last["cells"], "equal counts with other cells is a new sample"


def test_a_card_with_no_spec_and_no_wave_is_none(flow):
    s = flow.sample([_row(1)], now=_epoch(20))
    assert ["issue", "none", "none", "Todo", 0, 1, "task"] in s["cells"]


def test_the_lowest_wave_label_names_the_wave_without_leading_zeros(flow):
    s = flow.sample([_row(1, labels=["wave:10", "wave:02"])], now=_epoch(20))
    assert ["issue", "none", "2", "Todo", 0, 1, "task"] in s["cells"]


def test_a_card_in_no_column_has_no_column_name_in_its_cell(flow):
    s = flow.sample([_row(1, st="Nowhere")], now=_epoch(20))
    assert ["issue", "none", "none", None, 0, 1, "task"] in s["cells"]


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


def _write_history(hub_mod, items, day=20):
    flow_mod = hub_mod._flow
    flow_mod.append(flow_mod.history_path(hub_mod.BOARD, hub_mod.REPO), items,
                    now=datetime(2026, 10, day, 12, tzinfo=UTC).timestamp())


def test_the_flow_history_keeps_each_cards_spec_and_wave(hub, board):
    items = [_item(hub, 1, hub.COLUMN["unscheduled"], pa=5, labels=["wave:4"])]
    _write_history(hub, items)
    line = hub._flow.history_path(hub.BOARD, hub.REPO).read_text().splitlines()[-1]
    assert ["issue", "5", "4", hub.COLUMN["unscheduled"], 0, 1, "task"] in json.loads(line)["cells"]


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


def _item(hub_mod, n, st, pa=None, labels=(), closed_at=None, k=None):
    row = {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": "CLOSED" if closed_at else "OPEN", "c": closed_at,
           "st": st, "k": k, "l": list(labels), "si": {"total": None, "completed": None}, "o": OPENED}
    if pa is not None:
        row["pa"] = pa
    return row


def _seed(hub_mod, items):
    """The board's items, and the history store's record of each: Todo from its opening, Closed at its close (#1024)."""
    hub_mod.board_cache["items"] = items
    path = hub_mod._hist.history_path(hub_mod.BOARD, hub_mod.REPO)
    for i in items:
        events = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Todo"}]
        if i["c"]:
            events += [{"t": i["c"], "type": "status", "status": "Closed"}, {"t": i["c"], "type": "closed"}]
        hub_mod._hist.record_issue(path, i["n"], OPENED, OPENED, events, True, now=time.time(), keys=hub_mod.bar_keys(i))


def _last_counts(payload):
    """Each band's count on the last day; a filter that keeps no card has no day, so its count is 0."""
    return {b["name"]: (b["count"][-1] if b["count"] else 0) for b in payload["cfd"]["bands"]}


# -- the cumulative flow's filter --------------------------------------------------------------------------------------

def test_the_chart_route_filters_the_cumulative_flow_by_the_bar_values(hub, board):
    backlog = hub.COLUMN["issue_todo"]
    items = [_item(hub, 1, backlog, pa=5, labels=["wave:4"]),
             _item(hub, 2, backlog, labels=["bug"]),
             _item(hub, 3, backlog, pa=6, labels=["wave:4", "bug"])]
    _seed(hub, items)

    def last(query):
        return _last_counts(hub.flow_cfd(*hub.cfd_filters(query)))[backlog]

    assert last("ty=task") == 1, "the default type is tasks: only the first card"
    assert last("ty=bug") == 2
    assert last("ty=task&fs=5") == 1
    assert last("ty=bug&fs=6") == 1
    assert last("ty=bug&fw=6/4") == 1, "a Waves key is spec/wave, as the bar sends it (#980)"
    assert last("ty=task&fw=5/4&fs=5") == 1
    assert last("ty=bug&fw=none/none") == 1
    assert last("ty=bug&fk=bug") == 2
    assert last("ty=bug&fk=issue") == 0
    assert last("ty=task&fk=issue") == 1


def _kind_items(hub):
    """Cards whose project Kind field is set, as the live board holds them: Task and Spec, labelled bug and not."""
    backlog = hub.COLUMN["issue_todo"]
    return [_item(hub, 1, backlog, k="task"), _item(hub, 2, backlog, k="spec"),
            _item(hub, 3, backlog, k="task", labels=["bug"]), _item(hub, 4, backlog, k="spec", labels=["loop-bug"]),
            _item(hub, 5, backlog, k="bug")]


def test_the_kind_field_never_names_the_charts_kind(hub, board):
    backlog = hub.COLUMN["issue_todo"]
    items = _kind_items(hub)
    _seed(hub, items)

    def last(query):
        return _last_counts(hub.flow_cfd(*hub.cfd_filters(query)))[backlog]

    assert last("ty=task") == 3
    assert last("ty=task&fk=issue") == 3, "a Task or Spec card with no bug label is an issue, as on the board"
    assert last("ty=bug&fk=bug") == 2, "a card labelled bug or loop-bug is a bug, whatever its Kind field"
    assert last("ty=task&fk=issue,bug") == 3
    assert last("ty=bug&fk=issue,bug") == 2
    assert sorted(r["n"] for r in hub.lead_rows() if r["kind"] == "issue") == [1, 2, 5]
    assert sorted(r["n"] for r in hub.lead_rows() if r["kind"] == "bug") == [3, 4]


@pytest.mark.heavy   # one node start on the page's script: 3.2 s on a CI runner
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_server_and_the_page_give_each_card_the_same_kind(hub, board):
    items = _kind_items(hub)
    hub.board_cache["items"] = items
    lanes = (SCRIPTS / "board-lanes.js").read_text()
    script = lanes + f"\nconsole.log(JSON.stringify({json.dumps(items)}.map(kindKey)));"
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [r["kind"] for r in hub.lead_rows()]
    assert json.loads(result.stdout) == [hub._flow.kind_key(i) for i in items]


def test_the_control_chart_takes_the_same_filters(hub, board):
    closed = hub.COLUMN["issue_closed"]
    items = [_item(hub, 1, closed, pa=5, labels=["wave:2"], closed_at="2026-10-02T15:00:00Z"),
             _item(hub, 2, closed, pa=6, labels=["bug"], closed_at="2026-10-03T15:00:00Z")]
    _seed(hub, items)

    def dots(query):
        return sorted(d["n"] for d in hub.flow_lead(*hub.board_filters(query))["lead"]["control"]["all"]["dots"])

    assert dots("") == [1], "the default type is tasks"
    assert dots("fs=5") == [1]
    assert dots("fw=5/2") == [1]
    assert dots("ty=bug") == [2]
    assert dots("ty=bug&fk=bug") == [2]
    assert dots("ty=bug&fs=5") == []
    assert dots("fk=pr") == [], "the charts count issues: a PR is no row of the flow history or the lead time"


def test_the_drag_zoom_takes_the_same_filters(hub, board):
    closed = hub.COLUMN["issue_closed"]
    _seed(hub, [_item(hub, 1, closed, pa=5, closed_at="2026-10-02T15:00:00Z"),
                _item(hub, 2, closed, labels=["bug"], closed_at="2026-10-03T15:00:00Z")])
    span = "from=2026-09-01T00:00:00&to=2026-11-01T00:00:00"
    assert [d["n"] for d in hub.lead_zoom(span)["dots"]] == [1]
    assert [d["n"] for d in hub.lead_zoom(span + "&ty=bug&fk=bug")["dots"]] == [2]
    assert [d["n"] for d in hub.lead_zoom(span + "&fs=5")["dots"]] == [1]


def test_the_bar_query_names_waves_specs_and_kinds(hub):
    assert hub.board_filters("fw=5/4,none/none&fs=5&fk=bug,pr") == (["5/4", "none/none"], ["5"], ["bug", "pr"], "task")
    assert hub.board_filters("") == ([], [], [], "task")


def test_the_board_route_takes_no_filter_it_does_not_apply(hub):
    """The board filters on the page (boardOk, buildBoard): the served board is every card, and its route reads no bar value.

    Re-anchored (#973): `moves` is the pending-move record the serve already read, so one serve reads it once. It is not a
    filter, and a second parameter is still a defect."""
    import inspect
    assert list(inspect.signature(hub.board).parameters) in ([], ["moves"])
    served = next(line for line in HUB_PY.read_text().splitlines() if 'startswith("/api/board")' in line)
    nxt = HUB_PY.read_text().splitlines()[HUB_PY.read_text().splitlines().index(served) + 1]
    assert "board_filters" not in nxt


# -- the page: a filter change redraws both charts with the route's filtered answer ----------------------------------

def _page_line(hub, prefix):
    return next(line for line in hub.PAGE.splitlines() if line.startswith(prefix))


def _page_source(hub, start, end):
    """The page's own statements from `start` up to `end`, whole lines, so a multi-line function runs unchanged."""
    return hub.PAGE[hub.PAGE.index(start):hub.PAGE.index(end)].rstrip()


def _onchange(hub):
    return re.search(r'\$\("#flowb"\)\.onchange=.*?(?=\$\("#flowb"\)\.onclick=)', hub.PAGE, re.S).group(0)


def _page_harness(hub, answers, drive, onchange="", store=None, render=False, real_draw=True):
    """The page's own bar, chart and persistence lines, run in node over stub elements. `fetch` answers from `answers`,
    keyed by URL, with the hub's own route payloads; the script `drive` acts on the page and prints JSON. `store` seeds
    the page's localStorage, and the page's own save and saveB lines run, so a test reads the key each write reaches
    (#1275). No hand-written saver stands in for them: a change to the Board record's write line reaches every test.

    With `render`, the page's own `drawFlow` line runs unchanged and only its final sink is stubbed: the `#flowb`
    element's innerHTML setter records each markup the page wrote, so a test reads the DOM the page produced. With
    `real_draw=False` the same `drawFlow` is a no-op, the negative control that proves those reads bite (#1356 review)."""
    # The LT_TYPES line carries LT_START, the start the payload measures from, which ltOwn reads; the chart range restores
    # from its own key, apart from the Board filter's record under "hub" (#1247, #1275).
    lines = [_page_line(hub, p) for p in ["const LT_TYPES=", "let LT=", "let FLOWFF=", "const barQuery=",
                                         "const flowOpen=", "const ltQ=", "const ltZoom=", "const ltGet=",
                                         "const flowDetach=", "const barMoved=", "const pickFilter=", "const LT_TF=",
                                         "const CHART_KEY="]]
    if render:
        # The page's own layout, re-stack and renderer, as one contiguous slice: the drawn markup is the page's output.
        lines.append(hub.PAGE[hub.PAGE.index("let flowW="):hub.PAGE.index("const ltEx=")])
    else:
        lines += [_page_line(hub, p) for p in ("const flowFilter=", "const flowSave=", "let CF=")]
    # The two write paths are the page's own lines, never a look-alike: a regression that folded the chart range into
    # the Board record would show here (#1275).
    save = _page_line(hub, "const save=")
    board = _page_line(hub, "const bo=ls.bo||{},saveB=")
    if render:
        # The page's real drawFlow runs; only the element's innerHTML sink is stubbed, so DRAWN holds what the page
        # itself wrote. The negative control swaps the same line for a no-op (#1356 review).
        draw = ("const fbv=el();let _fb=\"\";"
                "Object.defineProperty(fbv,'innerHTML',{get:()=>_fb,set:v=>{_fb=String(v);DRAWN.push(_fb)}});"
                "fbv.clientWidth=1200;EL[\"#flowb\"]=fbv;\n"
                "const window={innerHeight:800};\n"
                + ("const drawFlow=()=>{};   // negative control: the page's draw writes nothing\n"
                   if not real_draw else _page_source(hub, "const drawFlow=", "const flowOpen=") + "\n"))
    else:
        draw = ("EL[\"#flowb\"]=el();\n"
                "const drawFlow=()=>DRAWN.push({...JSON.parse(JSON.stringify(FLOWD[FLOWK]||null)),hidden:[...CF.hidden]});"
                "   // the draw carries the states hidden when it ran (#1356)")
    stubs = f"""
const ANSWERS={json.dumps(answers)},FETCHED=[],DRAWN=[],POLLED=[];
const el=()=>({{hidden:true,handlers:{{}},addEventListener(t,f){{this.handlers[t]=f}}}});
const EL={{"#flowov":el(),"#fwl":el(),"#fsl":el(),"#fkl":el()}};
const $=s=>EL[s];
const fetch=async url=>{{FETCHED.push(url);if(!(url in ANSWERS))throw new Error("no answer for "+url);return {{json:async()=>ANSWERS[url]}}}};
const STORE={json.dumps(store or {})};
const localStorage=new Proxy({{}},{{get:(t,k)=>k==="getItem"?n=>n in STORE?STORE[n]:null:k==="setItem"?(n,v)=>{{STORE[n]=String(v)}}:STORE[k],set:(t,k,v)=>{{STORE[k]=String(v);return true}}}});
const ls=JSON.parse(localStorage.hub||"{{}}"),pins=new Set(),q="",per="day",src="";
let FLOWK=null,FLOWD={{}};
const COLORS={{}},esc=s=>String(s);
{save}
{board}
{draw}
const pollBoard=()=>POLLED.push(1);
const flowTabs=()=>{{}};   // the tab strip is not what this harness drives (#1080)
const BOARD_ROWS=[{{n:1,l:[]}},{{n:2,l:["bug"]}}];   // the two cards the board would read
const settle=()=>new Promise(r=>setTimeout(r,0));
"""
    # The page's own board filter, so a card list is the filter's answer, never the test's copy of it.
    script = stubs + (SCRIPTS / "board-lanes.js").read_text() + "\n" + "\n".join(lines) + "\n" + onchange + \
        "\n(async()=>{" + drive + "})().catch(e=>{console.error(e.stack);process.exit(1)});"
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _bar(fw="", fs="", fk="", ty="task"):
    return f"fw={fw}&fs={fs}&fk={fk}&ty={ty}"


def _cfd_bar(fw="", fs="", fk="", ty="task"):
    """The cumulative flow's route query: its own Type state, which opens on the tasks default the route always sent; All
    adds the extra `every=1` and keeps `ty` at a value the wire already knows, so the old parameter's meaning stands (#1356)."""
    every = ty == "all"
    return _bar(fw, fs, fk, "task" if every else ty) + ("&every=1" if every else "")


def test_a_kind_pick_redraws_the_open_cumulative_flow_with_the_filtered_route(hub, board):
    backlog = hub.COLUMN["issue_todo"]
    items = [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"]), _item(hub, 3, backlog, labels=["bug"])]
    _seed(hub, items)
    answers = {f"/api/chart/cfd?{q}": hub.flow_cfd(*hub.cfd_filters(q))
               for q in (_cfd_bar(ty="task"), _cfd_bar(ty="task", fk="bug"))}
    out = _page_harness(hub, answers, """
CF.ty="task";pickFilter("fkl","fk");
await flowOpen("cfd");await settle();
EL["#fkl"].handlers.change({target:{value:"bug",checked:true}});await settle();await settle();
console.log(JSON.stringify({fetched:FETCHED,bo,last:DRAWN[DRAWN.length-1],store:STORE,polled:POLLED.length}));""",
                           onchange=_onchange(hub))
    assert out["fetched"] == [f"/api/chart/cfd?{_cfd_bar(ty='task')}", f"/api/chart/cfd?{_cfd_bar(ty='task', fk='bug')}"]
    assert out["bo"]["fk"] == ["bug"] and out["polled"] == 1, "the pick is stored as the board's own"
    assert set(out["store"]) == {"hub"} and json.loads(out["store"]["hub"])["bo"]["fk"] == ["bug"], \
        "a kind pick writes the Board key only"
    count = _last_counts(out["last"])[backlog]
    assert count == 0, "the open chart is redrawn from the filtered route: no task is a bug"


def test_a_spec_clear_redraws_the_open_control_chart_and_its_chosen_span(hub, board):
    closed = hub.COLUMN["issue_closed"]
    _seed(hub, [_item(hub, 1, closed, pa=5, closed_at="2026-10-02T15:00:00Z"),
                _item(hub, 2, closed, closed_at="2026-10-03T15:00:00Z")])
    q5, qall = _bar(fs="5"), _bar()
    span = "&a=Doing&b="   # re-anchored (#1247): Todo is the payload's own start, so the chosen span is Doing
    answers = {f"/api/chart/lead?{q5}": hub.flow_lead(*hub.board_filters(q5)),
               f"/api/chart/lead?{qall}": hub.flow_lead(*hub.board_filters(qall)),
               f"/api/leadtime?tf=30{span}&{qall}": {"dots": [{"n": 1}, {"n": 2}]}}
    out = _page_harness(hub, answers, """
bo.fs=["5"];pickFilter("fsl","fs");
EL["#flowb"].onchange({target:{id:"lty",value:"task"}});
EL["#flowb"].onchange({target:{id:"ltr",value:"30"}});
LT.a="Doing";
await flowOpen("lead");await settle();
EL["#fsl"].handlers.click({target:{closest:s=>s==="[data-clear]"},preventDefault(){}});await settle();await settle();
console.log(JSON.stringify({fetched:FETCHED,fs:bo.fs,zc:LT.zc,lead:FLOWD.lead}));""", onchange=_onchange(hub))
    assert out["fs"] == []
    assert f"/api/chart/lead?{qall}" in out["fetched"], "the clear reads the control chart anew"
    assert f"/api/leadtime?tf=30{span}&{qall}" in out["fetched"], "a chosen span is read anew with the bar's values"
    assert sorted(d["n"] for d in out["lead"]["lead"]["control"]["all"]["dots"]) == [1, 2]
    assert out["zc"] == {"dots": [{"n": 1}, {"n": 2}]}


def test_an_answer_for_an_old_bar_value_is_never_drawn(hub, board):
    backlog = hub.COLUMN["issue_todo"]
    items = [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])]
    _seed(hub, items)
    answers = {f"/api/chart/cfd?{q}": hub.flow_cfd(*hub.cfd_filters(q))
               for q in (_cfd_bar(ty="task"), _cfd_bar(ty="task", fk="bug"))}
    out = _page_harness(hub, answers, """
CF.ty="task";pickFilter("fkl","fk");
const first=flowOpen("cfd");
EL["#fkl"].handlers.change({target:{value:"bug",checked:true}});
await first;await settle();await settle();
console.log(JSON.stringify({last:DRAWN[DRAWN.length-1]}));""", onchange=_onchange(hub))
    count = _last_counts(out["last"])[backlog]
    assert count == 0, "the answer for the picked kind is drawn, not the one read before the pick"


def test_the_bar_has_a_kinds_control_beside_waves_and_specs(hub):
    assert "<details id=fk class=fsel><summary>Kinds: all</summary><div class=fpop id=fkl></div></details>" in hub.PAGE
    assert 'pickFilter("fkl","fk")' in hub.PAGE
    # re-anchored (#973): the read may carry the ETag header and nothing else, so no bar value reaches the route
    assert re.search(r'fetch\("/api/board"\s*,\s*boardTag\?\{headers:\{"If-None-Match":boardTag\}\}:\{\}\)', hub.PAGE), "the board read carries no bar value: the page filters the board"


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


# -- the Type filter: specs, tasks or bugs (#985) ------------------------------------------------------------------------

def test_a_card_is_a_spec_a_bug_or_a_task_by_its_labels(flow):
    assert flow.type_key({"l": ["spec"]}) == "spec"
    assert flow.type_key({"l": ["bug"]}) == "bug"
    assert flow.type_key({"l": ["loop-bug"]}) == "bug"
    assert flow.type_key({"l": []}) == "task"
    assert flow.type_key({"l": ["spec", "bug"]}) == "spec", "a spec labelled bug is a spec"


def test_a_sample_names_each_cards_type_in_its_cell(flow):
    s = flow.sample([_row(1, labels=["spec"]), _row(2, labels=["bug"]), _row(3)], now=_epoch(20))
    assert ["issue", "none", "none", "Todo", 0, 1, "spec"] in s["cells"]
    assert ["bug", "none", "none", "Todo", 0, 1, "bug"] in s["cells"]
    assert ["issue", "none", "none", "Todo", 0, 1, "task"] in s["cells"]


def test_the_bar_names_a_type_and_the_default_is_tasks(hub):
    assert hub.board_filters("fw=5/4&ty=bug") == (["5/4"], [], [], "bug")
    assert hub.board_filters("") == ([], [], [], "task")
    assert hub.board_filters("ty=spec&fs=5") == ([], ["5"], [], "spec")
    assert hub.board_filters("ty=pr")[3] == "task", "a value that is no type falls back to the default"


def _typed_items(hub):
    """Two tasks closed in 24 h and 48 h, one task closed in 72 h, one open task; two bugs in 1 h and 2 h; one spec in 240 h."""
    closed = hub.COLUMN["issue_closed"]
    return [_item(hub, 1, closed, closed_at="2026-10-02T09:00:00Z"),
            _item(hub, 2, closed, closed_at="2026-10-03T09:00:00Z"),
            _item(hub, 3, closed, closed_at="2026-10-04T09:00:00Z"),
            _item(hub, 4, hub.COLUMN["unscheduled"]),
            _item(hub, 5, closed, labels=["bug"], closed_at="2026-10-01T10:00:00Z"),
            _item(hub, 6, closed, labels=["bug"], closed_at="2026-10-01T11:00:00Z"),
            _item(hub, 7, closed, labels=["spec"], closed_at="2026-10-11T09:00:00Z")]


def _dots(chart):
    return [d["n"] for d in chart["dots"]]


def test_each_type_selects_only_its_issues_in_the_control_chart(hub, board):
    _seed(hub, _typed_items(hub))
    for ty, expected in (("task", [1, 2, 3]), ("bug", [5, 6]), ("spec", [7])):
        assert _dots(hub.flow_lead(ty=ty)["lead"]["control"]["all"]) == expected, ty


def test_each_statistic_is_computed_over_the_selected_type_only(hub, board):
    _seed(hub, _typed_items(hub))
    tasks = hub.flow_lead(ty="task")["lead"]
    assert tasks["summary"]["average"]["text"] == "2d" and tasks["summary"]["median"]["text"] == "2d"
    assert tasks["summary"]["p85"]["text"] == "3d"
    # Re-anchored (#1250): the open task is censored in the survival reading, never an exclusion.
    assert tasks["excluded_total"] == 0 and tasks["excluded"] == []
    control = hub.flow_lead(ty="task")["lead"]["control"]["all"]
    assert control["average"]["text"] == "2d" and control["p85"]["text"] == "3d"
    # Re-anchored (#1355): the one axis is log, so the rolling mean is the geometric mean of 24 h, 48 h and 72 h, with
    # one sigma taken in log space, derived here from those three hours rather than from the code's own formula.
    tasks_hours = [24.0, 48.0, 72.0]
    logs = [math.log(h) for h in tasks_hours]
    centre, sigma = statistics.fmean(logs), statistics.pstdev(logs)
    rolling = control["dots"][0]["rolling"]
    assert rolling["hours"] == pytest.approx(math.exp(centre)), "the rolling mean is over the three tasks, not the bugs"
    assert rolling["low"] == pytest.approx(math.exp(centre - sigma)), "the sigma band is over the three tasks"
    assert rolling["high"] == pytest.approx(math.exp(centre + sigma))
    bugs = hub.flow_lead(ty="bug")["lead"]
    # Re-anchored (#1250): the median is Kaplan-Meier: the curve over 1h and 2h falls to one half at 1h.
    assert bugs["summary"]["average"]["text"] == "1h 30m" and bugs["summary"]["median"]["text"] == "1h"
    assert bugs["summary"]["p85"]["text"] == "2h" and bugs["excluded_total"] == 0
    specs = hub.flow_lead(ty="spec")["lead"]
    assert specs["summary"]["average"]["text"] == "10d" and specs["summary"]["p85"]["text"] == "10d"


def test_the_cumulative_flow_takes_the_type(hub, board):
    backlog = hub.COLUMN["issue_todo"]
    items = [_item(hub, 1, backlog), _item(hub, 2, backlog), _item(hub, 3, backlog, labels=["bug"]),
             _item(hub, 4, backlog, labels=["spec"])]
    _seed(hub, items)

    def last(ty):
        return _last_counts(hub.flow_cfd(ty=ty))[backlog]

    assert [last("task"), last("bug"), last("spec")] == [2, 1, 1]


# -- the cumulative flow's Type and visible states (#1356) ---------------------------------------------------------------

def test_the_cumulative_flow_type_offers_all_and_counts_one_card_of_each_type(hub, board):
    """#1356: All is a cumulative flow type: it counts the spec, the task and the bug, and each single type counts its own.
    The wire's own `ty` keeps its accepted values and its tasks default; All rides the extra `every=1` beside it."""
    backlog = hub.COLUMN["issue_todo"]
    _seed(hub, [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"]),
                _item(hub, 3, backlog, labels=["spec"])])

    assert hub.board_filters("ty=all")[3] == "task", "an unknown type stays the tasks default the route always sent"
    assert hub.cfd_filters("ty=task&every=1")[3] is None, "the extra every=1 keeps every type"
    assert [_last_counts(hub.flow_cfd(*hub.cfd_filters(q)))[backlog]
            for q in ("ty=task&every=1", "ty=spec", "ty=task", "ty=bug")] == [3, 1, 1, 1], \
        "All counts one card of each type; a single type counts only its own"


def test_the_cumulative_flow_classifies_a_type_as_the_lead_time_chart_does(hub, board):
    """#1356: the two views agree: a card labelled both spec and bug is a spec in both."""
    closed = hub.COLUMN["issue_closed"]
    _seed(hub, [_item(hub, 1, closed, labels=["spec", "bug"], closed_at="2026-10-02T09:00:00Z")])

    assert _last_counts(hub.flow_cfd(ty="spec"))[closed] == 1
    assert _last_counts(hub.flow_cfd(ty="bug"))[closed] == 0
    assert _dots(hub.flow_lead(ty="spec")["lead"]["control"]["all"]) == [1]   # re-anchored (#1355): one log axis, no scale key


def test_a_state_the_board_definition_does_not_name_never_shows(hub, board):
    """#1356: the bands are the board definition's columns; a card in a state it does not name counts in no band."""
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    item = _item(hub, 1, "Some Lane")
    hub.board_cache["items"] = [item]
    events = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Some Lane"}]
    hub._hist.record_issue(path, 1, OPENED, OPENED, events, True, now=time.time(), keys=hub.bar_keys(item))

    cfd = hub.flow_cfd(*hub.cfd_filters("ty=task&every=1"))["cfd"]

    assert [b["name"] for b in cfd["bands"]] == list(hub.ORDER), "the bands are exactly the board definition's columns"
    assert "Some Lane" not in [b["name"] for b in cfd["bands"]], "a state the definition does not name shows no band"
    assert all(b["count"][-1] == 0 for b in cfd["bands"]), "the card counts in no band"


def test_the_drag_zoom_takes_the_type(hub, board):
    _seed(hub, _typed_items(hub))
    span = "from=2026-09-01T00:00:00&to=2026-11-01T00:00:00"
    assert _dots(hub.lead_zoom(span)) == [1, 2, 3]
    assert _dots(hub.lead_zoom(span + "&ty=bug")) == [5, 6]
    assert _dots(hub.lead_zoom(span + "&ty=spec")) == [7]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_header_names_the_type_the_chart_holds(hub, board):
    _seed(hub, _typed_items(hub))
    lead = hub.flow_lead(ty="task")["lead"]
    block = _flow_block(hub).split("const drawFlow")[0]
    script = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
                        f"process.stdout.write(flowLead({json.dumps(lead)}));"])
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "tasks · <b>Todo -> closed</b> · window 20 issues" in result.stdout, "the view measures from Todo (#1247)"


def test_the_type_control_sits_beside_the_timeframe(hub):
    page = hub.PAGE
    assert page.index("<select id=ltr>") < page.index("<select id=lty>"), "Type groups with Timeframe"
    assert "id=lts" not in page and ">Scale <" not in page, \
        "a Scale control renders: the lead chart is always log (#1355)"
    assert '[["spec","Specs"],["task","Tasks"],["bug","Bugs"]]' in page


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_stored_chart_range_waits_for_the_lead_chart_and_the_type_is_never_stored(hub):
    """#1275: the stored range stays out of the shared state until the lead chart opens, so the Board tab reads the
    payload's own start; the timeframe, the Type and the scale stay the visit's choice (#1247)."""
    block = _flow_block(hub).split("const drawFlow")[0]

    def loaded(stored):
        stub = 'const localStorage={getItem:k=>k==="squad-chart"?' + json.dumps(json.dumps(stored)) + ':null,setItem(){}};'
        script = "\n".join([stub, "const esc=s=>String(s);", block, _page_line(hub, "const ltQ="),
                            "process.stdout.write(JSON.stringify({ty:LT.ty,tf:LT.tf,a:LT.a,b:LT.b,own:ltOwn()}));"])
        result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    fresh = {"ty": "bug", "tf": "12h", "a": "Todo", "b": "", "own": False}   # a fresh page opens on the past 12 hours (#2029)
    for stored in ({"tf": "30", "a": "Doing", "b": "Closed"}, {"ty": "task", "tf": "3", "a": "Doing"}, {}):
        assert loaded(stored) == fresh, "a stored chart range never reaches the Board tab's shared state"


def test_the_board_write_path_never_names_the_chart_key(hub):
    """#1275: the Board record's three write lines name no chart key and no chart range."""
    for prefix in ("const $=s=>document.querySelector(s),ls=", "const save=", "const bo=ls.bo||{},saveB="):
        line = _page_line(hub, prefix)
        assert "CHART_KEY" not in line and "squad-chart" not in line and "LT." not in line, \
            f"{prefix!r} writes the Board record, never the chart's"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_reload_restores_the_board_filter_and_the_chart_range_apart(hub, board):
    """#1275: the Board filter and the chart range each keep their own key; a reload restores each untouched by the other,
    and the chart's range never reaches the board's route or its cards."""
    backlog = hub.COLUMN["issue_todo"]
    _seed(hub, [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])])
    onchange = _onchange(hub)
    # The board's own route and its card list, by the page's bar query and its boardOk, read before any chart opens: a
    # range that leaked into the Board tab's shared state would narrow both here (#1275).
    observe = """const snap=()=>({a:LT.a,own:ltOwn(),route:barQuery("board"),
cards:BOARD_ROWS.filter(x=>boardOk(x,bo.fw||[],bo.fs||[],bo.fk||[])).map(x=>x.n)});
const pre=snap();"""
    report = "const post=snap();console.log(JSON.stringify({pre,post,bo,tf:LT.tf,a:LT.a,b:LT.b,store:STORE,fetched:FETCHED}));"
    first = _page_harness(hub, {}, 'pickFilter("fkl","fk");EL["#fkl"].handlers.change({target:{value:"bug",checked:true}});'
                          + observe + report, onchange=onchange, store={})
    assert set(first["store"]) == {"hub"}, "a Board filter writes only the Board key"
    assert first["pre"]["cards"] == [2]
    assert first["pre"]["own"] is False and first["pre"]["a"] == "Todo", "the board opens on the payload's own start"

    answers = {f"/api/leadtime?tf=30&a=Doing&b=&{_bar(ty='bug')}": {"dots": []}}
    second = _page_harness(hub, answers, 'EL["#flowb"].onchange({target:{id:"ltr",value:"30"}});'
                           'EL["#flowb"].onchange({target:{id:"lta",value:"Doing"}});await settle();' + observe + report,
                           onchange=onchange, store=first["store"])
    assert second["bo"] == first["bo"] == {"fk": ["bug"]}, "a chart range never touches the Board filter"
    assert second["store"]["hub"] == first["store"]["hub"], "a chart range leaves the Board key as it was"
    assert set(second["store"]) == {"hub", "squad-chart"}
    assert json.loads(second["store"]["squad-chart"]) == {"a": "Doing", "b": ""}, \
        "the chart key carries the range only; the timeframe stays the visit's choice (#1247)"

    span = f"/api/leadtime?tf=12h&a=Doing&b=&{_bar(fk='bug', ty='bug')}"
    answers = {f"/api/chart/lead?{_bar(fk='bug', ty='bug')}": {"lead": {}, "t": 1}, span: {"dots": []}}
    third = _page_harness(hub, answers, observe + 'await flowOpen("lead");await settle();' + report,
                          onchange=onchange, store=second["store"])
    assert third["bo"] == {"fk": ["bug"]}, "the Board filter survives the reload untouched"
    assert (third["pre"]["a"], third["pre"]["own"]) == ("Todo", False), \
        "the restored range stays off the board: the Board tab reads the payload's own start"
    assert "a=Doing" not in third["pre"]["route"] and "b=" not in third["pre"]["route"], \
        "the board's own route carries neither the stored Start nor a stored End"
    assert third["pre"]["cards"] == first["pre"]["cards"] == [2], "the chart range never narrows the board's cards"
    assert third["tf"] == "12h" and third["a"] == "Doing", "the range is restored when the lead chart opens; the timeframe is not"
    assert span in third["fetched"], "the restored range is what the opened chart reads"
    assert third["post"]["route"] == third["pre"]["route"] and third["post"]["cards"] == first["pre"]["cards"], \
        "opening the chart reads the board's route and cards unchanged"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_type_pick_saves_the_choice_and_redraws_the_open_lead_chart(hub, board):
    _seed(hub, _typed_items(hub))
    answers = {f"/api/chart/lead?{_bar(ty=ty)}": hub.flow_lead(ty=ty) for ty in ("task", "bug")}
    onchange = _onchange(hub)
    out = _page_harness(hub, answers, """
EL["#flowb"].onchange({target:{id:"lty",value:"task"}});
await flowOpen("lead");await settle();
EL["#flowb"].onchange({target:{id:"lty",value:"bug"}});await settle();await settle();
console.log(JSON.stringify({fetched:FETCHED,ty:LT.ty,store:STORE,drawn:DRAWN[DRAWN.length-1]}));""", onchange=onchange)
    assert out["fetched"] == [f"/api/chart/lead?{_bar()}", f"/api/chart/lead?{_bar(ty='bug')}"]
    assert out["ty"] == "bug"
    assert out["store"] == {}, "the choice stays for the visit and is never stored (#1247)"
    assert out["drawn"]["lead"]["type"] == "bug"


# -- the Board filter chip: one chip names the filter and stops one chart following it (#1147) --------------------------

def _chip_render(hub, fw=(), fs=(), fk=(), stopped=False):
    """The chip's markup, rendered by the page's own function."""
    block = _flow_block(hub).split("const drawFlow")[0]
    esc = _page_line(hub, "const esc=")
    script = "\n".join(["const COLORS={},COLNAME={};", esc, "const ls={};", block,
                        f"process.stdout.write(flowFilterChip({json.dumps(list(fw))},{json.dumps(list(fs))},"
                        f"{json.dumps(list(fk))},{str(stopped).lower()}));"])
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_board_filter_chip_appears_exactly_when_the_filter_is_set(hub):
    """#1147: the chip is drawn exactly when Waves, Specs or Kinds is non-empty, and it names each one that is set."""
    assert _chip_render(hub) == "", "an empty Board filter draws no chip"
    chip = _chip_render(hub, fw=["5/4"], fs=["2"], fk=["bug"])
    assert chip.count("id=ffchip") == 1, "one chip names the filter"
    for name in ("Waves: 5/4", "Specs: 2", "Kinds: bug"):
        assert name in chip, f"the chip does not name {name!r}"
    assert "id=ffx" in chip and "Stop following" in chip, "the chip carries its one action"
    assert _chip_render(hub, fk=["bug"]).count("Waves:") == 0, "an unset filter is not named"
    markup = _chip_render(hub, fk=["<b>&"])
    assert "<b>" not in markup and "&lt;b&gt;" in markup, "a filter name is escaped into the chip"


def test_the_board_filter_chip_is_drawn_once_with_the_chart_tabs(hub):
    """#1147: one chip element above the chart area, once, for both charts, never inside one chart's head."""
    page = hub.PAGE
    assert page.count("id=flowchips") == 1, "one chip element, once, for both charts"
    assert page.index("<div class=rkt id=flowtabs>") < page.index("id=flowchips") < page.index("<div id=flowov"), \
        "the chip does not sit above the chart area with the chart tabs"
    assert "flowFilterChip(" in _page_line(hub, "function flowTabs("), "the chip is not drawn with the chart tabs"
    for chart in ("const flowCFD=", "const ltChart=", "const flowLead="):
        assert "ffchip" not in _page_line(hub, chart), "a chart draws its own copy of the filter chip"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chip_stops_this_chart_following_and_leaves_the_board_filter_alone(hub, board):
    """#1147: the chip's one action stops the open chart following the Board filter and writes no Board state."""
    backlog = hub.COLUMN["issue_todo"]
    items = [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])]
    _seed(hub, items)
    cfd_all, cfd_bug = _cfd_bar(), _cfd_bar(fk="bug")   # the cumulative flow's own Type, its tasks default (#1356)
    lead_bug = _bar(fk="bug", ty="bug")   # the control chart opens on Bugs
    answers = {f"/api/chart/cfd?{cfd_all}": hub.flow_cfd(*hub.cfd_filters(cfd_all)),
               f"/api/chart/cfd?{cfd_bug}": hub.flow_cfd(*hub.cfd_filters(cfd_bug)),
               f"/api/chart/lead?{lead_bug}": hub.flow_lead(*hub.board_filters(lead_bug))}
    out = _page_harness(hub, answers, """
bo.fk=["bug"];
await flowOpen("cfd");await settle();
flowDetach();await settle();await settle();
await flowOpen("lead");await settle();
console.log(JSON.stringify({fetched:FETCHED,bo,ff:FLOWFF}));""")
    assert out["fetched"] == [f"/api/chart/cfd?{cfd_bug}", f"/api/chart/cfd?{cfd_all}", f"/api/chart/lead?{lead_bug}"], \
        "the stopped chart reads again without the filter, and the other chart still follows it"
    assert out["bo"]["fk"] == ["bug"], "the Board tab's own filter is untouched"
    assert out["ff"] == {"cfd": True}, "only the chart the action named stopped following the filter"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_stopping_the_chip_on_a_zoomed_control_chart_reads_it_once(hub, board):
    """#1147: the chip's one action reloads the zoomed control chart once, on the route without the Board filter.

    The chart is zoomed, so the action follows the zoom route rather than the timeframe route; two reads would race
    and draw whichever answer lands last."""
    backlog = hub.COLUMN["issue_todo"]
    _seed(hub, [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])])
    q_bug, q_all = _bar(fk="bug", ty="bug"), _bar(ty="bug")
    f, t = "2026-10-02T00:00:00.000Z", "2026-10-09T00:00:00.000Z"
    span = "&a=Todo&b="   # re-anchored (#1247): the view starts at Todo, as the payload measures
    from_q, to_q = urllib.parse.quote(f, safe=""), urllib.parse.quote(t, safe="")
    zoom_bug = f"/api/leadtime?from={from_q}&to={to_q}{span}&{q_bug}"
    zoom_all = f"/api/leadtime?from={from_q}&to={to_q}{span}&{q_all}"
    answers = {f"/api/chart/lead?{q_bug}": hub.flow_lead(*hub.board_filters(q_bug)),
               zoom_bug: {"dots": []}, zoom_all: {"dots": []}}
    out = _page_harness(hub, answers, f"""
bo.fk=["bug"];
await flowOpen("lead");await settle();
await ltZoom({json.dumps(f)},{json.dumps(t)});await settle();
FETCHED.length=0;
flowDetach();await settle();await settle();
console.log(JSON.stringify({{fetched:FETCHED,ff:FLOWFF,bo}}));""")
    assert out["fetched"] == [zoom_all], "the zoomed control chart reloads once, without the Board filter"
    assert out["ff"] == {"lead": True}, "the control chart stopped following the filter"
    assert out["bo"]["fk"] == ["bug"], "the Board tab's own filter is untouched"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_new_board_filter_makes_the_chart_follow_it_again(hub, board):
    """#1147: a new Board filter is a new instruction, so a chart that stopped following reads with it again."""
    backlog = hub.COLUMN["issue_todo"]
    _seed(hub, [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])])
    q_all, q_bug = _cfd_bar(), _cfd_bar(fk="bug")
    answers = {f"/api/chart/cfd?{q}": hub.flow_cfd(*hub.cfd_filters(q)) for q in (q_all, q_bug)}
    out = _page_harness(hub, answers, """
bo.fk=["bug"];pickFilter("fkl","fk");
await flowOpen("cfd");await settle();
flowDetach();await settle();await settle();
EL["#fkl"].handlers.change({target:{value:"bug",checked:false}});await settle();await settle();
console.log(JSON.stringify({ff:FLOWFF,fetched:FETCHED,bo}));""")
    assert out["ff"] == {}, "a new Board filter makes the chart follow it again"
    assert out["fetched"][-1] == f"/api/chart/cfd?{q_all}", "the chart reads the new filter's route"
    assert out["bo"]["fk"] == [], "the board keeps the new filter"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_cumulative_flow_route_carries_the_chart_type(hub, board):
    """#1356: the cumulative flow's Type is its own setting; its route sends it, so the payload counts that type. All is
    the extra `every=1` beside the fixed `ty` values, so a reader of the old parameter reads the chart it always read."""
    backlog = hub.COLUMN["issue_todo"]
    _seed(hub, [_item(hub, 1, backlog), _item(hub, 2, backlog, labels=["bug"])])
    q, every = _cfd_bar(ty="bug"), _cfd_bar(ty="all")
    answers = {f"/api/chart/cfd?{k}": hub.flow_cfd(*hub.cfd_filters(k)) for k in (q, every)}
    out = _page_harness(hub, answers, """
CF.ty="bug";await flowOpen("cfd");await settle();
CF.ty="all";await flowOpen("cfd");await settle();
console.log(JSON.stringify({fetched:FETCHED,last:DRAWN[DRAWN.length-1]}));""")
    assert "ty=all" not in every and every.endswith("ty=task&every=1"), "All never widens the old parameter's values"
    assert out["fetched"] == [f"/api/chart/cfd?{q}", f"/api/chart/cfd?{every}"], \
        "the cumulative flow route carries the Type it filters by"
    assert _last_counts(out["last"])[backlog] == 2, "All counts every type: the task and the bug"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_flow_filters_restore_from_the_page_key_after_a_reload(hub):
    """#1356: the Type and the hidden states persist in the page's own key across a redraw and a reload."""
    block = _flow_block(hub).split("const drawFlow")[0]
    script = "\n".join([
        "const STORE={};",
        "const localStorage={getItem:k=>k in STORE?STORE[k]:null,setItem:(k,v)=>{STORE[k]=String(v)}};",
        "const COLORS={},COLNAME={};const esc=s=>String(s);const ls={};",
        block,
        'CF.ty="spec";CF.hidden=["Doing"];flowSave();',
        'const stored=JSON.parse(STORE["squad-flow"]);const reload={...flowFilter()};',
        'STORE["squad-flow"]=\'{"ty":"nope","hidden":"Doing"}\';const bad={...flowFilter()};',
        "console.log(JSON.stringify({stored,reload,bad}));"])
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["stored"] == {"ty": "spec", "hidden": ["Doing"]}, "the page writes the two filters to its own key"
    assert out["reload"] == {"ty": "spec", "hidden": ["Doing"]}, "a reload restores both filters"
    assert out["bad"] == {"ty": "task", "hidden": []}, "a value that is no type opens on the tasks default; a hidden set that is no list is none"
    assert 'let CF={r:"all",...flowFilter()}' in hub.PAGE, \
        "the chart state restores from the page key at load, and opens on the whole history (All)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_flow_filter_redraws_the_held_payload_and_reads_no_board(hub, board):
    """#1356: a visibility change draws the payload the page holds with no read at all, and a Type change reads the chart
    route only: the board is never read again. The real handler runs on a browser-shaped event and the DOM it produced is
    read, never a harness record of the filter state (#1356 review)."""
    # A task in Todo and a spec in Doing: the spec answer has a day and a band, and its counts differ from the task
    # answer's, so the drawn markup itself proves which payload the page still holds. Each card's history names its own
    # column, unlike the shared seed, which records every card in Todo.
    items = [_item(hub, 1, hub.COLUMN["issue_todo"], labels=["bug"]), _item(hub, 2, "Doing", labels=["spec"])]
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    for item, st in ((items[0], hub.COLUMN["issue_todo"]), (items[1], "Doing")):
        hub._hist.record_issue(path, item["n"], OPENED, OPENED,
                               [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": st}],
                               True, now=time.time(), keys=hub.bar_keys(item))
    hub.board_cache["items"] = items
    q_first, q_spec = _cfd_bar(), _cfd_bar(ty="spec")
    answers = {f"/api/chart/cfd?{q}": hub.flow_cfd(*hub.cfd_filters(q)) for q in (q_first, q_spec)}
    # The toggle is the browser's own change event: a checkbox target with its dataset, checked state and input type.
    drive = """
const toggle=v=>EL["#flowb"].onchange({type:"change",target:
  {id:"",tagName:"INPUT",type:"checkbox",value:"on",dataset:{state:v},checked:false}});
await flowOpen("cfd");await settle();
EL["#flowb"].onchange({target:{id:"cfdy",value:"spec"}});await settle();await settle();
const before=DRAWN.length,beforeMarkup=DRAWN[DRAWN.length-1],beforeHtml=EL["#flowb"].innerHTML;
toggle("Doing");await settle();
console.log(JSON.stringify({fetched:FETCHED,before,draws:DRAWN.length,last:DRAWN[DRAWN.length-1],beforeMarkup,
  beforeHtml,afterHtml:EL["#flowb"].innerHTML,ty:CF.ty,hidden:CF.hidden,store:STORE,polled:POLLED.length}));"""
    out = _page_harness(hub, answers, drive, onchange=_onchange(hub), render=True)

    def legend(markup):
        return re.findall(r"<span class=flg><i[^>]*></i>([^<]*)</span>", markup)

    bands = [b["name"] for b in answers[f"/api/chart/cfd?{q_spec}"]["cfd"]["bands"]]
    assert out["ty"] == "spec" and out["hidden"] == ["Doing"], "the page keeps both filters"
    assert out["fetched"] == [f"/api/chart/cfd?{q_first}", f"/api/chart/cfd?{q_spec}"], \
        "the Type change reads the chart route, never the board"
    assert not any("/api/board" in u for u in out["fetched"]), "no filter change reads the board"
    assert out["polled"] == 0
    # The page's own drawFlow wrote the element: the markup the handler produced is the element's innerHTML, never a
    # harness record of the filter state (#1356 review).
    assert out["afterHtml"] != out["beforeHtml"], "the handler's real drawFlow writes the #flowb element"
    assert out["afterHtml"] == out["last"], "the element's innerHTML is what the page's drawFlow wrote"
    assert out["beforeHtml"].count("Loading the chart") == 0, "the drawn chart replaced the loading line"
    # The count rises only when the handler calls drawFlow: a toggle branch that skips it leaves the count unchanged.
    assert out["draws"] == out["before"] + 1, "the visibility toggle draws once, after the state is hidden"
    assert legend(out["beforeMarkup"]) == bands, "the chart before the toggle draws every band"
    assert "Todo 0" in out["beforeMarkup"] and "Doing 1" in out["beforeMarkup"], \
        "the drawn payload is still the held type=spec answer, never the unfiltered one"
    assert legend(out["last"]) == [b for b in bands if b != "Doing"], \
        "the markup the handler produced lost the hidden band from the legend"
    assert "<title>Doing</title>" not in out["last"], "the hidden band is still drawn"
    assert re.search(r'<input type=checkbox data-state="Doing"[^>]*>', out["last"]).group(0).find(" checked") < 0, \
        "the hidden state's checkbox reads unchecked"
    assert "Doing 1" not in out["last"], "the hover names only the visible states"
    assert json.loads(out["store"]["squad-flow"]) == {"ty": "spec", "hidden": ["Doing"]}, \
        "a filter change writes the page key, never the Board record"

    # The negative control: with the page's drawFlow replaced by a no-op the element keeps its old markup, so the two
    # assertions above read the page's own draw and would fail on a draw that stops writing #flowb (#1356 review).
    dead = _page_harness(hub, answers, drive, onchange=_onchange(hub), render=True, real_draw=False)
    assert dead["draws"] == 0 and dead["afterHtml"] == dead["beforeHtml"] == "", \
        "a drawFlow that writes nothing leaves the element alone: the proof reads the page's own draw"


def test_the_lead_chart_offers_twelve_hours_first_and_serves_it():
    """#2029: the Timeframe list starts with Past 12 hours, and the route serves that preset as half a day."""
    text = (Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/hub.py").read_text()
    line = next(row for row in text.splitlines() if row.startswith("const LT_TF="))
    assert line.startswith('const LT_TF=[["12h","Past 12 hours"],'), line
    tf = next(row for row in text.splitlines() if row.startswith("LEAD_TIMEFRAMES = "))
    assert '("12h", 0.5)' in tf, tf
