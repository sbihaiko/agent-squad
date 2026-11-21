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


def _table_procs():
    """The table through squad.procs, the reader built for a session inside the child's sandbox-exec wall (#1508), as
    (pid, ppid, age, command) rows; None when that reader cannot answer, so the binary below may."""
    try:
        from squad import procs
    except ImportError:
        return None
    try:
        table = procs.snapshot()
    except (procs.ReadError, OSError):   # a reader that cannot answer is no reader, never a crash of the session that ends
        return None
    now = time.time()
    # A process whose start the system will not say is never read as one that started inside the session: unknown is old.
    return [(p.pid, p.ppid, now - p.start if p.start else float("inf"), p.command) for p in table.values()]


def _table():
    # The read goes through squad.procs first: the child's sandbox refuses to exec the process table binary, and a session
    # that cannot read the table would end red on a green tree (#1691, #1508).
    rows = _table_procs()
    if rows is not None:
        return rows
    # One -o per column: procps reads the text after `=` as the column's header, to the end of the argument, so a joined
    # `pid=,ppid=,...` list is one column there and no row has four fields. -ww keeps the whole command: a cut line
    # loses the leftover's name.
    try:
        out = subprocess.run(["ps", "-e", "-ww", "-o", "pid=", "-o", "ppid=", "-o", "etime=", "-o", "command="],
                             capture_output=True, text=True).stdout
    except OSError:   # the sandbox refused the binary too: no rows, and the reap ends nothing it cannot see
        return []
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


def reap(tree, since, rows=None):
    """End every leak and its descendants; the answer is the pids of the leaks."""
    rows = _table() if rows is None else rows
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


def _inside_a_session(rows, pid):
    """True when an ancestor of `pid` runs pytest: another session started this one."""
    parents = {row: ppid for row, ppid, _, _ in rows}
    commands = {row: cmd for row, _, _, cmd in rows}
    seen = set()
    parent = parents.get(pid)
    while parent and parent != 1 and parent not in seen:
        seen.add(parent)
        if " -m pytest" in commands.get(parent, ""):
            return True
        parent = parents.get(parent)
    return False


def session_reap(tree, since, rows=None, pid=None):
    """What a pytest session does when it ends (tests/conftest.py): end the leftovers it started, and never one that
    another session still owns. A session a test started (a nested session, its ancestry runs pytest) ends nothing: the
    detached processes it sees are the outer session's, because a `nohup` child's parent is 1, so they read as leftovers
    of this tree. Its end killed a live check the outer session had just started, eight-tenths of a second after it took
    its pid (#2680). The outer session's own end, and the one by hand, still end every leftover."""
    rows = _table() if rows is None else rows
    if _inside_a_session(rows, os.getpid() if pid is None else pid):
        return []
    return reap(tree, since, rows)


if __name__ == "__main__":
    ended = reap(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.0)
    print(f"reap-leaks: ended {len(ended)} leftover process(es)" + (f": {ended}" if ended else ""))
