"""Hub machine load (#1776): the left pane's footer strip reads CPU, RAM and GPU, and the card of the run that owns
the load says so. These pin the parts that carry a rule — the cell formatting, the `?` fallback when a source cannot
answer, the two-sample warn rule, the attribution by worktree path and the marker that clears with the load."""
import datetime as dt
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
           "workspace": {"name": "child", "path": "/Users/owner/wt-as-1776", "head": "abc123", "state": "clean"}},
          {"event": "outcome", "node": "verify", "outcome": "pass", "ts": "2026-10-11T10:02:00Z",
           "workspace": {"name": ".", "path": "/r/checkout", "head": "def456", "state": "clean"}}]
    p.write_text("".join(json.dumps(e) + "\n" for e in ev))
    assert hub.scan(str(p))["wts"] == ["/Users/owner/wt-as-1776"]   # the target itself is not a worktree of its own


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
    assert "<div class=igs id=igs" in src and "instDraw(d)" in src    # the instruments card and its one paint (#983)
    assert 'const LOAD_MS=Number("__LOADMS__")||5000;' in src
    assert '.replace("__LOADMS__", str(int(LOAD_SAMPLE_SECS * 1000)))' in src
    assert "setInterval(pollIfVisible(loadPoll),LOAD_MS)" in src


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


def test_the_reading_is_a_sidebar_card_never_a_bar_under_the_window(pg):
    # Re-anchored to #983: the owner moved the machine reading from the Settings tab into a left-sidebar card drawn as
    # flight instruments; the alert strip still carries what must be seen at once
    assert "side" in pg.parents["igs"] and "inst" in pg.parents["igs"]
    assert "tab-set" not in pg.parents["igs"]
    assert "win" in pg.parents["side"] and "win" in pg.parents["board"]


def test_the_bar_is_a_row_of_the_windows_column_so_nothing_pushes_it_off(hub, pg):
    body = _css(hub, "body")
    assert "flex-direction:column" in body              # window = panes, then the bar
    win = _css(hub, "#win")
    assert "flex:1" in win and "min-height:0" in win    # the panes take the room left over: they scroll


def test_the_docked_board_stays_inside_the_panes_so_the_bar_keeps_the_full_width(hub, pg):
    # Re-anchored to the owner-approved mock (#889): the board is the Board tab's pane, inside the content column of the window row; the bar is still below both
    assert "win" in pg.parents["content"] and "content" in pg.parents["board"]
    assert "display:flex" in _css(hub, "#win")


def test_one_element_carries_the_reading_however_often_it_is_repainted(hub, pg):
    assert pg.order.count("igs") == 1                                 # one reading on the page, never two that can disagree
    assert hub.PAGE.count("setInterval(pollIfVisible(loadPoll)") == 1                # one timer for the whole window
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


def test_the_board_has_no_grip_its_height_is_the_pages(hub):
    """Re-anchored to the owner-approved mock (#889): the board is a tab pane, so there is no dock height to drag."""
    assert "id=grip" not in hub.PAGE and "#grip" not in hub.PAGE


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
        == "landed ? · queue empty · CL ? · GK ? · GPT ? · GEM ? · Loop:OFF"   # no watchd.pid: the loop is off (#554); the ledger has no open hold: an empty queue is a reading (#1173)


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
    assert p["facts"][0]["text"] == "landed PR #73 2h ago" and len(p["facts"]) == 3


def test_the_triage_rule_and_what_was_rejected_are_stated_beside_the_code(hub):
    src = HUB_PY.read_text()
    sect = src[src.index("the footer's facts (#1797)"):src.index("def load_payload(")]   # the rule sits with the code it governs
    assert "across the room" in sect and "within a minute" in sect and "one short token" in sect
    for gone in ("spend_cap", "throughput", "column header"):                 # rejected for the line, so it is not re-proposed
        assert gone in sect


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
    src = _src(burn=LIVE_BURN, gate={"kind": "fast", "result": "fail", "sha": "ebb70f83f95560f99ad017c990aad406b0c7a6c4", "ts": NOW - 300})
    line = hub.foot_line(hub.foot_items(src, NOW))
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

def test_the_left_pane_carries_only_what_a_reader_watches(hub):
    """The removal is the rule: an accordion named here is a defect, and one that came back would be read by name.
    Re-anchored (#1028): the two configuration cards left the sidebar for the Settings tab. Re-anchored again (owner,
    2026-11-01): Last ended is gone and Live work sits under the Monitor, so the pane reads cost, machine, live work."""
    side = hub.PAGE[hub.PAGE.index("<aside id=side>"):hub.PAGE.index("</aside>")]
    assert re.findall(r"<details id=(\w+)", side) == ["inst", "usage", "stats", "lrd"]
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
    assert [p["text"] for p in parts] == ["CL 20% 1.0h", "GK 80% ?", "GPT 95% ?", "GEM ?"]
    assert [p.get("tip") for p in parts] == ["resets in 1h 0m", None, None, None]
    assert [p["tone"] for p in parts] == ["ok", "warn", "err", hub.FOOT_NEUTRAL]


def test_subscription_reset_span_shows_on_every_tone_in_decimal_days(hub):
    """The owner reads the time to every reset, green tokens too, in one compact rung with one decimal: `5.5d` from a day
    up, `3.3h` under it, `40m` under an hour. The tooltip keeps the exact span."""
    w = hub.FOOT_WARN_PCT
    for pct in (w - 0.1, w, w + 0.1):
        part = _subs_parts(hub, _subs_src(hub, pct, resets=NOW + 3 * 3600 + 20 * 60))[0]
        assert (part["text"], part.get("tip")) == ("CL 80% 3.3h", "resets in 3h 20m"), pct
    days = _subs_parts(hub, _subs_src(hub, w - 0.1, resets=NOW + 5 * 86400 + 12 * 3600))[0]
    assert (days["text"], days.get("tip")) == ("CL 80% 5.5d", "resets in 5d 12h")
    close = _subs_parts(hub, _subs_src(hub, w - 0.1, resets=NOW + 60))[0]
    assert (close["text"], close.get("tip")) == ("CL 80% 1m", "resets in 1m")
    assert [hub._subs_span(x) for x in (59, 40 * 60, 3600, 23.5 * 3600, 86400, 5.5 * 86400)] \
        == ["0m", "40m", "1.0h", "23.5h", "1.0d", "5.5d"]


def test_subscription_question_reset_and_quota_reset_are_never_hidden(hub):
    w = hub.FOOT_WARN_PCT
    for pct in (w - 0.1, w, w + 0.1):
        part = _subs_parts(hub, _subs_src(hub, pct, resets=None))[0]
        assert part["text"] == "CL 80% ?"
        assert "tip" not in part
    reset = time.mktime((2026, 10, 16, 14, 20, 0, 0, 0, -1))
    assert hub._quota_fact({"codex": reset, "agy": None}, NOW, subs=()) == "codex reset 14:20 · agy ok"


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


def test_the_line_still_reads_the_same_tokens_in_the_same_order(hub):
    items = hub.foot_items(_src(quota={"codex": None, "agy": None}), NOW)
    assert hub.foot_line(items) == "landed PR #73 2h ago · CL ? · GK ? · GPT ? · GEM ?"
    assert all(" · ".join(p["text"] for p in i["parts"]) == i["text"] for i in items)   # a block's parts ARE its text


# ---------------------------------------------------------------- the ceiling, per attribute (#1816)
# The strip answered "is this machine saturated?" as a whole line, in amber, and only after the load outlived one sample.
# These pin the other statement it makes: a single reading at the machine's ceiling RIGHT NOW is red, on that attribute's
# own cell and nowhere else — above the amber, never instead of it, and never for a source that could not be read.

def _ld_block(hub):
    """The strip's cell code exactly as the page carries it: the constant and every `ld*` function, before substitution."""
    const = re.search(r"^const LOAD_ERR=.*$", hub.PAGE, re.M)
    assert const, "the page carries no LOAD_ERR constant"
    fns = re.findall(r"^function ld[A-Z]\w*\(.*$", hub.PAGE, re.M)
    assert len(fns) == 4, fns                      # ldNum, ldRound, ldCell, ldCells: the cells the gauges read (#983)
    return const.group(0) + "\n" + "\n".join(fns)


def _ld_script(hub):
    """The same code with the substitution the server makes when it serves the page."""
    return _ld_block(hub).replace("__LOADERR__", str(hub.LOAD_ERR_PCT))


# The printer reads the cells the page computes, in the shape the old strip drew them: the text, the order and the red
# class are the page's own; only the join is the test's. The instruments card draws one gauge per cell (#983).
PRINT = """const show=c=>`<b${c.red?' class="ldred"':""}>${c.k} ${c.t}</b>`;"""


def _ld_paint(hub, d):
    """The three cells as the page computes them: their text, their order and which one is red."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    script = f"{_ld_script(hub)}\n{PRINT}console.log(ldCells({json.dumps(d)}).map(show).join(' · '))"
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
    assert "font-variant-numeric:tabular-nums" in _css(hub, "#inst .gv")      # the digits stay one width: nothing shifts


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
    amber, red = _css(hub, "#inst.warn .gv b"), _css(hub, "#inst .ig.ldred .gv b")
    assert "var(--warn)" in amber and "var(--err)" in red      # the whole card stays amber...
    page = hub.PAGE                                            # ...and the cell at the ceiling is red with or without it: its rule comes later
    assert page.index("#inst.warn .gv b{") < page.index("#inst .ig.ldred .gv b{")
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


# ---------------------------------------------------------------- the subscriptions row (owner, 2026-11-01)

def test_each_subscription_answers_its_weekly_share_and_days_to_reset(hub, monkeypatch):
    """The Monitor reads the weekly window, not the 5 h one; the days to reset come unrounded, for the page to format."""
    now = 1_000_000.0
    monkeypatch.setattr(hub, "quota", {
        "claude": {"asof": now - 60, "plan": "Max", "windows": [
            {"label": "5 h", "pct": 12.0, "resets": now + 3 * 3600},
            {"label": "weekly", "pct": 77.0, "resets": now + 3.44 * 86400}]},
        "grok": {"asof": now - 60, "plan": None, "windows": [{"label": "weekly", "pct": 40.0, "resets": now + 86400}]},
    })
    rows = {r["key"]: r for r in hub.subs_gauges(now)}
    assert rows["claude"]["pct"] == 77.0 and rows["claude"]["window"] == "weekly"
    assert round((rows["claude"]["resets"] - now) / 86400, 2) == 3.44, "the reset is a timestamp, not a rounded day count"
    assert rows["grok"]["pct"] == 40.0


def test_a_provider_with_no_weekly_window_shows_its_monthly_share(hub, monkeypatch):
    """A plan that reports a 5 h window and a monthly one and no weekly. The gauge shows the monthly share (the
    owner's choice), never the window that merely resets last."""
    now = 1_000_000.0
    monkeypatch.setattr(hub, "quota", {
        "grok": {"asof": now - 60, "windows": [
            {"label": "5 h", "pct": 62.2, "resets": now + 4 * 3600},
            {"label": "month", "pct": 7.7, "resets": now + 30 * 86400}]}})
    rows = {r["key"]: r for r in hub.subs_gauges(now)}
    assert rows["grok"]["pct"] == 7.7 and rows["grok"]["window"] == "month"


def test_a_retired_subscription_in_the_stores_surfaces_nowhere(hub, monkeypatch, tmp_path):
    """A subscription the owner cancelled can still sit in the quota cache and the plans file. The Usage panel, the
    Subscriptions list, the plan reader, the preset catalog and the footer read SUBS only, so it never shows."""
    now = 1_000_000.0
    gone = "ki" + "mi"
    plans = tmp_path / "plans.json"
    plans.write_text(json.dumps({gone: {"plan": "code", "amount": 19, "currency": "USD"}}))
    monkeypatch.setattr(hub, "PLANS", plans)
    monkeypatch.setattr(hub, "quota", {gone: {"asof": now - 60, "windows": [{"label": "month", "pct": 99.0, "resets": now + 86400}]}})
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    assert gone not in hub.SUBS and gone not in hub.PRESETS and gone not in hub.plans_get()
    assert gone not in {r["key"] for r in hub.subs_gauges(now)}
    assert gone not in hub.subs_month()
    assert gone not in json.dumps(hub.foot_line([])).lower()


def test_a_subscription_with_no_reading_says_why_and_carries_no_share(hub, monkeypatch):
    monkeypatch.setattr(hub, "quota", {"claude": {"asof": 0, "err": "login expired"}})
    rows = {r["key"]: r for r in hub.subs_gauges(1_000_000.0)}
    assert rows["claude"]["pct"] is None and rows["claude"]["why"], "a missing reading is named, never drawn as zero"
    assert all(r["pct"] is None for r in rows.values())


def test_the_load_payload_carries_the_subscriptions_row(hub):
    assert "subs" in hub.load_payload(), "/api/load leaves the Monitor's subscription instruments out"


def test_the_monitor_draws_a_labelled_bar_and_a_reset_per_subscription(hub):
    """Owner, 2026-11-01: the dials painted as black circles, because the gauge styles are scoped to #inst and the row lives
    under #isub. A named bar answers "how much of the weekly window is spent, and when does it reset" without a caption."""
    page = hub.PAGE
    assert 'id=isub' in page, "no panel carries the subscription row"
    assert page.index("id=usage") < page.index("id=isub") < page.index("id=stats"), "the subscription row leads the Usage panel"
    i = page.index("function subsGauges(d)")
    fn = page[i:page.index("function instGauges(d)", i)]
    assert "gaugeSvg(p)" not in fn, "the dial is back under #isub, where #inst never styles it and it paints black"
    assert "class=sn" in fn and "class=sp2" in fn and "class=sb2" in fn and "class=sr2" in fn, \
        "the row does not draw its plan name, its share, its bar and its reset"
    assert 'style="width:${' in fn, "the bar carries no spent share"
    assert "resets in" in fn, "the reset does not say what the time is"
    assert ".subs" in fn


# ---------------------------------------------------------------- the throughput row (owner, 2026-11-01)

THROUGHPUT_NOW = dt.datetime(2026, 10, 20, 23, 0, tzinfo=dt.UTC)


def _stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _speed_store(hub, tmp_path, monkeypatch, counts, now=THROUGHPUT_NOW, slot_secs=4 * 3600, store_now=0, name="board"):
    """A history store whose six rolling slots hold the given closes, oldest slot first and the window last.
    Each close lands inside its slot, never on a boundary; `store_now` stamps the read."""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("SG_SPEED_WINDOW_SECS", raising=False)
    (tmp_path / name).mkdir(parents=True, exist_ok=True)   # one store per name: the history file is named after the board's folder
    monkeypatch.setattr(hub, "BOARD", tmp_path / name / "board.json")
    monkeypatch.setattr(hub, "REPO", "x/y")
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": []})
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    now_ts, number = now.timestamp(), 0
    for i, count in enumerate(counts):
        k = len(counts) - i - 1   # the slot's distance back from now, the window being 0
        start = now_ts - (k + 1) * slot_secs
        for j in range(count):
            stamp = _stamp(start + slot_secs * (j + 1) / (count + 1))
            number += 1
            hub._hist.record_issue(path, number, stamp, stamp,
                                   [{"t": stamp, "type": "added"}, {"t": stamp, "type": "closed"}], True, store_now)
    return path


def test_the_fact_counts_the_day_and_flags_the_oldest_source_once_it_passes_the_bound(hub, tmp_path, monkeypatch):
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 6], store_now=int(THROUGHPUT_NOW.timestamp()) - 60)
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["day"] == 6 and got["history"]["stale"] is False, got   # each age names its own read (#1661)
    assert "today" not in got
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 6], store_now=int(THROUGHPUT_NOW.timestamp()) - 3600, name="stale")
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["history"]["stale"] is True and got["history"]["age_secs"] == 3600, got


def test_the_speed_window_is_the_last_four_hours_never_the_calendar_day(hub, tmp_path, monkeypatch):
    """The same pattern laid around two clocks twelve hours apart reads the same window figure."""
    morning = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 6], now=morning, name="morning")
    assert hub.throughput_fact(now=morning)["window"] == 6
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 6], now=THROUGHPUT_NOW, name="night")
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["window"] == 6


def test_a_close_leaves_the_window_once_the_clock_passes_it(hub, tmp_path, monkeypatch):
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 3])
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["window"] == 3
    later = THROUGHPUT_NOW + dt.timedelta(hours=4, minutes=1)
    assert hub.throughput_fact(now=later)["window"] == 0, "a close out of the window still counted"


def test_the_speed_fact_reports_six_slots_one_median_and_the_bar_maximum(hub, tmp_path, monkeypatch):
    """Five available finished slots of 4, 9, 21, 6 and 12 closes with 10 in the window: the median is 9, the bar's
    maximum is 21, and the window is never part of the median it is judged against."""
    _speed_store(hub, tmp_path, monkeypatch, [4, 9, 21, 6, 12, 10])
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window_secs"] == 4 * 3600
    assert len(got["slots"]) == 6, "the fact does not carry the six rolling slots"
    assert got["slots"][-1]["n"] == 10 and got["slots"][-1]["finished"] is False, "the newest slot is the window"
    assert got["available"] == [4, 9, 21, 6, 12], "an availability flag is missing from the slots"
    assert got["window"] == 10 and got["median"] == 9 and got["min"] == 4 and got["max"] == 21, got
    assert got["trend"] == "flat", "10 against a median of 9 sits between both thresholds"


def test_an_unavailable_slot_enters_neither_the_median_nor_the_maximum(hub, tmp_path, monkeypatch):
    """The earliest close of the two sources is nine hours old: only the two slots ending after it are available."""
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 1, 2, 0])
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["available"] == [1, 2], (got, "the slots ending before the earliest close must be unavailable")
    assert got["median"] == 1.5 and got["max"] == 2 and got["min"] == 1, got
    assert got["trend"] == "down", "a zero window against a positive median reads slowing, not first day"


def test_a_zero_median_reads_accelerating_for_a_positive_window_and_steady_for_zero(hub, tmp_path, monkeypatch):
    """A zero median makes the ratio meaningless: a window above zero is accelerating, a window of zero is steady."""
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 3], name="positive")
    old = _stamp(THROUGHPUT_NOW.timestamp() - 30 * 3600)   # one close behind every slot keeps them available
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    hub._hist.record_issue(path, 99, old, old, [{"t": old, "type": "closed"}], True, 0)
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["trend"] == "up"
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 0], name="zero")
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    hub._hist.record_issue(path, 99, old, old, [{"t": old, "type": "closed"}], True, 0)
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["trend"] == "flat"


def test_the_bar_maximum_is_never_below_the_window_figure(hub, tmp_path, monkeypatch):
    """A record window fills the bar, so the maximum follows the window above the busiest finished slot."""
    _speed_store(hub, tmp_path, monkeypatch, [1, 1, 1, 1, 1, 30])
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["max"] == 30, "the bar's maximum fell below the window it draws"
    assert got["trend"] == "up"


def test_the_speed_age_is_the_oldest_of_the_two_sources(hub, tmp_path, monkeypatch):
    """#1193: a fresh history stamp must never suppress a stalled board cache, so the fact carries the older read."""
    now_ts = THROUGHPUT_NOW.timestamp()
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 2], store_now=now_ts - 7200)
    monkeypatch.setattr(hub, "board_cache", {"t": now_ts - 3600,
                                             "items": [{"n": 9, "s": "closed", "c": "2026-10-20T17:00:00Z"}]})
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["history"]["as_of"] == now_ts - 7200 and got["history"]["age_secs"] == 7200, (got, "the newer cache hid the stalled store")
    monkeypatch.setattr(hub, "board_cache", {"t": now_ts, "items": [{"n": 9, "s": "closed", "c": "2026-10-20T17:00:00Z"}]})
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["history"]["age_secs"] == 7200, "a fresh cache hid the stalled store"


def test_the_speed_window_follows_the_environment_override(hub, tmp_path, monkeypatch):
    """SG_SPEED_WINDOW_SECS moves the window and the slots without a code change."""
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 4], slot_secs=3600)
    monkeypatch.setenv("SG_SPEED_WINDOW_SECS", "3600")
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window_secs"] == 3600 and got["window"] == 4, got
    assert got["slots"][-1]["end"] - got["slots"][-1]["start"] == 3600
    assert len(got["slots"]) == 6


def _speed_html(hub, sp):
    """The throughput card as the page draws it, run by node."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    i = hub.PAGE.index("function speedRow(")
    script = f"{esc}\n{hub.PAGE[i:hub.PAGE.index('function instUsage', i)]}\nconsole.log(speedRow({json.dumps(sp)}))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _speed_text(hub, sp):
    """The throughput card's words, markup stripped."""
    return re.sub(r"<[^>]+>", " ", _speed_html(hub, sp))


def _specs(done=5, total=9, open_n=3, bar=True, state="info", value="5 of 9 closed - 55%"):
    """A spec progress fact as the hub returns it: the counts the Specs row draws."""
    return {"open": open_n, "done": done, "total": total, "pct": done * 100 // total if total else 0,
            "frac": done / total if total else 0, "value": value, "state": state, "bar": bar, "tip": ""}


def _row(closed, total):
    """A Tasks, Bugs or Loop-bugs row as the hub returns it: the closed count, the total and the served text."""
    value = "none" if not total else f"{closed} of {total} closed"
    return {"closed": closed, "total": total, "value": value, "frac": closed / total if total else 0}


def _speed(**kw):
    """A throughput fact as the hub returns it: four hours, median 9, maximum 21, the rows, overridden per case."""
    fact = {"window": 10, "window_secs": 4 * 3600, "day": 10, "median": 9, "min": 4, "max": 21, "trend": "up",
            "board": {"age_secs": 60, "stale": False, "as_of": 0},
            "history": {"age_secs": 60, "stale": False}, "labels": {"age_secs": 60, "stale": False},
            "tasks": _row(4, 10),
            "arrivals": {"bugs": _row(3, 7), "loop_bugs": _row(2, 4)},
            "specs": _specs()}
    fact.update(kw)
    return fact


def test_the_throughput_row_with_no_finished_slot_names_the_wait_and_draws_no_trend(hub):
    """No finished slot to compare with: the row names the wait for one and draws no trend word."""
    text = _speed_text(hub, _speed(window=5, day=5, median=None, min=None, max=5, trend=None))
    assert "steady" not in text and "accelerating" not in text and "slowing" not in text, text
    assert "first day" in text, text
    assert re.search(r"5\s+cards closed in the last 4 h", text), text


def test_the_throughput_row_names_the_trend_it_compared(hub):
    """The trend word follows the median the screen draws, and the unit says what the number is."""
    up = _speed_text(hub, _speed())
    assert "accelerating" in up and "first day" not in up, up
    assert "cards closed in the last 4 h" in up and "per day" not in up, up
    down = _speed_text(hub, _speed(window=2, day=2, trend="down"))
    assert "slowing" in down, down


def test_the_card_reads_the_rows_in_their_tones(hub):
    html = _speed_html(hub, _speed())
    assert 'class="spw ok" role=img aria-label="Tasks: 4 of 10 closed"' in html, html
    assert 'class="spw warn" role=img aria-label="Bugs: 3 of 7 closed"' in html, html
    assert 'class="spw err" role=img aria-label="Loop-bugs: 2 of 4 closed"' in html, html
    assert 'class="spw spec" role=img aria-label="Specs: 5 of 9 closed - 55%"' in html, html


def test_the_bug_rows_read_the_tasks_rule_of_closed_over_total(hub):
    """The owner, 2026-11-13: the Bugs and Loop-bugs rows read the same rule as Tasks, how many of the kind are closed."""
    text = _speed_text(hub, _speed())
    assert "opened" not in text, text
    assert re.search(r"Bugs\s+3 of 7 closed", text), text
    assert re.search(r"Loop-bugs\s+2 of 4 closed", text), text


def test_the_row_bar_draws_closed_over_total(hub):
    html = _speed_html(hub, _speed())
    row = re.search(r'class="spw warn".*?</div>', html, re.S).group(0)
    assert '<span class=spwv>3 of 7 closed</span>' in row, row
    assert re.search(r'<i style="width:42\.85\d*%"></i>', row), row
    zero = _speed_html(hub, _speed(arrivals={"bugs": _row(0, 0), "loop_bugs": _row(0, 0)}))
    assert "Bugs" not in zero and "Loop-bugs" not in zero, zero   # a kind with no card is not drawn (#2285)


def _drawn_rows(html):
    """The row labels the card draws, in order: the words inside the label span of each row."""
    return re.findall(r"<span class=spwl>([^<]+)</span>", html)


def test_a_row_with_nothing_to_count_is_not_drawn(hub):
    """#2285: a row whose total is zero has no label, no text and no bar; only the three kinds with cards draw."""
    html = _speed_html(hub, _speed(tasks=_row(0, 0), arrivals={"bugs": _row(0, 0), "loop_bugs": _row(0, 0)},
                                   specs=_specs(open_n=0, value="no open spec", bar=False, state="none")))
    assert _drawn_rows(html) == [], html
    assert "Tasks" not in html and "Bugs" not in html and "Loop-bugs" not in html and "Specs" not in html, html
    assert "spwt" not in html, "a hidden row draws no bar"
    text = re.sub(r"<[^>]+>", " ", html)
    assert re.search(r"10\s+cards closed in the last 4 h", text), "the hero line still draws"
    assert "spr role=img" in html, "the hero bar still draws"


def test_each_kind_row_hides_on_its_own_zero_total(hub):
    """#2285: the rule holds per row: one kind with no card leaves the other two drawn."""
    cases = [("tasks", _row(0, 0), "Tasks"), ("bugs", _row(0, 0), "Bugs"), ("loop_bugs", _row(0, 0), "Loop-bugs")]
    for key, zero, name in cases:
        kw = {"tasks": _row(4, 10), "arrivals": {"bugs": _row(3, 7), "loop_bugs": _row(2, 4)}}
        if key == "tasks":
            kw["tasks"] = zero
        else:
            kw["arrivals"][key] = zero
        rows = _drawn_rows(_speed_html(hub, _speed(**kw)))
        assert name not in rows, (name, rows)
        assert [n for n in ("Tasks", "Bugs", "Loop-bugs", "Specs") if n != name and n in rows], (name, rows)


def test_a_row_above_zero_with_zero_closed_draws_an_empty_bar(hub):
    """#2285: a kind with cards and none closed stays drawn, with a bar at no fill."""
    html = _speed_html(hub, _speed(arrivals={"bugs": _row(0, 2), "loop_bugs": _row(2, 4)}))
    row = re.search(r'class="spw warn".*?</div>', html, re.S).group(0)
    assert '<span class=spwv>0 of 2 closed</span>' in row, row
    assert '<i style="width:0%"></i>' in row, row


def test_the_specs_row_is_not_drawn_when_no_spec_is_open(hub):
    """#2285: no open spec leaves no Specs row, with no history and with one."""
    no_spec = _specs(open_n=0, value="no open spec", bar=False, state="none")
    for sp in (_speed(specs=no_spec), None):
        html = _speed_html(hub, sp) if sp else _run_speed(hub, "null", no_spec)
        assert "Specs" not in html, html
        assert "spw spec" not in html, html


def _run_speed(hub, sp_json, specs):
    """The card drawn for a fact given as raw JSON, so the no-history case keeps the Specs fact beside it."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    i = hub.PAGE.index("function speedRow(")
    script = f"{esc}\n{hub.PAGE[i:hub.PAGE.index('function instUsage', i)]}\nconsole.log(speedRow({sp_json}, {json.dumps(specs)}))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_row_of_every_card_closed_fills_its_track(hub):
    """The track is closed over the kind's whole count, so every card closed fills it and nothing overflows."""
    html = _speed_html(hub, _speed(arrivals={"bugs": _row(5, 5), "loop_bugs": _row(0, 0)}))
    row = re.search(r'class="spw warn".*?</div>', html, re.S).group(0)
    assert '<i style="width:100%"></i>' in row, row


def test_the_specs_row_carries_the_per_spec_tip(hub):
    """The old Specs tile's drill-down stays reachable on the row: a wrong count says which spec it came from."""
    sp = _speed()
    sp["specs"]["tip"] = "#1 Spec 1 — 2/5\n#2 Spec 2 — 3/4"
    html = _speed_html(hub, sp)
    assert 'title="#1 Spec 1 — 2/5\n#2 Spec 2 — 3/4"' in html, html


def test_the_bug_rows_carry_no_window(hub):
    """The rows count the board, never a window: the hero line already names the window's closures."""
    html = _speed_html(hub, _speed())
    assert 'title="last 4 h' not in html, html


def test_the_specs_row_reads_the_open_specs_with_no_second_line(hub):
    """Owner, 2026-11-18: the Specs row reads 'N of M closed - P%' over open specs, with no second line."""
    text = _speed_text(hub, _speed())
    assert re.search(r"Specs\s+5 of 9 closed - 55%", text), text
    assert "open" not in re.search(r"Specs.*", text).group(0), text
    html = _speed_html(hub, _speed())
    assert "spws" not in html and "no spec" not in html, html
    assert re.search(r'class="spw spec".*<i style="width:55\.55\d*%"></i>', html), html


def test_the_bar_draws_the_median_tick_and_the_window_fill(hub):
    html = _speed_html(hub, _speed())
    assert 'class=spmed' in html, html
    assert 'style="width:47.61904761904762%"' in html, "the window fill is not the window over the maximum"


def test_no_close_in_the_day_reads_no_history_with_no_bar(hub):
    html = _speed_html(hub, _speed(window=0, day=0, median=None, min=None, max=None, trend=None,
                                   tasks=_row(0, 0),
                                   arrivals={"bugs": _row(0, 0), "loop_bugs": _row(0, 0)}))
    assert "no history yet" in html and "spr role=img" not in html, html


def test_closes_before_the_window_draw_the_bar_at_zero_fill(hub):
    html = _speed_html(hub, _speed(window=0, day=6, trend="down"))
    assert 'style="width:0%"' in html and "spr role=img" in html, html


def test_a_kind_with_no_card_leaves_no_row_behind(hub):
    """#2285: a row with nothing to count is not drawn, so no muted row names an empty kind."""
    empty = _row(0, 0)
    html = _speed_html(hub, _speed(window=0, day=6, trend="down",
                                   arrivals={"bugs": empty, "loop_bugs": empty}))
    assert 'class="spw mute"' not in html and "Bugs" not in html and "Loop-bugs" not in html, html
    assert 'class="spw ok" role=img aria-label="Tasks: 4 of 10 closed"' in html, "the kind with cards stays drawn"


def test_the_last_line_names_the_age_only_once_the_oldest_source_is_stale(hub):
    assert "· history read 16 m ago" in _speed_text(hub, _speed(history={"age_secs": 16 * 60 + 30, "stale": True}))
    assert "· history read" not in _speed_text(hub, _speed())


def test_the_stale_label_note_appears_past_two_minutes_and_not_before(hub):
    """#1424: the card names an old label read, so a row that misses a new label says why."""
    fresh = _speed_text(hub, _speed(labels={"age_secs": 120, "stale": False}))
    assert "labels" not in fresh, fresh
    old = _speed_text(hub, _speed(labels={"age_secs": 130, "stale": True}))
    assert re.search(r"labels from the board read 2 min old", old), old


def test_the_label_stale_bound_is_the_hub_side_120_seconds(hub, tmp_path, monkeypatch):
    """#1424: the bound derives from the board cache's own stamp: 120 s reads fresh, 121 s reads stale, and a cache
    with no stamp never claims an age."""
    now_ts = THROUGHPUT_NOW.timestamp()
    _arrival_items(hub, tmp_path, monkeypatch, [_arrival_card(1, ["bug"], now_ts - 3600)])
    cache = dict(hub.board_cache)
    monkeypatch.setattr(hub, "board_cache", dict(cache, t=now_ts - 120))
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["labels"] == {"age_secs": 120, "stale": False}
    monkeypatch.setattr(hub, "board_cache", dict(cache, t=now_ts - 121))
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["labels"] == {"age_secs": 121, "stale": True}
    monkeypatch.setattr(hub, "board_cache", dict(cache, t=0))
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["labels"] == {"age_secs": None, "stale": False}


def test_no_history_reads_no_speed_and_the_page_draws_no_fake_zero(hub):
    assert hub.throughput_fact() is None
    assert "no history yet" in hub.PAGE


def test_the_window_count_follows_the_board_cache_not_a_quiet_history_store(hub):
    """#1193, owner: a store that stops moving must not freeze the number. A card the cache shows closed counts."""
    now = dt.datetime(2026, 11, 2, 18, 0, tzinfo=dt.UTC).timestamp()
    items = [{"n": 1, "s": "closed", "c": "2026-11-02T17:00:00Z"},
             {"n": 2, "s": "closed", "c": "2026-11-01T23:00:00Z"},
             {"n": 3, "s": "open"},
             {"n": 4, "s": "closed"}]
    assert hub.closed_since(items, now, 4 * 3600) == 1
    assert hub.closed_since([], now, 4 * 3600) is None


# ---------------------------------------------------------------- the window rows (owner, 2026-11-18, #2264)

def _arrival_items(hub, tmp_path, monkeypatch, items, now=THROUGHPUT_NOW):
    """The fact's two inputs for a window-row test: an empty history store and a board cache holding the given items,
    stamped at the read instant the cache carries."""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("SG_SPEED_WINDOW_SECS", raising=False)
    (tmp_path / "board").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub, "BOARD", tmp_path / "board" / "board.json")
    monkeypatch.setattr(hub, "REPO", "x/y")
    now_ts = now.timestamp()
    monkeypatch.setattr(hub, "board_cache", {"t": now_ts, "items": items, "prs": [], "err": None})
    return now_ts


def _arrival_card(n, labels, opened, closed=None):
    """One cache row as the items read writes it: `o` the creation stamp, `c` the close stamp, `l` the labels."""
    row = {"n": n, "t": f"card {n}", "s": "open", "c": None, "st": "Todo", "l": list(labels), "o": _stamp(opened)}
    if closed is not None:
        row.update(s="closed", st="Done", c=_stamp(closed))
    return row


def test_the_bug_rows_count_closed_in_the_window_over_open_now_and_closed(hub, tmp_path, monkeypatch):
    """Owner, 2026-11-18: `closed` counts the kind closed in the window; `total` is `closed` plus the kind open now."""
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_arrival_card(1, ["bug"], now_ts - 3 * 3600),                                          # open now
             _arrival_card(2, ["bug", "loop-bug:landing"], now_ts - 2 * 3600, closed=now_ts - 1 * 3600),   # closed in window
             _arrival_card(3, ["bug"], now_ts - 6 * 3600, closed=now_ts - 30 * 60),                  # closed in window
             _arrival_card(4, ["bug"], now_ts - 8 * 3600, closed=now_ts - 7 * 3600),                 # closed before window
             _arrival_card(5, ["bug"], now_ts - 5 * 3600),                                           # open now
             _arrival_card(6, ["enhancement"], now_ts - 30 * 60),                                    # not a defect
             _arrival_card(7, ["loop-bug:dispatch"], now_ts - 8 * 3600)]                             # open now, a loop defect
    _arrival_items(hub, tmp_path, monkeypatch, items)
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert {k: arrivals["bugs"][k] for k in ("closed", "total", "value")} == {"closed": 2, "total": 5, "value": "2 of 5 closed"}, arrivals
    assert {k: arrivals["loop_bugs"][k] for k in ("closed", "total", "value")} == {"closed": 1, "total": 2, "value": "1 of 2 closed"}, arrivals


def test_the_loop_bugs_row_is_never_above_its_bugs_superset(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_arrival_card(1, ["loop-bug:dispatch"], now_ts - 2 * 3600, closed=now_ts - 1800),
             _arrival_card(2, ["bug"], now_ts - 2 * 3600)]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert arrivals["loop_bugs"]["total"] <= arrivals["bugs"]["total"]
    assert arrivals["loop_bugs"]["closed"] <= arrivals["bugs"]["closed"]


def test_an_abandoned_card_is_in_no_window_row_count(hub, tmp_path, monkeypatch):
    """Owner, 2026-11-18: an abandoned card is in no count, closed or open."""
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [{**_arrival_card(1, ["bug"], now_ts - 2 * 3600, closed=now_ts - 1800), "cr": "not_planned"},
             {**_arrival_card(2, ["bug"], now_ts - 2 * 3600), "st": "Abandoned"},
             _arrival_card(3, ["bug"], now_ts - 2 * 3600, closed=now_ts - 900)]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert (arrivals["bugs"]["closed"], arrivals["bugs"]["total"]) == (1, 1), arrivals


def test_a_row_with_no_card_of_its_kind_reads_none(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    _arrival_items(hub, tmp_path, monkeypatch, [_arrival_card(1, ["enhancement"], now_ts - 1800)])
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert arrivals["bugs"] == {"closed": 0, "total": 0, "value": "none", "frac": 0}, arrivals


def test_an_open_card_counts_in_total_whatever_its_age(hub, tmp_path, monkeypatch):
    """Owner, 2026-11-18: `total` takes every card the board draws open now; the window bounds only the closures."""
    now_ts = THROUGHPUT_NOW.timestamp()
    _arrival_items(hub, tmp_path, monkeypatch, [_arrival_card(1, ["bug"], now_ts - 30 * 86400)])
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert (arrivals["bugs"]["closed"], arrivals["bugs"]["total"], arrivals["bugs"]["value"]) == (0, 1, "0 of 1 closed"), arrivals


def test_a_loop_defect_labelled_only_landing_counts_in_both_rows(hub, tmp_path, monkeypatch):
    """A card carrying no `bug` label still enters the Bugs row through the prefix rule, and the Loop-bugs row with it."""
    now_ts = THROUGHPUT_NOW.timestamp()
    _arrival_items(hub, tmp_path, monkeypatch, [_arrival_card(1, ["loop-bug:landing"], now_ts - 2 * 3600)])
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert (arrivals["bugs"]["closed"], arrivals["bugs"]["total"]) == (0, 1), arrivals
    assert (arrivals["loop_bugs"]["closed"], arrivals["loop_bugs"]["total"]) == (0, 1), arrivals


def test_a_closed_card_with_an_unreadable_creation_stamp_still_counts_when_closed_in_the_window(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [{**_arrival_card(1, ["bug"], 0, closed=now_ts - 1800), "o": "not a stamp"}]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert (arrivals["bugs"]["closed"], arrivals["bugs"]["total"]) == (1, 1), arrivals


def test_the_window_rows_follow_the_same_window_override_as_the_hero(hub, tmp_path, monkeypatch):
    """SG_SPEED_WINDOW_SECS moves the window rows with the hero figure: one window, not two."""
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_arrival_card(1, ["bug"], now_ts - 3 * 3600, closed=now_ts - 90 * 60),
             _arrival_card(2, ["bug"], now_ts - 3 * 3600, closed=now_ts - 30 * 60)]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    monkeypatch.setenv("SG_SPEED_WINDOW_SECS", "3600")
    arrivals = hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]
    assert (arrivals["bugs"]["closed"], arrivals["bugs"]["total"], arrivals["bugs"]["value"]) == (1, 1, "1 of 1 closed"), arrivals


def test_the_specs_row_is_cumulative_while_the_window_rows_keep_the_window(hub, tmp_path, monkeypatch):
    """Owner, 2026-11-18: the Specs row counts every open spec whatever its age, while the window rows keep the window."""
    now_ts = THROUGHPUT_NOW.timestamp()
    bug = _arrival_card(1, ["bug"], now_ts - 8 * 3600, closed=now_ts - 7 * 3600)
    spec = {"n": 2, "t": "Old spec", "s": "OPEN", "st": "Todo", "l": ["spec"],
            "si": {"completed": 3, "total": 4}, "o": _stamp(now_ts - 30 * 3600)}
    _arrival_items(hub, tmp_path, monkeypatch, [bug, spec])
    assert hub.throughput_fact(now=THROUGHPUT_NOW)["arrivals"]["bugs"]["value"] == "none"
    served = hub.load_payload()["specs"]
    assert (served["done"], served["total"], served["value"]) == (3, 4, "3 of 4 closed - 75%")


def test_the_window_rows_read_the_stamp_the_snapshot_already_carries(hub, tmp_path):
    """The rows run on the rows the items read writes, with no added snapshot field: a REST card taken through the
    hub's own jq emits the creation stamp the fact counts, labels included."""
    jq = shutil.which("jq")
    assert jq, "jq runs the items program the hub reads with"
    rest = [{"content_type": "Issue", "id": "PVTI_1", "fields": [{"name": "Status", "value": {"name": {"raw": "Todo"}}}],
             "content": {"id": 911, "number": 1, "repository_url": "https://api.github.com/repos/x/y", "title": "card 1",
                         "state": "OPEN", "state_reason": None, "closed_at": None,
                         "labels": [{"name": "bug"}, {"name": "loop-bug:dispatch"}],
                         "sub_issues_summary": {}, "created_at": _stamp(THROUGHPUT_NOW.timestamp() - 3600)}}]
    src = tmp_path / "items.json"
    src.write_text(json.dumps(rest))
    out = subprocess.run([jq, "-c", hub.board_items_jq("x/y"), str(src)], capture_output=True, text=True, check=True).stdout
    rows = [json.loads(line) for line in out.splitlines() if "_page" not in line]
    now_ts = THROUGHPUT_NOW.timestamp()
    assert hub.arrivals_fact(rows, now_ts, 4 * 3600)["bugs"]["total"] == 1, "the snapshot's own row is not a window row input"


# ---------------------------------------------------------------- the Tasks row (owner, 2026-11-18, #2264)

def _task_card(n, closed, labels=(), reason=None, state="closed"):
    """One cache row as the items read writes it: the close stamp `c`, the labels `l`, the close reason `cr`, and a
    creation stamp well before the window so the Tasks row takes it only by its close."""
    return {"n": n, "s": state, "c": _stamp(closed) if closed is not None else None,
            "st": "Done" if state == "closed" else "Todo", "l": list(labels), "cr": reason,
            "o": _stamp(THROUGHPUT_NOW.timestamp() - 6 * 3600)}


def test_the_tasks_row_counts_closed_in_the_window_over_open_now_and_closed(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_task_card(1, now_ts - 1 * 3600),                                    # closed in window: a task
             _task_card(2, now_ts - 2 * 3600, ["enhancement"]),                  # closed in window: a task
             _task_card(3, now_ts - 3 * 3600, ["spec"]),                         # a spec: never a task
             _task_card(4, now_ts - 90 * 60, ["bug"]),                           # a defect: never a task
             _task_card(5, now_ts - 60 * 60, ["loop-bug:dispatch"]),             # a loop defect: never a task
             _task_card(6, now_ts - 30 * 60, reason="not_planned"),              # abandoned: no count
             _task_card(7, now_ts - 6 * 3600),                                   # closed before the window: no count
             _task_card(8, None, state="open"),                                  # open now: a task
             _task_card(9, None, ["spec"], state="open"),                        # an open spec: never a task
             {**_task_card(10, None, state="open"), "o": _stamp(now_ts - 30 * 86400)}]   # open now, opened long ago
    _arrival_items(hub, tmp_path, monkeypatch, items)
    tasks = hub.throughput_fact(now=THROUGHPUT_NOW)["tasks"]
    assert tasks == {"closed": 2, "total": 4, "value": "2 of 4 closed", "frac": 0.5}, tasks


def test_a_closed_spec_stays_in_the_hero_figure_and_is_never_a_task(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_task_card(1, now_ts - 1 * 3600, ["spec"]),
             _task_card(2, now_ts - 2 * 3600, ["spec", "wave:02"])]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window"] == 2, "the hero figure counts a closed spec"
    assert got["tasks"] == {"closed": 0, "total": 0, "value": "none", "frac": 0}, got["tasks"]


def test_an_abandoned_card_stays_in_the_hero_figure_and_is_never_a_task(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_task_card(1, now_ts - 1 * 3600, reason="not_planned"),
             _task_card(2, now_ts - 2 * 3600, reason="completed")]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window"] == 2, "the hero figure counts a card closed as not planned"
    assert got["tasks"] == {"closed": 1, "total": 1, "value": "1 of 1 closed", "frac": 1.0}, got["tasks"]


def test_a_closed_defect_is_never_a_task_whatever_its_bug_spelling(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_task_card(i, now_ts - i * 60, labels) for i, labels in
             enumerate([["bug"], ["loop-bug"], ["loop-bug:landing"], ["loop-bug:dispatch"], ["bug", "loop-bug:infra"]], 1)]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window"] == 5, "the hero figure counts closed defects"
    assert got["tasks"] == {"closed": 0, "total": 0, "value": "none", "frac": 0}, got["tasks"]


def test_the_tasks_row_reads_the_close_stamps_the_hero_reads_from_the_store_too(hub, tmp_path, monkeypatch):
    """With no board cache the hero falls back to the store's `closed` events. A store event carries no label, so it
    is a closure without a task."""
    _speed_store(hub, tmp_path, monkeypatch, [0, 0, 0, 0, 0, 3])
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window"] == 3, "the hero figure's store fallback moved"
    assert got["tasks"] == {"closed": 0, "total": 0, "value": "none", "frac": 0}, got["tasks"]


def test_the_tasks_row_follows_the_same_window_override_as_the_hero(hub, tmp_path, monkeypatch):
    """SG_SPEED_WINDOW_SECS moves the Tasks row with the hero figure: one window, not two (#2264)."""
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_task_card(1, now_ts - 90 * 60),
             _task_card(2, now_ts - 30 * 60)]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    monkeypatch.setenv("SG_SPEED_WINDOW_SECS", "3600")
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window"] == 1, "the hero figure did not follow the override"
    assert got["tasks"] == {"closed": 1, "total": 1, "value": "1 of 1 closed", "frac": 1.0}, got["tasks"]


def test_no_card_closed_in_the_window_reads_the_tasks_row_none(hub, tmp_path, monkeypatch):
    now_ts = THROUGHPUT_NOW.timestamp()
    _arrival_items(hub, tmp_path, monkeypatch, [_task_card(1, now_ts - 8 * 3600)])
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["window"] == 0 and got["tasks"] == {"closed": 0, "total": 0, "value": "none", "frac": 0}, got["tasks"]


def test_a_card_closed_as_a_duplicate_is_not_a_task(hub, tmp_path, monkeypatch):
    """The carrier's not-delivered vocabulary is board_columns' one copy (#921): a duplicate closure is Abandoned, so it
    is in no Tasks count."""
    now_ts = THROUGHPUT_NOW.timestamp()
    items = [_task_card(1, now_ts - 1 * 60, reason="duplicate"),
             _task_card(2, now_ts - 2 * 60)]
    _arrival_items(hub, tmp_path, monkeypatch, items)
    got = hub.throughput_fact(now=THROUGHPUT_NOW)
    assert got["tasks"] == {"closed": 1, "total": 1, "value": "1 of 1 closed", "frac": 1.0}, got["tasks"]


def test_the_tasks_row_reads_the_reason_and_labels_the_snapshot_already_carries(hub, tmp_path):
    """The row runs on the rows the items read writes, with no added snapshot field: a REST card taken through the hub's
    own jq emits the close reason and the labels the fact counts (#2264)."""
    jq = shutil.which("jq")
    assert jq, "jq runs the items program the hub reads with"
    now_ts = THROUGHPUT_NOW.timestamp()
    def rest(n, reason, labels):
        return {"content_type": "Issue", "id": f"PVTI_{n}", "fields": [{"name": "Status", "value": {"name": {"raw": "Done"}}}],
                "content": {"id": 900 + n, "number": n, "repository_url": "https://api.github.com/repos/x/y",
                            "title": f"card {n}", "state": "CLOSED", "state_reason": reason, "closed_at": _stamp(now_ts - 60 * n),
                            "labels": [{"name": x} for x in labels], "sub_issues_summary": {},
                            "created_at": _stamp(now_ts - 6 * 3600)}}
    src = tmp_path / "items.json"
    src.write_text(json.dumps([rest(1, "completed", []), rest(2, "completed", ["bug"]),
                               rest(3, "not_planned", ["spec"])]))
    out = subprocess.run([jq, "-c", hub.board_items_jq("x/y"), str(src)], capture_output=True, text=True, check=True).stdout
    rows = [json.loads(line) for line in out.splitlines() if "_page" not in line]
    assert rows[0]["cr"] == "completed" and rows[2]["cr"] == "not_planned", "the snapshot drops the close reason"
    assert hub.tasks_fact([dict(r, s="closed") for r in rows], now_ts, 4 * 3600) == \
        {"closed": 1, "total": 1, "value": "1 of 1 closed", "frac": 1.0}, \
        "the snapshot's own row is not a Tasks row input, and a defect and a spec are no task either"


def test_a_cancelled_subscription_leaves_the_usage_panel_and_the_subscriptions_list(hub):
    """#1529: the owner cancelled Kimi. The Usage panel reads hub.SUBS and the Settings > Subscriptions list reads the
    registry's subscriptions (subscription_states), so neither names it, and no registry model is billed to it."""
    from squad import modelreg
    reg = modelreg.default_registry()
    assert "kimi" not in hub.SUBS and "KIMI" not in hub.SUB_ACR.values()
    assert "kimi" not in reg.subscriptions and "kimi" not in hub.subscription_states()
    assert not [m for m in reg.entries if "kimi" in str(m).lower()]
