"""Pair each Jev diagnostic in a run record with the held-out outcome of the same run.

The held-out outcome is the `ac_verify` gate: it copies the held-out acceptance test into the clone and runs the suite.
A run that never reached `ac_verify` has no label and no pair. This module only reads records. It sets no threshold
and makes no claim about one.
"""
import json
from pathlib import Path

from squad import record

HELD_OUT_NODE = "ac_verify"


def pairs_of(events):
    """Return (label, verdict, rows). label is the last held-out outcome ("pass", "fail") or None. Each row is one Jev diagnostic payload."""
    label = next((e["outcome"] for e in reversed(events) if e["event"] == "outcome" and e["node"] == HELD_OUT_NODE), None)
    verdict = next((e["verdict"] for e in reversed(events) if e["event"] == "verdict"), None)
    rows = [dict(e["payload"], node="spec" if e["node"] == "spec_draft" else e["node"], entry=e["entry"]) for e in events if e["event"] == "diagnostic" and e.get("source") == "jev"]
    return label, verdict, rows


def collect(runs_dir, prefix="subject-"):
    """One record per run directory: {run, issue, held_out, verdict, jev: [payload, ...]}."""
    out = []
    for d in sorted(Path(runs_dir).glob(f"{prefix}*")):
        rec = next((p for p in (d / "record.jsonl", d / "run" / "record.jsonl") if p.is_file()), None)   # the run folder itself, else the old `run/` layout
        if rec is None:
            continue
        label, verdict, rows = pairs_of(record.read(rec))
        issue = int(d.name.split("-")[1])
        out.append({"run": d.name, "issue": issue, "held_out": label, "verdict": verdict, "jev": rows})
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(collect(sys.argv[1]), indent=1))
