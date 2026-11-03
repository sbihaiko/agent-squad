"""Lead-time control chart arithmetic for the board's closed cards (SPEC 111). Stdlib only: no hub, no client, no file API (ADR-0137)."""
import math
import statistics
from datetime import UTC, datetime, timedelta

MEASURE = "on board -> closed"
ABANDONED = "Abandoned"
# The chart's own reason for a card the hub names as closed without delivery (#921). It is a label, not the carrier's
# vocabulary: board_columns reads the carrier's `state_reason` and hands this row its word, so the chart holds no copy
# of the reasons GitHub answers.
NOT_DELIVERED = "not_planned"


NO_TRANSITION = "no_transition"
NOT_READ = "not_read"


def _exclusion(row):
    if row.get("reason") in (NO_TRANSITION, NOT_READ):
        return row["reason"]
    if row.get("reason") == NOT_DELIVERED:
        return "not_planned"
    if row["column"] == ABANDONED:
        return "abandoned"
    if row["c"] is None:
        return "open"
    if not row["o"]:
        return "no_opened"
    return None


def _hours(row):
    return row["hours"]


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
        series.append({"n": row["n"], "closed": row["c"], "hours": hours[i], "rolling_hours": statistics.fmean(span),
                       "flowing_hours": row["flowing"], "held_hours": row["held"],
                       "approx": row["approx"], "unknown_first": row["unknown_first"]})
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


# SPEC 889 #900: the Jira-style control chart. The hub serves every number and position; the page draws marks only.
JIRA_CUBE_DAYS = 30           # Jira's y scale: linear under this many days, cube root from it
LOG_FLOOR_DAYS = 1 / 48       # the log axis starts at 30 minutes
LOG_FLOOR_HOURS = 1 / 60      # no log of a span under one minute
PLOT_WIDTH, PLOT_HEIGHT = 672, 212  # the lead chart's usable frame: the 800 x 340 page frame less its 64 px margins
DOT_PX = 9                    # dots closer than this many pixels merge into a numbered cluster
LOG_TICKS = [1 / 24, 1, 7]           # days, drawn with the one duration format
CUBE_TICKS = [1, 7, 30, 60]          # days, drawn with the one duration format
SHORT_RANGE = timedelta(days=3)      # a view this short labels its x axis in hours, a longer one in days
FIT_SHARE = 0.5                      # the cards cluster when the range they fill is at most half the view
FIT_MIN_PAD = timedelta(hours=1)
TIMEFRAME_SELECTS = "end transition"  # Timeframe keeps a card by the instant it entered the end column

# A cluster ring holds its count at the size the page draws it (#1146). The count inherits the chart box's
# --fs-body; these measure that text box, so the ring and the page agree on one size and one baseline.
RING_TEXT_PX = 15        # --fs-body in web-tokens.css: the size the count inherits from the chart box
AVG_GLYPH_EM = 0.6       # a digit's advance at the page's UI font, in em
LINE_EM = 1.0            # one line's height at that size, in em
RING_AIR_PX = 3          # air between the count's box and the ring
RING_BASELINE_EM = 0.35  # the count's baseline below its centre, in em: the page substitutes this
DOT_RADIUS_PX = 4.5      # a single dot's radius, the floor a ring never falls under


def count_box_radius(count):
    """The ring radius that holds `count`'s text box at the page body size, with air; a dot's radius is the floor."""
    box_w = len(str(count)) * AVG_GLYPH_EM * RING_TEXT_PX
    box_h = LINE_EM * RING_TEXT_PX
    return max(DOT_RADIUS_PX, math.hypot(box_w, box_h) / 2 + RING_AIR_PX)


def _stamp(moment):
    utc = moment.astimezone(UTC)
    return f"{utc:%b} {utc.day} {utc:%H:%M}"


def _axis_label(moment, span):
    return f"{moment:%H:%M}" if span <= SHORT_RANGE.total_seconds() else f"{moment:%b} {moment.day}"


def _fit(instants, start, end):
    """The range the cards fill, padded, when they cluster; None when they spread or already fill the view."""
    if not instants or end <= start:
        return None
    lo, hi = min(instants), max(instants)
    pad = max((hi - lo) * 0.1, FIT_MIN_PAD)
    fit_start, fit_end = max(start, lo - pad), min(end, hi + pad)
    if fit_end - fit_start > FIT_SHARE * (end - start) or (fit_start, fit_end) == (start, end):
        return None
    return {"start": fit_start.isoformat(), "end": fit_end.isoformat(),
            "text": f"{_stamp(fit_start)} to {_stamp(fit_end)} UTC"}


def _window_size(count):
    size = max(5, math.floor(0.2 * count + 0.5))
    return size + 1 if size % 2 == 0 else size


def _clamp(v):
    return min(1.0, max(0.0, v))


def _rolling(hours, log):
    """Centered mean and one sigma per dot, in hours. Linear: mean of hours. Log: geometric mean, sigma in log space."""
    size = _window_size(len(hours))
    half = (size - 1) // 2
    out = []
    for i in range(len(hours)):
        span = hours[max(0, i - half) : i + half + 1]
        if log:
            logs = [math.log(max(h, LOG_FLOOR_HOURS)) for h in span]
            mean, sigma = statistics.fmean(logs), statistics.pstdev(logs)
            out.append((math.exp(mean), math.exp(mean - sigma), math.exp(mean + sigma)))
        else:
            mean, sigma = statistics.fmean(span), statistics.pstdev(span)
            out.append((mean, max(mean - sigma, 0.0), mean + sigma))
    return out, size


def control_chart(items, timeframe=None, log=False, now=None, start=None, end=None, measure=MEASURE):
    """The chart over a timeframe in days, or over an explicit zoom range `start`..`end` (#900). A zoom names its dates."""
    now = now or datetime.now(UTC)
    zoom = start is not None
    excluded = {}
    delivered = []
    for row in items:
        reason = _exclusion(row)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            delivered.append(row)
    delivered.sort(key=lambda row: (datetime.fromisoformat(row["c"]), row["n"]))
    end = end or now
    if start is None and timeframe:
        start = end - timedelta(days=timeframe)
    plotted = [row for row in delivered if (start is None or datetime.fromisoformat(row["c"]) >= start)
               and datetime.fromisoformat(row["c"]) <= end]
    if start is None and plotted:
        start = datetime.fromisoformat(plotted[0]["c"])
    if start is None:
        start = end
    span = (end - start).total_seconds()

    hours = [_hours(row) for row in plotted]
    rolling, size = _rolling(hours, log)
    days_max = max([*hours, *(high for _, _, high in rolling)], default=0) / 24
    axis = "log" if log else ("cube" if days_max >= JIRA_CUBE_DAYS else "linear")
    if axis == "log":
        top = max(60.0, math.ceil(days_max))
        def position(h):
            return _clamp((math.log10(max(h / 24, LOG_FLOOR_DAYS)) - math.log10(LOG_FLOOR_DAYS))
                          / (math.log10(top) - math.log10(LOG_FLOOR_DAYS)))
        ticks = [(days * 24, _duration(days * 24), position(days * 24)) for days in LOG_TICKS]
    elif axis == "cube":
        top = float(math.ceil(max(days_max, 1) ** (1 / 3) * 1.05) ** 3)
        def position(h):
            return _clamp(max(h / 24, 0) ** (1 / 3) / top ** (1 / 3))
        ticks = [(days * 24, _duration(days * 24), position(days * 24)) for days in CUBE_TICKS if days <= top]
    else:
        top = float(max(1, math.ceil(max(hours + [high for _, _, high in rolling], default=0) * 1.1)))
        def position(h):
            return _clamp(h / top)
        ticks = [(top * k / 5, _duration(top * k / 5), position(top * k / 5)) for k in range(6)]

    dots = []
    for row, hrs, (mean, low, high) in zip(plotted, hours, rolling, strict=True):
        closed = datetime.fromisoformat(row["c"])
        x = _clamp((closed - start).total_seconds() / span) if span > 0 else 1.0
        flowing, held = row.get("flowing", hrs), row.get("held", 0.0)
        dots.append({"n": row["n"], "title": row.get("t", ""), "closed": closed.date().isoformat(), "hours": hrs,
                     "text": _duration(hrs), "flowing": flowing, "held": held,
                     "flowing_text": _duration(flowing), "held_text": _duration(held),
                     "approx": row.get("approx", False), "unknown_first": row.get("unknown_first", False),
                     "x": x, "y": position(hrs),
                     "rolling": {"hours": mean, "low": low, "high": high, "text": _duration(mean),
                                 "low_text": _duration(low), "high_text": _duration(high),
                                 "y": position(mean), "y_low": position(low), "y_high": position(high)}})

    clusters = []
    for dot in dots:
        for cluster in clusters:
            if math.hypot((cluster["x"] - dot["x"]) * PLOT_WIDTH, (cluster["y"] - dot["y"]) * PLOT_HEIGHT) < DOT_PX:
                cluster["dots"].append(dot)
                break
        else:
            clusters.append({"x": dot["x"], "y": dot["y"], "dots": [dot]})
    for cluster in clusters:   # a lone dot keeps the plain radius; a ring is sized for the count it holds (#1146)
        cluster["r"] = DOT_RADIUS_PX if len(cluster["dots"]) == 1 else count_box_radius(len(cluster["dots"]))

    def line(hrs, label):
        return None if hrs is None else {"hours": hrs, "text": _duration(hrs), "y": position(hrs), "label": label}

    summary = {"average": line(statistics.fmean(hours), "Average") if hours else None,
               "median": line(statistics.median(hours), "Median") if hours else None,
               "p85": line(_p85(hours), "p85") if hours else None}
    xticks = []
    for k in range(7):
        moment = start + timedelta(seconds=span * k / 6)
        xticks.append({"x": k / 6, "text": _axis_label(moment, span)})
    if zoom:
        scope = f"{start:%b} {start.day} to {end:%b} {end.day}"
    else:
        scope = f"last {timeframe * 24} hours" if timeframe and timeframe < 7 else f"last {timeframe} days" if timeframe else "all closed cards"
    sample = f"{len(dots)} {'issue' if len(dots) == 1 else 'issues'} plotted"
    text = f"{scope}, {measure}, {sample}; Timeframe selects by {TIMEFRAME_SELECTS}"
    return {
        "plot_frame": {"width": PLOT_WIDTH, "height": PLOT_HEIGHT},
        "measure": measure,
        "scale": axis,
        "axis": axis,
        "timeframe": timeframe,
        "window": {"size": size, "unit": "issues", "centered": True},
        "dots": dots,
        "clusters": clusters,
        "ticks": [{"hours": hrs, "text": text, "y": y} for hrs, text, y in ticks],
        "xticks": xticks,
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "fit": _fit([datetime.fromisoformat(row["c"]) for row in plotted], start, end),
        **summary,
        "excluded": excluded,
        "excluded_total": sum(excluded.values()),
        "text": text,
    }
