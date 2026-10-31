"""Hub run panes: the page lets ONE embedded dashboard follow its run live, however many runs it shows.

A dashboard in a frame holds its own stream and its own timers on its run's server. One frame per pane made a cost that
grew with the run count. These drive the page's pane code in node over a fake document: N panes, a count of the frames
that hold a live source, and a press on another pane that moves the one live frame. The rule itself (panes.js) is
driven alone, with no document at all (ADR-0137)."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
PANES_JS = ROOT / "contrib/squad-goal/scripts/panes.js"
PANES_FROM = "let built="
PANES_TO = "function refresh("


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_one_live_pane", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _panes_slice(hub):
    return hub.PAGE[hub.PAGE.index(PANES_FROM):hub.PAGE.index(PANES_TO)]


_NODE_PRE = r"""
const __timers=[],__made=[];
const mkEl=tag=>{const e={tag,children:[],style:{},dataset:{},attrs:{},className:"",textContent:"",title:"",_html:"",
  appendChild(c){e.children.push(c);return c},addEventListener(t,f){e["on"+t]=f},setAttribute(k,v){e.attrs[k]=v}};
  Object.defineProperty(e,"innerHTML",{get:()=>e._html,set:v=>{e._html=v;e.children=[]}});return e};
const __el={"#main":mkEl("main"),"#pipf":mkEl("iframe")};
const $=s=>__el[s]||(__el[s]=mkEl(s));
globalThis.document={createElement:t=>{__made.push(t);return mkEl(t)}};
globalThis.setTimeout=(f,ms)=>{__timers.push(ms);return 1};
globalThis.setInterval=(f,ms)=>{__timers.push(ms);return 1};
const LAYOUTS={1:[1,1],2:[2,1],3:[3,1],4:[2,2],6:[3,2]};
const usd=v=>v==null?"-":"$"+v.toFixed(2),age=s=>s+"s";
const name=r=>r.name||("run "+String(r.id).slice(-6));
const PIP_NORUNS="No open runs.";
const bo={};
let N=4,runs=[],slots=[],frames=new Map(),pins=new Set();
const pipDraw=()=>{};
const mk=n=>Array.from({length:n},(_,i)=>({id:"run-"+(i+1),port:String(9001+i),state:"running",spend:i,age:i,name:"job "+(i+1)}));
"""

_NODE_POST = r"""
const walk=(e,out=[])=>{out.push(e);e.children.forEach(c=>walk(c,out));return out};
const live=()=>walk($("#main")).filter(e=>e.tag==="iframe"&&e.src&&e.src!=="about:blank").map(e=>e.src);
const state=()=>({live:live(),cells:$("#main").children.length,framed:$("#main").children.filter(c=>c.children.some(k=>k.tag==="iframe")).length,
  kept:frames.size,snaps:$("#main").children.map(c=>walk(c).filter(e=>e.dataset&&e.dataset.snap).map(e=>e.textContent).join("")),
  iframes:__made.filter(t=>t==="iframe").length,timers:__timers.length});
const press=i=>$("#main").children[i].onclick();
const out={};
SCRIPT
console.log("RESULT"+JSON.stringify(out));
"""


def _node(script):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.split("RESULT")[-1])


def _drive(hub, script):
    return _node(_NODE_PRE + _panes_slice(hub) + _NODE_POST.replace("SCRIPT", script))


@pytest.mark.parametrize("panes", [1, 2, 3, 4, 6])
def test_with_n_live_runs_on_the_page_at_most_one_pane_follows_its_run(hub, panes):
    st = _drive(hub, f"N={panes};runs=mk(9);slots=runs.slice(0,N).map(r=>r.id);drawMain();out.s=state();")["s"]
    assert st["cells"] == panes                                   # every slot still shows its run
    assert st["live"] == ["/dash/run-1/"]               # one live source, the focused pane's own run
    assert st["framed"] == 1


def test_selecting_another_pane_moves_the_one_live_stream_to_it(hub):
    st = _drive(hub, """N=4;runs=mk(9);slots=runs.slice(0,N).map(r=>r.id);drawMain();out.first=state();
      press(2);out.moved=state();press(0);out.back=state();""")
    assert st["first"]["live"] == ["/dash/run-1/"]
    assert st["moved"]["live"] == ["/dash/run-3/"]      # one stream, now on the third pane's run
    assert st["back"]["live"] == ["/dash/run-1/"]
    assert st["moved"]["framed"] == 1 and st["moved"]["cells"] == 4
    assert st["moved"]["kept"] == 1                               # the old pane's frame is dropped, not hidden



def test_an_unfocused_pane_shows_the_state_the_page_already_has_and_refreshes_without_a_frame(hub):
    st = _drive(hub, """N=2;runs=mk(3);slots=["run-1","run-2"];drawMain();out.a=state();
      runs[1].state="ended";runs[1].spend=7.5;drawMain();out.b=state();""")
    assert "running" in st["a"]["snaps"][1]                       # the card of the unfocused pane
    assert "ended" in st["b"]["snaps"][1] and "$7.50" in st["b"]["snaps"][1]
    assert st["b"]["iframes"] == st["a"]["iframes"] == 1          # a repaint builds no frame, and keeps the live one


def test_the_pages_cost_does_not_grow_with_the_number_of_runs(hub):
    small = _drive(hub, "N=6;runs=mk(2);slots=runs.map(r=>r.id);drawMain();out.s=state();")["s"]
    big = _drive(hub, "N=6;runs=mk(60);slots=runs.slice(0,6).map(r=>r.id);drawMain();press(3);press(5);out.s=state();")["s"]
    assert len(small["live"]) == 1 and len(big["live"]) == 1
    assert big["timers"] == small["timers"] == 0                  # the panes add no timer of their own
    assert big["iframes"] - small["iframes"] == 2                 # only the two presses built a frame; never one per run


def test_the_pane_rule_lives_in_a_file_that_needs_no_document(hub):
    assert PANES_JS.is_file()
    assert PANES_JS.read_text() in hub.PAGE                       # the page carries the rule as written
    st = _node(PANES_JS.read_text() + r"""
const out={};
out.one=embedPlan(["a","b","c"],"b",false);
out.none=embedPlan(["a","b","c"],null,false);
out.gone=embedPlan(["a","b","c"],"z",false);
out.empty=embedPlan([],null,false);
out.budget=LIVE_BUDGET;
out.counts=[0,1,2,6,40].map(n=>{const s=Array.from({length:n},(_,i)=>"r"+i),p=embedPlan(s,null);return (p.pane?1:0)});
console.log("RESULT"+JSON.stringify(out));
""")
    assert st["one"] == {"pane": "b"}                             # the focused pane follows its run
    assert st["none"] == {"pane": "a"}                            # no focus yet: the first pane
    assert st["gone"] == {"pane": "a"}                            # a focus that left the page falls back to the first
    assert st["empty"] == {"pane": None}
    assert st["budget"] == 1
    assert max(st["counts"]) <= st["budget"]   # N runs, never more than the budget


def test_a_missing_cap_file_cannot_make_the_page_expensive(hub):
    bare = tempfile.mkdtemp()                                       # a checkout with no project file: the machine-wide pair still resolves
    co = tempfile.mkdtemp()                                         # a checkout whose project file raises the ceiling: the resolution fails
    (Path(co) / "docs/squad").mkdir(parents=True)
    (Path(co) / "docs/squad/defaults.yaml").write_text("caps:\n  runs: 11\n")
    old = os.environ.get("SG_CHECKOUT")
    try:
        os.environ["SG_CHECKOUT"] = bare
        assert hub.loop_caps() is not None
        os.environ["SG_CHECKOUT"] = co
        assert hub.loop_caps() is None
    finally:
        os.environ["SG_CHECKOUT"] = old
    assert "CAPS" not in _panes_slice(hub)                          # the live budget reads no cap: one stream whatever the file says
    st = _drive(hub, "N=6;runs=mk(40);slots=runs.slice(0,6).map(r=>r.id);drawMain();out.s=state();")["s"]
    assert len(st["live"]) == 1                                     # the harness's CAPS is null, as when the file is missing
