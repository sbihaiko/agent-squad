"""The board history store: what it reads, how it batches, what it spends, and that a rebuild gives the same intervals."""
import importlib.util
import json
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts"
FIXTURES = SCRIPTS / "fixtures" / "board-history"
_spec = importlib.util.spec_from_file_location("board_history", SCRIPTS / "board_history.py")
bh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bh)

T0 = 1_800_000_000
PROJECT = 5


def fixture(name):
    return json.loads((FIXTURES / name).read_text())["data"]["node"]


def test_history_path_is_one_file_per_repository():
    board = "/hub/hub-board.json"
    a = bh.history_path(board, "sbihaiko/agent-squad")
    b = bh.history_path(board, "sbihaiko/squad-board")
    assert a.name == "hub-history.sbihaiko_agent-squad.jsonl"
    assert a != b


def test_status_move_on_a_closed_item_is_read_and_item_alone_is_not():
    items = [
        {"n": 1, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
         "item_updated": "2026-10-09T00:00:00Z"},
        {"n": 2, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-05T00:00:00Z",
         "item_updated": "2026-10-05T00:00:00Z", "closed": True},
        {"n": 3, "issue_updated": "2026-10-07T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
         "item_updated": "2026-10-01T00:00:00Z"},
    ]
    stored = {
        1: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z"},
        2: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z"},
        3: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z"},
    }
    assert bh.due_reads(items, stored) == [2, 3]


def test_a_card_with_no_stored_record_is_read_once():
    items = [{"n": 9, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z"}]
    assert bh.due_reads(items, {}) == [9]


def test_one_issue_per_timeline_query_is_the_measured_batch():
    assert bh.BATCH_SIZE == 1
    assert bh.backfill_batch([4, 5, 6], {}) == [4]


def test_a_short_timeline_resumes_from_its_stored_cursor():
    page1 = {"totalCount": 3, "nodes": [
        {"__typename": "LabeledEvent", "createdAt": "2026-10-01T00:00:01Z", "label": {"name": "a"}},
        {"__typename": "LabeledEvent", "createdAt": "2026-10-01T00:00:02Z", "label": {"name": "b"}},
    ], "pageInfo": {"hasNextPage": True, "endCursor": "C1"}}
    assert bh.timeline_complete(page1) is False
    assert bh.timeline_after(None) is None
    store = {}
    bh.record_page(store, 7, page1, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z")
    assert bh.timeline_after(store[7]) == "C1"
    page2 = {"totalCount": 3, "nodes": [
        {"__typename": "LabeledEvent", "createdAt": "2026-10-01T00:00:03Z", "label": {"name": "c"}},
    ], "pageInfo": {"hasNextPage": False, "endCursor": "C2"}}
    bh.record_page(store, 7, page2, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z")
    assert bh.timeline_complete(page2) is True
    assert len(store[7]["events"]) == 3
    assert bh.timeline_after(store[7]) is None


def test_hourly_search_runs_once_an_hour_and_never_as_a_sweep():
    assert bh.search_due([], T0) is True
    records = [{"k": "search", "t": T0}]
    assert bh.search_due(records, T0 + 3599) is False
    assert bh.search_due(records, T0 + 3600) is True


def test_a_card_missing_from_the_hourly_search_ends_its_interval(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_issue(path, 5, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", [
        {"t": "2026-10-01T00:00:00Z", "type": "added"}], complete=True, now=T0)
    bh.record_issue(path, 6, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", [
        {"t": "2026-10-01T00:00:00Z", "type": "added"}], complete=True, now=T0)
    bh.record_search(path, found=[5], now=T0 + 3600)
    store = bh.load_store(path)
    assert bh.intervals(store[6]["events"])[-1]["on_board"] is False
    assert bh.intervals(store[5]["events"])[-1]["on_board"] is True


def test_points_per_hour_counts_only_the_last_hour(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, 1, T0)
    bh.record_points(path, 2, T0 + 100)
    bh.record_points(path, 5, T0 + 3700)
    assert bh.points_last_hour(path, T0 + 3700) == 5


def test_over_the_ceiling_the_next_read_is_deferred_to_the_window_edge(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, bh.POINTS_CEILING, T0)
    ok, wait_until = bh.may_read(path, T0 + 10, 1)
    assert ok is False
    assert wait_until == T0 + 3600


def test_retry_after_defers_the_next_read(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, 1, T0, retry_after=120)
    assert bh.may_read(path, T0 + 60, 1) == (False, T0 + 120)
    assert bh.may_read(path, T0 + 120, 1) == (True, None)


def test_the_monitor_fact_shows_the_points_the_store_counted(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, 5, T0)
    fact = bh.budget_fact(path, T0 + 1)
    assert fact["key"] == "graphql"
    assert fact["text"] == f"GraphQL 5/{bh.POINTS_CEILING} pts/h"


def test_a_resumed_backfill_reads_only_the_unread_issues(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    for n in (1, 2):
        bh.record_issue(path, n, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", [], complete=True, now=T0)
    assert bh.backfill_batch([1, 2, 3, 4], bh.load_store(path)) == [3]
    assert bh.coverage(bh.load_store(path))["read"] == 2


def test_two_repositories_on_one_project_never_mix(tmp_path):
    board = tmp_path / "hub-board.json"
    a = bh.history_path(board, "sbihaiko/agent-squad")
    b = bh.history_path(board, "sbihaiko/squad-board")
    bh.record_issue(a, 5, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z",
                    [{"t": "2026-10-01T00:00:00Z", "type": "added"}], complete=True, now=T0)
    assert 5 not in bh.load_store(b)


def test_rebuilt_intervals_equal_the_first_build(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    events = bh.events_of(fixture("removed-readded.json"))
    bh.record_issue(path, 234, "2026-10-14T05:54:37Z", "2026-10-14T05:31:18Z", events, complete=True, now=T0)
    first = bh.intervals(bh.load_store(path)[234]["events"])
    path.unlink()
    bh.record_issue(path, 234, "2026-10-14T05:54:37Z", "2026-10-14T05:31:18Z",
                    bh.events_of(fixture("removed-readded.json")), complete=True, now=T0)
    assert bh.intervals(bh.load_store(path)[234]["events"]) == first


def test_the_status_clock_is_the_project_field_updated_at():
    facts = bh.item_facts(fixture("label-added.json"), PROJECT)
    assert facts["status_updated"] == "2026-10-16T00:30:23Z"


def test_a_hand_built_timeline_gives_literal_intervals():
    events = [
        {"t": "2026-10-01T10:00:00Z", "type": "added"},
        {"t": "2026-10-01T10:01:00Z", "type": "status", "status": "Todo"},
        {"t": "2026-10-01T10:05:00Z", "type": "status", "status": "In Progress"},
        {"t": "2026-10-01T10:09:00Z", "type": "removed"},
        {"t": "2026-10-01T10:10:00Z", "type": "added"},
    ]
    got = bh.intervals(events)
    assert [(i["from"], i["to"], i["on_board"]) for i in got] == [
        ("2026-10-01T10:00:00Z", "2026-10-01T10:01:00Z", True),
        ("2026-10-01T10:01:00Z", "2026-10-01T10:05:00Z", True),
        ("2026-10-01T10:05:00Z", "2026-10-01T10:09:00Z", True),
        ("2026-10-01T10:09:00Z", "2026-10-01T10:10:00Z", False),
        ("2026-10-01T10:10:00Z", None, True),
    ]
    assert got[0]["column"] is None
    assert got[1]["column"] == bh.view_column("Todo", False, [], None)[0]
