"""The ticket profiler's dataset (SPEC 1035): one append-only file per ticket, one profile row per line.

A row is validated by the row contract in `squad/profiler.py` before it is written, and a row whose key is already
in its ticket's file is refused without a write. The file name is a digest of the ticket, so it names no issue
number, date, count, status or run. A write forms the whole file in a temporary sibling and renames it over the
old one (ADR-0030): an interrupted write leaves the previous file, whole lines only.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from squad import profiler


def path(root, issue):
    """The ticket's file under `root`: a digest of the ticket, never its number."""
    return Path(root) / (hashlib.sha256(str(issue).encode()).hexdigest() + ".jsonl")


def read(root, issue):
    """The ticket's rows in write order; none when the ticket has no file."""
    p = path(root, issue)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_bytes().splitlines()]


def append_row(root, row):
    """Append one row to its ticket's file. Return False and change nothing when its key is already there."""
    profiler.validate(row)
    p = path(root, row["issue"])
    existing = p.read_bytes() if p.exists() else b""
    if any(json.loads(line)["key"] == row["key"] for line in existing.splitlines()):
        return False
    _replace(p, existing + json.dumps(row, sort_keys=True).encode() + b"\n")
    return True


def _replace(p, content):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
