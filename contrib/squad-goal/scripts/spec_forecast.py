"""The spec forecast (#1026): one seeded simulation of the loop's capacity, a frozen baseline and a lane badge.

Stdlib only (ADR-0137): the caller passes the day records, the specs and the as-of date; this module reads no board, no
clock and no network, so the same inputs always give the same forecast.
"""
import math
import random
from datetime import date, timedelta

PARAMS = {
    "version": 1,
    "window_days": 90,
    "min_completions": 10,
    "min_coverage": 0.7,
    "reserve_window_days": 30,
    "iterations": 10000,
    "seed": 1018,
    "horizon_days": 730,
    "quantiles": (50, 85),
}
INSUFFICIENT = "insufficient history"
NO_FINITE = "no finite forecast"
_EPS = 1e-9


def _window(history, as_of, days):
    start = as_of - timedelta(days=days)
    return [d for d in history["days"] if start <= date.fromisoformat(d["day"]) < as_of]


def _simulate(pool, share, scopes, params):
    rng = random.Random(params["seed"])
    finishes = {spec: [] for spec, _ in scopes}
    idle = sum(pool) * (1 - share) <= _EPS
    for _ in range(params["iterations"]):
        left = [[spec, tickets] for spec, tickets in scopes if tickets > _EPS]
        done_on = {spec: 0 for spec, tickets in scopes if tickets <= _EPS}
        for k in range(1, params["horizon_days"] + 1):
            if not left or idle:
                break
            capacity = rng.choice(pool) * (1 - share)
            while capacity > _EPS and left:
                each = capacity / len(left)
                capacity = 0.0
                for entry in list(left):
                    take = min(each, entry[1])
                    entry[1] -= take
                    capacity += each - take
                    if entry[1] <= _EPS:
                        left.remove(entry)
                        done_on[entry[0]] = k
        for spec, _ in scopes:
            finishes[spec].append(done_on.get(spec, math.inf))
    return finishes


def _nearest_rank(values, percent):
    ordered = sorted(values)
    return ordered[math.ceil(percent / 100 * len(ordered)) - 1]


def forecast(history, specs, as_of, params=PARAMS):
    """Each spec's finish: the p50 and p85 days from the shared simulation, or the status that says why there is none."""
    days = _window(history, as_of, params["window_days"])
    recent = _window(history, as_of, params["reserve_window_days"])
    pool = [d["done"] for d in days if d["active"]]
    completions = sum(d["done"] for d in days)
    recent_done = sum(d["done"] for d in recent)
    share = sum(d["reserved"] for d in recent) / recent_done if recent_done else 0.0
    scopes = [(s["spec"], s["scope"]) for s in specs]
    base = {"version": params["version"], "samples": len(pool), "completions": completions,
            "coverage": history["coverage"], "capacity": {"per_day": 0.0, "reserved": share}}
    if (len(pool) == 0 or completions < params["min_completions"] or history["coverage"] < params["min_coverage"]):
        return {spec: {**base, "spec": spec, "scope": tickets, "status": INSUFFICIENT, "p50": None, "p85": None}
                for spec, tickets in scopes}
    base["capacity"]["per_day"] = sum(pool) / len(pool) * (1 - share)
    finishes = _simulate(pool, share, scopes, params)
    out = {}
    for spec, tickets in scopes:
        dates = {}
        for q in params["quantiles"]:
            k = _nearest_rank(finishes[spec], q)
            dates[q] = None if math.isinf(k) else (as_of + timedelta(days=k)).isoformat()
        status = "ok" if all(v is not None for v in dates.values()) else NO_FINITE
        out[spec] = {**base, "spec": spec, "scope": tickets, "status": status,
                     "p50": dates[params["quantiles"][0]], "p85": dates[params["quantiles"][1]]}
    return out


def frozen_baseline(stored, history, specs, spec, dispatch_day, params=PARAMS):
    """The baseline taken at the first implementation dispatch; a stored one is returned as it was, never recomputed."""
    if stored is not None:
        return stored
    res = forecast(history, specs, dispatch_day, params)[spec]
    return {"spec": spec, "as_of": dispatch_day.isoformat(), "status": res["status"],
            "p50": res["p50"], "p85": res["p85"], "version": params["version"]}


def lane_badge(rolling_p50, baseline_p50):
    """Calendar days the rolling p50 lies after (+) or before (-) the baseline p50; None while either is missing."""
    if rolling_p50 is None or baseline_p50 is None:
        return None
    delta = (date.fromisoformat(rolling_p50) - date.fromisoformat(baseline_p50)).days
    return f"{delta:+d}d" if delta else "0d"
