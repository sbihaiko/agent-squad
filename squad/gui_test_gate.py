"""Screenshot privacy gate: a capture from a step marked `sensitive` is held until a person approves it.

Nothing leaves the machine (a model call, an issue attachment) except through `egress`, which hands the sink only
captures that are unmarked or approved. An approval names the step, the capture's sha256 and the person, so it never
covers a different capture of the same step.
"""
import json
from pathlib import Path


def _approvals(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def approve(path, node, sha256, by):
    """Record that `by` looked at the capture `sha256` of step `node` and released it."""
    if not by or not by.strip():
        raise ValueError("an approval needs the name of the person who approved it")
    rows = _approvals(path)
    rows.append({"node": node, "sha256": sha256, "by": by})
    Path(path).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def is_released(result, approvals_path):
    if not result.get("held"):
        return True
    return any(a["node"] == result["id"] and a["sha256"] == result["capture"]["sha256"]
               for a in _approvals(approvals_path))


def egress(results, sink, approvals_path):
    """Pass each releasable capture's path to `sink`; return {"sent": [ids], "held": [ids]}."""
    out = {"sent": [], "held": []}
    for r in results:
        if r["capture"] is None:
            continue
        if is_released(r, approvals_path):
            sink(r["capture"]["path"])
            out["sent"].append(r["id"])
        else:
            out["held"].append(r["id"])
    return out
