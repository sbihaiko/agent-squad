"""Issue #1696: the status tab's spent-today figure is read from today's runs alone, so a poll never parses the whole
history, and it agrees with the cost chart's day bucket (ADR-0108: spend is a measured fact)."""
import importlib.util
import os
import pathlib
import tempfile
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
DAY = 86400.0


@pytest.fixture(scope="module")
def hub():
    for k in ("SG_REPO", "SG_OWNER", "SG_PROJECT"):
        os.environ.setdefault(k, "x")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_status_spend", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record(t0, spend):
    return {"t0": t0, "t1": t0 + 600, "spend": spend, "sub": spend, "api": 0.0, "mod": {}, "verdict": "pass",
            "dur": 10.0, "id": "r", "dir": "/r"}


def _call(t0, spend):
    return {"t0": t0, "model": "x", "in": 1, "out": 0, "cache": 0, "spend": spend, "unpriced": False, "uncounted": False}


def _folders(root, stamps):
    made = []
    for stamp in stamps:
        d = root / stamp
        d.mkdir(parents=True)
        (d / "record.jsonl").write_text("")
        made.append(d)
    return made


def test_the_status_spend_counts_today_alone_and_agrees_with_the_cost_chart(hub, tmp_path, monkeypatch):
    now = time.time()
    today = time.strftime("%Y%m%d", time.localtime(now))
    yday = time.strftime("%Y%m%d", time.localtime(now - DAY))
    week = time.strftime("%Y%m%d", time.localtime(now - 7 * DAY))
    runs = {
        f"run-{today}-080000": _record(now - 60, 1.25),      # started today
        f"run-{yday}-235959": _record(now - 30, 0.5),        # folder yesterday, first event today: the midnight crossing
        f"run-{yday}-120000": _record(now - DAY, 2.0),       # yesterday
        f"run-{week}-120000": _record(now - 7 * DAY, 4.0),   # a week ago: it must never be parsed
    }
    folders = _folders(tmp_path, runs)
    seen = []

    def fake_scan(p):
        name = pathlib.Path(p).parent.name
        seen.append(name)
        return runs.get(name)

    calls = [_call(now - 60, 0.25), _call(now - 7 * DAY, 0.75)]
    monkeypatch.setattr(hub, "project_dirs", lambda: [tmp_path])
    monkeypatch.setattr(hub, "scan", fake_scan)
    monkeypatch.setattr(hub, "ledger_calls", lambda: calls)

    assert hub.spend_today() == 2.0                          # 1.25 + 0.5 in runs, 0.25 in a review call
    assert f"run-{week}-120000" not in seen                  # the history is never parsed for a status poll
    assert hub.spend_today() == hub.stats("day")["totals"]["today"]   # the meter and the chart read one bucket
