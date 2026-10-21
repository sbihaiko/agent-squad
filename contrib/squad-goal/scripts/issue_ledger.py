#!/usr/bin/env python3
"""The loop's facts about one issue, in one file (#673): its attempts per run, its last failure, its stop reason, and what
each run's wave did with its child (ADR-0149).

usage: issue_ledger.py read  <key> [state|count|last_fail|held|wave]  the record as JSON, or one field of it
       issue_ledger.py count <key> [own|loop]                the count of one ledger
       issue_ledger.py fail  <key> <run> <evo> <why> [own|loop]  one failed dispatch: prints "<count> <fresh> <stopped>"
       issue_ledger.py stop  <key> <why> [own|loop]          the ledger stops: prints "- <run>: <why>" for each run counted
       issue_ledger.py wave  <key> <run> <state> <cause> [keep]  one wave event: prints recorded|duplicate|terminal|kept|floor
       issue_ledger.py rearm <key>                           the facts go, and the SQUAD-NOT-STARTABLE comment with them
       issue_ledger.py entered <owner>/<repo>                "<run>\t<n>" for each run a child of the repository entered

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

One transition function (ADR-0149): `transition` takes every event -- fail, stop, wave, rearm -- under the issue's lock, and
no other code changes the file. An event's identity is its kind and its run. A wave event (wave.sh: `entered`, `held`,
`refused`, `stuck`, `delivered`) sets the run's wave fact, `{state, cause, ts}`: the same state and cause again is a
duplicate and writes nothing; a `delivered` run is terminal, so a late event of that run changes nothing; `keep` leaves a
fact the run already has; any other event of the run replaces its fact, in the order the writers send them. An `entered`
event also sets the fact's `entered` field, and every later event of the run keeps it: a child that left its wave after the
entry still entered it. That field is the entry's one record; `entered` lists it for the wave readers (janitor, startability).

A corrupt file holds the issue (ADR-0149 rule 4): `read` gives the state `held` and names the file and the cause, every
writer but `rearm` refuses it and leaves it as it is, and the dispatch passes read `held` as a stop. It never reads as the
seed: a fallback to the old counters would hide the defect. A rearm, the owner's act, writes a fresh file over it.

A rearm keeps a floor (#692): `rearmed` holds the rearm's time and every run either ledger listed. `fail` and a wave event count no run the
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
WAVE = ("entered", "held", "refused", "stuck", "delivered")   # the wave events wave.sh sends (ADR-0149)
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


class Corrupt(ValueError):   # the issue's file does not parse as a ledger: the issue is held, never read as the seed (ADR-0149)
    pass


def load(key):
    p = path(key)
    try: rec = json.loads(p.read_text())
    except FileNotFoundError: return seed(key)
    except (ValueError, UnicodeDecodeError) as e: raise Corrupt(f"a corrupt ledger holds the issue: {p} ({e})") from None
    if not (isinstance(rec, dict) and all(isinstance(rec.get(led), dict) and isinstance(rec[led].get("runs"), list) for led in LEDGERS)
            and isinstance(rec.get("wave", {}), dict)):
        raise Corrupt(f"a corrupt ledger holds the issue: {p} (not a ledger record)")
    return rec


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


def read(key):   # a corrupt file reads held, with empty ledgers and its cause in `held`: the reader holds the issue
    try: rec = load(key)
    except Corrupt as e: return {**empty(key), "wave": {}, "state": "held", "held": str(e), "attempts": 0, "last_fail": ""}
    return {**rec, "wave": rec.get("wave", {}), "state": state(rec), "held": "", "attempts": rec["own"]["count"], "last_fail": last_fail(rec)}


def count(key, led="own"):
    return load(key)[led]["count"]


def first_line(why):
    return (str(why).splitlines() or [""])[0].replace("\t", " ")


def floor_runs(rec):
    return set((rec.get("rearmed") or {}).get("runs", []))


def _fail(rec, ev):   # -> ((count, fresh, stopped), changed): fresh 1 when this event counted the run
    L = rec[ev["led"]]; run = ev["run"]
    if run and (run in floor_runs(rec) or any(x["run"] == run for x in L["runs"])):
        return (L["count"], 0, int(bool(L["stopped"]))), False   # a run counts once, and a run from before the rearm not at all
    last = L["runs"][-1] if L["runs"] else None
    if (L["stopped"] and run) or (not L["stopped"] and last is not None and last["key"] != ev["evo"]):
        L["runs"], L["count"] = [], 0   # evolution: a new key, or a new run after the stop (ADR-0134)
    L["runs"].append({"run": run, "key": ev["evo"], "why": first_line(ev["why"])})
    L["count"] += 1; L["stopped"] = None
    return (L["count"], 1, 0), True


def _stop(rec, ev):   # -> (the runs counted, one comment line each, changed)
    L = rec[ev["led"]]
    L["stopped"] = first_line(ev["why"]) or "stopped"
    return [f"- {x['run'] or '(no run named)'}: {x['why']}" for x in L["runs"]], True


def _wave(rec, ev):   # -> (recorded|duplicate|terminal|kept|floor, changed): one wave event of one run (ADR-0149)
    run, st, cause = ev["run"], ev["state"], first_line(ev["cause"])
    if st not in WAVE: raise ValueError(f"not a wave event: {st!r} (one of {', '.join(WAVE)})")
    if not run: raise ValueError("a wave event names its run")
    if run in floor_runs(rec): return "floor", False   # a run from before the rearm counts toward nothing after it
    W = rec.setdefault("wave", {}); old = W.get(run)
    if old and old.get("state") == "delivered": return "terminal", False
    if old and ev.get("keep"): return "kept", False
    if old and old.get("state") == st and old.get("cause") == cause: return "duplicate", False
    W[run] = {"state": st, "cause": cause, "ts": time.time()}
    if st == "entered": W[run]["entered"] = cause
    elif old and old.get("entered"): W[run]["entered"] = old["entered"]   # the entry stays a fact of the run
    return "recorded", True


def _rearm(rec, ev):   # the loop facts go, and the floor (#692) keeps every run any of them named
    old = ev["old"] or {}
    runs = {x["run"] for led in LEDGERS for x in (old.get(led) or {}).get("runs", []) if isinstance(x, dict) and x.get("run")}
    runs |= set((old.get("wave") or {}).keys()) | set((old.get("rearmed") or {}).get("runs", []))
    rec.clear(); rec.update(empty(ev["key"]))
    rec["rearmed"] = {"ts": time.time(), "runs": sorted(runs)}   # a corrupt file has no runs to read: the time is the floor
    return None, True


RULES = {"fail": _fail, "stop": _stop, "wave": _wave, "rearm": _rearm}


def transition(key, ev):   # the one transition function (ADR-0149): every event of every writer, under the issue's lock
    with locked(key):
        try: rec = load(key); ev = {**ev, "old": rec if ev["op"] == "rearm" else None}
        except Corrupt:
            if ev["op"] != "rearm": raise   # a writer never overwrites a corrupt file: the issue stays held, the file stays for the owner
            rec = empty(key); ev = {**ev, "old": None}
        out, changed = RULES[ev["op"]](rec, {**ev, "key": key})
        if changed: save(key, rec)
        return out


def fail(key, run, evo, why, led="own"):   # -> (count, fresh, stopped)
    return transition(key, {"op": "fail", "run": run, "evo": evo, "why": why, "led": led})


def stop(key, why, led="own"):
    return transition(key, {"op": "stop", "why": why, "led": led})


def wave(key, run, st, cause, keep=False):
    return transition(key, {"op": "wave", "run": run, "state": st, "cause": cause, "keep": keep})


def rearm(key):   # the SQUAD-NOT-STARTABLE comment goes through the comment queue (cqflush.sh unstart~)
    o, r, n = parse(key)
    transition(key, {"op": "rearm"})
    q = state_dir() / "cq"; q.mkdir(parents=True, exist_ok=True)
    name = f"{o}__{r}#{n}-unstart~rearm{os.getpid()}.md"
    (q / (name + ".tmp")).write_text("SQUAD-NOT-STARTABLE:\n"); os.replace(q / (name + ".tmp"), q / name)
    return q / name


def entered(repo):   # -> [(run, n)]: each run a child of the repository entered, from every ledger file of it (ADR-0149)
    o, r, _ = parse(f"{repo}#0"); out = []
    for p in sorted(state_dir().joinpath("state").glob(f"{o}__{r}__*.json")):
        n = p.name[len(f"{o}__{r}__"):-len(".json")]
        if not n.isdigit(): continue
        try: rec = load(f"{repo}#{n}")
        except Corrupt: continue   # a corrupt file holds its issue at dispatch already; it names no entry
        out += [(run, n) for run, fact in sorted((rec.get("wave") or {}).items()) if isinstance(fact, dict) and fact.get("entered")]
    return out


def main(argv):
    if len(argv) < 2: print(__doc__.split("\n\n")[0], file=sys.stderr); return 2
    op, key, rest = argv[0], argv[1], argv[2:]
    try:
        if op == "entered":
            for run, n in entered(key): print(f"{run}\t{n}")
            return 0
        parse(key)
        if op == "read":
            d = read(key)
            v = d if not rest else d[rest[0]]
            print(v if isinstance(v, (str, int)) else json.dumps(v, sort_keys=True))
        elif op == "count": print(count(key, *(rest[:1] or ["own"])))
        elif op == "fail" and len(rest) >= 3: print(*fail(key, rest[0], rest[1], rest[2], *(rest[3:4] or ["own"])))
        elif op == "stop" and rest:
            for line in stop(key, rest[0], *(rest[1:2] or ["own"])): print(line)
        elif op == "wave" and len(rest) >= 3: print(wave(key, rest[0], rest[1], rest[2], rest[3:4] == ["keep"]))
        elif op == "rearm": print(f"REARM {key}: the loop facts are cleared; {rearm(key).name} removes the SQUAD-NOT-STARTABLE comment")
        else: print(f"issue_ledger.py: bad call: {' '.join(argv)}", file=sys.stderr); return 2
    except Corrupt as e:
        print(f"issue_ledger.py: held: {e}", file=sys.stderr); return 3
    except (ValueError, KeyError) as e:
        print(f"issue_ledger.py: {e}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
