"""The cumulative flow bands, one point per day, computed from the board's samples without the hub or any client.

A sample is {"t": epoch seconds UTC, "counts": {kind: {column: n}}, "frozen": n, "none": n, "cells": [...]}. A cell is
[kind, spec, wave, column, frozen, n] (#904). A sample written before the cells carries none: it answers no spec and no
wave, and its days are flagged `legacy` on the point, so the chart can say so.
"""
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from board_columns import TERMINAL

CLOSED = "Closed"
FROZEN = "frozen"
NO_COLUMN = "No column"
NONE = "none"


def cfd(samples, columns, zone="UTC", kind=None, skips=(), spec=None, wave=None):
    """One point per calendar day from the first sample on, in the named zone (#894).

    A day with a sample takes its last sample. A day with no sample is a gap: either a flow_skip record (`skips`) says a read
    of that day was not whole, or no read of it was made at all (the hub was down). Either way its count is unknown, so
    nothing is carried forward. board_flow_history writes one sample per UTC day for each whole read, so a quiet day with a
    whole read is a point (#894).

    `kind`, `spec` and `wave` filter the cards, as the board's bar does (#904): a list of values, where an empty list or None
    keeps every card. A kind is `issue`, `bug` or `pr`; one kind as a string still reads as that kind alone. A spec is a
    number or `none`. A wave is the bar's Waves key `spec/wave` (#980), each side a number or `none`. The lists combine
    with AND.
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
    kinds, specs, waves = _wanted([kind] if isinstance(kind, str) else kind), _wanted(spec), _wanted(wave)
    points, gaps = [], []
    previous = None   # the closed count of the day before, when that day is a point; None after a gap and at the start
    for k in range((last - first).days + 1):
        day = first + timedelta(days=k)
        if day not in last_of_day:
            gaps.append(day.isoformat())
            previous = None
            continue
        carried = last_of_day[day]
        totals, frozen, none = _carried(carried, kinds, specs, waves)
        bands = {c: totals.get(c, 0) for c in columns}
        bands[FROZEN] = frozen
        bands[NO_COLUMN] = none + sum(n for c, n in totals.items() if c not in columns)
        wip = sum(n for c, n in bands.items() if c in columns and c not in TERMINAL)
        closed = totals.get(CLOSED, 0)
        throughput = None if previous is None else closed - previous
        point = {"day": day.isoformat(), "t": carried["t"], "bands": bands, "wip": wip, "throughput": throughput}
        if "cells" not in carried: point["legacy"] = True
        points.append(point)
        previous = closed
    daily = [p["throughput"] for p in points if p["throughput"] is not None]
    return {"start": ordered[0]["t"], "points": points, "gaps": gaps,
            "average_throughput": sum(daily) / len(daily) if daily else None}


def _day(t, tz):
    return datetime.fromtimestamp(t, tz).date()


def _wanted(values):
    """The filter's values as strings, or None when the filter keeps every card (an empty list, as the bar's)."""
    return {str(v) for v in values} if values else None


def _carried(sample, kinds, specs, waves):
    """(totals by column, frozen, none) of one sample under the filters. A sample with no cells answers no spec and no
    wave: it keeps a card only when the filter names `none` for that dimension. Its kinds are its counts' keys."""
    if "cells" not in sample:
        if (specs and NONE not in specs) or (waves and f"{NONE}/{NONE}" not in waves):
            return Counter(), 0, 0
        return _totals(sample, kinds), sample["frozen"], sample["none"]
    totals, frozen, none = Counter(), 0, 0
    for k_kind, k_spec, k_wave, column, fz, n in sample["cells"]:
        if kinds and k_kind not in kinds: continue
        if specs and k_spec not in specs: continue
        if waves and f"{k_spec}/{k_wave}" not in waves: continue
        if column is None: none += n
        else: totals[column] += n
        if fz: frozen += n
    return totals, frozen, none


def _totals(sample, kinds):
    per_kind = [by for k, by in sample["counts"].items() if not kinds or k in kinds]
    totals = Counter()
    for per_column in per_kind:
        totals.update(per_column)
    return totals
