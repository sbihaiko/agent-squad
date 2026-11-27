"""The spend is visible (ADR-0277): one line per call, and a health cause for the cooldown and for the miss rate.

A burst of calls is driven through the real throttle with a stub transport and a moving clock, and three things are read
back: the shape of each call-log line (timestamp, transport, route, cache hit or miss, outcome, and the pass that spent
the call), the cause an exhausted cooldown becomes (it names when the cooldown expires and which pass owns it), and the
cause a sustained miss rate becomes. The causes are read from `ghspend.causes`, which is what `health.sh` runs, and the
hub's footer is shown to carry them, so the operator reads them without opening the log file.

The expected timestamps are literals (`date -u -r 1760000000`), never recomputed the way the code computes them.
No network, no model, no board: the stub transport and the temp state dir are the whole environment.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"


def _load(name: str):
    """Load a script module by path, with the scripts folder importable: the modules import each other by name."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader, f"cannot load {SCRIPTS / (name + '.py')}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ghread = _load("ghread")
ghthrottle = _load("ghthrottle")
ghspend = _load("ghspend")

T0 = 1_760_000_000.0  # 2025-10-09T08:53:20Z
RATE_LIMIT = json.dumps({"message": "API rate limit exceeded for user ID 1.", "status": "403"})
LABELS = "repos/o/r/labels"  # a cheap fact: the snapshot may answer it
ISSUE = "repos/o/r/issues/7"  # gates a write: always read live, one GraphQL request with its blockers
OPEN_PRS = "repos/o/r/pulls?state=open&per_page=100"  # an aggregate route: one GraphQL request


class Clock:
    def __init__(self, at: float = T0):
        self.at = at

    def now(self) -> float:
        return self.at


def _ok(body: str = "[]"):
    return lambda route: ghread.Transport(rc=0, body=body)


def _throttle(tmp_path: Path, clock: Clock, **kw):
    kw.setdefault("rand", lambda: 0.0)
    return ghthrottle.Throttle(state_dir=tmp_path, clock=clock.now, sleeper=lambda s: None, **kw)


def _lines(tmp_path: Path) -> list[str]:
    return (tmp_path / "api.log").read_text().splitlines()


@pytest.fixture(autouse=True)
def _no_ambient_pass(monkeypatch):
    for name in [n for n in os.environ if n.startswith("SG_")]:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------- one line per call


def test_each_call_appends_one_line_naming_transport_route_cache_outcome_and_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_GH_PASS", "landing")
    clock = Clock()
    th = _throttle(tmp_path, clock)
    th.read(LABELS, transport=_ok())  # a miss: nothing was kept yet
    clock.at += 5
    th.read(LABELS, transport=_ok())  # a hit: answered from the snapshot, no request
    clock.at += 5
    th.read(ISSUE, transport=_ok("{}"))  # gates a write: live
    clock.at += 5
    th.read(OPEN_PRS, transport=_ok())  # an aggregate route: GraphQL behind the same interface
    assert _lines(tmp_path) == [
        "2025-10-09T08:53:20Z REST repos/o/r/labels miss answer landing - -",
        "2025-10-09T08:53:25Z REST repos/o/r/labels hit answer landing - -",
        "2025-10-09T08:53:30Z GraphQL repos/o/r/issues/7 miss answer landing - -",
        "2025-10-09T08:53:35Z GraphQL repos/o/r/pulls?state=open&per_page=100 miss answer landing - -",
    ], "the last two columns are the points, and a body that names none writes the dash"


def test_a_graphql_answer_names_the_points_it_cost_on_the_line_the_throttle_writes(tmp_path, monkeypatch):
    """#3043: the line the passes read carries the cost, so a pass's share of the hourly budget is a sum over its lines."""
    monkeypatch.setenv("SG_GH_PASS", "branchland")
    th = _throttle(tmp_path, Clock())
    body = json.dumps({"data": {"rateLimit": {"cost": 11, "remaining": 4210}, "repository": {"pullRequests": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}})
    th.read(OPEN_PRS, transport=_ok(body))
    assert _lines(tmp_path)[-1].endswith(" miss answer branchland 11 4210"), _lines(tmp_path)[-1]


def test_a_refusal_a_failure_and_a_held_cooldown_each_name_their_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_GH_PASS", "autobug")
    clock = Clock()
    th = _throttle(tmp_path, clock)
    th.read("repos/o/r/issues/1", transport=lambda r: ghread.Transport(rc=1, body=json.dumps({"message": "Not Found", "status": "404"})))
    th.read("repos/o/r/issues/2", transport=lambda r: ghread.Transport(rc=1, detail="no network"))
    th.read("repos/o/r/issues/3", transport=lambda r: ghread.Transport(rc=1, body=RATE_LIMIT))
    th.read("repos/o/r/issues/4", transport=_ok())  # the cooldown holds: no request is sent
    assert [ln.split()[2:5] for ln in _lines(tmp_path)] == [
        ["repos/o/r/issues/1", "miss", "refused"],
        ["repos/o/r/issues/2", "miss", "failed"],
        ["repos/o/r/issues/3", "miss", "refused"],
        ["repos/o/r/issues/4", "miss", "cooling"],
    ]


def test_a_caller_that_names_no_pass_is_unattributed_never_guessed(tmp_path):
    th = _throttle(tmp_path, Clock())
    th.read(LABELS, transport=_ok())
    assert _lines(tmp_path) == ["2025-10-09T08:53:20Z REST repos/o/r/labels miss answer unattributed - -"]


def test_a_pass_name_that_is_not_one_word_is_unattributed(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_GH_PASS", "land ing\nforged-line")
    _throttle(tmp_path, Clock()).read(LABELS, transport=_ok())
    assert len(_lines(tmp_path)) == 1 and _lines(tmp_path)[0].endswith(" unattributed - -")


def test_an_unwritable_state_dir_costs_the_log_and_not_the_read(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    out = _throttle(blocker, Clock()).read(LABELS, transport=_ok('["x"]'))
    assert out.is_answer and out.answer == ["x"]


def test_the_log_is_rotated_so_it_cannot_grow_without_bound(tmp_path):
    log = tmp_path / "api.log"
    log.write_text("x" * (ghread.LOG_MAX_BYTES + 1))
    ghread.log_call(tmp_path, LABELS, "miss", "answer", pass_name="board", clock=Clock().now)
    assert (tmp_path / "api.log.1").exists()
    assert log.read_text() == "2025-10-09T08:53:20Z REST repos/o/r/labels miss answer board - -\n"


# ---------------------------------------------------------------- the cooldown is a health cause


def _exhaust(tmp_path: Path, monkeypatch, clock: Clock, owner: str = "landing"):
    monkeypatch.setenv("SG_GH_PASS", owner)
    th = _throttle(tmp_path, clock, cooldown=900.0, jitter=60.0)
    th.read(ISSUE, transport=lambda r: ghread.Transport(rc=1, body=RATE_LIMIT))
    return th


def test_an_exhausted_cooldown_is_a_cause_naming_when_it_expires_and_the_pass_that_owns_it(tmp_path, monkeypatch):
    clock = Clock()
    _exhaust(tmp_path, monkeypatch, clock, owner="landing")
    clock.at += 60
    found = ghspend.causes(tmp_path, now=clock.now())
    cooldown = [c for c in found if c.name == "gh-cooldown"]
    assert len(cooldown) == 1
    assert "until 2025-10-09T09:08:20Z" in cooldown[0].detail  # T0 + 900 s, no jitter drawn
    assert "landing" in cooldown[0].detail and "API rate limit exceeded" in cooldown[0].detail
    assert cooldown[0].brief == "until 09:08:20Z, owned by landing"


def test_a_second_pass_under_the_cooldown_does_not_take_its_ownership(tmp_path, monkeypatch):
    clock = Clock()
    _exhaust(tmp_path, monkeypatch, clock, owner="landing")
    monkeypatch.setenv("SG_GH_PASS", "autobug")
    clock.at += 10
    out = _throttle(tmp_path, clock).read(ISSUE, transport=_ok())
    assert out.is_refused
    (cause,) = [c for c in ghspend.causes(tmp_path, now=clock.now()) if c.name == "gh-cooldown"]
    assert "owned by landing" in cause.brief and "autobug" not in cause.detail
    assert _lines(tmp_path)[-1].endswith(" miss cooling autobug - -"), "the held read is attributed to the pass that was held back"


def test_the_cooldown_cause_clears_when_the_cooldown_expires(tmp_path, monkeypatch):
    clock = Clock()
    _exhaust(tmp_path, monkeypatch, clock)
    assert any(c.name == "gh-cooldown" for c in ghspend.causes(tmp_path, now=T0 + 899))
    assert not any(c.name == "gh-cooldown" for c in ghspend.causes(tmp_path, now=T0 + 901))


def test_no_cooldown_and_no_log_is_no_cause(tmp_path):
    assert ghspend.causes(tmp_path, now=T0) == []


# ---------------------------------------------------------------- the miss rate is a health cause


def _burst(tmp_path: Path, clock: Clock, route: str, n: int, *, window: float, step: float = 10.0):
    th = _throttle(tmp_path, clock, cache=ghthrottle.Cache(state_dir=tmp_path, window=window, clock=clock.now))
    for _ in range(n):
        clock.at += step
        th.read(route, transport=_ok())


def test_a_sustained_miss_rate_is_a_cause_in_health_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_GH_PASS", "frontier")
    clock = Clock()
    _burst(tmp_path, clock, LABELS, 30, window=0)  # the snapshot is off: every read of a cheap fact misses
    (cause,) = [c for c in ghspend.causes(tmp_path, now=clock.now()) if c.name == "gh-miss-rate"]
    assert "30 of 30" in cause.detail and "100%" in cause.detail and "frontier" in cause.detail
    assert cause.brief == "100% of 30 reads missed"


def test_a_snapshot_that_answers_the_repeats_is_not_a_miss_rate(tmp_path):
    clock = Clock()
    _burst(tmp_path, clock, LABELS, 30, window=3600)  # one miss, then 29 hits
    assert not any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now()))


def test_a_read_that_gates_a_write_is_always_live_and_never_a_miss_rate(tmp_path):
    clock = Clock()
    _burst(tmp_path, clock, ISSUE, 30, window=3600)  # the liveness rule makes every one of these a miss by design
    assert not any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now()))


def test_too_few_reads_are_not_a_sustained_rate(tmp_path):
    clock = Clock()
    _burst(tmp_path, clock, LABELS, 5, window=0)
    assert not any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now()))


def test_misses_older_than_the_window_do_not_count(tmp_path):
    clock = Clock()
    _burst(tmp_path, clock, LABELS, 30, window=0)
    assert not any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now() + 3 * 3600))


def test_the_thresholds_are_read_from_the_environment(tmp_path, monkeypatch):
    clock = Clock()
    _burst(tmp_path, clock, LABELS, 5, window=0)
    monkeypatch.setenv("SG_HEALTH_GH_MISS_MIN_CALLS", "5")
    assert any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now()))
    monkeypatch.setenv("SG_HEALTH_GH_MISS_RATE", "junk")  # a bad value falls back to the documented default
    assert any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now()))


def test_held_back_reads_are_not_counted_against_the_snapshot(tmp_path, monkeypatch):
    clock = Clock()
    _exhaust(tmp_path, monkeypatch, clock)
    th = _throttle(tmp_path, clock, cache=ghthrottle.Cache(state_dir=tmp_path, window=3600, clock=clock.now))
    for _ in range(30):
        clock.at += 1
        th.read(LABELS, transport=_ok())  # all of these are held by the cooldown: no request, so no verdict on the cache
    assert not any(c.name == "gh-miss-rate" for c in ghspend.causes(tmp_path, now=clock.now()))


def test_an_exhausted_pool_names_the_pass_with_the_largest_graphql_spend_in_the_hour(tmp_path):
    (tmp_path / "gh-ratelimit.json").write_text(
        json.dumps({"graphql": {"remaining": 0, "limit": 5000, "reset": T0 + 1800, "at": T0}}))
    lines = [ghread.Call(T0 - 60 * i, "GraphQL", OPEN_PRS, ghread.MISS, ghread.ANSWER, "landing") for i in range(5)]
    lines += [ghread.Call(T0 - 60, "GraphQL", OPEN_PRS, ghread.MISS, ghread.ANSWER, "autobug")]
    lines += [ghread.Call(T0 - 7200, "GraphQL", OPEN_PRS, ghread.MISS, ghread.ANSWER, "reconcile")]  # outside the hour
    (tmp_path / "api.log").write_text("".join(ghread.call_line(c) + "\n" for c in lines))
    quota = [c for c in ghspend.causes(tmp_path, now=T0) if c.name == "gh-quota"]
    assert quota, "a pool under its floor is a health cause"
    assert "graphql" in quota[0].brief and "landing" in quota[0].brief, quota[0].brief
    assert "reconcile" not in quota[0].detail, "a call older than the hour does not count"


# ---------------------------------------------------------------- the operator surface


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_gh_spend", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


