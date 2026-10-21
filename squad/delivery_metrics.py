"""The loop's delivery metrics, read from its own local records (issue #705).

Five numbers, each read from the record its writer keeps and never from a second source:

- **dispatched work that landed nothing**: one attempt is one run that dispatched one issue. The run
  landed when its ledger holds a `landed` fact of kind `run`, or its wave fact says `delivered`
  (the work reached the base) or `owed` (the wave landed and only the close is owed). The attempt
  landed nothing when its wave fact says `refused`, `stuck` or `held`, or the issue's own or loop
  ledger counts the run as a failed dispatch. Any other fact is an attempt that is still open, and
  an open attempt is neither a landing nor a failure.
- **unique attempts per issue**: every run the issue's ledger names, each once, whether the run
  counts a failure, carries a wave fact or wrote the landing.
- **no-op waves**: a run cut a wave (`auto/waved-<run>` stays for good) and the wave landed empty
  (`auto/wave-<run>` is gone, no landing ledger row names its branch, and no wave fact of the run
  says `stuck`, which is a landing that failed rather than a wave that carried nothing). Kept apart
  from a failed dispatch: a no-op wave is a fact about the wave, never about an issue.
- **adapter failures by class**: the counts `squad/adapter_classes.py` reads from the run folders
  that still exist (#698), used here and never re-derived.
- **repeated review findings**: each fix pass keeps `<prefix>-same-<key>` beside its fingerprint
  (revformat.py, ADR-0134), holding the rounds that left the findings as they were. A marker of 0
  is a changed fingerprint, so no repeat is left to count.
- **landings**: the rows of the landing ledger (`landed.tsv`), one per issue a landing pass put in
  the base, with the branch it came on.

This module is read-only: it opens every record for reading, writes none, takes no lock and starts
nothing. A record it cannot read is counted as unreadable and skipped, so a corrupt or half-written
file never ends the scan and never becomes a guess. It is stdlib only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from squad import adapter_classes

NO_LANDING = ("refused", "stuck", "held")   # a wave fact that says the run's child did not land
LANDED = ("delivered", "owed")              # a wave fact that says the work reached the base
STALL_PREFIXES = ("branchfix", "runfix")    # the fix passes that keep <prefix>-same-<key>
LEDGER_NAME = re.compile(r"(.+?)__(.+?)__([0-9]+)\.json")


def _int(text):
    """The integer one record field spells, else None: a malformed field is absent, never a zero."""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def landed_rows(path):
    """The rows of the landing ledger, `epoch TAB branch TAB sha TAB issue`. A malformed row is skipped."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        ts, sha, issue = _int(fields[0]), fields[2].strip(), _int(fields[3])
        if ts is None or issue is None or not fields[1].strip():
            continue
        rows.append({"ts": ts, "branch": fields[1].strip(), "sha": sha, "issue": issue})
    return rows


def issue_key(path, rec):
    """The issue's address seam key, `<owner>/<repo>#<n>`: the one the record carries, else its file name."""
    key = rec.get("key")
    if isinstance(key, str) and key:
        return key
    m = LEDGER_NAME.fullmatch(Path(path).name)
    return f"{m.group(1)}/{m.group(2)}#{m.group(3)}" if m else Path(path).stem


def read_ledgers(state):
    """Every issue ledger of the state directory, as (key, record), and the count the reader could not parse.

    A corrupt file is skipped and counted, never read as an empty ledger: the count says the scan is
    incomplete, and no issue is invented from it.
    """
    out, unreadable = [], 0
    for path in sorted((Path(state) / "state").glob("*.json")):
        try:
            rec = json.loads(path.read_text(errors="replace"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(rec, dict):
            unreadable += 1
            continue
        out.append((issue_key(path, rec), rec))
    return out, unreadable


def attempts_of(rec):
    """One entry per run the issue's ledger names: its run, the wave fact's state, and its outcome.

    The outcome is `landed`, `landed_nothing` or `open`, read from the rules in the module docstring.
    """
    runs, failed = set(), set()
    for led in ("own", "loop"):
        side = rec.get(led) if isinstance(rec.get(led), dict) else {}
        for entry in side.get("runs") or []:
            if isinstance(entry, dict) and isinstance(entry.get("run"), str) and entry["run"]:
                runs.add(entry["run"])
                failed.add(entry["run"])
    wave = rec.get("wave") if isinstance(rec.get("wave"), dict) else {}
    landed = rec.get("landed") if isinstance(rec.get("landed"), dict) else {}
    landed_runs = {ref for ref, fact in landed.items() if isinstance(fact, dict) and fact.get("kind") == "run"}
    runs |= {run for run in wave if isinstance(run, str) and run}
    runs |= landed_runs
    out = []
    for run in sorted(runs):
        fact = wave.get(run) if isinstance(wave.get(run), dict) else {}
        state = fact.get("state") if isinstance(fact.get("state"), str) else ""
        if run in landed_runs or state in LANDED:
            outcome = "landed"
        elif state in NO_LANDING or run in failed:
            outcome = "landed_nothing"
        else:
            outcome = "open"
        out.append({"run": run, "state": state, "outcome": outcome})
    return out


def noop_waves(state, landed_branches, stuck_runs=()):
    """The runs whose wave landed empty, in name order: cut (`waved-<run>`), over (`wave-<run>` gone), and
    named by no landing row and no `stuck` fact of its own."""
    auto = Path(state) / "auto"
    out = []
    try:
        names = sorted(p.name for p in auto.iterdir())
    except OSError:
        return out
    for name in names:
        if not name.startswith("waved-"):
            continue
        run = name[len("waved-"):]
        if not run or (auto / f"wave-{run}").exists() or run in stuck_runs:
            continue
        try:
            branch = (auto / name).read_text(errors="replace").strip() or f"wave/{run}"
        except OSError:
            branch = f"wave/{run}"
        if branch in landed_branches:
            continue
        out.append(run)
    return out


def repeated_findings(auto):
    """Each fix pass whose last round left the same findings again: (key, prefix, rounds). A marker that
    carries no count is no count, and a count of 0 is a changed fingerprint: neither is a repeat."""
    auto = Path(auto)
    try:
        names = sorted(p.name for p in auto.iterdir())
    except OSError:
        return []
    out = []
    for name in names:
        for prefix in STALL_PREFIXES:
            head = f"{prefix}-same-"
            if not name.startswith(head):
                continue
            try:
                rounds = int((auto / name).read_text(errors="replace").strip())
            except (OSError, ValueError):
                continue
            if rounds >= 1:
                out.append({"key": name[len(head):], "prefix": prefix, "rounds": rounds})
    out.sort(key=lambda row: (-row["rounds"], row["key"]))
    return out


def landings(rows):
    """The landing count, the branches it came on, and the issues it delivered, each once."""
    return {"count": len(rows), "branches": len({r["branch"] for r in rows}), "issues": len({r["issue"] for r in rows})}


def compute(state, run_folders=(), reg=None):
    """The delivery metrics over the state directory `state` and the run folders handed in, as one dict.

    The state directory holds the loop's own records: `state/*.json` (the issue ledgers), `landed.tsv`
    (the landing ledger) and `auto/` (the markers). `run_folders` are the run folders that still exist;
    their adapter failures are `adapter_classes.aggregate`'s reading, never a second classifier.
    """
    state = Path(state)
    folders = [Path(f) for f in run_folders]
    rows = landed_rows(state / "landed.tsv")
    records, unreadable = read_ledgers(state)
    per_issue, total, by_state, stuck_runs = [], {"attempts": 0, "landed": 0, "landed_nothing": 0, "open": 0}, {}, set()
    for key, rec in records:
        wave = rec.get("wave") if isinstance(rec.get("wave"), dict) else {}
        for run, fact in wave.items():
            if isinstance(fact, dict) and fact.get("state") == "stuck":
                stuck_runs.add(run)
        attempts = attempts_of(rec)
        row = {"issue": key, "attempts": len(attempts), "landed": 0, "landed_nothing": 0, "open": 0}
        for attempt in attempts:
            row[attempt["outcome"]] += 1
            total["attempts"] += 1
            total[attempt["outcome"]] += 1
            if attempt["outcome"] == "landed_nothing":
                label = attempt["state"] if attempt["state"] in NO_LANDING else "failed"
                by_state[label] = by_state.get(label, 0) + 1
        per_issue.append(row)
    per_issue.sort(key=lambda row: (-row["attempts"], row["issue"]))
    total["by_state"] = {k: by_state[k] for k in ("refused", "held", "stuck", "failed") if by_state.get(k)}
    noop = noop_waves(state, {r["branch"] for r in rows}, stuck_runs)
    classes = adapter_classes.aggregate(folders, reg)
    loops = repeated_findings(state / "auto")
    return {
        "sources": {"ledgers": len(records), "unreadable": unreadable, "landed_rows": len(rows), "run_folders": len(folders)},
        "dispatched": total,
        "attempts": {"issues": len(per_issue), "per_issue": per_issue},
        "noop_waves": {"count": len(noop), "runs": noop},
        "adapter": {"classes": classes, "count": sum(row["count"] for row in classes), "runs": len(folders), "err": None},
        "review": {"repeated": sum(loop["rounds"] for loop in loops), "loops": loops},
        "landings": landings(rows),
        "err": None,
    }
