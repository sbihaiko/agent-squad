"""The burndown counts one spec's children per day, from the parent relation and the whole reads, host-free (#1047)."""
import ast
import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
BURNDOWN_PY = SCRIPTS / "board_burndown.py"
SPEC = 1045


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("board_burndown_under_test", BURNDOWN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def burndown(mod):
    return mod.burndown


def _iso(day, hour=12, minute=0):
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00Z"


def _read(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=UTC).timestamp()


def _join(day, spec=SPEC, hour=12):
    return {"t": _iso(day, hour), "type": "spec", "spec": spec}


def _leave(day):
    return {"t": _iso(day), "type": "spec", "spec": None}


def _close(day, hour=12):
    return {"t": _iso(day, hour), "type": "closed"}


def _issue(*events, title="child"):
    return {"title": title, "events": list(events), "complete": True}


def test_the_child_set_is_the_parent_relation_and_never_a_title_prefix(burndown):
    store = {
        1: _issue(_join(20), title=f"[SPEC {SPEC}] a child"),
        2: _issue(title=f"[SPEC {SPEC}] a title that names the spec, with no parent relation"),
    }
    result = burndown(store, [_read(20), _read(21)], SPEC)
    assert [p["remaining"] for p in result["points"]] == [1, 1]
    assert [p["added"] for p in result["points"]] == [1, 0]


def test_a_closed_ticket_lowers_remaining_on_its_day_and_every_later_day(burndown):
    store = {1: _issue(_join(20), _close(21)), 2: _issue(_join(20))}
    result = burndown(store, [_read(20), _read(21), _read(22)], SPEC)
    assert [p["remaining"] for p in result["points"]] == [2, 1, 1]
    assert [p["closed"] for p in result["points"]] == [0, 1, 0]


def test_a_ticket_added_after_the_start_raises_remaining_from_its_join_day(burndown):
    store = {1: _issue(_join(20)), 2: _issue(_join(22))}
    result = burndown(store, [_read(20), _read(21), _read(22), _read(23)], SPEC)
    assert [p["remaining"] for p in result["points"]] == [1, 1, 2, 2]
    assert [p["added"] for p in result["points"]] == [1, 0, 1, 0]


def test_a_ticket_that_leaves_the_spec_stops_counting_on_the_day_it_left(burndown):
    store = {1: _issue(_join(20), _leave(22))}
    result = burndown(store, [_read(20), _read(21), _read(22), _read(23)], SPEC)
    assert [p["remaining"] for p in result["points"]] == [1, 1, 0, 0]
    assert [p["closed"] for p in result["points"]] == [0, 0, 0, 0]


def test_a_day_without_a_whole_read_is_a_gap_and_a_quiet_read_day_is_a_point(burndown):
    store = {1: _issue(_join(20))}
    result = burndown(store, [_read(20), _read(22)], SPEC)
    assert [p["day"] for p in result["points"]] == ["2026-10-20", "2026-10-22"]
    assert [p["remaining"] for p in result["points"]] == [1, 1]
    assert result["gaps"] == ["2026-10-21"], "a day with no whole read is a gap, never a zero"


def test_a_join_on_a_gap_day_is_counted_on_the_next_point_and_never_dropped(burndown):
    store = {1: _issue(_join(20)), 2: _issue(_join(21))}
    result = burndown(store, [_read(20), _read(22)], SPEC)
    assert [p["remaining"] for p in result["points"]] == [1, 2]
    assert [p["added"] for p in result["points"]] == [1, 1]
    assert result["gaps"] == ["2026-10-21"]


def test_a_spec_with_every_child_closed_ends_at_zero(burndown):
    store = {1: _issue(_join(20), _close(21)), 2: _issue(_join(20), _close(22))}
    result = burndown(store, [_read(20), _read(21), _read(22)], SPEC)
    assert result["points"][-1]["remaining"] == 0
    assert result["reason"] is None


def test_a_spec_with_no_child_answers_an_empty_series_with_a_reason(burndown):
    store = {1: _issue(_join(20, spec=999))}
    result = burndown(store, [_read(20)], SPEC)
    assert result == {"spec": SPEC, "start": None, "points": [], "gaps": [], "reason": "no child ticket"}


def test_no_whole_read_after_the_first_child_answers_an_empty_series_with_a_reason(burndown):
    store = {1: _issue(_join(20))}
    result = burndown(store, [_read(10)], SPEC)
    assert result["points"] == []
    assert result["reason"] == "no whole board read after the first ticket"


def test_the_zone_moves_the_day_boundary(burndown):
    # 02:30 UTC on 2026-10-21 is 23:30 on 2026-10-20 in Sao Paulo (UTC-3).
    store = {1: _issue({"t": "2026-10-21T02:30:00Z", "type": "spec", "spec": SPEC})}
    result = burndown(store, [_read(20), _read(21)], SPEC, zone="America/Sao_Paulo")
    assert [p["day"] for p in result["points"]] == ["2026-10-20", "2026-10-21"]
    assert [p["added"] for p in result["points"]] == [1, 0]


def test_the_week_counts_only_the_days_of_its_window_even_after_a_gap(mod):
    store = {1: _issue(_join(20), _close(21)), 2: _issue(_join(20))}
    series = mod.burndown(store, [_read(20), _read(30)], SPEC)
    summary = mod.summary(series, date(2026, 10, 30))
    assert summary["remaining"] == 1
    assert summary["closed_week"] == 0, "a ticket closed before the window is not this week's close"
    assert summary["guide"] is None and summary["guide_reason"]


def test_module_imports_no_hub_github_client_or_file_api():
    tree = ast.parse(BURNDOWN_PY.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    forbidden = {"hub", "hub_client", "github", "gh", "subprocess", "requests", "urllib", "http", "os", "io", "shutil"}
    assert not names & forbidden, sorted(names & forbidden)
