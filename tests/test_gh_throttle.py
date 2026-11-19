"""One shared throttle: the bucket every process spends from, and the cooldown every process observes (#4, ADR-0277).

The reader owns one outcome contract (`ghread.py`, #1893); this is the rate governor in front of it. The loop's heaviest
reader is the landing pass: `startability.sh`'s eligible-branch collection reads the open-PR list once and then two routes
per candidate (the issue's state and its `Blocked by` body). On 2026-10-12 that pass read against an exhausted REST
quota, collected **zero** eligible branches and said nothing -- "quiet because there is no work" and "quiet because
there is no quota" read identically in the log. So the bucket and the cooldown belong to the pass that spends, and the
burst the tests below drive is that pass's own burst (`landing_pass_burst`), not an invented one.

Everything here is driven through the seam: an injected transport (never the network) and an injected clock (never a
real sleep), so the suite is hermetic and fast. The two-process cases are the exception by design -- a cooldown that
outlives the process that wrote it is only proved by a second process observing it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
MODULE = SCRIPTS / "ghthrottle.py"

# The refusal GitHub actually answered with on 2026-10-12 (the body that used to become the open-PR list): it names the
# rate limit in its own words, it is self-attributed, and it carries its own request id.
RATE_LIMIT_MESSAGE = (
    "API rate limit exceeded for user ID 122891372. If you reach out to GitHub Support for help, please include the "
    "request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A and timestamp 2026-10-12 05:12:10 UTC."
)
REQUEST_ID = "06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A"


def _refusal(message: str, status: int, *, headers: bool = False) -> str:
    """A refusal body as `gh api` hands it over: JSON, with GitHub's own status beside its message."""
    body = json.dumps({"message": message, "status": str(status)})
    return f"HTTP/2.0 {status} \r\nx-github-request-id: {REQUEST_ID}\r\n\r\n{body}" if headers else body


RATE_LIMITED = _refusal(RATE_LIMIT_MESSAGE, 403, headers=True)
NOT_FOUND = _refusal("Not Found", 404, headers=True)
SECONDARY = _refusal("You have exceeded a secondary rate limit. Please wait a few minutes before you try again.", 403)
TOO_MANY = _refusal("Too Many Requests", 429, headers=True)


def _load(name: str, path: Path):
    """Load a script module by path, with the scripts folder importable: `ghthrottle` imports `ghread`."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ghread = _load("ghread", SCRIPTS / "ghread.py")
ghthrottle = _load("ghthrottle", MODULE)


class Clock:
    """The injected clock: the tests move time instead of sleeping through it."""

    def __init__(self, at: float = 1000.0):
        self.at = at
        self.slept: list[float] = []

    def now(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.at += seconds


def stub(body: str = "[]", rc: int = 0):
    """A transport that answers from a literal and records every route with the clock reading it was asked at."""
    seen: list[str] = []

    def transport(route: str):
        seen.append(route)
        return ghread.Transport(rc=rc, body=body, detail="")

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def throttle(tmp_path: Path, clock: Clock, **kw):
    return ghthrottle.Throttle(state_dir=tmp_path, clock=clock.now, sleeper=clock.sleep, **kw)


# --- the burst is bounded, and it is the landing pass's burst ---------------------------------------------------------


def test_the_landing_pass_burst_is_fixed_whatever_the_candidate_count(tmp_path):
    """Three reads: the open PRs, the open issues, and the closed candidates the open list did not carry -- never one per candidate."""
    assert ghthrottle.landing_pass_burst(0) == 3
    assert ghthrottle.landing_pass_burst(48) == 3, "the 2026-10-12 candidate set was 48 issues: it cost 97 routes before the aggregate reads"
    assert ghthrottle.landing_pass_burst(480) == ghthrottle.landing_pass_burst(1)


def test_a_doubled_landing_pass_burst_is_bounded_by_the_budget(tmp_path):
    """Two readers, one bucket: 2 x a full budget of routes, and still no more than one budget on the wire before the first wait."""
    clock = Clock()
    served: list[float] = []

    def transport(route: str):
        served.append(clock.at)
        return ghread.Transport(rc=0, body="[]")

    burst = 5  # a reader that fans out per candidate: the shape the aggregate reads retired, and the case the bucket still bounds.
    # The bound is the bucket's, not the number's, so a small budget proves it with ten file round trips instead of six hundred:
    # the runner's disk made the default budget's 600 reads a 2.9 s call, and the default budget has its own case below.
    a = throttle(tmp_path, clock, budget=burst)
    b = throttle(tmp_path, clock, budget=burst)
    for i in range(2 * burst):
        (a if i % 2 else b).read(f"repos/o/r/issues/{i}", transport=transport)
    assert len(served) == 2 * burst, "a read past the budget waits its turn, it is never dropped"
    assert clock.slept, "two full budgets have to exceed a single budget"
    before_the_first_wait = [at for at in served if at == served[0]]
    assert len(before_the_first_wait) == burst, (
        f"{len(before_the_first_wait)} requests reached GitHub before the bucket ran dry"
    )


@pytest.mark.heavy
def test_a_doubled_reader_cannot_double_the_shared_rate(tmp_path):
    """The rate belongs to the bucket, not to the reader: the same work costs the same wall time for two readers."""
    total = 4 * ghthrottle.DEFAULT_BUDGET
    lone = _elapsed(tmp_path / "lone", readers=1, total=total)
    pair = _elapsed(tmp_path / "pair", readers=2, total=total)
    assert pair == lone, f"a second reader in the same state doubled the rate: {lone}s alone, {pair}s in a pair"
    assert lone >= (total - ghthrottle.DEFAULT_BUDGET) / (ghthrottle.DEFAULT_BUDGET / ghthrottle.DEFAULT_WINDOW)


def _elapsed(where: Path, readers: int, total: int) -> float:
    """Wall time (on the injected clock) for `readers` readers to spend `total` requests against one shared state."""
    clock = Clock()
    throttles = [throttle(where, clock) for _ in range(readers)]
    transport = stub()
    for i in range(total):
        throttles[i % readers].read(f"repos/o/r/issues/{i}", transport=transport)
    assert len(transport.seen) == total
    return clock.at - 1000.0


@pytest.mark.heavy     # 1.45 s here and 2.1 s on a loaded CI runner, over the two-second line for an unmarked call
def test_the_documented_default_serves_a_healthy_pass_with_no_wait(tmp_path):
    """Generous enough that a healthy loop never waits: even the per-candidate shape the aggregate reads retired (201 routes
    for 100 candidates) sleeps not once."""
    clock = Clock()
    t = stub()
    th = throttle(tmp_path, clock)
    for i in range(201):
        th.read(f"repos/o/r/issues/{i}", transport=t)
    assert len(t.seen) == 201
    assert clock.slept == [], f"a pass inside the budget must not wait: it waited {clock.slept}"
    assert clock.at == 1000.0


def test_a_smaller_budget_bounds_the_burst_and_the_rest_waits(tmp_path):
    """The budget is configurable; below the burst, the reads past it wait for the next interval instead of piling on."""
    clock = Clock()
    t = stub()
    th = throttle(tmp_path, clock, budget=4, window=60.0)
    for i in range(10):
        th.read(f"repos/o/r/issues/{i}", transport=t)
    assert len(t.seen) == 10
    assert clock.slept, "the reads past the budget must wait for the bucket to refill"
    assert clock.at >= 1000.0 + 60.0


def test_the_budget_is_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_GH_BUDGET", "7")
    monkeypatch.setenv("SG_GH_WINDOW", "30")
    th = ghthrottle.Throttle(state_dir=tmp_path)
    assert (th.budget, th.window) == (7, 30.0)


# --- the cooldown: written by the first self-attributed 403/429, observed by every process ---------------------------


def test_the_first_self_attributed_refusal_writes_a_cooldown(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    out = th.read("repos/o/r/issues/1", transport=stub(RATE_LIMITED, rc=1))
    assert out.is_refused, "a 403 from GitHub is a refusal"
    assert out.message == RATE_LIMIT_MESSAGE and out.request_id == REQUEST_ID, "GitHub's own words survive the throttle"
    cd = json.loads((tmp_path / "gh-cooldown.json").read_text())
    assert cd["until"] > clock.now(), "the cooldown must outlive the read that wrote it"
    assert cd["message"] == RATE_LIMIT_MESSAGE and cd["request_id"] == REQUEST_ID


def test_a_429_cools_down_even_when_githubs_words_do_not_say_rate_limit(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/issues/1", transport=stub(TOO_MANY, rc=1))
    assert (tmp_path / "gh-cooldown.json").exists(), "429 is the rate-limit status itself"


def test_a_secondary_rate_limit_is_self_attributed(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/issues/1", transport=stub(SECONDARY, rc=1))
    assert (tmp_path / "gh-cooldown.json").exists()


def test_a_refusal_that_is_not_about_our_rate_writes_no_cooldown(tmp_path):
    """A 404 is GitHub answering about the resource, not about us: cooling the whole loop down for it would be wrong."""
    clock = Clock()
    th = throttle(tmp_path, clock)
    out = th.read("repos/o/r/issues/1", transport=stub(NOT_FOUND, rc=1))
    assert out.is_refused
    assert not (tmp_path / "gh-cooldown.json").exists()


def test_an_answer_writes_no_cooldown(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    out = th.read("repos/o/r/issues/1", transport=stub('[{"number": 1}]'))
    assert out.is_answer and not (tmp_path / "gh-cooldown.json").exists()


def test_while_the_cooldown_holds_a_read_is_refused_without_touching_the_network(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/issues/1", transport=stub(RATE_LIMITED, rc=1))
    quiet = stub()
    for i in range(5):
        out = th.read(f"repos/o/r/issues/{i}", transport=quiet)
        assert out.is_refused and out.answer is None, "a cooldown refusal is never an answer"
        assert out.message == RATE_LIMIT_MESSAGE and out.request_id == REQUEST_ID
    assert quiet.seen == [], f"the cooldown must not touch the network: {quiet.seen}"


def test_the_cooldown_expires_and_reads_go_through_again(tmp_path):
    clock = Clock()
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/issues/1", transport=stub(RATE_LIMITED, rc=1))
    cd = json.loads((tmp_path / "gh-cooldown.json").read_text())
    clock.at = cd["until"] + 1
    again = stub('[{"number": 1}]')
    out = th.read("repos/o/r/issues/1", transport=again)
    assert out.is_answer and again.seen == ["repos/o/r/issues/1"]


def test_the_second_self_attributed_refusal_does_not_move_the_cooldown(tmp_path):
    """The FIRST one writes it. A reader that re-reads under the cooldown cannot extend what the first one decided."""
    clock = Clock()
    th = throttle(tmp_path, clock)
    th.read("repos/o/r/issues/1", transport=stub(RATE_LIMITED, rc=1))
    first = json.loads((tmp_path / "gh-cooldown.json").read_text())
    clock.at += 30.0
    ghthrottle.Throttle(state_dir=tmp_path, clock=clock.now, sleeper=clock.sleep).read(
        "repos/o/r/issues/2", transport=stub(RATE_LIMITED, rc=1)
    )
    assert json.loads((tmp_path / "gh-cooldown.json").read_text())["until"] == first["until"]


def test_the_cooldown_carries_jitter_so_two_wake_ups_do_not_collide(tmp_path):
    """Two processes wake on the same second; the cooldown they write must not expire on the same second."""
    clock = Clock()
    first = tmp_path / "first"
    second = tmp_path / "second"
    for where, roll in ((first, 0.05), (second, 0.95)):
        th = ghthrottle.Throttle(state_dir=where, clock=clock.now, sleeper=clock.sleep, rand=lambda r=roll: r)
        th.read("repos/o/r/issues/1", transport=stub(RATE_LIMITED, rc=1))
    a = json.loads((first / "gh-cooldown.json").read_text())
    b = json.loads((second / "gh-cooldown.json").read_text())
    assert a["until"] != b["until"], "without jitter two wake-ups collide on the same second"
    assert a["until"] - clock.now() >= ghthrottle.DEFAULT_COOLDOWN
    assert a["jitter"] <= ghthrottle.DEFAULT_JITTER and a["jitter"] >= 0
    assert b["jitter"] > a["jitter"]


# --- the same throttle as a process, which is how a shell script reaches it ------------------------------------------


def _cli(tmp_path: Path, body: str, rc: int, *args: str):
    """Put a stub `gh` on PATH and run the throttle as a process. No network, no real gh."""
    b = tmp_path / "bin"
    b.mkdir(parents=True, exist_ok=True)
    # The body goes through a file: a refusal carries CRLFs and a header block, and a shell literal would flatten them.
    (tmp_path / "body").write_text(body)
    gh = b / "gh"
    gh.write_text(f'#!/bin/bash\ncat "{tmp_path}/body"\nprintf %s "$*" >> "{tmp_path}/gh-called"\nexit {rc}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", SG_STATE=str(tmp_path / "state"))
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.heavy
def test_the_cli_keeps_ghreads_three_outcomes_and_their_exit_codes(tmp_path):
    # Each case gets its own state: the refusal case writes a cooldown, and a cooldown from one case would answer the next.
    ok = _cli(tmp_path / "a", '[{"number": 7}]', 0, "repos/o/r/pulls?state=open")
    assert ok.returncode == 0 and json.loads(ok.stdout) == [{"number": 7}]
    refused = _cli(tmp_path / "b", RATE_LIMITED, 1, "repos/o/r/pulls?state=open")
    assert refused.returncode == 2, "a refusal keeps ghread's exit code, so a shell caller reads one contract"
    assert RATE_LIMIT_MESSAGE in refused.stdout and REQUEST_ID in refused.stdout
    failed = _cli(tmp_path / "c", "", 127, "repos/o/r/pulls?state=open")
    assert failed.returncode == 1 and "no verdict" in failed.stdout


def test_the_cli_serves_a_second_process_from_the_cooldown_the_first_wrote(tmp_path):
    """The whole point: the process that wrote the cooldown is gone, and the next one does not touch the network."""
    first = _cli(tmp_path, RATE_LIMITED, 1, "repos/o/r/issues/1")
    assert first.returncode == 2
    (tmp_path / "gh-called").unlink()
    second = _cli(tmp_path, '[{"number": 1}]', 0, "repos/o/r/issues/2")
    assert second.returncode == 2, f"a cooldown written by a dead process still refuses: {second.stdout}"
    assert RATE_LIMIT_MESSAGE in second.stdout
    assert not (tmp_path / "gh-called").exists(), "the second process reached for the network while the cooldown held"


# --- the quota: a pool under its floor holds the read before any request, and names its pool (#2355) -------------------

OPEN_PULLS = "repos/o/r/pulls?state=open&per_page=100"  # an aggregate route: one GraphQL request


def _quota(tmp_path: Path, clock: Clock, **left: int) -> None:
    """The quota readings GitHub gave: one pool per keyword, each with its own remaining count and an hour to run."""
    pools = {name: {"remaining": n, "limit": 5000, "reset": clock.now() + 1800, "at": clock.now()} for name, n in left.items()}
    (tmp_path / "gh-ratelimit.json").write_text(json.dumps(pools))


def test_a_graphql_pool_under_its_floor_holds_the_read_before_any_request(tmp_path, monkeypatch):
    monkeypatch.delenv("SG_GH_FLOOR", raising=False)
    clock = Clock()
    _quota(tmp_path, clock, graphql=0, core=4909)
    transport = stub("[]")
    out = throttle(tmp_path, clock).read(OPEN_PULLS, transport=transport)
    assert transport.seen == [], "a pool under its floor sends no request"
    assert not out.is_answer and "graphql" in out.line(), "the line names the pool that holds the read"


def test_a_core_pool_under_its_floor_holds_a_rest_read_before_any_request(tmp_path, monkeypatch):
    monkeypatch.delenv("SG_GH_FLOOR", raising=False)
    clock = Clock()
    _quota(tmp_path, clock, graphql=4000, core=12)
    transport = stub("[]")
    out = throttle(tmp_path, clock).read("repos/o/r/labels", transport=transport)
    assert transport.seen == [], "a pool under its floor sends no request"
    assert not out.is_answer and "core" in out.line(), "the line names the pool that holds the read"


def test_a_pool_above_its_floor_does_not_hold_the_read(tmp_path, monkeypatch):
    monkeypatch.delenv("SG_GH_FLOOR", raising=False)
    clock = Clock()
    _quota(tmp_path, clock, graphql=4000, core=4909)
    transport = stub("[]")
    out = throttle(tmp_path, clock).read(OPEN_PULLS, transport=transport)
    assert transport.seen == [OPEN_PULLS] and out.is_answer


def test_a_refusal_on_a_graphql_route_names_the_graphql_pool_in_the_cooldown(tmp_path):
    clock = Clock()
    throttle(tmp_path, clock).read(OPEN_PULLS, transport=stub(RATE_LIMITED, rc=1))
    cd = json.loads((tmp_path / "gh-cooldown.json").read_text())
    assert cd["pool"] == "graphql", "a GraphQL refusal names its pool, not only the pass"


def test_a_refusal_on_a_rest_route_names_the_core_pool_in_the_cooldown(tmp_path):
    clock = Clock()
    throttle(tmp_path, clock).read("repos/o/r/labels", transport=stub(RATE_LIMITED, rc=1))
    cd = json.loads((tmp_path / "gh-cooldown.json").read_text())
    assert cd["pool"] == "core"
