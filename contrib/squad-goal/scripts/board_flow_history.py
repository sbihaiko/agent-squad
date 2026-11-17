"""One flow sample per board pass: the cards each kind holds in each column, kept in hub-flow.<owner>_<name>.jsonl (SPEC 111).

Stdlib and board_columns only: no hub and no GitHub client (ADR-0137). Each repository keeps its own file (history_path),
because two profiles on one board see different cards (#894).

The one shared hub-flow.jsonl of before #894 is renamed to hub-flow.jsonl.legacy at first use, and its samples are NOT
migrated into the per-repository files: a legacy sample holds every repository's cards, so no repository's share of it is
known. A repository's cfd starts at its first sample after the upgrade. The legacy bytes stay on disk for anyone who needs
them.

The samples written before the close reason counted (#921) put a card closed as not planned in the Closed band; the
samples from that read on count it in Abandoned. The first append after the upgrade moves that count across in the past
samples once, and writes a flow_rebucket line that records the boundary, so the Closed band takes no step down that a
reader could take for a data error."""
import importlib.util
import itertools
import json
import os
import pathlib
import re
import time
from datetime import UTC, datetime

_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_columns)

DEFAULT_KIND = "issue"
TYPES = ("spec", "task", "bug")   # the lead-time and flow charts' Type (#985)
DEFAULT_TYPE = "task"
RAW_DAYS = 30
DAY = 86400
SHARED_NAME = "hub-flow.jsonl"   # the one history every repository on a board wrote before #894
REBUCKET = "flow_rebucket"       # the line that records the one re-bucket of a history (#921)
ATTEMPT_KEYS = frozenset({"gate_attempt", "gate_reuse"})   # #1255: journal facts, never samples or skips


def history_path(board, repo):
    """The history one repository keeps beside the board cache: hub-flow.<owner>_<name>.jsonl. An owner has no underscore,
    so the name cannot collide with another repository's."""
    return pathlib.Path(board).with_name(f"hub-flow.{repo.replace('/', '_')}.jsonl")


NONE = "none"                    # the board filter's value for a card with no spec (the board's specKey)
_WAVE = re.compile(r"wave:(\d+)$")
SAMPLE_VERSION = 2               # the sample shape with no wave (#2132); a sample with no `v` is the shape before it


def wave_key(labels):
    """The wave the board filter reads: the lowest `wave:NN` label, without leading zeros, or `none` (the board's waveKey).
    The flow sample carries no wave (#2132); the board's Waves filter still reads the label."""
    nums = [int(m[1]) for label in labels or () for m in [_WAVE.match(str(label))] if m]
    return str(min(nums)) if nums else NONE


def kind_key(row):
    """The kind the board's Kinds filter reads (the board's kindKey): `pr` for a pull request row, `bug` for a card
    labelled bug or loop-bug, else `issue`. The project's Kind field (`k`: Task, Spec, Bug ...) is never read: the bar
    offers none of its values, and the board keeps a card by its labels (#904)."""
    if row.get("kind") == "pr": return "pr"
    labels = row.get("l") or ()
    return "bug" if "bug" in labels or "loop-bug" in labels else DEFAULT_KIND


def spec_key(row):
    """The spec the board filter reads: the parent spec's number, or `none` (the board's specKey)."""
    parent = row.get("pa")
    return NONE if parent is None else str(parent)


def type_key(row):
    """The Type the lead-time and flow charts filter by (#985): `spec` for a card labelled spec, else `bug` for one labelled
    bug or loop-bug, else `task`. A card labelled both spec and bug is a spec."""
    labels = row.get("l") or ()
    return "spec" if "spec" in labels else "bug" if "bug" in labels or "loop-bug" in labels else "task"


def sample(rows, now=None):
    """One sample. `cells` holds each card's kind (kind_key), spec, column, frozen flag, count and type (type_key) as
    [kind, spec, column, frozen, n, type]: the charts filter by these (#904, #985). A card in no shipped column has column null.
    A sample written before #2132 carries the card's wave third; a new one carries no wave, and the reader takes both.
    `v` names the shape, so the reader never guesses it from a cell's length: a sample with no `v` is the older shape.
    `counts`, `frozen` and `none` are the totals those cells add up to, so a reader of the older shape still works."""
    names = {c["name"] for c in _columns.view_columns()}
    cells = {}
    for r in rows:
        closed = str(r.get("s", "")).lower() == "closed"
        column, freeze = _columns.view_column(r.get("st"), closed, r.get("l"), r.get("cr"))
        key = (kind_key(r), spec_key(r), column if column in names else None, 1 if freeze else 0, type_key(r))
        cells[key] = cells.get(key, 0) + 1
    listed = sorted(([kind, spec, col, fz, n, typ] for (kind, spec, col, fz, typ), n in cells.items()),
                    key=json.dumps)   # one order, so equal cards are equal cells
    counts, frozen, none = _totals_of(listed, SAMPLE_VERSION)
    return {"v": SAMPLE_VERSION, "t": int(time.time() if now is None else now), "counts": counts, "frozen": frozen,
            "none": none, "cells": listed}


def _version(s):
    """The shape a sample names: its `v`, or 1 for a sample written before the key (#2132)."""
    return s.get("v", 1)


def _cell_parts(cell, version=1):
    """(kind, spec, wave, column, frozen, count, type) of one cell in the shape its sample's `version` names: the shape
    before #2132 carries the wave third, and the shape after it carries no wave. The version, never the length, decides:
    a cell ends in a variable-length type tail."""
    if version < SAMPLE_VERSION:
        kind, spec, wave, column, fz, k, *typ = cell
    else:
        kind, spec, column, fz, k, *typ = cell
        wave = None
    return kind, spec, wave, column, fz, k, typ


def _cell(kind, spec, wave, column, fz, n, typ, version=1):
    """One cell in the shape its sample uses: the wave third for the shape before #2132, else no wave."""
    return [kind, spec, wave, column, fz, n, *typ] if version < SAMPLE_VERSION else [kind, spec, column, fz, n, *typ]


def _totals_of(cells, version=1):
    """(counts, frozen, none) that a list of cells adds up to: counts holds the cards in a shipped column, by kind."""
    counts, frozen, none = {}, 0, 0
    for kind, _spec, _wave, column, fz, n, *_ in (_cell_parts(c, version) for c in cells):
        if column is None:
            none += n
        else:
            by_column = counts.setdefault(kind, {})
            by_column[column] = by_column.get(column, 0) + n
        if fz: frozen += n
    return counts, frozen, none


def append(path, rows, now=None, incomplete=None):
    """Append one sample of `rows` when its counts or its cells differ from the last sample (a card that changes spec,
    kind or type changes the cells and no count, #904). Equal ones write a sample only when no
    sample was written on this UTC day yet: at most one unchanged sample a day, so a quiet day is a point (#894). Old lines
    roll up past RAW_DAYS. `incomplete` is the reason the read is not whole (the caller's completeness signal): such a read
    is never a sample, at any total, and is recorded as a flow_skip line that names the reason. Returns the new sample, or None."""
    path = pathlib.Path(path)
    _retire_legacy(path)
    s = sample(rows, now)
    lines = _read(path)
    if incomplete:
        _record_skip(lines, _total(s), s["t"], incomplete)
        _write(path, _rollup(lines, s["t"]))
        return None
    lines, rebucketed = _rebucket_once(lines, rows, s["t"])
    samples = [obj for _, obj in lines if _is_sample(obj)]
    if samples and samples[-1]["counts"] == s["counts"] and samples[-1].get("cells") == s["cells"] and _day(samples[-1]["t"]) == _day(s["t"]):
        if rebucketed: _write(path, _rollup(lines, s["t"]))
        return None
    lines.append((json.dumps(s), s))
    _write(path, _rollup(lines, s["t"]))
    return s


def _rebucket_once(lines, rows, now):
    """(lines, changed): the past samples with the cards closed as not planned moved from Closed to Abandoned, once (#921).

    A history with samples was written by a read that counted such a card in Closed. The samples from this read on count
    it in Abandoned, so without the move the Closed band steps down by that count at the boundary. A flow_rebucket line
    records the boundary, and a history that carries one is never touched again: a later close is already Abandoned in
    every sample, so moving it across would be false. A history with no sample yet has nothing to re-bucket."""
    if any(_is_rebucket(obj) for _, obj in lines) or not any(_is_sample(obj) for _, obj in lines):
        return lines, False
    n = sum(1 for r in rows
            if str(r.get("s", "")).lower() == "closed" and _columns.not_planned(r.get("cr")))
    if n:
        moved = []
        for text, obj in lines:
            if _is_sample(obj):
                obj = _rebucket_sample(obj, n)
                text = json.dumps(obj)
            moved.append((text, obj))
        lines = moved
    mark = {REBUCKET: n, "t": int(now)}
    lines = [*lines, (json.dumps(mark), mark)]
    return lines, True


def _rebucket_sample(s, n):
    """One sample with up to n closed cards moved from Closed to Abandoned, taken from the kinds that hold Closed."""
    closed, abandoned = _columns.ROLE["issue_closed"], _columns.ROLE["abandoned"]
    if "cells" in s:
        version = _version(s)
        moved, left = {}, n
        for cell in s["cells"]:
            kind, spec, wave, column, fz, k, typ = _cell_parts(cell, version)
            take = min(k, left) if column == closed else 0
            left -= take
            for key, k_taken in (((kind, spec, wave, abandoned, fz, tuple(typ)), take),
                                 ((kind, spec, wave, column, fz, tuple(typ)), k - take)):
                if k_taken:
                    moved[key] = moved.get(key, 0) + k_taken
        cells = [_cell(*key[:5], k_taken, key[5], version) for key, k_taken in moved.items()]
        counts, frozen, none = _totals_of(cells, version)
        return {**s, "counts": counts, "frozen": frozen, "none": none, "cells": cells}
    counts, left = {k: dict(v) for k, v in s["counts"].items()}, n
    for by_column in counts.values():
        if not left: break
        take = min(by_column.get(closed, 0), left)
        if take:
            by_column[closed] -= take
            by_column[abandoned] = by_column.get(abandoned, 0) + take
            left -= take
    return {**s, "counts": counts}


def _record_skip(lines, total, t, reason):
    """One flow_skip line per UTC day and reason (#894): a later skip of the same day and reason updates that line in place,
    with its latest time and count. A skip of another reason, or of another day, is a line of its own."""
    day = _day(t)
    skip = {"flow_skip": total, "t": t, "reason": reason}
    for i, (_, obj) in enumerate(lines):
        if _is_skip(obj) and obj.get("reason") == reason and _day(obj["t"]) == day:
            lines[i] = (json.dumps(skip), skip)
            return
    lines.append((json.dumps(skip), skip))


def _total(s):
    return sum(n for by_column in s["counts"].values() for n in by_column.values()) + s["none"]


_RETIRED = set()   # the shared files this process has already renamed: an old hub may write the shared file again (#894)


def _retire_legacy(path):
    """At first use of a repository's history in this process, the shared hub-flow.jsonl is renamed to hub-flow.jsonl.legacy:
    its bytes stay, and nothing is lost. A legacy file already there takes the shared bytes after its own, and the shared file
    goes, so the folder never holds a numbered file. A file another process renamed first is no error. A hub that still runs
    the old code writes the shared file again; this process retires it once only, so a restart is the only thing that can
    add legacy bytes."""
    shared = path.with_name(SHARED_NAME)
    if str(shared) in _RETIRED or not shared.exists():
        return
    _RETIRED.add(str(shared))
    target = shared.with_name(f"{SHARED_NAME}.legacy")
    try:
        if target.exists():
            with target.open("ab") as legacy:
                legacy.write(shared.read_bytes())
            shared.unlink()
        else:
            os.replace(shared, target)
    except FileNotFoundError:
        pass


def samples(path):
    return [obj for _, obj in _read(pathlib.Path(path)) if _is_sample(obj)]


def skips(path):
    """The flow_skip records of one history: a read that was not whole, with the day it fell on (#894)."""
    return [obj for _, obj in _read(pathlib.Path(path)) if _is_skip(obj)]


def _is_sample(obj):
    return isinstance(obj, dict) and isinstance(obj.get("t"), (int, float)) and isinstance(obj.get("counts"), dict)


def _is_skip(obj):
    return isinstance(obj, dict) and isinstance(obj.get("t"), (int, float)) and "flow_skip" in obj


def _is_rebucket(obj):
    return isinstance(obj, dict) and REBUCKET in obj


def _read(path):
    """Every line as (text, parsed). A line of another shape keeps its text and is never rolled up."""
    if not path.exists(): return []
    out = []
    for text in path.read_text().splitlines():
        if not text.strip(): continue
        try: obj = json.loads(text)
        except ValueError: obj = None
        out.append((text, obj))
    return out


def _rollup(lines, now):
    """Past RAW_DAYS, the last sample and the last flow_skip of each UTC day stay; the other lines of that day go.
    Gate attempt and gate reuse records (#1255) are facts, not samples: the old set filters on the record's
    kind, and they never enter it -- whatever a future sample looks like, an attempt record always stays, so
    the Gate Tries projection replays the whole attempt history however old it grows."""
    cutoff = now - RAW_DAYS * DAY
    old = [(i, (_is_sample(obj), _day(obj["t"]))) for i, (_, obj) in enumerate(lines)
           if (_is_sample(obj) or _is_skip(obj)) and obj.get("k") not in ATTEMPT_KEYS and obj["t"] < cutoff]
    last_of_day = {key: i for i, key in old}
    drop = {i for i, key in old if last_of_day[key] != i}
    return [line for i, line in enumerate(lines) if i not in drop]


def _day(t):
    return datetime.fromtimestamp(t, UTC).date()


_WRITE_SEQ = itertools.count()   # a thread id is reused once its thread ends, so two passes could share a temp name


def _write(path, lines):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{next(_WRITE_SEQ)}.tmp")
    tmp.write_text("".join(text + "\n" for text, _ in lines))
    os.replace(tmp, path)
