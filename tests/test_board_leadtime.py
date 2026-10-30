"""The lead-time chart arithmetic turns closed board cards into dots, a rolling average and a p85 summary (SPEC 111)."""
import ast
import importlib.util
import json
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


def _card(n, hours, column="Done"):
    return {"n": n, "o": _iso(BASE), "c": _iso(BASE + timedelta(hours=hours)), "column": column}


def _open(n):
    return {"n": n, "o": _iso(BASE), "c": None, "column": "Doing"}


def _no_opened(n, hours):
    return {"n": n, "o": None, "c": _iso(BASE + timedelta(hours=hours)), "column": "Done"}


def test_a_dot_is_closed_minus_opened_in_hours(lt):
    row = {"n": 7, "o": "2026-10-01T00:00:00+00:00", "c": "2026-10-02T04:00:00+00:00", "column": "Done"}

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
    # #921: GitHub's NOT_PLANNED verdict (abandoned or duplicate) is no delivery, so it never enters the series.
    items = [
        _card(1, 5),
        {**_card(2, 9), "reason": "not_planned"},
        {**_card(3, 7), "reason": "duplicate"},
    ]

    result = lt.lead_time(items)

    assert [dot["n"] for dot in result["series"]] == [1]
    assert result["excluded"] == {"not_planned": 2}
    assert result["summary"]["median"]["hours"] == 5.0


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

    assert result["measure"] == "opened -> closed"
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
