"""The machine slots read (#2435): runs and gate workers, their cap, the slots in use and the holder of each slot.

A slot is one unit of the machine-wide ceiling (`squad/caps.py`): `caps.runs` live runs and `caps.gates` gate worker
slots, across every project on this machine. The read names the holder of every slot and, for a run, the issues its claim
line names. The gate-worker row divides the cap by the workers of one gate (`SG_GATE_WORKERS`, six by default as the loop
exports it, `contrib/squad-goal/scripts/env.sh`), as the loop gates that fit at once; a gate takes `min(workers, cap)`
slots, never fewer than two, so one gate always fits while the cap holds two (gate.sh, `squad/caps.py`).

The read observes the ceiling and writes nothing (ADR-0162): `caps.Ceiling.run_holders` counts the same holders with
`reap=False`, and the gate leases are read and asked about once, so a dead lease stays for the next count. Children and
reviews are file-only settings: the read names their resolved values and names no control for them. The two caps sit in
the project settings file under `caps.runs` and `caps.gates`, where a project may only lower the plugin value.

Guard: `tests/test_machine_slots.py`, `tests/test_hub_machine_slots.py`.
"""
import json
import os
import re
from pathlib import Path

from squad import caps as C
from squad import defaults as D
from squad import project_settings as P
from squad import slot_holders as SH

DEFAULT_GATE_WORKERS = 6      # env.sh `SG_GATE_WORKERS:=6`: the worker count one loop gate asks for
GATE_KIND = "gates"


def gate_workers(value=None):
    """The workers of one gate: `value`, else `SG_GATE_WORKERS`, else the loop's default. A count under one reads the
    default rather than a gate that cannot run."""
    raw = value if value is not None else os.environ.get("SG_GATE_WORKERS")
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_GATE_WORKERS
    return n if n >= 1 else DEFAULT_GATE_WORKERS


def gates_at_once(cap, per_gate):
    """The loop gates that fit in `cap` gate worker slots, one gate taking `min(per_gate, cap)` slots (gate.sh)."""
    slot = min(per_gate, cap)
    return cap // slot if slot >= 1 else 0


def _issues(checkout, name):
    """The issue numbers the run's claim line names, in the order the line gives them, or []: the one line a dispatch
    writes (`slot_holders.CLAIM`), so a number the request cites in its prose never reads as a claimed issue."""
    folder = Path(name) if os.path.isabs(name) else Path(checkout) / "runs" / name
    try:
        text = (folder / "request.txt").read_text(errors="replace")
    except OSError:
        return []
    return [int(n) for m in SH.CLAIM.finditer(text) for n in re.findall(r"\d+", m.group(1))]


def _gate_leases(folder):
    """The live gate leases, one dict per slot, read without reaping: the holders are asked about in one call for all of
    them (`caps.started_at`), and a lease whose holder is gone counts nowhere and stays for the next count."""
    path = Path(folder) / GATE_KIND
    leases = []
    for p in sorted(path.glob("*.json")) if path.is_dir() else []:
        try:
            lease = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(lease, dict):
            leases.append(lease)
    started = C.started_at([lease.get("pid", -1) for lease in leases])
    return [lease for lease in leases if C.alive(lease.get("pid", -1), lease.get("since"), started)]


def read(target=".", active_runs=None, folder=None, defaults_path=None, workers=None):
    """The slots read: `runs` and `gates`, each with its cap, its slots in use and its holders, the gate workers of one
    gate and the gates that fit at once, `children` and `reviews` by their resolved values, and `file`, the project
    settings file both caps write. `active_runs` is the loop's list; without it only live leases count. `folder` and
    `defaults_path` are the suites' seams: the leases folder and the plugin defaults.
    """
    c = C.ceiling(target, defaults_path, folder)
    runs = c.run_holders(active_runs or Path(target) / "active-runs.txt", root=target, reap=False)
    run_holders = [{"name": n, "issues": _issues(target, n)} for n in runs]
    gate_leases = _gate_leases(c.folder)
    per_gate = gate_workers(workers)
    cap = c.caps[GATE_KIND]
    return {"file": str(P.path_for(target)),
            "caps": {k: c.caps[k] for k in C.KINDS},
            "runs": {"cap": c.caps["runs"], "in_use": len(run_holders), "holders": run_holders},
            "gates": {"cap": cap, "in_use": len(gate_leases), "holders": [lease.get("holder", "?") for lease in gate_leases],
                      "workers": per_gate, "gates_at_once": gates_at_once(cap, per_gate),
                      "running": len({lease.get("holder", "?") for lease in gate_leases})},
            "children": c.caps["children"], "reviews": c.caps["reviews"]}


def plugin(path=None):
    """The plugin defaults' four caps: `{runs, children, gates, reviews}`, the ceiling a project may only lower."""
    block = (D.load(Path(path or D.PATH)) or {}).get("caps") or {}
    return {k: block.get(k) for k in C.KINDS}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Print the machine slots read as JSON.")
    ap.add_argument("--target", default=".")
    ap.add_argument("--active-runs")
    a = ap.parse_args(argv)
    print(json.dumps(read(a.target, a.active_runs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
