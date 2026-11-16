"""Spec rules (#1962): a spec's tickets are its open sub-issues; a dev commit names a ticket by `#N` in its subject.

usage: python3 specstall.py closers <issues.json> <dev-subjects>
"""
import json
import re
import sys


def parent_of(entry):
    p = entry.get("parent")
    if isinstance(p, dict):
        p = p.get("number")
    return p if isinstance(p, int) else None


def kids_of(entries):
    kids = {}
    for e in entries:
        p = parent_of(e)
        if p is not None:
            kids.setdefault(p, []).append(e["number"])
    return kids


def landed_of(commits):
    out = {}
    for ts, subject in commits:
        for n in map(int, re.findall(r"#(\d+)\b", subject)):
            first, last = out.get(n, (ts, ts))
            out[n] = (min(first, ts), max(last, ts))
    return out


def closers(kids, open_set, landed):
    out = set()
    for spec, tickets in kids.items():
        if spec not in open_set:
            continue
        open_kids = [t for t in tickets if t in open_set]
        for t in open_kids:
            if all(u in landed for u in open_kids if u != t):
                out.add(t)
    return sorted(out)


def stalled(kids, open_set, landed, created, now, bound):
    out = {}
    for spec, tickets in kids.items():
        if spec not in open_set:
            continue
        open_kids = [t for t in tickets if t in open_set]
        if not open_kids:
            continue
        seen = [landed[t][1] for t in open_kids if t in landed]
        ref = max(seen) if seen else min(created.get(t, now) for t in open_kids)
        if now - ref >= bound:
            out[spec] = open_kids
    return out


def state(n, cols, holds, standing, marker):
    col = cols.get(n)
    if col == "Blocked":
        return "waiting on a blocker"
    if standing.get(n) == "diverged":
        return "conflict"
    if n in marker and not holds.get(n):
        return "failed run"
    if col == "Doing" and not holds.get(n):
        return "stranded"
    return f"in {col or 'no column'}"


def ages(kids, landed, now):
    out = {}
    for spec, tickets in kids.items():
        firsts = [landed[t][0] for t in tickets if t in landed]
        if firsts:
            out[spec] = now - min(firsts)
    return out


def _commits(path):
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        ts, _, subject = line.rstrip("\n").partition("\t")
        if ts.isdigit():
            rows.append((int(ts), subject))
    return rows


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "closers":
        entries = [e for e in json.load(open(sys.argv[2])) if not e.get("pull_request")]
        open_set = {e["number"] for e in entries}
        for n in closers(kids_of(entries), open_set, landed_of(_commits(sys.argv[3]))):
            print(n)
        sys.exit(0)
    print(__doc__, file=sys.stderr)
    sys.exit(2)
