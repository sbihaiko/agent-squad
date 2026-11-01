"""The burndown of one spec: one point per day from its first child to its last whole read, computed from the history store.

Stdlib only: no hub and no GitHub client (ADR-0137). A ticket is a child of the spec while its parent relation names the spec
(the spec event of board_history), never by its title. A day with a whole read is a point; a day without one is a gap, never
a zero. The flow of a gap day is not dropped: the next point counts the closes and joins since the previous point.
"""
import bisect
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

NO_CHILD = "no child ticket"
NO_READ = "no whole board read after the first ticket"
TRAILING_DAYS = 7   # the window of the week's numbers and of the trailing close rate the guide falls back to


def burndown(store, reads, spec, zone="UTC"):
    """The series for one spec. `store` maps each issue number to its record (board_history's events), and `reads` holds the
    epoch seconds of the whole board reads. A day is named in the zone."""
    tz = ZoneInfo(zone)
    timelines = [(n, _states(rec.get("events", []))) for n, rec in store.items()]
    added, closed = defaultdict(list), defaultdict(list)
    for n, states in timelines:
        for (_, prev_spec, prev_closed), (t, spec_now, closed_now) in zip(states, states[1:]):
            day = _day(t, tz)
            if spec_now == spec and prev_spec != spec: added[day].append(n)
            if spec_now == spec and closed_now and not prev_closed: closed[day].append(n)
    if not added:
        return {"spec": spec, "start": None, "points": [], "gaps": [], "reason": NO_CHILD}
    start = min(added)
    read_days = {_day(t, tz) for t in reads}
    last_read = max(read_days, default=None)
    if last_read is None or last_read < start:
        return {"spec": spec, "start": start.isoformat(), "points": [], "gaps": [], "reason": NO_READ}
    points, gaps, pending_added, pending_closed = [], [], [], []
    day = start
    while day <= last_read:
        pending_added.extend(added.get(day, ()))
        pending_closed.extend(closed.get(day, ()))
        if day in read_days:
            end = _start(day + timedelta(days=1), tz)
            remaining = sum(1 for _, states in timelines if _open_child(states, end, spec))
            points.append({"day": day.isoformat(), "remaining": remaining, "closed": len(pending_closed), "added": len(pending_added),
                           "closed_tickets": sorted(pending_closed), "added_tickets": sorted(pending_added)})
            pending_added, pending_closed = [], []
        else:
            gaps.append(day.isoformat())
        day += timedelta(days=1)
    return {"spec": spec, "start": start.isoformat(), "points": points, "gaps": gaps, "reason": None}


def summary(series, today, forecast=None):
    """The numbers under the chart and the guide line. `today` is a date; `forecast` is the spec's forecast close date, or None
    when the spec has none. The guide runs from today, at the last whole read's remaining count, to zero: on the forecast date
    when there is one, else at the trailing close rate. A spec with neither gets no guide, and the reason is named."""
    points = series["points"]
    if not points:
        return {"remaining": None, "as_of": None, "closed_week": 0, "added_week": 0, "rate": 0.0, "guide": None,
                "guide_reason": None, "reason": series["reason"]}
    last = points[-1]
    first_of_week = (today - timedelta(days=TRAILING_DAYS - 1)).isoformat()
    week = [p for p in points if first_of_week <= p["day"] <= today.isoformat()]
    closed_week, added_week = sum(p["closed"] for p in week), sum(p["added"] for p in week)
    remaining, rate = last["remaining"], closed_week / TRAILING_DAYS
    guide, why = None, None
    if remaining == 0:
        target, source = today, "done"
    elif forecast is not None:
        target, source = forecast, "forecast"
    elif closed_week:
        target, source = today + timedelta(days=-(-remaining * TRAILING_DAYS // closed_week)), "trailing"
    else:
        target, source = None, None
        why = f"no forecast and no ticket closed in the last {TRAILING_DAYS} days"
    if target is not None:
        guide = {"source": source, "from": {"day": today.isoformat(), "remaining": remaining},
                 "to": {"day": target.isoformat(), "remaining": 0}}
    return {"remaining": remaining, "as_of": last["day"], "closed_week": closed_week, "added_week": added_week, "rate": rate,
            "guide": guide, "guide_reason": why, "reason": None}


def _states(events):
    """The ticket's (epoch, spec, closed) after each of its events, in time order, from a sentinel before the first one."""
    out = [(float("-inf"), None, False)]
    for _, e in sorted(enumerate(events), key=lambda p: (_epoch(p[1]["t"]), p[0])):
        _, spec_now, closed_now = out[-1]
        if e["type"] == "spec": spec_now = e["spec"]
        elif e["type"] == "closed": closed_now = True
        elif e["type"] == "reopened": closed_now = False
        else: continue
        out.append((_epoch(e["t"]), spec_now, closed_now))
    return out


def _open_child(states, end, spec):
    """True when the ticket is an open child of the spec at the instant `end`."""
    _, child, closed = states[bisect.bisect_left(states, end, key=lambda s: s[0]) - 1]
    return child == spec and not closed


def _epoch(t):
    return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def _day(t, tz):
    return datetime.fromtimestamp(t, tz).date()


def _start(day, tz):
    return datetime.combine(day, time.min, tzinfo=tz).timestamp()
