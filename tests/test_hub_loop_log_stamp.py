"""A loop-log line carries its UTC time when it has one; a line written before the stamp reads as unknown age, never as zero."""
import calendar
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

HUB_PY = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_loop_log_stamp", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stamped_hold_line_reads_its_utc_time(hub):
    line = "2026-10-30 19:00:00 DISPATCH HOLD: 3 children wait for review (cap 2); no new run this pass"
    assert hub.loop_log_stamp(line) == calendar.timegm((2026, 10, 30, 19, 0, 0))


def test_unstamped_hold_line_is_unknown_age_not_zero(hub):
    line = "DISPATCH HOLD: 3 children wait for review (cap 2); no new run this pass"
    assert hub.loop_log_stamp(line) is None


def test_cycle_line_without_a_date_is_unknown_age(hub):
    assert hub.loop_log_stamp("19:00:00 CYCLE 90s") is None
