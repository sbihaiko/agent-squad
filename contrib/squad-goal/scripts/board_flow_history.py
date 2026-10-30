"""One flow sample per board pass: the cards each kind holds in each column, kept in hub-flow.jsonl (SPEC 111).

Stdlib and board_columns only: no hub and no GitHub client (ADR-0137). Each repository keeps its own file (history_path),
because two profiles on one board see different cards (#894)."""
import importlib.util
import json
import os
import pathlib
import threading
import time
from datetime import UTC, datetime

_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_columns)

DEFAULT_KIND = "issue"
RAW_DAYS = 30
DAY = 86400
SHARED_NAME = "hub-flow.jsonl"   # the one history every repository on a board wrote before #894


def history_path(board, repo):
    """The history one repository keeps beside the board cache: hub-flow.<owner>_<name>.jsonl. An owner has no underscore,
    so the name cannot collide with another repository's."""
    return pathlib.Path(board).with_name(f"hub-flow.{repo.replace('/', '_')}.jsonl")


def sample(rows, now=None):
    names = {c["name"] for c in _columns.view_columns()}
    counts, frozen, none = {}, 0, 0
    for r in rows:
        closed = str(r.get("s", "")).lower() == "closed"
        column, freeze = _columns.view_column(r.get("st"), closed, r.get("l"))
        if freeze: frozen += 1
        if column not in names:
            none += 1
            continue
        by_column = counts.setdefault(r.get("k") or DEFAULT_KIND, {})
        by_column[column] = by_column.get(column, 0) + 1
    return {"t": int(time.time() if now is None else now), "counts": counts, "frozen": frozen, "none": none}


def append(path, rows, now=None, incomplete=None):
    """Append one sample of `rows` when its counts differ from the last sample, rolling up old lines past RAW_DAYS.
    `incomplete` is the reason the read is not whole (the caller's completeness signal): such a read is never a sample,
    at any total, and is recorded as a flow_skip line that names the reason. Returns the new sample, or None."""
    path = pathlib.Path(path)
    _retire_legacy(path)
    s = sample(rows, now)
    lines = _read(path)
    if incomplete:
        skip = {"flow_skip": _total(s), "t": s["t"], "reason": incomplete}
        lines.append((json.dumps(skip), skip))
        _write(path, _rollup(lines, s["t"]))
        return None
    samples = [obj for _, obj in lines if _is_sample(obj)]
    if samples and samples[-1]["counts"] == s["counts"]: return None
    lines.append((json.dumps(s), s))
    _write(path, _rollup(lines, s["t"]))
    return s


def _total(s):
    return sum(n for by_column in s["counts"].values() for n in by_column.values()) + s["none"]


def _retire_legacy(path):
    """At first use of a repository's history, the shared hub-flow.jsonl is renamed to hub-flow.jsonl.legacy: its bytes stay,
    and nothing is deleted. A legacy name already taken is never overwritten. A file another process renamed first is no error."""
    shared = path.with_name(SHARED_NAME)
    if not shared.exists():
        return
    target = shared.with_name(f"{SHARED_NAME}.legacy")
    if target.exists():
        target = shared.with_name(f"{SHARED_NAME}.legacy.{os.getpid()}.{time.time_ns()}")
    try:
        os.replace(shared, target)
    except FileNotFoundError:
        pass


def samples(path):
    return [obj for _, obj in _read(pathlib.Path(path)) if _is_sample(obj)]


def _is_sample(obj):
    return isinstance(obj, dict) and isinstance(obj.get("t"), (int, float)) and isinstance(obj.get("counts"), dict)


def _is_skip(obj):
    return isinstance(obj, dict) and isinstance(obj.get("t"), (int, float)) and "flow_skip" in obj


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


def _write(path, lines):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text("".join(text + "\n" for text, _ in lines))
    os.replace(tmp, path)
