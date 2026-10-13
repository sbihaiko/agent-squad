"""The run-level heartbeat watchdog: a live run whose process tree has gone flat AND whose record has stopped is killed.

ADR-0051 gives one call a deadline. This module covers what that deadline does not: a run that wedges between two calls
holds its slot until its own deadline, and a loop that has stopped looks exactly like a loop with nothing to do.

Both signals are needed, and neither is enough alone:

* the CPU time of the run's process tree has not moved for `HB_FLAT_MIN` minutes (default 5). This is the rule
  `contrib/squad-goal/scripts/heartbeat.sh` already applies to a hung test host.
* the run's record has taken no new event for `HB_RECORD_MIN` minutes (default 5).

A long Bash call (a full test suite, a build) writes no record event for minutes while its process works, so a watchdog
that reads the record alone kills healthy work. A tree whose CPU is flat while the record still grows is a run between
two calls, and it is left alone.

A kill appends the killed verdict `killed: heartbeat_stale` to the run's record through `squad.killed`, the one writer of
that verdict, and prints one line naming the run id, the flat CPU time and the last node. The branch and the worktree are
left exactly as they are: the run's work belongs to the landing pass, never to the watchdog.

usage: python3 -m squad.heartbeat ACTIVE_RUNS_FILE CHECKOUT STATE_DIR
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from squad import killed as K
from squad import record

KILL = "killed: heartbeat_stale"    # the reason the killed verdict carries
CPU_MOVED_S = 1.0                   # CPU seconds a tree may gain between two passes and still count as flat
FLAT_MIN = 5.0                      # HB_FLAT_MIN
RECORD_MIN = 5.0                    # HB_RECORD_MIN


def ps_seconds(text):
    """The seconds in one `ps -o time=` field, printed as [[dd-]hh:]mm:ss[.ss]. 0.0 when it cannot be read."""
    days, dash, rest = text.partition("-")
    if not dash:
        rest = days
    secs = 0.0
    try:
        for part in rest.split(":"):
            secs = secs * 60 + float(part)
    except ValueError:
        return 0.0
    return secs + (int(days) * 86400 if dash and days.isdigit() else 0.0)


def proc_table(text=None):
    """pid -> (parent pid, CPU seconds) for the processes this user can see, from one `ps`."""
    if text is None:
        text = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,time="], capture_output=True, text=True, check=False).stdout
    out = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) < 3 or not f[0].isdigit():
            continue
        out[int(f[0])] = (int(f[1]) if f[1].isdigit() else 0, ps_seconds(f[2]))
    return out


def tree(root, table):
    """The root pid and every descendant of it, deepest last, as far as the table reaches."""
    out, seen = [], set()

    def walk(pid):
        if pid is None or pid in seen or pid not in table:
            return
        seen.add(pid)
        out.append(pid)
        for q, (pp, _) in table.items():
            if pp == pid:
                walk(q)

    walk(root)
    return out


def ancestors(table, pid=None):
    """The pids above `pid` (this process by default): a watchdog never signals one of them."""
    out, cur = set(), os.getpid() if pid is None else pid
    while cur in table and table[cur][0] not in out:
        cur = table[cur][0]
        out.add(cur)
    return out


def kill_tree(pids):
    """Terminate the pids of one run, deepest first, then kill what is left: a wedged run is not asked twice."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass


def run_folder(entry, checkout):
    """The run folder of one active-runs entry: an absolute path is its own folder, an id lives under the checkout."""
    return Path(entry) if entry.startswith("/") else Path(checkout) / "runs" / entry


def events(folder):
    """The run's events, or None when the record is missing or unreadable: a read that fails is never a kill."""
    path = Path(folder) / "record.jsonl"
    if not path.exists():
        return None
    try:
        return record.read(path)
    except (record.RecordError, OSError):
        return None


def root_pid(evs):
    """The pid the run's last call recorded, or None when it has not started one."""
    calls = [e for e in evs if e["event"] == "call_start"]
    return calls[-1]["pid"] if calls else None


def stamp(path):
    """The flat-CPU stamp of one run: (CPU seconds, the time of the last movement), or None."""
    try:
        cpu, since = Path(path).read_text().split()
        return float(cpu), float(since)
    except (OSError, ValueError):
        return None


def entries(active):
    """The run ids and run folders the active-runs file lists, in its own order."""
    try:
        return Path(active).read_text().split()
    except OSError:
        return []


def sweep(active, checkout, state, now=None, table=None, minutes=None, kill=kill_tree, mark=K.mark_killed, out=None):
    """One watchdog pass over the runs the active-runs file lists. Return the lines it printed, one per kill.

    `minutes` is the pair (flat, record), the two windows; its default reads `HB_FLAT_MIN` and `HB_RECORD_MIN`. A run that
    has a verdict, a record that cannot be read, or no live process is not this pass's work: the first two are nothing to
    kill, and the last belongs to the dead-run pass of `contrib/squad-goal/scripts/heartbeat.sh`.
    """
    now = time.time() if now is None else now
    flat_min, record_min = minutes or (float(os.environ.get("HB_FLAT_MIN", FLAT_MIN)), float(os.environ.get("HB_RECORD_MIN", RECORD_MIN)))
    table = proc_table() if table is None else table
    out = out or print
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    mine, lines = ancestors(table), []
    for entry in entries(active):
        folder = run_folder(entry, checkout)
        evs, st = events(folder), state / folder.name
        if not evs or evs[-1]["event"] == "verdict":
            st.unlink(missing_ok=True)
            continue
        pids = [p for p in tree(root_pid(evs), table) if p not in mine]
        if not pids:
            st.unlink(missing_ok=True)          # the run's process is gone: the dead-run pass owns that run
            continue
        cpu = sum(table[p][1] for p in pids)
        was = stamp(st)
        flat = was is not None and cpu <= was[0] + CPU_MOVED_S and now - was[1] >= flat_min * 60
        cpu, since = (max(cpu, was[0]), was[1]) if flat else (cpu, now)
        st.write_text(f"{cpu} {since}\n")
        last = evs[-1]
        if not flat or now - last["ts"] < record_min * 60:
            continue                            # a working tree, or a record that still grows under a flat tree
        mark(folder, KILL)
        kill(list(reversed(pids)))              # the tree, deepest first: a dying parent must not orphan its children
        st.unlink(missing_ok=True)
        lines.append(f"STALE HEART run={folder.name} cpu={cpu:g}s last node {last.get('node')}, killed: heartbeat_stale")
        out(lines[-1])
    return lines


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 3:
        print("usage: python3 -m squad.heartbeat ACTIVE_RUNS_FILE CHECKOUT STATE_DIR", file=sys.stderr)
        return 2
    sweep(a[0], a[1], a[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
