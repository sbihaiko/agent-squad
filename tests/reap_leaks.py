"""End the processes a test session started and left behind (#1722).

A leak is a process whose command names a test leftover inside this tree, which started after the session began, and
which no process of the tree owns any more: its parent is 1, or no ancestor names the tree or runs pytest (Linux hands
an orphan to a subreaper, never to 1). A process a test or a suite of the tree still owns, an older process, and a
process of another tree are never touched: the live loop, a nightly check and another session keep theirs. The session calls `reap` when it ends (`tests/conftest.py`);
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
    # One -o per column: procps reads the text after `=` as the column's header, to the end of the argument, so a joined
    # `pid=,ppid=,...` list is one column there and no row has four fields.
    out = subprocess.run(["ps", "-e", "-o", "pid=", "-o", "ppid=", "-o", "etime=", "-o", "command="],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        cols = line.split(None, 3)
        if len(cols) == 4:
            rows.append((int(cols[0]), int(cols[1]), _seconds(cols[2]), cols[3]))
    return rows


def _owned(pid, parents, commands, roots):
    """True when an ancestor of `pid` names the tree or runs pytest: a test or a suite still owns the process."""
    seen = set()
    parent = parents.get(pid)
    while parent and parent != 1 and parent not in seen:
        seen.add(parent)
        cmd = commands.get(parent, "")
        if " -m pytest" in cmd or any(r + "/" in cmd for r in roots):
            return True
        parent = parents.get(parent)
    return False


def leaks(tree, since, rows=None):
    """The pids of the leftovers of `tree` that started at or after `since` and that no process of the tree owns."""
    roots = {str(Path(tree)), str(Path(tree).resolve())}
    now = time.time()
    rows = _table() if rows is None else rows
    parents = {pid: ppid for pid, ppid, _, _ in rows}
    commands = {pid: cmd for pid, _, _, cmd in rows}
    return [pid for pid, ppid, age, cmd in rows
            if now - age >= since - 1 and any(k in cmd for k in LEFTOVERS) and any(r + "/" in cmd for r in roots)
            and (ppid == 1 or not _owned(pid, parents, commands, roots))]


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
