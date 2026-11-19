#!/usr/bin/env python3
"""What the loop spends, as health causes (ADR-0277: "the loop can see what it spends").

`health.sh` runs this beside its other checks. It reads the two things the GitHub interface leaves in `$SG_STATE` -- the shared
cooldown (`gh-cooldown.json`) and the call log (`api.log`, one line per call, format in `ghread.py`) -- and returns the causes
they carry, so the next exhaustion is a diagnosis already written rather than an afternoon of archaeology:

* `gh-cooldown`: the shared cooldown holds. It names WHEN it expires and WHICH PASS owns it -- the pass whose request GitHub
  refused. That is what separates "the loop is quiet" from "the loop has no quota left", and a second pass held back by the
  cooldown never takes the ownership.
* `gh-quota`: a GraphQL or core pool is under `SG_GH_FLOOR`, so live reads are held unsent. It names the pool and the pass with the
  most GraphQL requests in the hour (#2355).
* `gh-miss-rate`: the snapshot is not answering the repeats. Of the reads in the window that the snapshot MAY answer
  (`ghthrottle.fact_class` is not `LIVE`), at least `SG_HEALTH_GH_MISS_RATE` missed it. The reads that gate a write are live by
  design (the liveness rule) and a read the cooldown held back sent no request, so neither says anything about the snapshot and
  neither is counted, and neither is a write (cache `write`, whatever its outcome): a write says nothing about the snapshot. Fewer than `SG_HEALTH_GH_MISS_MIN_CALLS` such reads is a burst, not a sustained rate.

Documented defaults (each overridable in the environment `health.sh` runs in; a bad value falls back to the default):

    SG_HEALTH_GH_MISS_RATE        0.8   the share of snapshot-eligible reads that missed
    SG_HEALTH_GH_MISS_MINUTES      30   the window the rate is read over
    SG_HEALTH_GH_MISS_MIN_CALLS    20   the fewest eligible reads in the window that make a rate

Why 0.8: a loop that beats every 30 s against a 60 s snapshot window misses every other read of a fact by construction, so half
is the healthy floor; four in five means the snapshot is off (`SG_GH_CACHE_WINDOW=0`), its directory is unwritable, or the keys
churn. Nothing here reaches the network, and nothing here writes: it reads two files and prints.

    python3 ghspend.py causes [state-dir]     # one cause per line: <name> TAB <brief> TAB <detail>; nothing when healthy

The brief is the short form the hub's footer shows beside the cause's name (`until 09:08:20Z, owned by landing`); the detail is
the sentence `health.sh` alerts with. Neither holds a quote, a backslash, a tab or a newline.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

import ghread
import ghthrottle

RATE_ENV = "SG_HEALTH_GH_MISS_RATE"
MINUTES_ENV = "SG_HEALTH_GH_MISS_MINUTES"
MIN_CALLS_ENV = "SG_HEALTH_GH_MISS_MIN_CALLS"
DEFAULT_RATE = 0.8
DEFAULT_MINUTES = 30.0
DEFAULT_MIN_CALLS = 20.0
_TAIL_BYTES = 1_000_000  # the window is minutes of a log that is rotated at two megabytes: the tail holds all of it


class Cause(NamedTuple):
    name: str
    brief: str
    detail: str


def _number(name: str, default: float, *, ceiling: float | None = None) -> float:
    """A configured number, or the documented default: a bad value in the loop's environment never stops a health pass."""
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 and (ceiling is None or value <= ceiling) else default


def _clean(text: Any) -> str:
    """One line with no quote or backslash: it is printed as TSV and written into health.json by a shell."""
    return " ".join(str(text).replace('"', "'").replace("\\", "/").split())


def _clock(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _cooldown(state: Path, now: float) -> Cause | None:
    try:
        held = json.loads((state / ghthrottle.COOLDOWN_FILE).read_text())
        until = float(held["until"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(held, dict) or until <= now:
        return None
    owner = held.get("pass") if isinstance(held.get("pass"), str) and held.get("pass") else ghread.UNATTRIBUTED
    pool = f" on the {held['pool']} pool" if isinstance(held.get("pool"), str) and held.get("pool") else ""
    wrote = held.get("at")
    when = f", written {_clock(float(wrote))}" if isinstance(wrote, (int, float)) and not isinstance(wrote, bool) else ""
    return Cause(
        "gh-cooldown",
        _clean(f"until {_clock(until)[11:]}, owned by {owner}"),
        _clean(
            f"the shared GitHub cooldown holds until {_clock(until)} ({max(0, int(until - now))}s more), owned by the {owner} pass"
            f"{pool}{when}: GitHub answered '{held.get('message', '')}'. No pass sends a request until it expires"
        ),
    )


def _quota(state: Path, now: float) -> Cause | None:
    hold = ghthrottle.quota_hold(state, now)
    if hold is None:
        return None
    pool, left = hold
    spent = Counter(
        c.pass_name
        for c in _calls(state)
        if c.transport == "GraphQL" and c.cache == ghread.MISS and c.outcome != ghread.COOLING and c.at >= now - 3600
    )
    top = spent.most_common(1)
    if top:
        who, n = top[0]
        brief = f"{pool} pool at {left} left, top GraphQL spend {who} ({n} in the hour)"
        spend = f"The {who} pass spent the most GraphQL requests in the last hour ({n})."
    else:
        brief = f"{pool} pool at {left} left, no GraphQL spend in the hour"
        spend = "No GraphQL request spent in the last hour."
    return Cause(
        "gh-quota",
        _clean(brief),
        _clean(f"the {pool} pool has {left} requests left, under SG_GH_FLOOR, so every live read is held unsent. {spend}"),
    )


def _calls(state: Path) -> list[ghread.Call]:
    path = state / ghread.LOG_FILE
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = raw.splitlines()[1:] if size > _TAIL_BYTES else raw.splitlines()  # the first line of a tail is cut mid-way
    return [c for c in map(ghread.parse_call, lines) if c is not None]


def _miss_rate(state: Path, now: float) -> Cause | None:
    rate = _number(RATE_ENV, DEFAULT_RATE, ceiling=1.0)
    minutes = _number(MINUTES_ENV, DEFAULT_MINUTES)
    need = int(_number(MIN_CALLS_ENV, DEFAULT_MIN_CALLS))
    eligible = [
        c
        for c in _calls(state)
        if c.at >= now - minutes * 60
        and c.outcome != ghread.COOLING
        and c.cache in (ghread.HIT, ghread.MISS)
        and c.outcome != ghread.DONE   # a write GitHub took, logged before cache `write` existed (#1328)
        and ghthrottle.fact_class(c.route) != ghthrottle.LIVE
    ]
    misses = [c for c in eligible if c.cache == ghread.MISS]
    if len(eligible) < need or not eligible or len(misses) / len(eligible) < rate:
        return None
    pct = round(100 * len(misses) / len(eligible))
    top, top_n = Counter(c.pass_name for c in misses).most_common(1)[0]
    return Cause(
        "gh-miss-rate",
        _clean(f"{pct}% of {len(eligible)} reads missed"),
        _clean(
            f"{len(misses)} of {len(eligible)} snapshot-eligible reads in the last {minutes:g} min missed the snapshot ({pct}%), "
            f"most of them ({top_n}) by the {top} pass: the snapshot is not answering the repeats "
            f"(SG_GH_CACHE_WINDOW off, an unwritable state dir, or keys that change every read)"
        ),
    )


def causes(state: Path | str | None = None, now: float | None = None) -> list[Cause]:
    """The causes the cooldown and the call log carry right now; an empty list is a healthy spend."""
    base = Path(state) if state is not None else ghread.state_dir()
    at = time.time() if now is None else now
    return [c for c in (_cooldown(base, at), _quota(base, at), _miss_rate(base, at)) if c is not None]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or argv[1] != "causes":
        sys.stderr.write(f"usage: {argv[0] if argv else 'ghspend.py'} causes [state-dir]\n")
        return 64
    for c in causes(argv[2] if len(argv) == 3 else None):
        print(f"{c.name}\t{c.brief}\t{c.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
