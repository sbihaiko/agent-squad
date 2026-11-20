"""Hub left pane: both accordions open and close only on the user's click (#1796)."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SRC = HUB_PY.read_text()

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_run_groups", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_needs_human_without_a_live_process_is_not_in_running(hub):
    ended = hub.group_of({"state": "needs-human", "age": 120, "live": False})
    live = hub.group_of({"state": "running", "age": 120, "live": True})
    assert ended != "running"
    assert live == "running"


def test_live_running_starting_stalled_are_in_running(hub):
    for st in ("running", "starting", "stalled"):
        assert hub.group_of({"state": st, "age": 60, "live": True}) == "running"


def test_the_answer_carries_the_live_group_alone(hub):
    """No page draws an age band any more (#1778): the hub builds the Live header and nothing else. An ended run fills no
    group, so its spend is in no header."""
    rows = [
        {"state": "running", "live": True, "spend": 1.0, "agents": 1},
        {"state": "completed", "live": False, "age": 600, "spend": 2.5, "agents": 0},
    ]
    groups = hub.run_groups(rows, {"runs": 6, "children": 20})
    assert groups == [{"key": "running", "label": "WORKERS", "text": "RUNS 1/6 · AGENTS 1/20", "title": "$1.00", "empty": False}]


def test_run_groups_no_longer_builds_an_age_band_or_a_muted_line(hub):
    """The ladder and its 'no runs in ...' line leave with the bands they named (#1778)."""
    assert not hasattr(hub, "run_muted") and not hasattr(hub, "RUN_GROUPS")
    assert [g["key"] for g in hub.run_groups([], None)] == ["running"]


def test_the_answer_names_no_rows_band_and_keeps_the_bare_array_alone(hub, monkeypatch):
    """No page places a row in a band any more (#1778), so the answer carries no per-row `band` and no muted line. The rows
    are the bare array's own, never copies given a page's field."""
    live = dict(LIVE)
    ended = dict(HOUR_2)
    cc = {"id": "childcc", "kind": "childcc", "state": "waiting", "live": False, "rank": 0, "age": 4000, "spend": 0, "agents": 0}
    monkeypatch.setattr(hub, "run_list", lambda: [live, ended, cc])
    monkeypatch.setattr(hub, "loop_caps", lambda: {"runs": 6, "children": 20})
    answer = hub.runs_answer()
    assert set(answer) == {"runs", "groups"}, "the answer carries a field no page reads"
    assert not any("band" in r for r in answer["runs"])
    assert answer["runs"][0] is live, "the rows are the bare list's own, not copies for a page"


def test_a_changed_cap_shows_after_one_answer(hub, monkeypatch):
    """The caps are read on every answer, not substituted at page load (#1277)."""
    monkeypatch.setattr(hub, "run_list", lambda: [LIVE])
    monkeypatch.setattr(hub, "loop_caps", lambda: {"runs": 6, "children": 20})
    assert hub.runs_answer()["groups"][0]["text"] == "RUNS 1/6 · AGENTS 1/20"
    monkeypatch.setattr(hub, "loop_caps", lambda: {"runs": 3, "children": 10})
    assert hub.runs_answer()["groups"][0]["text"] == "RUNS 1/3 · AGENTS 1/10"


# --- the page's own code, run in node: the list and the three sections are read off the DOM ---


def test_fn_finds_an_async_declaration_whatever_its_whitespace():
    """The page harness extracts a whole declaration, never a span misaligned by the async modifier's spacing (#1277 debt)."""
    src = "let a=1;\nasync\nfunction poll(){return 1}\nconst b=2;"
    span = _fn(src, "poll")
    assert re.match(r"async\s+function\s+poll\s*\(", span), "the extracted span drops or misplaces the async modifier"
    assert span.endswith("}") and "return 1" in span


def _fn(src, name):
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", src)   # the declaration is located as a whole, never by a fixed lookbehind that a different modifier spacing would misalign
    if m is None:
        raise AssertionError(f"function {name} is not declared")
    i = m.start()
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i : k + 1]
    raise AssertionError(f"function {name} is not closed")


def _decl(src, name):
    i = src.index(f"const {name}=")
    depth, k = 0, i + len(f"const {name}=")
    while k < len(src):
        c = src[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            return src[i : k + 1]
        k += 1
    raise AssertionError(f"const {name} is not closed")


def _slice(src, start, end):
    i = src.index(start)
    return src[i : src.index(end, i)]


def _pieces():
    rows = SRC[SRC.index("// runs of one issue share the issue label") : SRC.index("function drawList(")]   # name, short and the Live work card
    acc = SRC[SRC.index("const ACC=") :]
    acc = acc[: acc.index("if(RO){")]   # the accordion state and its boot; the read-only pass is not part of it (#1278)
    return [
        _decl(SRC, "usd"),
        _decl(SRC, "age"),
        _decl(SRC, "esc"),
        _decl(SRC, "iq"),
        _decl(SRC, "save"),
        _decl(SRC, "label"),
        _decl(SRC, "labelTip"),
        _decl(SRC, "sub"),
        _fn(SRC, "loadMark"),
        _fn(SRC, "plan"),
        rows,
        _fn(SRC, "drawList"),
        SRC[SRC.index("function drawMain(") : SRC.index("function refresh(")],   # the Runs tab's one list and its row forms (#1770)
        _fn(SRC, "selectNew"),
        _fn(SRC, "applyFilter"),
        _fn(SRC, "poll"),
        _fn(SRC, "livePoll"),   # the poll is one statement followed by its own live read (#1697): the harness carries both
        acc,
    ]


_DOM = """
const STORE = {};
function boot(){
  const Q = []; const DOM = {};   // the queue is the browser's: a programmatic .open fires toggle as a later task
  class El {
    constructor(id){ this.id = id; this._o = false; this.dataset = {}; this.L = {}; this._html = ""; this.innerHTML = ""; }
    get open(){ return this._o; }
    set open(v){ v = !!v; if (v === this._o) return; this._o = v; if (this.bound) Q.push(this); }
    addEventListener(t, f){ (this.L[t] = this.L[t] || []).push(f); this.bound = true; }
    fire(t){ (this.L[t] || []).forEach((f) => f({ target: this })); }
    closest(){ return null; }
    querySelectorAll(){ return []; }   // the card's own stacking pass reads its rows' chips; this stub holds none (#2507)
    set innerHTML(v){
      this._html = String(v);
    }
    get innerHTML(){ return this._html; }
  }
  const $ = (s) => { const id = String(s).replace(/^#/, ""); return DOM[id] || (DOM[id] = new El(id)); };
  const document = {
    querySelector: $,
    querySelectorAll: () => [],
  };
  const localStorage = STORE;   // the whole process shares it, so a second boot() is a reload
  const CALLS = { mdlLoad: 0, limLoad: 0 };
  function mdlLoad(){ CALLS.mdlLoad++; }
  function limLoad(){ CALLS.limLoad++; }   // Settings > Spend & limits (#926) answers its own header, like squad models
  const ls = JSON.parse(localStorage.hub || "{}");
  let allRuns = [], runs = [], N = 1, pins = new Set(), slots = [], known = null, liveWork = null, TAB = "runs", ENDED = [];
  let q = ls.q || "", per = ls.per || "day", src = ls.src || "both";
  let GROUPS = [];   // the answer's group entries, as the runs answer carries them (#1277)
  let FETCH = {}, FETCHED = [];   // what each poll's fetch answers, and the exact url it asked
  async function fetch(u){ FETCHED.push(u); return { json: async () => FETCH[u] }; }
  function refresh(){ plan(); drawList(); drawMain(); }
@@PIECES@@
  function drain(){ let n = 0; while (Q.length) { if (++n > 100) throw new Error("toggle storm"); Q.shift().fire("toggle"); } }
  return {
    runs: (rows, groups) => { allRuns = rows; runs = rows; GROUPS = groups || []; },
    draw: () => { drawList(); drawMain(); },
    poll: () => { selectNew(); refresh(); },
    wire: () => poll(),   // the page's own poll, over the fetch answers setFetch gave
    setFetch: (o) => { FETCH = o },
    fetched: () => FETCHED,
    count: () => allRuns.length,
    mainHtml: () => $("#main").innerHTML,
    clickAcc: (id) => { const e = $("#" + id); e.open = !e.open; drain(); },
    accOpen: () => ["stats", "mdl", "lim", "slk"].filter((i) => $("#" + i).open),
    calls: () => CALLS,
    store: () => JSON.parse(STORE.hub || "{}"),
  };
}
""".replace("@@PIECES@@", "\n".join(_pieces()))


def _run(js):
    assert NODE, "node is not installed: the hub page's own drawList is run here"
    script = _DOM + "\nconsole.log(JSON.stringify((()=>{" + js + "})()));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr or p.stdout
    return json.loads(p.stdout)


def _run_async(js):
    """As _run, for a page function that answers a promise: the poll reads its fetch before it draws."""
    assert NODE, "node is not installed: the hub page's own poll is run here"
    script = _DOM + "\n(async()=>{const o=await (async()=>{" + js + "})();console.log(JSON.stringify(o))})();"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr or p.stdout
    return json.loads(p.stdout)


def _run_row(tag, rank, age, **kw):
    r = {"id": f"run-20261011-1010{tag}0-abcd", "rank": rank, "age": age, "spend": 1.0, "agents": 1, "dur": 4, "port": 1234, "state": "running" if rank == 0 else "completed"}
    r.update(kw)
    return r


LIVE = _run_row("1", 0, 10)             # running
HOUR_1 = _run_row("2", 1, 600)          # 1h
HOUR_2 = _run_row("3", 1, 4000)         # 2h


def _js(rows, body, groups=None):
    return f"const R={json.dumps(rows)};const G={json.dumps(groups or [])};" + body


def test_a_waiting_childcc_pass_is_a_worker_and_never_counted(hub):
    """The pass is a worker, never a run: it never fills an age band, and the occupancy never counts it (#1277)."""
    cc = {"id": "childcc", "kind": "childcc", "state": "waiting", "live": False, "rank": 0, "age": 4000, "spend": 0, "agents": 0}
    groups = hub.run_groups([LIVE, cc], {"runs": 6, "children": 20})
    assert [g["key"] for g in groups if not g["empty"]] == ["running"], "the pass leaves every age band empty"
    assert groups[0]["text"] == "RUNS 1/6 · AGENTS 1/20", "the occupancy counts the pass"


def test_the_poll_answers_both_wire_shapes_without_blanking_the_list(hub):
    """A hub older than the page ignores the query and answers the bare array: the live list still reads (#1277)."""
    rows = [LIVE, HOUR_1]
    out = _run_async(f"""
      const a = boot();
      const page = {{runs: {json.dumps(rows)}, groups: {json.dumps(hub.run_groups(rows))}}};
      a.setFetch({{"/api/runs?groups=1": page}}); await a.wire();
      const shape = a.mainHtml();
      a.setFetch({{"/api/runs?groups=1": {json.dumps(rows)}}}); await a.wire();   // the old hub's array
      return {{ shape, array: a.mainHtml(), fetched: a.fetched(), count: a.count() }};
    """)
    assert set(out["fetched"]) == {"/api/runs?groups=1", "/api/live"}, "the page asks for the answer that carries the groups, then its own live work (#1697)"
    assert f'data-rid="{LIVE["id"]}"' in out["shape"], "the opted-in answer draws the live run"
    assert "class=sr" in out["shape"], "the opted-in answer draws no occupancy beside Live"
    assert out["count"] == 2, "an old hub's array is read as the runs, never as an object with no `runs`"
    assert f'data-rid="{LIVE["id"]}"' in out["array"], "an old hub's array still lists the live run"
    assert "class=sr" not in out["array"], "an old hub's array draws an occupancy it never sent"


def test_opening_one_side_section_leaves_its_siblings_alone():
    out = _run("""
      const a = boot();
      a.clickAcc("stats"); const one = a.accOpen();
      a.clickAcc("mdl"); const two = a.accOpen();
      return { one, two };
    """)
    assert out["one"] == ["stats"]
    assert set(out["two"]) == {"stats", "mdl"}, "opening one section never closes another"


def test_each_side_section_keeps_its_own_state_across_a_reload():
    out = _run("""
      const a = boot();
      a.clickAcc("stats"); a.clickAcc("lim"); a.clickAcc("mdl"); a.clickAcc("mdl");
      const stored = a.store();
      const b = boot();
      return { stored, restored: b.accOpen() };
    """)
    assert sorted(out["stored"].get("acc") or []) == ["lim", "stats"]
    assert out["restored"] == ["stats", "lim"], "each section comes back the way it was left"


def test_an_accordion_answers_only_itself_and_comes_back_the_way_it_was_left():
    """The left pane carries three (the worktree one left, the owner's ruling 2026-10-16): each behaves as the others do —
    its own header, its own stored state, no sibling touched."""
    out = _run("""
      const a = boot();
      a.clickAcc("stats"); const opened = a.accOpen();
      const b = boot();
      return { opened, restored: b.accOpen(), stored: a.store() };
    """)
    assert out["opened"] == ["stats"], "its own header opens it and nothing else"
    assert out["stored"].get("acc") == ["stats"] and out["restored"] == ["stats"]   # and it comes back the way it was left


def test_a_stored_value_in_the_old_single_id_shape_does_not_break_the_page():
    out = _run("""
      STORE.hub = JSON.stringify({ acc: "mdl" });     // the shape the page used to store
      const a = boot(); const booted = a.accOpen();
      a.clickAcc("stats"); const after = a.accOpen();
      return { booted, after };
    """)
    assert set(out["booted"]) <= {"plans", "mdl", "stats"}, "the old string is ignored or migrated, never trusted as a list"
    assert "stats" in out["after"], "a click still opens the section"
    assert sorted(x for x in out["after"] if x != "stats") == sorted(x for x in out["booted"] if x != "stats"), "and touches no sibling"


def test_opening_squad_models_still_loads_the_models():
    out = _run("""
      const a = boot(); const before = a.calls().mdlLoad;
      a.clickAcc("mdl"); const after = a.calls().mdlLoad;
      return { before, after };
    """)
    assert out["after"] == out["before"] + 1, "opening squad models still calls mdlLoad()"


def test_opening_spend_limits_still_loads_the_guard_settings():
    out = _run("""
      const a = boot(); const before = a.calls().limLoad;
      a.clickAcc("lim"); const after = a.calls().limLoad;
      return { before, after };
    """)
    assert out["after"] == out["before"] + 1, "opening Spend & limits still calls limLoad()"


def test_no_poll_path_writes_the_open_state():
    slices = {
        "the 3 s run poll": _slice(SRC, "async function poll(", "function bars("),
        "the run selector": _slice(SRC, "function selectNew(", "async function poll("),
        "the stats poll": _slice(SRC, "async function pollStats(", "pollStats();setInterval"),
        "the 5 s board poll": _slice(SRC, "async function pollBoard(", "// highlight:begin"),
        "the version poll": _slice(SRC, "// The open page follows the hub build", "</script>"),
    }
    for name, body in slices.items():
        for token in ("openGroup", "openAcc", "userPicked", "ls.acc"):
            assert token not in body, f"{name} writes {token}"


def test_request_issues_reads_the_dispatch_form(hub):
    """A frontier child's request names its slice as `GitHub issue(s) 10, 12`: both issues, on a `CHILD` line or not (#47)."""
    assert hub.request_issues("Work GitHub issue(s) 10, 12 (base origin/main) as one slice.") == {"10", "12"}
    assert hub.request_issues("  - CHILD — GitHub issue(s) 21 (base origin/main): fix it.") == {"21"}
    assert hub.request_issues("CHILD — GitHub issue(s) 33, 56 (bug slice).\nsee issue #900\n") == {"33", "56"}


def test_run_label_leads_with_the_action_then_the_issues(hub):
    """The card's name is computed here, not in the page: the action the run takes, then every issue it works (#47)."""
    assert hub.run_label({"id": "run-20261013-000010-444444", "issues": ["10", "12"], "kind": "code"}) == "implement #10, #12"
    assert hub.run_label({"id": "run-20261013-000011-555555", "issues": ["5"], "kind": "code", "bug": True}) == "fix #5"
    assert hub.run_label({"id": "run-20261013-000012-666666", "issues": ["7"], "kind": "code", "fix": 12}) == "fix PR #12 · #7"
    assert hub.run_label({"id": "run-20261013-000013-777777", "issues": [], "kind": "code"}) == "run 777777"


def test_the_list_renders_the_label_the_hub_computed(hub):
    """The page renders the label it is given; it never rebuilds one from the issues or the kind (ADR-0137)."""
    row = _run_row("1", 0, 10, id="run-20261013-000010-444444", label="dispatch #31, #25", issues=["31", "25"], kind="code")
    out = _run(_js([row], """
      const a = boot(); a.runs(R,G); a.draw();
      return { html: a.mainHtml() };
    """, hub.run_groups([row])))
    assert "dispatch #31, #25" in out["html"]


def test_the_headers_come_from_the_answer_and_the_page_builds_none(hub):
    """The page draws the text and title the hub sent, and it holds no header rule and no substituted caps (#1277)."""
    rows = [LIVE, HOUR_1]
    groups = hub.run_groups(rows, {"runs": 6, "children": 20})
    out = _run(_js(rows, """
      const a = boot(); a.runs(R,G); a.draw();
      return { html: a.mainHtml() };
    """, groups))
    running = next(g for g in groups if g["key"] == "running")
    assert running["text"] in out["html"] and f'title="{running["title"]}"' in out["html"]
    assert "function groupHeader(" not in hub.PAGE, "the page builds a group header again"
    assert "const CAPS=" not in hub.PAGE and "__CAPS__" not in hub.PAGE, "the caps are substituted into the page again"
