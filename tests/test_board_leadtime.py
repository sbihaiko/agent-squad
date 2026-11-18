"""The lead-time chart arithmetic turns closed board cards into dots, a rolling average and a p85 summary (SPEC 111)."""
import ast
import importlib.util
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
SCRIPT = SCRIPTS / "board_leadtime.py"
BASE = datetime(2026, 10, 1, tzinfo=UTC)
ALLOWED_IMPORTS = {"__future__", "datetime", "math", "statistics"}


@pytest.fixture(scope="module")
def lt():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("board_leadtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(moment):
    return moment.isoformat()


def _flags(hours):
    return {"hours": hours, "flowing": hours, "held": 0.0, "approx": False, "unknown_first": False}


def _card(n, hours, column="Done"):
    return {"n": n, "o": _iso(BASE), "c": _iso(BASE + timedelta(hours=hours)), "column": column, **_flags(hours)}


def _open(n):
    return {"n": n, "o": _iso(BASE), "c": None, "column": "Doing"}


def _no_opened(n, hours):
    return {"n": n, "o": None, "c": _iso(BASE + timedelta(hours=hours)), "column": "Done", **_flags(hours)}


def test_a_dot_is_the_flowing_and_held_hours_of_its_span(lt):
    row = {"n": 7, "o": "2026-10-01T00:00:00+00:00", "c": "2026-10-02T04:00:00+00:00", "column": "Done", **_flags(28.0)}

    result = lt.lead_time([row])

    assert result["series"][0]["n"] == 7
    assert result["series"][0]["hours"] == 28.0


def test_abandoned_open_and_no_opened_rows_give_no_dot_and_count_each_reason(lt):
    items = [
        _card(1, 5),
        _card(2, 9, column="Abandoned"),
        _open(3),
        _open(4),
        _no_opened(5, 3),
    ]

    result = lt.lead_time(items)

    assert [dot["n"] for dot in result["series"]] == [1]
    assert result["excluded"] == {"abandoned": 1, "no_opened": 1}  # re-anchored (#1250): an open card is censored in the survival reading, never an exclusion


def test_a_card_closed_as_not_planned_is_no_dot_and_is_counted_not_planned(lt):
    # #921: the hub names a card closed by GitHub as not planned with the chart's own reason, so it never enters the series.
    items = [
        _card(1, 5),
        {**_card(2, 9), "reason": "not_planned"},
        {**_card(3, 7), "reason": "not_planned"},
    ]

    result = lt.lead_time(items)

    assert [dot["n"] for dot in result["series"]] == [1]
    assert result["excluded"] == {"not_planned": 2}
    assert result["summary"]["median"]["hours"] == 5.0


def test_the_carrier_spelling_never_reaches_the_chart(lt):
    """The chart reads the hub's own reason word: a spelling the chart does not own (`reopened`, `superseded`) excludes
    nothing, because board_columns maps the carrier's vocabulary, not this module. `duplicate` belongs to that mapping,
    so it never arrives here as a reason of its own."""
    result = lt.lead_time([{**_card(1, 5), "reason": "superseded"}, {**_card(2, 7), "reason": "reopened"}])

    assert [dot["n"] for dot in result["series"]] == [1, 2]
    assert result["excluded"] == {}


def test_a_completed_close_is_delivered_whatever_the_reason_field_says(lt):
    result = lt.lead_time([{**_card(1, 5), "reason": "completed"}])

    assert [dot["n"] for dot in result["series"]] == [1]
    assert result["excluded"] == {}


def test_dots_sort_by_close_time_not_input_order(lt):
    items = [_card(1, 30), _card(2, 10), _card(3, 20)]

    result = lt.lead_time(items)

    assert [dot["n"] for dot in result["series"]] == [2, 3, 1]


def test_rolling_average_covers_the_last_window_issues_and_fewer_at_the_start(lt):
    items = [_card(n, hours) for n, hours in enumerate([1, 2, 3, 4, 5], start=1)]

    result = lt.lead_time(items, window=3)

    assert [dot["rolling_hours"] for dot in result["series"]] == [1.0, 1.5, 2.0, 3.0, 4.0]


def test_summary_reports_average_median_minimum_maximum_and_p85_of_the_dots(lt):
    items = [_card(n, hours) for n, hours in enumerate(range(1, 11), start=1)]

    summary = lt.lead_time(items)["summary"]

    assert summary["average"]["hours"] == 5.5
    assert summary["median"]["hours"] == 5, "re-anchored (#1250): the Kaplan-Meier curve over 1h..10h falls to one half at 5h"
    assert summary["minimum"]["hours"] == 1.0
    assert summary["maximum"]["hours"] == 10.0
    assert summary["p85"]["hours"] == 9.0


def test_each_summary_value_has_a_human_duration_text(lt):
    items = [_card(n, hours) for n, hours in enumerate(range(1, 11), start=1)]

    summary = lt.lead_time(items)["summary"]

    assert summary["average"]["text"] == "5h 30m"
    assert summary["maximum"]["text"] == "10h"
    assert summary["p85"]["text"] == "9h"


def test_a_day_and_hours_render_as_days_then_hours(lt):
    result = lt.lead_time([_card(1, 28)])

    assert result["summary"]["average"] == {"hours": 28.0, "text": "1d 4h"}


def test_result_names_the_window_in_issues_and_the_measure(lt):
    result = lt.lead_time([_card(1, 5)])

    assert result["measure"] == "on board -> closed"
    assert result["window"] == {"size": 20, "unit": "issues"}
    assert lt.lead_time([_card(1, 5)], window=3)["window"] == {"size": 3, "unit": "issues"}


def test_result_is_json_serializable(lt):
    items = [_card(1, 5), _open(2)]

    result = json.loads(json.dumps(lt.lead_time(items)))
    assert result["excluded"] == {} and result["excluded_cards"] == {}  # re-anchored (#1250): an open card is censored in the survival reading, never an exclusion


def test_no_dots_gives_an_empty_series_and_no_summary_numbers(lt):
    items = [_open(1), _card(2, 6, column="Abandoned")]

    result = lt.lead_time(items)

    assert result["series"] == []
    assert result["summary"] is None
    assert result["excluded"] == {"abandoned": 1}  # re-anchored (#1250): an open card is censored in the survival reading, never an exclusion


def test_empty_input_gives_an_empty_series_and_no_summary(lt):
    result = lt.lead_time([])

    assert result["series"] == []
    assert result["summary"] is None


def test_module_imports_no_hub_github_client_or_file_api(lt):
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    opens = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open"]

    assert imported <= ALLOWED_IMPORTS
    assert opens == []


# SPEC 889 #900: the Jira-style control chart. Every number is computed here; the page places marks and draws none.
NOW = BASE + timedelta(days=60)


def _closed(n, hours, days_ago=0):
    closed = NOW - timedelta(days=days_ago)
    opened = closed - timedelta(hours=hours)
    return {"n": n, "t": f"card {n}", "o": _iso(opened), "c": _iso(closed), "column": "Done", **_flags(hours)}


def _chart(lt, items, **kw):
    return lt.control_chart(items, now=NOW, **kw)


def test_the_rolling_window_is_twenty_percent_odd_and_at_least_five(lt):
    assert _chart(lt, [_closed(n, n) for n in range(1, 11)])["window"]["size"] == 5, "10 issues: 20% is 2, raised to 5"
    assert _chart(lt, [_closed(n, n) for n in range(1, 31)])["window"]["size"] == 7, "30 issues: 20% is 6, odd is 7"
    assert _chart(lt, [_closed(n, n) for n in range(1, 51)])["window"]["size"] == 11, "50 issues: 20% is 10, odd is 11"


def test_the_rolling_mean_is_centered_on_each_dot_and_shrinks_at_the_ends(lt):
    result = _chart(lt, [_closed(n, n) for n in range(1, 10)])   # hours 1..9, window 5

    means = [dot["rolling"]["hours"] for dot in result["dots"]]
    # The chart's one axis is log, so the rolling mean is the geometric mean of the window: cube root of 1·2·3 and of
    # 7·8·9 at the ends, the fifth root of 3·4·5·6·7 in the middle.
    assert means[0] == pytest.approx(1.8171205928321397, rel=1e-9)
    assert means[4] == pytest.approx(4.789388953186989, rel=1e-9)
    assert means[8] == pytest.approx(7.958114415792785, rel=1e-9)


def test_the_band_is_one_sigma_in_log_space_around_the_geometric_mean(lt):
    result = _chart(lt, [_closed(n, h) for n, h in enumerate([2, 4, 6, 8, 10], start=1)])

    centre = result["dots"][2]["rolling"]
    assert centre["hours"] == pytest.approx(5.210342169394703, rel=1e-9)
    assert centre["low"] == pytest.approx(2.951249402510582, rel=1e-9)
    assert centre["high"] == pytest.approx(9.19870258985181, rel=1e-9)


def test_the_y_axis_is_always_log_with_round_ticks(lt):
    """#1355: the chart takes no scale option; every view draws the log axis with its round ticks (#1334).

    Re-anchored (#2311): the axis fits the plotted values. The top is the next round tick above the largest value and
    the floor the round tick below the smallest, so the ladder each view labels follows its own data."""
    small = _chart(lt, [_closed(1, 3), _closed(2, 30 * 24)])
    long = _chart(lt, [_closed(1, 29 * 24), _closed(2, 5)])
    month = _chart(lt, [_closed(1, 30 * 24), _closed(2, 5)])

    assert small["axis"] == long["axis"] == month["axis"] == "log"
    assert [tick["text"] for tick in small["ticks"]] == \
        ["2h", "4h", "8h", "1d", "2d", "7d", "14d", "30d", "60d"], \
        "the small view's ladder does not run from the tick below its 3h card to the tick above its 30d card"
    assert [tick["text"] for tick in long["ticks"]] == ["4h", "8h", "1d", "2d", "7d", "14d", "30d"], \
        "the long view's ladder does not run from the tick below its 5h card to the tick above its 29d card"
    for view, name in ((long, "long"), (month, "month")):
        week = next(tick["y"] for tick in view["ticks"] if tick["text"] == "7d")
        top = max(dot["y"] for dot in view["dots"])
        assert week < top < 1.0, f"the {name} view's axis does not carry its month-long card above the week tick"


def test_the_axis_fits_the_plotted_values(lt):
    """#2311: the largest value here is 5h, so the top is 8h and not the fixed 60 days; the floor is the round tick
    below the 1h card, and the ticks are the round durations inside that range."""
    result = _chart(lt, [_closed(n, h) for n, h in enumerate([1, 2, 3, 4, 5], start=1)])

    assert [tick["text"] for tick in result["ticks"]] == ["30m", "1h", "2h", "4h", "8h"]
    assert [tick["hours"] for tick in result["ticks"]] == [0.5, 1, 2, 4, 8]
    assert [tick["y"] for tick in result["ticks"]] == [0, 0.25, 0.5, 0.75, 1.0]


def test_every_dot_band_and_reference_line_stays_inside_the_plot(lt):
    """#2311: the fitted axis carries every mark, so no dot, band edge, stack edge or reference line leaves the plot."""
    chart = _chart(lt, [_closed(n, h) for n, h in enumerate([1, 2, 3, 4, 5], start=1)])

    marks = [dot["y"] for dot in chart["dots"]]
    marks += [point["y"] for point in chart["band"]["polygon"]]
    marks += [band[edge] for dot in chart["dots"] for band in dot["stack"] for edge in ("y0", "y")]
    lines = (chart["average"], chart["median"], chart["p85"],
             chart["summary"]["minimum"], chart["summary"]["maximum"])
    marks += [line["y"] for line in lines if line and line["y"] is not None]

    assert all(0.0 <= y <= 1.0 for y in marks), f"a mark leaves the fitted plot: {marks}"


def test_the_axis_floor_follows_cards_under_thirty_minutes(lt):
    """#2311: the floor is no longer fixed at 30 minutes, so a 5-minute card sits above the axis, not on it."""
    result = _chart(lt, [_closed(1, 5 / 60), _closed(2, 20 / 60)])

    assert [tick["text"] for tick in result["ticks"]] == ["2m", "5m", "15m", "30m"]
    assert min(dot["y"] for dot in result["dots"]) > 0.0, "a card under the old 30-minute floor still sits on the axis"


def test_the_axis_floor_sits_strictly_below_a_card_at_the_smallest_round_tick(lt):
    """#2311: the floor is the round tick below the smallest card, so a view of one-minute cards puts the floor under
    the minute: the log range never collapses and no card sits on the axis."""
    result = _chart(lt, [_closed(n, 1 / 60) for n in (1, 2)])

    floor, top = result["ticks"][0], result["ticks"][-1]
    assert floor["hours"] < 1 / 60, f"the floor is not below the one-minute card: {floor['hours']}"
    assert floor["hours"] < top["hours"], "the fitted range collapsed to a zero or negative log span"
    assert min(dot["y"] for dot in result["dots"]) > 0.0, "a one-minute card sits on the axis"


def test_the_axis_labels_the_round_durations_inside_a_between_rung_range(lt):
    """#2311: a view whose smallest card is 5 days and whose largest is 40 days labels the round durations inside that
    range, so neither end is a raw mark."""
    result = _chart(lt, [_closed(1, 5 * 24), _closed(2, 40 * 24)])

    assert [tick["text"] for tick in result["ticks"]] == ["2d", "7d", "14d", "30d", "60d"]


def test_the_axis_keeps_round_durations_past_the_ladders_last_tick(lt):
    """#2311: past 365 days the round step doubles, so a long view still labels round durations and never only its two
    raw ends."""
    result = _chart(lt, [_closed(1, 30000), _closed(2, 40000)])   # about 3.4 and 4.6 years

    assert [tick["hours"] for tick in result["ticks"]] == [8760, 17520, 35040, 70080]
    assert all(tick["text"].endswith("d") for tick in result["ticks"]), \
        f"a tick past the ladder is not a round duration: {[tick['text'] for tick in result['ticks']]}"
    assert len({tick["hours"] for tick in result["ticks"]}) >= 2, "the axis drew fewer than two distinct ticks"


def test_a_timeframe_keeps_only_the_cards_closed_inside_it(lt):
    items = [_closed(1, 5, days_ago=1), _closed(2, 5, days_ago=20)]

    assert [dot["n"] for dot in _chart(lt, items, timeframe=7)["dots"]] == [1]
    assert sorted(dot["n"] for dot in _chart(lt, items, timeframe=None)["dots"]) == [1, 2]


def test_the_date_axis_places_the_range_end_at_the_right_edge(lt):
    result = _chart(lt, [_closed(1, 5, days_ago=0), _closed(2, 5, days_ago=5)], timeframe=10)

    xs = {dot["n"]: dot["x"] for dot in result["dots"]}
    assert xs[1] == 1.0, "a card closed now sits on the right edge"
    assert xs[2] == pytest.approx(0.5), "a card closed 5 of 10 days ago sits in the middle"


def test_cards_clear_at_the_shared_plot_frame_stay_in_separate_clusters(lt):
    # Re-anchored (#1355): the one axis is log, so the worked example is a pair whose screen gap clears the dot.
    result = _chart(lt, [_closed(1, 5), _closed(2, 8)])

    assert result["plot_frame"] == {"width": 672, "height": 212}
    assert sorted(len(cluster["dots"]) for cluster in result["clusters"]) == [1, 1]
    first, second = result["dots"]
    assert math.hypot((first["x"] - second["x"]) * result["plot_frame"]["width"],
                      (first["y"] - second["y"]) * result["plot_frame"]["height"]) > lt.DOT_PX


def test_dots_closer_than_one_dot_merge_into_one_cluster(lt):
    items = [_closed(1, 5, days_ago=2), _closed(2, 5, days_ago=2), _closed(3, 90, days_ago=2)]

    clusters = _chart(lt, items)["clusters"]

    assert sorted(len(c["dots"]) for c in clusters) == [1, 2]
    merged = next(c for c in clusters if len(c["dots"]) == 2)
    assert [d["n"] for d in merged["dots"]] == [1, 2]


def test_the_window_text_names_the_days_and_the_plotted_sample(lt):
    items = [_closed(1, 5, days_ago=1), _open(2), _no_opened(3, 4)]

    result = _chart(lt, items, timeframe=30)

    assert result["text"] == "last 30 days, on board -> closed, 1 issue plotted; Timeframe selects by end transition"
    assert result["excluded_total"] == 1  # re-anchored (#1250): an open card is censored in the survival reading, never an exclusion


def test_the_average_median_and_p85_carry_values_and_positions(lt):
    items = [_closed(n, h) for n, h in enumerate(range(1, 11), start=1)]

    result = _chart(lt, items)

    assert result["average"]["hours"] == 5.5
    assert result["median"]["hours"] == 5, "re-anchored (#1250): the Kaplan-Meier curve over 1h..10h falls to one half at 5h"
    assert result["p85"]["hours"] == 9.0
    assert 0.0 <= result["p85"]["y"] <= 1.0
    assert result["p85"]["text"] == "9h"


def test_no_closed_card_gives_no_dots_and_no_lines(lt):
    result = _chart(lt, [_open(1)])

    assert result["dots"] == []
    assert result["average"] is None
    assert result["median"]["hours"] is None and result["median"]["y"] is None, "no line for a median that is not estimable"
    assert result["text"] == "all closed cards, on board -> closed, 0 issues plotted; Timeframe selects by end transition"
    assert result["excluded_total"] == 0  # re-anchored (#1250): an open card is censored in the survival reading, never an exclusion


# SPEC 889 #900: a drag across the plot zooms to a date range. The hub recomputes the chart for that range.
def test_a_zoom_range_keeps_only_the_cards_closed_inside_it(lt):
    items = [_closed(1, 5, days_ago=1), _closed(2, 5, days_ago=5), _closed(3, 5, days_ago=20)]
    start, end = NOW - timedelta(days=6), NOW - timedelta(days=2)

    result = _chart(lt, items, start=start, end=end)

    assert sorted(dot["n"] for dot in result["dots"]) == [2]


def test_a_zoom_range_places_its_start_on_the_left_edge_and_its_end_on_the_right(lt):
    start, end = NOW - timedelta(days=10), NOW
    result = _chart(lt, [_closed(1, 5, days_ago=0), _closed(2, 5, days_ago=5)], start=start, end=end)

    xs = {dot["n"]: dot["x"] for dot in result["dots"]}
    assert xs[1] == 1.0, "a card closed at the zoom end sits on the right edge"
    assert xs[2] == pytest.approx(0.5), "a card closed 5 of 10 days ago sits in the middle"


def test_the_chart_reports_the_range_it_plots(lt):
    start, end = NOW - timedelta(days=10), NOW
    result = _chart(lt, [_closed(1, 5, days_ago=1)], start=start, end=end)

    assert result["range"] == {"start": _iso(start), "end": _iso(end), "text": "Nov 20 to Nov 30 UTC"}, \
        "re-anchored (#1334): the range carries the text the page prints"


def test_a_zoom_range_names_its_dates_in_the_window_text(lt):
    start, end = datetime(2026, 9, 30, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC)
    result = _chart(lt, [_closed(1, 5, days_ago=20)], start=start, end=end)

    assert result["text"] == "Sep 30 to Oct 8, on board -> closed, 0 issues plotted; Timeframe selects by end transition"


def test_a_zoom_range_with_no_card_keeps_its_range_and_no_dots(lt):
    start, end = NOW - timedelta(days=3), NOW - timedelta(days=2)
    result = _chart(lt, [_closed(1, 5, days_ago=20)], start=start, end=end)

    assert result["dots"] == []
    assert result["range"] == {"start": _iso(start), "end": _iso(end), "text": "Nov 27 00:00 to Nov 28 00:00 UTC"}, \
        "re-anchored (#1334): the range carries the text the page prints"


def test_a_dot_is_the_flowing_and_held_hours_so_the_parked_time_is_out(lt):
    row = {"n": 1, "o": _iso(BASE), "c": _iso(BASE + timedelta(hours=10)), "column": "Closed", "reason": None,
           "hours": 6.0, "flowing": 4.0, "held": 2.0, "approx": False, "unknown_first": False}
    series = lt.lead_time([row])["series"][0]
    assert series["hours"] == 6.0
    assert series["flowing_hours"] == 4.0
    assert series["held_hours"] == 2.0


def test_a_dot_marks_an_approximate_or_unknown_first_interval(lt):
    row = {"n": 1, "o": _iso(BASE), "c": _iso(BASE + timedelta(hours=2)), "column": "Closed", "reason": None,
           "hours": 2.0, "flowing": 2.0, "held": 0.0, "approx": True, "unknown_first": True}
    series = lt.lead_time([row])["series"][0]
    assert series["approx"] is True
    assert series["unknown_first"] is True


# SPEC 1063 #1065: the caption names the span, the measure and the plotted sample; a fit-to-data range and hour or day axis labels.
def _at(n, closed):
    return {"n": n, "t": f"card {n}", "o": _iso(closed - timedelta(hours=1)), "c": _iso(closed), "column": "Done",
            **_flags(1.0)}


def test_the_caption_names_the_span_the_measure_and_the_plotted_sample(lt):
    result = _chart(lt, [_closed(1, 5, days_ago=1)], timeframe=30)

    assert result["text"] == "last 30 days, on board -> closed, 1 issue plotted; Timeframe selects by end transition"


def test_the_caption_changes_with_the_start_and_end_filters(lt):
    items = [_closed(1, 5, days_ago=1)]

    default = _chart(lt, items, timeframe=30)["text"]
    chosen = _chart(lt, items, timeframe=30, measure="opened -> Doing")["text"]

    assert chosen != default
    assert "opened -> Doing" in chosen


def test_a_clustered_view_offers_a_fit_range_that_names_its_instants(lt):
    items = [_at(1, NOW - timedelta(hours=5)), _at(2, NOW - timedelta(hours=4)), _at(3, NOW - timedelta(hours=3))]

    fit = _chart(lt, items, timeframe=30)["fit"]

    assert fit["start"] == "2026-11-29T18:00:00+00:00"
    assert fit["end"] == "2026-11-29T22:00:00+00:00"
    assert fit["text"] == "Nov 29 18:00 to Nov 29 22:00 UTC"


def test_a_spread_view_offers_no_fit_range(lt):
    items = [_closed(1, 5, days_ago=1), _closed(2, 5, days_ago=10), _closed(3, 5, days_ago=20)]

    assert _chart(lt, items, timeframe=30)["fit"] is None


def test_a_short_range_labels_its_axis_in_hours_and_a_long_one_in_days(lt):
    short = _chart(lt, [_closed(1, 5, days_ago=0)], timeframe=1)
    long = _chart(lt, [_closed(1, 5, days_ago=0)], timeframe=30)

    assert [tick["text"] for tick in short["xticks"]] == ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "00:00"]
    assert [tick["text"] for tick in long["xticks"]] == ["Oct 31", "Nov 5", "Nov 10", "Nov 15", "Nov 20", "Nov 25", "Nov 30"]


def test_a_fit_range_labels_its_axis_in_hours(lt):
    items = [_at(1, NOW - timedelta(hours=5)), _at(2, NOW - timedelta(hours=4)), _at(3, NOW - timedelta(hours=3))]
    fit = _chart(lt, items, timeframe=30)["fit"]

    zoomed = _chart(lt, items, start=datetime.fromisoformat(fit["start"]), end=datetime.fromisoformat(fit["end"]))

    assert [tick["text"] for tick in zoomed["xticks"]] == ["18:00", "18:40", "19:20", "20:00", "20:40", "21:20", "22:00"]


# SPEC 1327 #1331: a cluster is one outlined filled dot of one fixed radius; a lone card is a hollow dot.
def test_a_single_dot_keeps_the_plain_dot_radius(lt):
    clusters = _chart(lt, [_closed(1, 5, days_ago=2)])["clusters"]

    assert [c["r"] for c in clusters] == [lt.DOT_RADIUS_PX], "a lone card stays a plain dot"


def test_a_cluster_is_one_fixed_radius_dot_with_its_count_cards_and_ranges(lt):
    # Re-anchored (#2311): the axis now fits the plotted hours, so the three cards sit closer together to stay within
    # one dot on the frame: 5.0h, 5.05h and 5.1h span about 6 px of the fitted 4h..8h axis.
    items = [_closed(n, hours, days_ago=2) for n, hours in enumerate((5.0, 5.05, 5.1), start=1)]

    clusters = _chart(lt, items)["clusters"]

    assert len(clusters) == 1 and len(clusters[0]["dots"]) == 3
    cluster = clusters[0]
    assert cluster["r"] == lt.CLUSTER_RADIUS_PX, "every cluster takes the one fixed radius"
    assert cluster["count"] == 3 and cluster["cards"] == [1, 2, 3]
    assert cluster["lead"]["min"] == {"hours": 5.0, "text": "5h"}
    assert cluster["lead"]["max"] == {"hours": 5.1, "text": "5h 6m"}
    assert cluster["lead"]["text"] == "5h to 5h 6m"
    assert cluster["closed"]["text"] == "Nov 28", "one shared close names one day"


def test_a_lone_dot_names_its_own_card_date_range_and_lead_range(lt):
    cluster = _chart(lt, [_closed(7, 6, days_ago=2)])["clusters"][0]

    assert cluster["count"] == 1 and cluster["cards"] == [7]
    assert cluster["closed"]["text"] == "Nov 28"
    assert cluster["lead"] == {"min": {"hours": 6.0, "text": "6h"}, "max": {"hours": 6.0, "text": "6h"},
                               "text": "6h"}, "one value names both ends of a one-card range"


# SPEC 1327 #1432: the marks are small. The numbers are literals here, never the module's own constants, so a later
# change that grows a mark fails this test; the payload names the same three sizes the page draws.
def test_the_marks_are_small_and_the_payload_names_their_size(lt):
    lone = _chart(lt, [_closed(1, 5, days_ago=2)])
    cluster = _chart(lt, [_closed(n, 5, days_ago=2) for n in (1, 2)])

    assert [c["r"] for c in lone["clusters"]] == [3], "a lone card draws 3 px"
    assert [c["r"] for c in cluster["clusters"]] == [5], "a cluster draws 5 px"
    assert lone["mark"] == {"dot_radius": 3, "cluster_radius": 5, "outline": 1.5}


# #1432: every chart the module builds carries the same size dict, whatever names its window: the control chart over all
# closed cards, one over a timeframe, and one whose marks cluster into a fit range. A path that drops `mark` would leave
# the page to invent its own sizes.
@pytest.mark.parametrize("kind", ["all", "timeframe", "fit"])
def test_every_chart_kind_carries_the_one_mark_size_dict(lt, kind):
    items = [_closed(n, 5, days_ago=2) for n in (1, 2)]
    if kind == "all":
        chart = _chart(lt, items)
    elif kind == "timeframe":
        chart = _chart(lt, items, timeframe=30)
    else:
        chart = _chart(lt, [_at(n, NOW - timedelta(hours=h)) for n, h in ((1, 5), (2, 4), (3, 3))], timeframe=30)
        assert chart["clusters_fit"], "the fit path drew no clusters_fit"

    assert chart["mark"] == {"dot_radius": 3, "cluster_radius": 5, "outline": 1.5}, kind


def test_the_cluster_radius_never_grows_with_its_count(lt):
    few = _chart(lt, [_closed(n, 5, days_ago=2) for n in range(1, 4)])["clusters"][0]["r"]
    many = _chart(lt, [_closed(n, 5, days_ago=2) for n in range(1, 13)])["clusters"][0]["r"]

    assert few == many == lt.CLUSTER_RADIUS_PX, "the fixed radius holds whatever the count"


def test_a_card_past_p85_never_merges_into_a_cluster_seeded_first(lt):
    # the outlier (10.2h) closes first and sits one step over p85 (10h), within DOT_PX of the cards at p85 on the plot
    # frame; without the closed-seed rule its cluster would take the seven cards at 10h
    items = [_closed(1, 10.2, days_ago=2)] + [_closed(n, 10, days_ago=2) for n in range(2, 9)]

    result = _chart(lt, items)
    clusters = result["clusters"]

    assert sorted(c["count"] for c in clusters) == [1, 7]
    assert next(c for c in clusters if 1 in c["cards"])["count"] == 1, "the outlier stays a lone dot, however close its close"
    assert next(c for c in clusters if c["count"] == 7)["cards"] == [2, 3, 4, 5, 6, 7, 8]
    assert _frame_gap(result, 1, 2) < lt.DOT_PX, "the outlier sits within the merge distance of a card at p85"


def test_a_card_past_p85_never_merges_into_a_cluster_seeded_last(lt):
    # the same cards in the other order: the cards at p85 close first and merge, then the outlier closes last and stays lone
    items = [_closed(n, 10, days_ago=2) for n in range(1, 8)] + [_closed(8, 10.2, days_ago=2)]

    result = _chart(lt, items)
    clusters = result["clusters"]

    assert sorted(c["count"] for c in clusters) == [1, 7]
    assert next(c for c in clusters if c["count"] == 7)["cards"] == [1, 2, 3, 4, 5, 6, 7]
    assert next(c for c in clusters if 8 in c["cards"])["count"] == 1
    assert _frame_gap(result, 8, 1) < lt.DOT_PX, "the outlier sits within the merge distance of a card at p85"


def _frame_gap(result, first, second):
    dots = {d["n"]: d for d in result["dots"]}
    frame = result["plot_frame"]
    return math.hypot((dots[first]["x"] - dots[second]["x"]) * frame["width"],
                      (dots[first]["y"] - dots[second]["y"]) * frame["height"])


def test_clusters_follow_the_caller_plot_size(lt):
    # Re-anchored (#1355): on the log axis this pair clears the dot in the full frame and merges in the shorter one.
    # Re-anchored (#2311): the fitted axis spreads 5h and 5.2h about 12 px on the 212 px frame and about 6 px on the
    # 106 px one, so the same pair still names the frame's share of the rule.
    items = [_closed(1, 5), _closed(2, 5.2)]
    full = {"width": 672, "height": 212}

    drawn_apart = _chart(lt, items, plot=full)
    assert sorted(len(c["dots"]) for c in drawn_apart["clusters"]) == [1, 1]

    squashed = _chart(lt, items, plot={"width": 672, "height": 106})
    assert [c["count"] for c in squashed["clusters"]] == [2], \
        "one screen-space rule in the caller's plot frame: a shorter frame merges the marks the full frame holds apart"


# SPEC 1218 #1256: the chart breaks a card's life into its columns and reports the gate's share.
def _gated(n, hours, columns, gate):
    return {**_card(n, hours), "columns": columns, "gate": gate}


def test_the_stack_splits_a_row_into_its_column_hours(lt):
    row = _gated(1, 10.0, {"Doing": 4.0, "Gating": 2.0, "Closed": 4.0}, 2.0)

    stack = lt.lead_time([row])["series"][0]["stack"]

    assert {band["column"]: band["hours"] for band in stack} == {"Doing": 4.0, "Gating": 2.0, "Closed": 4.0}
    assert sum(band["hours"] for band in stack) == pytest.approx(row["hours"]), \
        "the column totals disagree with the measure beside them"
    assert next(band for band in stack if band["column"] == "Gating")["text"] == "2h"


def test_a_plain_row_stacks_its_whole_measure_in_its_own_column(lt):
    stack = lt.lead_time([_card(1, 5)])["series"][0]["stack"]

    assert [(band["column"], band["hours"]) for band in stack] == [("Done", 5.0)], "a plain row invents no band"


def test_gate_time_reads_the_gating_hours_and_a_card_that_never_gated_adds_none(lt):
    items = [_gated(1, 10.0, {"Doing": 6.0, "Gating": 4.0}, 4.0), _gated(2, 8.0, {"Doing": 8.0}, 0.0)]

    summary = lt.lead_time(items)["summary"]

    assert summary["gate"] == {"hours": 4.0, "text": "4h"}


def test_a_card_that_never_gated_draws_no_gating_band(lt):
    stack = lt.lead_time([_gated(1, 8.0, {"Doing": 8.0}, 0.0)])["series"][0]["stack"]

    assert "Gating" not in [band["column"] for band in stack]


def test_the_existing_measure_is_unchanged_for_a_fixture_with_no_gating_interval(lt):
    items = [_gated(n, float(n), {"Closed": float(n)}, 0.0) for n in range(1, 11)]

    result = lt.lead_time(items)

    assert result["summary"]["average"]["hours"] == 5.5
    assert result["summary"]["median"]["hours"] == 5, "the Kaplan-Meier median over 1h..10h falls to one half at 5h"
    assert result["summary"]["minimum"]["hours"] == 1.0
    assert result["summary"]["maximum"]["hours"] == 10.0
    assert result["summary"]["p85"]["hours"] == 9.0
    assert result["summary"]["gate"]["hours"] == 0.0


def test_the_control_chart_dot_carries_its_stack_and_the_chart_and_gate_time_agree(lt):
    row = {"n": 1, "t": "card 1", "o": _iso(NOW - timedelta(hours=10)), "c": _iso(NOW), "column": "Closed",
           "hours": 10.0, "flowing": 8.0, "held": 2.0, "approx": False, "unknown_first": False,
           "columns": {"Doing": 6.0, "Gating": 3.0, "Closed": 1.0}, "gate": 3.0}

    chart = _chart(lt, [row])
    dot = chart["dots"][0]

    assert sum(band["hours"] for band in dot["stack"]) == pytest.approx(dot["hours"])
    assert dot["gate_hours"] == 3.0 and dot["gate_text"] == "3h"
    assert chart["gate"]["hours"] == 3.0
    assert chart["gate"]["text"] == "3h"
    assert chart["gate"]["label"] == "Gate time"


def test_the_control_chart_keeps_a_never_gated_card_out_of_gate_time(lt):
    never = {"n": 2, "t": "card 2", "o": _iso(NOW - timedelta(hours=4)), "c": _iso(NOW), "column": "Closed",
             "hours": 4.0, "flowing": 4.0, "held": 0.0, "approx": False, "unknown_first": False,
             "columns": {"Doing": 4.0}, "gate": 0.0}

    chart = _chart(lt, [never])

    assert chart["gate"]["hours"] == 0.0
    assert [band["column"] for band in chart["dots"][0]["stack"]] == ["Doing"]


def test_a_row_with_empty_columns_stacks_its_whole_measure_in_its_own_column(lt):
    row = _gated(1, 5.0, {}, 0.0)

    stack = lt.lead_time([row])["series"][0]["stack"]

    assert [(band["column"], band["hours"]) for band in stack] == [("Done", 5.0)], \
        "empty columns falls back to the row's own column like no columns at all"


def test_gate_time_in_the_summary_covers_every_delivered_card_and_the_chart_only_its_plotted(lt):
    older = {"n": 1, "t": "card 1", "o": _iso(NOW - timedelta(days=20)), "c": _iso(NOW - timedelta(days=19)),
             "column": "Closed", "hours": 8.0, "flowing": 8.0, "held": 0.0, "approx": False, "unknown_first": False,
             "columns": {"Doing": 8.0}, "gate": 0.0}
    recent = {"n": 2, "t": "card 2", "o": _iso(NOW - timedelta(hours=10)), "c": _iso(NOW), "column": "Closed",
              "hours": 4.0, "flowing": 4.0, "held": 0.0, "approx": False, "unknown_first": False,
              "columns": {"Doing": 4.0}, "gate": 0.0}
    gated = {"n": 3, "t": "card 3", "o": _iso(NOW - timedelta(hours=12)), "c": _iso(NOW), "column": "Closed",
             "hours": 6.0, "flowing": 6.0, "held": 0.0, "approx": False, "unknown_first": False,
             "columns": {"Doing": 3.0, "Gating": 3.0}, "gate": 3.0}

    summary = lt.lead_time([older, recent, gated])["summary"]["gate"]["hours"]
    chart = _chart(lt, [older, recent, gated], timeframe=7)["gate"]["hours"]

    assert summary == 3.0, "the summary's gate time covers every delivered card, like its average"
    assert chart == 3.0, "the chart's gate time covers the plotted cards only, like its average"
    assert chart <= summary


# SPEC 1327 #1334: an explicit band polygon and an expanded summary.
def test_the_band_is_a_polygon_along_each_dots_rolling_high_and_low(lt):
    chart = _chart(lt, [_closed(n, h) for n, h in enumerate([2, 4, 6, 8, 10], start=1)])

    band, dots = chart["band"], chart["dots"]
    assert [p["x"] for p in band["high"]] == [d["x"] for d in dots]
    assert [p["y"] for p in band["high"]] == [d["rolling"]["y_high"] for d in dots]
    assert [p["x"] for p in band["low"]] == [d["x"] for d in dots]
    assert [p["y"] for p in band["low"]] == [d["rolling"]["y_low"] for d in dots]
    assert band["polygon"] == band["high"] + list(reversed(band["low"])), \
        "the polygon is the high edge then the low edge back again"


def test_the_band_never_falls_below_zero_and_spans_only_the_observed_range(lt):
    items = [_closed(n, h, days_ago=30 - n) for n, h in enumerate([1, 2, 3, 4, 5, 6, 100], start=1)]
    chart = _chart(lt, items, timeframe=30)

    band, dots = chart["band"], chart["dots"]
    assert all(p["hours"] >= 0 for p in band["low"]), "a band edge falls below zero hours"
    assert all(p["y"] >= 0 for p in band["low"] + band["high"]), "a band point falls under the plot floor"
    assert band["low"][0]["x"] == dots[0]["x"] and band["low"][-1]["x"] == dots[-1]["x"]
    assert 0 < band["low"][0]["x"] and band["low"][-1]["x"] < 1, \
        "the band is bounded by the first and last observation, never by the empty window"


def test_the_summary_carries_the_minimum_maximum_plotted_and_excluded_counts(lt):
    items = [_closed(1, 2), _closed(2, 4), _closed(3, 6), _closed(4, 8), _closed(5, 10),
             {**_closed(9, 3), "reason": "not_planned"}]

    summary = _chart(lt, items)["summary"]

    assert summary["minimum"]["hours"] == 2.0 and summary["minimum"]["text"] == "2h"
    assert summary["minimum"]["label"] == "Minimum"
    assert summary["maximum"]["hours"] == 10.0 and summary["maximum"]["text"] == "10h"
    assert summary["plotted"] == 5 and summary["plotted_text"] == "5 issues plotted"
    assert summary["excluded"] == 1, "the summary counts the cards the chart leaves out"


def test_the_fit_range_gives_each_dot_a_second_x_from_the_same_payload(lt):
    items = [_at(1, NOW - timedelta(hours=5)), _at(2, NOW - timedelta(hours=4)), _at(3, NOW - timedelta(hours=3))]
    chart = _chart(lt, items, timeframe=30)

    assert chart["fit"] is not None
    assert all(0.0 <= dot["xf"] <= 1.0 for dot in chart["dots"])
    assert [dot["xf"] for dot in chart["dots"]] != [dot["x"] for dot in chart["dots"]]
    assert [p["x"] for p in chart["band"]["fit"]["high"]] == [dot["xf"] for dot in chart["dots"]]
    assert len(chart["clusters"]) == 1, "the cards share one ring in the whole window"
    assert len(chart["clusters_fit"]) == 3, "the fit view separates the dots it spreads, so it re-clusters them"


def test_a_flat_view_serves_no_fit_range_and_no_fit_position(lt):
    chart = _chart(lt, [_closed(1, 5, days_ago=1), _closed(2, 5, days_ago=10), _closed(3, 5, days_ago=20)],
                   timeframe=30)

    assert chart["fit"] is None
    assert all("xf" not in dot for dot in chart["dots"])
    assert "clusters_fit" not in chart and "fit" not in chart["band"]


def test_the_range_carries_its_printed_text(lt):
    result = _chart(lt, [_closed(1, 5, days_ago=1)], timeframe=30)
    zoomed = _chart(lt, [_closed(1, 5, days_ago=20)], start=NOW - timedelta(days=3), end=NOW - timedelta(days=2))

    assert result["range"]["text"] == "Oct 31 to Nov 30 UTC"
    assert zoomed["range"]["text"] == "Nov 27 00:00 to Nov 28 00:00 UTC"


def test_the_range_text_names_both_years_when_the_view_crosses_one(lt):
    chart = _chart(lt, [_closed(1, 5)], start=datetime(2026, 12, 30, tzinfo=UTC),
                   end=datetime(2027, 1, 3, tzinfo=UTC))

    assert chart["range"]["text"] == "Dec 30, 2026 to Jan 3, 2027 UTC"


def test_a_twelve_hour_timeframe_names_whole_hours(lt):
    """#2029: the 12-hour preset is half a day, and the chart text reads `last 12 hours`, never `12.0`."""
    chart = lt.control_chart([], timeframe=0.5, now=BASE)
    assert "last 12 hours" in json.dumps(chart), json.dumps(chart)[:300]
