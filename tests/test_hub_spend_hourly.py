"""Issue #1267: the Spend box reads the median tokens per hour and the tokens of the last 60 minutes.
The readings #1251 added stay, in tokens: the models run on subscriptions, so a dollar figure is not what the loop pays."""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SRC = HUB_PY.read_text()
POLL = SRC[SRC.index("async function pollStats"): SRC.index("pollStats();setInterval")]
NOW = 1_800_000_000.0
H = 3600.0


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_spend_hourly", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(t1, tokens, done=True):
    return {"id": "r", "t0": t1 - 600, "t1": t1, "spend": 0.0, "sub": 0.0, "api": 0.0,
            "mod": {"m": {"in": tokens, "out": 0, "cache": 0, "spend": 0.0, "calls": 1}}, "dur": 10.0, "done": done}


def _call(t0, spend, unpriced=False, tokens=2):
    return {"t0": t0, "model": "m", "in": tokens, "out": 0, "cache": 0, "spend": spend, "unpriced": unpriced, "uncounted": False}


def test_pace_median_tokens_over_hours_with_runs_and_last_hour_sum(hub):
    runs = [_run(NOW - 10 * 60, 60), _run(NOW - 20 * 60, 0),    # last 60 minutes: 60 tokens
            _run(NOW - 3 * H, 100),                             # one hour of 100 tokens
            _run(NOW - 5 * H, 20)]                              # one hour of 20 tokens
    p = hub.spend_pace(runs, [], NOW)
    assert p["last60_tokens"] == 60
    assert p["window_hours"] == 24
    assert p["active_hours"] == 3 and p["idle_hours"] == 21
    assert p["median_tokens"] == 60   # median of 60, 100, 20; the 21 no-run hours are not zeros


def test_pace_ignores_unfinished_runs_and_runs_outside_the_window(hub):
    runs = [_run(NOW - 30 * 60, 3, done=False), _run(NOW - 30 * H, 99)]
    p = hub.spend_pace(runs, [], NOW)
    assert p["last60_tokens"] == 0 and p["active_hours"] == 0 and p["median_tokens"] is None


def test_pace_names_unpriced_calls_in_the_window(hub):
    calls = [_call(NOW - 60, 0.0, unpriced=True, tokens=5), _call(NOW - 120, 1.5, tokens=7),
             _call(NOW - 40 * H, 0.0, unpriced=True, tokens=9)]
    p = hub.spend_pace([], calls, NOW)
    assert p["unpriced"] == 1 and p["last60_tokens"] == 12   # a call's tokens, whatever its price


def test_stats_carries_pace_and_page_only_prints_it(hub, monkeypatch):
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    monkeypatch.setattr(hub, "ledger_calls", lambda: [])
    assert "pace" in hub.stats("hour")
    line = next(ln for ln in POLL.splitlines() if "stsr" in ln)
    assert "median_tokens" in line and "last60_tokens" in line
    assert "tokens/h median" in line and "last ${p.window_hours} h" in line   # the window is named
    assert '"$"+' not in line   # no dollar figure leads the summary (#1267)
    assert not re.search(r"sort\(|reduce\(", POLL)   # the page computes nothing (ADR-0137)
