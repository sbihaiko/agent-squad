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
import re
import tempfile
import threading
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


@pytest.fixture
def served(hub, monkeypatch):
    """The hub's own handler over HTTP, with each costly build counted and one clock under the test's hand."""
    stats_calls, prof_calls, clock = [], [], [1000.0]

    def counted_stats(period, src="both"):
        stats_calls.append((period, src))
        return {"buckets": [], "pace": {}, "totals": {}, "models": [], "operator": {}}

    def counted_prof(*a, **k):
        prof_calls.append(1)
        return {"tree": []}

    monkeypatch.setattr(hub, "stats", counted_stats)
    monkeypatch.setattr(hub, "profiler_view", counted_prof)
    monkeypatch.setattr(hub, "POLL_CLOCK", lambda: clock[0], raising=False)
    monkeypatch.setattr(hub, "_STATS_KEPT", {}, raising=False)
    monkeypatch.setattr(hub, "_PROF_KEPT", {}, raising=False)
    monkeypatch.setattr(hub, "PROFILER_SWITCH", pathlib.Path(tempfile.mkdtemp()) / "profiler.json", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_port, stats_calls, prof_calls, clock
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


def test_two_stats_reads_with_unchanged_inputs_build_once(served, hub):
    """AC B of #2582: the Spend answer is kept while its inputs are, and no window expires it."""
    port, stats_calls, _, clock = served
    first = get(port, "/api/stats?period=day&src=both")
    second = get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 1, f"two reads with unchanged inputs built the spend answer {len(stats_calls)} times"
    assert first == second, "the second read serves the same answer"
    clock[0] += 1000.0
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 1, f"a later read with unchanged inputs built the answer {len(stats_calls)} times"


def test_a_changed_stats_input_builds_the_answer_again(served, hub):
    """AC C of #2582: a review call written to the ledger makes the next read build, never the answer before it."""
    port, stats_calls, _, _ = served
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 1
    (hub.STATE / "token-usage.tsv").write_text("2026-01-01T00:00:00\trun\tdeepseek-flash\t10\t5\t0\n")
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 2, f"a changed ledger built the answer {len(stats_calls)} times"
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 2, "the changed ledger is one change, so the read after it serves the build it made"


def test_a_changed_operator_transcript_builds_the_answer_again(served, hub, monkeypatch):
    """AC C of #2582: the operator fold is a named input beside the review ledger; a transcript that moved builds again."""
    port, stats_calls, _, _ = served
    monkeypatch.setattr(hub, "OPERATOR", None)
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 1
    (hub.STATE / "operator-usage.json").write_text('{"files": {}}')
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 2, f"a changed operator transcript built the answer {len(stats_calls)} times"
    get(port, "/api/stats?period=day&src=both")
    assert len(stats_calls) == 2, "one transcript change is one rebuild"


def test_the_spend_answer_is_kept_per_period_and_source(served):
    """The page asks for a period and a billing source: one pair's answer never serves another pair."""
    port, stats_calls, _, _ = served
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


def test_the_profiler_file_view_is_built_once_per_off_window(served):
    """With the sampling switch off the page reads the file views every 30 s: one build per window, shared by pages."""
    port, _, prof_calls, clock = served
    get(port, "/api/profiler")
    get(port, "/api/profiler")
    assert len(prof_calls) == 1, f"two reads inside one window built the profiler view {len(prof_calls)} times"
    clock[0] += 1000.0
    get(port, "/api/profiler")
    assert len(prof_calls) == 2, "a read past the window builds the view again"


def test_two_profiler_reads_inside_one_sampler_interval_build_once(served, hub, monkeypatch):
    """AC 1 of #2848: a build that costs more than the interval it is kept for still serves the read that asks after it.

    With the switch on, the Loop page asks the view every poll and the build reads the process table, the trip, the runs
    and the load. The kept stamp is the instant the answer existed, never the instant the build began, so the read that
    waited for the build serves it instead of building it again."""
    port, _, prof_calls, clock = served
    hub.PROFILER_SWITCH.write_text('{"on": true}')
    cost = hub.PROF_ON_SECS * 2.5   # a build the page's own interval cannot cover by the stamp at its start

    def slow_prof(*a, **k):
        prof_calls.append(1)
        clock[0] += cost
        return {"tree": []}

    monkeypatch.setattr(hub, "profiler_view", slow_prof)
    get(port, "/api/profiler")
    get(port, "/api/profiler")
    assert len(prof_calls) == 1, (
        f"two reads inside one sampler interval built the profiler view {len(prof_calls)} times, with a build of {cost} s")


def test_two_profiler_serves_on_the_real_clock_build_once_and_again_past_the_window(hub, monkeypatch):
    """AC 1 of #2848 through `profiler_served`'s own default clock, with no `now` passed and the module clock injected.

    The stamp is read from the same clock as the window comparison, after the build, so a build that outlasts the window
    still serves the read that asks right after it, and the read past the window builds again. A stamp and a comparison
    on two clocks cannot pass both halves: a mismatched stamp rebuilds at the first read, and a mismatch on the other side
    never rebuilds past the window."""
    clock = [1000.0]
    built = []
    switch = pathlib.Path(tempfile.mkdtemp()) / "profiler.json"
    switch.write_text('{"on": true}')
    monkeypatch.setattr(hub, "POLL_CLOCK", lambda: clock[0])
    monkeypatch.setattr(hub, "PROFILER_SWITCH", switch)
    monkeypatch.setattr(hub, "_PROF_KEPT", {})
    window = hub.PROF_ON_SECS

    def slow_view(*a, **k):
        built.append(1)
        clock[0] += window * 2.5   # a build longer than the window it is kept for
        return {"tree": []}

    monkeypatch.setattr(hub, "profiler_view", slow_view)

    class _Caller:
        allowed, tailnet, reason = True, False, ""

    hub.profiler_served(_Caller(), 3600, None, None)   # no `now`: the real default path
    hub.profiler_served(_Caller(), 3600, None, None)
    assert len(built) == 1, f"two reads inside one window built the view {len(built)} times"
    clock[0] += window + 1.0   # a read past the window, counted from the stamp
    hub.profiler_served(_Caller(), 3600, None, None)
    assert len(built) == 2, "a read past the window served the old view"


def test_the_profiler_cadence_is_the_sampler_interval_above_the_page_floor(hub):
    """AC 2 of #2848: the page cadence is the sampler interval, never a literal and never under the page floor.

    A slower sampler sets the cadence exactly; a faster one is held at the floor, which is a page cadence and never a
    sampler tick, so the page can never poll the profiler faster than the sampler reads."""
    assert hub.prof_poll_secs(hub.PAGE_PROF_FLOOR_S) == hub.PAGE_PROF_FLOOR_S
    assert hub.prof_poll_secs(hub.PAGE_PROF_FLOOR_S + 3.0) == hub.PAGE_PROF_FLOOR_S + 3.0
    assert hub.prof_poll_secs(0.5) == hub.PAGE_PROF_FLOOR_S
    assert hub.PROF_POLL_SECS == hub.prof_poll_secs(hub.PROFILER_SECS)
    assert hub.PROF_POLL_SECS >= hub.PROFILER_SECS


def test_the_loop_page_polls_the_profiler_no_faster_than_the_sampler_reads(hub):
    """AC 2 of #2848: the Loop page takes its profiler cadence from the sampler's own interval, never from a literal."""
    assert hub.PROF_POLL_SECS >= hub.PROFILER_SECS, (
        f"the page polls every {hub.PROF_POLL_SECS} s, faster than the sampler's {hub.PROFILER_SECS} s")
    page = hub.served_page("")
    m = re.search(r'const PROF_MS=Number\("(\d+)"\)', page)
    assert m, "the page's profiler poll does not name its interval"
    assert int(m[1]) >= hub.PROFILER_SECS * 1000, (
        f"the page polls the profiler every {m[1]} ms, faster than the sampler's {hub.PROFILER_SECS} s")
    assert re.search(r"setInterval\(\(\)=>\{if\(profDue\(TAB,document\.hidden,profV\)\)profPoll\(\)\},PROF_MS\)", page), (
        "the profiler poll does not read the page's own interval")


def test_the_switch_write_clears_the_kept_profiler_view(hub, served):
    """A switch write is the page's own change: the next read shows it, never a view kept from before it."""
    assert hasattr(hub, "prof_kept_clear"), "the switch write clears the kept view through `prof_kept_clear`"
    port, _, prof_calls, _ = served
    get(port, "/api/profiler")
    hub.prof_kept_clear()
    get(port, "/api/profiler")
    assert len(prof_calls) == 2, "the view kept before the write served the read after it"


def test_the_watch_tick_reads_a_change_within_two_seconds(hub):
    """The watcher's tick reads a changed record within 2 s, beats the fallback poll, and does not read five times a second."""
    assert 0.5 <= hub.RUN_WATCH_SECS <= 2.0, (
        f"RUN_WATCH_SECS is {hub.RUN_WATCH_SECS}: the watcher must read a change within 2 s and no faster than two ticks a second")
    assert hub.RUN_WATCH_SECS <= hub.RUN_POLL_SECS / 2, (
        f"RUN_WATCH_SECS is {hub.RUN_WATCH_SECS}: the watcher must be under half the fallback poll {hub.RUN_POLL_SECS}, "
        "else the fallback does its work")


POLLS = ("poll", "pollStats", "loadPoll", "loopPoll", "accPoll", "pollBoard", "squadChartTick", "endedPoll", "profPoll")
POLLER = re.compile(r"setInterval\((.*?),(\d+|LOAD_MS|PROF_MS)\)")
HELPER = "pollIfVisible"


def test_every_desktop_poller_reads_through_the_one_visible_gate(hub):
    """A tab in the background asks nothing, as the phone page and the profiler already do: a hidden page costs no CPU.

    Every hub read goes through one gate, so a poller added later cannot regress the rule by naming no hidden check. A
    timer that only rewrites text already on the page opens no request, and it is not asserted."""
    page = hub.served_page("")
    assert f"const {HELPER}=" in page, "the page defines no visible gate for its polls"
    # The Loop timers decide through `profDue`/`profFileDue`, which read document.hidden themselves: either gate passes.
    loose = [body.strip()[:70] for body, _ in POLLER.findall(page)
             if any(re.search(rf"\b{p}\b", body) for p in POLLS)
             and f"{HELPER}(" not in body and "document.hidden" not in body]
    assert not loose, "these pollers read the hub without the visible gate: " + " | ".join(loose)


def test_a_page_back_in_front_reads_the_fast_polls_without_a_burst(hub):
    """A hidden page reads nothing, so its return reads the two fastest polls at once. The slow ones wait their timer: nine
    reads at once against a hub that just idled is the burst the hidden page removed."""
    page = hub.served_page("")
    handler = page[page.index("visibilitychange"):]
    handler = handler[:handler.index("});") + 3]
    missing = [p for p in ("poll", "pollBoard", "profPoll") if p not in handler]
    assert not missing, "a returning page reads nothing of " + ", ".join(missing)
    burst = [p for p in ("pollStats", "endedPoll", "loadPoll", "accPoll", "loopPoll") if p + "()" in handler]
    assert not burst, "a returning page bursts the hub with " + ", ".join(burst)
