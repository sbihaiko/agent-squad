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
    # the checkout is this test's own, never the one the runner was started in: a source read under the runner's own
    # repository would be a reading of this machine, not of the fixture (#151)
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_SHARED"] = tempfile.mkdtemp()
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


def test_the_run_pane_is_hidden_with_visibility_and_holds_no_frame_while_it_is(hub):
    css = _css(hub, "body.dockb.max #main")
    assert "visibility:hidden" in css
    assert "display:none" not in css               # its space is what the board takes; the frame is detached by the panes rule (#34), not by the CSS
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
let __syncs=0;const liveSync=()=>{__syncs++};   /* the panes rule's own repaint: counted, never run here */
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
steps.seenAfter=[...new Set(__seen)];steps.madeAfter=[...__made];steps.syncs=__syncs;
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


def test_the_toggle_hands_the_run_pane_to_the_panes_rule_and_touches_no_frame_itself(hub):
    st = _board_steps(hub, {})
    assert "#main" not in st["seen"] and "#main" not in st["seenAfter"]   # the pane is not even looked up
    assert st["made"] == [] and st["madeAfter"] == []                     # the toggle builds nothing
    assert "drawMain" not in _board_slice(hub) and "frames" not in _board_slice(hub)
    assert st["syncs"] == 6                                               # each of the six presses (four maximize, one collapse, one Esc) asks the panes rule to repaint once


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
# the caps the graph carries, the quota markers the review chain skips models over, the drop on a window too narrow for
# the row, and the one token per subscription (#1839) the line carries last — the acronym, the quota and the time to its
# reset. The loop's own health and its last progress were dropped from the line by the owner: both said as much about the
# loop's mood as about the machine, and the line is a machine reading.

NOW = 1_000_000.0
CAPS = {"runs": 10, "children": 20}


def _src(**over):
    """One reading of every source, all readable, so a test can break exactly one of them."""
    base = {"landing": {"issue": 73, "ts": NOW - (2 * 3600 + 14 * 60)},
            "quota": {"codex": None, "agy": None}}
    return {**base, **over}


def _facts(hub, src=None):
    return [i["text"] for i in hub.foot_items(src or _src(), NOW)]


def test_the_facts_follow_the_load_cells_in_this_order(hub):
    reset = time.mktime((2026, 10, 11, 14, 20, 0, 0, 0, -1))                 # the reset the marker names: the subs block carries it
    items = hub.foot_items(_src(quota={"codex": None, "agy": reset}), NOW)
    assert [i["key"] for i in items] == ["landing", "subs"]
    assert hub.foot_line(items) == "landed PR #73 2h ago · CL ? · GK ? · GPT ? · GEM ?"
    assert [i["bad"] for i in items] == [False] * 2


def test_the_occupancy_pair_and_the_worktree_count_carry_no_block_of_their_own(hub):
    """#455: the occupancy pair repeated the WORKERS header one screen above, and the worktree count went to the left
    pane. Both left the line, so neither may be drawn on it again. The header's own readings are guarded in
    `test_hub_occupancy_header.py`, so the rule did not leave with the block."""
    keys = [i["key"] for i in hub.foot_items(_src(occ={"live": 4, "agents": 7}, wt=3), NOW)]
    assert keys == ["landing", "subs"] and "occ" not in keys and "wt" not in keys
    line = hub.foot_line(hub.foot_items(_src(occ={"live": 4, "agents": 7}, wt=3), NOW))
    assert "runs" not in line and "agents" not in line and "wt" not in line, line
    assert "runs" not in hub.FOOT_BLOCK_COLORS and "wt" not in hub.FOOT_BLOCK_COLORS   # the palette left with the blocks


def test_the_bar_carries_no_drift_fact(hub):
    """The bar shows what an operator acts on: the last landing, the gate, the burn and the quotas. The board-versus-runs
    drift is a cause of the loop's own health pass, never a fact of the bar (#48)."""
    for s in [_src()]:
        line = hub.foot_line(hub.foot_items(s, NOW))
        assert "drift" not in line, line
    assert [i["key"] for i in hub.foot_items(_src(), NOW)] == ["landing", "subs"]


def test_only_the_limited_quota_markers_are_named_each_with_its_reset(hub):
    """The block's own text, read with no subscription in SUBS: the day a token leaves the subs block, its marker returns."""
    reset = time.mktime((2026, 10, 11, 14, 20, 0, 0, 0, -1))
    assert hub._quota_fact({"codex": reset, "agy": reset}, NOW, subs=()) == "codex reset 14:20 · agy reset 14:20"
    assert hub._quota_fact({"codex": None, "agy": None}, NOW, subs=()) == "codex ok · agy ok"
    assert hub._quota_fact({"codex": "?", "agy": None}, NOW, subs=()) == "codex ? · agy ok"   # one unreadable marker only


def test_a_marker_the_subs_block_already_carries_is_not_named_twice(hub):
    """`codex` and `GPT` are one subscription, `agy` and `GEM` another: the purple block repeated the green tokens (#87).
    The pairing is one constant; a marker is skipped while its subscription is in SUBS and returns when it leaves."""
    assert hub.MARKER_SUB == {"codex": "gpt", "agy": "gemini"}
    assert all(v in hub.SUBS for v in hub.MARKER_SUB.values())
    reset = time.mktime((2026, 10, 11, 14, 20, 0, 0, 0, -1))
    assert hub._quota_fact({"codex": reset, "agy": reset}, NOW) == ""
    assert hub._quota_fact({"codex": reset, "agy": reset}, NOW, subs=("claude", "gpt")) == "agy reset 14:20"
    assert hub._quota_fact({"codex": None, "agy": None}, NOW, subs=("gemini",)) == "codex ok"
    items = hub.foot_items(_src(quota={"codex": reset, "agy": reset}), NOW)
    assert "quota" not in [i["key"] for i in items]                          # an empty block draws no lone `·`
    assert " ·  · " not in hub.foot_line(items) and "codex" not in hub.foot_line(items)
    assert "quota" in hub.FOOT_BLOCK_COLORS                                   # the colour waits for the day a marker returns


def test_a_quota_marker_counts_only_while_the_time_it_names_is_ahead(hub):
    assert hub.limited_until(str(int(NOW + 60)), NOW) == NOW + 60
    assert hub.limited_until(str(int(NOW - 60)), NOW) is None                # the window passed: the model is back
    assert hub.limited_until("", NOW) is None                                # no marker: nothing was spent
    assert hub.limited_until("soon", NOW) == "?"                             # a file naming no time the hub can read


def test_one_unreadable_source_shows_a_question_mark_and_leaves_the_rest_intact(hub):
    line = hub.foot_line(hub.foot_items(_src(landing=None), NOW))
    assert line == "landed ? · CL ? · GK ? · GPT ? · GEM ?"
    # a source this reading does not carry at all draws no block of its own, and the landing still says `?`
    assert hub.foot_line(hub.foot_items({"quota": {"codex": "?", "agy": None}}, NOW)) \
        == "landed ? · CL ? · GK ? · GPT ? · GEM ?"


def test_the_facts_are_read_from_the_files_the_loop_already_writes(hub, tmp_path, monkeypatch):
    now = time.time()
    st = tmp_path / "st"; (st / "auto").mkdir(parents=True)
    shared = tmp_path / "shared"; shared.mkdir()   # the provider limit markers: the one shared directory, not the repository's
    (shared / "agy-limited").write_text(str(int(now + 3600)))
    monkeypatch.setattr(hub, "STATE", st)
    monkeypatch.setattr(hub, "SHARED", shared)
    src = hub.foot_sources(now)
    assert src["quota"]["agy"] == int(now + 3600) and src["quota"]["codex"] is None
    assert "wt" not in src                                          # the pass reads no worktree count: it left with the accordion
    facts = hub.foot_line(hub.foot_items(src, now))
    assert "drift" not in facts and "agy" not in facts
    assert "landed ?" in facts                                      # no merged PR read: no landing to name
    monkeypatch.setattr(hub, "STATE", tmp_path / "nothing-here")               # a state dir with none of these files
    monkeypatch.setattr(hub, "SHARED", tmp_path / "nothing-here")              # and a shared dir with no limit marker
    assert hub.foot_line(hub.foot_items(hub.foot_sources(now), now)) \
        == "landed ? · CL ? · GK ? · GPT ? · GEM ?"


def _iso(ts):
    """One merge time in the shape the read that fills it writes."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _landed_row(n, age, now, merged=True, at=None, repo="o/r"):
    """One row of the landing ledger the board pass holds, in the shape `landed_branches_of` builds."""
    return {"n": n, "t": f"landing {n}", "c": _iso(now - age if at is None else at), "m": merged, "r": repo}


def test_the_last_landing_is_the_newest_merged_pull_request(hub, monkeypatch):
    """Delivery is a pull request (#75): the merge is the landing. The newest merge names the bar and its merge time is
    the age; an older merge still names its own age."""
    now = time.time()
    monkeypatch.setattr(hub, "board_cache", {"landed": [_landed_row(42, 5 * 3600, now)]})
    assert hub.foot_sources(now)["landing"] == {"issue": 42, "ts": pytest.approx(now - 5 * 3600, abs=2)}
    assert "landed PR #42 5h ago" in hub.foot_line(hub.foot_items(hub.foot_sources(now), now))
    monkeypatch.setattr(hub, "board_cache", {"landed": [_landed_row(42, 5 * 3600, now),
                                                      _landed_row(73, 2 * 3600 + 14 * 60, now)]})
    assert hub.foot_sources(now)["landing"]["issue"] == 73                      # the newest merge wins
    assert "landed PR #73 2h ago" in hub.foot_line(hub.foot_items(hub.foot_sources(now), now))


def test_a_closed_pull_request_that_never_merged_is_not_a_landing(hub, monkeypatch):
    """A pull request closed without a merge landed nothing: it is named by its own column, never by this fact."""
    now = time.time()
    monkeypatch.setattr(hub, "board_cache", {"landed": [_landed_row(12, 60, now, merged=False)]})
    assert hub.foot_sources(now)["landing"] is None
    assert "landed ?" in hub.foot_line(hub.foot_items(hub.foot_sources(now), now))
    monkeypatch.setattr(hub, "board_cache", {"landed": [_landed_row(12, 60, now, merged=False),
                                                      _landed_row(9, 3 * 86400 + 2 * 3600, now)]})
    assert "landed PR #9 3d ago" in hub.foot_line(hub.foot_items(hub.foot_sources(now), now))


def test_a_merge_time_the_hub_cannot_read_is_not_a_landing(hub, monkeypatch):
    now = time.time()
    monkeypatch.setattr(hub, "board_cache", {"landed": [{"n": 7, "t": "landing", "c": "soon", "m": True, "r": "o/r"},
                                                      {"n": 8, "t": "landing", "m": True, "r": "o/r"}]})
    assert hub.foot_sources(now)["landing"] is None
    assert "landed ?" in hub.foot_line(hub.foot_items(hub.foot_sources(now), now))


def test_no_marker_reads_a_question_mark_in_the_neutral_tone_never_a_landing(hub):
    for src in (_src(landing=None), _src(landing={"issue": 73}), _src(landing={"issue": 73, "ts": "soon"}),
                _src(landing={"ts": NOW - 60})):
        item = [i for i in hub.foot_items(src, NOW) if i["key"] == "landing"][0]
        assert item["text"] == "landed ?" and item["bad"] is False, src
        assert [p["tone"] for p in item["parts"]] == [hub.FOOT_NEUTRAL]
    item = [i for i in hub.foot_items(_src(), NOW) if i["key"] == "landing"][0]
    assert item["text"] == "landed PR #73 2h ago" and item["bad"] is False
    assert [p["text"] for p in item["parts"]] == ["landed PR #73 2h ago"]      # one token: the age is one rung, not three
    assert [p["tone"] for p in item["parts"]] == [""]                          # the block's colour stands for the value
    assert hub.FOOT_BLOCK_COLORS["landing"] not in hub.FOOT_TONES


def test_the_age_of_a_fact_reads_one_rung_and_the_countdown_of_a_plan_keeps_two(hub):
    """#455: `16h 13m ago` is a precision nobody reads across the room, and the reader acts on the rung. A subscription's
    time to reset is the one span that keeps two rungs, because it is the time the reader plans by."""
    assert [hub._age(x) for x in (40, 600, 7200, 16 * 3600 + 13 * 60, 172800, 3 * 86400)] \
        == ["40s", "10m", "2h", "16h", "2d", "3d"]
    assert all(re.fullmatch(r"\d+[smhd]", hub._age(x)) for x in range(0, 400000, 997))   # one rung at every age, never `2h 14m`
    assert hub._age(16 * 3600 + 13 * 60) == hub._age(16 * 3600 + 25 * 60)      # under the rung: two ages one reading
    assert hub._subs_reset(NOW + 3 * 3600 + 20 * 60, NOW) == "3h 20m"          # the countdown keeps its two rungs


def test_the_page_serves_the_facts_from_its_cache_and_reads_no_source_itself(hub, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("serving a view must not read a source of its own")
    monkeypatch.setattr(hub, "foot_sources", boom)
    hub.LOAD_STATE.update({"cpu": [12.0], "ram": 71.2, "gpu": None, "asof": 1.0, "runs": {},
                           "facts": hub.foot_items(_src(), NOW)})
    p = hub.load_payload()
    assert p["line"] == "CPU 12% · RAM 71% · GPU ?"                           # the three load cells stay first, unchanged
    assert p["facts"][0]["text"] == "landed PR #73 2h ago" and len(p["facts"]) == 2


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


def test_the_line_drops_trailing_facts_and_never_empties_itself(hub):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    items = hub.foot_items(_src(quota={"codex": None, "agy": None}), NOW)
    texts = [i["text"] for i in items]
    full = " · ".join(texts)
    assert _foot_text(hub, items, len(full)) == full                           # a wide window keeps all three, in order
    two = " · ".join(texts[:2])
    assert _foot_text(hub, items, len(two)) == two                             # 3 -> 2: the last block drops whole
    assert _foot_text(hub, items, len(two) - 1) == texts[0]                    # 2 -> 1, still a whole block
    # The last block is never dropped, it is cut (#446): a bar that has one fact to carry draws it, cut, and never the
    # machine reading alone. The live footer did the opposite on 2026-10-16 and drew no fact at all.
    cut = _foot_text(hub, items, 12)
    assert cut.endswith("…") and len(cut) == 12 and texts[0].startswith(cut[:-1].rstrip())
    assert _foot_text(hub, items, 0) == "…"                                    # no room at all: the bar says it is cut


def test_a_block_wider_than_the_budget_is_cut_and_still_carried(hub):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    wide = {"key": "burn", "text": "tokens high (limit 5M/24h): " + "x" * 260, "bad": True}
    assert _foot_text(hub, [wide], 40) == wide["text"][:39] + "…"               # the block the line cannot fit is the one it keeps
    assert _foot_text(hub, [wide, {"key": "gate", "text": "gate fast fail ebb70f8 5m ago"}], 40).endswith("…")


LIVE_BURN = {"over": True, "threshold": 5000000, "window_hours": 24, "burners": [
    {"consumer": "run:run-20261015-191451-9f6b30:route.router.1", "tokens": 16910258},
    {"consumer": "run:run-20261015-184237-60acae:route.router.1", "tokens": 7159543},
    {"consumer": "run:run-20261015-191935-e168a6:route.router.1", "tokens": 5693232},
    {"consumer": "run:run-20261015-184826-c2d878:route.router.1", "tokens": 5378177},
    {"consumer": "run:run-20261015-180805-d879a7:route.router.1", "tokens": 5138278}]}


def test_the_burn_block_states_the_spend_and_the_count_never_a_name(hub):
    """The live reading of 2026-10-16: five burners, 40.3M spent, the worst 16.9M. The block stated one of the names and
    the count of the rest -- `…9f6b30:route.router.1 16.9M +4` -- and the owner read it as soup. Two numbers now, and the
    names on the hover."""
    fact = hub._burn_fact(LIVE_BURN)
    assert fact == "tokens 40.3M/24h · 5 over 5M", fact
    assert "(" not in fact and "route.router" not in fact                      # one clause, and no machine id on the row
    assert len(fact) <= hub.BURN_MAX_CHARS and "run-20261015-191451-9f6b30" not in fact
    tip = hub._burn_tip(LIVE_BURN)
    assert tip.startswith("5 calls, each over 5M in 24h · …9f6b30:route.router.1 16.9M") and tip.endswith("+2 more"), tip
    assert hub._burn_fact({**LIVE_BURN, "over": False}) == ""                  # a reading under the threshold draws no block
    # a reading the hub cannot compare against a threshold states the same two numbers and names the cap as a word
    assert hub._burn_fact({"over": True, "burners": LIVE_BURN["burners"]}) == "tokens 40.3M/24h · 5 over the cap"


def test_the_gate_and_the_landing_stay_on_the_line_beside_the_burn(hub):
    """#446: the bar drew the three cells and no fact at all, one 287-character block having emptied the line."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    src = _src(burn=LIVE_BURN, gate={"kind": "fast", "result": "fail", "sha": "ebb70f83f95560f99ad017c990aad406b0c7a6c4", "ts": NOW - 300})
    line = _foot_text(hub, hub.foot_items(src, NOW), 160)
    assert line.startswith(hub._burn_fact(LIVE_BURN)), line
    assert "landed PR #73 2h ago" in line and "gate fast fail 5m ago" in line, line
    # the sha the gate ran at is MOVED off the row and never lost: it is the block's tooltip (#455)
    gate = [i for i in hub.foot_items(src, NOW) if i["key"] == "gate"][0]
    assert gate["tip"] == "ran at ebb70f8" and "tip" in hub.PAGE and "title=" in hub.PAGE


# ---------------------------------------------------------------- the worktrees accordion and its one press (#455)
# The footer counted the child trees (`worktrees/child/*`) and the cleaner only ever looked at `runs/*/ws/*`: two sets, so
# the number a reader saw was not the set a pass could free (measured 2026-10-13: 21 trees in the first set, none of them
# ever examined). The count left the footer line for a fourth left-pane accordion (#455, `10/21`, with the `clean now`
# press beside it). That accordion left the page (the owner's ruling 2026-10-16: it read poorly in the left bar, and the
# pass its press asked for by hand runs on every third cycle by itself), so the count, the press, the reading behind them
# and the route the press called left together. `wtclean.sh` is untouched: it holds the rules and the loop still runs it.

def test_the_left_pane_carries_three_accordions_and_no_worktree_one(hub):
    """The removal is the rule: a fourth accordion named here is a defect, and one that came back would be read by name."""
    left = [i for i in re.findall(r"<details id=(\w+)", hub.PAGE) if i in ("plans", "mdl", "stats", "wts")]
    assert left == ["plans", "mdl", "stats"]
    for gone in ("id=wts", "id=wtsr", "id=wtb", "id=wtcl", "id=wtmsg", "wtDraw", "wtClean", "wtSay", "wtBusy"):
        assert gone not in hub.PAGE, gone


def test_the_hub_reads_no_worktree_count_and_answers_no_cleanup_route(hub):
    """What the accordion drew left with it: no reading of the child trees, no hand-run pass, no route at all."""
    src = HUB_PY.read_text()
    for gone in ("def wt_parts", "def wt_clean", "def _wt_count", "def _wt_plan", "WT_DIRS", "WT_PLAN_SECS",
                 "WT_CLEAN_SECS", "wt.json", '"/api/wtclean"'):
        assert gone not in src, gone
    assert "wt" not in hub.foot_sources()          # the pass that serves the line does not read the trees
    assert "wt" not in hub.load_payload()          # and serves no count to the page
    assert [i for i in hub.foot_items(_src(wt=3), NOW) if i["key"] == "wt"] == []   # nor draws a block of the line


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
    return [i for i in hub.foot_items(src, NOW) if i["key"] == "subs"][0]["parts"]


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
    assert [p["text"] for p in parts] == ["CL 20%", "GK 80% ?", "GPT 95% ?", "GEM ?"]
    assert [p.get("tip") for p in parts] == ["resets in 1h 0m", None, None, None]
    assert [p["tone"] for p in parts] == ["ok", "warn", "err", hub.FOOT_NEUTRAL]


def test_subscription_reset_span_follows_the_warn_ladder_and_keeps_its_token_tooltip(hub):
    w = hub.FOOT_WARN_PCT
    cases = ((w - 0.1, "CL 80%", "resets in 3h 20m"),
             (w, "CL 80% 3h 20m", None),
             (w + 0.1, "CL 80% 3h 20m", None))
    for pct, text, tip in cases:
        part = _subs_parts(hub, _subs_src(hub, pct, resets=NOW + 3 * 3600 + 20 * 60))[0]
        assert (part["text"], part.get("tip")) == (text, tip)
    close = _subs_parts(hub, _subs_src(hub, w - 0.1, resets=NOW + 60))[0]
    assert (close["text"], close.get("tip")) == ("CL 80%", "resets in 1m")


def test_subscription_question_reset_and_quota_reset_are_never_hidden(hub):
    w = hub.FOOT_WARN_PCT
    for pct in (w - 0.1, w, w + 0.1):
        part = _subs_parts(hub, _subs_src(hub, pct, resets=None))[0]
        assert part["text"] == "CL 80% ?"
        assert "tip" not in part
    reset = time.mktime((2026, 10, 16, 14, 20, 0, 0, 0, -1))
    assert hub._quota_fact({"codex": reset, "agy": None}, NOW, subs=()) == "codex reset 14:20 · agy ok"


def test_the_page_renders_the_subscription_token_tooltip_from_its_fact(hub):
    part = _subs_parts(hub, _subs_src(hub, hub.FOOT_WARN_PCT - 0.1, resets=NOW + 3 * 3600 + 20 * 60))[0]
    out = _foot_paint(hub, [{"key": "subs", "text": part["text"], "bad": False, "color": "run", "parts": [part]}])
    assert 'title="resets in 3h 20m"' in out


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
    items = hub.foot_items(_src(gate={"kind": "fast", "result": "pass", "sha": "ebb70f8", "ts": NOW - 300}), NOW)
    order = [i["color"] for i in items]
    assert [i["key"] for i in items] == ["landing", "gate", "subs"]
    root = re.search(r"^:root\{([^}]*)\}", hub.PAGE, re.M).group(1)
    palette = set(re.findall(r"--([a-z-]+):", root))
    assert all(c in palette for c in order), order                             # a colour the hub already has, not a new one
    state = {"ok", "warn", "err"}                                              # a block's colour may never say the value is fine
    assert not state & set(order), order
    assert all(a != b for a, b in zip(order, order[1:], strict=False)), order   # told apart at a glance, neighbours never equal


def test_a_bad_value_still_reads_as_bad_over_its_blocks_own_colour(hub):
    """No fact of the line turns amber since health and progress left it (#1843's rule still stands for the day one returns)."""
    items = hub.foot_items(_src(), NOW)
    assert not [i for i in items if i["bad"]]
    bad = {"key": "landing", "color": "wait", "bad": True, "text": "last landing ?", "parts": [{"text": "last landing ?", "tone": ""}]}
    assert _foot_paint(hub, [bad]) == "<span class=bad>last landing ?</span>", _foot_paint(hub, [bad])
    subs = [i for i in items if i["key"] == "subs"][0]
    out = _foot_paint(hub, [subs])
    assert f"var(--{subs['color']})" in out                                    # the block wears its own colour...
    assert "var(--err)" not in out                                             # ...and no token of it is red for being one
    red = _foot_paint(hub, [{"key": "subs", "color": subs["color"], "bad": False, "text": "CL 96% ?",
                             "parts": [{"text": "CL 96% ?", "tone": "err"}]}])
    assert "var(--err)" in red and f"var(--{subs['color']})" in red, red        # the bad reading sits ON that colour


def test_the_line_still_reads_the_same_tokens_in_the_same_order(hub):
    items = hub.foot_items(_src(quota={"codex": None, "agy": None}), NOW)
    assert hub.foot_line(items) == "landed PR #73 2h ago · CL ? · GK ? · GPT ? · GEM ?"
    assert all(" · ".join(p["text"] for p in i["parts"]) == i["text"] for i in items)   # a block's parts ARE its text
    painted = re.sub(r"<[^>]+>", "", _foot_paint(hub, items))
    assert painted == hub.foot_line(items)                                     # one row, the same tokens, in the same order


def test_the_blocks_are_joined_by_a_separator_the_page_draws_and_never_a_blocks_own(hub):
    """#455: a block's inner ` · ` was the same glyph as the separator between blocks, so the blocks ran together as twelve
    tokens. The page draws the join now — a dimmed span with its own margin — and the span is narrower than the ` · ` the
    width budget counts, so a row fitted to the budget never overflows the bar."""
    assert "FOOT_SEP_HTML" in hub.PAGE and "class=sep" in hub.PAGE
    assert ".sep{margin:" in hub.PAGE and re.search(r"#ldfacts \.sep\{[^}]*color:", hub.PAGE)
    assert "join(FOOT_SEP_HTML)" in hub.PAGE                                   # the drawn join is what the row is built with
    assert 'join(" · ")' in hub.PAGE                                           # the plain join stays the width the budget counts
    sep = re.search(r"^const FOOT_SEP_HTML=(.*)$", hub.PAGE, re.M).group(1)
    assert "&middot;" in sep and " · " not in sep                              # the drawn join is never the row's own ` · ` text
    assert "gap:10px" in hub.PAGE and "#ldfacts{margin-left:20px" in hub.PAGE  # the breathing room of the same change


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
