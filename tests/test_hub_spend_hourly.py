"""Issue #1251: the Spend box reads the median spend per hour and the last 60 minutes in USD, computed by the hub."""
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


def _run(t1, spend, done=True):
    return {"id": "r", "t0": t1 - 600, "t1": t1, "spend": spend, "sub": 0.0, "api": spend, "mod": {}, "dur": 10.0, "done": done}


def _call(t0, spend, unpriced=False):
    return {"t0": t0, "model": "m", "in": 1, "out": 1, "cache": 0, "spend": spend, "unpriced": unpriced, "uncounted": False}


def test_pace_median_over_hours_with_runs_and_last_hour_sum(hub):
    runs = [_run(NOW - 10 * 60, 4.0), _run(NOW - 20 * 60, 2.0),   # last 60 minutes: 6.0
            _run(NOW - 3 * H, 10.0),                              # one hour of 10.0
            _run(NOW - 5 * H, 2.0)]                               # one hour of 2.0
    p = hub.spend_pace(runs, [], NOW, "both")
    assert p["last60"] == 6.0
    assert p["window_hours"] == 24
    assert p["active_hours"] == 3 and p["idle_hours"] == 21
    assert p["median_hour"] == 6.0   # median of 6, 10, 2; the 21 no-run hours are not zeros


def test_pace_ignores_unfinished_runs_and_runs_outside_the_window(hub):
    runs = [_run(NOW - 30 * 60, 3.0, done=False), _run(NOW - 30 * H, 99.0)]
    p = hub.spend_pace(runs, [], NOW, "both")
    assert p["last60"] == 0.0 and p["active_hours"] == 0 and p["median_hour"] is None


def test_pace_names_unpriced_calls_in_the_window(hub):
    calls = [_call(NOW - 60, 0.0, unpriced=True), _call(NOW - 120, 1.5), _call(NOW - 40 * H, 0.0, unpriced=True)]
    p = hub.spend_pace([], calls, NOW, "both")
    assert p["unpriced"] == 1 and p["last60"] == 1.5


def test_stats_carries_pace_and_page_only_prints_it(hub, monkeypatch):
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    monkeypatch.setattr(hub, "ledger_calls", lambda: [])
    assert "pace" in hub.stats("hour")
    assert "st.pace" in POLL and "median_hour" in POLL and "last60" in POLL
    assert not re.search(r"sort\(|reduce\(", POLL)   # the page computes nothing (ADR-0137)
