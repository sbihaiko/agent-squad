"""The children one run can fund (#480): the dispatchers ask for no more than this, so the engine never rejects a spawn
for spend_over_remainder and leaves its issue in Doing with nobody on it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fundroom", ROOT / "contrib/squad-goal/scripts/fundroom.py")
assert spec and spec.loader
fundroom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fundroom)


def graph(cap, router, child):
    return {"run": {"spend_cap": cap}, "nodes": [
        {"name": "route", "kind": "dispatch", "budget": {"spend": {"max": child}}},
        {"name": "router", "kind": "agent", "budget": {"spend": {"max": router}}},
        {"name": "final_gate", "kind": "gate"}]}


def test_the_run_cap_less_the_agents_divided_by_one_child():
    """The measured run: a cap of 40, a router that may hold 8, and children of 12 fund two children, not four."""
    assert fundroom.room(graph(40, 8, 12)) == 2


def test_a_run_that_cannot_fund_one_child_funds_none():
    assert fundroom.room(graph(10, 8, 12)) == 0


def test_a_graph_with_no_spend_cap_names_no_bound():
    assert fundroom.room({"nodes": graph(40, 8, 12)["nodes"]}) is None
