"""The work a drain waits for besides a live run (#1711).

A drain stops the loop and sends TERM to the driver group, so it waits first for every piece of work in flight. A run is
one of them and `run_liveness.drain` answers it. Two more are not runs and are not in that list: a child review, which
holds a `reviews` slot (`caps.KINDS`), and a landing, which holds the loop's own `locks/single-wland` lock. Without this
reading the stop lands in the middle of a review or a landing and takes it with the group.

The rule is the one the run read already carries: a fact that cannot be read is `unknown`, and `unknown` holds the drain.
So every failure raises `ReadError`, and a lock with no readable pid is unknown rather than "nobody is landing": a drain
that waits is safe, and one that stops under live work is not.

A lease is read through `caps`, the owner of the slots, and its holder is judged by `caps.alive`: a pid that a later
process reused is not the holder. This module is stdlib and `caps` only, and it writes nothing.

Guard: `tests/test_run_liveness.py`, `contrib/squad-goal/scripts/test-watchd-drain.sh`.
"""
from __future__ import annotations

import json
from pathlib import Path

from squad import caps
from squad.procs import ReadError

REVIEWS = "reviews"          # the caps kind a child review takes (`runreview.sh`)
LANDING = ("locks", "single-wland", "pid")   # the lock a landing holds, and the file that names its holder (`wave.sh`)


def _reviews() -> list[str]:
    """The live child reviews, one name per lease. A lease whose file cannot be read is unknown, never "no review"."""
    out = []
    folder = caps.folder() / REVIEWS
    try:
        files = sorted(folder.glob("*.json"))
    except OSError as e:
        raise ReadError(f"cannot read {folder}: {e}") from e
    for p in files:
        try:
            lease = json.loads(p.read_text())
        except FileNotFoundError:
            continue      # a lease released between the listing and the read is no review
        except (OSError, ValueError) as e:
            raise ReadError(f"cannot read the review lease {p}: {e}") from e
        pid = lease.get("pid", -1)
        if not isinstance(pid, int):
            raise ReadError(f"the review lease {p} names no pid")
        if caps.alive(pid, lease.get("since")):
            out.append(str(lease.get("holder") or pid))
    return out


def _landing(state: Path) -> list[str]:
    """The landing in flight, named by its holder pid. A lock with no pid, or one that cannot be read, is unknown: the
    landing took the lock before it wrote its pid, so a missing number is not "nobody is landing"."""
    p = state.joinpath(*LANDING)
    try:
        text = p.read_text()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise ReadError(f"cannot read the landing lock {p}: {e}") from e
    pid = text.strip()
    if not pid.isdigit():
        raise ReadError(f"the landing lock {p} names no pid")
    return [f"landing:{pid}"] if caps.alive(int(pid)) else []


def live_work(state) -> list[str]:
    """The live work besides a run: one name per child review in flight and one for a landing in flight, each once.
    Raises `ReadError` when any of them cannot be read, which holds a drain as a live one does."""
    return list(dict.fromkeys(_reviews() + _landing(Path(state))))
