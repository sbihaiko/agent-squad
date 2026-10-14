"""The stuck-call monitor.

It reads run records and never calls an agent. A call is open when its `call_start` event has no later
outcome for the same node, entry and item. An open call with a live process past its limit is stuck.
An open call whose process is gone is abandoned. The monitor sees only calls that leave a record.
"""
import json
import os
import sys
import time
from pathlib import Path

from squad import record


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def open_calls(events):
    closed = {(e["node"], e["entry"], e.get("item")): e["pos"] for e in events if e["event"] == "outcome"}
    return [e for e in events if e["event"] == "call_start" and closed.get((e["node"], e["entry"], e.get("item")), 0) < e["pos"]]


def scan(path, now=None, alive=_alive):
    """Return the findings for one record: stuck, abandoned, or deadline_missed (a stuck call past its own limit)."""
    now = time.time() if now is None else now
    out = []
    for c in open_calls(record.read(path)):
        over = now - c["started_at"] - c["limit"] if c["limit"] is not None else 0     # a null limit: no deadline, only a dead process is a finding
        kind = "abandoned" if not alive(c["pid"]) else "stuck" if over > 0 else None
        if kind:
            out.append({"finding": kind, "record": str(path), "node": c["node"], "entry": c["entry"], "item": c.get("item"),
                        "session": c["session"], "over_limit_s": round(max(over, 0), 1), "at": now,
                        "note": "the deadline did not fire: investigate, do not retune the limit" if kind == "stuck" else "process is gone and the last turn was never recorded"})
    return out


DEADLINE_GRACE = 60.0    # seconds past a call's limit before its still-open call is a deadline that did not fire


NO_PROGRESS_SECONDS = 300.0    # seconds the record may stay still, with no call open, before `no_progress`


class Watcher:
    """The run-scoped monitor: the record scan on a timer, for one run folder.
    `step(now)` reads the record and writes each new finding once (`stuck`, `abandoned`, `deadline_missed`, `no_progress`, `held`, `ended`), as one JSON line, to `monitor.log` in the folder and to `out`.
    `out` is bound when the watcher is built (the output of the run that started it), so a timer that outlives its run never writes into a later stream.
    A condition that stays true is not written again. The watcher never calls a model, never writes to the record and never pauses a run.
    `step` returns False once the record holds a verdict, or after `stop()`; the timer then ends."""

    def __init__(self, folder, out=None, alive=_alive):
        self.folder, self.alive, self.out = Path(folder), alive, out or sys.stdout
        self.log = self.folder / "monitor.log"
        self.seen = set()
        self._stop = False

    def stop(self):
        self._stop = True

    def step(self, now=None):
        now = time.time() if now is None else now
        path = self.folder / "record.jsonl"
        if self._stop:
            return False
        if path.exists():
            found = scan(path, now, self.alive)
            found += [{**f, "finding": "deadline_missed", "note": "the call is still open well past its deadline: the deadline did not fire"}
                      for f in found if f["finding"] == "stuck" and f["over_limit_s"] > DEADLINE_GRACE]
            events = record.read(path)
            found += self._run_findings(events, now)
            for f in found:
                self._write(f)
            if any(e["event"] == "verdict" for e in events):
                return False
        return True

    def _run_findings(self, events, now):
        """The findings about the run as a whole: a pause (`held`), a still record (`no_progress`) and the verdict (`ended`)."""
        out, rec = [], str(self.folder / "record.jsonl")
        for e in events:
            if e["event"] == "paused":
                out.append({"finding": "held", "record": rec, "node": e["node"], "entry": None, "item": e.get("item"), "pos": e["pos"], "at": now,
                            "held": e["held"], "note": "a person paused the run at this node"})
        last = events[-1] if events else None
        if last is None:
            return out
        if last["event"] == "verdict":
            out.append({"finding": "ended", "record": rec, "node": None, "entry": None, "item": None, "at": now, "verdict": last["verdict"],
                        "exit_code": last["exit_code"], "reasons": last["reasons"], "note": "the run has a verdict; the monitor stops"})
        elif last["event"] != "paused" and not open_calls(events) and now - last["ts"] > NO_PROGRESS_SECONDS:
            out.append({"finding": "no_progress", "record": rec, "node": None, "entry": None, "item": None, "pos": last["pos"], "at": now,
                        "still_s": round(now - last["ts"], 1), "note": "the record has not grown and no call is open: the run may be stalled"})
        return out

    def _write(self, f):
        key = (f["finding"], f["node"], f["entry"], f.get("item"), f.get("pos"))
        if key in self.seen:
            return
        self.seen.add(key)
        line = json.dumps(f)
        with open(self.log, "a") as fh:
            fh.write(line + "\n")
        if not self.out.closed:    # the run that built the watcher closed its output: monitor.log keeps the line
            print(f"monitor: {line}", file=self.out, flush=True)


def run_timer(watcher, seconds, clock=time.time, wait=time.sleep):
    """Run `watcher.step` every `seconds` on a daemon thread, so the monitor ends with its parent process. Return the thread."""
    import threading

    def loop():
        while True:
            try:
                if not watcher.step(clock()):
                    return
            except Exception as e:  # noqa: BLE001 - a read that fails once (a record mid-write) is tried again; the monitor never stops a run
                print(f"squad: warning: the monitor could not read the record: {e}", file=sys.stderr)
            wait(seconds)
    t = threading.Thread(target=loop, daemon=True, name="run-monitor")
    t.start()
    return t


def sweep(root, log, now=None, alive=_alive):
    """Scan every record under root and append each finding to the log as one JSON line. Return the findings."""
    found = [f for p in sorted(Path(root).rglob("record.jsonl")) for f in scan(p, now, alive)]
    if found:
        with open(log, "a") as fh:
            fh.writelines(json.dumps(f) + "\n" for f in found)
    return found


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not 2 <= len(a) <= 3:
        print("usage: python -m squad.monitor RUNS_DIR LOG [INTERVAL_SECONDS]", file=sys.stderr)
        return 2
    root, log = a[0], a[1]
    while True:
        sweep(root, log)
        if len(a) < 3:
            return 0
        time.sleep(float(a[2]))


if __name__ == "__main__":
    sys.exit(main())
