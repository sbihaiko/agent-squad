"""The cumulative flow bands, one point per day, computed from the board's samples without the hub or any client.

A sample is {"t": epoch seconds UTC, "counts": {kind: {column: n}}, "frozen": n, "none": n}.
"""
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from board_columns import TERMINAL

CLOSED = "Closed"
FROZEN = "frozen"
NO_COLUMN = "No column"


def cfd(samples, columns, zone="UTC", kind=None, skips=()):
    """One point per calendar day from the first sample on, in the named zone (#894).

    A day with a sample takes its last sample. A day with no sample and a flow_skip record (`skips`) is a gap: the read
    of that day was not whole, so there is no value to draw. Any other day with no sample carries the last sample: the
    board's counts did not move that day, and board_flow_history writes no row for an unchanged read.
    """
    if not samples:
        return {"start": None, "points": [], "gaps": [], "average_throughput": None}
    tz = ZoneInfo(zone)
    ordered = sorted(samples, key=lambda s: s["t"])
    last_of_day = {}
    for s in ordered:
        last_of_day[_day(s["t"], tz)] = s
    skipped = {_day(x["t"], tz) for x in skips}
    first = _day(ordered[0]["t"], tz)
    last = max([*last_of_day, *(d for d in skipped if d >= first)])
    points, gaps = [], []
    carried = None
    previous = None   # the closed count of the day before, when that day is a point; None after a gap and at the start
    for k in range((last - first).days + 1):
        day = first + timedelta(days=k)
        if day in last_of_day:
            carried = last_of_day[day]
        elif day in skipped:
            gaps.append(day.isoformat())
            previous = None
            continue
        totals = _totals(carried, kind)
        bands = {c: totals.get(c, 0) for c in columns}
        bands[FROZEN] = carried["frozen"]
        bands[NO_COLUMN] = carried["none"] + sum(n for c, n in totals.items() if c not in columns)
        wip = sum(n for c, n in bands.items() if c in columns and c not in TERMINAL)
        closed = totals.get(CLOSED, 0)
        throughput = None if previous is None else closed - previous
        points.append({"day": day.isoformat(), "t": carried["t"], "bands": bands, "wip": wip, "throughput": throughput})
        previous = closed
    daily = [p["throughput"] for p in points if p["throughput"] is not None]
    return {"start": ordered[0]["t"], "points": points, "gaps": gaps,
            "average_throughput": sum(daily) / len(daily) if daily else None}


def _day(t, tz):
    return datetime.fromtimestamp(t, tz).date()


def _totals(sample, kind):
    per_kind = sample["counts"].values() if kind is None else [sample["counts"].get(kind, {})]
    totals = Counter()
    for per_column in per_kind:
        totals.update(per_column)
    return totals
