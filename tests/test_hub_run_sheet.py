"""A run opens in a side sheet, as the approved mock draws it (SPEC 889, #984).

A click on a run in Live work or Last ended opens the sheet; the in-page run pane (an embedded dashboard frame) is gone.
The sheet reads only what the hub already serves for a run: the run row, and the record through the `/dash/` proxy.
Its one write is the pause request, through the run dashboard's own control route.

The sheet's model is `run-sheet.js`, a text the hub inlines into the page. These tests run it in node, exactly as the page
carries it, and read the served page for the wiring.
"""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
SHEET_JS = SCRIPTS / "run-sheet.js"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_run_sheet")
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir()
    for k, v in {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": str(base / "checkout"),
                 "SG_HUB_DIR": str(base / "hub"), "SG_STATE": str(base / "state")}.items():
        mp.setenv(k, v)
    mp.delenv("SG_BOARD_CACHE", raising=False)
    try:
        spec = importlib.util.spec_from_file_location("hub_run_sheet", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        mp.undo()


def _node(script):
    if NODE is None:
        pytest.skip("node is required")
    src = SHEET_JS.read_text() + "\n" + script
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


ROW_LIVE = {"id": "run-20261031-203643-606a7a", "port": "54625", "state": "running", "label": "implement #980",
            "issues": ["980"], "rank": 0, "spend": 3.8, "dur": 21.4, "running": True}
ROW_ENDED = {**ROW_LIVE, "id": "run-20261031-203926-d864b3", "port": "", "state": "completed", "rank": 1,
             "issues": ["981", "982"], "spend": 3.2089, "dur": 73.2, "running": False}

T0 = 1793489966.0
RECORD = [
    {"event": "run_start", "run_id": ROW_ENDED["id"], "graph_name": "router-dynamic",
     "plan": ["main", "route", "router", "scope_gate", "review_gate"], "pos": 1, "ts": T0},
    {"event": "call_start", "node": "router", "entry": 1, "pos": 2, "ts": T0 + 1},
    {"event": "progress", "node": "router", "entry": 1, "messages": 3, "context_tokens": 21431, "pos": 3, "ts": T0 + 10},
    {"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "reasons": ["chose tdd"], "model": "deepseek",
     "spend": 0.25, "tokens_in": 21385, "tokens_out": 3319, "pos": 4, "ts": T0 + 20},
    {"event": "gate_start", "node": "scope_gate", "entry": 1, "pos": 5, "ts": T0 + 21},
    {"event": "gate_start", "node": "review_gate", "entry": 1, "pos": 6, "ts": T0 + 21},
    {"event": "call_start", "node": "route.router.1", "entry": 1, "pos": 7, "ts": T0 + 22},
    {"event": "progress", "node": "route.router.1", "entry": 1, "messages": 9, "context_tokens": 142000, "pos": 8, "ts": T0 + 60},
    {"event": "sandbox_deny", "node": "route.router.1", "reason": "Write outside the worktree", "pos": 9, "ts": T0 + 70},
    {"event": "outcome", "node": "route.router.1", "entry": 1, "outcome": "pass", "model": "haiku", "spend": 1.75,
     "tokens_in": 2000000, "tokens_out": 32000, "pos": 10, "ts": T0 + 300},
    {"event": "review_round", "node": "route.router.1", "round": 1, "verdict": "fix", "pos": 11, "ts": T0 + 380},
    {"event": "outcome", "node": "scope_gate", "entry": 1, "outcome": "pass", "pos": 12, "ts": T0 + 400},
    {"event": "outcome", "node": "review_gate", "entry": 1, "outcome": "fail", "reasons": ["CHANGES"], "pos": 13, "ts": T0 + 401},
]


def _model(row=ROW_ENDED, record=RECORD):
    return _node(f"console.log(JSON.stringify(rkModel({json.dumps(row)},{json.dumps(record)})));")


# ---- the model: chips ----

def test_chips_say_live_or_ended_and_carry_id_cost_and_elapsed():
    live = _model(ROW_LIVE, RECORD[:3])["chips"]
    assert live["state"] == "live" and live["id"] == ROW_LIVE["id"]
    assert live["cost"] == "$3.80" and live["el"] == "21 min"
    ended = _model()["chips"]
    assert ended["state"] == "ended" and ended["cost"] == "$3.21" and ended["el"] == "1 h 13 min"


def test_issues_are_the_row_issues_as_numbers():
    assert _model()["issues"] == [981, 982]


# ---- the model: nodes, gates and models ----

def test_nodes_follow_the_plan_then_the_children_and_mark_the_gates():
    nodes = _model()["nodes"]
    assert [n["n"] for n in nodes] == ["main", "route", "router", "scope_gate", "review_gate", "route.router.1"]
    by = {n["n"]: n for n in nodes}
    assert by["scope_gate"]["gate"] and by["review_gate"]["gate"] and not by["router"]["gate"]
    assert by["router"]["st"] == "done" and by["review_gate"]["st"] == "fail" and by["main"]["st"] == "pend"


def test_a_started_node_runs_while_the_run_is_live():
    by = {n["n"]: n for n in _model(ROW_LIVE, RECORD[:8])["nodes"]}
    assert by["route.router.1"]["st"] == "run" and by["scope_gate"]["st"] == "run"


def test_model_rows_sum_calls_tokens_and_spend_per_model_dearest_first():
    m = _model()
    assert [r["m"] for r in m["models"]] == ["haiku", "deepseek"]
    haiku = m["models"][0]
    assert haiku["calls"] == 1 and haiku["tin"] == 2000000 and haiku["tout"] == 32000
    assert abs(haiku["usd"] - 1.75) < 1e-9 and haiku["nodes"] == ["route.router.1"]
    assert abs(m["total"]["usd"] - 2.0) < 1e-9 and m["total"]["calls"] == 2


# ---- the model: the trace ----

def test_events_are_newest_first_with_a_level_and_the_model_of_their_call():
    ev = _model()["ev"]
    assert len(ev) == len(RECORD) and ev[0]["n"] == "review_gate"
    lv = {(e["n"], e["x"].split(" ")[0]): e["l"] for e in ev}
    assert ev[0]["l"] == "error", "a failed outcome is an error"
    assert lv[("route.router.1", "sandbox_deny")] == "warn"
    assert lv[("scope_gate", "gate")] == "gate"
    assert lv[("route.router.1", "review")] == "gate"
    prog = [e for e in ev if e["n"] == "router" and e["x"].startswith("progress")][0]
    assert prog["m"] == "deepseek" and prog["l"] == "info", "a progress event carries the model of its call"


def test_the_trace_filters_by_node_model_and_level():
    out = _node(f"const m=rkModel({json.dumps(ROW_ENDED)},{json.dumps(RECORD)});console.log(JSON.stringify({{n:rkFilter(m.ev,{{n:'router'}}).length,"
                "m:rkFilter(m.ev,{m:'haiku'}).length,e:rkFilter(m.ev,{l:'error'}).length,w:rkFilter(m.ev,{l:'warn'}).length,"
                "g:rkFilter(m.ev,{l:'gate'}).length,all:rkFilter(m.ev,{}).length}));")
    assert out == {"n": 3, "m": 3, "e": 1, "w": 2, "g": 4, "all": len(RECORD)}


# ---- the sheet body: tabs and the controls that filter ----

def _html(tab, f=None):
    return _node(f"const m=rkModel({json.dumps(ROW_ENDED)},{json.dumps(RECORD)});console.log(JSON.stringify(rkHtml(m,{json.dumps(tab)},{json.dumps(f or {})})));")


def test_overview_has_two_tabs_issue_links_focusable_nodes_and_model_rows():
    h = _html("ov")
    tabs = re.findall(r'<button role="tab" data-rkt="(\w+)" aria-selected="(\w+)"', h)
    assert tabs == [("ov", "true"), ("tr", "false")]
    assert 'data-o="981"' in h and 'data-o="982"' in h
    assert re.search(r'class="gn[^"]*gate[^"]*" data-n="review_gate" tabindex="0" role="button"', h)
    assert re.search(r'class="mrow" data-m="haiku" tabindex="0" role="button"', h)


def test_trace_draws_the_filters_and_only_the_matching_events():
    h = _html("tr", {"n": "router"})
    for k in ("n", "m", "l"):
        assert f'data-rkf="{k}"' in h
    assert '<option value="router" selected>' in h
    assert h.count('class="te ') == 3 and 'data-rkf="clear"' in h
    assert "3 of 13" in h


def test_the_body_escapes_what_the_record_says():
    rec = RECORD[:1] + [{"event": "outcome", "node": "<b>x</b>", "outcome": "fail", "reasons": ["<img src=x>"], "ts": T0}]
    h = _node(f"const m=rkModel({json.dumps(ROW_ENDED)},{json.dumps(rec)});console.log(JSON.stringify(rkHtml(m,'tr',{{}})));")
    assert "<img" not in h and "<b>x</b>" not in h


# ---- the page: the sheet replaces the run pane ----

def test_the_page_has_the_run_sheet_and_no_run_pane(hub):
    page = hub.PAGE
    assert re.search(r"<div id=rk role=dialog aria-label=Run>", page)
    for k in ("id=rkchips", "id=rkx", "id=rkb", "id=rkgh", "id=rkpause"):
        assert k in page
    assert "createElement(\"iframe\")" not in page and "<iframe" not in page, "the page still embeds a run dashboard"
    assert "embedPlan(" not in page, "the pane plan is still inlined"
    assert "function rkModel(" in page, "the sheet model is not inlined"
    assert "id=bflow" in page and "id=blead" in page and "id=tab-flow" in page, "the Flow charts tab lost a chart"


def test_live_and_ended_rows_open_the_sheet(hub):
    page = hub.PAGE
    draw = page[page.index("function drawMain("):page.index("function refresh(")]
    assert "data-rid=" in draw, "a live run row does not open the sheet"   # the #main handler opens a data-rid row (#1770)
    ended = page[page.index("async function endedPoll("):]
    ended = ended[:ended.index("\n")]
    # the ended rows feed the KPI and the sheet, not a panel: the Last ended card left the sidebar (owner, 2026-11-01)
    assert "ENDED=rows" in ended and "drawMain()" in ended, "the ended rows no longer reach the board's figures"
    assert '$("#ended")' not in page, "a script still writes the removed Last ended panel"


def test_the_sheet_reads_the_record_and_pauses_through_the_existing_routes(hub):
    page = hub.PAGE
    assert '"/dash/"+encodeURIComponent(id)+"/run/record.jsonl"' in page
    assert '"/dash/"+encodeURIComponent(id)+"/run/control"' in page
    assert '"/ended/"+encodeURIComponent(id)' in page
    src = HUB_PY.read_text()
    routes = ('self.path in ("/api/models", "/api/plans", "/api/promote", "/api/limits", "/api/columns", '
              '"/api/slots", "/api/slack/files", "/api/personas", "/api/personas/remove", '
              '"/api/slack/preview", "/api/slack/sample", '
              '"/api/slack/clients", "/api/slack/people", "/api/slack/channels", "/api/slack/owner", '
              '"/api/slack/grant", "/api/sync")')
    assert routes in src, "the write routes changed"
    # /api/columns: Spend & limits saves the column limits (#2432); /api/slots: its slots sub-group (#2435);
    # /api/slack/files: the Communication save and its one edit (#2694, #2696);
    # /api/personas: the Personas theme's preview and its confirmed save (#3011);
    # /api/personas/remove is registered here alone; the dispatcher and the answer it returns are proven over loopback
    # in tests/test_hub_personas_edit.py (#3014);
    # /api/slack/people: the members editor resolves an @name to a Slack user (#2911);
    # /api/slack/preview and /api/slack/sample: the persona editor's reply preview and voice sample (#2697);
    # /api/slack/clients: the client default's proposed writes and its save (#2827);
    # the old settings route is retired (#2701)


def test_keyboard_and_clicks_go_through_the_sheet_controls(hub):
    """The page wires the controls the node tests below exercise: a key, a click, Escape and every paint."""
    page = hub.PAGE
    assert re.search(r'\$\("#rkb"\)\.addEventListener\("keydown",e=>\{const a=rkKey\(', page), "a key on the sheet skips rkKey"
    assert re.search(r'\$\("#rkb"\)\.addEventListener\("click",e=>\{[^\n]*rkAct\(', page), "a click on the sheet skips rkAct"
    esc = page[page.index('addEventListener("keydown",e=>{const w=rkEscape('):]
    assert "closeRun()" in esc[:300]
    paint = page[page.index("function rkPaint("):page.index("async function rkLoad(")]
    assert "rkRender(" in paint and ".innerHTML=" not in paint.split("rkRender(")[1].split(";")[0], "a paint writes the body around rkRender"
    follow = page[page.index("function rkFollow("):]
    assert "rkPaint(true)" not in follow[:follow.index("\n")], "a poll forces a repaint"


# ---- the controls: behavior, on a small stub of the page ----

STUB = r"""
function mk(tag,attrs,doc){const e={tagName:tag.toUpperCase(),attrs,dataset:{},cls:(attrs.class||"").split(/\s+/).filter(Boolean),
  getAttribute(a){return a in this.attrs?this.attrs[a]:null},
  matches(sel){return sel.split(",").some(s=>{const m=s.trim().match(/^([a-z]*)((?:\.[\w-]+)*)((?:\[[\w-]+(?:=[^\]]+)?\])*)$/);if(!m)throw Error("selector "+s);
    if(m[1]&&m[1].toUpperCase()!==this.tagName)return false;
    for(const c of m[2].split(".").filter(Boolean))if(!this.cls.includes(c))return false;
    for(const [,a,v] of m[3].matchAll(/\[([\w-]+)(?:=([^\]]+))?\]/g)){const x=this.getAttribute(a);if(x==null)return false;if(v!=null&&x!==v.replace(/^"|"$/g,""))return false}
    return true})},
  closest(sel){return this.matches(sel)?this:null},
  focus(){doc.activeElement=this}};
  for(const [k,v] of Object.entries(attrs))if(k.startsWith("data-"))e.dataset[k.slice(5)]=v;return e}
function stubBody(doc){let html="",kids=[];const b={writes:0,scrollTop:0,
  get innerHTML(){return html},
  set innerHTML(h){html=h;b.writes++;kids=[...h.matchAll(/<(\w+)([^>]*)>/g)].map(([,t,a])=>mk(t,Object.fromEntries([...a.matchAll(/([\w-]+)="([^"]*)"/g)].map(x=>[x[1],x[2]])),doc))},
  contains(el){return kids.includes(el)},querySelector(sel){return kids.find(k=>k.matches(sel))||null}};return b}
const doc={activeElement:null},b=stubBody(doc);
const html=(row,rec,tab,f)=>rkHtml(rkModel(row,rec),tab,f||{});
"""


def _stub(script):
    return _node(STUB + script)


def test_a_poll_never_repaints_an_unchanged_sheet_so_the_focus_stays():
    """Review finding 1: every 3 s poll replaced the body, so a focused node lost its focus and Enter acted on nothing."""
    out = _stub(f"""const row={json.dumps(ROW_ENDED)},rec={json.dumps(RECORD)};
rkRender(b,html(row,rec,'ov'),doc);const g=b.querySelector('span[data-n="router"]');g.focus();
const again=rkRender(b,html(row,rec,'ov'),doc);rkRender(b,html(row,rec,'ov'),doc);
console.log(JSON.stringify({{again,writes:b.writes,same:doc.activeElement===g}}));""")
    assert out == {"again": False, "writes": 1, "same": True}, "an unchanged ended run is repainted by the poll"


def test_a_changed_body_puts_the_focus_back_on_the_same_node():
    out = _stub(f"""const row={json.dumps(ROW_LIVE)},rec={json.dumps(RECORD[:8])};
rkRender(b,html(row,rec,'ov'),doc);const g=b.querySelector('span[data-n="scope_gate"]');g.focus();
rkRender(b,html(row,{json.dumps(RECORD)},'ov'),doc);
const a=doc.activeElement;console.log(JSON.stringify({{writes:b.writes,moved:a!==g,n:a&&a.dataset.n,inside:b.contains(a)}}));""")
    assert out == {"writes": 2, "moved": True, "n": "scope_gate", "inside": True}, "a live repaint drops the focused node"


def test_a_poll_never_closes_a_filter_being_chosen_but_the_choice_paints():
    out = _stub(f"""const row={json.dumps(ROW_LIVE)};
rkRender(b,html(row,{json.dumps(RECORD[:8])},'tr'),doc);const s=b.querySelector('select[data-rkf="m"]');s.focus();
const poll=rkRender(b,html(row,{json.dumps(RECORD)},'tr'),doc);const w=b.writes;
const pick=rkRender(b,html(row,{json.dumps(RECORD)},'tr',{{m:'haiku'}}),doc,true);const a=doc.activeElement;
console.log(JSON.stringify({{poll,w,pick,writes:b.writes,f:a&&a.dataset.rkf,inside:b.contains(a)}}));""")
    assert out == {"poll": False, "w": 1, "pick": True, "writes": 2, "f": "m", "inside": True}


def test_enter_or_space_on_a_node_or_a_model_row_filters_the_trace():
    out = _stub(f"""rkRender(b,html({json.dumps(ROW_ENDED)},{json.dumps(RECORD)},'ov'),doc);const st={{tab:'ov',f:{{}}}};
const g=b.querySelector('span[data-n="review_gate"]'),m=b.querySelector('div[data-m="haiku"]'),i=b.querySelector('a[data-o="981"]');
console.log(JSON.stringify({{enter:rkKey(st,g,'Enter'),space:rkKey(st,m,' '),other:rkKey(st,g,'x'),link:rkKey(st,i,'Enter'),click:rkAct(st,i),
  tab:rkAct(st,b.querySelector('button[data-rkt="tr"]'))}}));""")
    assert out["enter"] == {"st": {"tab": "tr", "f": {"n": "review_gate"}}}
    assert out["space"] == {"st": {"tab": "tr", "f": {"m": "haiku"}}}
    assert out["other"] is None and out["link"] is None, "a key other than Enter or Space, or on a link, filters"
    assert out["click"] == {"o": 981} and out["tab"] == {"st": {"tab": "tr", "f": {}}}


def test_escape_closes_the_run_sheet_before_the_ticket_sheet():
    out = _node("console.log(JSON.stringify([rkEscape('Escape',true,true),rkEscape('Escape',false,true),rkEscape('Escape',false,false),rkEscape('Enter',true,true)]));")
    assert out == ["run", "ticket", None, None]


def test_a_pin_from_before_the_sheet_is_neither_restored_nor_drawn(hub):
    """Review finding 3: a click no longer pins, so a pin restored from localStorage marked its row for good."""
    page = hub.PAGE
    assert "pins=new Set((ls.pins" not in page, "the old pins are restored from localStorage"
    draw = page[page.index("function drawList("):page.index("function refresh(")]
    assert '"pinned"' not in draw, "a run row is still drawn pinned"


def test_the_runs_tab_lists_runs_that_open_the_sheet(hub):
    """The Runs tab lists the runs as rows that open the sheet, never a frame."""
    page = hub.PAGE
    assert 'data-t=runs role=tab>Runs' in page and "id=bruns" not in page and "<div id=main>" in page
    pane = page[page.index("id=tab-runs"):page.index("</section>", page.index("id=tab-runs"))]
    draw = page[page.index("function drawMain("):page.index("function refresh(")]
    assert "data-rid=" in draw and "iframe" not in draw
    assert "iframe" not in pane, "the Runs pane embeds a frame"
    assert re.search(r'\$\("#main"\)\.addEventListener\("click"[^\n]*openRun\(', page)


def test_a_plan_node_with_no_event_is_still_a_trace_filter_option():
    rec = [{"event": "run_start", "plan": ["main", "router"], "graph_name": "g", "ts": T0},
           {"event": "call_start", "node": "router", "entry": 0, "ts": T0 + 1}]
    h = _node(f"const m=rkModel({json.dumps(ROW_LIVE)},{json.dumps(rec)});console.log(JSON.stringify(rkHtml(m,'tr',{{n:'main'}})));")
    assert '<option value="main" selected>' in h, "a node picked in the Overview is missing from the node filter"


def test_the_header_chips_shrink_so_the_close_button_stays_on_their_row():
    css = (SCRIPTS / "run-sheet.css").read_text()
    chips = re.search(r"#rkchips\{([^}]*)\}", css)
    assert chips, "the run sheet has no #rkchips rule"
    assert "flex-wrap:wrap" not in chips.group(1), "the chips wrap, so the close button drops to a second row"
    assert "min-width:0" in chips.group(1), "the chips row cannot shrink below its content"


def test_overview_shows_the_wall_time_split_and_a_suite_run_again_on_one_tree():
    rec = [
        {"event": "run_start", "graph_name": "router-dynamic", "plan": ["router"], "pos": 1, "ts": T0},
        {"event": "call_start", "node": "route.router.1", "entry": 1, "session": "s", "pid": 1, "limit": None, "started_at": T0, "pos": 2, "ts": T0 + 1},
        {"event": "progress", "node": "route.router.1", "entry": 1, "messages": 2, "context_tokens": 1, "tool": "Bash",
         "command_class": "suite", "phase": "end", "elapsed": 1800.0, "pos": 3, "ts": T0 + 60},
        {"event": "progress", "node": "route.router.1", "entry": 1, "messages": 3, "context_tokens": 1, "tool": "Bash",
         "command_class": "gate", "phase": "end", "elapsed": 600.0, "pos": 4, "ts": T0 + 70},
        {"event": "gate_start", "node": "test_gate", "entry": 1, "pid": 1, "limit": None, "started_at": T0 + 100, "pos": 5, "ts": T0 + 100},
        {"event": "gate_result", "node": "test_gate", "entry": 1, "item": None, "sha": "abc", "dirt": "d1", "suite": "fast",
         "outcome": "fail", "source": "test_gate.1", "pos": 6, "ts": T0 + 400},
        {"event": "gate_start", "node": "test_gate", "entry": 1, "pid": 1, "limit": None, "started_at": T0 + 500, "pos": 7, "ts": T0 + 500},
        {"event": "gate_result", "node": "test_gate", "entry": 1, "item": None, "sha": "abc", "dirt": "d1", "suite": "fast",
         "outcome": "fail", "source": "test_gate.1", "pos": 8, "ts": T0 + 800},
        {"event": "outcome", "node": "test_gate", "entry": 1, "outcome": "fail", "elapsed": 700.0, "pos": 9, "ts": T0 + 800},
        {"event": "outcome", "node": "route.router.1", "entry": 1, "outcome": "pass", "elapsed": 3000.0, "pos": 10, "ts": T0 + 3000},
        {"event": "verdict", "verdict": "completed", "pos": 11, "ts": T0 + 4000},
    ]
    out = _node(f"const m=rkModel({json.dumps(ROW_ENDED)},{json.dumps(rec)});console.log(JSON.stringify({{split:m.split,html:rkOverview(m)}}));")
    assert out["split"] == {"wall": 4000, "suite": 2500, "gate_wait": 600, "model": 600, "idle": 300}
    assert "42 min" in out["html"] and "fast ×2, 10 min" in out["html"]
