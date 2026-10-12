"""Hub machine load (#1776): the left pane's footer strip reads CPU, RAM and GPU, and the card of the run that owns
the load says so. These pin the parts that carry a rule — the cell formatting, the `?` fallback when a source cannot
answer, the two-sample warn rule, the attribution by worktree path and the marker that clears with the load."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_machine_load", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- the strip's cells

def test_a_cell_is_a_rounded_percentage(hub):
    assert hub.pct_cell(62.4) == "62%"
    assert hub.pct_cell(0) == "0%"          # an idle machine is a reading, not a missing one
    assert hub.pct_cell(100) == "100%"


def test_a_cell_is_a_question_mark_when_the_source_did_not_answer(hub):
    assert hub.pct_cell(None) == "?"        # never a stale or invented number
    assert hub.pct_cell("62%") == "?"
    assert hub.pct_cell(float("nan")) == "?"
    assert hub.pct_cell(-1) == "?"


def test_the_line_reads_cpu_ram_gpu_in_that_order(hub):
    assert hub.load_line(62.4, 71.2, 27.0) == "CPU 62% · RAM 71% · GPU 27%"
    assert hub.load_line(62.4, None, 27.0) == "CPU 62% · RAM ? · GPU 27%"


def test_the_cadence_and_the_thresholds_are_named_constants(hub):
    assert 0 < hub.LOAD_SAMPLE_SECS <= 5          # the page may not refresh slower than every 5 s
    assert isinstance(hub.LOAD_WARN_PCT, (int, float)) and 0 < hub.LOAD_WARN_PCT <= 100
    assert hub.LOAD_WARN_SAMPLES > 1              # above the threshold for MORE than one sample
    assert hub.LOAD_MIN_SHARE_PCT >= 0


# ---------------------------------------------------------------- the reading itself

def test_a_source_that_raises_is_unavailable_not_a_traceback(hub):
    def boom():
        raise RuntimeError("no such device")
    got = hub.sample_load({"cpu": lambda: 62.4, "ram": boom, "gpu": lambda: None})
    assert got == {"cpu": 62.4, "ram": None, "gpu": None}
    assert hub.load_line(**got) == "CPU 62% · RAM ? · GPU ?"


def test_a_source_answering_with_a_non_percentage_is_unavailable(hub):
    got = hub.sample_load({"cpu": lambda: "n/a", "ram": lambda: -1, "gpu": lambda: float("nan")})
    assert got == {"cpu": None, "ram": None, "gpu": None}


def test_the_gpu_reading_comes_from_the_accelerators_own_counters(hub):
    text = '  "PerformanceStatistics" = {"Tiler Utilization %"=29,"Device Utilization %"=30,"Renderer Utilization %"=29}'
    assert hub.parse_gpu(text) == 30.0
    assert hub.parse_gpu("") is None                    # no accelerator in the machine
    assert hub.parse_gpu('"PerformanceStatistics" = {}\n') is None
    assert hub.parse_gpu('"Device Utilization %"=abc') is None


def test_a_cpu_or_ram_sample_spawns_nothing(hub, monkeypatch):
    """The reading must not add to the load it reports: this machine's own sources, with every spawn made fatal."""
    def boom(*a, **k):
        raise AssertionError("a CPU or RAM sample must not spawn a process")
    monkeypatch.setattr(hub, "sh", boom)
    monkeypatch.setattr(hub.subprocess, "run", boom)
    got = hub.sample_load({"cpu": hub.cpu_pct, "ram": hub.ram_pct})
    for v in got.values():
        assert v is None or 0 <= v <= 100                 # a reading, or an honest `?`


# ---------------------------------------------------------------- the warn rule

def test_one_hot_sample_is_not_load(hub):
    assert hub.sustained([95.0]) is False


def test_two_samples_in_a_row_above_the_threshold_are_load(hub):
    assert hub.sustained([95.0, 93.0]) is True


def test_the_run_of_samples_has_to_be_unbroken(hub):
    assert hub.sustained([95.0, 10.0, 95.0]) is False
    assert hub.sustained([10.0, 95.0, 95.0]) is True
    assert hub.sustained([95.0, None]) is False         # a sample that could not be read breaks the run: never a guess
    assert hub.sustained([None, 95.0, 95.0]) is True    # ... and one older than the run of samples does not hold it back


def test_a_machine_under_the_threshold_is_never_warned(hub):
    assert hub.sustained([hub.LOAD_WARN_PCT - 1, hub.LOAD_WARN_PCT - 1]) is False


# ---------------------------------------------------------------- who owns the CPU

def test_a_process_tree_is_credited_to_the_worktree_its_argv_names(hub):
    prev = {10: (1, 100.0, "/bin/zsh"), 11: (10, 50.0, "claude --project /u/wt-as-1776"), 12: (11, 20.0, "dotnet test")}
    cur = {10: (1, 100.0, "/bin/zsh"), 11: (10, 52.0, "claude --project /u/wt-as-1776"), 12: (11, 26.0, "dotnet test")}
    assert hub.attribute(prev, cur, {"run-a": ["/u/wt-as-1776"]}) == {"run-a": 8.0}   # 2 s of agent + the 6 s of the test it spawned


def test_runs_are_told_apart_by_worktree_path_not_by_process_name(hub):
    # every child of every run is the same `claude` and every test host the same `dotnet`: only the path separates them
    prev = {1: (0, 0.0, "/sbin/launchd"), 2: (1, 0.0, "claude --project /u/wt-as-1776"), 3: (1, 0.0, "claude --project /u/wt-as-1775"),
            4: (2, 0.0, "dotnet test"), 5: (3, 0.0, "dotnet test")}
    cur = {1: (0, 0.0, "/sbin/launchd"), 2: (1, 3.0, "claude --project /u/wt-as-1776"), 3: (1, 5.0, "claude --project /u/wt-as-1775"),
           4: (2, 4.0, "dotnet test"), 5: (3, 1.0, "dotnet test")}
    assert hub.attribute(prev, cur, {"a": ["/u/wt-as-1776"], "b": ["/u/wt-as-1775"]}) == {"a": 7.0, "b": 6.0}


def test_a_run_that_owns_nothing_is_absent(hub):
    prev = {1: (0, 0.0, "/sbin/launchd"), 9: (1, 0.0, "node /srv/other")}
    cur = {1: (0, 0.0, "/sbin/launchd"), 9: (1, 9.0, "node /srv/other")}
    assert hub.attribute(prev, cur, {"a": ["/u/wt-as-1776"]}) == {}


def test_the_run_folder_owns_the_child_worktrees_inside_it(hub):
    owners = hub.owners_of([{"id": "run-1", "dir": "/r/runs/run-20261011-132053-54d24b", "wts": ["/u/wt-as-1776"]}])
    assert owners == {"run-1": ["/r/runs/run-20261011-132053-54d24b", "/u/wt-as-1776"]}
    prev = {1: (0, 0.0, "python -m squad.dashboard_server /r/runs/run-20261011-132053-54d24b"),
            2: (1, 0.0, "zsh /r/runs/run-20261011-132053-54d24b/ws/worktrees_child-route.router.7")}
    cur = {1: (0, 0.5, "python -m squad.dashboard_server /r/runs/run-20261011-132053-54d24b"),
           2: (1, 4.5, "zsh /r/runs/run-20261011-132053-54d24b/ws/worktrees_child-route.router.7")}
    assert hub.attribute(prev, cur, owners) == {"run-1": 5.0}   # the server that carries the run folder counts with its children


def test_a_share_is_a_percentage_of_the_whole_machine(hub):
    assert hub.share_pct(4.0, 5.0, 8) == 10.0    # four CPU-seconds in a five-second window on eight cores
    assert hub.share_pct(0.0, 5.0, 8) == 0.0
    assert hub.share_pct(40.0, 5.0, 8) == 100.0


def test_the_record_names_the_worktrees_a_run_works_in(hub):
    p = Path(tempfile.mkdtemp()) / "record.jsonl"
    ev = [{"event": "run_start", "ts": "2026-10-11T10:00:00Z"},
          {"event": "outcome", "node": "route.router.7", "outcome": "pass", "ts": "2026-10-11T10:01:00Z",
           "workspace": {"name": "child", "path": "/Users/bihaiko/wt-as-1776", "head": "abc123", "state": "clean"}},
          {"event": "outcome", "node": "verify", "outcome": "pass", "ts": "2026-10-11T10:02:00Z",
           "workspace": {"name": ".", "path": "/r/checkout", "head": "def456", "state": "clean"}}]
    p.write_text("".join(json.dumps(e) + "\n" for e in ev))
    assert hub.scan(str(p))["wts"] == ["/Users/bihaiko/wt-as-1776"]   # the target itself is not a worktree of its own


# ---------------------------------------------------------------- the card marker

def test_only_the_run_owning_the_cpu_carries_the_marker(hub):
    rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    marked = hub.attach_load([dict(r) for r in rows], {"a": 41.0, "b": 0.0}, warn=True)
    assert marked[0]["cpu"] == 41
    assert "cpu" not in marked[1]                  # it owns nothing of this load
    assert "cpu" not in marked[2]                  # it owns no part of the machine at all


def test_the_marker_clears_when_the_load_does(hub):
    rows = [{"id": "a", "cpu": 41}]
    assert "cpu" not in hub.attach_load(rows, {"a": 41.0}, warn=False)[0]


def test_a_dab_of_cpu_on_a_busy_machine_blames_nobody(hub):
    assert "cpu" not in hub.attach_load([{"id": "a"}], {"a": hub.LOAD_MIN_SHARE_PCT / 2}, warn=True)[0]


# ---------------------------------------------------------------- what the page is served

def test_the_page_reads_the_cached_reading_and_never_samples_it(hub, monkeypatch):
    def no_sample(*a, **k):
        raise AssertionError("serving the page must not sample the machine")
    monkeypatch.setattr(hub, "sample_load", no_sample)
    hub.LOAD_STATE.update({"cpu": [12.0, 90.0, 91.0], "ram": 71.2, "gpu": None, "asof": 1.0, "runs": {"a": 41.0}})
    p = hub.load_payload()
    assert p["line"] == "CPU 91% · RAM 71% · GPU ?"
    assert p["cpu"] == 91 and p["warn"] is True and p["runs"] == {"a": 41.0}


def test_an_empty_history_reads_as_unavailable(hub):
    hub.LOAD_STATE.update({"cpu": [], "ram": None, "gpu": None, "asof": 0.0, "runs": {}})
    p = hub.load_payload()
    assert p["line"] == "CPU ? · RAM ? · GPU ?" and p["warn"] is False


def test_the_page_carries_a_footer_strip_and_polls_the_reading():
    src = HUB_PY.read_text()
    assert 'elif self.path.startswith("/api/load")' in src            # served, not sampled, per view
    assert '<div id=foot' in src and "id=ldline" in src              # the strip and its line
    assert 'const LOAD_MS=Number("__LOADMS__")||5000;' in src
    assert '.replace("__LOADMS__", str(int(LOAD_SAMPLE_SECS * 1000)))' in src
    assert "setInterval(loadPoll,LOAD_MS)" in src


# ---------------------------------------------------------------- where the reading sits

VOID = {"meta", "link", "br", "hr", "img", "input", "source", "area"}


class Page(HTMLParser):
    """Enough of the page's element tree to ask where a box sits: its ancestors and the document order."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parents, self.order, self._open = {}, [], []
        self.feed(html)

    def _note(self, attrs):
        eid = dict(attrs).get("id")
        if eid:
            self.parents[eid] = tuple(a for a, _ in self._open if a)
            self.order.append(eid)

    def handle_starttag(self, tag, attrs):
        self._note(attrs)
        if tag not in VOID:
            self._open.append((dict(attrs).get("id"), tag))

    def handle_startendtag(self, tag, attrs):
        self._note(attrs)

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][1] == tag:
                del self._open[i:]
                return


@pytest.fixture(scope="module")
def pg(hub):
    return Page(hub.PAGE)


def _css(hub, selector):
    """One CSS rule's body, matched on a whole selector at a rule boundary."""
    m = re.search(r"(?:^|[\n}])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", hub.PAGE)
    assert m, f"no CSS rule for {selector}"
    return m.group(1)


def test_the_reading_left_the_pane_and_became_the_windows_own_bar(pg):
    # the left pane keeps its footer for the loop's own state; the machine reading is about the whole window
    assert "side" not in pg.parents["foot"]
    assert pg.parents["foot"] == pg.parents["win"]      # a sibling of the panes' container, so the bar spans the window
    assert "win" in pg.parents["side"] and "win" in pg.parents["board"]
    assert pg.order.index("foot") > pg.order.index("board")   # painted below both panes, not between them


def test_the_bar_is_a_row_of_the_windows_column_so_nothing_pushes_it_off(hub, pg):
    body = _css(hub, "body")
    assert "flex-direction:column" in body              # window = panes, then the bar
    win = _css(hub, "#win")
    assert "flex:1" in win and "min-height:0" in win    # the panes take the room left over: they scroll, the bar does not
    foot = _css(hub, "#foot")
    assert "flex:none" in foot and "position:sticky" not in foot   # a fixed height of its own, not sticky inside a scroller


def test_the_docked_board_stays_inside_the_panes_so_the_bar_keeps_the_full_width(hub, pg):
    dock = _css(hub, "body.dockb #win")
    assert "grid-template-columns:300px 1fr" in dock and "grid-template-rows:minmax(0,1fr) auto" in dock


def test_one_element_carries_the_reading_however_often_it_is_repainted(hub, pg):
    assert pg.order.count("ldline") == 1                              # one reading on the page, never two that can disagree
    assert hub.PAGE.count("setInterval(loadPoll") == 1                # one timer for the whole window
    assert hub.PAGE.count('fetch("/api/load")') == 1                  # one request per tick, whatever renders the value


def _render_load_mark(hub, run):
    """The marker the page really paints, run by node (already a dependency of this suite)."""
    js = re.search(r"^function loadMark\(.*$", hub.PAGE, re.M).group(0)
    script = f"{js}\nconsole.log(loadMark({json.dumps(run)}))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_the_page_paints_the_marker_only_for_a_run_that_owns_the_load(hub):
    assert "cpu 41%" in _render_load_mark(hub, {"cpu": 41})
    assert _render_load_mark(hub, {"id": "b"}) == ""


# ---------------------------------------------------------------- the board's maximize toggle (#1781)

BOARD_OPTS_FROM = "const bo=ls.bo||{}"
BOARD_OPTS_TO = '$("#bz").onclick=()=>setMax(!bo.max);'


def _head(hub):
    """The board's header as served, from its opening tag to the body it swaps."""
    return hub.PAGE[hub.PAGE.index("<div id=bhead>"):hub.PAGE.index("<div id=bbody>")]


def _board_slice(hub):
    """The page's board options: the saved record, the two controls and their handlers, nothing else."""
    a = hub.PAGE.index(BOARD_OPTS_FROM)
    b = hub.PAGE.index(BOARD_OPTS_TO) + len(BOARD_OPTS_TO)
    return hub.PAGE[a:b]


def test_the_board_header_carries_a_maximize_button_beside_the_collapse_one(hub, pg):
    head = _head(hub)
    assert "<button id=bz" in head and "<button id=bx" in head                # both controls live in the same header
    assert head.index("id=lf") < head.index("id=bz") < head.index("id=bx")    # added beside the collapse control
    assert pg.order.count("bz") == 1
    m = re.search(r"<button id=bz title=\"([^\"]+)\" aria-pressed=false>", head)
    assert m, head
    assert "maximize" in m.group(1).lower()        # a title that says what it does


def test_both_header_controls_are_titled_pressed_buttons(hub):
    head = _head(hub)
    both = re.findall(r"<button id=b([xz]) title=\"([^\"]+)\" aria-pressed=false>", head)
    assert sorted(b for b, _ in both) == ["x", "z"]      # focusable, titled, and each says whether it is on
    assert all(t.strip() for _, t in both)


def test_the_maximized_board_claims_both_cells_of_the_right_column(hub):
    css = _css(hub, "body.dockb.max #board")
    assert "grid-row:1/-1" in css                  # top to bottom: the run pane's cell included
    assert "height:auto" in css                    # no longer a fixed-height row of its own
    assert "grid-template-rows:minmax(0,1fr) 0" in _css(hub, "body.dockb.max #win")   # the run pane's row has nothing left to give


def test_the_run_pane_is_hidden_with_visibility_so_its_iframe_is_never_detached(hub):
    css = _css(hub, "body.dockb.max #main")
    assert "visibility:hidden" in css
    assert "display:none" not in css               # its space is what the board takes; the element is untouched
    assert not re.search(r"#main[^\n{]*\{[^}]*display:none", hub.PAGE)


def test_the_grip_has_nothing_to_resize_while_the_board_is_maximized(hub):
    assert "display:none" in _css(hub, "body.dockb.max #grip")


def test_the_toggle_never_touches_the_left_bar(hub):
    assert "grid-row:1/-1" in _css(hub, "body.dockb.max #board")               # the maximize moved the board, not the bar
    assert "grid-template-columns:300px 1fr" in _css(hub, "body.dockb #win")
    assert not re.search(r"body\.dockb\.max #(side|list|win)\s*,", hub.PAGE)   # only the right column's two cells move
    assert not re.search(r"body\.dockb\.max #side\s*\{", hub.PAGE)


def test_the_maximize_adds_no_reload_path_of_its_own(hub):
    assert hub.PAGE.count("location.reload") == 1                              # /api/version stays the only reload
    board = _board_slice(hub)
    assert not re.search(r"location\.(reload|href|assign|replace)", board)     # the toggle cannot navigate at all


_NODE_PRE = r"""
const __el={},__seen=[],__made=[];
const __mk=id=>({id,innerHTML:"",title:"",style:{},attrs:{},classes:new Set(),
  classList:{add:c=>__el[id].classes.add(c),remove:c=>__el[id].classes.delete(c),
    toggle:(c,on)=>{on?__el[id].classes.add(c):__el[id].classes.delete(c)},
    contains:c=>__el[id].classes.has(c)},
  setAttribute(k,v){__el[id].attrs[k]=v},getAttribute(k){return __el[id].attrs[k]}});
const $=s=>{__seen.push(s);return __el[s]||(__el[s]=__mk(s))};
__el["#board"]=__mk("#board");__el["#main"]=__mk("#main");__el["#bz"]=__mk("#bz");__el["#bx"]=__mk("#bx");
const __body={classes:new Set(),classList:{toggle:(c,on)=>{on?__body.classes.add(c):__body.classes.delete(c)},contains:c=>__body.classes.has(c)}};
globalThis.document={body:__body,createElement:t=>{__made.push(t);return __mk(t)}};
globalThis.localStorage={hub:__STORED__};
const ls=JSON.parse(localStorage.hub||"{}");
const pins=new Set(),q="",per="day";
"""

_NODE_POST = r"""
const snap=()=>({body:[...__body.classes].sort(),board:[...__el["#board"].classes].sort(),
  press:__el["#bz"].attrs["aria-pressed"],bxpress:__el["#bx"].attrs["aria-pressed"],
  title:__el["#bz"].title,icon:__el["#bz"].innerHTML,
  saved:JSON.parse(localStorage.hub||"{}").bo});
const steps={init:snap(),seen:[...new Set(__seen)],made:[...__made]};
$("#bz").onclick();steps.maxOn=snap();
$("#bz").onclick();steps.maxOff=snap();
$("#bz").onclick();$("#bx").onclick();steps.collapseWhileMaximized=snap();
$("#bz").onclick();steps.maximizeWhileCollapsed=snap();
escBoard();steps.afterEsc=snap();
steps.seenAfter=[...new Set(__seen)];steps.madeAfter=[...__made];
console.log("RESULT"+JSON.stringify(steps));
"""


def _board_steps(hub, saved):
    """Run the page's board options in node over a stored record, clicking both controls in turn."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    script = _NODE_PRE.replace("__STORED__", json.dumps(json.dumps({"bo": saved}))) + _board_slice(hub) + _NODE_POST
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.split("RESULT")[-1])


def test_the_choice_is_saved_with_the_boards_other_options(hub):
    st = _board_steps(hub, {})
    assert st["init"]["body"] == ["dockb"] and st["init"]["press"] == "false"
    assert st["maxOn"]["body"] == ["dockb", "max"]
    assert st["maxOn"]["saved"]["max"] is True and st["maxOn"]["saved"]["off"] is False   # saved with the board's other options
    assert st["maxOn"]["press"] == "true" and st["maxOn"]["icon"] != st["maxOff"]["icon"]   # the icon reflects the state
    assert "svg" in st["maxOn"]["icon"] and "restore" in st["maxOn"]["title"].lower()
    assert st["maxOff"]["body"] == ["dockb"] and st["maxOff"]["saved"]["max"] is False


def test_the_toggle_never_removes_recreates_or_re_srcs_the_run_panes_iframe(hub):
    st = _board_steps(hub, {})
    assert "#main" not in st["seen"] and "#main" not in st["seenAfter"]   # the pane is not even looked up
    assert st["made"] == [] and st["madeAfter"] == []                     # nothing detached, nothing re-created
    assert "drawMain" not in _board_slice(hub) and "frames" not in _board_slice(hub)


def test_the_two_controls_can_never_both_be_on(hub):
    st = _board_steps(hub, {})
    assert st["collapseWhileMaximized"]["body"] == ["dockb"]              # the collapse restored the split first
    assert st["collapseWhileMaximized"]["board"] == ["off"] and st["collapseWhileMaximized"]["saved"]["max"] is False
    assert st["collapseWhileMaximized"]["bxpress"] == "true" and st["collapseWhileMaximized"]["press"] == "false"
    assert st["maxOn"]["bxpress"] == "false" and st["maxOff"]["bxpress"] == "false"   # the collapse control reads its own state
    assert st["maximizeWhileCollapsed"]["body"] == ["dockb", "max"]       # maximizing a collapsed board restores it first
    assert st["maximizeWhileCollapsed"]["board"] == [] and st["maximizeWhileCollapsed"]["saved"]["off"] is False


def test_a_stored_state_that_is_both_is_never_restored(hub):
    st = _board_steps(hub, {"off": True, "max": True})
    assert st["init"]["body"] == ["dockb", "max"] and st["init"]["board"] == []


def test_a_reload_restores_the_maximized_board_from_the_boards_saved_options(hub):
    st = _board_steps(hub, {"max": True})
    assert st["init"]["body"] == ["dockb", "max"] and st["init"]["board"] == []
    assert st["init"]["press"] == "true"
    assert _board_steps(hub, {"off": True})["init"]["body"] == ["dockb"]   # collapsed stays collapsed, and not maximized


def test_escape_restores_the_split(hub):
    st = _board_steps(hub, {"max": True})
    assert st["afterEsc"]["body"] == ["dockb"] and st["afterEsc"]["saved"]["max"] is False
    assert re.search(r'key==="Escape"\)\{[^}]*escBoard\(\)', hub.PAGE)     # the page's Escape branch is what restores it


# ---------------------------------------------------------------- the line's facts (#1797)
# The strip answered "is this machine saturated?". These pin the facts that answer the other half — whether the squad is
# working or merely looks busy — and nothing else: the order they are read in, the `?` for a source that went unreadable,
# `ok` for an empty health pass, the pair #1795 writes for the drift, the caps the graph carries, the quota markers the
# review chain skips models over, the amber past the quarter hour, the drop on a window too narrow for the row, and the
# one token per subscription (#1839) the line carries last — the acronym, the quota and the time to its reset.

NOW = 1_000_000.0
CAPS = {"max_parallel": 10, "max_children": 20}


def _src(**over):
    """One reading of every source, all readable, so a test can break exactly one of them."""
    base = {"health": {"causes": [], "armed": {}, "ts": NOW},
            "progress": NOW - 240, "occ": {"live": 4, "agents": 7},
            "drift": {"runs": 2, "inprogress": 2}, "quota": {"codex": None, "agy": None}}
    return {**base, **over}


def _facts(hub, src=None, caps=CAPS):
    return [i["text"] for i in hub.foot_items(src or _src(), caps, NOW)]


def test_the_six_facts_follow_the_load_cells_in_this_order(hub):
    reset = time.mktime((2026, 10, 11, 14, 20, 0, 0, 0, -1))                 # the reset the marker names, read as a clock time
    items = hub.foot_items(_src(quota={"codex": None, "agy": reset}), CAPS, NOW)
    assert [i["key"] for i in items] == ["health", "progress", "occ", "drift", "quota", "subs"]
    assert hub.foot_line(items) == ("health ok · last progress 4m · 4/10 runs 7/20 agents · drift 0 · "
                                    "codex ok · agy reset 14:20 · CL ? · GK ? · GPT ? · GEM ?")
    assert [i["bad"] for i in items] == [False] * 6


def test_health_names_the_most_recent_still_armed_cause_with_its_age(hub):
    armed = {"watch-stalled": NOW - 540, "fork-parked": NOW - 3600}
    assert _facts(hub, _src(health={"causes": ["watch-stalled", "fork-parked"], "armed": armed, "ts": NOW}))[0] \
        == "health: watch-stalled 9m"                                        # named, with its age — never a bare count
    assert _facts(hub, _src(health={"causes": ["watch-stalled"], "armed": {}, "ts": NOW - 540}))[0] \
        == "health: watch-stalled 9m"                                        # nothing in alerts.log: the pass that saw it ages it


def test_health_is_ok_when_the_pass_found_no_cause_and_never_a_count(hub):
    assert _facts(hub, _src(health={"causes": [], "armed": {}, "ts": NOW}))[0] == "health ok"
    text = _facts(hub, _src(health={"causes": ["a", "b", "c"], "armed": {"a": NOW - 600}, "ts": NOW}))[0]
    assert text == "health: a 10m" and "3" not in text


def test_progress_is_the_newest_transition_the_loop_owns_and_turns_bad_past_the_quarter_hour(hub):
    assert hub.FOOT_PROGRESS_WARN_SECS == 15 * 60
    assert _facts(hub, _src(progress=NOW - 900))[1] == "last progress 15m"
    assert [i["key"] for i in hub.foot_items(_src(progress=NOW - 901), CAPS, NOW) if i["bad"]] == ["progress"]
    assert hub.foot_items(_src(progress=NOW - 900), CAPS, NOW)[1]["bad"] is False
    assert _facts(hub, _src(progress=NOW - 2400))[1] == "last progress 40m"


def test_occupancy_reads_the_caps_the_graph_carries_and_never_a_hard_coded_pair(hub):
    assert _facts(hub, _src(occ={"live": 4, "agents": 7}), {"max_parallel": 3, "max_children": 40})[2] \
        == "4/3 runs 7/40 agents"                                            # the caps come from the graph, not from 10/20
    assert _facts(hub, _src(occ={"live": 4, "agents": 7}), None)[2] == "?"
    assert _facts(hub, _src(occ=None))[2] == "?"


def test_drift_is_the_pair_health_writes_and_never_a_count_of_its_own(hub):
    assert _facts(hub, _src(drift={"runs": 2, "inprogress": 5}))[3] == "drift 3"
    assert _facts(hub, _src(drift={"runs": 2, "inprogress": 2}))[3] == "drift 0"
    assert _facts(hub, _src(drift=None))[3] == "drift ?"                     # #1795's pair is not there yet
    assert _facts(hub, _src(drift={"runs": 2, "inprogress": -1}))[3] == "drift ?"   # an unreadable board is not a drift
    # the live-run count the footer already holds is NOT this number: only the pair decides it
    assert _facts(hub, _src(occ={"live": 9, "agents": 1}, drift={"runs": 2, "inprogress": 2}))[3] == "drift 0"


def test_only_the_limited_quota_markers_are_named_each_with_its_reset(hub):
    reset = time.mktime((2026, 10, 11, 14, 20, 0, 0, 0, -1))
    assert _facts(hub, _src(quota={"codex": reset, "agy": reset}))[4] == "codex reset 14:20 · agy reset 14:20"
    assert _facts(hub, _src(quota={"codex": None, "agy": None}))[4] == "codex ok · agy ok"
    assert _facts(hub, _src(quota={"codex": "?", "agy": None}))[4] == "codex ? · agy ok"   # one unreadable marker only


def test_a_quota_marker_counts_only_while_the_time_it_names_is_ahead(hub):
    assert hub.limited_until(str(int(NOW + 60)), NOW) == NOW + 60
    assert hub.limited_until(str(int(NOW - 60)), NOW) is None                # the window passed: the model is back
    assert hub.limited_until("", NOW) is None                                # no marker: nothing was spent
    assert hub.limited_until("soon", NOW) == "?"                             # a file naming no time the hub can read


def test_one_unreadable_source_shows_a_question_mark_and_leaves_the_rest_intact(hub):
    line = hub.foot_line(hub.foot_items(_src(progress=None), CAPS, NOW))
    assert line == ("health ok · last progress ? · 4/10 runs 7/20 agents · drift 0 · codex ok · agy ok · "
                    "CL ? · GK ? · GPT ? · GEM ?")
    assert hub.foot_line(hub.foot_items({"health": None, "progress": None, "occ": None, "drift": None,
                                        "quota": {"codex": "?", "agy": None}}, None, NOW)) \
        == "health ? · last progress ? · ? · drift ? · codex ? · agy ok · CL ? · GK ? · GPT ? · GEM ?"


def test_the_facts_are_read_from_the_files_the_loop_already_writes(hub, tmp_path, monkeypatch):
    now = time.time()
    st = tmp_path / "st"; (st / "auto").mkdir(parents=True)
    (st / "health.json").write_text(json.dumps({"ts": now, "ok": False, "runs": 2, "inprogress": 5, "causes": ["watch-stalled"]}))
    armed = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 540))
    (st / "alerts.log").write_text(f"{armed} HEALTH watch-stalled: no cycle for 6 min\n")
    (st / "cycle.log").write_text("16:19:46 autobug.sh 31s\n")
    (st / "agy-limited").write_text(str(int(now + 3600)))
    monkeypatch.setattr(hub, "STATE", st)
    src = hub.foot_sources(now)
    assert src["health"]["causes"] == ["watch-stalled"]
    assert abs(src["health"]["armed"]["watch-stalled"] - (now - 540)) < 2
    assert src["drift"] == {"runs": 2, "inprogress": 5}                       # the pair #1795 writes, read as it stands
    assert src["quota"]["agy"] == int(now + 3600) and src["quota"]["codex"] is None
    facts = hub.foot_line(hub.foot_items(src, {"max_parallel": 10, "max_children": 20}, now))
    assert "health: watch-stalled 9m" in facts and "drift 3" in facts and "agy reset " in facts
    monkeypatch.setattr(hub, "STATE", tmp_path / "nothing-here")               # a state dir with none of these files
    assert hub.foot_line(hub.foot_items(hub.foot_sources(now), None, now)) \
        == "health ? · last progress ? · ? · drift ? · codex ok · agy ok · CL ? · GK ? · GPT ? · GEM ?"


def test_the_page_serves_the_facts_from_its_cache_and_reads_no_source_itself(hub, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("serving a view must not read a source of its own")
    monkeypatch.setattr(hub, "foot_sources", boom)
    hub.LOAD_STATE.update({"cpu": [12.0], "ram": 71.2, "gpu": None, "asof": 1.0, "runs": {},
                           "facts": hub.foot_items(_src(), CAPS, NOW)})
    p = hub.load_payload()
    assert p["line"] == "CPU 12% · RAM 71% · GPU ?"                           # the three load cells stay first, unchanged
    assert p["facts"][0]["text"] == "health ok" and len(p["facts"]) == 6


def test_the_triage_rule_and_what_was_rejected_are_stated_beside_the_code(hub):
    src = HUB_PY.read_text()
    sect = src[src.index("the footer's facts (#1797)"):src.index("def load_payload(")]   # the rule sits with the code it governs
    assert "across the room" in sect and "within a minute" in sect and "one short token" in sect
    for gone in ("spend_cap", "throughput", "column header"):                 # rejected for the line, so it is not re-proposed
        assert gone in sect


def test_the_footer_paints_the_facts_beside_the_load_cells_and_the_amber_value(hub):
    assert "id=ldfacts" in hub.PAGE
    assert "footFit(d.facts,footCols())" in hub.PAGE                          # the drop is decided where the width is known
    assert "if(l)ldPaint(l,d);" in hub.PAGE                                   # the cells are painted here since #1816, cell by cell
    assert "<b id=ldline>CPU ? · RAM ? · GPU ?</b>" in hub.PAGE                # and read as before until the first tick lands
    assert ".bad{color:var(--warn)" in hub.PAGE                               # the one value that turns amber does
    assert "nowrap" in _css(hub, "#ldfacts")                                  # the row drops facts, it never wraps


def _foot_text(hub, items, cols):
    """The line the page really builds, run by node over a list of facts and a character budget."""
    js = re.search(r"^function footFit\(.*\n^function footText\(.*$", hub.PAGE, re.M).group(0)
    out = subprocess.run(["node", "-"], input=f"{js}\nconsole.log(footText({json.dumps(items)}, {cols}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def test_the_line_drops_trailing_facts_on_a_narrow_window_and_never_truncates_one(hub):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    items = hub.foot_items(_src(quota={"codex": None, "agy": None}), CAPS, NOW)
    texts = [i["text"] for i in items]
    full = " · ".join(texts)
    assert _foot_text(hub, items, len(full)) == full                           # a wide window keeps all five, in order
    four = " · ".join(texts[:4])
    assert _foot_text(hub, items, len(four)) == four                           # 5 -> 4: the quota pair drops first
    assert _foot_text(hub, items, len(four) - 1) == " · ".join(texts[:3])      # 4 -> 3, still whole items
    assert _foot_text(hub, items, 0) == ""                                     # nothing fits: no number is cut into another


# ---------------------------------------------------------------- the colour of each fact (#1843)
# The facts read in the bar's one grey, so the blocks ran together and a subscription about to run out said nothing a
# glance could catch. These pin the two colours the line now carries: the palette colour that says WHICH fact a block
# is, and the consumption tone that says how much of a subscription's quota is spent — the three tones the load strip
# already draws (#1816), judged against thresholds named beside the load strip's own constants.

def _subs_src(hub, claude=None, hand=None, resets=None):
    """One reading of the subs source: the probe's first window for `claude` — the token these cases read — and `?`
    for the other three, so a single test reads one token's tone without the rest of the block in the way."""
    def row(pct):
        return {"probe": {"windows": [{"pct": pct, "resets": resets}]} if pct is not None else None, "quota": None}
    out = {k: row(claude if k == "claude" else None) for k in hub.SUBS}
    out["claude"] = {**out["claude"], "quota": hand}
    return _src(subs=out)


def _subs_parts(hub, src):
    """The subs block's parts as the page receives them: one per subscription, in SUBS' order."""
    return [i for i in hub.foot_items(src, CAPS, NOW) if i["key"] == "subs"][0]["parts"]


def _foot_js(hub):
    """The footer's per-item paint exactly as the page carries it: the two constants it reads and the two functions."""
    consts = [re.search(rf"^const {n}=.*$", hub.PAGE, re.M) for n in ("esc", "FTONE")]
    assert all(consts), "the page carries no esc/FTONE constant"
    fns = re.findall(r"^function foot(?:Parts|Item)\(.*$", hub.PAGE, re.M)
    assert len(fns) == 2, fns                      # footParts, footItem
    return "\n".join([c.group(0) for c in consts] + fns)


def _foot_paint(hub, items):
    """The line the page really paints for these facts: each block in its own colour, each token in its own tone."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    script = f"{_foot_js(hub)}\nconsole.log({json.dumps(items)}.map(footItem).join(' · '))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def test_the_consumption_thresholds_are_named_beside_the_load_strips_own(hub):
    assert 0 < hub.FOOT_WARN_PCT < hub.FOOT_ERR_PCT <= 100
    src = HUB_PY.read_text()
    # read once, beside the load strip's own pair — never a bare threshold in the render
    assert "FOOT_WARN_PCT = LOAD_WARN_PCT" in src and "FOOT_ERR_PCT = LOAD_ERR_PCT" in src
    assert "FOOT_WARN_PCT" not in hub.PAGE and "FOOT_ERR_PCT" not in hub.PAGE


def test_each_subscription_token_is_painted_by_its_own_consumption_not_the_block_as_a_whole(hub):
    src = _src(subs={"claude": {"probe": {"windows": [{"pct": 20.0, "resets": NOW + 3600}]}, "quota": None},
                     "grok": {"probe": {"windows": [{"pct": hub.FOOT_WARN_PCT, "resets": None}]}, "quota": None},
                     "gpt": {"probe": {"windows": [{"pct": hub.FOOT_ERR_PCT, "resets": None}]}, "quota": None},
                     "gemini": {"probe": None, "quota": None}})
    parts = _subs_parts(hub, src)
    assert [p["text"] for p in parts] == ["CL 20% 1h 0m", "GK 80% ?", "GPT 95% ?", "GEM ?"]
    assert [p["tone"] for p in parts] == ["ok", "warn", "err", hub.FOOT_NEUTRAL]


def test_a_band_is_read_at_its_own_threshold_and_the_one_under_it(hub):
    w, e = hub.FOOT_WARN_PCT, hub.FOOT_ERR_PCT
    for pct, tone in ((0.0, "ok"), (w - 0.1, "ok"), (w, "warn"), (e - 0.1, "warn"), (e, "err"), (100.0, "err")):
        assert _subs_parts(hub, _subs_src(hub, pct))[0]["tone"] == tone, pct
    assert _subs_parts(hub, _subs_src(hub, None))[0]["tone"] == hub.FOOT_NEUTRAL


def test_an_unverifiable_subscription_reads_a_question_mark_and_stays_neutral(hub):
    parts = _subs_parts(hub, _subs_src(hub, None))
    assert parts[0]["text"] == "CL ?"                                          # never painted as if it carried a number
    assert parts[0]["tone"] == hub.FOOT_NEUTRAL
    assert hub.FOOT_NEUTRAL not in hub.FOOT_TONES and hub.FOOT_NEUTRAL not in ("ok", "warn", "err")
    out = _foot_paint(hub, [{"key": "subs", "text": "CL ?", "bad": False, "color": "run", "parts": parts}])
    assert "var(--ok)" not in out and "var(--warn)" not in out and "var(--err)" not in out, out
    # the same paint at the ceiling does carry the red, so the line above is a statement about the tone and not about a
    # paint that never carried a colour at all
    red = _foot_paint(hub, [{"key": "subs", "text": "CL 96% ?", "bad": False, "color": "run",
                             "parts": [{"text": "CL 96% ?", "tone": "err"}]}])
    assert "var(--err)" in red, red


def test_every_fact_block_carries_its_own_colour_from_the_hub_palette(hub):
    items = hub.foot_items(_src(), CAPS, NOW)
    order = [i["color"] for i in items]
    assert [i["key"] for i in items] == ["health", "progress", "occ", "drift", "quota", "subs"]
    root = re.search(r"^:root\{([^}]*)\}", hub.PAGE, re.M).group(1)
    palette = set(re.findall(r"--([a-z-]+):", root))
    assert all(c in palette for c in order), order                             # a colour the hub already has, not a new one
    state = {"ok", "warn", "err"}                                              # a block's colour may never say the value is fine
    assert not state & set(order), order
    assert all(a != b for a, b in zip(order, order[1:], strict=False)), order   # told apart at a glance, neighbours never equal


def test_a_bad_value_still_reads_as_bad_over_its_blocks_own_colour(hub):
    items = hub.foot_items(_src(progress=NOW - 901), CAPS, NOW)
    prog = [i for i in items if i["key"] == "progress"][0]
    assert prog["bad"] is True and prog["color"]
    assert _foot_paint(hub, [prog]) == "<span class=bad>last progress 15m</span>", _foot_paint(hub, [prog])
    subs = [i for i in items if i["key"] == "subs"][0]
    out = _foot_paint(hub, [subs])
    assert f"var(--{subs['color']})" in out                                    # the block wears its own colour...
    assert "var(--err)" not in out                                             # ...and no token of it is red for being one
    red = _foot_paint(hub, [{"key": "subs", "color": subs["color"], "bad": False, "text": "CL 96% ?",
                             "parts": [{"text": "CL 96% ?", "tone": "err"}]}])
    assert "var(--err)" in red and f"var(--{subs['color']})" in red, red        # the bad reading sits ON that colour


def test_the_line_still_reads_the_same_tokens_in_the_same_order(hub):
    items = hub.foot_items(_src(quota={"codex": None, "agy": None}), CAPS, NOW)
    assert hub.foot_line(items) == ("health ok · last progress 4m · 4/10 runs 7/20 agents · drift 0 · "
                                    "codex ok · agy ok · CL ? · GK ? · GPT ? · GEM ?")
    assert all(" · ".join(p["text"] for p in i["parts"]) == i["text"] for i in items)   # a block's parts ARE its text
    painted = re.sub(r"<[^>]+>", "", _foot_paint(hub, items))
    assert painted == hub.foot_line(items)                                     # one row, the same tokens, in the same order


# ---------------------------------------------------------------- the ceiling, per attribute (#1816)
# The strip answered "is this machine saturated?" as a whole line, in amber, and only after the load outlived one sample.
# These pin the other statement it makes: a single reading at the machine's ceiling RIGHT NOW is red, on that attribute's
# own cell and nowhere else — above the amber, never instead of it, and never for a source that could not be read.

def _ld_block(hub):
    """The strip's cell code exactly as the page carries it: the constant and every `ld*` function, before substitution."""
    const = re.search(r"^const LOAD_ERR=.*$", hub.PAGE, re.M)
    assert const, "the page carries no LOAD_ERR constant"
    fns = re.findall(r"^function ld[A-Z]\w*\(.*$", hub.PAGE, re.M)
    assert len(fns) == 5, fns                      # ldNum, ldRound, ldCell, ldCells, ldPaint
    return const.group(0) + "\n" + "\n".join(fns)


def _ld_script(hub):
    """The same code with the substitution the server makes when it serves the page."""
    return _ld_block(hub).replace("__LOADERR__", str(hub.LOAD_ERR_PCT))


# Enough of a DOM for ldPaint to build a line in, and a printer that shows the tags, so the test reads the elements the
# browser would get — not a re-derivation of them.
DOM = """const el=t=>({t:t,kids:[],appendChild(n){this.kids.push(n)},
 set textContent(v){this.kids=v===""?[]:[{textContent:v}]},get textContent(){return this.kids.map(k=>k.textContent).join("")}});
globalThis.document={createElement:t=>el(t),createTextNode:s=>({textContent:s})};
const show=n=>n.kids?`<${n.t}${n.className?` class="${n.className}"`:""}>${n.kids.map(show).join("")}</${n.t}>`:n.textContent;
"""


def _ld_paint(hub, d):
    """The strip's line as the page really paints it: three cells, their own classes, and the separators between them."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    script = f"{_ld_script(hub)}\n{DOM}const l=el('b');ldPaint(l,{json.dumps(d)});console.log(l.kids.map(show).join(''))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def _ld_text(hub, d):
    """The same line without its markup: what the reader sees."""
    return re.sub(r"<[^>]+>", "", _ld_paint(hub, d))


def test_the_strip_still_reads_exactly_as_the_server_builds_it(hub):
    # the client now formats the cells itself; the one thing it may not change is the text
    for d in ({"cpu": 96, "ram": 63, "gpu": 42}, {"cpu": 62.4, "ram": 71.2, "gpu": 27.0},
              {"cpu": None, "ram": None, "gpu": None}, {"cpu": 94.9, "ram": 95.0, "gpu": 100.0},
              {"cpu": 2.5, "ram": 62.5, "gpu": 63.5}, {"cpu": 99.6, "ram": 104.0, "gpu": None},
              {"cpu": 0, "ram": 100, "gpu": 7}):
        assert _ld_text(hub, d) == hub.load_line(d["cpu"], d["ram"], d["gpu"]), d


def test_the_cells_keep_their_order_and_their_own_separators(hub):
    out = _ld_paint(hub, {"cpu": 7, "ram": 63, "gpu": 42})
    assert out == '<b>CPU 7%</b> · <b>RAM 63%</b> · <b>GPU 42%</b>', out
    assert _css(hub, "#foot").count("font-variant-numeric:tabular-nums") == 1   # the digits stay one width: nothing shifts


def test_one_attribute_at_the_ceiling_paints_its_own_cell_and_not_the_line(hub):
    for hot in ("cpu", "ram", "gpu"):
        d = {"cpu": 20, "ram": 21, "gpu": 22, hot: 96}
        out = _ld_paint(hub, d)
        assert out.count('class="ldred"') == 1, out
        assert f'<b class="ldred">{hot.upper()} 96%</b>' in out, out
        assert [c.upper() for c in ("cpu", "ram", "gpu") if f'<b class="ldred">{c.upper()}' in out] == [hot.upper()]


def test_the_ceiling_is_inclusive_so_949_is_not_red_and_950_is(hub):
    assert _ld_paint(hub, {"cpu": 94.9, "ram": 94.9, "gpu": 94.9}).count('class="ldred"') == 0
    assert _ld_paint(hub, {"cpu": 94.999, "ram": 0, "gpu": 0}).count('class="ldred"') == 0
    assert _ld_paint(hub, {"cpu": 95.0, "ram": 95.0, "gpu": 95.0}).count('class="ldred"') == 3
    assert '<b class="ldred">GPU 95%</b>' in _ld_paint(hub, {"cpu": 1, "ram": 2, "gpu": 95.0})


def test_a_source_the_hub_cannot_read_is_a_question_mark_and_never_red(hub):
    out = _ld_paint(hub, {"cpu": None, "ram": 96, "gpu": None})
    assert "CPU ?" in out and "GPU ?" in out and _ld_text(hub, {"cpu": None, "ram": 96, "gpu": None}) == "CPU ? · RAM 96% · GPU ?"
    assert out.count('class="ldred"') == 1 and '<b class="ldred">RAM 96%</b>' in out


def test_red_wins_over_the_amber_of_a_sustained_warning(hub):
    amber, red = _css(hub, "#foot.warn b"), _css(hub, "#foot.warn b.ldred")
    assert "var(--warn)" in amber and "var(--err)" in red      # the whole line stays amber...
    assert "var(--err)" in _css(hub, "#foot b.ldred")          # ...and the cell at the ceiling is red with or without it
    out = _ld_paint(hub, {"cpu": 96, "ram": 88, "gpu": 90})    # a warned strip, one attribute at its ceiling
    assert '<b class="ldred">CPU 96%</b> · <b>RAM 88%</b> · <b>GPU 90%</b>' == out, out


def test_the_ceiling_is_a_named_constant_and_never_a_literal_in_the_page(hub):
    assert isinstance(hub.LOAD_ERR_PCT, (int, float)) and hub.LOAD_WARN_PCT < hub.LOAD_ERR_PCT <= 100
    assert "95" not in _ld_block(hub)                                       # no bare threshold in the page's own script
    assert "__LOADERR__" in hub.PAGE                                        # the page is served the constant
    assert '.replace("__LOADERR__", str(LOAD_ERR_PCT))' in HUB_PY.read_text()


def test_the_served_payload_carries_the_reading_the_cell_is_judged_on(hub):
    # the cell's colour is decided on the number the hub serves, so that number is the reading itself, not a rounded one
    hub.LOAD_STATE.update({"cpu": [94.6], "ram": 71.2, "gpu": None, "asof": 1.0, "runs": {}})
    p = hub.load_payload()
    assert p["cpu"] == 94.6
    assert p["line"] == "CPU 95% · RAM 71% · GPU ?"                          # the text rounds, the colour does not
    assert _ld_paint(hub, p).count('class="ldred"') == 0
    assert _ld_paint(hub, {**p, "cpu": 95.0}).count('class="ldred"') == 1
