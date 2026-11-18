"""Spec rules (#1962, #2002): a spec's tickets are its open sub-issues; a dev commit names a ticket by `#N` in its subject.

A stalled spec is a call for the owner only when the owner started it: its card sits in Todo or a later column, never in
Backlog and never with no card (docs/adr/0142-a-spec-is-never-work.md, clause 5). Only a commit that closes or fixes a
ticket is progress; a commit that only mentions `#N`, and a TDD RED commit, are not.

usage: python3 specstall.py closers <issues.json> <dev-subjects>
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the alias map sits beside this script (#2151)
from board_columns import ALIAS_COLUMNS, SHIPPED_NAME, canonical   # noqa: E402  the one map of the board's column names

# The shipped board's own axis, in the definition's order (#2002), read from the one alias map: `tests/test_spec_stall.py`
# proves it names the shipped columns. A card that wears a spelling the map holds reads as that column, and a card that
# wears a retired pre-definition name reads as the column `SHIPPED_NAME` gives it. Blocked is no column: it is the owner's
# freeze over the column the work reached, so a card wears it on no axis of its own.
COLUMNS = tuple(entry["name"] for entry in ALIAS_COLUMNS)
START = COLUMNS.index("Todo")   # the owner's go signal: a card that reached this column or any later one is started
_CLOSING = re.compile(r"(?i)\b(?:close[sd]?|fix(?:es|ed)?)\s+#(\d+)\b")   # the keywords that close an issue when a commit lands


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


def progress_of(commits):
    """The tickets a dev commit closes or fixes, each as (first, last) in time, as `landed_of` reads (#2002).
    A commit that only mentions `#N` moves no ticket, and a TDD RED commit is a test that must fail: neither is progress."""
    out = {}
    for ts, subject in commits:
        if subject.lstrip().startswith("TDD RED"):
            continue
        for n in map(int, _CLOSING.findall(subject)):
            first, last = out.get(n, (ts, ts))
            out[n] = (min(first, ts), max(last, ts))
    return out


def started(col):
    """True when the spec's card sits in Todo or a later column: the owner's move that starts a spec (ADR-0142 clause 5)."""
    col = canonical(col)
    col = SHIPPED_NAME.get(col, col)
    return col in COLUMNS and COLUMNS.index(col) >= START


def started_specs(stuck, cols):
    """The stalled specs whose card is started: a spec in Backlog, or with no card, waits for the owner and is no stall."""
    return {spec: ts for spec, ts in stuck.items() if started(cols.get(spec))}


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
