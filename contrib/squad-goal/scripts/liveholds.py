"""The one read of "which issues a live run holds" (#1829, ADR-0149: one writer per loop fact).

A dispatcher asks this question before it starts a run, and a start asks it before it enters a child. They must answer the
same way: the frontier asked a weaker question than the start's, so it sent one issue out on every cycle and the start
refused it every time. `launch.py` writes the run's request, so its rule is the writer's rule: a run in `active-runs.txt`
whose record carries no verdict holds every issue its request names, and a run with no record yet holds them for its
launch window only.

Two shapes of text name an issue, and both are read here:
- the request's `CHILD - GitHub issue(s) N, M` header, else the first such header, else a plain `issue #N` mention;
- a `spawn_accepted` event's own words. The router writes "issue <owner>/<repo> #N" and "Refs #N", which the first reader
  missed, so a run that had accepted its children still held none of them.

A run's tree and its record are read as they are on disk; this module reads no remote and calls no model.
"""
import json
import pathlib
import re
import time

LAUNCH_WINDOW = 300.0   # a run with no record is live only in its launch window, as launch.py has it


def issue_child_request(txt):
    """True when the deliverable is a local commit on the branch the land reads: the dispatch builders mark those runs with a
    `CHILD - GitHub issue` block, or a `FORK-ONLY` line that a record from before the local land route's retirement still
    carries."""
    return bool(re.search(r"^CHILD[^\n]*GitHub issue", txt, re.M) or re.search(r"^FORK-ONLY", txt, re.M))


def issue_named(txt):
    """The issues a request names, across every `CHILD` block: each block's header list, else the first header anywhere, else
    the plain `issue #N` mention. One header alone would hide the other blocks' issues (#470)."""
    head = r"GitHub issue\(?s?\)? ?#? ?([0-9][0-9, ]*)"
    for pat in (r"^CHILD[^\n]*?" + head, head, r"\bissue #([0-9]+)"):
        found = []
        for m in re.finditer(pat, txt, re.M):
            found += [int(x) for x in re.split(r"[^0-9]+", m.group(1)) if x and int(x) not in found]
        if found:
            return found
    return []


def has_verdict(record_text):
    """True when the record's last invocation ended: a run with a verdict holds nothing (ADR-0167)."""
    for line in record_text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("event") == "verdict":
            return True
    return False


def _issues_in_text(text):
    out = set()
    for pat in (r"\bgithub\s+issues?\s*#?\s*([0-9][0-9,\s]*)", r"\bissues?\s*#\s*(\d+)\b", r"\bgui/(\d+)\b"):
        for m in re.finditer(pat, text, re.IGNORECASE):
            out.update(int(x) for x in re.findall(r"\d+", m.group(1)))
    # the router's own words: "issue owner/repo #N" and "Refs #N"
    for m in re.finditer(r"(?:\bissue|Refs)\s+[\w.-]+/[\w.-]+#\s*(\d+)|(?:\bissue|Refs)\s+#(\d+)", text, re.IGNORECASE):
        out.add(int(m.group(1) or m.group(2)))
    return out


def busy_from_record(record_text):
    """The issues the run's accepted spawns name, however the router wrote them."""
    busy = set()
    for line in record_text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("event") == "spawn_accepted":
            busy |= _issues_in_text(json.dumps(e, ensure_ascii=False))
    return busy


def live_holders(active_path, base=None, now=None, window=LAUNCH_WINDOW):
    """{issue: run folder name}: every issue a live run of the active list holds, by the rule the start enforces (ADR-0150).
    A row that is not an absolute path is read under `base`, the checkout the loop starts runs in."""
    now = time.time() if now is None else now
    try:
        rows = pathlib.Path(active_path).read_text().split()
    except OSError:
        return {}
    held = {}
    for row in rows:
        f = pathlib.Path(row) if pathlib.Path(row).is_absolute() else pathlib.Path(base or ".") / "runs" / row
        rec, rq = f / "record.jsonl", f / "request.txt"
        if rec.exists():
            if has_verdict(rec.read_text()):
                continue
        elif not rq.exists() or now - rq.stat().st_mtime > window:
            continue
        try:
            txt = rq.read_text()
        except OSError:
            continue
        if not issue_child_request(txt):
            continue
        for n in issue_named(txt):
            held.setdefault(n, f.name)
    return held
