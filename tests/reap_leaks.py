"""End the processes a test session started and left behind (#1722).

A leak is a process whose parent is 1, whose command names a test leftover inside this tree, and which started after the
session began. A process with a living parent, an older process, and a process of another tree are never touched: the
live loop, a nightly check and another session keep theirs. The session calls `reap` when it ends (`tests/conftest.py`);
`python tests/reap_leaks.py <tree> <since-epoch>` does the same by hand.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

LEFTOVERS = ("serial.sh live-check", "live-check.sh --run", "squad.livecheck")


def _seconds(etime):
    """`ps` elapsed time, `[[dd-]hh:]mm:ss`, as seconds."""
    days, _, clock = etime.rpartition("-")
    parts = [int(p) for p in clock.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return int(days or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def _table():
    out = subprocess.run(["ps", "-eo", "pid=,ppid=,etime=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        cols = line.split(None, 3)
        if len(cols) == 4:
            rows.append((int(cols[0]), int(cols[1]), _seconds(cols[2]), cols[3]))
    return rows


def leaks(tree, since, rows=None):
    """The pids of the leftovers of `tree` that started at or after `since` and have parent 1."""
    roots = {str(Path(tree)), str(Path(tree).resolve())}
    now = time.time()
    return [pid for pid, ppid, age, cmd in (_table() if rows is None else rows)
            if ppid == 1 and now - age >= since - 1 and any(k in cmd for k in LEFTOVERS)
            and any(r + "/" in cmd for r in roots)]


def reap(tree, since):
    """End every leak and its descendants; the answer is the pids of the leaks."""
    rows = _table()
    found = leaks(tree, since, rows)
    doomed = list(found)
    for pid in doomed:   # the list grows while it is read, so a child of a child is reached too
        doomed.extend(p for p, parent, _, _ in rows if parent == pid and p not in doomed)
    for pid in doomed:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return found


if __name__ == "__main__":
    ended = reap(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.0)
    print(f"reap-leaks: ended {len(ended)} leftover process(es)" + (f": {ended}" if ended else ""))
