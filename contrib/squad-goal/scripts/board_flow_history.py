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


def append(path, rows, now=None):
    """Append one sample of `rows` when its counts differ from the last sample, rolling up samples older than RAW_DAYS.
    A read under half the last sample's cards is a truncated read: it is recorded as a flow_skip line, not a sample, and
    a second read that repeats it is taken as the truth. Returns the new sample, or None when nothing was appended."""
    path = pathlib.Path(path)
    s = sample(rows, now)
    lines = _read(path)
    samples = [obj for _, obj in lines if _is_sample(obj)]
    if samples and samples[-1]["counts"] == s["counts"]: return None
    total = _total(s)
    if samples and total * 2 < _total(samples[-1]) and not _repeats_skip(lines, total):
        skip = {"flow_skip": total, "t": s["t"], "reason": f"{total} cards read, {_total(samples[-1])} in the last sample"}
        lines.append((json.dumps(skip), skip))
        _write(path, lines)
        return None
    lines.append((json.dumps(s), s))
    _write(path, _rollup(lines, s["t"]))
    return s


def _total(s):
    return sum(n for by_column in s["counts"].values() for n in by_column.values()) + s["none"]


def _repeats_skip(lines, total):
    """True when the last line is a flow_skip that named the same total: the read is the board's, not a truncation."""
    last = lines[-1][1] if lines else None
    return isinstance(last, dict) and last.get("flow_skip") == total


def samples(path):
    return [obj for _, obj in _read(pathlib.Path(path)) if _is_sample(obj)]


def _is_sample(obj):
    return isinstance(obj, dict) and isinstance(obj.get("t"), (int, float)) and isinstance(obj.get("counts"), dict)


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
    cutoff = now - RAW_DAYS * DAY
    old = [i for i, (_, obj) in enumerate(lines) if _is_sample(obj) and obj["t"] < cutoff]
    last_of_day = {_day(lines[i][1]["t"]): i for i in old}
    drop = {i for i in old if last_of_day[_day(lines[i][1]["t"])] != i}
    return [line for i, line in enumerate(lines) if i not in drop]


def _day(t):
    return datetime.fromtimestamp(t, UTC).date()


def _write(path, lines):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text("".join(text + "\n" for text, _ in lines))
    os.replace(tmp, path)
