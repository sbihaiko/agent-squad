#!/usr/bin/env python3
"""One shared throttle, and one shared snapshot, in front of the one GitHub reader (#4, #6, ADR-0277).

`ghread.py` owns the outcome contract -- an answer, a refusal, or a failure before GitHub reached a verdict. This module
owns the RATE, and it owns it for every process at once:

* a **token bucket** with a configurable budget, refilled at `budget / window` per second and spent once per request.
  The state lives in `$SG_STATE/gh-budget.json`, so a second reader beside this one -- a duplicated `watch.sh`, a hand-run
  pass -- spends from the same bucket. The budget is generous enough that a healthy loop never waits and small enough
  that a doubled reader cannot double the rate.
* a **cooldown** written by the first refusal GitHub attributes to our own request rate (a 403 that names the rate limit,
  or any 429). It lives in `$SG_STATE/gh-cooldown.json`, so it outlives the process that wrote it: while it holds, a
  remote read returns the refusal outcome and **no request is sent at all**. It carries jitter, so two readers that wake
  on the same second do not both start reading on the same second.

Documented defaults (every one of them overridable per loop, never edited at run time):

    SG_GH_BUDGET             300   requests per window, shared by every process
    SG_GH_WINDOW              60   seconds in a window
    SG_GH_COOLDOWN           900   seconds the first self-attributed 403/429 holds every reader back
    SG_GH_COOLDOWN_JITTER     60   seconds of jitter added to that cooldown, drawn per writer
    SG_GH_CACHE_WINDOW        60   seconds a read is answered from the snapshot; 0 turns the snapshot off
    SG_GH_AGGREGATE_WINDOW    90   seconds a caller that chose it reads the open-PR and open-issue lists from the snapshot
    SG_GH_FLOOR              300   requests a GraphQL or core pool may fall to before every live read is held, unsent (#2355)
    SG_GH_STRETCH_FLOOR     1500   points a pool may fall to before every cycle waits longer, whatever its share (#3043)

Why 300/60 s: the landing pass is the loop's heaviest reader and it reads in bursts, not in a stream. Its eligible-branch
collection reads the open-PR list once and then two routes per candidate (the issue's state and its `Blocked by` body) --
`landing_pass_burst(n) = 1 + 2n`. A 149-candidate pass fits inside one full bucket with no wait at all, which is more
than this loop has ever had waiting (the landing pass collected against a candidate board), and it is a
third of the ~900 REST points per minute GitHub's own secondary limits allow. A doubled reader shares the same 300: it
can spend the bucket twice as fast and then it waits, which is the whole point -- two readers, one rate.

The transport stays invisible to callers: a caller hands over a route and reads back a `ghread.Read`, and the fact that
REST is the transport behind it is not part of what it is handed. A sibling slice adds GraphQL behind the same interface.

The snapshot (#6). A read repeated inside its window is answered from `$SG_STATE/gh-cache` and spends no request and no
token, and the kind of fact decides what keeps an entry:

* a **cheap** fact (`TTL`: the repository, its labels, its milestones) lives for `SG_GH_CACHE_WINDOW` seconds;
* an **expensive** fact (`KEYED`: a PR's files, a PR's commits) carries a key. A caller that holds the PR's head SHA passes
  it (`read(route, key=sha)`): an unchanged head is the same fact however old the entry is, a changed head throws the entry
  away and the next read is live. Without a caller's key the ETag GitHub answered with is the key: once the window has
  passed the entry is revalidated with one conditional request, a `304` refreshes the entry without reading or parsing the
  fact again, and any other answer means the next read is live;
* the ETag is kept for a cheap fact too, so past its window the entry is checked with one conditional request and a `304`
  refreshes it: GitHub does not count a `304` against the primary limit (#45);
* a corrupt, truncated, foreign or absent entry is a miss, never an error, and an unwritable state dir costs the snapshot
  and not the read. Only an answer is kept: a refusal or a failure never becomes a snapshot.

The liveness rule: **every fact that gates a write is read live**. A merge, a close and a card move each rest on a read --
is the branch still eligible, is the base red, is the work branch still open, is the issue still open and what blocks it -- and a
stale answer there is a merge onto a base another pass already moved, a class of red the loop cannot attribute. So the
snapshot is an allowlist, not a denylist: `fact_class(route)` names the few routes that may be snapshotted, and every other
route -- the open-PR and open-issue lists, a single issue, a single PR and its merge, a commit's status and checks, a
branch, a ref, a compare, a workflow run, a comment, a board item, and any route nobody classified -- is `LIVE` and is
neither looked up in the cache nor stored in it. Writes never reach the cache at all.

Two doors sit beside the plain read, and both are a caller's explicit choice (#45). `--aggregate <route>` answers the
open-PR or open-issue list from the snapshot for `SG_GH_AGGREGATE_WINDOW` seconds (60 to 120): the two passes that
announce a PR and that land a branch read it once for the whole set, and the answer is at most one window old -- never for
a read that is about to write. `--stretch <base>` is the adaptive cycle: every live read keeps the `x-ratelimit-*` headers
GitHub answered with in `$SG_STATE/gh-ratelimit.json`, and once the lowest pool has under 20 percent of its limit left
the loop waits longer between cycles (twice the base under 20 percent, four times under 10, eight times under 5).

This module is reached from a shell script the same way `ghread.py` is -- `python3 ghthrottle.py <route>` -- with the same
exit codes (0 the answer on stdout, 2 a refusal, 1 a failure before a verdict), so a caller reads one contract whether the
read was throttled or not. It does not own the health causes (`ghspend.py`) or the direct-read check.

The spend is visible (ADR-0277). Every call this module answers appends one line to `$SG_STATE/api.log` (the format is in
`ghread.py`): a read the snapshot answered is a `hit` and spends no request; a read that went to GitHub is a `miss`; a read the
cooldown held back is a `miss` with the outcome `cooling`, because no request was sent. The cooldown file names the pass that
wrote it (`SG_GH_PASS`), so health can say who owns an exhausted cooldown, and `ghspend.py` reads both files to say so.

Nothing here reaches the network on its own account. A caller that only ever replaces its knowledge from an answer keeps what
it had, and the refusal line is what tells the operator why the loop is quiet (the landing pass prints it).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import random
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import ghread

BUDGET_ENV = "SG_GH_BUDGET"
WINDOW_ENV = "SG_GH_WINDOW"
COOLDOWN_ENV = "SG_GH_COOLDOWN"
JITTER_ENV = "SG_GH_COOLDOWN_JITTER"
STATE_ENV = "SG_STATE"
CACHE_WINDOW_ENV = "SG_GH_CACHE_WINDOW"
AGGREGATE_WINDOW_ENV = "SG_GH_AGGREGATE_WINDOW"
QUOTA_FLOOR_ENV = "SG_GH_FLOOR"
STRETCH_FLOOR_ENV = "SG_GH_STRETCH_FLOOR"
QUOTA_POOLS = ("graphql", "core")  # every pool a read may spend; each one has its own limit and its own hour

DEFAULT_QUOTA_FLOOR = 300  # requests a pool may fall to before every read is held, and no request is sent
# The stretch floor is in POINTS, not in a share (#3043). Five times the stop floor: a pool of a 5000-point hour at 1500
# left is 30 percent, so a pass whose calls average the 11 points the 2026-11-27 audit measured has about 130 calls left --
# the cycle slows while there is still room to do the work of the pass, not only while there is none.
DEFAULT_STRETCH_FLOOR = 1500
DEFAULT_BUDGET = 300  # requests per window, shared by every process
DEFAULT_WINDOW = 60.0  # seconds in a window
DEFAULT_COOLDOWN = 900.0  # the first self-attributed 403/429 holds every reader back for this long
DEFAULT_JITTER = 60.0  # seconds of jitter added to it, drawn per writer, so two wake-ups do not collide
DEFAULT_AGGREGATE_WINDOW = 90.0  # seconds the open lists are answered from the snapshot, for a caller that chose it
DEFAULT_CACHE_WINDOW = 60.0  # seconds a snapshot answers a repeated read; one bucket window, so a beat-by-beat loop repeats at most once

# The landing pass's own burst (the pull-request collection): the open-PR list once, then the state and
# the `Blocked by` body of each candidate. Measured against the pass, and asserted against it in tests/test_gh_throttle.
LANDING_PASS_FIXED_READS = 3  # the open PRs, the open issues, and the issues of the candidates the open list lacks
LANDING_PASS_READS_PER_CANDIDATE = 0  # since #45 a candidate adds no read: its facts come from those three

BUCKET_FILE = "gh-budget.json"
COOLDOWN_FILE = "gh-cooldown.json"
CACHE_DIR = "gh-cache"
RATELIMIT_FILE = "gh-ratelimit.json"
AGGREGATE_PREFIX = "aggregate:"  # the snapshot key of an aggregate read: no cheap or keyed route can collide with it

# The adaptive cycle: (share of the limit left below which the step applies, how many times longer the cycle is). The lowest
# step that holds wins, and a pool at exactly 20 percent is not yet low.
LOW_QUOTA_STEPS = ((0.05, 8.0), (0.10, 4.0), (0.20, 2.0))

# What a route's fact may be answered from. LIVE is the default, and the only way out of it is to be named below.
LIVE = "live"  # gates a write, or nobody classified it: read from GitHub every time, never looked up, never stored
TTL = "ttl"  # cheap to re-read: kept for the window
KEYED = "keyed"  # expensive to re-derive: kept while its head SHA or its ETag is the one GitHub still has
_REPO = r"^repos/[^/?]+/[^/?]+"
_CHEAP = re.compile(_REPO + r"(?:/(?:labels|milestones|collaborators|releases|topics|languages))?(?:\?.*)?$")
_EXPENSIVE = re.compile(_REPO + r"/pulls/\d+/(?:files|commits)(?:\?.*)?$")

# GitHub's own words for a refusal it attributes to how fast we asked: the rate limit itself, a secondary limit, or abuse
# detection. A 403 about permissions ("Resource not accessible by integration") is not one of these, and cooling the whole
# loop down for it would silence a pass over a fault no wait can fix.
_SELF_ATTRIBUTED = re.compile(r"rate limit|abuse|too many requests|secondary", re.IGNORECASE)
_HTTP_STATUS = re.compile(r"^HTTP/[\d.]+\s+(\d{3})", re.MULTILINE)
_USAGE = 64

# A bucket refilled to within a rounding error of a token IS a token, and a wait is never shorter than a millisecond. Both
# exist for the same reason: the refill is a subtraction against a clock, so the deficit can come out at 1e-16 and a wait
# that short is absorbed by the clock's own precision -- the loop would then spin without ever making progress.
_REFILL_EPSILON = 1e-9
_MIN_WAIT = 0.001


def landing_pass_burst(candidates: int) -> int:
    """The routes one eligible-branch collection reads for `candidates` candidates: `1 + 2n` (see the module docstring)."""
    return LANDING_PASS_FIXED_READS + LANDING_PASS_READS_PER_CANDIDATE * candidates


def _number(name: str, default: float) -> float:
    """A configured number, or the documented default: a bad value in the loop's environment never stops a read."""
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def _aggregate_window() -> float:
    try:
        value = float(os.environ.get(AGGREGATE_WINDOW_ENV, DEFAULT_AGGREGATE_WINDOW))
    except ValueError:
        return DEFAULT_AGGREGATE_WINDOW
    return value if value >= 0 else DEFAULT_AGGREGATE_WINDOW


def _cache_window() -> float:
    """The snapshot window: the documented default, or what the environment names. Zero is a value (the snapshot is off);
    a bad or negative one is not, and never stops a read."""
    try:
        value = float(os.environ.get(CACHE_WINDOW_ENV, DEFAULT_CACHE_WINDOW))
    except ValueError:
        return DEFAULT_CACHE_WINDOW
    return value if value >= 0 else DEFAULT_CACHE_WINDOW


def default_state_dir() -> Path:
    """Where the bucket and the cooldown live: the loop's own state dir, so every process of the loop shares them."""
    return Path(os.environ.get(STATE_ENV) or Path.home() / ".cache" / "squad-goal")


def pool_below(pool: str, floor: float, state_dir: Path | None = None, now: float | None = None) -> int | None:
    """The requests left in `pool` when that is under `floor` and the hour that reading describes is still running;
    `None` otherwise. A missing reading or a rolled-over hour is no floor (#847): the pass runs, as it did before the
    reading. GraphQL is a pool of its own (#1648): a pass that floors on core alone reads into a refusal with thousands
    of core requests left, and that refusal renews the limit."""
    entry = _read_json((state_dir or default_state_dir()) / RATELIMIT_FILE).get(pool)
    if not isinstance(entry, dict): return None
    try:
        remaining, reset = int(entry["remaining"]), float(entry.get("reset") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if reset and reset <= (time.time() if now is None else now): return None
    return remaining if remaining < floor else None


def quota_hold(state_dir: Path | None = None, now: float | None = None) -> tuple[str, int] | None:
    """The first pool under `SG_GH_FLOOR` and its requests left, or None: the pool a read is held for, before any request (#2355)."""
    floor = _number(QUOTA_FLOOR_ENV, DEFAULT_QUOTA_FLOOR)
    for pool in QUOTA_POOLS:
        left = pool_below(pool, floor, state_dir, now)
        if left is not None:
            return pool, left
    return None


def core_below(floor: float, state_dir: Path | None = None) -> int | None:
    """The core requests left when that is under `floor`, on its own pool (#847)."""
    return pool_below("core", floor, state_dir)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Replace the file atomically: a reader in another process sees the old state or the new one, never half of either."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(value, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return  # an unwritable state dir costs the sharing, never the read: the bucket falls back to this process


def _http_status(body: str) -> int | None:
    """The status in front of the body when the transport asked for GitHub's headers (`gh api --include`), else None.

    The throttle reads it here rather than taking it from `ghread.Read`, whose job is to keep the three outcomes apart and
    which hands a refusal over without the HTTP status beside it. A transport that hands over the body alone (a stub, a
    test) leaves this None, and the refusal's own words decide.
    """
    found = _HTTP_STATUS.search(body)
    return int(found.group(1)) if found else None


def _self_attributed(out: ghread.Read, status: int | None) -> bool:
    """Is this refusal GitHub telling us we asked too fast? 429 is the rate-limit status itself; a 403 has to say so."""
    if not out.is_refused:
        return False
    return status == 429 or bool(_SELF_ATTRIBUTED.search(out.message))


def fact_class(route: str) -> str:
    """`TTL` or `KEYED` for the few facts that may be snapshotted, `LIVE` for every other route (the liveness rule)."""
    if _EXPENSIVE.match(route):
        return KEYED
    return TTL if _CHEAP.match(route) else LIVE


class Cache:
    """The snapshot of the reads that may be repeated. One file per route in `$SG_STATE/gh-cache`, shared by every process."""

    def __init__(
        self, state_dir: Path | str | None = None, window: float | None = None, clock: Callable[[], float] = time.time
    ):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.window = float(window) if window is not None else _cache_window()
        self.clock = clock

    def entry_path(self, route: str) -> Path:
        return self.state_dir / CACHE_DIR / f"{hashlib.sha256(route.encode()).hexdigest()[:40]}.json"

    def _load(self, route: str) -> dict[str, Any] | None:
        """The entry for this route, or None for anything that is not one: absent, unreadable, truncated, not an object,
        another route's, stamped in the future, or carrying a body that is not JSON. A miss, never an error."""
        entry = _read_json(self.entry_path(route))
        at, body = entry.get("at"), entry.get("body")
        if entry.get("route") != route or not isinstance(body, str):
            return None
        if isinstance(at, bool) or not isinstance(at, (int, float)) or at > self.clock():
            return None
        if body.strip():
            try:
                json.loads(body)
            except ValueError:
                return None
        return {"route": route, "at": float(at), "key": str(entry.get("key") or ""), "etag": str(entry.get("etag") or ""), "body": body}

    def store(self, route: str, out: ghread.Read, key: str = "", etag: str = "") -> None:
        """Keep an answer. A refusal, a failure or a partial answer is not a snapshot of anything and is not kept."""
        if not out.is_answer or out.partial:
            return
        _write_json(
            self.entry_path(route),
            {"route": route, "at": self.clock(), "key": key, "etag": etag, "body": out.body},
        )

    def _refresh(self, entry: dict[str, Any]) -> ghread.Read:
        """An unchanged key keeps the entry for another window and hands it back as it was stored, not re-derived."""
        _write_json(self.entry_path(entry["route"]), {**entry, "at": self.clock()})
        body = entry["body"]
        return ghread.Read(kind=ghread.ANSWER, answer=json.loads(body) if body.strip() else None, body=body)

    def read(
        self,
        route: str,
        live: Callable[[str, ghread.TransportFn | None], ghread.Read],
        transport: ghread.TransportFn | None = None,
        key: str | None = None,
        revalidate: ghread.RevalidateFn | None = None,
    ) -> ghread.Read:
        """Answer from the snapshot when the fact may be and the entry still holds; otherwise `live(route, transport)`.

        `key` is the head SHA the caller holds for a keyed fact. `revalidate(route, etag)` is the conditional request.
        """
        kind = fact_class(route)
        if kind == LIVE or self.window <= 0:
            return live(route, transport)
        entry = self._load(route)
        if entry is not None:
            if key is not None:
                if entry["key"] == key:
                    ghread.log_call(self.state_dir, route, ghread.HIT, ghread.ANSWER, clock=self.clock)
                    return self._refresh(entry)
            elif self.clock() - entry["at"] < self.window:
                ghread.log_call(self.state_dir, route, ghread.HIT, ghread.ANSWER, clock=self.clock)
                return ghread.Read(
                    kind=ghread.ANSWER, answer=json.loads(entry["body"]) if entry["body"].strip() else None, body=entry["body"]
                )
            elif entry["etag"] and revalidate is not None and self._unchanged(route, entry["etag"], revalidate):
                return self._refresh(entry)
        seen = {"etag": ""}
        inner = transport or ghread.gh_transport

        def tap(r: str) -> ghread.Transport:
            res = inner(r)
            seen["etag"] = ghread.headers_of(res.body).get("etag", "")
            return res

        out = live(route, tap)
        self.store(route, out, key=key or "", etag=seen["etag"])
        return out

    @staticmethod
    def _unchanged(route: str, etag: str, revalidate: ghread.RevalidateFn) -> bool:
        """Did GitHub answer 304 to the entry's ETag? Anything else -- a new entity, a refusal, no answer -- is no."""
        try:
            res = revalidate(route, etag)
        except OSError:
            return False
        return res is not None and _http_status(res.body) == 304


def _pool_of(route: str, body: str) -> str:
    """The pool a refusal drew on: the resource GitHub names in its headers, else the pool the route spends."""
    named = ghread.headers_of(body).get("x-ratelimit-resource")
    if named:
        return named
    return "graphql" if ghread.aggregate(route) is not None else "core"


class Throttle:
    """The shared bucket and the shared cooldown. One instance per process; both files are what makes it shared."""

    def __init__(
        self,
        state_dir: Path | str | None = None,
        budget: float | None = None,
        window: float | None = None,
        cooldown: float | None = None,
        jitter: float | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
        cache: Cache | None = None,
    ):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.budget = float(budget) if budget is not None else _number(BUDGET_ENV, DEFAULT_BUDGET)
        self.window = float(window) if window is not None else _number(WINDOW_ENV, DEFAULT_WINDOW)
        self.cooldown = float(cooldown) if cooldown is not None else _number(COOLDOWN_ENV, DEFAULT_COOLDOWN)
        self.jitter = float(jitter) if jitter is not None else _number(JITTER_ENV, DEFAULT_JITTER)
        self.clock = clock
        self.sleeper = sleeper
        self.rand = rand
        self.cache = cache if cache is not None else Cache(state_dir=self.state_dir, clock=clock)

    @property
    def rate(self) -> float:
        """Tokens per second: a full window's budget spread over the window."""
        return self.budget / self.window

    @property
    def bucket_path(self) -> Path:
        return self.state_dir / BUCKET_FILE

    @property
    def cooldown_path(self) -> Path:
        return self.state_dir / COOLDOWN_FILE

    @property
    def quota_path(self) -> Path:
        return self.state_dir / RATELIMIT_FILE

    @contextlib.contextmanager
    def _sole_writer(self) -> Iterator[None]:
        """The bucket is a read-modify-write across processes; the lock is what keeps two readers from both seeing it full."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            lock = (self.state_dir / f"{BUCKET_FILE}.lock").open("a+")
        except OSError:
            yield  # no lock to take: an unwritable state dir leaves each process its own bucket rather than no read
            return
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def holding(self) -> dict[str, Any]:
        """The cooldown that still holds, or an empty dict. This is the state a second process reads off disk."""
        state = _read_json(self.cooldown_path)
        return state if float(state.get("until", 0)) > self.clock() else {}

    def spend(self) -> None:
        """Take one token, waiting for the bucket to refill when a burst has emptied it."""
        while True:
            with self._sole_writer():
                state = _read_json(self.bucket_path)
                now = self.clock()
                tokens = float(state.get("tokens", self.budget)) + (now - float(state.get("at", now))) * self.rate
                tokens = min(tokens, self.budget)  # a bucket fills to one window's budget and no further
                if tokens >= 1.0 - _REFILL_EPSILON:
                    _write_json(self.bucket_path, {"tokens": tokens - 1.0, "at": now})
                    return
                wait = max((1.0 - tokens) / self.rate, _MIN_WAIT)
            self.sleeper(wait)

    def write_cooldown(self, out: ghread.Read, pool: str) -> None:
        """The FIRST self-attributed refusal writes it; a later reader under it cannot move it or stretch it."""
        if self.holding():
            return
        jitter = self.rand() * self.jitter
        now = self.clock()
        _write_json(
            self.cooldown_path,
            {
                "until": now + self.cooldown + jitter,
                "at": now,
                "base": self.cooldown,
                "jitter": jitter,
                "message": out.message,
                "request_id": out.request_id,
                "pid": os.getpid(),
                "pass": ghread.current_pass(),  # who owns it: the answer to "the loop is quiet, or has no quota left?"
                "pool": pool,  # which limit was exhausted: graphql and core are separate, and the pass alone does not say which
            },
        )

    def read(
        self,
        route: str,
        transport: ghread.TransportFn | None = None,
        key: str | None = None,
        revalidate: ghread.RevalidateFn | None = None,
    ) -> ghread.Read:
        """Read one route: from the snapshot when the fact may be and the entry holds -- no token, no request -- and
        through the throttle otherwise. A fact that gates a write is never answered from the snapshot (`fact_class`).

        `key` is the head SHA the caller holds for the fact. The conditional request that checks an ETag is a read too: it
        waits for the cooldown and spends a token, and where no transport was injected it is `gh api` itself.
        """
        if revalidate is None and transport is None:
            revalidate = ghread.gh_conditional_transport
        return self.cache.read(route, self._live, transport=transport, key=key, revalidate=self._paced(revalidate))

    def _paced(self, revalidate: ghread.RevalidateFn | None) -> ghread.RevalidateFn | None:
        if revalidate is None:
            return None

        def checked(route: str, etag: str) -> ghread.Transport:
            if self.holding():
                self._log(route, ghread.MISS, ghread.COOLING)
                return ghread.Transport(rc=1, detail="cooling down: the entry is not revalidated")
            self.spend()
            res = revalidate(route, etag)
            if _http_status(res.body) == 304:  # the entry still holds: one request spent, and the snapshot answered
                self._log(route, ghread.HIT, ghread.ANSWER)
            else:
                self._log(route, ghread.MISS, ghread.ANSWER if res.rc == 0 else ghread.FAILED)
            return res

        return checked

    def _live(self, route: str, transport: ghread.TransportFn | None = None) -> ghread.Read:
        """Read one route through the throttle: refused while a cooldown holds, bounded by the bucket otherwise."""
        held = self.holding()
        if held:
            self._log(route, ghread.MISS, ghread.COOLING)
            # GitHub's own words travel with the refusal, because the cooldown IS that refusal still holding; the note
            # beside them says no request was sent. Returning anything else here would let a caller read a rate limit as
            # an empty answer, which is the failure this whole interface exists to end.
            return ghread.Read(
                kind=ghread.REFUSED,
                message=str(held.get("message", "")),
                request_id=str(held.get("request_id", "")),
                detail=f"cooling down for {self._remaining(held):.0f}s more (no request sent)",
            )
        hold = quota_hold(self.state_dir, self.clock())
        if hold is not None:
            pool, left = hold
            self._log(route, ghread.MISS, ghread.COOLING)
            return ghread.Read(kind=ghread.FAILED, detail=f"quota pool {pool} has {left} requests left, under SG_GH_FLOOR: no request sent")
        self.spend()
        status: dict[str, int | None] = {"code": None}
        inner = transport or ghread.gh_transport

        def probe(r: str) -> ghread.Transport:
            res = inner(r)
            status["code"] = _http_status(res.body)
            self._note_quota(res.body)
            return res

        out = ghread.read(route, transport=probe)
        self._log(route, ghread.MISS, out.kind, cost=out.cost, remaining=out.remaining)
        if _self_attributed(out, status["code"]):
            self.write_cooldown(out, _pool_of(route, out.body))
        return out

    def _note_quota(self, body: str) -> None:
        """Keep what GitHub says is left of the pool this answer was drawn from. A body without the headers keeps nothing."""
        headers = ghread.headers_of(body)
        try:
            remaining, limit = int(headers["x-ratelimit-remaining"]), int(headers["x-ratelimit-limit"])
            reset = float(headers.get("x-ratelimit-reset") or 0)
        except (KeyError, ValueError):
            return
        if limit <= 0:
            return
        with self._sole_writer():
            state = _read_json(self.quota_path)
            state[headers.get("x-ratelimit-resource") or "core"] = {
                "remaining": remaining, "limit": limit, "reset": reset, "at": self.clock(),
            }
            _write_json(self.quota_path, state)

    def quota_left(self) -> float:
        """The lowest share of its limit any pool still has (1.0 when nothing is known). A pool whose hour has rolled over
        is not counted: its reading describes a window that is gone."""
        now = self.clock()
        shares = [1.0]
        for pool in _read_json(self.quota_path).values():
            if not isinstance(pool, dict):
                continue
            try:
                remaining, limit, reset = float(pool["remaining"]), float(pool["limit"]), float(pool.get("reset") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if limit > 0 and (reset == 0 or reset > now):
                shares.append(max(remaining, 0.0) / limit)
        return min(shares)

    def points_left(self) -> int | None:
        """The fewest points any pool has left, right now, or `None` when no reading describes a running hour (#3043).

        The share says a big pool is low only when it is nearly empty; the points say it directly, and points are what an
        hour is spent in. A pool whose hour has rolled over is not counted, exactly as `quota_left` skips it."""
        now = self.clock()
        lefts: list[int] = []
        for pool in _read_json(self.quota_path).values():
            if not isinstance(pool, dict):
                continue
            try:
                remaining, reset = int(pool["remaining"]), float(pool.get("reset") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if reset == 0 or reset > now:
                lefts.append(max(remaining, 0))
        return min(lefts) if lefts else None

    def graphql_points_left(self) -> int | None:
        """The points the GraphQL pool has left this hour, or `None` when no reading describes it (#3043). The floor counts
        this pool alone: points are not comparable across pools, because an hour holds 5000 of them for the GraphQL pool
        and fewer for a small one, so one absolute floor over every pool would stretch a pool whose whole hour sits under
        the floor itself."""
        pool = _read_json(self.quota_path).get("graphql")
        if not isinstance(pool, dict):
            return None
        try:
            remaining, reset = int(pool["remaining"]), float(pool.get("reset") or 0)
        except (KeyError, TypeError, ValueError):
            return None
        return max(remaining, 0) if reset == 0 or reset > self.clock() else None

    def stretch(self, base: float) -> float:
        """Extra seconds a cycle of `base` seconds waits, 0 while the quota is healthy -- by either reading.

        A pool under 20 percent of its limit stretches the cycle in steps (the share reading, #45), and a pool under
        `SG_GH_STRETCH_FLOOR` points doubles it whatever its share (#3043). The longest of the two holds: a small pool
        that is low by one reading is low."""
        extra = 0.0
        left = self.quota_left()
        for below, times in LOW_QUOTA_STEPS:
            if left < below:
                extra = base * (times - 1.0)
                break
        points = self.graphql_points_left()
        if points is not None and points < _number(STRETCH_FLOOR_ENV, DEFAULT_STRETCH_FLOOR):
            extra = max(extra, base)
        return extra

    def read_aggregate(self, route: str, transport: ghread.TransportFn | None = None) -> ghread.Read:
        """Read one aggregate route (the open PRs or the open issues) from the snapshot while it is younger than
        `SG_GH_AGGREGATE_WINDOW`, live otherwise. Only an answer is kept; a partial answer is read again."""
        window = _aggregate_window()
        cache = Cache(state_dir=self.state_dir, window=window, clock=self.clock)
        key = AGGREGATE_PREFIX + route
        if window > 0:
            entry = cache._load(key)
            if entry is not None and self.clock() - entry["at"] < window:
                body = entry["body"]
                return ghread.Read(kind=ghread.ANSWER, answer=json.loads(body) if body.strip() else None, body=body)
        out = self._live(route, transport)
        if window > 0 and out.body.strip():   # an empty body is no list: keeping it would answer the next window with nothing
            cache.store(key, out)
        return out
    def _log(self, route: str, cache: str, outcome: str, cost: int | None = None, remaining: int | None = None) -> None:
        ghread.log_call(self.state_dir, route, cache, outcome, clock=self.clock, cost=cost, remaining=remaining)

    def _remaining(self, held: dict[str, Any]) -> float:
        return max(0.0, float(held.get("until", 0)) - self.clock())

    def note(self) -> str:
        """The line an operator reads when the loop is quiet because of a cooldown, or an empty string."""
        held = self.holding()
        if not held:
            return ""
        return (
            f"the shared cooldown holds for {self._remaining(held):.0f}s more: GitHub answered '{held.get('message', '')}'"
            f" (request id {held.get('request_id') or 'not named'}), written by pid {held.get('pid')} for the {held.get('pass') or ghread.UNATTRIBUTED} pass"
            f" on the {held.get('pool') or 'unnamed'} pool. No request is sent until it expires."
        )


_PROCESS: Throttle | None = None


def process_throttle() -> Throttle:
    """This process's throttle, built once from the environment it was started with."""
    global _PROCESS
    if _PROCESS is None:
        _PROCESS = Throttle()
    return _PROCESS


def read(route: str, transport: ghread.TransportFn | None = None, **kw: Any) -> ghread.Read:
    """Read one route through this process's shared throttle (a `Throttle` of your own takes `**kw` instead)."""
    return (Throttle(**kw) if kw else process_throttle()).read(route, transport=transport)


def main(argv: list[str]) -> int:
    """The CLI: ghread's exit codes, so a shell caller reads one contract whether the read was throttled or not."""
    if argv[1:2] == ["--stretch"] and len(argv) == 3:
        try:
            base = float(argv[2])
        except ValueError:
            base = -1.0
        if base < 0:
            sys.stderr.write(f"usage: {argv[0]} --stretch <base seconds>\n")
            return _USAGE
        print(f"{process_throttle().stretch(base):.0f}")
        return 0
    aggregate = argv[1:2] == ["--aggregate"]
    args = argv[2:] if aggregate else argv[1:]
    if len(args) != 1 or args[0] in ("-h", "--help") or args[0].startswith("--"):
        sys.stderr.write(
            f"usage: {argv[0] if argv else 'ghthrottle.py'} [--aggregate] <route>   # e.g. repos/o/r/pulls?state=open\n"
            f"       {argv[0] if argv else 'ghthrottle.py'} --stretch <base seconds>\n"
        )
        return _USAGE
    th = process_throttle()
    out = th.read_aggregate(args[0]) if aggregate else th.read(args[0])
    if out.is_answer:
        sys.stdout.write(out.body)
        return 0
    line = out.line()
    note = th.note()
    print(f"{line} -- {note}" if note else line)
    return 2 if out.is_refused else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
