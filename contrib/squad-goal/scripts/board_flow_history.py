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
import time
from datetime import UTC, datetime

_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_columns)

DEFAULT_KIND = "issue"
RAW_DAYS = 30
DAY = 86400
SHARED_NAME = "hub-flow.jsonl"   # the one history every repository on a board wrote before #894
REBUCKET = "flow_rebucket"       # the line that records the one re-bucket of a history (#921)


def history_path(board, repo):
    """The history one repository keeps beside the board cache: hub-flow.<owner>_<name>.jsonl. An owner has no underscore,
    so the name cannot collide with another repository's."""
    return pathlib.Path(board).with_name(f"hub-flow.{repo.replace('/', '_')}.jsonl")


def sample(rows, now=None):
    names = {c["name"] for c in _columns.view_columns()}
    counts, frozen, none = {}, 0, 0
    for r in rows:
        closed = str(r.get("s", "")).lower() == "closed"
        column, freeze = _columns.view_column(r.get("st"), closed, r.get("l"), r.get("cr"))
        if freeze: frozen += 1
        if column not in names:
            none += 1
            continue
        by_column = counts.setdefault(r.get("k") or DEFAULT_KIND, {})
        by_column[column] = by_column.get(column, 0) + 1
    return {"t": int(time.time() if now is None else now), "counts": counts, "frozen": frozen, "none": none}


def append(path, rows, now=None, incomplete=None):
    """Append one sample of `rows` when its counts differ from the last sample. Equal counts write a sample only when no
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
    if samples and samples[-1]["counts"] == s["counts"] and _day(samples[-1]["t"]) == _day(s["t"]):
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
    counts, left = {k: dict(v) for k, v in s["counts"].items()}, n
    closed, abandoned = _columns.ROLE["issue_closed"], _columns.ROLE["abandoned"]
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
    its bytes stay, and nothing is deleted. A legacy name already taken is never overwritten. A file another process renamed
    first is no error. A hub that still runs the old code writes the shared file again; this process renames it once only,
    so a restart is the only thing that can add a legacy file."""
    shared = path.with_name(SHARED_NAME)
    if str(shared) in _RETIRED or not shared.exists():
        return
    _RETIRED.add(str(shared))
    target = shared.with_name(f"{SHARED_NAME}.legacy")
    if target.exists():
        target = shared.with_name(f"{SHARED_NAME}.legacy.{os.getpid()}.{time.time_ns()}")
    try:
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
    """Past RAW_DAYS, the last sample and the last flow_skip of each UTC day stay; the other lines of that day go."""
    cutoff = now - RAW_DAYS * DAY
    old = [(i, (_is_sample(obj), _day(obj["t"]))) for i, (_, obj) in enumerate(lines)
           if (_is_sample(obj) or _is_skip(obj)) and obj["t"] < cutoff]
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
