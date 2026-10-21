"""The cumulative flow bands, one point per day, computed from the board's samples without the hub or any client.

A sample is {"t": epoch seconds UTC, "counts": {kind: {column: n}}, "frozen": n, "none": n}.
"""
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from board_columns import TERMINAL

CLOSED = "Closed"
FROZEN = "frozen"
NO_COLUMN = "No column"


def cfd(samples, columns, zone="UTC", kind=None):
    if not samples:
        return {"start": None, "points": [], "average_throughput": None}
    tz = ZoneInfo(zone)
    ordered = sorted(samples, key=lambda s: s["t"])
    last_of_day = {}
    for s in ordered:
        last_of_day[datetime.fromtimestamp(s["t"], tz).date()] = s
    points = []
    previous = None
    for day, s in last_of_day.items():
        totals = _totals(s, kind)
        bands = {c: totals.get(c, 0) for c in columns}
        bands[FROZEN] = s["frozen"]
        bands[NO_COLUMN] = s["none"] + sum(n for c, n in totals.items() if c not in columns)
        wip = sum(n for c, n in bands.items() if c in columns and c not in TERMINAL)
        closed = totals.get(CLOSED, 0)
        throughput = None
        if previous is not None and (day - previous[0]).days == 1:
            throughput = closed - previous[1]
        points.append({"day": day.isoformat(), "t": s["t"], "bands": bands, "wip": wip, "throughput": throughput})
        previous = (day, closed)
    daily = [p["throughput"] for p in points if p["throughput"] is not None]
    return {"start": ordered[0]["t"], "points": points,
            "average_throughput": sum(daily) / len(daily) if daily else None}


def _totals(sample, kind):
    per_kind = sample["counts"].values() if kind is None else [sample["counts"].get(kind, {})]
    totals = Counter()
    for per_column in per_kind:
        totals.update(per_column)
    return totals
