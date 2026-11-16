#!/usr/bin/env python3
"""The loop's facts about one issue, in one file (#673): its attempts per run, its last failure, its stop reason, and what
each run's wave did with its child (ADR-0149).

usage: issue_ledger.py read  <key> [state|count|last_fail|held|wave]  the record as JSON, or one field of it
       issue_ledger.py count <key> [own|loop]                the count of one ledger
       issue_ledger.py fail  <key> <run> <evo> <why> [own|loop]  one failed dispatch: prints "<count> <fresh> <stopped>"
       issue_ledger.py stop  <key> <why> [own|loop]          the ledger stops: prints "- <run>: <why>" for each run counted
       issue_ledger.py wave  <key> <run> <state> <cause> [keep]  one wave event: prints recorded|duplicate|observed|opened|closed|stale|terminal|kept|floor
       issue_ledger.py rearm <key>                           the facts go, and the SQUAD-NOT-STARTABLE comment with them
       issue_ledger.py replay <key>                          the state rebuilt from the ledger's events, against the stored state and the old counters
       issue_ledger.py migrate <key>                         the old auto/ counters of a missing ledger become its file, once
       issue_ledger.py entered <owner>/<repo>                "<run>\t<n>" for each run a child of the repository entered
       issue_ledger.py landtry <key> <run> <cause>           one failed landing of the run's wave: prints the run's tries
       issue_ledger.py owe   <key> <run> <wave> <sha>        the landed wave could not close the issue: it is owed
       issue_ledger.py owed  <owner>/<repo> <run>            "<sha>\t<wave>\t<n>" for each issue the run still owes
       issue_ledger.py landed <key> <ref> <run|branch> <sha>  the landing pass put the issue's work in the base: prints recorded|duplicate|floor
       issue_ledger.py landed <owner>/<repo>                 "<ref>\t<n>" for each issue a landing pass put in the base
       issue_ledger.py requeue <key> <run> <head>            the run requeued the issue's child on this head: prints recorded|duplicate|floor
       issue_ledger.py requeued <key> <run>                  the head the run requeued the issue on, "" when it did not
       issue_ledger.py review <key> <run> <verdict> <findings> [round]  one review of the issue's work: prints recorded|reset|duplicate|frozen|floor
       issue_ledger.py coder <key>                           the places down the coder chain the next attempt starts
       issue_ledger.py board <key> <run> <column> <req> [instant] [writer]  one board write: prints recorded|duplicate|stale|floor
       issue_ledger.py boardw <key> <run> <column> [actor]   that write landed, at this instant, by this actor: prints written|duplicate|superseded
       issue_ledger.py boardx <key> <run> <column>           the pending write is dropped unwritten: prints dropped|superseded
       issue_ledger.py pending <key|owner/repo>              "<n>\t<run>\t<column>\t<instant>\t<req>\t<writer>" for each board write not yet landed

This module is the only writer of $SG_STATE/state/<owner>__<repo>__<n>.json. The key is <owner>/<repo>#<n>, the address
seam's (ADR-0138), never a bare issue number. Owner intent stays on GitHub (ready-for-agent, the Todo card, frozen:owner,
loop-bug); this file holds only loop facts. A missing file of a new issue reads as todo with 0 attempts. Each write is a temp file, fsync, then a rename (ADR-0030), under an fcntl lock per issue, so two writers at once
lose no count and a kill in a write leaves the old file whole.

Two ledgers, as wave.sh kept them: `own` counts the failures of the issue's work (SG_REQUEUE_CAP), `loop` the requeues the
loop itself caused (#621, SG_LOOP_REQUEUE_CAP). `fail` keeps wave.sh's count rule (#609, ADR-0134): one run counts once;
evolution starts the count again at one: a new evolution key (the newest non-RED `Refs #N` commit in the base), or a new run
after the stop, which only the owner lets start. A runless failure after a stop counts on and clears the stop, as the old
ledger's appended line did.

One transition function (ADR-0149): `transition` takes every event -- fail, stop, wave, rearm -- under the issue's lock, and
no other code changes the file. An event's identity is its kind and its run. A wave event (wave.sh: `entered`, `held`,
`refused`, `stuck`, `delivered`) sets the run's wave fact, `{state, cause, ts}`: the same state and cause again is a
duplicate and writes nothing (a held or stuck one raises its episode's count, see below); a `delivered` run is terminal, so a late event of that run changes nothing; `keep` leaves a
fact the run already has; any other event of the run replaces its fact, in the order the writers send them. An `entered`
event also sets the fact's `entered` field, and every later event of the run keeps it: a child that left its wave after the
entry still entered it. That field is the entry's one record; `entered` lists it for the wave readers (janitor, startability).
A `landtry` event is a failed landing of the run's wave, on a cause a repeat does not change: it sets the fact `stuck` and
adds one to its `tries`, the same cause too; any other event of the run keeps the tries (wave.sh stops at SG_WAVE_LAND_TRIES). An `owe` event
sets the fact `owed`, with the wave and the sha that landed, for an issue the landed wave could not close; `owed` lists them
for the run, and the `delivered` event of the close ends it.

A hold is one episode (#1167): a `held` or `stuck` wave event of a run opens its episode, `{run, cause, start, count}`, and a
repeat of the same cause counts one more observation of it -- the file changes in the count and nothing else. A different
cause or any other state of the run closes the episode, and `rearm` closes it and keeps it, so a later hold opens a new one.
A keyed writer passes the episode's `start`; a key that is not the open episode's is `stale` and writes nothing. A writer that
may not wait for the lock (`wait` false) is `locked`. `read <key> episodes` gives each episode with its age.
`open_episodes` reads every issue's open episodes in one pass over the state directory, for a reader that needs the whole
queue and never one read per child, and keeps that pass until one of the ledger files changes (#1746).

A `landed` fact is the landing pass's own record (`wave.sh` for a wave, `branchland.sh` for a pushed work branch): the issue's
work is in the base. It is keyed by the run that landed it (`kind` run) or by the branch a branchland pass landed (`kind`
branch), with the branch and the sha. Rule R reads it instead of a run's land heads (#691, #695): a landing is delivered work
only when a landing pass wrote the fact. A `requeue` fact holds the head a run requeued the issue's child on, so the pass
that requeued it is not tried again on the same head (the old auto/requeued-<run>-<child> marker, ADR-0149 rule 6).

A `board` fact is one card write of a writer (ADR-0164): `{run, column, instant, state, req, by}`. Its identity is the run and
the column, and its instant is the event's own time, set once when the event is first recorded; `by` is the writer's name, so
a reader knows whose fact it reads. Every writer of a card -- the autobug dispatch, the janitor's reconcile and
`reconcile.py` -- records its write through `board`, so one path holds every prediction of the board's next read (#1841).
`board` records the event `pending`
before the card write; `boardw` marks it `written` when the write lands, and `boardx` drops it unwritten when the gates no
longer back it. A retry of the same event is a duplicate and keeps its first instant and its state; an event older than the
fact is `stale`, so a late delivery never overwrites a newer one; a write for an event the fact no longer holds is
`superseded`. `pending` lists what has not landed, so a dispatch that crashed between the event and its write finds the event
and resumes it with the stored request.

A `review` event is one review of the issue's work, keyed by its run and round (ADR-0151, ADR-0149 rule 1): a retry of one
round is no second verdict. The streak holds the consecutive `changes` verdicts, the `moves` identical findings twice made
down the coder chain, the last finding, and the cause when the streak ended the issue. Three consecutive `changes`
verdicts set `stopped` and the issue reads needs-human, so its dispatch is held for the owner (ADR-0029). An `approve`
resets the count and the moves, and lifts no stop the owner has not lifted; a new finding starts the moves again at zero.
`coder` prints the moves, the places down the chain the next fix attempt starts (ADR-0151 rule 2).

A corrupt file holds the issue (ADR-0149 rule 4): `read` gives the state `held` and names the file and the cause, every
writer but `rearm` refuses it and leaves it as it is, and the dispatch passes read `held` as a stop. It never reads as the
seed: a fallback to the old counters would hide the defect. A rearm, the owner's act, writes a fresh file over it.

A rearm keeps a floor (#692): `rearmed` holds the rearm's time and every run either ledger listed. `fail` and a wave event count no run the
floor names, and the janitor counts no run whose verdict is older than the floor's time, so the old runs never stop the issue
again. The run set covers a record with no time; the time covers a run no ledger listed.

A missing file holds the issue when the old counters of the profile's primary repository (SG_REPO) are there for it --
auto/requeues-<n>, auto/loopqueues-<n>, or their .runs ledgers (ADR-0149 rule 4): the issue has loop facts the ledger does
not hold, so a read as todo would lose them, and a read of the old files would be the fallback the rule forbids. `read`
gives `held` with the cause, and every writer but `rearm` and `migrate` refuses it. The owner's `migrate` writes the old
counters into the file once (the counter wins over the line count: a counter the owner reset holds, #673: requeues-543 read 1
while its ledger held 2 lines); `rearm` clears them. Once the file exists the old files are read no more. An issue with no
file and none of those counters is new: it reads todo.

`replay` is the check ADR-0149 rule 6 asks for before a marker prefix goes: it rebuilds the counts from each ledger's runs and the
review streak from its verdicts, through the same rules, and compares them with the stored state and with any old counter beside
the file. It prints "REPLAY <key>: ok" and exits 0, or names the issue and each field that differs on stderr and exits 1; a held
ledger exits 3, never a pass.
"""
import contextlib
import fcntl
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time

LEDGERS = ("own", "loop")
OLD = {"own": "requeues", "loop": "loopqueues"}   # the old marker names wave.sh wrote in auto/ before #673
WAVE = ("entered", "held", "refused", "stuck", "owed", "delivered")   # the wave events wave.sh sends (ADR-0149)
EPISODE = ("held", "stuck")   # the wave states a hold episode covers (#1167)
BOARD_FIELD = "Status"   # the project field a card's column is written to (ADR-0164)
STREAK = 3   # consecutive CHANGES verdicts that end the issue needs-human (ADR-0151 rule 1)
VERDICTS = ("changes", "approve")   # the review verdicts the streak counts (ADR-0151)
KEY = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([0-9]+)")


def parse(key):   # <owner>/<repo>#<n> -> (owner, repo, n); anything else is refused (ADR-0138: never a bare number)
    m = KEY.fullmatch(key or "")
    if not m: raise ValueError(f"not an issue key <owner>/<repo>#<n>: {key!r}")
    return m.group(1), m.group(2), m.group(3)


# The profile the module was imported under. The hub's mux pins the profile env around the hub's import and restores the
# shell's after it, so a read of SG_STATE at request time finds nothing an import found (#1073). No default enters here: with
# neither value the caller is refused by name, never handed the machine-wide path.
_STATE_AT_IMPORT = os.environ.get("SG_STATE", "")


def state_dir():
    d = os.environ.get("SG_STATE") or _STATE_AT_IMPORT
    if not d:
        raise RuntimeError("issue_ledger: no SG_STATE; the profile in force names no state directory")
    return pathlib.Path(d)


def path(key):
    o, r, n = parse(key)
    return state_dir() / "state" / f"{o}__{r}__{n}.json"


def review_fact(rec):   # the review streak of the issue: a fact from the ledger, or the one a new issue has
    r = rec.get("review") or {}
    return {"changes": r.get("changes", 0), "moves": r.get("moves", 0), "seen": r.get("seen", 0),
            "findings": r.get("findings", ""), "stopped": r.get("stopped"), "verdicts": list(r.get("verdicts") or [])}


def empty(key):
    return {"key": key, "review": review_fact({}), **{led: {"count": 0, "runs": [], "stopped": None} for led in LEDGERS}}


def seed(key):   # the old auto/ files of the primary repository: what `migrate` writes into a missing file
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


class Held(ValueError):   # the issue's ledger cannot be read as one: the issue is held, never read as the seed (ADR-0149 rule 4)
    pass


class Corrupt(Held):   # the issue's file does not parse as a ledger
    pass


class Missing(Held):   # the issue's file is missing while the old counters of the issue are there
    pass


def old_files(key):   # the old auto/ counter files of the issue, when the key is the primary repository's
    o, r, n = parse(key)
    if os.environ.get("SG_REPO", f"{o}/{r}") != f"{o}/{r}": return []
    a = state_dir() / "auto"
    return [p.name for name in OLD.values() for p in (a / f"{name}-{n}", a / f"{name}-{n}.runs") if p.exists()]


def load(key):
    p = path(key)
    try: rec = json.loads(p.read_text())
    except FileNotFoundError:
        old = old_files(key)
        if old: raise Missing(f"a missing ledger holds the issue: {p} is not there, and the old counters {', '.join(old)} are; "
                              f"`issue_ledger.py migrate {key}` carries them in, `rearm` clears them") from None
        return empty(key)   # a new issue: no loop fact yet
    except (ValueError, UnicodeDecodeError) as e: raise Corrupt(f"a corrupt ledger holds the issue: {p} ({e})") from None
    if not (isinstance(rec, dict) and all(isinstance(rec.get(led), dict) and isinstance(rec[led].get("runs"), list) for led in LEDGERS)
            and all(isinstance(rec.get(k, {}), dict) for k in ("wave", "landed", "requeued", "review", "board"))
            and isinstance((rec.get("review") or {}).get("verdicts", []), list)):
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
def locked(key, wait=True):   # one lock per issue: a writer holds it from its read to its rename; the kernel frees it when a writer dies
    p = path(key); p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p) + ".lock", "a") as f:
        if wait: fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        else:
            try: fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try: yield True
        finally: fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def state(rec):
    return ("frozen" if rec["own"]["stopped"] else
            "needs-human" if rec["loop"]["stopped"] or (rec.get("review") or {}).get("stopped") else "todo")


def last_fail(rec):   # the reason of the newest own failure, the one a redispatch must repair (#670)
    runs = rec["own"]["runs"]
    return runs[-1]["why"] if runs else ""


def episodes(rec, now=None):   # the hold episodes with their age: an open one ages to now, a closed one to its close (#1167)
    now = time.time() if now is None else now
    return [{"run": e["run"], "cause": e["cause"], "start": e["start"], "count": e["count"], "open": "closed" not in e,
             "age": round(e.get("closed", now) - e["start"], 3)} for e in rec.get("episodes") or []]


def _key_of(name):   # the issue key a ledger file name spells, `<owner>__<repo>__<n>.json`; None when it is not that name
    stem = name[:-5] if name.endswith(".json") else ""
    parts = stem.rsplit("__", 2)
    if len(parts) != 3: return None
    key = f"{parts[0]}/{parts[1]}#{parts[2]}"
    try: parse(key)
    except ValueError: return None
    return key


_OPEN_CACHE: dict = {}   # state dir -> (pass token, files' signature, open episodes, read): the queue kept between ticks (#1746)
_OPEN_LOCK = threading.Lock()


def _state_signature(d):
    """The ledger files' own signature: each file's name, modification time and size, in name order. A reader compares it
    with the pass it kept, so an unchanged ledger is served from that pass and no file is opened."""
    sig = []
    for f in sorted((d / "state").glob("*.json")):
        try: st = f.stat()
        except OSError: return None
        sig.append((f.name, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def open_episodes(state=None, now=None):
    """Every open hold episode in the ledger, in one pass over the state directory (#1173): each issue file is opened
    once, and an episode with no `closed` is a child that still waits. Each episode carries its key beside its run, cause,
    start, count and age, oldest first. `read` is False when a file the ledger owns does not parse as one: the queue is
    then unknown, never empty, because a missing reading is not a reading of nothing (ADR-0018).
    The pass is kept until one of the ledger files changes (#1746): the footer asks once a tick, and an unchanged ledger
    is one signature of a few stats, never a parse of every issue. An age is measured against the caller's own `now`."""
    d = pathlib.Path(state) if state is not None else state_dir()
    now = time.time() if now is None else now
    token = time.monotonic_ns()
    sig = _state_signature(d)
    with _OPEN_LOCK:
        kept = _OPEN_CACHE.get(str(d)) if sig is not None else None
    if kept is not None and kept[1] == sig:
        out, read = kept[2], kept[3]
    else:
        out, read = [], True
        for f in sorted((d / "state").glob("*.json")):
            try: rec = json.loads(f.read_text())
            except (OSError, ValueError): read = False; continue
            key = _key_of(f.name)
            if not isinstance(rec, dict) or key is None: read = False; continue
            try: eps = episodes(rec, now)
            except (KeyError, TypeError): read = False; continue
            out += [{"key": key, **{k: e[k] for k in ("run", "cause", "start", "count")}} for e in eps if e["open"]]
        out.sort(key=lambda e: e["start"])
        if sig is not None:
            with _OPEN_LOCK:
                held = _OPEN_CACHE.get(str(d))
                if held is None or held[0] < token: _OPEN_CACHE[str(d)] = (token, sig, out, read)
    return {"episodes": [{**e, "age": round(now - e["start"], 3)} for e in out], "read": read}


def read(key):   # a corrupt file, or a missing one beside the old counters, reads held with its cause: the reader holds the issue
    try: rec = load(key)
    except Held as e: return {**empty(key), "wave": {}, "landed": {}, "requeued": {}, "episodes": [], "state": "held", "held": str(e), "attempts": 0, "last_fail": ""}
    return {**rec, "wave": rec.get("wave", {}), "landed": rec.get("landed", {}), "requeued": rec.get("requeued", {}),
            "board": rec.get("board", {}), "episodes": episodes(rec), "review": review_fact(rec), "state": state(rec), "held": "",
            "attempts": rec["own"]["count"], "last_fail": last_fail(rec)}


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


def _episode(rec, run, st, cause, start, now):   # -> (observed|opened|closed|stale|None, changed): the run's hold episode (#1167)
    E = rec.setdefault("episodes", [])
    cur = next((e for e in E if e["run"] == run and "closed" not in e), None)
    if st not in EPISODE:
        if cur is None: return None, False
        cur["closed"] = now
        return "closed", True
    if start is not None and (cur is None or start != cur["start"]): return "stale", False   # a writer holds a key that is not the open episode's
    if cur is not None and cur["cause"] == cause:
        cur["count"] += 1
        return "observed", True
    if cur is not None: cur["closed"] = now
    E.append({"run": run, "cause": cause, "start": now, "count": 1})
    return "opened", True


def _wave(rec, ev):   # -> (recorded|duplicate|observed|opened|closed|stale|terminal|kept|floor, changed): one wave event of one run (ADR-0149)
    run, st, cause = ev["run"], ev["state"], first_line(ev["cause"])
    if st not in WAVE: raise ValueError(f"not a wave event: {st!r} (one of {', '.join(WAVE)})")
    if not run: raise ValueError("a wave event names its run")
    if run in floor_runs(rec): return "floor", False   # a run from before the rearm counts toward nothing after it
    W = rec.setdefault("wave", {}); old = W.get(run)
    if old and old.get("state") == "delivered": return "terminal", False
    if old and ev.get("keep"): return "kept", False
    ep, ep_changed = _episode(rec, run, st, cause, ev.get("start"), time.time())
    if ep == "stale": return "stale", False
    tries = ev.get("try") and (old or {}).get("tries", 0) + 1
    if not tries and old and old.get("state") == st and old.get("cause") == cause:
        return (ep, True) if ep_changed else ("duplicate", False)   # a repeated hold is one observation of its episode, not a new fact
    W[run] = {"state": st, "cause": cause, "ts": time.time(), **(ev.get("extra") or {})}
    if st == "entered": W[run]["entered"] = cause
    elif old and old.get("entered"): W[run]["entered"] = old["entered"]   # the entry stays a fact of the run
    if tries: W[run]["tries"] = tries; return tries, True
    if old and old.get("tries"): W[run]["tries"] = old["tries"]   # a loop-caused failure counts no try and loses none
    return "recorded", True


def _landed(rec, ev):   # -> (recorded|duplicate|floor, changed): a landing pass put the issue's work in the base (ADR-0149)
    ref, kind, sha = ev["ref"], ev["kind"], ev["sha"]
    if kind not in ("run", "branch"): raise ValueError(f"not a landing kind: {kind!r} (run or branch)")
    if ref in floor_runs(rec): return "floor", False   # a run from before the rearm counts toward nothing after it
    L = rec.setdefault("landed", {}); old = L.get(ref)
    if old and old.get("sha") == sha: return "duplicate", False
    L[ref] = {"kind": kind, "sha": sha, "ts": time.time()}
    return "recorded", True


def _requeue(rec, ev):   # -> (recorded|duplicate|floor, changed): the head a run requeued the issue's child on
    run, head = ev["run"], ev["head"]
    if not run: raise ValueError("a requeue fact names its run")
    if run in floor_runs(rec): return "floor", False
    Q = rec.setdefault("requeued", {}); old = Q.get(run)
    if old and old.get("head") == head: return "duplicate", False
    Q[run] = {"head": head, "ts": time.time()}
    return "recorded", True


def _conflict(rec, ev):   # -> (first|repeat|held|duplicate|floor, changed): a rebase conflict of the issue's wave (ADR-0151 rule 2)
    run, sig = ev["run"], ev["sig"]
    if not run: raise ValueError("a conflict names its run")
    if run in floor_runs(rec): return "floor", False
    C = rec.get("conflict")
    if C and C["sig"] == sig:   # the same paths (the wave keys sig on them, #1343): the same conflict, whichever run hit it
        if run in C["runs"]: return "duplicate", False
        C["runs"].append(run); C["count"] += 1
        return ("repeat" if C["count"] == 2 else "held"), True
    rec["conflict"] = {"sig": sig, "runs": [run], "count": 1}
    return "first", True


def _review(rec, ev):   # -> (recorded|reset|duplicate|frozen|floor, changed): one review of the issue's work (ADR-0151)
    run, v, f, rnd = ev["run"], ev["verdict"], ev["findings"], str(ev.get("round") or 0)
    if v not in VERDICTS: raise ValueError(f"not a review verdict: {v!r} (one of {', '.join(VERDICTS)})")
    if not run: raise ValueError("a review verdict names its run")
    if run in floor_runs(rec): return "floor", False   # a run from before the rearm counts toward nothing after it
    R = rec["review"] = review_fact(rec)
    if any(x.get("run") == run and str(x.get("round") or 0) == rnd for x in R["verdicts"]): return "duplicate", False
    R["verdicts"].append({"run": run, "round": rnd, "verdict": v, "findings": f, "ts": time.time()})
    if v == "approve":
        R["changes"], R["moves"], R["seen"], R["findings"] = 0, 0, 0, ""
        return "reset", True   # the stop an earlier streak set stays: only the owner lifts it (ADR-0029)
    R["changes"] += 1
    R["seen"] = R["seen"] + 1 if f and f == R["findings"] else (1 if f else 0)   # identical findings twice move the coder (#702)
    R["moves"] += 1 if f and R["seen"] % 2 == 0 else 0
    R["findings"] = f
    if R["changes"] >= STREAK and not R["stopped"]:
        R["stopped"] = f"{STREAK} reviews of the issue asked for changes"
        return "frozen", True
    return "recorded", True


def _migrate(rec, ev):   # the old counters become the file, once: a ledger that exists is never migrated over
    if ev["old"] is not None: raise ValueError(f"nothing to migrate for {ev['key']}: its ledger exists, or it has no old counters")
    rec.clear(); rec.update(seed(ev["key"]))
    return None, True


def _rearm(rec, ev):   # the loop facts go, and the floor (#692) keeps every run any of them named
    old = ev["old"] or {}
    runs = {x["run"] for led in LEDGERS for x in (old.get(led) or {}).get("runs", []) if isinstance(x, dict) and x.get("run")}
    runs |= set((old.get("wave") or {}).keys()) | set((old.get("requeued") or {}).keys()) | set((old.get("rearmed") or {}).get("runs", []))
    runs |= {x["run"] for x in ((old.get("review") or {}).get("verdicts") or []) if isinstance(x, dict) and x.get("run")}
    runs |= {k for k, f in (old.get("landed") or {}).items() if isinstance(f, dict) and f.get("kind") == "run"}
    runs |= {(old.get("board") or {}).get("run", "")} - {""}
    now = time.time()
    closed = [{**e, "closed": e.get("closed", now)} for e in old.get("episodes") or []]   # an episode the rearm ends keeps its count and age (#1167)
    rec.clear(); rec.update(empty(ev["key"]))
    rec["rearmed"] = {"ts": now, "runs": sorted(runs)}   # a corrupt file has no runs to read: the time is the floor
    rec["episodes"] = closed
    return None, True


def _board(rec, ev):   # -> (recorded|duplicate|stale|floor, changed): one card write of a dispatch, pending before the write (ADR-0164)
    run, col = ev["run"], ev["column"]
    if not run or not col: raise ValueError("a board event names its run and its column")
    if run in floor_runs(rec): return "floor", False
    old = rec.get("board") or {}
    if old and ev["instant"] < old["instant"]: return "stale", False   # a late delivery never overwrites a newer event
    if old and (old["run"], old["column"]) == (run, col): return "duplicate", False   # the same event: the first instant stands
    rec["board"] = {"run": run, "column": col, "instant": ev["instant"], "state": "pending", "req": ev["req"],
                    "project": ev["project"], "field": ev["field"], "by": ev.get("by") or ""}
    return "recorded", True


def _boardw(rec, ev):   # -> (written|duplicate|superseded, changed): the card write of an event landed, and when, and by whom
    old = rec.get("board") or {}
    if (old.get("run"), old.get("column")) != (ev["run"], ev["column"]): return "superseded", False
    if old["state"] == "written": return "duplicate", False
    rec["board"] = {**old, "state": "written", "landed": ev["ts"], "by": ev["actor"]}
    return "written", True


def _boardx(rec, ev):   # -> (dropped|superseded, changed): a pending event the gates no longer back is dropped, never written
    old = rec.get("board") or {}
    if (old.get("run"), old.get("column")) != (ev["run"], ev["column"]) or old.get("state") != "pending": return "superseded", False
    rec["board"] = {**old, "state": "dropped"}
    return "dropped", True


RULES = {"fail": _fail, "stop": _stop, "wave": _wave, "landed": _landed, "requeue": _requeue, "conflict": _conflict, "review": _review,
         "rearm": _rearm, "migrate": _migrate, "board": _board, "boardw": _boardw, "boardx": _boardx}


def transition(key, ev):   # the one transition function (ADR-0149): every event of every writer, under the issue's lock
    with locked(key, ev.get("wait", True)) as got:
        if not got: return "locked"   # a writer that may not wait is refused by name, never queued behind the lock holder
        try: rec = load(key); ev = {**ev, "old": rec if ev["op"] in ("rearm", "migrate") else None}
        except Corrupt:
            if ev["op"] != "rearm": raise   # a writer never overwrites a corrupt file: the issue stays held, the file stays for the owner
            rec = empty(key); ev = {**ev, "old": None}
        except Missing:
            if ev["op"] not in ("rearm", "migrate"): raise   # only the owner's act decides what the old counters become
            rec = empty(key); ev = {**ev, "old": None}
        out, changed = RULES[ev["op"]](rec, {**ev, "key": key})
        if changed: save(key, rec)
        return out


def fail(key, run, evo, why, led="own"):   # -> (count, fresh, stopped)
    return transition(key, {"op": "fail", "run": run, "evo": evo, "why": why, "led": led})


def stop(key, why, led="own"):
    return transition(key, {"op": "stop", "why": why, "led": led})


def wave(key, run, st, cause, keep=False, extra=None, start=None, wait=True):   # start: the open episode's key a keyed writer holds
    return transition(key, {"op": "wave", "run": run, "state": st, "cause": cause, "keep": keep, "extra": extra, "start": start, "wait": wait})


def migrate(key):
    return transition(key, {"op": "migrate"})


def rearm(key):   # the SQUAD-NOT-STARTABLE comment goes through the comment queue (cqflush.sh unstart~)
    o, r, n = parse(key)
    transition(key, {"op": "rearm"})
    # #1301 the notstartable claims health.sh wrote with the marker are its memory that the marker was posted: the marker
    # goes, so the claims of this issue go with it, and a later failure with the same reason is marked again. health.sh
    # serves the profile's primary repository and names a claim by a bare issue number, so only that repository's rearm
    # may clear; another repository's issue of the same number keeps its claims.
    if os.environ.get("SG_REPO", f"{o}/{r}") == f"{o}/{r}":
        for p in (state_dir() / "auto").glob(f"notstartable-{n}-*"):
            with contextlib.suppress(OSError): p.unlink()
    q = state_dir() / "cq"; q.mkdir(parents=True, exist_ok=True)
    name = f"{o}__{r}#{n}-unstart~rearm{os.getpid()}.md"
    (q / (name + ".tmp")).write_text("SQUAD-NOT-STARTABLE:\n"); os.replace(q / (name + ".tmp"), q / name)
    return q / name


def landtry(key, run, cause):   # -> the run's failed landings for the issue, this one counted; 0 when the floor names the run
    out = transition(key, {"op": "wave", "run": run, "state": "stuck", "cause": cause, "try": True})
    return out if isinstance(out, int) else 0


def owe(key, run, w, sha):
    return wave(key, run, "owed", f"landed {w} as {sha}, and the issue did not close", extra={"wave": w, "sha": sha})


def landed(key, ref, kind, sha):   # -> recorded|duplicate|floor: a landing pass put the issue's work in the base
    return transition(key, {"op": "landed", "ref": ref, "kind": kind, "sha": sha})


def requeue(key, run, head):   # -> recorded|duplicate|floor: the head a run requeued the issue's child on
    return transition(key, {"op": "requeue", "run": run, "head": head})


def conflict(key, run, sig):   # -> first|repeat|held|duplicate|floor: one rebase conflict of the issue's wave (ADR-0151 rule 2)
    return transition(key, {"op": "conflict", "run": run, "sig": sig})


def requeued(key, run):   # the head the run requeued the issue on, "" when it did not
    return ((load(key).get("requeued") or {}).get(run) or {}).get("head", "")


def review(key, run, verdict, findings="", rnd=0):   # -> recorded|reset|duplicate|frozen|floor: one review of the issue
    return transition(key, {"op": "review", "run": run, "verdict": verdict, "findings": findings, "round": rnd})


def board(key, run, column, req="", instant=None, by=""):
    """-> recorded|duplicate|stale|floor: a card write is an event, pending before it (ADR-0164). The event carries its
    instant, the column it targets and the writer's name, so a reader of the board knows whose write it predicts (#1841)."""
    return transition(key, {"op": "board", "run": run, "column": column, "req": req, "project": os.environ.get("SG_PROJECT", ""),
                            "field": BOARD_FIELD, "instant": time.time() if instant is None else float(instant), "by": by})


def boardw(key, run, column, actor=""):   # -> written|duplicate|superseded: the card write of the event landed, at this instant, by this actor
    return transition(key, {"op": "boardw", "run": run, "column": column, "actor": actor, "ts": time.time()})


def board_writes(repo):   # -> [{repo, issue, project, field, transition, actor, instant, landed}]: each board event that has a landing instant
    out = []
    for n, rec in repo_files(repo):
        b = rec.get("board") or {}
        if b.get("state") != "written" or "landed" not in b: continue
        out.append({"repo": repo, "issue": n, "project": b.get("project", ""), "field": b.get("field", ""),
                    "transition": f"{b['run']}/{b['column']}", "actor": b.get("by") or "unknown",
                    "instant": b["instant"], "landed": b["landed"]})
    return out


def boardx(key, run, column):   # -> dropped|superseded: a pending card write the gates no longer back, dropped unwritten
    return transition(key, {"op": "boardx", "run": run, "column": column})


def pending(target):   # -> [(n, run, column, instant, req, by)]: the board writes not yet landed, of one issue or of a repository
    recs = [(parse(target)[2], load(target))] if "#" in target else repo_files(target)
    return [(n, b["run"], b["column"], b["instant"], b.get("req", ""), b.get("by", "")) for n, rec in recs
            for b in [rec.get("board") or {}] if b.get("state") == "pending"]


_PRED_CACHE: dict = {}   # (state dir, repository) -> (files' signature, predictions): the board reads it every serve (#1841)


def predictions(repo, state=None):
    """-> [{n, column, instant, by, req}]: each card write the repository's ledger holds pending, with the instant the
    writer recorded it and the writer's name. A prediction is the writer's own fact, never GitHub's (#1841): a reader of
    the board draws it over a cache that lags, and only the writer's proof (`boardw`) or its drop (`boardx`) ends it.
    The pass is kept until one of the ledger files changes, as `open_episodes` keeps its own (#1746)."""
    d = pathlib.Path(state) if state is not None else state_dir()
    sig = _state_signature(d)
    with _OPEN_LOCK:
        kept = _PRED_CACHE.get((str(d), repo)) if sig is not None else None
    if kept is not None and kept[0] == sig:
        return kept[1]
    rows = [{"n": n, "column": b["column"], "instant": b["instant"], "by": b.get("by", ""), "req": b.get("req", "")}
            for n, rec in repo_files(repo) for b in [rec.get("board") or {}] if b.get("state") == "pending"]
    if sig is not None:
        with _OPEN_LOCK:
            _PRED_CACHE[(str(d), repo)] = (sig, rows)
    return rows


def coder(key):   # the places down the coder chain the next fix attempt starts: one per repeated finding (ADR-0151 rule 2)
    return int(review_fact(load(key)).get("moves") or 0)


def repo_files(repo):   # -> [(n, record)] for every readable ledger file of the repository; a corrupt one holds its issue at dispatch already
    o, r, _ = parse(f"{repo}#0"); out = []
    for p in sorted(state_dir().joinpath("state").glob(f"{o}__{r}__*.json")):
        n = p.name[len(f"{o}__{r}__"):-len(".json")]
        if not n.isdigit(): continue
        try: out.append((n, load(f"{repo}#{n}")))
        except Held: continue
    return out


def owed(repo, run):   # -> [(sha, wave, n)]: each issue the run's landed wave still owes a close
    return [(f.get("sha", ""), f.get("wave", ""), n) for n, rec in repo_files(repo)
            for f in [(rec.get("wave") or {}).get(run)] if isinstance(f, dict) and f.get("state") == "owed"]


def entered(repo):   # -> [(run, n)]: each run a child of the repository entered, from every ledger file of it (ADR-0149)
    return [(run, n) for n, rec in repo_files(repo) for run, fact in sorted((rec.get("wave") or {}).items())
            if isinstance(fact, dict) and fact.get("entered")]


def landings(repo):   # -> {n: {(ref, kind)}}: the landings each issue of the repository holds, from every readable ledger file
    return {n: {(ref, (fact or {}).get("kind", "run")) for ref, fact in (rec.get("landed") or {}).items() if isinstance(fact, dict)}
            for n, rec in repo_files(repo)}


def legacy(key, led):   # -> (counter or None, [{run, key, why}] or None, frozen): the old auto/ files of one ledger, read only to be compared
    o, r, n = parse(key)
    if os.environ.get("SG_REPO", f"{o}/{r}") != f"{o}/{r}": return None, None, False
    a = state_dir() / "auto"; name = OLD[led]
    try: c = (a / f"{name}-{n}").read_text().strip()
    except OSError: c = ""
    try: lines = [x for x in (a / f"{name}-{n}.runs").read_text().splitlines() if x]
    except OSError: lines = None
    runs = None if lines is None else [dict(zip(("run", "key", "why"), (x.split("\t", 2) + ["", ""])[:3])) for x in lines if x != "frozen"]
    return (int(c) if c.isdigit() else None), runs, bool(lines) and lines[-1] == "frozen"


def replay(key):   # -> [what differs]: the state rebuilt from the ledger's events, against the stored state and the old counters
    try: rec = load(key)
    except Held as e: raise Held(f"{key}: {e}") from None
    diffs = []
    for led in LEDGERS:
        L = rec[led]; mine = [{f: x.get(f, "") for f in ("run", "key", "why")} for x in L["runs"]]
        counter, old_runs, frozen = legacy(key, led)
        want = counter if counter is not None else len(mine)   # the counter wins over the line count (#673)
        if L["count"] != want:
            diffs.append(f"{led}.count: stored {L['count']}, replay {want}" + (f" ({OLD[led]}-{parse(key)[2]} holds {counter})" if counter is not None else ""))
        if old_runs is not None and old_runs != mine:
            diffs.append(f"{led}.runs: stored {mine}, {OLD[led]}-{parse(key)[2]}.runs holds {old_runs}")
        if old_runs is not None and frozen != bool(L.get("stopped")):
            diffs.append(f"{led}.frozen: stored {L.get('stopped')!r}, {OLD[led]}-{parse(key)[2]}.runs {'ends' if frozen else 'does not end'} with frozen")
    fresh = {"review": review_fact({})}
    for v in (rec.get("review") or {}).get("verdicts") or []:
        _review(fresh, {"run": v.get("run"), "verdict": v.get("verdict"), "findings": v.get("findings", ""), "round": v.get("round")})
    got, had = fresh["review"], review_fact(rec)
    for f in ("changes", "moves", "seen", "findings"):
        if got[f] != had[f]: diffs.append(f"review.{f}: stored {had[f]!r}, replay {got[f]!r}")
    if bool(got["stopped"]) != bool(had["stopped"]):
        diffs.append(f"review.stopped: stored {had['stopped']!r}, replay {got['stopped']!r}")
    return diffs


def main(argv):
    if len(argv) < 2: print(__doc__.split("\n\n")[0], file=sys.stderr); return 2
    op, key, rest = argv[0], argv[1], argv[2:]
    try:
        if op == "entered":
            for run, n in entered(key): print(f"{run}\t{n}")
            return 0
        if op == "pending":
            for row in pending(key): print("\t".join(str(x) for x in row))
            return 0
        if op == "owed" and rest:
            for sha, w, n in owed(key, rest[0]): print(f"{sha}\t{w}\t{n}")
            return 0
        if op == "landed" and "#" not in key:
            for ref, n in ((ref, n) for n, facts in sorted(landings(key).items(), key=lambda kv: int(kv[0]))
                           for ref in sorted(f for f, _ in facts)):
                print(f"{ref}\t{n}")
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
        elif op == "landtry" and len(rest) >= 2: print(landtry(key, rest[0], rest[1]))
        elif op == "owe" and len(rest) >= 3: print(owe(key, rest[0], rest[1], rest[2]))
        elif op == "landed" and len(rest) >= 3: print(landed(key, rest[0], rest[1], rest[2]))
        elif op == "requeue" and len(rest) >= 2: print(requeue(key, rest[0], rest[1]))
        elif op == "requeued" and rest: print(requeued(key, rest[0]))
        elif op == "conflict" and len(rest) >= 2: print(conflict(key, rest[0], rest[1]))
        elif op == "review" and len(rest) >= 3: print(review(key, rest[0], rest[1], rest[2], *(rest[3:4] or [0])))
        elif op == "replay":
            diffs = replay(key)
            if diffs:
                print(f"issue_ledger.py: replay of {key} differs: " + "; ".join(diffs), file=sys.stderr); return 1
            print(f"REPLAY {key}: ok")
        elif op == "board" and len(rest) >= 3:
            print(board(key, rest[0], rest[1], rest[2], *(rest[3:4] or []), by=(rest[4] if len(rest) > 4 else "")))
        elif op == "boardw" and len(rest) >= 2: print(boardw(key, rest[0], rest[1], *(rest[2:3] or [""])))
        elif op == "boardx" and len(rest) >= 2: print(boardx(key, rest[0], rest[1]))
        elif op == "coder": print(coder(key))
        elif op == "wave" and len(rest) >= 3: print(wave(key, rest[0], rest[1], rest[2], rest[3:4] == ["keep"]))
        elif op == "migrate": migrate(key); print(f"MIGRATE {key}: the old counters are in its ledger file; they are read no more")
        elif op == "rearm": print(f"REARM {key}: the loop facts are cleared; {rearm(key).name} removes the SQUAD-NOT-STARTABLE comment")
        else: print(f"issue_ledger.py: bad call: {' '.join(argv)}", file=sys.stderr); return 2
    except Held as e:
        print(f"issue_ledger.py: held: {e}", file=sys.stderr); return 3
    except (ValueError, KeyError) as e:
        print(f"issue_ledger.py: {e}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
