"""Lead-time control chart arithmetic for the board's closed cards (SPEC 111). Stdlib only: no hub, no client, no file API (ADR-0137)."""
import math
import statistics
from datetime import UTC, date, datetime, timedelta

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


# #1354: every card the chart leaves out states its reason in words. The page prints the reason beside the count, and
# the payload carries the same words, so neither a reader of the hub alone nor the page sees a bare number.
EXCLUSION_WORDS = {
    NOT_DELIVERED: "closed as not planned",
    "abandoned": "closed as abandoned",
    OPEN: "still open",
    "no_opened": "no opened date",
    NO_TRANSITION: "no transition into the chosen columns",
    NO_START: "never entered the start column",
    NOT_READ: "not yet read by the history pass",
}


def exclusion_text(reason):
    """One exclusion reason in words; an unknown reason stays its own key."""
    return EXCLUSION_WORDS.get(reason, reason)


def excluded_text(excluded):
    """The exclusions in words beside their counts, one reason per entry (#1354)."""
    return "; ".join(f"{count} {exclusion_text(reason)}" for reason, count in sorted(excluded.items()))


def exclusion_words(excluded):
    """The word for each exclusion reason the payload carries, so the page prints the hub's one vocabulary (#1354)."""
    return {reason: exclusion_text(reason) for reason in excluded}


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


# SPEC 889 #900: the control chart. The hub serves every number and position; the page draws marks only.
# SPEC 1327 #1355: the y axis is always log, with round ticks (#1334); the chart takes no scale option.
# #2311: the axis fits the plotted values. The top is the round tick above the largest mark and the floor the round tick
# below the smallest, so a view of hours-sized cards no longer runs to a fixed 60 days.
LOG_FLOOR_HOURS = 1 / 60      # no log of a span under one minute; the axis floor never drops below it
DEFAULT_TOP_HOURS = 24        # the axis top of a view that plots nothing
PLOT_WIDTH, PLOT_HEIGHT = 672, 212  # the lead chart's usable frame: the 800 x 340 page frame less its 64 px margins
DOT_PX = 9                    # dots closer than this many pixels merge into a numbered cluster
# The round durations the axis may label, in hours, with the one duration format: 1m to 365d.
ROUND_TICKS_HOURS = [1 / 60, 2 / 60, 5 / 60, 15 / 60, 30 / 60, 1, 2, 4, 8, 24, 48, 168, 336, 720, 1440, 2160, 8760]
SHORT_RANGE = timedelta(days=3)      # a view this short labels its x axis in hours, a longer one in days
FIT_SHARE = 0.5                      # the cards cluster when the range they fill is at most half the view
FIT_MIN_PAD = timedelta(hours=1)
TIMEFRAME_SELECTS = "end transition"  # Timeframe keeps a card by the instant it entered the end column

# SPEC 1327 #1331: a lone card is one hollow dot; a cluster is one outlined filled dot of this one fixed radius. The
# count-box radius is gone: a cluster carries its count, its cards and its ranges in the payload, and the plot draws no
# number and no stacked bar.
# SPEC 1327 #1432: the marks stay small. One place names the three sizes, and the payload serves them, so the page and
# the payload cannot disagree.
CLUSTER_RADIUS_PX = 5   # every cluster's dot: one radius, whatever the count it holds
DOT_RADIUS_PX = 3       # a lone card's dot radius
MARK_OUTLINE_PX = 1.5   # the contrast ring on both kinds of mark
FALLBACK_LABEL = "measured from its first time on the board"   # a card that never entered the default start column (#1354)


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


def _round_above(hours):
    """The first round tick above `hours`, the axis top; past the ladder's last tick, the step doubles."""
    for tick in ROUND_TICKS_HOURS:
        if tick > hours:
            return tick
    top = ROUND_TICKS_HOURS[-1]
    while top <= hours:
        top *= 2
    return top


def _round_below(hours):
    """The last round tick below `hours`, the axis floor, never under one minute."""
    floor = LOG_FLOOR_HOURS
    for tick in ROUND_TICKS_HOURS:
        if tick >= hours:
            break
        floor = tick
    return floor


def _axis_ticks(floor, top):
    """The round durations inside the fitted range, the floor and the top included."""
    ticks = [tick for tick in ROUND_TICKS_HOURS if floor <= tick <= top]
    if not ticks or ticks[0] > floor:
        ticks.insert(0, floor)
    if ticks[-1] < top:
        ticks.append(top)
    return ticks


def _axis_marks(hours, rolling, lines):
    """Every value the plot draws: the dots, the band's two edges and the reference lines, so none leaves the plot."""
    marks = [*hours, *(low for _, low, _ in rolling), *(high for _, _, high in rolling)]
    return marks + [line for line in lines if line is not None]


def _day_span(first, last):
    """The closed-date range one cluster carries: each end as the page's day, both when they differ, one when not."""
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    one = f"{a:%b} {a.day}"
    return {"start": first, "end": last, "text": one if a == b else f"{one} to {b:%b} {b.day}"}


def _hour_span(lo, hi):
    """The lead-time range one cluster carries, as the one duration format; one value when both ends agree."""
    low, high = _value(lo), _value(hi)
    return {"min": low, "max": high, "text": low["text"] if lo == hi else f"{low['text']} to {high['text']}"}


def _clusters(dots, width, height, axis="x", limit=None):
    """The dots merged into clusters by the screen-space rule in the caller's plot frame: two marks closer than DOT_PX
    share one cluster (#1146), recomputed for every view from the frame the caller names (#1331). A card past `limit`,
    the p85 of the plotted hours, never merges, and the cluster it seeds stays closed to every later card: an outlier
    stays a lone dot whatever order the dots arrive in. Every cluster serves its fixed radius, its
    count, its cards, its closed-date range and its lead-time range."""
    clusters = []
    for dot in dots:
        home = None
        if limit is None or dot["hours"] <= limit:
            for cluster in clusters:
                if cluster["open"] and math.hypot((cluster["x"] - dot[axis]) * width,
                                                  (cluster["y"] - dot["y"]) * height) < DOT_PX:
                    home = cluster
                    break
        if home is None:
            # a cluster an outlier seeds is closed: no later card ever merges into it, so a card past p85 stays a lone
            # dot however close its close, in every order the dots arrive (#1331)
            clusters.append({"x": dot[axis], "y": dot["y"], "dots": [dot],
                             "open": limit is None or dot["hours"] <= limit})
        else:
            home["dots"].append(dot)
    for cluster in clusters:
        members = cluster["dots"]
        cluster.update(r=DOT_RADIUS_PX if len(members) == 1 else CLUSTER_RADIUS_PX,
                       count=len(members), cards=[member["n"] for member in members],
                       closed=_day_span(min(member["closed"] for member in members),
                                        max(member["closed"] for member in members)),
                       lead=_hour_span(min(member["hours"] for member in members),
                                       max(member["hours"] for member in members)))
        del cluster["open"]
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


def _rolling(hours):
    """Centered geometric mean and one sigma in log space per dot, in hours: the chart's one axis is log."""
    size = _window_size(len(hours))
    half = (size - 1) // 2
    out = []
    for i in range(len(hours)):
        span = hours[max(0, i - half) : i + half + 1]
        logs = [math.log(max(h, LOG_FLOOR_HOURS)) for h in span]
        mean, sigma = statistics.fmean(logs), statistics.pstdev(logs)
        out.append((math.exp(mean), math.exp(mean - sigma), math.exp(mean + sigma)))
    return out, size


def control_chart(items, timeframe=None, now=None, start=None, end=None, measure=MEASURE, plot=None):
    """The chart over a timeframe in days, or over an explicit zoom range `start`..`end` (#900). A zoom names its dates.
    The y axis is always log (#1355). The caller names the plot frame `plot` (its width and height in pixels); the
    clusters recompute from that frame and the same screen-space rule on every call, so a zoom or a resize redraws the
    marks its view holds (#1331)."""
    now = now or datetime.now(UTC)
    zoom = start is not None
    plot_frame = {"width": plot["width"], "height": plot["height"]} if plot else \
        {"width": PLOT_WIDTH, "height": PLOT_HEIGHT}
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
    rolling, size = _rolling(hours)
    fit = _fit([utc(row["c"]) for row in plotted], start, end)
    fit_start, fit_end = (datetime.fromisoformat(fit[k]) for k in ("start", "end")) if fit else (None, None)
    fit_span = (fit_end - fit_start).total_seconds() if fit else 0
    average = statistics.fmean(hours) if hours else None
    p85_hours = _p85(hours) if hours else None
    median_hours = km["median"]["hours"] if km["median"] else None
    marks = _axis_marks(hours, rolling, [average, p85_hours, median_hours,
                                         min(hours) if hours else None, max(hours) if hours else None])
    floor = _round_below(min(marks)) if marks else 1.0
    top = _round_above(max(marks)) if marks else DEFAULT_TOP_HOURS
    def position(h):
        return _clamp((math.log10(max(h, floor)) - math.log10(floor))
                      / (math.log10(top) - math.log10(floor)))
    ticks = [(h, _duration(h), position(h)) for h in _axis_ticks(floor, top)]

    dots = []
    for row, hrs, (mean, low, high) in zip(plotted, hours, rolling, strict=True):
        closed = utc(row["c"])
        x = _clamp((closed - start).total_seconds() / span) if span > 0 else 1.0
        flowing, held = row.get("flowing", hrs), row.get("held", 0.0)
        dot = {"n": row["n"], "title": row.get("t", ""), "closed": closed.date().isoformat(), "hours": hrs,
               "text": _duration(hrs), "flowing": flowing, "held": held,
               "flowing_text": _duration(flowing), "held_text": _duration(held),
               "approx": row.get("approx", False), "unknown_first": row.get("unknown_first", False),
               "fallback": bool(row.get("fallback")), **_stack_fields(row, position), "x": x, "y": position(hrs),
               "rolling": {"hours": mean, "low": low, "high": high, "text": _duration(mean),
                           "low_text": _duration(low), "high_text": _duration(high),
                           "y": position(mean), "y_low": position(low), "y_high": position(high)}}
        if fit:   # the fit range's own position for the same dot, so the page switches range from this one payload (#1334)
            dot["xf"] = _clamp((closed - fit_start).total_seconds() / fit_span) if fit_span > 0 else 1.0
        dots.append(dot)

    outlier_limit = _p85(hours) if hours else None
    clusters = _clusters(dots, plot_frame["width"], plot_frame["height"], limit=outlier_limit)
    band = _band(dots) if dots else None
    if fit and band:
        band["fit"] = _band(dots, "xf")

    def line(hrs, label):
        return None if hrs is None else {"hours": hrs, "text": _duration(hrs), "y": position(hrs), "label": label}

    median = line(km["median"]["hours"], "Median") if km["median"] else \
        {"hours": None, "text": NOT_ESTIMABLE, "y": None, "label": "Median"}
    median.update(estimable=km["estimable"], ci=km["ci"], ci_text=km["ci_text"])   # the Kaplan-Meier median (#1250)
    excluded_total = sum(excluded.values())
    summary = {"average": line(average, "Average") if hours else None,
               "median": median,
               "p85": line(p85_hours, "p85") if hours else None,
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
        scope = f"last {timeframe * 24:g} hours" if timeframe and timeframe < 7 else f"last {timeframe} days" if timeframe else "all closed cards"
    # #1354: a dot measured from its board entry rather than the chosen start column says so, so the chart never
    # claims a measure it did not use.
    fallback_cards = sorted(row["n"] for row in plotted if row.get("fallback"))
    fallback = {"count": len(fallback_cards), "cards": fallback_cards,
                "text": f"{len(fallback_cards)} {'card' if len(fallback_cards) == 1 else 'cards'} {FALLBACK_LABEL}"
                        if fallback_cards else ""}
    note = f", {fallback['text']}" if fallback_cards else ""
    text = f"{scope}, {measure}, {summary['plotted_text']}{note}; Timeframe selects by {TIMEFRAME_SELECTS}"
    return {
        "plot_frame": plot_frame,
        # #1432: the page draws these sizes and names none of its own, so a mark can never grow here and stay small there.
        "mark": {"dot_radius": DOT_RADIUS_PX, "cluster_radius": CLUSTER_RADIUS_PX, "outline": MARK_OUTLINE_PX},
        "measure": measure,
        "axis": "log",
        "timeframe": timeframe,
        "window": {"size": size, "unit": "issues", "centered": True},
        "dots": dots,
        "clusters": clusters,
        **({"clusters_fit": _clusters(dots, plot_frame["width"], plot_frame["height"], "xf", outlier_limit)} if fit else {}),
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
        "excluded_text": excluded_text(excluded),
        "excluded_reasons": exclusion_words(excluded),
        "fallback": fallback,
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
