"""The retirement of a run whose wave the age cap requeued (#1970).

The landing pass sends the issues of a capped wave back to the queue. The run that worked them must stop first: a run that
keeps working holds a run slot and spend, and the loop may dispatch the same issue a second time. The steps go in this
order, and each pass may repeat them:

1. A run whose last invocation has a final verdict is over: nothing is read, signalled or written.
2. The engine process is the pid that the record names (`run_liveness.engine_process`), while that pid runs a squad engine
   and did not start after the record named it (`run_liveness.same_process`). A reused pid is never the run's. The engine
   and its descendants are the run's tree. Each process is known by its pid and its start time.
3. Each signal goes only to a process whose pid and start time still match (a fresh snapshot before each round). SIGTERM
   goes first, then SIGKILL after a bounded grace, then a bounded wait.
4. The end is verified: no process of the tree survives, and no run process (`reap.LIVE_PROC`) works inside the run folder.
   A process of the folder outside the tree is never signalled: a cwd alone proves no ownership. It holds the end.
5. The run's uncommitted work is kept (`wip_snapshot`, as the janitor takeover keeps it).
6. The killed verdict is written through `squad.killed`, the one writer, after the engine is gone, so no live engine
   rewrites the record from memory. The verdict frees the run slot (ADR-0167), and the count of the ceiling reclaims the
   dead holder's leases (`Ceiling.occupancy`).

A survivor, a read that fails, a run that named no process and moved recently, or work that could not be kept is a hold:
nothing is written, and the caller keeps the claim and tries again on a later pass.

usage: python3 -m squad.retire <run folder> <reason> [--checkout DIR]
  Prints one line per step. Exit 0 when the run is over, 1 on a hold, 2 on a usage error.
"""
from __future__ import annotations

import importlib.util
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from squad import caps as C
from squad import killed as K
from squad import procs as P
from squad import reap, record
from squad import run_liveness as RL

ENGINE_CMD = r"[s]quad (start|resume)( |$)"   # the engine of a run, as the janitor's ENGINE reads it
GRACE_S = 10.0   # the bound of each wait: after SIGTERM, and after SIGKILL
POLL_S = 0.5


class Outcome(NamedTuple):
    ended: bool
    lines: list[str]


def _tree(root: int, table) -> list[int]:
    kids: dict[int, list[int]] = {}
    for pid, p in table.items():
        kids.setdefault(p.ppid, []).append(pid)
    out, stack = [], [root]
    while stack:
        q = stack.pop()
        if q in out:
            continue
        out.append(q)
        stack.extend(kids.get(q, ()))
    return out


def _gone(p) -> bool:
    return p is None or getattr(p, "stat", "S") == "Z"


def _survivors(ids: dict[int, float | None], table) -> list[int]:
    """The pids of `ids` that still run as the same process: same pid, same start time, and not a zombie."""
    return [q for q, st in ids.items() if not _gone(table.get(q)) and table[q].start == st]


def _stop(engine: int, table, snapshot, kill, sleep, grace_s, poll_s, say) -> list[int]:
    """Signal the engine's tree, SIGTERM then SIGKILL, and return the pids that survive both."""
    ids = {q: table[q].start for q in _tree(engine, table)}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        fresh = snapshot()
        if not _gone(fresh.get(engine)) and fresh[engine].start == ids[engine]:
            for q in _tree(engine, fresh):   # a process the engine started since the first read is the run's too
                ids.setdefault(q, fresh[q].start)
        for q in sorted(_survivors(ids, fresh), key=lambda q: q != engine):
            say(f"RETIRE signal {signal.Signals(sig).name} pid={q} ({fresh[q].command[:70]})")
            try:
                kill(q, sig)
            except (ProcessLookupError, PermissionError):
                pass
        waited = 0.0
        while True:
            left = _survivors(ids, snapshot())
            if not left or waited >= grace_s:
                break
            sleep(poll_s)
            waited += poll_s
        if not left:
            return []
    return left


def retire(folder, reason: str, *, snapshot: Callable = P.snapshot, cwds: Callable | None = None, kill: Callable = os.kill,
           sleep: Callable = time.sleep, now: float | None = None, keep_work: Callable | None = None,
           ceiling=None, grace_s: float = GRACE_S, poll_s: float = POLL_S) -> Outcome:
    """End the run in `folder` for `reason`. Return (ended, lines). `snapshot`, `cwds` (a list of (pid, cwd, command)),
    `kill`, `sleep` and `keep_work` (folder, run id -> (lines, ok)) are injectable, so a test signals no real process."""
    folder = Path(folder)
    lines: list[str] = []
    say = lines.append
    hold = lambda why: Outcome(False, lines + [f"RETIRE HOLD {folder.name}: {why}"])  # noqa: E731
    rec = folder / "record.jsonl"
    try:
        text, moved = rec.read_text(), rec.stat().st_mtime
    except FileNotFoundError:
        return hold("the run has no record, so no process of it can be proven gone")
    except OSError as e:
        return hold(f"the record cannot be read: {e}")
    if C.final_verdict(text) is not None:
        return Outcome(True, [f"RETIRE OVER {folder.name}: the run already has its verdict; nothing is touched"])
    now = time.time() if now is None else now
    events = RL.invocation(text)
    run_id = next((e.get("run_id") for e in events if e.get("event") in ("run_start", "resume") and e.get("run_id")), None) or folder.name
    try:
        table = snapshot()
    except P.ReadError as e:
        return hold(f"the process table cannot be read: {e}")
    proc = RL.engine_process(events)
    if proc is None:
        if now - moved < reap.NO_PID_IDLE_S:
            return hold("the run named no process and its record moved inside the bound, so it may still work")
    else:
        pid, since = proc
        seen = table.get(pid)
        if not _gone(seen) and re.search(ENGINE_CMD, seen.command) and RL.same_process(seen.start, since):
            try:
                left = _stop(pid, table, snapshot, kill, sleep, grace_s, poll_s, say)
            except P.ReadError as e:
                return hold(f"the process table cannot be read: {e}")
            if left:
                return hold(f"pid {', '.join(map(str, left))} of the run's tree survives SIGKILL; the claim stays")
        else:
            say(f"RETIRE engine pid={pid} of {folder.name} is gone (or the pid is another process now); nothing is signalled")
    rows = cwds() if cwds is not None else reap._procs()
    if rows is None:
        return hold("the process cwds cannot be read, so no survivor can be ruled out")
    here = str(folder)
    live = [q for q, cwd, c in rows if (cwd == here or cwd.startswith(here + "/")) and reap.LIVE_PROC.search(c)]
    if live:
        return hold(f"run process pid {', '.join(map(str, live))} still works inside the run folder; it is not the engine's, so it is not signalled")
    if keep_work is not None:
        saved, ok = keep_work(folder, run_id)
        lines.extend(saved)
        if not ok:
            return hold("a tree of the run could not be saved (WIP failed)")
    try:
        wrote = K.mark_killed(folder, reason)
    except record.RecordError as e:
        return hold(f"the record is corrupt: {e}")
    if ceiling is not None:
        try:
            ceiling.occupancy()   # the owner's locked reclaim frees the dead holder's leases
        except Exception as e:  # noqa: BLE001 -- the verdict already frees the slot; a lease read is a note
            say(f"RETIRE NOTE the lease count failed: {e}")
    say(f"RETIRED {folder.name}: {reason}" + ("" if wrote else " (the record already ended)"))
    return Outcome(True, lines)


def _wip_keeper():
    """`snapshot_run` of contrib/squad-goal/scripts/wip_snapshot.py, the one writer of a run's WIP refs (#1662)."""
    path = Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts" / "wip_snapshot.py"
    spec = importlib.util.spec_from_file_location("squad_wip_snapshot", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no wip_snapshot at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return lambda folder, run_id: mod.snapshot_run(Path(folder), run_id)


def main(argv: list[str]) -> int:
    args, checkout = list(argv), None
    if "--checkout" in args:
        i = args.index("--checkout")
        checkout = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    if len(args) != 2 or not args[1]:
        print(__doc__.split("usage: ", 1)[1].splitlines()[0], file=sys.stderr)
        return 2
    try:
        keep = _wip_keeper()
    except (ImportError, OSError) as e:
        print(f"RETIRE HOLD {Path(args[0]).name}: the work keeper cannot load: {e}")
        return 1
    ceiling = None
    if checkout:
        try:
            ceiling = C.ceiling(checkout)
        except Exception:  # noqa: BLE001 -- no sound caps block: the verdict alone frees the slot
            ceiling = None
    out = retire(args[0], args[1], keep_work=keep, ceiling=ceiling)
    for line in out.lines:
        print(line)
    return 0 if out.ended else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
