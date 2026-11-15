"""The hub's background passes run only while a page reads the profile (#1790).

The profiler named the cost: with three profiles open and no page reading them, `history_pass` took 4.5 % of a core and
`foot_refresh` 2.7 %, paid every minute and every five seconds. One rule now answers both loops, `hub.sampling`, and the
request handler writes its only input, `hub.page_read`.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GHREAD_PY = SCRIPTS / "ghread.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS", "SG_HUB_IDLE_SECS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_idle", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_page_has_read_the_profile_so_no_pass_runs(hub):
    assert hub.sampling(now=1_700_000_000.0) is False


def test_a_page_that_read_a_second_ago_keeps_every_pass_running(hub):
    hub.page_read(now=1_700_000_000.0)
    assert hub.sampling(now=1_700_000_001.0) is True


def test_a_page_that_stopped_reading_releases_the_passes_after_the_bound(hub):
    hub.page_read(now=1_700_000_000.0)
    assert hub.sampling(now=1_700_000_000.0 + hub.HUB_IDLE_SECS) is True, "the bound is inclusive: a read at the edge still counts"
    assert hub.sampling(now=1_700_000_000.0 + hub.HUB_IDLE_SECS + 1) is False


def test_the_bound_is_the_profile_setting(hub, monkeypatch, tmp_path):
    """A profile may shorten or lengthen the idle bound through its own file, so the pass cost is a knob and never a constant."""
    import os
    monkeypatch.setenv("SG_HUB_IDLE_SECS", "30")
    spec = importlib.util.spec_from_file_location("hub_idle_30", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.page_read(now=1_700_000_000.0)
    assert mod.sampling(now=1_700_000_031.0) is False
    assert os.environ["SG_HUB_IDLE_SECS"] == "30"
