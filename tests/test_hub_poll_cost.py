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
POLLER = re.compile(r"setInterval\((.*?),(\d+|LOAD_MS)\)")
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
