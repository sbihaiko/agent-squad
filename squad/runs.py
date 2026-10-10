"""Find a run folder for `squad dashboard`. It reads records and never starts a run.

A run folder holds `record.jsonl`, or `run/record.jsonl` (a run that a harness wrapped with a graph and a target).
Runs are numbered from 1, oldest first, so a number stays the same when a newer run appears.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

PAGE = Path(__file__).resolve().parent.parent / "dashboard" / "agent-squad-run-dashboard.html"


class RunNotFound(Exception):
    pass


@dataclass(frozen=True)
class Run:
    number: int
    folder: Path
    record: Path
    run_id: str | None
    started: float


def _record(folder):
    for rel in ("record.jsonl", "run/record.jsonl"):
        if (folder / rel).is_file():
            return folder / rel
    return None


def _start(record):
    """(run_id, start time) from the first event; the file time when the first line has no `ts`."""
    try:
        with record.open() as f:
            first = json.loads(f.readline())
        run_id, ts = first.get("run_id"), first.get("ts")
    except (OSError, ValueError, AttributeError):
        run_id, ts = None, None
    return run_id, float(ts) if isinstance(ts, (int, float)) else record.stat().st_mtime


def request_of(folder, width=60):
    """The request a run started from, cut to `width` characters: the text `with_request` appended to the entry agent in the run's graph.json."""
    try:
        g = json.loads((Path(folder) / "graph.json").read_text())
    except (OSError, ValueError):
        return ""
    for n in g.get("nodes", []):
        m = re.search(r"\n\n(?:Work|Task):\n(.*)\Z", n.get("instructions") or "", re.S)
        if m:
            text = " ".join(m.group(1).split())
            return text if len(text) <= width else text[:width - 1] + "…"
    return ""


def list_runs(runs_dir):
    found = []
    root = Path(runs_dir)
    for d in (sorted(root.iterdir()) if root.is_dir() else []):
        rec = _record(d) if d.is_dir() else None
        if rec is not None:
            found.append((_start(rec), d, rec))
    found.sort(key=lambda t: (t[0][1], t[1].name))
    return [Run(i, d.resolve(), rec.resolve(), rid, ts) for i, ((rid, ts), d, rec) in enumerate(found, 1)]


def find(every, name):
    """The run whose folder name or run id is `name` (a run's name is both, #188); a name wins over a number."""
    return next((r for r in every if name in (r.folder.name, r.run_id)), None)


def resolve(runs_dir, ref=None):
    """The run named by `ref`: None is the latest; a folder name, a run id or a run name; else digits are a number."""
    every = list_runs(runs_dir)
    if not every:
        raise RunNotFound(f"no runs under {runs_dir}")
    if ref is None or ref == "":
        return every[-1]
    text = str(ref)
    named = find(every, text)
    if named:
        return named
    if text.isdigit() and 1 <= int(text) <= len(every):
        return every[int(text) - 1]
    raise RunNotFound(f"no run {text!r}: {runs_dir} has {len(every)} run{'s' if len(every) != 1 else ''} (1-{len(every)}); `squad dashboard --list` shows them")


def page_url(run):
    """The dashboard page for one run. A page cannot read a folder it was not given, so the URL carries the folder
    for the page to show and to check against the folder the person connects."""
    return "file://" + quote(str(PAGE)) + "#run=" + quote(str(run.folder))
