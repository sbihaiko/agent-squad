"""Where a run's wall time went: suite, gate wait, model and idle, and the suites run again on one tree (#2205).

usage: python3 -m squad.run_time RUN_FOLDER

The split reads three kinds of record event. A Bash `progress` end event carries its `command_class` and `elapsed`. An
`outcome` carries an agent call's `elapsed`; a node with a `gate_start` is a gate, and its time is a suite run. A
`gate_result` ends one gate stage; its start is the node's last `gate_start`.
"""
import argparse
from pathlib import Path

from squad import record


def split(events):
    stamps = [float(e["ts"]) for e in events if isinstance(e.get("ts"), (int, float))]
    wall = max(stamps) - min(stamps) if stamps else 0.0
    gates = {e.get("node") for e in events if e.get("event") == "gate_start"}
    calls = {e.get("node") for e in events if e.get("event") == "call_start"}
    bash = {"suite": 0.0, "gate": 0.0}
    agent = stage = 0.0
    for e in events:
        if e.get("event") == "progress" and e.get("phase") == "end" and e.get("command_class") in bash:
            bash[e["command_class"]] += float(e.get("elapsed") or 0.0)
        elif e.get("event") == "outcome" and e.get("node") in gates:
            stage += float(e.get("elapsed") or 0.0)
        elif e.get("event") == "outcome" and e.get("node") in calls:
            agent += float(e.get("elapsed") or 0.0)
    suite = bash["suite"] + stage
    model = max(0.0, agent - bash["suite"] - bash["gate"])
    idle = max(0.0, wall - suite - bash["gate"] - model)
    return {"wall": wall, "suite": suite, "gate_wait": bash["gate"], "model": model, "idle": idle}


def repeats(events):
    """The gate stages that ran more than once on one tree and suite, most seconds first."""
    started, groups = {}, {}
    for e in events:
        at = (e.get("node"), e.get("entry"), e.get("item"))
        if e.get("event") == "gate_start":
            started[at] = e
        elif e.get("event") == "gate_result":
            begun = started.pop(at, None)
            seconds = 0.0
            if begun is not None:
                t0 = begun.get("started_at") if begun.get("started_at") is not None else begun.get("ts")
                seconds = max(0.0, float(e.get("ts") or 0.0) - float(t0 or 0.0))
            key = (e.get("suite"), e.get("sha"), e.get("dirt"))
            count, total = groups.get(key, (0, 0.0))
            groups[key] = (count + 1, total + seconds)
    out = [{"suite": s, "sha": sha, "dirt": dirt, "count": n, "seconds": round(sec, 3)}
           for (s, sha, dirt), (n, sec) in groups.items() if n > 1]
    return sorted(out, key=lambda r: -r["seconds"])


def report(run_dir):
    events = record.read(Path(run_dir) / "record.jsonl")
    return {"split": split(events), "repeats": repeats(events)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("run_dir")
    args = p.parse_args(argv)
    if not (Path(args.run_dir) / "record.jsonl").is_file():
        p.error(f"no record.jsonl in {args.run_dir}")
    out = report(args.run_dir)
    for name, key in (("wall", "wall"), ("suite", "suite"), ("gate wait", "gate_wait"), ("model", "model"), ("idle", "idle")):
        print(f"{name}: {out['split'][key] / 60:.1f} min")
    for r in out["repeats"]:
        print(f"repeat {r['suite']}: {r['count']} runs on tree {r['sha']}, {r['seconds']:.0f} s")


if __name__ == "__main__":
    main()
