"""Guards for the totals bar under the graph (owner request after #86).

Rule: unknown stays unknown. A figure the record lacks reads "unknown" in the bar, never zero.
The cells come from the core, and the replay cursor cuts them like every other figure.
"""
import json

import pytest

from tests.dashboard_helpers import PAGE_SRC, _run22, _run22_tokens, run_node
from tests.helpers import DESIGN


def _totals(events, **kw):
    return run_node([{"op": "totals", "events": events, **kw}])[0]


def _cells(events, **kw):
    return {c["id"]: c for c in run_node([{"op": "cells", "events": events, **kw}])[0]}


def test_the_totals_bar_sums_cost_time_and_tokens_of_a_finished_run_and_names_the_rate_as_an_estimate():
    _, events = _run22_tokens()
    t = _totals(events, cap=10)
    assert t["spend"] == pytest.approx(0.0414606 + 0.19228799999999996) and t["cap_pct"] == pytest.approx(t["spend"] / 10)
    assert t["wall"] == pytest.approx(events[-1]["ts"] - events[0]["ts"])
    assert (t["tokens_in"], t["tokens_out"]) == (31200, 6400) and t["tps"] == pytest.approx(6400 / (7.667778458009707 + 25.13335620801081))
    c = _cells(events, cap=10)
    assert c["cost"]["text"].startswith("$0.234 of $10") and c["in"]["text"] == "31k" and c["out"]["text"] == "6.4k" and c["tps"]["text"].startswith("~195")
    assert "estimate" in c["tps"]["label"].lower() and "per call-second" in c["tps"]["title"] and "overlap" in c["tps"]["title"]


def test_a_record_with_no_tokens_shows_unknown_in_the_bar_never_zero():
    _, events = _run22()
    t = _totals(events, cap=10)
    assert t["tokens_in"] is None and t["tokens_out"] is None and t["tps"] is None and t["partial_tokens"] is False
    c = _cells(events, cap=10)
    assert (c["in"]["text"], c["out"]["text"], c["tps"]["text"]) == ("unknown", "unknown", "unknown")
    assert c["cost"]["text"].startswith("$0.234") and c["time"]["text"] != "unknown"


def test_a_record_with_no_spend_and_no_cap_shows_unknown_cost_and_no_cap_text():
    rev = run_node([{"op": "record", "text": (DESIGN / "router.record.jsonl").read_text()}])[0]["events"]
    c = _cells(rev)
    assert c["cost"]["text"] == "unknown" and c["time"]["text"] == "unknown"
    assert "of $" not in _cells(_run22()[1])["cost"]["text"]


def test_partial_tokens_are_marked_as_a_floor_when_an_agent_call_has_a_spend_and_no_tokens():
    _, events = _run22_tokens()
    ev = json.loads(json.dumps(events))
    next(e for e in ev if e["event"] == "outcome" and e["node"] == "actor").pop("tokens_in")
    t = _totals(ev, cap=10)
    assert t["partial_tokens"] is True and t["tokens_in"] == 1200
    c = _cells(ev, cap=10)
    assert c["in"]["text"] == "≥ 1.2k" and "floor" in c["in"]["title"]
    assert _totals(events, cap=10)["partial_tokens"] is False   # a node with no spend (a gate) never makes the sum partial


def test_a_live_run_counts_its_time_on_from_run_start_and_a_run_with_a_verdict_stops():
    _, events = _run22_tokens()
    live = [e for e in events if e["event"] != "verdict"]
    start = live[0]["ts"]
    assert _totals(live, now=start + 500)["wall"] == pytest.approx(500)
    assert _totals(live)["wall"] == pytest.approx(live[-1]["ts"] - start)       # no clock: the span to the last event
    assert _totals(events, now=start + 500)["wall"] == pytest.approx(events[-1]["ts"] - start)   # a verdict ends the count


def test_the_bar_at_a_replay_cursor_shows_the_totals_up_to_that_position_only():
    _, events = _run22_tokens()
    cut = run_node([{"op": "replay", "events": events, "cursor": 3}])[0]["events"]   # spec has finished at pos 3, actor has not
    t = _totals(cut, cap=10)
    assert t["spend"] == pytest.approx(0.0414606) and (t["tokens_in"], t["tokens_out"]) == (1200, 400)
    assert t["tps"] == pytest.approx(400 / 7.667778458009707)
    assert t["wall"] == pytest.approx(cut[-1]["ts"] - cut[0]["ts"])
    assert _totals(events, cap=10)["spend"] > t["spend"]


def test_the_totals_in_the_statistics_are_the_bar_totals_so_the_two_cannot_drift():
    doc, events = _run22_tokens()
    s = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True}])[0]["totals"]
    assert s == _totals(events, cap=10)


def test_the_page_draws_the_bar_under_the_graph_from_the_core_cells_and_cuts_it_at_the_cursor():
    src = PAGE_SRC()
    assert 'id="totals"' in src and "core.totalsCells(core.runTotals(" in src
    body = src[src.index("function renderTotals"):src.index("setInterval(() => { if (S.replay")]
    assert "r.events" in body                                       # the replay cut, not the whole record
    assert src.index('id="mapbox"') < src.index('id="totals"')      # the bar is a sibling under the map
