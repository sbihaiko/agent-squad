"""The spec forecast (#1026): initial parameters, a seeded simulation, the frozen baseline and the lane badge."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
AS_OF = date(2026, 11, 1)


def _load():
    spec = importlib.util.spec_from_file_location("spec_forecast", SCRIPTS / "spec_forecast.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sf = _load()


def history(done_per_day, coverage=1.0, reserved_per_day=0, start=AS_OF - timedelta(days=60)):
    """One day per entry, all active; done_per_day is the completions of each day, in order."""
    days = [{"day": (start + timedelta(days=i)).isoformat(), "active": True, "done": d, "reserved": reserved_per_day}
            for i, d in enumerate(done_per_day)]
    return {"days": days, "coverage": coverage}


def test_initial_parameters_are_the_spec_values_and_versioned():
    p = sf.PARAMS
    assert p["version"] == 1
    assert p["window_days"] == 90
    assert p["min_completions"] == 10
    assert p["min_coverage"] == 0.7
    assert p["reserve_window_days"] == 30
    assert p["iterations"] == 10000
    assert p["seed"] == 1018
    assert p["quantiles"] == (50, 85)


def test_a_fixed_seed_gives_the_same_forecast_every_call():
    hist = history([1, 2, 0, 1, 3, 1, 2, 0, 2, 1, 1, 2])
    specs = [{"spec": 1018, "scope": 6}]
    params = dict(sf.PARAMS, iterations=500)
    assert sf.forecast(hist, specs, AS_OF, params) == sf.forecast(hist, specs, AS_OF, params)


def test_too_few_completions_show_insufficient_history():
    hist = history([1, 1, 1, 1, 1])
    out = sf.forecast(hist, [{"spec": 1018, "scope": 4}], AS_OF)
    assert out[1018]["status"] == "insufficient history"
    assert out[1018]["p50"] is None and out[1018]["p85"] is None


def test_low_coverage_shows_insufficient_history_even_with_enough_completions():
    hist = history([2, 2, 2, 2, 2, 2], coverage=0.69)
    out = sf.forecast(hist, [{"spec": 1018, "scope": 4}], AS_OF)
    assert out[1018]["status"] == "insufficient history"


def steady(done, reserved=0, days=60):
    return history([done] * days, reserved_per_day=reserved)


def test_a_steady_throughput_finishes_the_scope_on_the_worked_day():
    out = sf.forecast(steady(2), [{"spec": 1018, "scope": 10}], AS_OF, dict(sf.PARAMS, iterations=50))
    assert out[1018]["status"] == "ok"
    assert out[1018]["p50"] == (AS_OF + timedelta(days=5)).isoformat()
    assert out[1018]["p85"] == (AS_OF + timedelta(days=5)).isoformat()


def test_the_reserved_share_of_bugs_and_unscoped_work_is_taken_off_the_capacity():
    out = sf.forecast(steady(2, reserved=1), [{"spec": 1018, "scope": 10}], AS_OF, dict(sf.PARAMS, iterations=50))
    assert out[1018]["capacity"]["reserved"] == 0.5
    assert out[1018]["p50"] == (AS_OF + timedelta(days=10)).isoformat()


def test_two_specs_share_one_capacity_and_neither_finishes_at_the_solo_day():
    specs = [{"spec": 1018, "scope": 10}, {"spec": 1019, "scope": 10}]
    out = sf.forecast(steady(2), specs, AS_OF, dict(sf.PARAMS, iterations=50))
    assert out[1018]["p50"] == out[1019]["p50"] == (AS_OF + timedelta(days=10)).isoformat()


def test_a_finished_spec_hands_its_share_to_the_one_still_open():
    specs = [{"spec": 1018, "scope": 2}, {"spec": 1019, "scope": 10}]
    out = sf.forecast(steady(2), specs, AS_OF, dict(sf.PARAMS, iterations=50))
    assert out[1018]["p50"] == (AS_OF + timedelta(days=2)).isoformat()
    assert out[1019]["p50"] == (AS_OF + timedelta(days=6)).isoformat()


def test_capacity_that_is_all_reserved_shows_no_finite_forecast():
    out = sf.forecast(steady(2, reserved=2), [{"spec": 1018, "scope": 10}], AS_OF, dict(sf.PARAMS, iterations=50))
    assert out[1018]["status"] == "no finite forecast"
    assert out[1018]["p50"] is None and out[1018]["p85"] is None


def test_the_baseline_freezes_at_the_first_dispatch_and_ignores_later_days():
    early = steady(2)
    later = history([2] * 60 + [50] * 30, reserved_per_day=0)
    spec = {"spec": 1018, "scope": 10}
    dispatch = AS_OF - timedelta(days=30)
    a = sf.frozen_baseline(None, early, [spec], 1018, dispatch, dict(sf.PARAMS, iterations=50))
    b = sf.frozen_baseline(None, later, [spec], 1018, dispatch, dict(sf.PARAMS, iterations=50))
    assert a == b
    assert a["as_of"] == dispatch.isoformat()
    assert a["p50"] == (dispatch + timedelta(days=5)).isoformat()


def test_a_stored_baseline_never_changes():
    stored = {"spec": 1018, "as_of": "2026-10-01", "p50": "2026-10-20", "p85": "2026-10-28", "status": "ok", "version": 1}
    got = sf.frozen_baseline(stored, steady(7), [{"spec": 1018, "scope": 1}], 1018, AS_OF, dict(sf.PARAMS, iterations=50))
    assert got is stored


def test_the_lane_badge_counts_calendar_days_later_as_plus_and_earlier_as_minus():
    assert sf.lane_badge("2026-11-10", "2026-11-07") == "+3d"
    assert sf.lane_badge("2026-11-05", "2026-11-07") == "-2d"
    assert sf.lane_badge("2026-11-07", "2026-11-07") == "0d"


def test_the_lane_badge_is_absent_without_both_dates():
    assert sf.lane_badge(None, "2026-11-07") is None
    assert sf.lane_badge("2026-11-10", None) is None
