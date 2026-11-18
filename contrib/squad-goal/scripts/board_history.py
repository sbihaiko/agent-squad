"""One history store per repository: each card's column, hold and spec intervals, kept in hub-history.<owner>_<name>.jsonl (#1023).

Stdlib and board_columns only: no hub and no GitHub client (ADR-0137). The hub fetches the payloads and calls this module,
which decides what to read, keeps what it read, and counts the GraphQL points it spent. The file is append-only: one record
per line, and the last issue record wins. board_flow_history.py is the flow-sample journal and stays a separate store.
"""
import importlib.util
import itertools
import json
import os
import pathlib
from datetime import UTC, datetime

_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_columns)
view_column = _columns.view_column

BATCH_SIZE = 1          # one issue per timeline query: the only size the API check measured complete (docs/board-history-coverage.md)
POINTS_CEILING = 4000   # GraphQL points the hub may spend an hour; GitHub allows 5000, the rest is headroom
WINDOW = 3600           # seconds in the points hour and between hourly searches
SEARCH_EVERY = 3600
PARKED = _columns.ROLE["unscheduled"]


def history_path(board, repo):
    """The history one repository keeps beside the board cache: hub-history.<owner>_<name>.jsonl."""
    return pathlib.Path(board).with_name(f"hub-history.{repo.replace('/', '_')}.jsonl")


def item_facts(issue, project):
    """The clocks one issue carries on one project: the Status field's updatedAt (the Status clock), the issue's own, and the
    Status value the card carries now."""
    items = [i for i in (issue.get("projectItems") or {}).get("nodes", []) if (i.get("project") or {}).get("number") == project]
    status = max((((i.get("fieldValueByName") or {}).get("updatedAt")) or "" for i in items), default="")
    value = max((((i.get("fieldValueByName") or {}).get("name")) or "" for i in items), default="")
    return {"n": issue["number"], "issue_updated": issue["updatedAt"], "status_updated": status, "status": value}


def last_status(rec):
    """The last Status a record knows the card held, "" when it knows none."""
    for e in reversed(rec.get("events") or []):
        if e.get("type") == "status": return e.get("status") or ""
    return ""


def last_closed(rec):
    """True when the record's last close or reopen event closed the card, False when that event reopened it or the record
    holds none: a card the record never saw close stands open."""
    for e in reversed(rec.get("events") or []):
        if e.get("type") == "closed": return True
        if e.get("type") == "reopened": return False
    return False


def due_reads(items, stored):
    """The numbers to read: a card with no complete record, one whose Status moved past its record, one whose record
    stops before the Status the card carries now, or one whose closed state disagrees with the record's last close or
    reopen. Neither the item's nor the issue's own updatedAt is a reason: the first moves on writes that touch no Status,
    the second on any edit. The third rule is what a lagging Status clock leaves behind (#1394): a timeline read while
    GitHub had not yet published a move is complete by its own page cursor, and the clock that stamps the move can agree
    with the record's, so only the value itself says the record is behind. The fourth is what a close that moves no card
    leaves behind (#1419): the issue closes through the API and its card keeps its Status, so no clock moves and the
    record never reads the ClosedEvent. Every item states its closed flag (`closed`, a required bool; a caller that cannot
    say the issue's state must not call this function): it is compared with the record's last close or reopen, and only a
    disagreement is a reason."""
    return [i["n"] for i in items
            if (rec := stored.get(i["n"])) is None
            or not rec.get("complete")
            or i["status_updated"] > rec["status_updated"]
            or bool(i.get("status")) and last_status(rec) != i["status"]
            or bool(i["closed"]) != last_closed(rec)]


def due_items(items, stored):
    """The numbers a history pass reads, from the board's own rows. A row states the issue's number (`n`), the Status
    value (`st`), the Status field's updatedAt (`su`), and the issue's own state (`s`). The closed flag comes from the
    issue state -- the value the issue API answers and the items read carries as `content.state` -- never from the
    card's Status (`st`), which a close through the API leaves where it was (#1419). The Status value is the row's own
    reason to read: a record whose clock already agrees with the row, and whose events stop at an older Status, is due
    on the value alone (#1394, #2313). Every row states its state; a row that names none is read once and then stands
    open against its record."""
    return due_reads([{"n": i["n"], "status_updated": i.get("su") or "", "status": i.get("st") or "",
                       "closed": str(i.get("s") or "").lower() == "closed"} for i in items], stored)


def backfill_batch(numbers, store):
    """The next numbers still short of a complete timeline, one query's worth; a resumed backfill picks up where it stopped."""
    return [n for n in numbers if not (store.get(n) or {}).get("complete")][:BATCH_SIZE]


def timeline_complete(page):
    """True when the page is the last one of its timeline."""
    return not (page.get("pageInfo") or {}).get("hasNextPage", False)


def timeline_after(rec):
    """The cursor the next read resumes from while a timeline is short; None means from the start."""
    return None if rec is None or rec.get("complete") else rec.get("cursor")


def _event(node):
    kind, t = node.get("__typename"), node.get("createdAt")
    if kind == "ProjectV2ItemStatusChangedEvent": return {"t": t, "type": "status", "status": node.get("status") or ""}
    if kind == "LabeledEvent": return {"t": t, "type": "label", "name": node["label"]["name"]}
    if kind == "UnlabeledEvent": return {"t": t, "type": "unlabel", "name": node["label"]["name"]}
    if kind == "ClosedEvent": return {"t": t, "type": "closed"}
    if kind == "ReopenedEvent": return {"t": t, "type": "reopened"}
    if kind == "AddedToProjectV2Event": return {"t": t, "type": "added", "project": (node.get("project") or {}).get("number")}
    if kind == "RemovedFromProjectV2Event": return {"t": t, "type": "removed", "project": (node.get("project") or {}).get("number")}
    if kind == "ParentIssueAddedEvent": return {"t": t, "type": "spec", "spec": (node.get("parent") or {}).get("number")}
    if kind == "ParentIssueRemovedEvent": return {"t": t, "type": "spec", "spec": None}
    return None


def events_of(issue):
    """The events one issue's timeline page holds, in the order GitHub gave them."""
    return [e for e in map(_event, issue["timelineItems"]["nodes"]) if e]


def record_page(store, number, page, issue_updated, status_updated, project=None):
    """Merge one timeline page into the in-memory store: its events follow the stored ones, and its end cursor resumes the
    next read. `project` names the board this record is kept for: an add or a removal that names another project is that
    board's move, never this card's, so a card taken off another board never leaves this one (#1663)."""
    prev = store.get(number, {})
    events = list(prev.get("events", [])) + [e for e in map(_event, page["nodes"])
                                             if e and not (project is not None and e.get("project") not in (None, project))]
    complete = timeline_complete(page)
    cursor = None if complete else (page.get("pageInfo") or {}).get("endCursor")
    store[number] = {"issue_updated": issue_updated, "status_updated": status_updated, "events": events,
                     "complete": complete, "cursor": cursor}


def _apply(state, e):
    kind = e["type"]
    if kind == "status": state["status"] = e["status"]
    elif kind == "label": state["labels"].add(e["name"])
    elif kind == "unlabel": state["labels"].discard(e["name"])
    elif kind == "closed": state["closed"] = True
    elif kind == "reopened": state["closed"] = False
    elif kind == "added": state["on_board"] = True
    elif kind in ("removed", "gone"): state["on_board"] = False
    elif kind == "spec": state["spec"] = e["spec"]


def _key(e):
    """The identity of one recorded event: a move seen twice in one poll, or read again on a re-read, is one move."""
    return (e.get("t"), e.get("type"), e.get("status"), e.get("name"), e.get("spec"))


def intervals(events):
    """Each run of one state between events: its column, hold and spec, and whether the card is on the board. Equal
    neighbours merge, and the last run is open (to None). Events sort by their UTC instant, and a repeated event counts once.
    A run is `unknown` when the card took its column strictly before its add: the instant it took the column is not here."""
    state = {"status": "", "status_t": None, "closed": False, "labels": set(), "spec": None, "on_board": False}
    out, seen = [], set()
    for _, e in sorted(enumerate(events), key=lambda p: (_instant(p[1]["t"]), p[0])):
        if _key(e) in seen:
            continue
        seen.add(_key(e))
        _apply(state, e)
        if e["type"] == "status": state["status_t"] = _instant(e["t"])
        column, hold = None, None
        if state["on_board"] and state["status"]:
            column, hold = view_column(_columns.recorded_column(state["status"]), state["closed"], sorted(state["labels"]), None)   # history keeps the names it recorded (#2190)
        snap = {"on_board": state["on_board"], "column": column, "hold": hold, "spec": state["spec"]}
        if out and {k: out[-1][k] for k in snap} == snap:
            continue
        if out: out[-1]["to"] = e["t"]
        out.append({"from": e["t"], "to": None, **snap, "approx": e["type"] == "gone",
                    "unknown": e["type"] == "added" and bool(state["status"]) and state["status_t"] < _instant(e["t"])})
    return out


def labels_now(events):
    """The labels the card carries after its last event: a label added and never removed is one it holds now. The flow
    charts read the mark a card carries, such as `moved` (#1663)."""
    held = set()
    for e in sorted((e for e in events if e.get("type") in ("label", "unlabel")), key=lambda e: _instant(e["t"])):
        if e["type"] == "label": held.add(e.get("name"))
        else: held.discard(e.get("name"))
    return held


ROLE = _columns.ROLE
def _in_scope(s, spec):
    return bool(s["on_board"]) and s["spec"] == spec


TERMINAL = (_columns.ROLE["issue_closed"], _columns.ROLE["abandoned"])
GATING = _columns.ROLE["gating"]


def column_stretches(runs, lo, hi):
    """Each stretch one card spends in one column inside `lo`..`hi`, in order, as `{"column", "hours"}`. Contiguous runs
    that name the same column are one stretch: a freeze or a spec change ends an interval without changing the column
    (SPEC 1218). A run off the board, or parked with no hold, is no stretch at all, and it breaks the contiguity: the
    card was not in a column then. A parked run with a hold is a stretch in its own column (Backlog): the measure counts
    it as held time, so the stack keeps it and its band names a real board column with a palette role (#1256)."""
    out, joined = [], False
    for r in runs:
        if not r["on_board"] or (r["column"] in (None, PARKED) and r["hold"] is None):
            joined = False
            continue
        a, b = max(_instant(r["from"]), lo), min(_instant(r["to"]) if r["to"] else hi, hi)
        if b <= a:
            joined = False
            continue
        hours = (b - a).total_seconds() / 3600
        if joined and out[-1]["column"] == r["column"]:
            out[-1]["hours"] += hours
        else:
            out.append({"column": r["column"], "hours": hours})
        joined = True
    return out


def _on_board_hours(runs, lo, hi):
    """Each on-board run inside `lo`..`hi`, in order, as (column, hold, hours): the run's start and end clamped to the
    window, and a run that names no interval left out. The one walk both the lead observation and the board time sum, so
    the column and gap accounting has a single source (SPEC 1218, #1354)."""
    for r in runs:
        if not r["on_board"]:
            continue
        a, b = max(_instant(r["from"]), lo), min(_instant(r["to"]) if r["to"] else hi, hi)
        if b <= a:
            continue
        yield r["column"], r["hold"], (b - a).total_seconds() / 3600


def lead_observation(runs, start=None, end=None, complete=True, now=None):
    """One card's lead observation: the flowing and held hours from its FIRST entry into `start` (a return there never
    restarts the clock) to its last entry into `end`, or to `now` while it is still open (censored). None when the card
    never entered `start`, or when it left the board or closed without an entry into `end`, or when it is open and no `now`
    is given. None `start` is the card's first time on the board; None `end` is Closed. Backlog with no hold is the
    parked time: it counts in neither. `start_lo`..`start_hi` is the interval the start is known in: one instant for a
    recorded move, two when a search set the boundary. `unknown_first` marks a card whose column was set before its add,
    or a timeline not yet read whole: its start is not in the store. `stretches` is the time the card spent in each
    column inside the window, and `columns` sums it by column for the chart's stack; `gate` is the Gating share (#1256).
    Both the measure and the stack walk the same slice, `runs[k:]` from the first entry into `start`, clamped to
    `lo`..`hi`: the card's time before the start column and after the end transition is in neither, so the stack's
    total always equals `flowing` + `held`."""
    end = end or _columns.ROLE["issue_closed"]
    k = next((i for i, r in enumerate(runs) if (r["on_board"] if start is None else r["column"] == start)), None)
    if k is None:
        return None
    entered = runs[k]
    lo = _instant(entered["from"])
    closed = next((r for r in reversed(runs) if r["column"] == end), None)
    censored = closed is None or _instant(closed["from"]) < lo
    if censored:
        last = runs[-1]
        if now is None or not last["on_board"] or last["column"] in TERMINAL:
            return None
        hi = _instant(now) if isinstance(now, str) else now.astimezone(UTC)
    else:
        hi = _instant(closed["from"])
    flowing = held = 0.0
    for column, hold, hours in _on_board_hours(runs[k:], lo, hi):
        if column in (None, PARKED) and hold is None:
            continue
        if hold: held += hours
        else: flowing += hours
    start_lo = runs[k - 1]["from"] if entered["approx"] and k > 0 else entered["from"]
    stretches = column_stretches(runs[k:], lo, hi)
    columns = {}
    for s in stretches:
        columns[s["column"]] = columns.get(s["column"], 0.0) + s["hours"]
    return {"start": entered["from"], "end": None if censored else closed["from"], "flowing": flowing, "held": held,
            "stretches": stretches, "columns": columns, "gate": columns.get(GATING, 0.0),
            "approx": any(r["approx"] and lo < _instant(r["from"]) <= hi for r in runs),
            "unknown_first": not complete or bool(next((r["unknown"] for r in runs if r["on_board"]), False)),
            "censored": censored, "start_lo": start_lo, "start_hi": entered["from"]}


def board_time(runs, end=None, complete=True):
    """One card's time on the board, from its FIRST time on the board to its last entry into `end` (Closed by default):
    every hour it spent there counts, the parked Backlog wait included (#1354). The lead-time chart uses it only for the
    view's default start column (Todo): a closed card the history read that never entered Todo is measured from its board
    entry instead, whatever its Type, because a spec is a document and its card moves with its tickets (ADR-0142), and a
    bug or a task can be born or moved past that column too. The start is the only substitute: `end` stays the reader's
    choice, so a card with no entry into `end` is not measured at all, and a start the reader chose by hand never reaches
    this function (#1248). The shape is `lead_observation`'s, uncensored: `columns` sums every on-board run under the
    column the card wore, Backlog for a parked or unnamed one, and the unaccounted gaps are Backlog too, so the stack
    always adds up to `flowing`. None when the card was never on the board, or closed without an entry into `end`."""
    end = end or _columns.ROLE["issue_closed"]
    k = next((i for i, r in enumerate(runs) if r["on_board"]), None)
    if k is None:
        return None
    lo = _instant(runs[k]["from"])
    closed = next((r for r in reversed(runs) if r["column"] == end), None)
    if closed is None or _instant(closed["from"]) < lo:
        return None
    hi = _instant(closed["from"])
    columns = {}
    for column, _hold, hours in _on_board_hours(runs[k:], lo, hi):
        column = PARKED if column in (None, PARKED) else column
        columns[column] = columns.get(column, 0.0) + hours
    hours = (hi - lo).total_seconds() / 3600
    gap = hours - sum(columns.values())
    if gap > 1e-9:   # time off the board inside the window is still the card's wait, never a float rounding sliver
        columns[PARKED] = columns.get(PARKED, 0.0) + gap
    return {"start": runs[k]["from"], "end": closed["from"], "flowing": hours, "held": 0.0,
            "columns": columns, "gate": columns.get(GATING, 0.0), "censored": False,
            "approx": any(r["approx"] and lo < _instant(r["from"]) <= hi for r in runs),
            "unknown_first": not complete or bool(next((r["unknown"] for r in runs if r["on_board"]), False)),
            "start_lo": runs[k]["from"], "start_hi": runs[k]["from"]}


def lead_span(runs, start=None, end=None, complete=True):
    """The closed span of `lead_observation`: from a card's first entry into `start` to its last entry into `end`, or None
    when it has no entry into `start` or no entry into `end` after it."""
    obs = lead_observation(runs, start=start, end=end, complete=complete)
    if obs is None:
        return None
    return {k: obs[k] for k in ("start", "end", "flowing", "held", "approx", "unknown_first")}


def _instant(text):
    """One recorded timestamp as a UTC instant: a zoneless record is UTC, an offset is converted (#1250)."""
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_records(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def _rewrite(path, recs):
    tmp = pathlib.Path(f"{path}.tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    os.replace(tmp, path)


def load_store(path):
    """The last record of each issue in the file, keyed by number."""
    return {r["n"]: r for r in read_records(path) if r.get("k") == "issue"}


def record_issue(path, number, issue_updated, status_updated, events, complete, now, cursor=None, keys=None):
    """One card's timeline as read; `keys` are the bar values the board filters the card by, kept for the cards off the board."""
    return _append(path, {"k": "issue", "n": number, "issue_updated": issue_updated, "status_updated": status_updated,
                          "events": events, "complete": complete, "cursor": cursor, "t": now, "keys": keys})


def compact(path, now):
    """Rewrite the store with each card's last issue record, the points inside the window, and the last search."""
    if not pathlib.Path(path).exists(): return
    recs = read_records(path)
    issues = {r["n"]: r for r in recs if r.get("k") == "issue"}
    points = [r for r in recs if r.get("k") == "points" and now - WINDOW < r["t"] <= now]
    defers = [r for r in recs if r.get("k") == "defer" and r["until"] > now]
    searches = [r for r in recs if r.get("k") == "search"][-1:]
    _rewrite(path, [*issues.values(), *points, *defers, *searches])


def coverage(store):
    return {"read": len(store), "complete": sum(1 for r in store.values() if r.get("complete")),
            "last": max((r["t"] for r in store.values()), default=None)}


def coverage_fact(store, total):
    """The Monitor's history line: cards read of the board's total, how many timelines are complete, and the last refresh."""
    c = coverage(store)
    last = _iso(c["last"]) if c["last"] is not None else "never"
    return {"key": "history", "text": f"History {c['read']}/{total} read, {c['complete']} complete, last refresh {last}"}


def search_due(records, now):
    """True once an hour has passed since the last search, or when none has run."""
    last = max((r["t"] for r in records if r.get("k") == "search"), default=None)
    return last is None or now - last >= SEARCH_EVERY


def record_search(path, found, now):
    """Log one hourly search. A stored card the search did not return ends its interval: a transfer, a deletion or a removal.
    A search that was not read (`found` None) logs its time only: no absence is read from it, and it is not retried this hour."""
    search = _append(path, {"k": "search", "t": now, "read": found is not None})
    if found is None:
        return search
    found, stamp, store = set(found), _iso(now), load_store(path)
    for n, rec in sorted(store.items()):
        if n in found or (rec["events"] and rec["events"][-1]["type"] == "gone"):
            continue
        events = rec["events"] + [{"t": stamp, "type": "gone"}]
        record_issue(path, n, rec["issue_updated"], rec["status_updated"], events, rec["complete"], now, rec.get("cursor"),
                     rec.get("keys"))
    return search


def record_points(path, cost, now, retry_after=None):
    """Log the points one call cost; a Retry-After defers the next read until it has passed."""
    points = _append(path, {"k": "points", "t": now, "cost": cost})
    if retry_after is not None:
        _append(path, {"k": "defer", "until": now + retry_after})
    return points


def points_last_hour(path, now):
    return sum(r["cost"] for r in read_records(path)
               if r.get("k") == "points" and now - WINDOW < r["t"] <= now)


def allow(recs, now, cost):
    """(True, None) when a read of `cost` points fits in the records, else (False, the time it may run again)."""
    deferred = max((r["until"] for r in recs if r.get("k") == "defer"), default=0)
    if now < deferred:
        return False, deferred
    window = sorted((r for r in recs if r.get("k") == "points" and now - WINDOW < r["t"] <= now), key=lambda r: r["t"])
    used = sum(r["cost"] for r in window)
    if used + cost <= POINTS_CEILING:
        return True, None
    need, freed = used + cost - POINTS_CEILING, 0
    for r in window:
        freed += r["cost"]
        if freed >= need:
            return False, r["t"] + WINDOW
    return False, now + WINDOW


def budget_fact(path, now):
    """The Monitor's GraphQL instrument: the points the store counted in the last hour, read from the store, never recounted."""
    used = points_last_hour(path, now)
    return {"key": "graphql", "text": f"GraphQL {used}/{POINTS_CEILING} pts/h",
            "parts": [{"tone": "err" if used >= POINTS_CEILING else "ok"}]}
