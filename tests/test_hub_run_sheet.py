"""A run opens in a side sheet, as the approved mock draws it (SPEC 889, #984).

A click on a run in Live runs or Last ended opens the sheet; the in-page run pane (an embedded dashboard frame) is gone.
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
    draw = page[page.index("function drawList("):page.index("function refresh(")]
    assert "openRun(" in draw, "a live run row does not open the sheet"
    ended = page[page.index("async function endedPoll("):]
    ended = ended[:ended.index("\n")]
    assert "data-rid=" in ended and "target=_blank" not in ended, "a Last ended row still opens a new tab"
    assert re.search(r'\$\("#ended"\)\.addEventListener\("click"', page)


def test_the_sheet_reads_the_record_and_pauses_through_the_existing_routes(hub):
    page = hub.PAGE
    assert '"/dash/"+encodeURIComponent(id)+"/run/record.jsonl"' in page
    assert '"/dash/"+encodeURIComponent(id)+"/run/control"' in page
    assert '"/ended/"+encodeURIComponent(id)' in page
    src = HUB_PY.read_text()
    assert 'self.path in ("/api/models", "/api/plans", "/api/promote", "/api/limits", "/api/slack")' in src, "the write routes changed"


def test_keyboard_filters_on_enter_or_space_and_escape_closes(hub):
    page = hub.PAGE
    assert re.search(r'\$\("#rkb"\)\.addEventListener\("keydown".*?e\.key==="Enter"\|\|e\.key===" "', page)
    esc = page[page.index('addEventListener("keydown",e=>{if(e.key==="Escape")'):]
    assert "closeRun()" in esc[:400]


def test_the_runs_tab_lists_runs_that_open_the_sheet(hub):
    """The Runs chart tab stays (it is renamed elsewhere); it lists the runs as rows that open the sheet, never a frame."""
    page = hub.PAGE
    assert "id=bruns" in page and "<div id=main>" in page
    draw = page[page.index("function drawMain("):]
    draw = draw[:draw.index("\n}\n")]
    assert "data-rid=" in draw and "iframe" not in draw
    assert re.search(r'\$\("#main"\)\.addEventListener\("click"[^\n]*openRun\(', page)


def test_a_plan_node_with_no_event_is_still_a_trace_filter_option():
    rec = [{"event": "run_start", "plan": ["main", "router"], "graph_name": "g", "ts": T0},
           {"event": "call_start", "node": "router", "entry": 0, "ts": T0 + 1}]
    h = _node(f"const m=rkModel({json.dumps(ROW_LIVE)},{json.dumps(rec)});console.log(JSON.stringify(rkHtml(m,'tr',{{n:'main'}})));")
    assert '<option value="main" selected>' in h, "a node picked in the Overview is missing from the node filter"
