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
    assert "location" not in board and "reload" not in board


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
  press:__el["#bz"].attrs["aria-pressed"],title:__el["#bz"].title,icon:__el["#bz"].innerHTML,
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
    script = _NODE_PRE.replace("__STORED__", json.dumps(json.dumps(saved))) + _board_slice(hub) + _NODE_POST
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.split("RESULT")[-1])


def test_the_choice_is_saved_with_the_boards_other_options(hub):
    st = _board_steps(hub, {})
    assert st["init"]["body"] == ["dockb"] and st["init"]["press"] == "false"
    assert st["maxOn"]["body"] == ["dockb", "max"] and st["maxOn"]["saved"] == {"dock": "bottom", "max": True}
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
