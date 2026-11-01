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

from squad import modelreg, profiler

NO_PROFILE = "no profile"


def path(root, issue):
    """The ticket's file under `root`: a digest of the ticket, never its number."""
    return Path(root) / (hashlib.sha256(str(issue).encode()).hexdigest() + ".jsonl")


def read(root, issue):
    """The ticket's rows in write order; none when the ticket has no file."""
    p = path(root, issue)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_bytes().splitlines()]


def card(root, issue):
    """The ticket's three card numbers: total tokens, attempts, winning model. `NO_PROFILE` when it has no rows."""
    rows = read(root, issue)
    if not rows:
        return NO_PROFILE
    return {"total_tokens": _total(rows), "attempts": len(rows), "winning_model": _winner(rows)}


def _total(rows):
    """The tokens in, out and cache of every row that ran a session; None when a row that ran one has no count."""
    total = 0
    for r in rows:
        if r["tokens_src"] == "missing":
            if r["outcome"] in ("refused", "skipped") or r["model_id"] == profiler.NO_MODEL:
                continue
            return None
        if r["tokens_in"] is None or r["tokens_out"] is None:
            return None
        total += r["tokens_in"] + r["tokens_out"] + (r["tokens_cache"] or 0)
    return total


def _winner(rows):
    """The model of the last accepted row that ran a model, in write order; None when no row won."""
    win = None
    for r in rows:
        if r["outcome"] == "accepted" and r["model_id"] != profiler.NO_MODEL:
            win = r["model_id"]
    return win


def append_row(root, row):
    """Append one row to its ticket's file. Return False and change nothing when its key is already there."""
    profiler.validate(row)
    p = path(root, row["issue"])
    existing = p.read_bytes() if p.exists() else b""
    if any(json.loads(line)["key"] == row["key"] for line in existing.splitlines()):
        return False
    _replace(p, existing + json.dumps(row, sort_keys=True).encode() + b"\n")
    return True


def _billing(model_id, reg):
    """How a row's call was paid: the auth of the route its spelling resolves to, `none` for a node that ran no model,
    `unknown` for a spelling the registry does not hold."""
    if model_id == profiler.NO_MODEL:
        return "none"
    ref = modelreg.normalize(model_id, reg)
    entry = reg.entries.get(ref.id) if ref.known else None
    route = entry.routes.get(ref.route) if entry and ref.route else None
    return route["auth"] if route else "unknown"


def _sum(values):
    """The sum of the counts the record holds; None when it holds none, never a zero."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def chain_summary(root, issues, reg=None):
    """The tickets' rows grouped by chain, phase and billing. A phase run is counted in the group of its first attempt,
    and its first try is accepted. A ticket with no rows adds no group."""
    reg = reg or modelreg.default_registry()
    groups = {}
    for issue in issues:
        for r in read(root, issue):
            g = groups.setdefault((r["chain_id"], r["phase"], _billing(r["model_id"], reg)),
                                  {"rows": [], "phase_runs": 0, "first_try": 0})
            g["rows"].append(r)
            if r["attempt"] == 1:
                g["phase_runs"] += 1
                if r["outcome"] == "accepted":
                    g["first_try"] += 1
    out = []
    for (chain_id, phase, billing), g in sorted(groups.items()):
        rows = g["rows"]
        out.append({"chain_id": chain_id, "phase": phase, "billing": billing, "attempts": len(rows),
                    "phase_runs": g["phase_runs"], "first_try_rate": g["first_try"] / g["phase_runs"]
                    if g["phase_runs"] else None,
                    "tokens_in": _sum(r["tokens_in"] for r in rows),
                    "tokens_out": _sum(r["tokens_out"] for r in rows),
                    "tokens_cache": _sum(r["tokens_cache"] for r in rows),
                    "tokens_missing": sum(r["tokens_src"] == "missing" for r in rows)})
    return out


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
