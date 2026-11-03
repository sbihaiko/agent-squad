"""The maintenance step that frees what a dead run holds while the loop is stopped (#1226).

The janitor and the heartbeat run inside the loop. When the loop is stopped, nothing frees a lease whose holder died, and a
run that died with no verdict reads as live and holds a `caps.runs` slot. The health pass runs every 2 minutes whether the
loop runs or not (`health.sh`, launchd), and it calls this step.

The step does two things and writes through their existing owners only (ADR-0149):

- It counts the leases once (`Ceiling.occupancy`). That count is the locked dead-holder reclaim of `squad/caps.py`, with its
  pid-reuse check. This step adds no second reclaim.
- For each run that `active-runs.txt` names, with no final verdict (`caps.final_verdict`, ADR-0167) and whose own process is
  proven dead, it writes the killed verdict through `squad.killed`, the one writer. The active-runs line stays: landing and
  issue reconciliation read it, and the slot counter frees the slot from the verdict.

Proof of death: the last invocation (after the last `run_start` or `resume`) names its engine process in a `call_start` or a
`gate_start` (`pid`, `started_at`), read by the one reader (`squad/run_liveness.py`), and `caps.alive` with the pid-reuse
rule (`run_liveness.same_process`) says that process is gone. A process that
started after the run named the pid is a different process, so it is no proof of life. An invocation that named no process
is not proven dead.

The step never acts on:
- a run inside its launch grace (`request.txt` younger than `caps.LAUNCH_GRACE`), or whose record moved in the last `IDLE_S`;
- a run with a live lease or a live wait marker in its name (`<run>` or `<run>:<child>`);
- a run waiting for a gate slot: a gate or session process lives inside its folder (the janitor's reading, #1191).
It kills no process. This is not ADR-0166's takeover of a live stalled run: it touches only runs whose process is gone.
A missing list or record means absence, never a finished run.

usage: python3 -m squad.reap <active-runs.txt> <checkout> [--dry]
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from squad import caps as C
from squad import killed as K
from squad import record
from squad import run_liveness as RL

IDLE_S = 120   # a record that moved this recently belongs to a process that may still be starting or resuming
LIVE_PROC = re.compile(r"gate\.sh\b|_bundled/claude|/claude\b|\bclaude-code\b|squad\.cli|-m squad\b|[s]quad (start|resume)( |$)")


def _procs():
    """(pid, cwd, command) for every process: one `ps` and one `lsof` for the cwd, as the janitor reads them."""
    try:
        ps = subprocess.run(["/bin/ps", "-axwwo", "pid=,command="], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cmd = {}
    for line in ps.splitlines():
        pid, _, c = line.strip().partition(" ")
        if pid.isdigit():
            cmd[int(pid)] = c.strip()
    lsof = next((p for p in (os.environ.get("SG_LSOF"), "/usr/sbin/lsof", "/usr/bin/lsof") if p and os.path.exists(p)), None)
    if lsof is None:
        return None
    try:
        out = subprocess.run([lsof, "-d", "cwd", "-Fpn"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    rows, pid = [], None
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            rows.append((pid, line[1:], cmd.get(pid, "")))
    return rows


def _held(ceiling, name):
    """True when a live lease or a live wait marker names the run or one of its children."""
    occ = ceiling.occupancy()   # the locked reclaim: a dead holder's lease goes here, a live one stays
    names = [w["holder"] for w in occ["waiting"]] + [h for k in C.KINDS for h in ceiling.holders(k)]
    return any(h == name or str(h).startswith(name + ":") for h in names)


def reap(active_runs, checkout, ceiling=None, procs=None, dry=False, now=None, say=print):
    """One maintenance pass. Return the names of the runs given (or, with `dry`, that would get) the killed verdict.
    `ceiling` and `procs` (a list of (pid, cwd, command)) are injectable; None reads the machine."""
    ceiling = ceiling or C.Ceiling({k: 1 for k in C.KINDS})
    ceiling.occupancy()   # free the dead leases of every kind first, whether a run is listed or not
    now = time.time() if now is None else now
    try:
        rows = Path(active_runs).read_text().split()
    except OSError:
        return []
    done = []
    for r in dict.fromkeys(rows):
        folder = Path(r) if r.startswith("/") else Path(checkout) / "runs" / r
        rec = folder / "record.jsonl"
        try:
            text, moved = rec.read_text(), rec.stat().st_mtime
        except OSError:
            continue   # no record: the launch window or the janitor decides, never this step
        if C.final_verdict(text) is not None or C._in_launch_window(folder, now) or now - moved < IDLE_S:
            continue
        events = RL.invocation(text)
        proc = RL.engine_process(events)
        if proc is None or C.alive(proc[0], proc[1]):
            continue
        name = next((e.get("run_id") for e in events if e.get("event") in ("run_start", "resume") and e.get("run_id")), None) or folder.name
        if _held(ceiling, str(name)) or (name != folder.name and _held(ceiling, folder.name)):
            continue
        if procs is None:
            procs = _procs()
            if procs is None:
                return done   # the process table is unreadable: no proof that no gate waits, so nothing is marked
        here = str(folder)
        if any((cwd == here or cwd.startswith(here + "/")) and LIVE_PROC.search(c) for _, cwd, c in procs):
            continue
        if not dry:
            try:
                if not K.mark_killed(folder):
                    continue
            except record.RecordError:
                continue   # a corrupt record is not ours to rewrite
        say(f"REAPED {folder.name}: no verdict and its process {proc[0]} is gone; killed verdict written, active-runs line kept")
        done.append(folder.name)
    return done


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv if a != "--dry"]
    if len(args) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    reap(args[0], args[1], dry=dry)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
