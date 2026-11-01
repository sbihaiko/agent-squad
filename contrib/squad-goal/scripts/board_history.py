"""One history store per repository: each card's column, hold and spec intervals, kept in hub-history.<owner>_<name>.jsonl (#1023).

Stdlib and board_columns only: no hub and no GitHub client (ADR-0137). The hub fetches the payloads and calls this module,
which decides what to read, keeps what it read, and counts the GraphQL points it spent. The file is append-only: one record
per line, and the last issue record wins. board_flow_history.py is the flow-sample journal and stays a separate store.
"""
import importlib.util
import json
import pathlib
from datetime import UTC, datetime

_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_columns)
view_column = _columns.view_column

BATCH_SIZE = 1          # one issue per timeline query: the only size the API check measured complete (docs/board-history-coverage.md)
POINTS_CEILING = 4000   # GraphQL points the hub may spend an hour; GitHub allows 5000, the rest is headroom
WINDOW = 3600           # seconds in the points hour and between hourly searches
SEARCH_EVERY = 3600


def history_path(board, repo):
    """The history one repository keeps beside the board cache: hub-history.<owner>_<name>.jsonl."""
    return pathlib.Path(board).with_name(f"hub-history.{repo.replace('/', '_')}.jsonl")


def item_facts(issue, project):
    """The clocks one issue carries on one project: the Status field's updatedAt (the Status clock) and the issue's own."""
    items = [i for i in (issue.get("projectItems") or {}).get("nodes", []) if (i.get("project") or {}).get("number") == project]
    status = max((((i.get("fieldValueByName") or {}).get("updatedAt")) or "" for i in items), default="")
    return {"n": issue["number"], "issue_updated": issue["updatedAt"], "status_updated": status}


def due_reads(items, stored):
    """The numbers to read: a card with no complete record, or whose Status moved past its record. Neither the item's nor the
    issue's own updatedAt is a reason: the first moves on writes that touch no Status, the second on any edit."""
    return [i["n"] for i in items
            if (rec := stored.get(i["n"])) is None
            or not rec.get("complete")
            or i["status_updated"] > rec["status_updated"]]


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
    if kind == "AddedToProjectV2Event": return {"t": t, "type": "added"}
    if kind == "RemovedFromProjectV2Event": return {"t": t, "type": "removed"}
    if kind == "ParentIssueAddedEvent": return {"t": t, "type": "spec", "spec": (node.get("parent") or {}).get("number")}
    if kind == "ParentIssueRemovedEvent": return {"t": t, "type": "spec", "spec": None}
    return None


def events_of(issue):
    """The events one issue's timeline page holds, in the order GitHub gave them."""
    return [e for e in map(_event, issue["timelineItems"]["nodes"]) if e]


def record_page(store, number, page, issue_updated, status_updated):
    """Merge one timeline page into the in-memory store: its events follow the stored ones, and its end cursor resumes the next read."""
    prev = store.get(number, {})
    events = list(prev.get("events", [])) + [e for e in map(_event, page["nodes"]) if e]
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


def intervals(events):
    """Each run of one state between events: its column, hold and spec, and whether the card is on the board. Equal
    neighbours merge, and the last run is open (to None)."""
    state = {"status": "", "closed": False, "labels": set(), "spec": None, "on_board": False}
    out = []
    for _, e in sorted(enumerate(events), key=lambda p: (p[1]["t"], p[0])):
        _apply(state, e)
        column, hold = None, None
        if state["on_board"] and state["status"]:
            column, hold = view_column(state["status"], state["closed"], sorted(state["labels"]), None)
        snap = {"on_board": state["on_board"], "column": column, "hold": hold, "spec": state["spec"]}
        if out and {k: out[-1][k] for k in snap} == snap:
            continue
        if out: out[-1]["to"] = e["t"]
        out.append({"from": e["t"], "to": None, **snap})
    return out


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


def load_store(path):
    """The last record of each issue in the file, keyed by number."""
    return {r["n"]: r for r in read_records(path) if r.get("k") == "issue"}


def record_issue(path, number, issue_updated, status_updated, events, complete, now, cursor=None):
    _append(path, {"k": "issue", "n": number, "issue_updated": issue_updated, "status_updated": status_updated,
                   "events": events, "complete": complete, "cursor": cursor, "t": now})


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
    """Log one hourly search. A stored card the search did not return ends its interval: a transfer, a deletion or a removal."""
    found, stamp, store = set(found), _iso(now), load_store(path)
    _append(path, {"k": "search", "t": now})
    for n, rec in sorted(store.items()):
        if n in found or (rec["events"] and rec["events"][-1]["type"] == "gone"):
            continue
        events = rec["events"] + [{"t": stamp, "type": "gone"}]
        record_issue(path, n, rec["issue_updated"], rec["status_updated"], events, rec["complete"], now, rec.get("cursor"))


def record_points(path, cost, now, retry_after=None):
    """Log the points one call cost; a Retry-After defers the next read until it has passed."""
    _append(path, {"k": "points", "t": now, "cost": cost})
    if retry_after is not None:
        _append(path, {"k": "defer", "until": now + retry_after})


def points_last_hour(path, now):
    return sum(r["cost"] for r in read_records(path)
               if r.get("k") == "points" and now - WINDOW < r["t"] <= now)


def may_read(path, now, cost):
    """(True, None) when a read of `cost` points fits, else (False, the time it may run again)."""
    recs = read_records(path)
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
