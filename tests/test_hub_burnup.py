"""The spec lane's burn-up (#1025): the hub answers one spec's lines from the history store, and the page opens it from the lane."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
SPEC = 1018
OPENED = "2026-10-01T09:00:00Z"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_burnup")
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir()
    mp.setenv("SG_REPO", "x/y")
    mp.setenv("SG_OWNER", "x")
    mp.setenv("SG_PROJECT", "4")
    mp.setenv("SG_CHECKOUT", str(base / "checkout"))
    mp.setenv("SG_HUB_DIR", str(base / "hub"))
    mp.setenv("SG_STATE", str(base / "state"))
    mp.delenv("SG_BOARD_CACHE", raising=False)
    try:
        yield _load("hub_burnup", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def store(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    return path


def _day(d):
    return f"2026-10-0{d}T09:00:00Z"


def _ev(d, kind, **fields):
    return {"t": _day(d), "type": kind, **fields}


def test_the_hub_serves_the_burnup_route_for_one_spec():
    source = HUB_PY.read_text()
    assert 'self.path.startswith("/api/chart/burnup")' in source
    assert "flow_burnup(int(m[1]))" in source


def test_the_burnup_answer_comes_from_the_history_store(hub, store):
    hub._hist.record_issue(store, 7, OPENED, OPENED, [
        _ev(1, "added"), _ev(1, "status", status="Doing"), _ev(1, "spec", spec=SPEC), _ev(3, "closed"),
    ], True, 1_700_000_000)
    answer = hub.flow_burnup(SPEC)
    assert answer["points"] == [
        {"t": _day(1), "scope": 1, "done": 0, "held": 0, "marks": [{"k": "added", "n": 7}]},
        {"t": _day(3), "scope": 1, "done": 1, "held": 0, "marks": []},
    ]


def test_the_burnup_answer_is_cached_until_the_store_changes(hub, store):
    hub._hist.record_issue(store, 7, OPENED, OPENED, [_ev(1, "added"), _ev(1, "spec", spec=SPEC)], True, 1_700_000_000)
    first = hub.flow_burnup(SPEC)
    assert hub.flow_burnup(SPEC) is first
    hub._hist.record_issue(store, 8, OPENED, OPENED, [_ev(2, "added"), _ev(2, "spec", spec=SPEC)], True, 1_700_000_001)
    assert hub.flow_burnup(SPEC)["points"][-1]["scope"] == 2


def test_the_spec_lane_offers_the_burnup_and_the_page_fetches_it(hub):
    assert re.search(r'class=bu data-bu="\$\{l\.num\}"', hub.PAGE)
    assert 'fetch("/api/chart/burnup?spec="+n)' in hub.PAGE
    assert "buToggle(bu.dataset.bu)" in hub.PAGE
