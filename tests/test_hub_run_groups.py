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


def test_ended_run_lands_in_the_age_bucket(hub):
    cases = [
        (0, "1h"),
        (3600, "1h"),
        (3600 + 1, "2h"),
        (7200, "2h"),
        (7200 + 1, "4h"),
        (14400, "4h"),
        (14400 + 1, "8h"),
        (5 * 3600, "8h"),
        (28800, "8h"),
        (28800 + 1, "24h"),
        (12 * 3600, "24h"),
        (86400, "24h"),
        (86400 + 1, "d"),
        (30 * 3600, "d"),
    ]
    for age, key in cases:
        assert hub.group_of({"state": "completed", "age": age, "live": False}) == key


def test_run_list_age_ladder_has_six_bands(hub):
    m = re.search(r"const GROUPS=\[(.*?)\];", hub.PAGE)
    assert m, "GROUPS missing"
    assert re.findall(r'\["([^"]*)","([^"]*)"\]', m.group(1))[1:] == [
        ("1h", "≤ 1 h"),
        ("2h", "1–2 h"),
        ("4h", "2–4 h"),
        ("8h", "4–8 h"),
        ("24h", "8–24 h"),
        ("d", "24 h+ (days)"),
    ]


def test_run_list_group_header_reads_workers_not_open(hub):
    m = re.search(r"const GROUPS=(\[[^\]]+\])", hub.PAGE)
    assert m, "GROUPS missing"
    groups = m.group(1)
    assert '["running","WORKERS"]' in groups
    assert "OPEN" not in groups and '["open"' not in groups


# --- the page's own code, run in node: the list and the three sections are read off the DOM ---


def _fn(src, name):
    i = src.index(f"function {name}(")
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
    header = _fn(SRC, "groupHeader")
    state = SRC[SRC.index(header) + len(header) : SRC.index("function drawList(")]
    assert "openGroup" in state, "the open state does not live beside groupHeader any more"
    acc = SRC[SRC.index("const ACC=") :]
    acc = acc[: acc.index("mdlLoad();") + len("mdlLoad();")]
    return [
        _decl(SRC, "usd"),
        _decl(SRC, "age"),
        _decl(SRC, "esc"),
        _decl(SRC, "GROUPS"),
        _decl(SRC, "save"),
        _decl(SRC, "label"),
        _decl(SRC, "labelTip"),
        _decl(SRC, "sub"),
        _fn(SRC, "groupHeader"),
        _fn(SRC, "loadMark"),
        _fn(SRC, "plan"),
        state,
        _fn(SRC, "drawList"),
        _fn(SRC, "selectNew"),
        acc,
    ]


_DOM = """
const STORE = {};
function boot(){
  const Q = []; let GHS = []; const DOM = {};   // the queue is the browser's: a programmatic .open fires toggle as a later task
  class El {
    constructor(id){ this.id = id; this._o = false; this.dataset = {}; this.L = {}; this._html = ""; this.innerHTML = ""; }
    get open(){ return this._o; }
    set open(v){ v = !!v; if (v === this._o) return; this._o = v; if (this.bound) Q.push(this); }
    addEventListener(t, f){ (this.L[t] = this.L[t] || []).push(f); this.bound = true; }
    fire(t){ (this.L[t] || []).forEach((f) => f({ target: this })); }
    closest(){ return null; }
    set innerHTML(v){
      this._html = String(v);
      if (this.id !== "list") return;
      GHS = this._html.split('<div class="gh').slice(1).map((c) => {
        const m = /^ ([^"]*)" data-g="([^"]+)"/.exec(c);
        return { k: m ? m[2] : "?", op: !!(m && /(^| )op( |$)/.test(m[1])), rows: (c.match(/<div class="r /g) || []).length, onclick: null };
      });
    }
    get innerHTML(){ return this._html; }
  }
  const $ = (s) => { const id = String(s).replace(/^#/, ""); return DOM[id] || (DOM[id] = new El(id)); };
  const document = {
    querySelector: $,
    querySelectorAll: (s) => s === ".gh"
      ? GHS.map((g) => ({ dataset: { g: g.k }, get onclick(){ return g.onclick; }, set onclick(f){ g.onclick = f; } }))
      : [],
  };
  const localStorage = STORE;   // the whole process shares it, so a second boot() is a reload
  const CALLS = { mdlLoad: 0 };
  function mdlLoad(){ CALLS.mdlLoad++; }
  const ls = JSON.parse(localStorage.hub || "{}");
  let allRuns = [], runs = [], N = 1, pins = new Set(), slots = [], known = null;
  let q = ls.q || "", per = ls.per || "day", src = ls.src || "both";
  const CAPS = null;
  function refresh(){ plan(); drawList(); }
@@PIECES@@
  function drain(){ let n = 0; while (Q.length) { if (++n > 100) throw new Error("toggle storm"); Q.shift().fire("toggle"); } }
  return {
    runs: (rows) => { allRuns = rows; runs = rows; },
    draw: () => drawList(),
    poll: () => { selectNew(); refresh(); },
    click: (k) => { const g = GHS.find((x) => x.k === k); if (!g) throw new Error("no group header " + k); g.onclick(); },
    openGroups: () => GHS.filter((g) => g.op).map((g) => g.k),
    groupRows: () => { const o = {}; GHS.forEach((g) => (o[g.k] = g.rows)); return o; },
    listHtml: () => $("#list").innerHTML,
    clickAcc: (id) => { const e = $("#" + id); e.open = !e.open; drain(); },
    accOpen: () => ["plans", "stats", "mdl"].filter((i) => $("#" + i).open),
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


def _run_row(tag, rank, age, **kw):
    r = {"id": f"run-20261011-1010{tag}0-abcd", "rank": rank, "age": age, "spend": 1.0, "agents": 1, "dur": 4, "port": 1234, "state": "running" if rank == 0 else "completed"}
    r.update(kw)
    return r


LIVE = _run_row("1", 0, 10)             # running
HOUR_1 = _run_row("2", 1, 600)          # 1h
HOUR_2 = _run_row("3", 1, 4000)         # 2h
HOUR_2_GONE = _run_row("3", 1, 40000)   # the same run, now 11 h old: the 2h bucket is empty
FRESH = _run_row("4", 0, 2)             # a run that just appeared


def _js(rows, body):
    return f"const R={json.dumps(rows)};" + body


def test_page_group_rule_matches_python_edges():
    rows = [
        _run_row("a", 1, 3600, id="edge-1h", spend=0.01),
        _run_row("b", 1, 3601, id="edge-2h", spend=0.02),
        _run_row("c", 1, 7200, id="edge-2h-top", spend=0.03),
        _run_row("d", 1, 7201, id="edge-4h", spend=0.04),
        _run_row("e", 1, 14400, id="edge-4h-top", spend=0.05),
        _run_row("f", 1, 14401, id="edge-8h", spend=0.06),
        _run_row("g", 1, 5 * 3600, id="edge-5h", spend=0.07),
        _run_row("h", 1, 28800, id="edge-8h-top", spend=0.08),
        _run_row("i", 1, 28801, id="edge-24h", spend=0.09),
        _run_row("j", 1, 12 * 3600, id="edge-12h", spend=0.10),
        _run_row("k", 1, 86400, id="edge-24h-top", spend=0.11),
        _run_row("l", 1, 86401, id="edge-days", spend=0.12),
        _run_row("m", 1, 30 * 3600, id="edge-30h", spend=0.13),
    ]
    out = _run(_js(rows, """
      const a = boot(); a.runs(R); a.draw();
      for (const k of ["2h", "4h", "8h", "24h", "d"]) a.click(k);
      return { rows: a.groupRows() };
    """))
    assert {k: out["rows"][k] for k in ["1h", "2h", "4h", "8h", "24h", "d"]} == {"1h": 1, "2h": 2, "4h": 2, "8h": 3, "24h": 3, "d": 2}


def test_empty_age_bands_render_with_zero_spend():
    out = _run(_js([HOUR_1], """
      const a = boot(); a.runs(R); a.draw();
      return { rows: a.groupRows(), html: a.listHtml() };
    """))
    assert list(out["rows"]) == ["running", "1h", "2h", "4h", "8h", "24h", "d"]
    assert out["html"].count('0 · $0.00') == 5


def test_first_filled_age_band_opens_when_nothing_is_stored():
    out = _run(_js([HOUR_2], """
      const a = boot(); a.runs(R); a.draw();
      return { open: a.openGroups() };
    """))
    assert out["open"] == ["2h"]


def test_a_group_header_click_toggles_it_both_ways():
    out = _run(_js([LIVE, HOUR_1, HOUR_2], """
      const a = boot(); a.runs(R); a.draw();
      const start = a.openGroups();
      a.click("running"); const closed = a.openGroups();
      a.click("running"); const reopened = a.openGroups();
      return { start, closed, reopened };
    """))
    assert out["start"] == ["running"], "the first draw opens the newest group once"
    assert out["closed"] == [], "clicking an open header closes it"
    assert out["reopened"] == ["running"], "clicking a closed header opens it"


def test_two_run_groups_can_stay_open_at_once():
    out = _run(_js([LIVE, HOUR_1, HOUR_2], """
      const a = boot(); a.runs(R); a.draw();
      a.click("1h"); a.click("2h");
      return { open: a.openGroups() };
    """))
    assert set(out["open"]) == {"running", "1h", "2h"}, "any number of groups stay open"


def test_a_run_leaving_its_bucket_leaves_the_open_groups_alone():
    out = _run(_js([LIVE, HOUR_1, HOUR_2], f"""
      const a = boot(); a.runs(R); a.draw();
      a.click("2h"); const before = a.openGroups();
      a.runs({json.dumps([LIVE, HOUR_1, HOUR_2_GONE])}); a.poll();   // the 2h run aged out of the bucket
      return {{ before, after: a.openGroups(), rows: a.groupRows() }};
    """))
    assert set(out["before"]) == {"running", "2h"}
    assert out["after"] == out["before"], "a bucket change never moves the open groups"
    assert out["rows"]["2h"] == 0, "an open group that emptied stays open and empty"


def test_a_run_appearing_does_not_move_the_open_groups():
    out = _run(_js([LIVE, HOUR_1], f"""
      const a = boot(); a.runs(R); a.draw(); a.poll();
      a.click("1h"); const before = a.openGroups();
      a.runs(R.concat([{json.dumps(FRESH)}])); a.poll();   // a new run starts: the list must not re-pick
      return {{ before, after: a.openGroups() }};
    """))
    assert set(out["before"]) == {"running", "1h"}
    assert out["after"] == out["before"], "a run appearing never moves the open groups"


def test_the_open_groups_survive_a_reload():
    out = _run(_js([LIVE, HOUR_1, HOUR_2], """
      const a = boot(); a.runs(R); a.draw();
      a.click("1h"); a.click("running");          // the user closes the group the first draw opened
      const stored = a.store();
      const b = boot(); b.runs(R); b.draw();      // a reload: a fresh page over the same storage
      return { stored, restored: b.openGroups() };
    """))
    assert out["stored"].get("groups") == ["1h"], "the open set is stored beside the pins"
    assert out["restored"] == ["1h"], "a reload restores exactly the set the user left"


def test_opening_one_side_section_leaves_its_siblings_alone():
    out = _run("""
      const a = boot();
      a.clickAcc("plans"); const one = a.accOpen();
      a.clickAcc("mdl"); const two = a.accOpen();
      return { one, two };
    """)
    assert out["one"] == ["plans"]
    assert set(out["two"]) == {"plans", "mdl"}, "opening one section never closes another"


def test_each_side_section_keeps_its_own_state_across_a_reload():
    out = _run("""
      const a = boot();
      a.clickAcc("plans"); a.clickAcc("stats"); a.clickAcc("mdl"); a.clickAcc("mdl");
      const stored = a.store();
      const b = boot();
      return { stored, restored: b.accOpen() };
    """)
    assert sorted(out["stored"].get("acc") or []) == ["plans", "stats"]
    assert out["restored"] == ["plans", "stats"], "each section comes back the way it was left"


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


def test_no_poll_path_writes_the_open_state():
    slices = {
        "the 3 s run poll": _slice(SRC, "async function poll(", "function bars("),
        "the run selector": _slice(SRC, "function selectNew(", "async function poll("),
        "the stats poll": _slice(SRC, "async function pollStats(", "pollStats();setInterval"),
        "the 5 s board poll": _slice(SRC, "async function pollBoard(", '$("#lfl").onchange'),
        "the version poll": _slice(SRC, "let ver=null;", "</script>"),
    }
    for name, body in slices.items():
        for token in ("openGroup", "openAcc", "userPicked", "ls.acc"):
            assert token not in body, f"{name} writes {token}"
