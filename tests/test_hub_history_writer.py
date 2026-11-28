"""The hub writes the history store (#1024): each card's timeline is read into it, partial timelines resume, the points are counted, and an hourly search ends the interval of a card that has gone."""
import ast
import importlib.util
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
MUX_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hubmux.py"
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


def test_a_complete_card_is_reread_only_once_its_status_has_moved(hub, writer, monkeypatch):
    reads = []
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "su": TODO}, {"n": 2, "su": TODO}])
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, []), read_search=lambda: [1, 2], now=T0)
    reads.clear()
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, []), read_search=lambda: [1, 2],
                     now=T0 + hub._hist.WINDOW)
    assert reads == []
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "su": "2026-10-03T09:00:00Z"}, {"n": 2, "su": TODO}])
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, []), read_search=lambda: [1, 2],
                     now=T0 + 2 * hub._hist.WINDOW)
    assert reads == [1]


def test_a_complete_card_keeps_its_record_until_its_new_read_is_complete(hub, writer, monkeypatch):
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "su": TODO}, {"n": 2, "su": TODO}])
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: [1, 2], now=T0)
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "su": "2026-10-03T09:00:00Z"}, {"n": 2, "su": TODO}])

    def read(n, cursor):
        if n == 2:
            return _payload(n, [_status(OPENED, "Todo")])
        if cursor is None:
            return _payload(n, [_status(TODO, "Doing")], cursor="C1", more=True)
        return None

    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0 + hub._hist.WINDOW)
    store = hub._hist.load_store(writer)
    assert store[1]["complete"] is True
    assert [e["status"] for e in store[1]["events"]] == ["Todo"]


def test_a_pass_keeps_only_the_last_record_of_each_card(hub, writer, monkeypatch):
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "su": TODO}])
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: [1], now=T0)
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "su": "2026-10-03T09:00:00Z"}])
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo"), _status(TODO, "Doing")]),
                     read_search=lambda: [1], now=T0 + 1)
    records = [r for r in hub._hist.read_records(writer) if r.get("k") == "issue" and r["n"] == 1]
    assert len(records) == 1
    assert [e["status"] for e in hub._hist.load_store(writer)[1]["events"]] == ["Todo", "Doing"]


def test_a_search_capped_at_its_results_limit_is_not_read(hub, monkeypatch):
    search = {"issueCount": 1500, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"number": 1}, {"number": 2}]}
    monkeypatch.setattr(hub, "gh_graphql", lambda query, **kw: {"data": {"search": search}})
    assert hub._history_search() is None
    search["issueCount"] = 2
    assert hub._history_search() == [1, 2]


def test_the_coverage_counts_only_the_stored_cards_on_the_board(hub, writer):
    hub._hist.record_issue(writer, 1, OPENED, OPENED, [], True, T0)
    hub._hist.record_issue(writer, 9, OPENED, OPENED, [], True, T0)
    assert hub._coverage_payload(hub._hist.load_store(writer)) == {
        "read": 1, "complete": 1, "total": 2, "last": hub._hist._iso(T0)}


def test_a_card_that_left_the_board_still_counts_in_the_cfd(hub, writer, monkeypatch):
    monkeypatch.setitem(hub.board_cache, "items", [])
    events = [{"t": OPENED, "type": "added"}, {"t": TODO, "type": "status", "status": "Doing"},
              {"t": "2026-10-03T09:00:00Z", "type": "closed"}]
    hub._hist.record_issue(writer, 1, OPENED, TODO, events, True, T0)
    closed = next(b for b in hub._cfd_payload()["cfd"]["bands"] if b["name"] == hub.COLUMN["issue_closed"])
    assert closed["count"][-1] == 1


def test_the_cfd_serves_each_held_band_top_as_the_page_draws_it(hub, writer, monkeypatch):
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "s": "open", "st": "Doing", "l": [], "t": "card"}])
    events = [{"t": OPENED, "type": "added"}, {"t": TODO, "type": "status", "status": "Doing"},
              {"t": "2026-10-03T09:00:00Z", "type": "label", "name": "frozen:owner"}]
    hub._hist.record_issue(writer, 1, OPENED, TODO, events, True, T0)
    for band in hub._cfd_payload()["cfd"]["bands"]:
        assert band["held_upper"] == [lo + h for lo, h in zip(band["lower"], band["held"], strict=True)]


def test_a_closed_card_the_history_has_not_read_is_not_yet_read(hub, writer, monkeypatch):
    monkeypatch.setitem(hub.board_cache, "items", [{"n": 1, "s": "closed", "st": "Done", "l": [], "t": "card", "c": TODO, "o": OPENED}])
    assert hub.lead_rows()[0]["reason"] == "not_read"


def test_a_partial_timeline_resumes_from_its_cursor_once_the_budget_allows(hub, writer):
    seen = []

    def read(n, cursor):
        seen.append((n, cursor))
        if n == 2:
            return _payload(n, [])
        if cursor is None:
            return _payload(n, [_status(OPENED, "Todo")], cursor="C1", more=True)
        return _payload(n, [_status(TODO, "In Progress")])

    hub._hist.record_points(writer, hub._hist.POINTS_CEILING - 1, T0)
    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0)
    assert hub._hist.load_store(writer)[1]["complete"] is False
    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=T0 + hub._hist.WINDOW + 1)
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


def test_the_search_names_a_card_the_board_read_missed_and_its_timeline_is_read(hub, writer):
    """#1193: the pass read only the cards the board cache listed, so a card the cache missed -- the window in which the
    frontier logged "the board read carries no card for it" -- never had its timeline read, and the closure the loop itself
    caused never reached the store. The hourly search knows every number the repository holds, so a number it returns and the
    store lacks a complete record of is due."""
    reads = []

    def read(n, cursor):
        reads.append(n)
        return _payload(n, [_status(OPENED, "Todo"), _status(TODO, "Done")])

    hub.history_pass(read_issue=read, read_search=lambda: [1, 2, 7], now=T0)
    assert 7 in reads, "the search named #7, the board cache does not list it, and no timeline was read for it"
    store = hub._hist.load_store(writer)
    assert store[7]["complete"] and store[7]["events"][-1]["status"] == "Done"


def test_a_card_the_search_names_and_the_store_already_holds_complete_is_not_read_again(hub, writer):
    reads = []
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, [_status(OPENED, "Todo")]),
                     read_search=lambda: [1, 2, 7], now=T0)
    before = list(reads)
    hub.history_pass(read_issue=lambda n, c: reads.append(n) or _payload(n, [_status(OPENED, "Todo")]),
                     read_search=lambda: [1, 2, 7], now=T0 + hub._hist.SEARCH_EVERY)
    assert reads.count(7) == 1 and before.count(7) == 1, f"#7 was read again while its record stood complete: {reads}"


def test_an_hourly_search_ends_the_interval_of_a_card_it_did_not_find(hub, writer):
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: [1, 2], now=T0)
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: [1],
                     now=T0 + hub._hist.SEARCH_EVERY)
    store = hub._hist.load_store(writer)
    assert store[2]["events"][-1]["type"] == "gone"
    assert store[1]["events"][-1]["type"] != "gone"


def test_a_search_that_is_not_read_ends_no_interval_and_waits_an_hour(hub, writer):
    hub.history_pass(read_issue=lambda n, c: _payload(n, [_status(OPENED, "Todo")]), read_search=lambda: None, now=T0)
    assert all(e["type"] != "gone" for e in hub._hist.load_store(writer)[2]["events"])
    assert hub._hist.search_due(hub._hist.read_records(writer), T0 + 1) is False


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


def _mux_read_threads():
    """The read functions the live server starts for each profile's hub: the names of hubmux's own `SAMPLERS` tuple, the
    one its start loops over."""
    for node in ast.walk(ast.parse(MUX_PY.read_text())):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SAMPLERS" for t in node.targets) \
                and isinstance(node.value, ast.Tuple):
            names = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            if "refresh_board" in names:
                return names
    raise AssertionError("hubmux names no SAMPLERS tuple with a board read")


def test_the_live_mux_starts_the_history_writer_as_a_thread():
    assert "refresh_history" in _mux_read_threads()


def test_the_live_mux_starts_the_fast_board_read_as_a_thread():
    """The live hub is hubmux: a read its SAMPLERS tuple does not name never runs there, so a card waited for the full
    read's period (#3124)."""
    assert "refresh_fast" in _mux_read_threads()


def test_the_store_a_pass_writes_is_the_store_the_charts_and_the_monitor_read(hub, writer):
    now = time.time()
    read = lambda n, c: _payload(n, [_status(OPENED, "Todo")])   # noqa: E731
    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=now - 86400)
    hub.history_pass(read_issue=read, read_search=lambda: [1, 2], now=now)
    assert hub._cfd_payload()["cfd"]["coverage"]["read"] == 2
    assert hub._coverage_payload(hub._hist.load_store(writer))["read"] == 2
    assert hub.throughput_fact() is not None
