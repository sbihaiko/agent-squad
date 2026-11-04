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
NO_START = "no_start"   # the card never entered the start column (#1248)
NOT_READ = "not_read"
OPEN = "open"           # an open card is no dot, and no exclusion either: the survival reading holds it censored (#1250)


def utc(text):
    """One recorded timestamp as a UTC instant: a zoneless record is UTC, an offset is converted (#1250)."""
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _exclusion(row):
    if row.get("reason") in (NO_TRANSITION, NO_START, NOT_READ):
        return row["reason"]
    if row.get("reason") == NOT_DELIVERED:
        return "not_planned"
    if row["column"] == ABANDONED:
        return "abandoned"
    if row["c"] is None:
        return OPEN
    if not row["o"]:
        return "no_opened"
    return None


def _hours(row):
    return row["hours"]


def _split(items):
    """The rows the chart plots, closed and delivered, and the cards it leaves out, by reason. An open card is neither."""
    excluded, cards, delivered = {}, {}, []
    for row in items:
        reason = _exclusion(row)
        if reason == OPEN:
            continue
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            cards.setdefault(reason, []).append(row["n"])
        else:
            delivered.append(row)
    delivered.sort(key=lambda row: (utc(row["c"]), row["n"]))
    return delivered, excluded, {r: sorted(ns) for r, ns in cards.items()}


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


GATE_LABEL = "Gate time"


def _bands(row):
    """The row's stacked bands, longest first: the hours it spent in each column, as the history store recorded them, or
    its whole measured time in its own column for a plain row (#1256). An empty `columns` maps is a plain row too: the
    row stacks its whole measure in its own column, so a card with hours never draws no stack. A column with no hours
    draws no band."""
    columns = row.get("columns")
    pairs = columns.items() if columns else [(row["column"], _hours(row))]
    return sorted(({"column": column, "hours": hours, "text": _duration(hours)}
                   for column, hours in pairs if hours > 0), key=lambda band: (-band["hours"], band["column"]))


def _gate(row):
    return row.get("gate", 0.0)


def _stack_fields(row, position=None):
    """The row's stack, its gate hours and duration. With an axis `position`, each band carries the two edges the page
    draws: `y0` at its foot and `y` at its head, in the same normalized space as the dot (SPEC 1218)."""
    stack, acc = [], 0.0
    for band in _bands(row):
        band = dict(band)
        if position is not None:
            band.update(y0=position(acc), y=position(acc + band["hours"]))
        acc += band["hours"]
        stack.append(band)
    return {"stack": stack, "gate_hours": _gate(row), "gate_text": _duration(_gate(row))}


def _p85(hours):
    ordered = sorted(hours)
    return ordered[math.ceil(85 * len(ordered) / 100) - 1]


def lead_time(items, window=20, now=None, measure=MEASURE):
    """The closed cards' series and summary. The summary's median is the Kaplan-Meier median over every card that
    started (#1250); the average, minimum, maximum and p85 are over the closed cards."""
    delivered, excluded, excluded_cards = _split(items)

    hours = [_hours(row) for row in delivered]
    series = []
    for i, row in enumerate(delivered):
        span = hours[max(0, i - window + 1) : i + 1]
        series.append({"n": row["n"], "closed": row["c"], "hours": hours[i], "rolling_hours": statistics.fmean(span),
                       "flowing_hours": row["flowing"], "held_hours": row["held"],
                       "approx": row["approx"], "unknown_first": row["unknown_first"], **_stack_fields(row)})
    summary = None
    if hours:
        km = survival(items, now=now)
        summary = {
            "average": _value(statistics.fmean(hours)),
            "median": km["median"] or {"hours": None, "text": NOT_ESTIMABLE},
            "minimum": _value(min(hours)),
            "maximum": _value(max(hours)),
            "p85": _value(_p85(hours)),
            # Gate time here covers every delivered card, like the average, the minimum and the p85 below and above it;
            # `control_chart`'s gate covers the cards its timeframe plots, like that chart's own average (#1256).
            "gate": _value(sum(_gate(row) for row in delivered)),
        }
    return {
        "measure": measure,
        "unit": "hours",
        "window": {"size": window, "unit": "issues"},
        "excluded": excluded,
        "excluded_cards": excluded_cards,
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
# SPEC 1327 #1334: a linear view labels its y axis on one regular interval from this round-duration ladder, as minutes,
# with 4 to 6 ticks. The ladder under 30 days covers every linear view.
LINEAR_TICKS = (5, 10, 15, 30, 60, 120, 180, 240, 360, 720, 1440, 2880, 4320, 7200, 10080, 20160, 43200)
SHORT_RANGE = timedelta(days=3)      # a view this short labels its x axis in hours, a longer one in days
FIT_SHARE = 0.5                      # the cards cluster when the range they fill is at most half the view
FIT_MIN_PAD = timedelta(hours=1)
TIMEFRAME_SELECTS = "end transition"  # Timeframe keeps a card by the instant it entered the end column

# A cluster ring holds its count at the size the page draws it (#1146). The count inherits the chart box's
# --fs-body; these measure that text box, so the ring and the page agree on one size. The count itself sits clear
# above the ring on a leader line (#1304), so no baseline is substituted into the page.
RING_TEXT_PX = 15        # --fs-body in web-tokens.css: the size the count inherits from the chart box
AVG_GLYPH_EM = 0.6       # a digit's advance at the page's UI font, in em
LINE_EM = 1.0            # one line's height at that size, in em
RING_AIR_PX = 3          # air between the count's box and the ring
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


def _linear_ticks(max_hours):
    """The y axis of a linear view: 0 to a round top at one interval from LINEAR_TICKS, 4 to 6 ticks (#1334).
    When no ladder entry yields 3 to 5 intervals, the longest step still reaches the ceiling on as many as it takes."""
    ceiling = max(max_hours, 0.0)
    for minutes in LINEAR_TICKS:
        size = minutes / 60
        count = math.ceil(ceiling / size) if ceiling else 0
        if 3 <= count <= 5:
            return size * count, [(size * k, _duration(size * k)) for k in range(count + 1)]
    size = LINEAR_TICKS[-1] / 60
    count = max(1, math.ceil(ceiling / size))
    return size * count, [(size * k, _duration(size * k)) for k in range(count + 1)]


def _clusters(dots, axis="x"):
    """The dots merged into rings: two marks closer than DOT_PX in the plot frame share one cluster (#1146). In a fit
    view the same dots merge at their fit position, so the page draws the rings that view holds (#1334)."""
    clusters = []
    for dot in dots:
        for cluster in clusters:
            if math.hypot((cluster["x"] - dot[axis]) * PLOT_WIDTH,
                          (cluster["y"] - dot["y"]) * PLOT_HEIGHT) < DOT_PX:
                cluster["dots"].append(dot)
                break
        else:
            clusters.append({"x": dot[axis], "y": dot["y"], "dots": [dot]})
    for cluster in clusters:   # a lone dot keeps the plain radius; a ring is sized for the count it holds (#1146)
        cluster["r"] = DOT_RADIUS_PX if len(cluster["dots"]) == 1 else count_box_radius(len(cluster["dots"]))
    return clusters


def _band(dots, axis="x"):
    """The rolling spread as one explicit polygon: each dot's high edge left to right, then its low edge back again,
    never under zero and bounded by the first and last observation (#1334)."""
    high = [{"x": d[axis], "y": d["rolling"]["y_high"], "hours": d["rolling"]["high"]} for d in dots]
    low = [{"x": d[axis], "y": d["rolling"]["y_low"], "hours": d["rolling"]["low"]} for d in dots]
    return {"low": low, "high": high, "polygon": high + low[::-1]}


def _range_text(start, end):
    """The printed range: the hour and minute of a short view, the two dates of a long one (#1334); a range that
    crosses a year names both years, so no end reads as the same year as the other."""
    if (end - start) <= SHORT_RANGE:
        return f"{_stamp(start)} to {_stamp(end)} UTC"
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year} UTC"
    return f"{start:%b} {start.day} to {end:%b} {end.day} UTC"


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
    delivered, excluded, excluded_cards = _split(items)
    end = end or now
    if start is None and timeframe:
        start = end - timedelta(days=timeframe)
    plotted = [row for row in delivered if (start is None or utc(row["c"]) >= start) and utc(row["c"]) <= end]
    km = survival(items, start=start, end=end, now=now)
    if start is None and plotted:
        start = utc(plotted[0]["c"])
    if start is None:
        start = end
    span = (end - start).total_seconds()

    hours = [_hours(row) for row in plotted]
    rolling, size = _rolling(hours, log)
    fit = _fit([utc(row["c"]) for row in plotted], start, end)
    fit_start, fit_end = (datetime.fromisoformat(fit[k]) for k in ("start", "end")) if fit else (None, None)
    fit_span = (fit_end - fit_start).total_seconds() if fit else 0
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
        top, ladder = _linear_ticks(max(hours + [high for _, _, high in rolling], default=0))
        def position(h):
            return _clamp(h / top)
        ticks = [(hrs, text, position(hrs)) for hrs, text in ladder]

    dots = []
    for row, hrs, (mean, low, high) in zip(plotted, hours, rolling, strict=True):
        closed = utc(row["c"])
        x = _clamp((closed - start).total_seconds() / span) if span > 0 else 1.0
        flowing, held = row.get("flowing", hrs), row.get("held", 0.0)
        dot = {"n": row["n"], "title": row.get("t", ""), "closed": closed.date().isoformat(), "hours": hrs,
               "text": _duration(hrs), "flowing": flowing, "held": held,
               "flowing_text": _duration(flowing), "held_text": _duration(held),
               "approx": row.get("approx", False), "unknown_first": row.get("unknown_first", False),
               **_stack_fields(row, position), "x": x, "y": position(hrs),
               "rolling": {"hours": mean, "low": low, "high": high, "text": _duration(mean),
                           "low_text": _duration(low), "high_text": _duration(high),
                           "y": position(mean), "y_low": position(low), "y_high": position(high)}}
        if fit:   # the fit range's own position for the same dot, so the page switches range from this one payload (#1334)
            dot["xf"] = _clamp((closed - fit_start).total_seconds() / fit_span) if fit_span > 0 else 1.0
        dots.append(dot)

    clusters = _clusters(dots)
    band = _band(dots) if dots else None
    if fit and band:
        band["fit"] = _band(dots, "xf")

    def line(hrs, label):
        return None if hrs is None else {"hours": hrs, "text": _duration(hrs), "y": position(hrs), "label": label}

    median = line(km["median"]["hours"], "Median") if km["median"] else \
        {"hours": None, "text": NOT_ESTIMABLE, "y": None, "label": "Median"}
    median.update(estimable=km["estimable"], ci=km["ci"], ci_text=km["ci_text"])   # the Kaplan-Meier median (#1250)
    excluded_total = sum(excluded.values())
    summary = {"average": line(statistics.fmean(hours), "Average") if hours else None,
               "median": median,
               "p85": line(_p85(hours), "p85") if hours else None,
               "minimum": line(min(hours), "Minimum") if hours else None,   # the expanded summary (#1334)
               "maximum": line(max(hours), "Maximum") if hours else None,
               "plotted": len(dots),
               "plotted_text": f"{len(dots)} {'issue' if len(dots) == 1 else 'issues'} plotted",
               "excluded": excluded_total}
    xticks = []
    for k in range(7):
        moment = start + timedelta(seconds=span * k / 6)
        xticks.append({"x": k / 6, "text": _axis_label(moment, span)})
    if zoom:
        scope = f"{start:%b} {start.day} to {end:%b} {end.day}"
    else:
        scope = f"last {timeframe * 24} hours" if timeframe and timeframe < 7 else f"last {timeframe} days" if timeframe else "all closed cards"
    text = f"{scope}, {measure}, {summary['plotted_text']}; Timeframe selects by {TIMEFRAME_SELECTS}"
    return {
        "plot_frame": {"width": PLOT_WIDTH, "height": PLOT_HEIGHT},
        "measure": measure,
        "scale": axis,
        "axis": axis,
        "timeframe": timeframe,
        "window": {"size": size, "unit": "issues", "centered": True},
        "dots": dots,
        "clusters": clusters,
        **({"clusters_fit": _clusters(dots, "xf")} if fit else {}),
        "band": band,
        "ticks": [{"hours": hrs, "text": text, "y": y} for hrs, text, y in ticks],
        "xticks": xticks,
        "range": {"start": start.isoformat(), "end": end.isoformat(), "text": _range_text(start, end)},
        "gate": {"hours": sum(_gate(row) for row in plotted), "text": _duration(sum(_gate(row) for row in plotted)),
                 "label": GATE_LABEL},   # the plotted cards only, like this chart's average and p85
        "fit": fit,
        "average": summary["average"],   # the reference lines stay at the top level the page reads them from
        "median": median,
        "p85": summary["p85"],
        "summary": summary,
        "survival": km,
        "wip": wip(items),
        "excluded": excluded,
        "excluded_cards": excluded_cards,
        "excluded_total": excluded_total,
        "text": text,
    }


# #1250: the Kaplan-Meier median over the arrival cohort. A card whose start falls inside the window enters the risk set;
# a card still open enters it censored at the reading instant. The median is the first time the curve falls to or below
# one half; its 95 % interval is where the log-log Greenwood band crosses one half.
Z95 = 1.959963984540054
NOT_ESTIMABLE = "not estimable"
EXCLUDED_FROM_COHORT = (NOT_DELIVERED, "abandoned", NOT_READ, NO_TRANSITION, NO_START)


def _observation(row):
    """The row's observation: the hub's `obs`, or for a closed delivered row with no `obs`, its own start and hours.
    An open row with no `obs` has no start that can be read, so it is not observed: it is never timed from another field."""
    if row.get("obs"):
        return row["obs"]
    if _exclusion(row) is None:
        return {"start_lo": row["o"], "start_hi": row["o"], "hours": row["hours"], "censored": False,
                "unknown_start": False}
    return None


def _km_curve(observations):
    """[(t, S, lower, upper)] at each distinct event time, from (hours, finished) pairs, in one sweep. At a tie, a finish
    comes before a censoring: a card censored at t is still at risk at t."""
    curve, s, v = [], 1.0, 0.0
    ordered = sorted(observations, key=lambda o: (o[0], not o[1]))
    n, i = len(ordered), 0
    while i < len(ordered):
        t, d, c = ordered[i][0], 0, 0
        while i < len(ordered) and ordered[i][0] == t:
            d, c, i = d + ordered[i][1], c + (not ordered[i][1]), i + 1
        if d:
            s *= 1 - d / n
            if s <= 0:
                curve.append((t, 0.0, 0.0, 0.0))
                break
            v += d / (n * (n - d))
            se = math.sqrt(v) / abs(math.log(s))
            curve.append((t, s, s ** math.exp(Z95 * se), s ** math.exp(-Z95 * se)))
        n -= d + c
    return curve


def _crossing(curve, index):
    return next((point[0] for point in curve if point[index] <= 0.5), None)


def _day(moment):
    return f"{moment:%b} {moment.day}"


def survival(items, start=None, end=None, now=None):
    """The Kaplan-Meier reading over the cards whose start falls in `start`..`end` (all starts when `start` is None)."""
    now = now or datetime.now(UTC)
    cohort, unknown, intervals = [], [], 0
    for row in items:
        if row.get("reason") in EXCLUDED_FROM_COHORT or row.get("column") == ABANDONED:
            continue
        obs = _observation(row)
        if obs is None:
            continue
        late = utc(obs["start_hi"])
        if (start is not None and late < start) or (end is not None and late > end):
            continue
        if obs.get("unknown_start"):
            unknown.append(row["n"])
            continue
        early = obs["hours"] + (late - utc(obs["start_lo"])).total_seconds() / 3600
        intervals += early != obs["hours"]
        cohort.append((late, obs["hours"], early, not obs["censored"]))
    curve = _km_curve([(h, done) for _, h, _, done in cohort])
    median = _crossing(curve, 1)
    low, high = _crossing(curve, 2), _crossing(curve, 3)
    median_range = None
    if intervals:
        late_median = _crossing(_km_curve([(e, done) for _, _, e, done in cohort]), 1)
        if late_median != median:
            median_range = {"low": _value(median) if median is not None else None,
                            "high": _value(late_median) if late_median is not None else None}
    censored = sum(1 for *_, done in cohort if not done)
    first = start or min((s for s, *_ in cohort), default=None)
    last = end or now
    value = _value(median) if median is not None else None
    ci = {"level": 0.95, "low": _value(low) if low is not None else None, "high": _value(high) if high is not None else None}
    ci_text = f"95% CI {ci['low']['text'] if ci['low'] else '?'} to {ci['high']['text'] if ci['high'] else NOT_ESTIMABLE}"
    head = f"Kaplan-Meier median {value['text']} ({ci_text})" if value else \
        f"Kaplan-Meier median {NOT_ESTIMABLE}: " + ("the curve never falls to one half" if cohort else "no card arrived")
    arrived = f"{len(cohort)} {'card' if len(cohort) == 1 else 'cards'} arrived" + \
        (f" {_day(first)} to {_day(last)}" if first else "")
    parts = [head, f"{arrived}, {censored} still open (censored)"]
    if unknown:
        parts.append(f"{len(unknown)} unknown start")
    if median_range:
        bounds = (median_range["low"], median_range["high"])
        parts.append("a start read between two polls moves the median: " +
                     " to ".join(v["text"] if v else NOT_ESTIMABLE for v in bounds))
    return {"method": "kaplan-meier", "estimable": median is not None, "median": value, "ci": ci, "ci_text": ci_text,
            "median_range": median_range, "cohort": len(cohort), "events": len(cohort) - censored, "censored": censored,
            "cohort_start": first.date().isoformat() if first else None, "cohort_end": last.date().isoformat(),
            "unknown_start": {"count": len(unknown), "cards": sorted(unknown)}, "interval_starts": intervals,
            "text": " · ".join(parts)}


def wip(items):
    """The work in progress: the open cards with a known start, and the age of the oldest, in the lead measure."""
    ages = [obs["hours"] for row in items if (obs := row.get("obs")) and obs["censored"] and not obs.get("unknown_start")]
    oldest = _value(max(ages)) if ages else None
    return {"count": len(ages), "oldest": oldest,
            "text": f"WIP {len(ages)} waiting" + (f", oldest {oldest['text']}" if oldest else "")}
