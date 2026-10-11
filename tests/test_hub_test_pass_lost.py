"""Hub run list: a coordinator test pass that dies mid-flight stops counting as a live run (#1783).

The pass writes `state: running` at its start and its terminal state at the end. A pass that is killed in
between leaves that file at `running` forever, and the old bound skipped only the finished ones, so the left
pane counted a live run with no process, no folder and no dashboard. The hub now bounds a `running` entry by
age too, and shows it as its last known state with a lost note instead of dropping it -- the loop's own
readers of these files are untouched; this is the hub's display bound, not a claim the test failed.
"""
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

LOST_AFTER = 4 * 3600   # well past the suite's own timeout: a pass that has not touched its file is not running


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_STATE"] = str(tmp_path_factory.mktemp("state"))
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_test_pass_lost", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pass_file(hub, stem, state, age, issue=1565):
    """One result file the way test.sh writes it, with its mtime set `age` seconds back."""
    t = hub.STATE / "tests"
    t.mkdir(parents=True, exist_ok=True)
    f = t / (stem + ".json")
    now = time.time()
    f.write_text(json.dumps({"issue": issue, "cmd": "bash fork-gate.sh bash serial.sh gui-suite pytest",
                             "dir": "/tmp/x", "state": state, "t0": now - age}))
    os.utime(f, (now - age, now - age))
    return f


def _test_rows(hub):
    return [r for r in hub.runs() if r.get("kind") == "test"]


def test_a_dead_running_pass_is_not_listed_as_a_live_run(hub):
    f = _pass_file(hub, "test-1565-dead", "running", 68 * 60)
    row = next(r for r in _test_rows(hub) if r["id"] == f.stem)
    assert row["state"] != "running", "a pass that has not touched its file in 68 min is not running"
    assert row["live"] is False, "the left pane must not count it among the live runs"
    assert "lost" in row["tip"].lower(), "it stays visible, marked lost"
    assert hub.group_of(row) != "running", "and it leaves the running group"


def test_a_fresh_running_pass_is_still_a_live_run(hub):
    f = _pass_file(hub, "test-1565-live", "running", 60)
    row = next(r for r in _test_rows(hub) if r["id"] == f.stem)
    assert row["state"] == "running"
    assert row["live"] is True
    assert "lost" not in row["tip"].lower()
    assert hub.group_of(row) == "running"


def test_a_running_pass_just_past_the_bound_is_lost(hub):
    f = _pass_file(hub, "test-1565-edge", "running", LOST_AFTER + 60)
    row = next(r for r in _test_rows(hub) if r["id"] == f.stem)
    assert row["state"] != "running" and row["live"] is False


def test_an_old_finished_pass_is_still_dropped(hub):
    f = _pass_file(hub, "test-1566-old-done", "completed", 5 * 3600, issue=1566)
    assert not [r for r in _test_rows(hub) if r["id"] == f.stem], "the bound on finished passes is unchanged"


def test_the_hub_never_rewrites_the_result_file(hub):
    f = _pass_file(hub, "test-1567-untouched", "running", 5 * 3600, issue=1567)
    before = f.stat().st_mtime
    _test_rows(hub)
    assert json.loads(f.read_text())["state"] == "running", "the runner's file keeps the state the loop reads"
    assert f.stat().st_mtime == before, "and the hub never touches it"
