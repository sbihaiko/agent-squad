#!/usr/bin/env python3
"""The loop's facts about one issue, in one file (#673): its attempts per run, its last failure, its stop reason.

usage: issue_ledger.py read  <key> [state|count|last_fail]   the record as JSON, or one field of it
       issue_ledger.py count <key> [own|loop]                the count of one ledger
       issue_ledger.py fail  <key> <run> <evo> <why> [own|loop]  one failed dispatch: prints "<count> <fresh> <stopped>"
       issue_ledger.py stop  <key> <why> [own|loop]          the ledger stops: prints "- <run>: <why>" for each run counted
       issue_ledger.py rearm <key>                           the facts go, and the SQUAD-NOT-STARTABLE comment with them

This module is the only writer of $SG_STATE/state/<owner>__<repo>__<n>.json. The key is <owner>/<repo>#<n>, the address
seam's (ADR-0138), never a bare issue number. Owner intent stays on GitHub (ready-for-agent, the Todo card, frozen:owner,
loop-bug); this file holds only loop facts, so a wipe of the state folder is no loss: a missing file reads as todo with 0
attempts. Each write is a temp file, fsync, then a rename (ADR-0030), under an fcntl lock per issue, so two writers at once
lose no count and a kill in a write leaves the old file whole.

Two ledgers, as wave.sh kept them: `own` counts the failures of the issue's work (SG_REQUEUE_CAP), `loop` the requeues the
loop itself caused (#621, SG_LOOP_REQUEUE_CAP). `fail` keeps wave.sh's count rule (#609, ADR-0134): one run counts once;
evolution starts the count again at one: a new evolution key (the newest non-RED `Refs #N` commit in the base), or a new run
after the stop, which only the owner lets start. A runless failure after a stop counts on and clears the stop, as the old
ledger's appended line did.

A rearm keeps a floor (#692): `rearmed` holds the rearm's time and every run either ledger listed. `fail` counts no run the
floor names, and the janitor counts no run whose verdict is older than the floor's time, so the old runs never stop the issue
again. The run set covers a record with no time; the time covers a run no ledger listed.

Seed: a missing file of the profile's primary repository (SG_REPO) reads the old auto/requeues-<n> and auto/loopqueues-<n>
counters and their .runs ledgers, so a live cutover needs no halt. The counter wins over the line count: a counter the owner
reset holds (#673: requeues-543 read 1 while its ledger held 2 lines). The first write makes the file, and the old files are
read no more.
"""
import contextlib
import fcntl
import json
import os
import pathlib
import re
import sys
import tempfile
import time

LEDGERS = ("own", "loop")
OLD = {"own": "requeues", "loop": "loopqueues"}   # the old marker names wave.sh wrote in auto/ before #673
KEY = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([0-9]+)")


def parse(key):   # <owner>/<repo>#<n> -> (owner, repo, n); anything else is refused (ADR-0138: never a bare number)
    m = KEY.fullmatch(key or "")
    if not m: raise ValueError(f"not an issue key <owner>/<repo>#<n>: {key!r}")
    return m.group(1), m.group(2), m.group(3)


def state_dir():
    return pathlib.Path(os.environ["SG_STATE"])


def path(key):
    o, r, n = parse(key)
    return state_dir() / "state" / f"{o}__{r}__{n}.json"


def empty(key):
    return {"key": key, **{led: {"count": 0, "runs": [], "stopped": None} for led in LEDGERS}}


def seed(key):   # the old auto/ files of the primary repository, read once while the file is missing
    rec = empty(key); o, r, n = parse(key)
    if os.environ.get("SG_REPO", f"{o}/{r}") != f"{o}/{r}": return rec   # the old files had no repository: they are SG_REPO's
    a = state_dir() / "auto"
    for led, name in OLD.items():
        try: lines = (a / f"{name}-{n}.runs").read_text().splitlines()
        except OSError: lines = []
        lines = [x for x in lines if x]
        runs = [dict(zip(("run", "key", "why"), (x.split("\t", 2) + ["", ""])[:3])) for x in lines if x != "frozen"]
        try: c = (a / f"{name}-{n}").read_text().strip()
        except OSError: c = ""
        L = rec[led]; L["runs"] = runs; L["count"] = int(c) if c.isdigit() else len(runs)
        if lines and lines[-1] == "frozen": L["stopped"] = "frozen before the ledger file (#673)"
    return rec


def load(key):
    p = path(key)
    try: return json.loads(p.read_text())
    except FileNotFoundError: return seed(key)
    except ValueError: return seed(key)   # no writer leaves a torn file (ADR-0030); a hand edit that broke it reads as the seed


def save(key, rec):   # temp file, fsync, rename, then the directory's fsync: a kill at any point leaves the old file whole
    p = path(key); p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f, indent=1, sort_keys=True); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise
    d = os.open(str(p.parent), os.O_RDONLY)
    try: os.fsync(d)
    finally: os.close(d)


@contextlib.contextmanager
def locked(key):   # one lock per issue: a writer holds it from its read to its rename; the kernel frees it when a writer dies
    p = path(key); p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p) + ".lock", "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def state(rec):
    return "frozen" if rec["own"]["stopped"] else "needs-human" if rec["loop"]["stopped"] else "todo"


def last_fail(rec):   # the reason of the newest own failure, the one a redispatch must repair (#670)
    runs = rec["own"]["runs"]
    return runs[-1]["why"] if runs else ""


def read(key):
    rec = load(key)
    return {**rec, "state": state(rec), "attempts": rec["own"]["count"], "last_fail": last_fail(rec)}


def count(key, led="own"):
    return load(key)[led]["count"]


def fail(key, run, evo, why, led="own"):   # -> (count, fresh, stopped): fresh 1 when this call counted the run
    with locked(key):
        rec = load(key); L = rec[led]
        if run and (run in (rec.get("rearmed") or {}).get("runs", []) or any(x["run"] == run for x in L["runs"])):
            return L["count"], 0, int(bool(L["stopped"]))   # a run counts once, and a run from before the rearm not at all
        last = L["runs"][-1] if L["runs"] else None
        if (L["stopped"] and run) or (not L["stopped"] and last is not None and last["key"] != evo):
            L["runs"], L["count"] = [], 0   # evolution: a new key, or a new run after the stop (ADR-0134)
        L["runs"].append({"run": run, "key": evo, "why": (why.splitlines() or [""])[0].replace("\t", " ")})
        L["count"] += 1; L["stopped"] = None
        save(key, rec)
        return L["count"], 1, 0


def stop(key, why, led="own"):   # -> the runs counted, one comment line each
    with locked(key):
        rec = load(key); L = rec[led]
        L["stopped"] = (why.splitlines() or [""])[0] or "stopped"
        save(key, rec)
        return [f"- {x['run'] or '(no run named)'}: {x['why']}" for x in L["runs"]]


def rearm(key):   # the loop facts go; the SQUAD-NOT-STARTABLE comment goes through the comment queue (cqflush.sh unstart~)
    o, r, n = parse(key)
    with locked(key):
        old = load(key); rec = empty(key)
        runs = {x["run"] for led in LEDGERS for x in old[led]["runs"] if x["run"]} | set((old.get("rearmed") or {}).get("runs", []))
        rec["rearmed"] = {"ts": time.time(), "runs": sorted(runs)}   # the floor (#692): no run from before it counts again
        save(key, rec)
    q = state_dir() / "cq"; q.mkdir(parents=True, exist_ok=True)
    name = f"{o}__{r}#{n}-unstart~rearm{os.getpid()}.md"
    (q / (name + ".tmp")).write_text("SQUAD-NOT-STARTABLE:\n"); os.replace(q / (name + ".tmp"), q / name)
    return q / name


def main(argv):
    if len(argv) < 2: print(__doc__.split("\n\n")[0], file=sys.stderr); return 2
    op, key, rest = argv[0], argv[1], argv[2:]
    try:
        parse(key)
        if op == "read":
            d = read(key)
            print(json.dumps(d, sort_keys=True) if not rest else d[rest[0]])
        elif op == "count": print(count(key, *(rest[:1] or ["own"])))
        elif op == "fail" and len(rest) >= 3: print(*fail(key, rest[0], rest[1], rest[2], *(rest[3:4] or ["own"])))
        elif op == "stop" and rest:
            for line in stop(key, rest[0], *(rest[1:2] or ["own"])): print(line)
        elif op == "rearm": print(f"REARM {key}: the loop facts are cleared; {rearm(key).name} removes the SQUAD-NOT-STARTABLE comment")
        else: print(f"issue_ledger.py: bad call: {' '.join(argv)}", file=sys.stderr); return 2
    except (ValueError, KeyError) as e:
        print(f"issue_ledger.py: {e}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
