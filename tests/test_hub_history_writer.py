"""The hub writes the history store (#1024): each card's timeline is read into it, partial timelines resume, the points are counted, and an hourly search ends the interval of a card that has gone."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
HIST_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "board_history.py"
T0 = 1_800_000_000
OPENED = "2026-10-01T09:00:00Z"
TODO = "2026-10-02T09:00:00Z"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_history_writer")
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
        yield _load("hub_history_writer", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def writer(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "board.json")
    monkeypatch.setattr(hub, "REPO", "x/y")
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1}, {"n": 2}])
    return hub._hist.history_path(hub.BOARD, hub.REPO)


def _status(at, status):
    return {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": at, "status": status}


def _payload(n, nodes, cursor=None, more=False, cost=1):
    page = {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}
    issue = {"number": n, "updatedAt": OPENED,
             "projectItems": {"nodes": [{"project": {"number": 4}, "fieldValueByName": {"updatedAt": TODO}}]},
             "timelineItems": page}
    return {"data": {"rateLimit": {"cost": cost}, "repository": {"issue": issue}}}


def test_a_pass_stores_each_card_timeline_and_leaves_a_fresh_one_unread(hub, writer):
    reads = []

    def read(n, cursor):
        reads.append((n, cursor))
        return _payload(n, [_status(OPENED, "Todo")])

    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0)
    store = hub._hist.load_store(writer)
    assert sorted(store) == [1, 2]
    assert all(r["complete"] for r in store.values())
    assert store[1]["events"] == [{"t": OPENED, "type": "status", "status": "Todo"}]
    assert reads == [(1, None), (2, None)]

    reads.clear()
    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0 + 60)
    assert reads == []


def test_a_fresh_pass_rereads_a_card_once_an_hour_has_passed(hub, writer):
    reads = []
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, []), read_search=lambda: [1, 2], now=T0)
    reads.clear()
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, []), read_search=lambda: [1, 2],
                     now=T0 + hub._hist.WINDOW)
    assert reads == [1, 2]


def test_a_partial_timeline_resumes_from_its_cursor(hub, writer):
    seen = []

    def read(n, cursor):
        seen.append((n, cursor))
        if n == 2:
            return _payload(n, [])
        if cursor is None:
            return _payload(n, [_status(OPENED, "Todo")], cursor="C1", more=True)
        return _payload(n, [_status(TODO, "In Progress")])

    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0)
    assert hub._hist.load_store(writer)[1]["complete"] is False
    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0 + 1)
    assert (1, "C1") in seen
    store = hub._hist.load_store(writer)
    assert store[1]["complete"] is True
    assert [e["status"] for e in store[1]["events"]] == ["Todo", "In Progress"]


def test_a_pass_spends_points_and_stops_at_the_ceiling(hub, writer):
    hub._hist.record_points(writer, hub._hist.POINTS_CEILING, T0)
    reads = []
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, []), read_search=lambda: [1, 2], now=T0 + 10)
    assert reads == []


def test_the_points_a_read_costs_are_counted_in_the_store(hub, writer):
    hub.history_pass(read_issue=lambda n, c: _payload(n, [], cost=3), read_search=lambda: [1, 2], now=T0)
    assert hub._hist.points_last_hour(writer, T0) == 6


def test_an_hourly_search_ends_the_interval_of_a_card_it_did_not_find(hub, writer):
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: [1, 2], now=T0)
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: [1],
                     now=T0 + hub._hist.SEARCH_EVERY)
    store = hub._hist.load_store(writer)
    assert store[2]["events"][-1]["type"] == "gone"
    assert store[1]["events"][-1]["type"] != "gone"


def test_a_failed_search_records_nothing(hub, writer):
    hub.history_pass(read_issue=lambda n, c: _payload(n, []), read_search=lambda: None, now=T0)
    assert hub._hist.search_due(hub._hist.read_records(writer), T0 + 1) is True


def test_the_day_zone_is_the_zone_the_environment_names(hub, monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    assert hub.day_zone() == "America/Sao_Paulo"
    monkeypatch.setenv("TZ", "Not/AZone")
    assert hub.day_zone() == "UTC"


def test_the_cfd_payload_states_the_zone_it_samples_in(hub, monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    assert hub._cfd_payload()["cfd"]["zone"] == "America/Sao_Paulo"


def test_the_hub_starts_the_history_writer_as_a_thread(hub):
    source = HUB_PY.read_text()
    assert "target=refresh_history" in source
