"""The stuck-call monitor (ADR-0051 rules 3 to 6).

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
                        "note": "the deadline did not fire: investigate, do not retune the limit (ADR-0051 rule 6)" if kind == "stuck" else "process is gone and the last turn was never recorded"})
    return out


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
