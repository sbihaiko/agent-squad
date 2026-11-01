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
    assert result["excluded"] == {"abandoned": 1, "open": 2, "no_opened": 1}


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
    assert summary["median"]["hours"] == 5.5
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

    assert json.loads(json.dumps(lt.lead_time(items)))["excluded"] == {"open": 1}


def test_no_dots_gives_an_empty_series_and_no_summary_numbers(lt):
    items = [_open(1), _card(2, 6, column="Abandoned")]

    result = lt.lead_time(items)

    assert result["series"] == []
    assert result["summary"] is None
    assert result["excluded"] == {"abandoned": 1, "open": 1}


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
    assert means[0] == 2.0, "the first dot averages the dots it has: 1, 2, 3"
    assert means[4] == 5.0, "the middle dot averages 3 to 7"
    assert means[8] == 8.0, "the last dot averages 7, 8, 9"


def test_the_linear_band_is_one_sigma_and_its_lower_edge_stays_at_zero(lt):
    result = _chart(lt, [_closed(n, h) for n, h in enumerate([2, 4, 6, 8, 10], start=1)])

    centre = result["dots"][2]["rolling"]
    assert centre["hours"] == 6.0
    assert centre["low"] == pytest.approx(6 - 8 ** 0.5, abs=1e-9)
    assert centre["high"] == pytest.approx(6 + 8 ** 0.5, abs=1e-9)

    low_edge = _chart(lt, [_closed(n, 1) for n in range(1, 7)] + [_closed(7, 100)])["dots"][5]["rolling"]
    assert low_edge["low"] == 0.0, "a band never falls below zero hours"


def test_the_log_band_is_one_sigma_in_log_space_around_the_geometric_mean(lt):
    result = _chart(lt, [_closed(n, h) for n, h in enumerate([2, 4, 6, 8, 10], start=1)], log=True)

    centre = result["dots"][2]["rolling"]
    logs = [math.log(h) for h in [2, 4, 6, 8, 10]]
    mean = sum(logs) / 5
    sigma = (sum((v - mean) ** 2 for v in logs) / 5) ** 0.5
    assert centre["hours"] == pytest.approx(math.exp(mean), rel=1e-9)
    assert centre["low"] == pytest.approx(math.exp(mean - sigma), rel=1e-9)
    assert centre["high"] == pytest.approx(math.exp(mean + sigma), rel=1e-9)


def test_the_scale_is_linear_under_thirty_days_and_cube_root_from_thirty(lt):
    under = _chart(lt, [_closed(1, 29 * 24), _closed(2, 5)])
    at = _chart(lt, [_closed(1, 30 * 24), _closed(2, 5)])

    assert under["axis"] == "linear"
    assert at["axis"] == "cube"


def test_the_log_scale_ticks_name_an_hour_a_day_and_a_week(lt):
    result = _chart(lt, [_closed(1, 3), _closed(2, 30 * 24)], log=True)

    assert result["axis"] == "log"
    assert [tick["text"] for tick in result["ticks"]] == ["1h", "1d", "7d"]


def test_a_timeframe_keeps_only_the_cards_closed_inside_it(lt):
    items = [_closed(1, 5, days_ago=1), _closed(2, 5, days_ago=20)]

    assert [dot["n"] for dot in _chart(lt, items, timeframe=7)["dots"]] == [1]
    assert sorted(dot["n"] for dot in _chart(lt, items, timeframe=None)["dots"]) == [1, 2]


def test_the_date_axis_places_the_range_end_at_the_right_edge(lt):
    result = _chart(lt, [_closed(1, 5, days_ago=0), _closed(2, 5, days_ago=5)], timeframe=10)

    xs = {dot["n"]: dot["x"] for dot in result["dots"]}
    assert xs[1] == 1.0, "a card closed now sits on the right edge"
    assert xs[2] == pytest.approx(0.5), "a card closed 5 of 10 days ago sits in the middle"


def test_dots_closer_than_one_dot_merge_into_a_numbered_cluster(lt):
    items = [_closed(1, 5, days_ago=2), _closed(2, 5, days_ago=2), _closed(3, 90, days_ago=2)]

    clusters = _chart(lt, items)["clusters"]

    assert sorted(len(c["dots"]) for c in clusters) == [1, 2]
    merged = next(c for c in clusters if len(c["dots"]) == 2)
    assert [d["n"] for d in merged["dots"]] == [1, 2]


def test_the_window_text_names_the_days_and_the_excluded_reasons(lt):
    items = [_closed(1, 5, days_ago=1), _open(2), _no_opened(3, 4)]

    result = _chart(lt, items, timeframe=30)

    assert result["text"] == "last 30 days, 2 excluded because still open, no opened date"


def test_the_average_median_and_p85_carry_values_and_positions(lt):
    items = [_closed(n, h) for n, h in enumerate(range(1, 11), start=1)]

    result = _chart(lt, items)

    assert result["average"]["hours"] == 5.5
    assert result["median"]["hours"] == 5.5
    assert result["p85"]["hours"] == 9.0
    assert 0.0 <= result["p85"]["y"] <= 1.0
    assert result["p85"]["text"] == "9h"


def test_no_closed_card_gives_no_dots_and_no_lines(lt):
    result = _chart(lt, [_open(1)])

    assert result["dots"] == []
    assert result["average"] is None
    assert result["text"] == "all closed cards, 1 excluded because still open"


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

    assert result["range"] == {"start": _iso(start), "end": _iso(end)}


def test_a_zoom_range_names_its_dates_in_the_window_text(lt):
    start, end = datetime(2026, 9, 30, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC)
    result = _chart(lt, [_closed(1, 5, days_ago=20)], start=start, end=end)

    assert result["text"] == "Sep 30 to Oct 8"


def test_a_zoom_range_with_no_card_keeps_its_range_and_no_dots(lt):
    start, end = NOW - timedelta(days=3), NOW - timedelta(days=2)
    result = _chart(lt, [_closed(1, 5, days_ago=20)], start=start, end=end)

    assert result["dots"] == []
    assert result["range"] == {"start": _iso(start), "end": _iso(end)}


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
