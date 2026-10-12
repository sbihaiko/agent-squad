#!/usr/bin/env python3
"""One shared throttle in front of the one GitHub reader (#4, ADR-0277).

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

Why 300/60 s: the landing pass is the loop's heaviest reader and it reads in bursts, not in a stream. Its eligible-branch
collection reads the open-PR list once and then two routes per candidate (the issue's state and its `Blocked by` body) --
`landing_pass_burst(n) = 1 + 2n`. A 149-candidate pass fits inside one full bucket with no wait at all, which is more
than this loop has ever had waiting (`forkclose.sh` collected against a 48-candidate board on 2026-10-12), and it is a
third of the ~900 REST points per minute GitHub's own secondary limits allow. A doubled reader shares the same 300: it
can spend the bucket twice as fast and then it waits, which is the whole point -- two readers, one rate.

The transport stays invisible to callers: a caller hands over a route and reads back a `ghread.Read`, and the fact that
REST is the transport behind it is not part of what it is handed. A sibling slice adds GraphQL behind the same interface.

This module is reached from a shell script the same way `ghread.py` is -- `python3 ghthrottle.py <route>` -- with the same
exit codes (0 the answer on stdout, 2 a refusal, 1 a failure before a verdict), so a caller reads one contract whether the
read was throttled or not. It does not own the per-resource cache, the api.log/health surface or the direct-read check.

Nothing here reaches the network on its own account, and nothing here logs to `$SG_STATE`: a caller that only ever
replaces its knowledge from an answer keeps what it had, and the refusal line is what tells the operator why the loop is
quiet (see `forkclose.sh`'s `gh_read`, which prints it).
"""

from __future__ import annotations

import contextlib
import fcntl
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

DEFAULT_BUDGET = 300  # requests per window, shared by every process
DEFAULT_WINDOW = 60.0  # seconds in a window
DEFAULT_COOLDOWN = 900.0  # the first self-attributed 403/429 holds every reader back for this long
DEFAULT_JITTER = 60.0  # seconds of jitter added to it, drawn per writer, so two wake-ups do not collide

# The landing pass's own burst (forkclose.sh's eligible-branch collection): the open-PR list once, then the state and
# the `Blocked by` body of each candidate. Measured against the pass, and asserted against it in tests/test_gh_throttle.
LANDING_PASS_FIXED_READS = 1
LANDING_PASS_READS_PER_CANDIDATE = 2

BUCKET_FILE = "gh-budget.json"
COOLDOWN_FILE = "gh-cooldown.json"

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


def default_state_dir() -> Path:
    """Where the bucket and the cooldown live: the loop's own state dir, so every process of the loop shares them."""
    return Path(os.environ.get(STATE_ENV) or Path.home() / ".cache" / "squad-goal")


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
    ):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.budget = float(budget) if budget is not None else _number(BUDGET_ENV, DEFAULT_BUDGET)
        self.window = float(window) if window is not None else _number(WINDOW_ENV, DEFAULT_WINDOW)
        self.cooldown = float(cooldown) if cooldown is not None else _number(COOLDOWN_ENV, DEFAULT_COOLDOWN)
        self.jitter = float(jitter) if jitter is not None else _number(JITTER_ENV, DEFAULT_JITTER)
        self.clock = clock
        self.sleeper = sleeper
        self.rand = rand

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

    def write_cooldown(self, out: ghread.Read) -> None:
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
            },
        )

    def read(self, route: str, transport: ghread.TransportFn | None = None) -> ghread.Read:
        """Read one route through the throttle: refused while a cooldown holds, bounded by the bucket otherwise."""
        held = self.holding()
        if held:
            # GitHub's own words travel with the refusal, because the cooldown IS that refusal still holding; the note
            # beside them says no request was sent. Returning anything else here would let a caller read a rate limit as
            # an empty answer, which is the failure this whole interface exists to end.
            return ghread.Read(
                kind=ghread.REFUSED,
                message=str(held.get("message", "")),
                request_id=str(held.get("request_id", "")),
                detail=f"cooling down for {self._remaining(held):.0f}s more (no request sent)",
            )
        self.spend()
        status: dict[str, int | None] = {"code": None}
        inner = transport or ghread.gh_transport

        def probe(r: str) -> ghread.Transport:
            res = inner(r)
            status["code"] = _http_status(res.body)
            return res

        out = ghread.read(route, transport=probe)
        if _self_attributed(out, status["code"]):
            self.write_cooldown(out)
        return out

    def _remaining(self, held: dict[str, Any]) -> float:
        return max(0.0, float(held.get("until", 0)) - self.clock())

    def note(self) -> str:
        """The line an operator reads when the loop is quiet because of a cooldown, or an empty string."""
        held = self.holding()
        if not held:
            return ""
        return (
            f"the shared cooldown holds for {self._remaining(held):.0f}s more: GitHub answered '{held.get('message', '')}'"
            f" (request id {held.get('request_id') or 'not named'}), written by pid {held.get('pid')}. No request is sent until it expires."
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
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        sys.stderr.write(f"usage: {argv[0] if argv else 'ghthrottle.py'} <route>   # e.g. repos/o/r/pulls?state=open\n")
        return _USAGE
    th = process_throttle()
    out = th.read(argv[1])
    if out.is_answer:
        sys.stdout.write(out.body)
        return 0
    line = out.line()
    note = th.note()
    print(f"{line} -- {note}" if note else line)
    return 2 if out.is_refused else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
