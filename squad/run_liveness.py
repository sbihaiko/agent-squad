"""The one reader of a run's liveness: does the run's own engine process still run? (#1226, #1228)

A record holds one or more invocations: each `run_start` or `resume` starts one. Only the last invocation counts (ADR-0167):
a verdict in it is final, and a verdict before it is history, so a run resumed after a verdict is live again.

The run's own process is the engine pid that the last `call_start` or `gate_start` of the last invocation names
(`squad/session.py`, `squad/runners/gate.py`, `squad/runners/dispatch.py` write `os.getpid()` and `started_at`). It is the
run's while it lives as a squad run (its command matches `RUN_CMD`) and did not start after the call named it
(`same_process`): a later start is a reused pid. A run with no call yet is a launch while its record moved within the launch
window the caller gives.

`same_process` is the one pid identity rule: the hub's run list, the loop switch, the watchd drain, `squad/reap.py` and the
lease reclaim in `squad/caps.py` read it.

A read that fails is an explicit unknown: `ReadError`, never "no process" and never "an empty record". A missing file is
absence: a missing list names no run, and a missing record is a run with no event.

The drain entry point answers more than runs: a child review and a landing are work in flight too, and the stop takes
them with the group (#1711). `squad/drainwork.py` reads them, in the same pass and by the same unknown rule, and this
module imports it inside the entry point, so importing this module still does no I/O.

The module does no I/O when it is imported. Paths, the process snapshot, the time and the launch window come from the
caller. The hub imports it through its `load_squad`; `watchd.sh` runs the entry point below, so the drain never imports the
hub.

usage: python3 -m squad.run_liveness drain <active-runs.txt> <checkout> [--launch-s SECONDS] [--run-cmd REGEX]
  Writes `live` or `idle` on stdout and exits 0. A read it cannot make exits UNKNOWN_EXIT, writes the reason on stderr and
  writes nothing on stdout.

Guard: `tests/test_run_liveness.py`, `contrib/squad-goal/scripts/test-watchd-drain.sh`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple, Union

from squad import drainwork  # a review and a landing in flight are work a drain waits for too (#1711)
from squad import procs as P
from squad.procs import ReadError  # re-exported: callers catch it here

RUN_CMD = r"[s]quad start( |$)"   # a squad run's command, as heartbeat.sh reads it; SG_RUN_CMD_RE overrides it
START_SLACK = 1.0                  # `ps` gives a start in whole seconds
PS_LSTART = "%a %b %d %H:%M:%S %Y"
UNKNOWN_EXIT = 3


class Proc(NamedTuple):
    start: float | None   # the start epoch, or None where the system gave no start that parses
    cmd: str


Procs = Mapping[int, Proc]
ProcSource = Union[Procs, Callable[[], Procs]]   # noqa: UP007 -- evaluated at import: the drain may run a python3 older than 3.10


# ---------------------------------------------------------------- the rule layer: no I/O
def invocation(text: str) -> list[dict]:
    """The events of the record's last invocation: the last `run_start` or `resume` first, then every event after it; all
    the events when there is none. A line that is not a JSON object is skipped."""
    events: list[dict] = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("event") in ("run_start", "resume"):
            events = []
        events.append(ev)
    return events


def verdict(events: list[dict]) -> dict | None:
    """The last `verdict` event of an invocation, or None."""
    return next((e for e in reversed(events) if e.get("event") == "verdict"), None)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def engine_process(events: list[dict]) -> tuple[int, float | None] | None:
    """(pid, since) of the engine process the invocation last named in a `call_start` or `gate_start`, or None. `since` is the
    event's `started_at`, else its `ts`, else None. An event with no valid pid (a bool, or 1 and below) names no process."""
    for ev in reversed(events):
        if ev.get("event") in ("call_start", "gate_start"):
            pid = ev.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1:
                since = ev.get("started_at") if _number(ev.get("started_at")) else ev.get("ts") if _number(ev.get("ts")) else None
                return pid, since
    return None


def same_process(began, since) -> bool:
    """True when a live pid that started at `began` is still the process named at `since`. A process that started after it
    was named is another process that reused the pid. With no start (`began` None) or no time it was named (`since` not a
    number), the pid alone answers. `START_SLACK` absorbs the whole seconds of `ps`."""
    if began is None or not _number(since):
        return True
    return began <= since + START_SLACK


def run_pid(events: list[dict], procs: Procs, run_cmd: str = RUN_CMD, self_pid: int | None = None) -> int | None:
    """The pid of the invocation's engine process while it runs as that process, else None. `procs` is one process snapshot.
    The reader's own pid is never a run's."""
    proc = engine_process(events)
    if proc is None:
        return None
    pid, since = proc
    seen = procs.get(pid)
    if pid == self_pid or seen is None or not re.search(run_cmd, seen.cmd) or not same_process(seen.start, since):
        return None
    return pid


def run_live(events: list[dict], moved: float | None, procs: ProcSource, now: float, launch_s: float,
             run_cmd: str = RUN_CMD, self_pid: int | None = None) -> bool:
    """True while the invocation has no verdict and its engine process runs; before its first call, while the record moved
    (`moved`, its write time; None when there is no record) within `launch_s` of `now`. `procs` may be a callable: it is read
    only when a call needs it."""
    if verdict(events) is not None:
        return False
    if any(e.get("event") in ("call_start", "gate_start") for e in events):
        return run_pid(events, procs() if callable(procs) else procs, run_cmd, self_pid) is not None
    return moved is not None and now - moved < launch_s


def parse_ps(text: str) -> dict[int, Proc]:
    """The snapshot of one `ps -axwwo pid=,lstart=,command=` (LC_ALL=C). A line whose start does not parse keeps its pid with
    no start and no command. A line with no pid is skipped."""
    out: dict[int, Proc] = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) < 2 or not f[0].isdigit():
            continue
        try:
            start: float | None = time.mktime(time.strptime(" ".join(f[1:6]), PS_LSTART))
        except ValueError:
            out[int(f[0])] = Proc(None, "")
            continue
        cmd = line.strip().split(None, 6)
        out[int(f[0])] = Proc(start, cmd[6] if len(cmd) > 6 else "")
    return out


# ---------------------------------------------------------------- the read layer
def read_processes() -> dict[int, Proc]:
    """One snapshot of every process, through the one reader (squad/procs.py: libproc and sysctl, no exec, so a sandboxed
    child reads it too). A failed read raises ReadError; the listing always names this process, so an empty answer is one."""
    table = P.snapshot()
    procs = {pid: Proc(p.start, p.command) for pid, p in table.items()}
    if not procs:
        raise ReadError("cannot read the processes: no process named")
    return procs


def read_text(path) -> str:
    """The file's text; "" when it is missing. Any other failure raises ReadError."""
    try:
        return Path(path).read_text(errors="replace")
    except FileNotFoundError:
        return ""
    except OSError as e:
        raise ReadError(f"cannot read {path}: {e}") from e


def _moved(path) -> float | None:
    try:
        return Path(path).stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ReadError(f"cannot stat {path}: {e}") from e


def record_path(run: str, checkout: str) -> Path:
    """The record of an active-runs entry: an absolute run folder, or a name under `<checkout>/runs`."""
    return Path(run) / "record.jsonl" if run.startswith("/") else Path(checkout) / "runs" / run / "record.jsonl"


def run_live_at(run: str, checkout: str, procs: ProcSource, now: float, launch_s: float,
                run_cmd: str = RUN_CMD, self_pid: int | None = None) -> bool:
    """`run_live` for one active-runs entry, read from its record. Raises ReadError when the record cannot be read."""
    f = record_path(run, checkout)
    text = read_text(f)
    return run_live(invocation(text), _moved(f), procs, now, launch_s, run_cmd, self_pid)


def live_runs(active_runs, checkout: str, procs: ProcSource, now: float, launch_s: float,
              run_cmd: str = RUN_CMD, self_pid: int | None = None) -> list[str]:
    """The entries of the active-runs list whose run is live, each once, in list order. A callable `procs` is called once,
    and only when a run needs the snapshot. Raises ReadError when the list, a record or the snapshot cannot be read."""
    held: dict[str, Procs] = {}

    def snapshot() -> Procs:
        if "p" not in held:
            held["p"] = procs() if callable(procs) else procs
        return held["p"]
    rows = [r.strip() for r in read_text(active_runs).splitlines() if r.strip()]
    return [r for r in dict.fromkeys(rows) if run_live_at(r, checkout, snapshot, now, launch_s, run_cmd, self_pid)]


# ---------------------------------------------------------------- the entry point the drain calls
def main(argv: list[str]) -> int:
    usage = __doc__.split("usage: ", 1)[1].splitlines()[0]
    args: list[str] = []
    opts: dict[str, str | None] = {"--launch-s": None, "--run-cmd": None}
    i = 0
    while i < len(argv):
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
            continue
        args.append(argv[i])
        i += 1
    if len(args) != 3 or args[0] != "drain":
        print("usage: " + usage, file=sys.stderr)
        return 2
    try:
        launch_s = float(opts["--launch-s"] or 60 * float(os.environ.get("HB_DEAD_IDLE_MIN") or 1))
    except ValueError:
        print("usage: " + usage, file=sys.stderr)
        return 2
    run_cmd = opts["--run-cmd"] or os.environ.get("SG_RUN_CMD_RE") or RUN_CMD
    try:
        runs = live_runs(args[1], args[2], read_processes, time.time(), launch_s, run_cmd, os.getpid())
        other = drainwork.live_work(Path(args[1]).resolve().parent)   # a child review and a landing in flight (#1711)
    except ReadError as e:
        print(f"run_liveness: unknown: {e}", file=sys.stderr)
        return UNKNOWN_EXIT
    for name in other:
        print(f"run_liveness: {name} is in flight", file=sys.stderr)   # the drain names what it waits for
    print("live" if runs or other else "idle")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
