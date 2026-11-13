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
    # A drawn cumulative flow opens at zero: the series begins the day before the earliest card opened, and that day counts
    # nothing. The owner reads the first point as "nothing was in the system yet", never as work that appeared from nowhere.
    first_day = min(starts).astimezone(tz).date() - timedelta(days=1)
    last_day = now.astimezone(tz).date()
    points, previous = [], None
    series = [_continuous(runs) for runs in cards]
    for k in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=k)
        moment = min(datetime.combine(day + timedelta(days=1), time(), tzinfo=tz), now)
        totals, held = Counter(), Counter()
        for runs in series:
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
    return {"start": datetime.combine(first_day, time(), tzinfo=tz).timestamp(), "points": points,
            "average_throughput": sum(daily) / len(daily) if daily else None}


def _continuous(runs):
    """One card's runs with its moves taken out: an off-board run that ends with the card back on a column is the artifact of
    a card removed from the board and re-added, never work that left the system (#1663). The card keeps the column it held
    before the move, so the bands draw no valley where it was moved and no wall where it came back. A removal nothing
    re-adds is a card that left for good, and it ends at that removal, as it always did."""
    out, back = [], any(run["on_board"] for run in runs)
    for k, run in enumerate(runs):
        if not run["on_board"] and back and any(r["on_board"] for r in runs[k + 1:]):
            if out: out[-1]["to"] = run["to"]   # the card spans the move: the gap belongs to the run before it
            continue
        out.append(dict(run))
    return out


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
