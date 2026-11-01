#!/usr/bin/env python3
"""Late board writes and their corrections (#1022, ADR-0164, ADR-0149).

A board write is late when GitHub records it after its event. Every positive lag is recorded, keyed by the actor that landed
the write, and shown with no threshold. A correction repairs one late write from evidence: it names the repository, the
issue, the project, the field and the transition, keeps both instants and its evidence. An ambiguous match is a conflict,
never a guess. Corrections live in one versioned file with a backup (ADR-0030); a late instant with no readable correction
reads approximate. The computation reads no host and opens no client: the issue ledger is its one source of events.

usage: board_lag.py rows <owner>/<repo>                              the Monitor payload, as JSON
       board_lag.py correct <owner>/<repo> <issue> <project> <field> <transition> <landed> <effective> <evidence>
                                                                     one verified correction: prints recorded|duplicate,
                                                                     or names the conflict on stderr and exits 1
"""
import contextlib
import json
import os
import pathlib
import sys
import tempfile

import issue_ledger

VERSION = 1
KEYS = ("repo", "issue", "project", "field", "transition")
STORE_NAME = "board-corrections.json"


class Conflict(ValueError):
    pass


class Lost(ValueError):
    pass


def lag(instant, landed):
    if instant is None or landed is None: return None
    d = float(landed) - float(instant)
    return d if d > 0 else None


def late_writes(writes):
    out = []
    for w in writes:
        d = lag(w.get("instant"), w.get("landed"))
        if d is None: continue
        out.append({**{k: w.get(k, "") for k in KEYS}, "actor": w.get("actor") or "unknown",
                    "instant": float(w["instant"]), "landed": float(w["landed"]), "lag": d})
    return out


def by_actor(lates):
    agg = {}
    for x in lates:
        a = agg.setdefault(x["actor"], {"actor": x["actor"], "count": 0, "max": 0.0, "total": 0.0})
        a["count"] += 1
        a["max"] = max(a["max"], x["lag"])
        a["total"] += x["lag"]
    return [agg[k] for k in sorted(agg)]


def make_correction(key, landed, effective, evidence, lates):
    if not all(str(key.get(k) or "").strip() for k in KEYS): raise Conflict(f"a correction names every key: {', '.join(KEYS)}")
    if not str(evidence or "").strip(): raise Conflict("a correction names its evidence")
    if float(effective) > float(landed): raise Conflict("impossible order: the effective instant is after the write")
    found = [x for x in lates if all(x[k] == key[k] for k in KEYS)]
    if not found: raise Conflict("no candidate matches the transition: nothing is corrected")
    if len(found) > 1: raise Conflict(f"{len(found)} candidates match the transition: a correction is never guessed")
    if found[0]["landed"] != float(landed): raise Conflict("the write instant differs from the ledger: the correction is stale")
    return {**{k: key[k] for k in KEYS}, "landed": float(landed), "effective": float(effective), "evidence": str(evidence).strip()}


def backup_path(p):
    p = pathlib.Path(p)
    return p.with_name(p.name + ".bak")


def _valid(data):
    return (isinstance(data, dict) and data.get("version") == VERSION and isinstance(data.get("corrections"), list)
            and all(isinstance(c, dict) and all(k in c for k in KEYS + ("landed", "effective", "evidence"))
                    for c in data["corrections"]))


def _load(p):
    try: data = json.loads(pathlib.Path(p).read_text())
    except (OSError, ValueError): return None
    return data["corrections"] if _valid(data) else None


def read_store(p):   # -> (corrections, ok|backup|absent|lost): the file, else its backup, else nothing readable
    got = _load(p)
    if got is not None: return got, "ok"
    got = _load(backup_path(p))
    if got is not None: return got, "backup"
    p = pathlib.Path(p)
    return [], ("lost" if p.exists() or backup_path(p).exists() else "absent")


def _atomic(p, body):   # temp file, fsync, rename (ADR-0030): a kill leaves the old file whole
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError): os.unlink(tmp)
        raise


def write_store(p, corrections):
    p = pathlib.Path(p)
    body = json.dumps({"version": VERSION, "corrections": corrections}, indent=1, sort_keys=True) + "\n"
    _atomic(p, body)
    _atomic(backup_path(p), body)


def record(p, correction):   # -> recorded|duplicate: a correction is added, never overwritten; a lost store is never written over
    corrections, status = read_store(p)
    if status == "lost": raise Lost(f"the corrections are lost: {p} and its backup do not read; nothing is written over them")
    same = [c for c in corrections if all(c[k] == correction[k] for k in KEYS)]
    if same:
        if same[0] == correction: return "duplicate"
        raise Conflict("a correction for this transition already holds other evidence")
    write_store(p, corrections + [correction])
    return "recorded"


def resolve(lates, corrections):   # each late write with its effective instant; approximate unless a readable correction repairs it
    idx = {tuple(c[k] for k in KEYS): c for c in corrections}
    out = []
    for x in lates:
        c = idx.get(tuple(x[k] for k in KEYS))
        ok = c is not None and c["landed"] == x["landed"]
        out.append({**x, "effective": float(c["effective"]) if ok else x["landed"], "approximate": not ok})
    return out


def monitor(lates, corrections, status):
    rs = resolve(lates, corrections)
    rows = []
    for a in by_actor(rs):
        approx = sum(1 for x in rs if x["actor"] == a["actor"] and x["approximate"])
        text = f"{a['count']} late; max {a['max']:.3f} s; total {a['total']:.3f} s"
        rows.append({"actor": a["actor"], "count": a["count"], "text": text + (f"; {approx} approximate" if approx else "")})
    return {"rows": rows, "late": len(rs), "approximate": sum(1 for x in rs if x["approximate"]), "store": status}


def store_path():
    return issue_ledger.state_dir() / STORE_NAME


def payload(repo):
    corrections, status = read_store(store_path())
    return monitor(late_writes(issue_ledger.board_writes(repo)), corrections, status)


def main(argv):
    if len(argv) < 2: print(__doc__.split("\n\n")[0], file=sys.stderr); return 2
    op, repo = argv[0], argv[1]
    try:
        if op == "rows" and len(argv) == 2:
            print(json.dumps(payload(repo), sort_keys=True))
            return 0
        if op == "correct" and len(argv) == 9:
            n, project, field, transition, landed, effective, evidence = argv[2:]
            key = {"repo": repo, "issue": n, "project": project, "field": field, "transition": transition}
            c = make_correction(key, float(landed), float(effective), evidence, late_writes(issue_ledger.board_writes(repo)))
            print(record(store_path(), c))
            return 0
    except ValueError as e:
        print(f"board_lag.py: {e}", file=sys.stderr)
        return 1
    print(f"board_lag.py: bad call: {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
