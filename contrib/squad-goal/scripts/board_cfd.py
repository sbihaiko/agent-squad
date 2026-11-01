"""The cumulative flow bands, one point per calendar day, from the history store's column intervals (#1024).

Each card is its list of runs (board_history.intervals): a run names the card's column, its hold tag and whether it is on the
board. A day's point samples every card at the day's end in the named zone, or at `now` for today. A card parked in Backlog
with no hold counts in no band. Stdlib and board_columns only: no hub, no GitHub client, no file API (ADR-0137).
"""
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from board_columns import ROLE, TERMINAL

CLOSED = "Closed"
PARKED = ROLE["unscheduled"]


def cfd(cards, columns, zone="UTC", now=None):
    """Each calendar day from the first run's day to today, with the count of cards in each column and the held count of each.

    `cards` is one list of runs per card. A card that is not on the board, or has no column, counts nowhere. The throughput of a
    day is the increase of the Closed count over the day before, and absent on the first day.
    """
    now = now or datetime.now(UTC)
    tz = ZoneInfo(zone)
    starts = [_instant(run["from"]) for runs in cards for run in runs]
    if not starts:
        return {"start": None, "points": [], "average_throughput": None}
    first_day, last_day = min(starts).astimezone(tz).date(), now.astimezone(tz).date()
    points, previous = [], None
    for k in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=k)
        moment = min(datetime.combine(day + timedelta(days=1), time(), tzinfo=tz), now)
        totals, held = Counter(), Counter()
        for runs in cards:
            run = _at(runs, moment)
            if run is None: continue
            totals[run["column"]] += 1
            if run["hold"]: held[run["column"]] += 1
        bands = {c: totals.get(c, 0) for c in columns}
        closed = totals.get(CLOSED, 0)
        points.append({"day": day.isoformat(), "t": moment.timestamp(), "bands": bands,
                       "held": {c: held.get(c, 0) for c in columns},
                       "wip": sum(n for c, n in bands.items() if c not in TERMINAL),
                       "throughput": None if previous is None else closed - previous})
        previous = closed
    daily = [p["throughput"] for p in points if p["throughput"] is not None]
    return {"start": min(starts).timestamp(), "points": points,
            "average_throughput": sum(daily) / len(daily) if daily else None}


def _at(runs, moment):
    """The run a card is in at `moment`, or None when it is on no column or parked in Backlog without a hold."""
    for run in runs:
        if not run["on_board"]: continue
        if _instant(run["from"]) <= moment and (run["to"] is None or moment < _instant(run["to"])):
            if run["column"] is None or (run["column"] == PARKED and not run["hold"]): return None
            return run
    return None


def _instant(text):
    return datetime.fromisoformat(text)
