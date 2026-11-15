"""Runs > Usage on the phone (SPEC 1596, #1604): the token bars per hour, day or month and the model pie with its legend,
drawn from the desktop's /api/stats payload. The rules are `hub-mobile-usage.js`, run under node alone with no DOM.
Every expected value below is a literal worked from the fixture, never recomputed by the module under test.
"""
import json
import pathlib
import shutil

import pytest

from tests.test_hub_mobile_move import run_js

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "hub-mobile-usage.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

BUCKETS = [
    {"t": "2026-11-08 09:00", "spend": 1.0, "tokens": 1000000, "runs": 1, "median_dur": None},
    {"t": "2026-11-08 10:00", "spend": 2.0, "tokens": 2000000, "runs": 2, "median_dur": None},
    {"t": "2026-11-08 11:00", "spend": 4.0, "tokens": 4000000, "runs": 3, "median_dur": None},
]
MODELS = [
    {"model": "claude-sonnet", "month_tokens": 600, "spend": 9.0},
    {"model": "deepseek-flash", "month_tokens": 300, "spend": 1.0},
    {"model": "claude-opus", "month_tokens": 100, "spend": 5.0},
]
SEVEN = MODELS + [
    {"model": "gpt-codex", "month_tokens": 50, "spend": 0.5},
    {"model": "claude-haiku", "month_tokens": 30, "spend": 0.2},
    {"model": "agy-gemini", "month_tokens": 20, "spend": 0.1},
]


def js(expr):
    return run_js(USAGE_JS.read_text(), expr)


def test_bars_are_a_share_of_the_peak_bucket_for_the_hour():
    got = js(f"usageBars({json.dumps(BUCKETS)}, 'hour')")
    assert [b["h"] for b in got] == [25.0, 50.0, 100.0]
    assert [b["label"] for b in got] == ["09:00", "10:00", "11:00"]


def test_day_and_month_read_their_own_part_of_the_bucket_key():
    day = js(f"usageBars({json.dumps(BUCKETS)}, 'day')")
    assert [b["label"] for b in day] == ["08", "08", "08"]
    month = js("usageBars([{t: '2026-11', tokens: 5, spend: 1, runs: 1}], 'month')")
    assert [b["label"] for b in month] == ["11"]


def test_pie_slices_are_shares_of_the_month_tokens():
    got = js(f"usageSlices({json.dumps(MODELS)})")
    assert [(s["model"], s["pct"]) for s in got] == [("claude-sonnet", 60), ("deepseek-flash", 30), ("claude-opus", 10)]
    assert got[0]["legend"] == "claude-sonnet · 60% · 600 tokens"


def test_pie_groups_every_model_past_five_as_other():
    got = js(f"usageSlices({json.dumps(SEVEN)})")
    assert [s["model"] for s in got] == ["claude-sonnet", "deepseek-flash", "claude-opus", "gpt-codex", "claude-haiku", "other · 1 model"]
    assert got[-1]["tokens"] == 20


def test_the_ring_lays_each_slice_end_to_end_around_the_circle():
    got = js(f"usageRing(usageSlices({json.dumps(MODELS)}), 50)")
    circle = 2 * 3.141592653589793 * 50
    assert [round(s["len"], 3) for s in got] == [round(0.6 * circle, 3), round(0.3 * circle, 3), round(0.1 * circle, 3)]
    assert [round(s["off"], 3) for s in got] == [0, round(-0.6 * circle, 3), round(-0.9 * circle, 3)]


def test_peak_names_the_busiest_bucket_in_millions():
    assert js(f"usagePeak({json.dumps(BUCKETS)}, 'hour')") == "4.0M at 11:00"


def test_today_line_reads_the_served_totals():
    assert js("usageTodayLine({tok_today: 1000000, today: 1})") == "1.0M tokens · $1.00"


def test_today_line_names_the_operator_session_beside_the_loops_own():
    """#1756: the dollar figure is the loop's, so a day of operator calls is named beside it, never hidden."""
    assert js("usageTodayLine({tok_today: 1000000, today: 1}, {tokens_today: 2000000})") == "1.0M tokens · $1.00 · +2.0M operator"
    assert js("usageTodayLine({tok_today: 1000000, today: 1}, {tokens_today: 0})") == "1.0M tokens · $1.00"


def test_the_phone_names_the_operator_session_as_its_own_slice_and_segment():
    """#1756: the operator session's calls are their own slice and their own bar segment, never merged into the loop's model."""
    models = MODELS + [{"model": "claude-opus", "consumer": "operator", "month_tokens": 100, "spend": 0.0}]
    got = js(f"usageSlices({json.dumps(models)})")
    assert [(s["model"], s["tokens"]) for s in got] == [("claude-sonnet", 600), ("deepseek-flash", 300), ("claude-opus", 100), ("operator · claude-opus", 100)]
    bars = js(f"usageBars({json.dumps([{'t': '2026-11-08 10:00', 'tokens': 1000000, 'op': 3000000, 'runs': 1}])}, 'hour')")
    assert [(b["tokens"], b["op"], b["h"], b["hLoop"]) for b in bars] == [(1000000, 3000000, 100.0, 25.0)]
