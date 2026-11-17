"""A router's Bash time, split by command class (#1991).

`classify` names one Bash command: gate, suite, git, gh or other. The adapter writes the class and the elapsed seconds of
each Bash call into the record's progress events. `report` sums those end events over a window, and reads the child-gate
history for the wait a gate call spent for a caps.gates slot. A run's record never holds that wait, so the wait is a
machine-wide figure, read apart from the call time it sits in.

usage: python3 -m squad.bash_time --runs-dir DIR [--history FILE] [--hours 24] [--node-prefix route.router]

`DIR` is any folder a record sits at or below: one run folder, a `runs` folder, or the parent of the gui trees.
"""
import argparse
import json
import re
import time
from pathlib import Path

CLASSES = ("gate", "suite", "git", "gh", "other")
GATE = re.compile(r"(?:^|[\s/])(?:child-gate|fast-gate)\.sh\b")
SUITE = re.compile(r"\bpytest\b|\btest-[\w.-]+\.sh\b|shell-suites\.sh")
SKIP_HEAD = {"cd", "export", "set", "source", "."}
SPLIT = re.compile(r"&&|\|\||;|\|")


def _head(segment):
    words = segment.split()
    while words and ("=" in words[0] or words[0] == "rtk"):
        words = words[1:]
    return words[0] if words else None


def classify(command):
    """The class of one Bash command. A git or gh first word decides first; then a gate, then a suite; else other."""
    if not isinstance(command, str):
        return "other"
    for segment in SPLIT.split(command):
        head = _head(segment)
        if head is None or head in SKIP_HEAD:
            continue
        if head in ("git", "gh"):
            return head
        break
    if GATE.search(command):
        return "gate"
    if SUITE.search(command):
        return "suite"
    return "other"


def _records(runs_dir):
    """Every record at or below one root: a run folder, a `runs` folder of runs, or the parent the loop writes below."""
    return sorted(Path(runs_dir).rglob("record.jsonl"))


def _progress_ends(runs_dir):
    for path in _records(runs_dir):
        for line in path.read_text().splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "progress" and ev.get("phase") == "end":
                yield ev


def _history(path, since, until):
    """The child-gate history rows finished in the window: (runs, wait seconds, run seconds)."""
    runs = wait = run = 0
    for line in Path(path).read_text().splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        fin, start, waited = (float(c) for c in cols[:3])
        if not since <= fin <= until:
            continue
        runs, wait, run = runs + 1, wait + waited, run + (fin - start)
    return {"runs": runs, "wait_seconds": wait, "run_seconds": run}


def report(runs_dir, now=None, hours=24.0, node_prefix="route.router", history=None):
    """Bash seconds per class over the window, and the child-gate wait when a history file is given."""
    until = time.time() if now is None else now
    since = until - hours * 3600
    seconds = {c: 0.0 for c in CLASSES}
    calls = {c: 0 for c in CLASSES}
    for ev in _progress_ends(runs_dir):
        if not str(ev.get("node", "")).startswith(node_prefix) or not since <= ev.get("ts", 0) <= until:
            continue
        cls = ev.get("command_class")
        if cls not in seconds:
            continue
        seconds[cls] += float(ev.get("elapsed") or 0.0)
        calls[cls] += 1
    seconds = {c: v for c, v in seconds.items() if calls[c]}
    calls = {c: n for c, n in calls.items() if n}
    out = {"seconds": seconds, "calls": calls, "total_seconds": sum(seconds.values()),
           "largest": max(seconds, key=seconds.get) if seconds else None}
    if history is not None:
        out["gate_wait"] = _history(history, since, until)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs-dir", required=True)
    p.add_argument("--history")
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--node-prefix", default="route.router")
    args = p.parse_args(argv)
    out = report(args.runs_dir, hours=args.hours, node_prefix=args.node_prefix, history=args.history)
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
