"""The footer counts down to the watch cycle's next end, from the CYCLE lines the loop writes to cycle.log."""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

HUB_PY = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub.py"
NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_cycle_countdown", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fact(hub, cycle):
    items = hub.foot_items({"cycle": cycle}, NOW)
    return [i for i in items if i["key"] == "cycle"]


def test_the_countdown_runs_to_the_last_end_plus_the_median_cycle(hub):
    f = _fact(hub, {"end": NOW - 100, "secs": [300, 320, 350]})
    assert f and f[0]["text"] == "cycle in 3m40s", f


def test_an_overdue_cycle_says_how_late_it_is(hub):
    f = _fact(hub, {"end": NOW - 400, "secs": [300]})
    assert f[0]["text"] == "cycle +1m40s late"


def test_no_cycle_reading_draws_no_block(hub):
    assert _fact(hub, None) == []


def test_the_cycle_reads_cycle_log(hub, monkeypatch):
    d = Path(tempfile.mkdtemp())
    (d / "cycle.log").write_text("10:00:00 janitor x\n10:05:00 CYCLE 300s\n10:06:00 janitor y\n10:10:30 CYCLE 330s\n")
    monkeypatch.setattr(hub, "STATE", d)
    now = __import__("time").mktime(__import__("time").strptime(__import__("time").strftime("%Y-%m-%d ") + "10:12:00", "%Y-%m-%d %H:%M:%S"))
    c = hub._cycle_state(now)
    assert c["secs"] == [300, 330] and c["end"] == now - 90
