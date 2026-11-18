"""Who holds each run slot when a frontier pass refuses a startable card for "no slot" (#2286).

The frontier pass writes one record per refusal to `slot-refusals.jsonl` in the loop's state directory: the instant,
the free run slots, the startable Todo cards the pass left, and the holder of each run slot with the kind of slot it
holds. The pass writes the record itself, from facts it already holds: the run counters of `squad/caps.py` and the
active-run list. No sampler, no thread and no separate measure starts for it.

A holder is one of five kinds:

- `gates`: the run waits for a gate slot, the wait `caps` names in its `waiting` leases.
- `autobug`: autobug dispatched the run, the claim marker `auto/bug-<n>` names its issue.
- `landing`: the run finished and writes its verdict, so it waits for its landing.
- `doing`: a live run with no verdict yet, so the card it works stands in Doing.
- `unknown`: a holder the pass cannot explain, named `unknown` rather than guessed (ADR-0155).

The evidence order is that list: a run that waits for a gate slot is named for the wait even when it is an autobug
run, because the wait is what holds the slot.

The fold from these records to the Loop page's split lives in the page's rule layer (`hub_profiler.slot_wait`), and the
reader of their file lives beside it (`hub_profiler.slot_records`), so the page imports no part of this module.
Guard: `tests/test_slot_holders.py`, `tests/test_hub_profiler.py`.
"""
import json
import os
import pathlib
import re
import time

FILE = "slot-refusals.jsonl"      # the record in the loop's state directory
KINDS = ("doing", "landing", "gates", "autobug", "unknown")
MAX_RECORDS = 2000                # the newest records the file keeps: a beat of refusals a day reads well inside it


def classify(name, record_text, waiting=(), autobug=()):
    """The kind of slot `name` holds: `gates`, `autobug`, `landing`, `doing`, else `unknown`.

    `record_text` is the holder's `record.jsonl` text, or None when the folder holds no readable record. `waiting` names
    the holders that wait for a gate slot, `autobug` the holders autobug dispatched.
    """
    if name in waiting:
        return "gates"
    if name in autobug:
        return "autobug"
    if record_text is None:
        return "unknown"
    from squad import caps as C
    return "landing" if C.final_verdict(record_text) is not None else "doing"


def _folder(checkout, name):
    return pathlib.Path(name) if os.path.isabs(name) else pathlib.Path(checkout) / "runs" / name


def _autobug_holders(state_dir, checkout, holders):
    """The holders autobug dispatched: the claim markers `auto/bug-<n>` name the issues it took, and the run's own
    request names them. A marker's issue the request does not name is another run's.

    The markers are read once and each request is scanned for its own numbers with one small pattern, never for one
    alternation built from every marker: the frontier pass calls this on its own hot path, so a large `auto/` directory
    costs one small scan a request, never one huge pattern a pass."""
    marks = {p.name[4:] for p in (pathlib.Path(state_dir) / "auto").glob("bug-*") if p.name[4:].isdigit()}
    if not marks:
        return set()
    rx = re.compile(r"(?<!\d)(\d+)(?!\d)")
    out = set()
    for name in holders:
        try:
            text = (_folder(checkout, name) / "request.txt").read_text(errors="replace")
        except OSError:
            continue
        if any(n in marks for n in rx.findall(text)):
            out.add(name)
    return out


def record_pass(state_dir, todo, checkout="."):
    """Write one record of who holds each run slot now, and its kinds, as the frontier pass sees them (#2286): the free
    run slots and the cap from `squad/caps.py`, the holders from the active-run list, the gate waiters from the same
    counter's waiting leases, and the autobug runs from their claim markers. A ceiling that cannot be read writes no
    record and raises, so the pass names the failure instead of recording a guess."""
    from squad import caps as C
    ceil = C.ceiling(checkout)
    active = pathlib.Path(state_dir) / "active-runs.txt"
    holders = ceil.run_holders(active, root=checkout)
    waiting = {w.get("holder") for w in ceil.waiting() if w.get("ceiling") in ("gates", "reviews")}
    autobug = _autobug_holders(state_dir, checkout, holders)
    named = []
    for name in holders:
        try:
            text = (_folder(checkout, name) / "record.jsonl").read_text(errors="replace")
        except OSError:
            text = None
        named.append({"name": name, "kind": classify(name, text, waiting, autobug)})
    return write(pathlib.Path(state_dir) / FILE, at=time.time(),
                 free=C.free_runs(ceil, active, root=checkout), cap=ceil.caps["runs"],
                 todo=int(todo), holders=named)


def write(path, at, free, cap, todo, holders):
    """Append one refusal record and keep the newest `MAX_RECORDS` lines. A holder named twice is written once."""
    seen, named = set(), []
    for h in holders:
        if h["name"] in seen:
            continue
        seen.add(h["name"])
        named.append({"name": h["name"], "kind": h["kind"]})
    line = {"at": float(at), "free": int(free), "cap": int(cap), "todo": int(todo), "holders": named}
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.exists():
        lines = path.read_text(errors="replace").splitlines()[-(MAX_RECORDS - 1):]
    lines.append(json.dumps(line, separators=(",", ":")))
    tmp = path.with_name(f"{path.name}.{os.getpid()}")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)      # an interruptible write is atomic (ADR-0030)
    return line


