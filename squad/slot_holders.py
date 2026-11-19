"""Who holds each run slot when a frontier pass refuses a startable card for "no slot" (#2286).

The frontier pass writes one record per refusal to `slot-refusals.jsonl` in the loop's state directory: the instant,
the free run slots, the startable Todo cards the pass left, and the holder of each run slot with the kind of slot it
holds. The pass writes the record itself, from facts it already holds: the run counters of `squad/caps.py` and the
active-run list. No sampler, no thread and no separate measure starts for it.

A holder is one of these kinds, in the order of evidence:

- `gates`: the run waits for a gate slot, the wait `caps` names in its `waiting` leases.
- `autobug`: autobug dispatched the run, the claim marker `auto/bug-<n>` names its issue.
- `wave`: a wave started the run, the marker `auto/wave-<run>` names the wave.
- `landing`: the run finished and writes its verdict, so it waits for its landing.
- `doing`: a card dispatch started the run. The request's claim line names the issue, and the marker `auto/disp-<n>`
  holds it. The record carries that issue number.
- `unknown`: a holder the pass cannot explain, named `unknown` rather than guessed (ADR-0155). The record carries the age
  of the holder's last progress, from the mtime of its `record.jsonl`.

A run that waits for a gate slot is named for the wait even when it is an autobug run, because the wait is what holds
the slot.

The fold from these records to the Loop page's split lives in the page's rule layer (`hub_profiler.slot_wait`), and the
reader of their file lives beside it (`hub_profiler.slot_records`), so the page imports no part of this module.
Guard: `tests/test_slot_holders.py`, `tests/test_hub_profiler.py`.
"""
import json
import os
import pathlib
import re
import time

from squad import caps as C

FILE = "slot-refusals.jsonl"      # the record in the loop's state directory
KINDS = ("doing", "wave", "landing", "gates", "autobug", "unknown")
MAX_RECORDS = 2000                # the newest records the file keeps: a beat of refusals a day reads well inside it
# The claim line the dispatch writes, one per child: `CHILD — GitHub issue(s) 31, 32 (…):`. Only this line names the
# issues a run works; a number the request cites in its prose is no claim (autobug.sh and autofrontier.sh write it).
CLAIM = re.compile(r"^CHILD\s*[—–-]\s*GitHub issue\(s\)[ \t]+([0-9, \t]+)", re.MULTILINE)


def classify(name, record_text, waiting=(), autobug=(), wave=(), card=()):
    """The kind of slot `name` holds: `gates`, `autobug`, `wave`, `landing`, `doing`, else `unknown`.

    `record_text` is the holder's `record.jsonl` text, or None when the folder holds no readable record. `waiting` names
    the holders that wait for a gate slot, `autobug` the holders autobug dispatched, `wave` the holders a wave started,
    and `card` the holders a card dispatch started.
    """
    if name in waiting:
        return "gates"
    if name in autobug:
        return "autobug"
    if name in wave:
        return "wave"
    if record_text is not None and C.final_verdict(record_text) is not None:
        return "landing"
    return "doing" if name in card else "unknown"


def _folder(checkout, name):
    return pathlib.Path(name) if os.path.isabs(name) else pathlib.Path(checkout) / "runs" / name


def _text(path):
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _claimed(checkout, name):
    """The issue numbers the run's claim line names, in the order the line gives them."""
    text = _text(_folder(checkout, name) / "request.txt")
    return [int(n) for m in CLAIM.finditer(text) for n in re.findall(r"\d+", m.group(1))]


def _autobug_holders(state_dir, checkout, holders):
    """The holders autobug dispatched: the claim markers `auto/bug-<n>` name the issues it took, and the run's own
    request names them. A marker's issue the request does not name is another run's.

    Only the claim line the dispatch writes names an issue a run works (`CLAIM`): a request that cites issue 77 in its
    prose is no proof that this run is the autobug run of issue 77. Each request is scanned with the one small pattern,
    never with an alternation built from every marker, so a large `auto/` directory costs one small scan a request."""
    marks = {p.name[4:] for p in (pathlib.Path(state_dir) / "auto").glob("bug-*") if p.name[4:].isdigit()}
    if not marks:
        return set()
    return {name for name in holders if {str(n) for n in _claimed(checkout, name)} & marks}


def _card_holders(state_dir, checkout, holders):
    """The holders a card dispatch started, each mapped to its issue: the claim line names the issue, and its marker
    `auto/disp-<n>` is the dispatch that holds it."""
    auto = pathlib.Path(state_dir) / "auto"
    out = {}
    for name in holders:
        for n in _claimed(checkout, name):
            if (auto / f"disp-{n}").exists():
                out.setdefault(name, n)
                break
    return out


def _wave_holders(state_dir, holders):
    """The holders a wave started, each mapped to the wave its marker `auto/wave-<run>` names."""
    auto = pathlib.Path(state_dir) / "auto"
    out = {}
    for name in holders:
        marker = auto / f"wave-{name}"
        if marker.exists():
            out[name] = _text(marker).strip() or f"wave/{name}"
    return out


def _record(folder):
    """The text of the holder's `record.jsonl` and its mtime, or `(None, None)` when the folder holds no readable record."""
    path = folder / "record.jsonl"
    try:
        return path.read_text(errors="replace"), path.stat().st_mtime
    except OSError:
        return None, None


def record_pass(state_dir, todo, checkout="."):
    """Write one record of who holds each run slot now, and its kinds, as the frontier pass sees them (#2286): the free
    run slots and the cap from `squad/caps.py`, the holders from the active-run list, the gate waiters from the same
    counter's waiting leases, and the autobug, wave and card runs from their markers and claim lines. A ceiling that
    cannot be read writes no record and raises, so the pass names the failure instead of recording a guess."""
    ceil = C.ceiling(checkout)
    active = pathlib.Path(state_dir) / "active-runs.txt"
    holders = ceil.run_holders(active, root=checkout)
    waiting = {w.get("holder") for w in ceil.waiting() if w.get("ceiling") in ("gates", "reviews")}
    autobug = _autobug_holders(state_dir, checkout, holders)
    wave = _wave_holders(state_dir, holders)
    card = _card_holders(state_dir, checkout, holders)
    now = time.time()
    named = []
    for name in holders:
        text, mtime = _record(_folder(checkout, name))
        kind = classify(name, text, waiting, autobug, wave, card)
        holder = {"name": name, "kind": kind}
        if kind == "doing":
            holder["issue"] = card[name]
        elif kind == "wave":
            holder["wave"] = wave[name]
        elif kind == "unknown" and mtime is not None:
            holder["age_s"] = round(max(0.0, now - mtime), 1)
        named.append(holder)
    return write(pathlib.Path(state_dir) / FILE, at=now,
                 free=C.free_runs(ceil, active, root=checkout), cap=ceil.caps["runs"],
                 todo=int(todo), holders=named)


def write(path, at, free, cap, todo, holders):
    """Append one refusal record and keep the newest `MAX_RECORDS` lines. A holder named twice is written once, and each
    holder keeps the detail its kind carries (`issue`, `wave` or `age_s`).

    The line goes in with one `O_APPEND` write, and the tail prune follows it: two overlapping passes each append their
    own line, so neither drops the line the other wrote. A prune that finds the file inside its bound rewrites nothing;
    one that finds it past the bound replaces it whole, under a temp name and a replace (ADR-0030)."""
    seen, named = set(), []
    for h in holders:
        if h["name"] in seen:
            continue
        seen.add(h["name"])
        named.append(dict(h))
    line = {"at": float(at), "free": int(free), "cap": int(cap), "todo": int(todo), "holders": named}
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:   # O_APPEND: a line another pass wrote is never dropped
        f.write(json.dumps(line, separators=(",", ":")) + "\n")
    _prune(path)
    return line


def _prune(path):
    """Drop the lines past the newest `MAX_RECORDS`. A prune that cannot read or write the file leaves the appended
    line and the file whole: a lost tail is cheaper than a lost record."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_RECORDS:
        return
    try:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.prune")
        tmp.write_text("\n".join(lines[-MAX_RECORDS:]) + "\n")
        tmp.replace(path)      # an interruptible write is atomic (ADR-0030)
    except OSError:
        return


