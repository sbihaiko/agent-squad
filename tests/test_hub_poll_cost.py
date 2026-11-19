"""The hub computes a polled answer once per the page's own poll window, and a hidden page asks nothing (#2440).

One open page made the hub burn 0.47 core. Two of the costs are answers every page and every client ask the same
question for: `/api/stats` costs 2.5 s per build and `/api/profiler` costs 0.8 to 1.8 s, and each is rebuilt on every
request. The route keeps its answer for the page's own poll window, as `/api/live` does since #1933, so any number of
pages share one build and the answer a page reads is never older than the poll that would have refreshed it anyway.

The rest of the cost is time: the run watcher reads every record's stamp at a fifth of a second, and the desktop page
polls every endpoint while it is hidden. Both are asserted here as the page and the tick are served.

The clock is injected, so freshness is a fact of the test and never a sleep (ADR-0012).
"""
import http.client
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_poll_cost", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (pathlib.Path(mod.STATE) / "auto").mkdir(parents=True, exist_ok=True)
    return mod


class Served:
    """One hub handler, its counted builds, the clock under the test's hand and the record file a build reads."""

    def __init__(self, port, stats_calls, prof_calls, clock, records):
        self.port, self.stats_calls, self.prof_calls, self.clock, self.records = port, stats_calls, prof_calls, clock, records


@pytest.fixture
def served(hub, monkeypatch):
    """The hub's own handler over HTTP, with each costly build counted and one clock under the test's hand."""
    stats_calls, prof_calls, clock = [], [], [1000.0]
    records = pathlib.Path(tempfile.mkdtemp()) / "records.txt"   # what the profiler build reads, one run a line
    records.write_text("run-a\n")

    def counted_stats(period, src="both"):
        stats_calls.append((period, src))
        return {"buckets": [], "pace": {}, "totals": {}, "models": [], "operator": {}}

    def counted_prof(*a, **k):
        prof_calls.append(1)
        return {"rows": records.read_text().split()}

    monkeypatch.setattr(hub, "stats", counted_stats)
    monkeypatch.setattr(hub, "profiler_view", counted_prof)
    monkeypatch.setattr(hub, "POLL_CLOCK", lambda: clock[0], raising=False)
    monkeypatch.setattr(hub, "_STATS_KEPT", {}, raising=False)
    monkeypatch.setattr(hub, "_PROF_KEPT", {}, raising=False)
    monkeypatch.setattr(hub, "PROFILER_SWITCH", pathlib.Path(tempfile.mkdtemp()) / "profiler.json", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield Served(srv.server_port, stats_calls, prof_calls, clock, records)
    finally:
        srv.shutdown()
        srv.server_close()


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = json.loads(r.read())
    c.close()
    assert r.status == 200, body
    return body


def test_two_stats_reads_inside_the_poll_window_build_once(served):
    """One page's two reads inside its poll window share one build; the next window builds again."""
    port, stats_calls, clock = served.port, served.stats_calls, served.clock
    first = get(port, "/api/stats?period=day&src=both")
    second = get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 1, f"two reads inside one window built the spend answer {len(stats_calls)} times"
    assert first == second, "the second read inside the window serves the same answer"
    clock[0] += 1000.0
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 2, f"a read past the window built the answer {len(stats_calls)} times"


def test_the_spend_answer_is_kept_per_period_and_source(served):
    """The page asks for a period and a billing source: one pair's answer never serves another pair."""
    port, stats_calls = served.port, served.stats_calls
    get(port, "/api/stats?period=day&src=both")
    get(port, "/api/stats?period=hour&src=both")
    get(port, "/api/stats?period=day&src=sub")
    assert stats_calls == [("day", "both"), ("hour", "both"), ("day", "sub")], stats_calls


def test_the_spend_answer_is_per_profile(hub, monkeypatch):
    """The key carries no profile because the module is one per profile: hubmux loads `hub_<slug>`, so two profiles load two
    modules and each keeps its own answer."""
    other = importlib.util.module_from_spec(importlib.util.spec_from_file_location("hub_poll_cost_other", HUB_PY))
    other.__spec__.loader.exec_module(other)
    assert other._STATS_KEPT is not hub._STATS_KEPT, "two loaded profiles share one kept spend answer"


def test_the_spend_answer_is_no_older_than_twenty_seconds(hub, served):
    """The issue's Expected list: `/api/stats` answers from a cache no older than 20 s."""
    assert hub.STATS_POLL_SECS <= 20.0, f"STATS_POLL_SECS is {hub.STATS_POLL_SECS}, older than the 20 s the issue asks"
    get(served.port, "/api/stats?period=day&src=both")
    served.clock[0] += 19.9
    get(served.port, "/api/stats?period=day&src=both")
    assert len(served.stats_calls) == 1, "a read under 20 s must not wait on a fresh build"
    served.clock[0] += 0.2
    get(served.port, "/api/stats?period=day&src=both")
    assert len(served.stats_calls) == 2, "a read past 20 s must build again"


def test_the_profiler_file_view_is_built_once_per_off_window(served):
    """With the sampling switch off the page reads the file views every 30 s: one build per window, shared by pages."""
    get(served.port, "/api/profiler")
    get(served.port, "/api/profiler")
    assert len(served.prof_calls) == 1, f"two reads inside one window built the profiler view {len(served.prof_calls)} times"
    served.clock[0] += 1000.0
    get(served.port, "/api/profiler")
    assert len(served.prof_calls) == 2, "a read past the window builds the view again"


def test_the_off_switch_view_is_served_from_a_cache_under_a_fifth_of_a_second(served):
    """The issue's Expected list: the switch-off file views come from a cache and cost under 0.2 s."""
    get(served.port, "/api/profiler")
    start = time.monotonic()
    for _ in range(5):
        get(served.port, "/api/profiler")
    cost = (time.monotonic() - start) / 5
    assert len(served.prof_calls) == 1, "a cached read built the view again"
    assert cost < 0.2, f"a cached profiler read cost {cost:.3f} s, over the issue's 0.2 s"


def test_a_record_written_after_a_cached_read_shows_within_the_switch_on_cadence(served, hub):
    """The page polls every 2 s with the switch on: a record written after a cached read is served at the next poll."""
    assert 0 < hub.PROF_ON_SECS <= 2.0, f"PROF_ON_SECS is {hub.PROF_ON_SECS}, slower than the page's 2 s poll"
    hub.PROFILER_SWITCH.write_text(json.dumps({"on": True, "at": 0}))
    assert get(served.port, "/api/profiler")["rows"] == ["run-a"]
    served.records.write_text("run-a\nrun-b\n")   # a record written after the read that built the kept view
    served.clock[0] += 2.0
    assert get(served.port, "/api/profiler")["rows"] == ["run-a", "run-b"], \
        "a record written after a cached read waited past the page's next poll"


def test_the_switch_write_clears_the_kept_profiler_view(hub, served):
    """A switch write is the page's own change: the next read shows it, never a view kept from before it."""
    assert callable(getattr(hub, "clear_prof_cache", None)), "the switch write clears the kept view through `clear_prof_cache`"
    get(served.port, "/api/profiler")
    hub.clear_prof_cache()
    get(served.port, "/api/profiler")
    assert len(served.prof_calls) == 2, "the view kept before the write served the read after it"


def test_the_watch_tick_reads_a_change_within_two_seconds(hub):
    """The watcher's tick reads a changed record within 2 s, beats the fallback poll, and does not read five times a second."""
    assert 0.5 <= hub.RUN_WATCH_SECS <= 2.0, (
        f"RUN_WATCH_SECS is {hub.RUN_WATCH_SECS}: the watcher must read a change within 2 s and no faster than two ticks a second")
    assert hub.RUN_WATCH_SECS <= hub.RUN_POLL_SECS / 2, (
        f"RUN_WATCH_SECS is {hub.RUN_WATCH_SECS}: the watcher must be under half the fallback poll {hub.RUN_POLL_SECS}, "
        "else the fallback does its work")


def test_the_watch_tick_costs_under_a_fiftieth_of_a_core(hub, tmp_path):
    """The issue's Expected list: the watcher costs under 0.02 core. The tick's measured time over its own period is its
    share of the core, taken as the best of five readings of the same tree so a busy machine is never the claim."""
    for n in range(200):
        d = tmp_path / f"run-20261115-{n:06d}-aaaaaa"
        d.mkdir()
        (d / "record.jsonl").write_text("{}")
    ticks = []
    for _ in range(5):
        start = time.monotonic()
        hub.run_watch_state([tmp_path])
        ticks.append(time.monotonic() - start)
    cost = min(ticks) / hub.RUN_WATCH_SECS
    assert cost < 0.02, f"the watch tick costs {cost:.4f} core: {min(ticks) * 1000:.1f} ms a tick over 200 records"


# ---- the page's own visible gate, run in node --------------------------------------------------------
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

GATE_LINES = ("const pollIfVisible=", "poll();setInterval(pollIfVisible(poll),",
              "loadPoll();setInterval(pollIfVisible(loadPoll),", "pollBoard();setInterval(pollIfVisible(pollBoard),",
              "setInterval(pollIfVisible(squadChartTick),", 'addEventListener("visibilitychange"')


def _gate_script(hub, body):
    """The page's own gate and its four poll registrations, run in node over stubs that count each route as a fetch.

    The stubs stand for the four pollers' work and never for the gate: the gate line is the page's own text, so a page
    that lost it fails here."""
    page = hub.served_page("")
    lines = [next(line for line in page.splitlines() if line.startswith(p)) for p in GATE_LINES]
    stubs = """
const FETCHED=[],INTERVALS=[],HANDLERS={};
globalThis.document={hidden:true,addEventListener(t,f){HANDLERS[t]=f}};
globalThis.addEventListener=(t,f)=>{HANDLERS[t]=f};
globalThis.setInterval=(f,ms)=>{INTERVALS.push(f)};
globalThis.fetch=async u=>{FETCHED.push(u);return {json:async()=>({})}};
const poll=()=>fetch("/api/loop"),loadPoll=()=>fetch("/api/load"),pollBoard=()=>fetch("/api/board"),
      squadChartTick=()=>fetch("/api/chart/cfd"),profPoll=()=>fetch("/api/profiler");
const LOAD_MS=5000,TAB="loop",profV={},profDue=()=>true;
"""
    script = stubs + "\n".join(lines) + "\nFETCHED.length=0;\n" + body + "\n"
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_a_hidden_page_asks_nothing_for_board_loop_load_or_chart(hub):
    """document.hidden=true: no interval callback fires a fetch for the board, the loop, the load or the chart."""
    out = _gate_script(hub, "for(const f of INTERVALS)f();console.log(JSON.stringify({n:INTERVALS.length,fetched:FETCHED}));")
    assert out["n"] == 4, "the page registers one timer for the board, the loop, the load and the chart"
    assert out["fetched"] == [], "a hidden page read: " + ", ".join(out["fetched"])


@needs_node
def test_a_page_back_in_front_restarts_the_polls(hub):
    """visibilitychange on a hidden page reads nothing; back in front, it reads the loop, the board and the profiler."""
    out = _gate_script(hub, """
document.hidden=true;HANDLERS.visibilitychange();
const whileHidden=FETCHED.slice();
document.hidden=false;HANDLERS.visibilitychange();
console.log(JSON.stringify({whileHidden,returned:FETCHED,n:INTERVALS.length}));""")
    assert out["whileHidden"] == [], "a hidden page read on visibilitychange: " + ", ".join(out["whileHidden"])
    assert out["returned"] == ["/api/loop", "/api/board", "/api/profiler"], out["returned"]
    assert out["n"] == 4, "a return keeps the page's four poll timers"
