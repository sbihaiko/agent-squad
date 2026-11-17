"""The hub page's charts are pushed per subject: each chart redraws when its own input changes, a dropped stream is named,
and a reconnect reads each open subject once. With no live stream the open chart is polled on the board tick.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
OPENED = "2026-10-01T09:00:00Z"
BAR = "fs=&fk=&ty=task"   # the cumulative flow's route, at the tasks default it always sent (#1356); no wave key (SPEC 1965, #1988)

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _load(name, path):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_chart_stream")
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
        yield _load("hub_chart_stream", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub, "CHARTS", {})
    return tmp_path


def _changed(versions, held):
    versions.refresh()
    return sorted(versions.newer(held))


def test_a_chart_subject_rises_only_when_its_own_input_changes(hub, board):
    assert sorted(hub.CHART_INPUTS) == ["cfd", "lead"]
    versions = hub.hubstream.Versions(hub.CHART_INPUTS)
    versions.refresh()
    held = versions.newer({})
    item = {"n": 1, "i": "PVT_1", "t": "card 1", "s": "OPEN", "c": None, "st": hub.COLUMN["issue_todo"], "k": None,
            "l": [], "si": {"total": None, "completed": None}, "o": OPENED}
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    events = [{"t": OPENED, "type": "added"}, {"t": OPENED, "type": "status", "status": "Todo"}]
    hub._hist.record_issue(path, 1, OPENED, OPENED, events, True, now=time.time(), keys=hub.bar_keys(item))
    assert _changed(versions, held) == ["cfd", "lead"], "a history record is the input of both charts"
    held = versions.newer({})
    hub.board_cache["t"] = time.time() + 60
    assert _changed(versions, held) == ["lead"], "the board read time is the lead chart's input only"
    held = versions.newer({})
    assert _changed(versions, held) == [], "a quiet tick rises no subject"


def _chart_harness(hub, drive, onchange=""):
    # Re-anchored (#1247): the chart's settings are no longer stored, so there is no saveLT line; the LT_TYPES line
    # carries LT_START, the start the payload measures from, which ltOwn reads.
    lines = ["const LT_TYPES=", "let FLOWFF=", "const barQuery", "const flowOpen", "const ltQ", "const ltZoom", "const ltGet",
             "const barMoved", "const pickFilter", "function squadChart(", "function squadStreamLost(",
             "function squadStreamBack(", "pollBoard();setInterval(pollBoard", "function squadChartTick(", "setInterval(squadChartTick"]
    page = [next(line for line in hub.PAGE.splitlines() if line.startswith(p)) for p in lines]
    stubs = """
const FETCHED=[],POLLED=[],INTERVALS=[],RUNS=[];
globalThis.window={squadStreamLive:undefined};
const el=()=>({hidden:true,handlers:{},addEventListener(t,f){this.handlers[t]=f}});
const EL={"#flowov":el(),"#fwl":el(),"#fsl":el(),"#fkl":el(),"#sse":el(),"#flowb":el()};
const $=s=>EL[s];
globalThis.setInterval=(f,ms)=>{INTERVALS.push(f)};
globalThis.fetch=async url=>{FETCHED.push(url);return {json:async()=>({t:1,cfd:{ranges:[]}})}};
let bo={},FLOWK=null,FLOWD={},CF={r:"30",ty:"task",hidden:[]},LT={tf:"30",log:false,ty:"task",pick:null,zc:null,zr:null,tok:0,a:"Todo",b:""};   // the cumulative flow's own Type and hidden states (#1356)
const ls={},save=()=>{},saveB=()=>{},pollBoard=()=>POLLED.push(1);
const drawFlow=()=>{},flowTabs=()=>{},poll=()=>RUNS.push(1);
const settle=()=>new Promise(r=>setTimeout(r,0));
"""
    script = stubs + "\n".join(page) + "\n" + onchange + "\n(async()=>{" + drive + "})().catch(e=>{console.error(e.stack);process.exit(1)});"
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_a_chart_event_redraws_only_the_open_chart(hub):
    out = _chart_harness(hub, """
await flowOpen("cfd");await settle();FETCHED.length=0;
squadChart("lead");await settle();
const lead=FETCHED.slice();
squadChart("cfd");await settle();
console.log(JSON.stringify({lead,cfd:FETCHED.slice()}));""")
    assert out["lead"] == [], "a lead event does not touch the open cumulative flow"
    assert out["cfd"] == [f"/api/chart/cfd?{BAR}"], "the open chart reads once by its own event"


@needs_node
def test_a_dropped_stream_is_named_and_the_drawn_state_stays(hub):
    out = _chart_harness(hub, """
await flowOpen("cfd");await settle();FETCHED.length=0;POLLED.length=0;
squadStreamLost();await settle();
console.log(JSON.stringify({sse:EL["#sse"].hidden,text:EL["#sse"].textContent,fetched:FETCHED.slice(),polled:POLLED.length}));""")
    assert out["sse"] is False, "the page names the dropped stream"
    assert "lost" in out["text"]
    assert out["fetched"] == [] and out["polled"] == 0, "a dropped stream reads nothing and clears nothing"


@needs_node
def test_a_reconnect_reads_each_open_subject_once(hub):
    out = _chart_harness(hub, """
await flowOpen("cfd");await settle();FETCHED.length=0;POLLED.length=0;
squadStreamLost();squadStreamBack();await settle();await settle();
console.log(JSON.stringify({sse:EL["#sse"].hidden,fetched:FETCHED.slice(),polled:POLLED.length,runs:RUNS.length}));""")
    assert out["sse"] is True, "a reconnect clears the named drop"
    assert out["fetched"] == [f"/api/chart/cfd?{BAR}"], "one read of the open chart"
    assert out["polled"] == 1, "one read of the board"
    assert out["runs"] == 1, "one read of the runs"


@needs_node
def test_with_no_live_stream_the_open_chart_is_polled_on_the_board_tick(hub):
    out = _chart_harness(hub, """
await flowOpen("cfd");await settle();FETCHED.length=0;POLLED.length=0;
INTERVALS[1]();await settle();
const noStream={fetched:FETCHED.slice(),polled:POLLED.length};
FETCHED.length=0;POLLED.length=0;window.squadStreamLive=true;
INTERVALS[1]();await settle();
const liveChart={fetched:FETCHED.slice(),polled:POLLED.length};
FETCHED.length=0;POLLED.length=0;INTERVALS[0]();await settle();
console.log(JSON.stringify({noStream,liveChart,board:POLLED.length}));""")
    assert out["noStream"] == {"fetched": [f"/api/chart/cfd?{BAR}"], "polled": 0}, "no stream: the open chart is re-read"
    assert out["liveChart"] == {"fetched": [], "polled": 0}, "a live stream: the tick reads no chart"
    assert out["board"] == 1, "the board tick is unchanged"


@needs_node
def test_the_page_stream_hands_each_newer_chart_version_to_the_page(hub):
    script = f"""
const CALLS=[],TIMERS=[],ES=[];
class FakeES{{constructor(u){{this.url=u;this.l={{}};this.closed=false;ES.push(this)}}addEventListener(t,f){{this.l[t]=f}}close(){{this.closed=true}}}}
globalThis.setTimeout=(f)=>{{TIMERS.push(f)}};
globalThis.EventSource=FakeES;
globalThis.window={{EventSource:FakeES,SQUAD_HUB_STREAM:"/alpha/api/stream",squadChart:k=>CALLS.push(["chart",k]),squadStreamLost:()=>CALLS.push(["lost"]),squadStreamBack:()=>CALLS.push(["back"])}};
globalThis.poll=()=>CALLS.push(["poll"]);
{(SCRIPTS / "hub-stream.js").read_text()}
const first=ES[0];
first.onopen();
first.l.cfd({{data:'{{"v":5}}'}});first.l.cfd({{data:'{{"v":5}}'}});first.l.lead({{data:'{{"v":7}}'}});
first.onerror();
TIMERS.shift()();
ES[1].onopen();
console.log(JSON.stringify({{calls:CALLS,urls:ES.map(e=>e.url),closed:first.closed,live:window.squadStreamLive}}));"""
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["calls"] == [["chart", "cfd"], ["chart", "lead"], ["lost"], ["back"]], "a repeated version is no second paint"
    assert out["urls"][1].endswith("?held=cfd:5,lead:7"), "a reconnect names the versions the page holds"
    assert out["closed"] is True and out["live"] is True
