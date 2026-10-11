"""Hub run list: the first group is RUNNING and holds only a live process (#1496)."""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_run_groups", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_needs_human_without_a_live_process_is_not_in_running(hub):
    ended = hub.group_of({"state": "needs-human", "age": 120, "live": False})
    live = hub.group_of({"state": "running", "age": 120, "live": True})
    assert ended != "running"
    assert live == "running"


def test_live_running_starting_stalled_are_in_running(hub):
    for st in ("running", "starting", "stalled"):
        assert hub.group_of({"state": st, "age": 60, "live": True}) == "running"


def test_ended_run_lands_in_the_age_bucket(hub):
    assert hub.group_of({"state": "needs-human", "age": 100, "live": False}) == "1h"
    assert hub.group_of({"state": "failed", "age": 4000, "live": False}) == "2h"
    assert hub.group_of({"state": "completed", "age": 100, "live": False}) == "1h"


def test_run_list_group_header_reads_running_not_open(hub):
    m = re.search(r"const GROUPS=(\[[^\]]+\])", hub.PAGE)
    assert m, "GROUPS missing"
    groups = m.group(1)
    assert '["running","RUNNING"]' in groups
    assert "OPEN" not in groups and '["open"' not in groups
