"""Lead-time control chart arithmetic for the board's closed cards (SPEC 111). Stdlib only: no hub, no client, no file API (ADR-0137)."""
import math
import statistics
from datetime import datetime

MEASURE = "opened -> closed"
ABANDONED = "Abandoned"


def _exclusion(row):
    if row["column"] == ABANDONED:
        return "abandoned"
    if row["c"] is None:
        return "open"
    if not row["o"]:
        return "no_opened"
    return None


def _hours(row):
    opened = datetime.fromisoformat(row["o"])
    closed = datetime.fromisoformat(row["c"])
    return (closed - opened).total_seconds() / 3600


def _duration(hours):
    minutes = round(hours * 60)
    days, rest = divmod(minutes, 1440)
    hrs, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hrs}h" if hrs else f"{days}d"
    if hrs:
        return f"{hrs}h {mins}m" if mins else f"{hrs}h"
    return f"{mins}m"


def _value(hours):
    return {"hours": hours, "text": _duration(hours)}


def _p85(hours):
    ordered = sorted(hours)
    return ordered[math.ceil(85 * len(ordered) / 100) - 1]


def lead_time(items, window=20):
    excluded = {}
    delivered = []
    for row in items:
        reason = _exclusion(row)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            delivered.append(row)
    delivered.sort(key=lambda row: (datetime.fromisoformat(row["c"]), row["n"]))

    hours = [_hours(row) for row in delivered]
    series = []
    for i, row in enumerate(delivered):
        span = hours[max(0, i - window + 1) : i + 1]
        series.append({"n": row["n"], "closed": row["c"], "hours": hours[i], "rolling_hours": statistics.fmean(span)})
    summary = None
    if hours:
        summary = {
            "average": _value(statistics.fmean(hours)),
            "median": _value(statistics.median(hours)),
            "minimum": _value(min(hours)),
            "maximum": _value(max(hours)),
            "p85": _value(_p85(hours)),
        }
    return {
        "measure": MEASURE,
        "unit": "hours",
        "window": {"size": window, "unit": "issues"},
        "excluded": excluded,
        "series": series,
        "summary": summary,
    }
