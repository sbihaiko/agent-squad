"""The run-level heartbeat: a wedged run is killed, not merely logged.

The monitor writes a stuck call to `monitor.log` and changes nothing, so a run whose model never answers holds its slot until
its own deadline (issue #72). This pass acts.

Two signals must hold at once, and neither is enough alone:

- the process tree of the run's latest call has not moved a CPU second for `HB_FLAT_MIN` minutes (default 5), the rule
  `heartbeat.sh` already applies to a hung GUI test host;
- the run's record has taken no new event for `HB_RECORD_MIN` minutes (default 5).

A long Bash call — a full test suite, a build — writes no record event for minutes while its process works, so a watchdog
that reads the record alone kills healthy work. A run waiting on a person or a lock burns no CPU while its record moves,
so a watchdog that reads the CPU alone kills a run that is merely idle.

On a kill the pass appends the run's verdict through `squad.killed` (the one writer) with the reason
`killed: heartbeat_stale`, prints one line naming the run, its flat CPU time and its last node, and leaves the branch and
the worktree exactly as they are: the run's work belongs to the landing pass. Stamps live in the stamp directory, one file
per run, so a pass that dies halfway loses only the reading it was taking.

A run with no call open has no process the record can name, so it is judged on nothing and left alone: only a call the
record carries a pid for is measured.

usage: python3 -m squad.heartbeat <active-runs.txt> <checkout> <stamp dir>
"""
import os
import signal
import sys
import time
from pathlib import Path

from squad import killed as K
from squad import procs, record

HB_FLAT_MIN = 5.0      # minutes a call's process tree may hold the same CPU seconds before it is flat
HB_RECORD_MIN = 5.0    # minutes the record may hold the same size before it is frozen
CPU_STEP = 1.0         # seconds of CPU the tree may gain between two reads and still read as unmoved: `ps` quantizes to a second
TERM_GRACE_S = 2.0     # seconds between SIGTERM and SIGKILL for a tree that ignores the first


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_time(text):
    """`ps` CPU time as seconds. It prints `MM:SS`, `HH:MM:SS` or `D-HH:MM:SS`; anything else is 0."""
    text = text.strip()
    days, _, clock = text.rpartition("-")
    if not clock:
        days, clock = "0", text
    parts = clock.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0.0
    if len(nums) == 1:
        nums = [0, 0, nums[0]]
    secs = 0
    for n in nums:
        secs = secs * 60 + n
    return float(days or 0) * 86400.0 + secs


def _ps_table():
    """Every process as (pid, ppid, cpu seconds), from one read of the process table (squad/procs.py, no exec)."""
    return [(p.pid, p.ppid, p.cpu) for p in procs.snapshot().values()]


def process_tree(root, rows):
    """The pids of `root` and all its descendants, children first. A pid `ps` no longer reports is left out."""
    by_parent = {}
    for pid, ppid, _ in rows:
        by_parent.setdefault(ppid, []).append(pid)
    tree, stack = [], [root]
    while stack:
        pid = stack.pop()
        tree.append(pid)
        stack.extend(by_parent.get(pid, ()))
    return tree


def cpu_seconds(root, rows):
    """The CPU seconds the tree under `root` has burned. 0 when `ps` does not report it (the process is gone)."""
    tree = set(process_tree(root, rows))
    return sum(c for pid, _, c in rows if pid in tree)


def run_pid(events):
    """The pid to measure: the run's latest call whose process is still alive, or None.

    A run between two calls has no open call of its own, and the record names no process for the run itself, so the pass
    reads nothing and writes nothing: silence is not evidence.
    """
    for e in reversed(events):
        if e["event"] == "call_start" and e.get("pid") and _alive(e["pid"]):
            return e["pid"], e["node"]
    return None, None


def last_node(events):
    for e in reversed(events):
        if e.get("node"):
            return e["node"]
    return None


def stamp(stamp_dir, run, cpu, now):
    """The previous reading for this run, and the file the next pass reads. Return (cpu, since) or None."""
    f = Path(stamp_dir) / Path(run).name
    if f.exists():
        try:
            last, since = f.read_text().split()
            return float(last), float(since)
        except ValueError:
            pass
    return None, None


def scan(run_file, checkout, stamp_dir, now=None, rows=None, kill=os.kill, mark=K.mark_killed, out=None, read=procs.snapshot,
         killpg=os.killpg):
    """One heartbeat pass over the runs an active list names. Return one record per kill.

    `rows`, `kill` and `mark` are injectable so a test runs the pass with a stub run and no real process.
    """
    now = time.time() if now is None else now
    stamp_dir = Path(stamp_dir)
    stamp_dir.mkdir(parents=True, exist_ok=True)
    rows = _ps_table() if rows is None else rows
    out = sys.stdout if out is None else out
    flat_min = float(os.environ.get("HB_FLAT_MIN", HB_FLAT_MIN))
    record_min = float(os.environ.get("HB_RECORD_MIN", HB_RECORD_MIN))
    done = []
    for line in Path(run_file).read_text().splitlines() if Path(run_file).exists() else []:
        folder = Path(line.strip())
        if not line.strip():
            continue
        folder = folder if folder.is_absolute() else Path(checkout) / folder
        rec = folder / "record.jsonl"
        if not rec.exists():
            continue
        events = record.read(rec)
        if events and events[-1]["event"] == "verdict":
            (stamp_dir / folder.name).unlink(missing_ok=True)
            continue
        pid, _ = run_pid(events)
        if pid is None:
            continue
        cpu = cpu_seconds(pid, rows)
        s = stamp_dir / folder.name
        last, since = stamp(stamp_dir, folder, cpu, now)
        if last is None or cpu > last + CPU_STEP:
            s.write_text(f"{cpu} {now}\n")      # the tree moved: this reading is the new baseline
            continue
        record_frozen = now - rec.stat().st_mtime >= record_min * 60
        if not record_frozen:
            continue
        if now - (since or now) < flat_min * 60:
            continue
        node = last_node(events)
        try:
            survivors = stop_run(pid, read, kill, killpg)
        except procs.ReadError:
            continue   # no owned set from an unreadable table, so nothing is signalled
        mark(folder, "killed: heartbeat_stale")
        s.unlink(missing_ok=True)
        line_text = f"HEARTBEAT KILL {folder.name}: flat at {cpu:.0f}s of CPU for {flat_min:.0f}m and its record still for {record_min:.0f}m; last node {node or 'none'}"
        print(line_text, file=out, flush=True)
        for left in survivors:
            print(f"HEARTBEAT SURVIVOR {folder.name}: pid {left} outlived SIGKILL", file=out, flush=True)
        done.append({"run": folder.name, "folder": str(folder), "cpu_s": cpu, "node": node, "at": now})
    return done


def owned_procs(root, table):
    """{pid: (start, pgid)} for `root` and every descendant at this read. A process with no start time is never owned: its
    pid could not be checked again before a signal."""
    by_parent = {}
    for p in table.values():
        by_parent.setdefault(p.ppid, []).append(p.pid)
    out = {}
    stack = [root]
    while stack:
        pid = stack.pop()
        p = table.get(pid)
        if p is None or p.start is None or pid in out:
            continue
        out[pid] = (p.start, p.pgid)
        stack.extend(by_parent.get(pid, ()))
    return out


def _holds(table, pid, start):
    """True while `pid` names the process that started at `start`, and is not a zombie."""
    p = table.get(pid)
    return p is not None and p.start == start and p.stat != "Z"


def _signal(send, target, sig):
    """One signal; a process that ended between the read and the signal is no error, so the pass goes on to the next run."""
    try:
        send(target, sig)
    except (ProcessLookupError, PermissionError):
        pass


def kill_owned(owned, read, kill=os.kill, killpg=os.killpg, sleep=time.sleep):
    """TERM, then KILL, to each process group an owned process led at the owned read while an owned member still holds its
    start time, and to each owned process that still holds its start time. Every signal follows a fresh read, so a reused pid is never signalled. A group
    whose leader is not owned is never signalled: an engine that shares its parent's group would otherwise signal the parent,
    the driver that started it. Return the owned pids that survive KILL."""
    led = {g for _, (_, g) in owned.items() if g > 1 and g in owned and owned[g][1] == g}   # groups an owned process leads
    for sig in (signal.SIGTERM, signal.SIGKILL):
        now = read()
        for pgid in sorted({g for pid, (start, g) in owned.items() if g in led and _holds(now, pid, start)}):
            _signal(killpg, pgid, sig)
        for pid, (start, _) in owned.items():
            if _holds(read(), pid, start):
                _signal(kill, pid, sig)
        if sig is signal.SIGTERM:
            sleep(TERM_GRACE_S)
    final = read()
    return [pid for pid, (start, _) in owned.items() if _holds(final, pid, start)]


def stop_run(root, read=procs.snapshot, kill=os.kill, killpg=os.killpg, sleep=time.sleep):
    """End the run's owned processes: its engine, its descendants (the tool shells among them) and their groups, as one read
    of the process table names them. Return the owned pids that survive."""
    return kill_owned(owned_procs(root, read()), read, kill, killpg, sleep)


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scan(argv[0], argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
