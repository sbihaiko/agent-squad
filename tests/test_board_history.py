"""The board history store: what it reads, how it batches, what it spends, and that a rebuild gives the same intervals."""
import importlib.util
import json
import pathlib

import pytest

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


def test_status_move_on_a_closed_item_is_read_and_no_other_move_is_not():
    items = [
        {"n": 1, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
         "item_updated": "2026-10-09T00:00:00Z", "closed": False},
        {"n": 2, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-05T00:00:00Z",
         "item_updated": "2026-10-05T00:00:00Z", "closed": False},
        {"n": 3, "issue_updated": "2026-10-07T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
         "item_updated": "2026-10-01T00:00:00Z", "closed": False},
    ]
    stored = {
        1: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z", "complete": True},
        2: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z", "complete": True},
        3: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z", "complete": True},
    }
    assert bh.due_reads(items, stored) == [2]


def test_a_card_with_no_stored_record_is_read_once():
    items = [{"n": 9, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
              "closed": False}]
    assert bh.due_reads(items, {}) == [9]


def test_a_close_that_moves_no_card_is_read_from_the_board_row():
    stored = {7: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
                  "events": [{"t": "2026-10-01T00:00:00Z", "type": "added"},
                             {"t": "2026-10-01T00:00:00Z", "type": "status", "status": "Backlog"}],
                  "complete": True}}
    row = {"n": 7, "su": "2026-10-01T00:00:00Z", "st": "Backlog", "s": "CLOSED"}
    assert bh.due_items([row], stored) == [7], "the issue closed while its card kept Status Backlog"
    assert bh.due_items([dict(row, s="OPEN")], stored) == [], "an open card against an open record is no reason"


def test_a_status_move_the_clock_hides_is_read_from_the_board_row():
    stored = {2286: {"issue_updated": "2026-11-18T14:29:07Z", "status_updated": "2026-11-18T14:29:10Z",
                     "events": [{"t": "2026-11-18T14:29:07Z", "type": "status", "status": "Todo"}],
                     "complete": True}}
    row = {"n": 2286, "su": "2026-11-18T14:29:10Z", "st": "Reviewing", "s": "OPEN"}
    assert bh.due_items([row], stored) == [2286], "the clock agrees and only the value says the record is behind"
    stored[2286]["events"].append({"t": "2026-11-18T16:39:39Z", "type": "status", "status": "Reviewing"})
    assert bh.due_items([row], stored) == [], "a record that ends in the card's Status is settled"


def test_a_short_timeline_is_due_again_until_it_is_complete():
    items = [{"n": 4, "issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
              "closed": False}]
    stored = {4: {"issue_updated": "2026-10-01T00:00:00Z", "status_updated": "2026-10-01T00:00:00Z",
                  "complete": False, "cursor": "C1"}}
    assert bh.due_reads(items, stored) == [4]


def test_a_resumed_backfill_picks_up_a_short_timeline_before_the_next_issue():
    stored = {1: {"complete": True}, 2: {"complete": False, "cursor": "C1"}}
    assert bh.backfill_batch([1, 2, 3], {}) == [1]
    assert bh.backfill_batch([1, 2, 3], stored) == [2]
    stored[2]["complete"] = True
    assert bh.backfill_batch([1, 2, 3], stored) == [3]


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


def test_a_project_event_of_another_project_is_not_this_cards_move():
    """#1663: an issue can sit on several projects, and the timeline carries one added/removed event per project. The card's
    board is this project's: an event naming another project is never its move, so a card moved off another board never
    leaves this one and no valley opens where it was moved."""
    page = {"totalCount": 3, "nodes": [
        {"__typename": "AddedToProjectV2Event", "createdAt": "2026-10-01T00:00:01Z", "project": {"number": 3}},
        {"__typename": "AddedToProjectV2Event", "createdAt": "2026-10-01T00:00:02Z", "project": {"number": 2}},
        {"__typename": "RemovedFromProjectV2Event", "createdAt": "2026-10-01T00:00:03Z", "project": {"number": 2}},
    ], "pageInfo": {"hasNextPage": False, "endCursor": None}}
    store = {}
    bh.record_page(store, 7, page, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", project=3)
    assert [e["type"] for e in store[7]["events"]] == ["added"], "only this project's own events are the card's moves"
    assert bh.intervals(store[7]["events"])[-1]["on_board"] is True
    # A page read for another board keeps that board's events and drops these.
    other = {}
    bh.record_page(other, 7, page, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", project=2)
    assert [e["type"] for e in other[7]["events"]] == ["added", "removed"]
    assert bh.intervals(other[7]["events"])[-1]["on_board"] is False


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


def test_the_hourly_search_writes_one_record_for_each_card_it_did_not_find(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    for n in (5, 6, 7):
        bh.record_issue(path, n, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z",
                        [{"t": "2026-10-01T00:00:00Z", "type": "added"}], complete=True, now=T0)
    before = len(bh.read_records(path))
    bh.record_search(path, found=[5, 7], now=T0 + 3600)
    appended = bh.read_records(path)[before:]
    assert [r["k"] for r in appended] == ["search", "issue"]
    assert appended[1]["n"] == 6 and appended[1]["events"][-1]["type"] == "gone"


def test_points_per_hour_counts_only_the_last_hour(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, 1, T0)
    bh.record_points(path, 2, T0 + 100)
    bh.record_points(path, 5, T0 + 3700)
    assert bh.points_last_hour(path, T0 + 3700) == 5


def test_over_the_ceiling_the_next_read_is_deferred_to_the_window_edge(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, bh.POINTS_CEILING, T0)
    ok, wait_until = bh.allow(bh.read_records(path), T0 + 10, 1)
    assert ok is False
    assert wait_until == T0 + 3600


def test_retry_after_defers_the_next_read(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_points(path, 1, T0, retry_after=120)
    assert bh.allow(bh.read_records(path), T0 + 60, 1) == (False, T0 + 120)
    assert bh.allow(bh.read_records(path), T0 + 120, 1) == (True, None)


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


def test_a_short_timeline_keeps_its_cursor_in_the_file_across_a_reload(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_issue(path, 7, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z",
                    [{"t": "2026-10-01T00:00:01Z", "type": "added"}], complete=False, now=T0, cursor="C1")
    rec = bh.load_store(path)[7]
    assert bh.timeline_after(rec) == "C1"
    assert bh.backfill_batch([7], bh.load_store(path)) == [7]


def test_the_coverage_fact_shows_cards_read_complete_and_the_last_refresh(tmp_path):
    path = tmp_path / "hub-board.jsonl"
    bh.record_issue(path, 1, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", [], complete=True, now=T0)
    bh.record_issue(path, 2, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", [], complete=False, now=T0 + 60, cursor="C1")
    store = bh.load_store(path)
    assert bh.coverage(store) == {"read": 2, "complete": 1, "last": T0 + 60}
    assert bh.coverage_fact(store, 589)["text"] == "History 2/589 read, 1 complete, last refresh 2027-01-15T08:01:00Z"


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


def _timeline(*events):
    return [{"t": f"2026-10-01T{h:02d}:{m:02d}:00Z", "type": kind, **extra} for h, m, kind, extra in events]


def test_lead_span_splits_flowing_and_held_and_skips_the_owner_park():
    runs = bh.intervals(_timeline(
        (10, 0, "added", {}),
        (10, 1, "status", {"status": "Todo"}),
        (11, 1, "status", {"status": "In Progress"}),
        (12, 1, "label", {"name": "frozen:owner"}),
        (12, 31, "unlabel", {"name": "frozen:owner"}),
        (13, 1, "status", {"status": "Backlog"}),
        (14, 1, "status", {"status": "In Progress"}),
        (14, 31, "closed", {}),
    ))
    span = bh.lead_span(runs, start="Todo")
    assert span["start"] == "2026-10-01T10:01:00Z"
    assert span["end"] == "2026-10-01T14:31:00Z"
    assert span["flowing"] == pytest.approx(3.0)
    assert span["held"] == pytest.approx(0.5)
    assert span["approx"] is False and span["unknown_first"] is False


def test_lead_span_is_none_without_a_verified_todo_entry():
    runs = bh.intervals(_timeline((10, 0, "added", {}), (10, 5, "status", {"status": "In Progress"}), (11, 0, "closed", {})))
    assert bh.lead_span(runs, start="Todo") is None


def test_lead_span_marks_a_card_whose_first_run_is_not_on_the_board_as_unknown_first():
    runs = bh.intervals(_timeline((10, 1, "status", {"status": "Todo"}), (10, 2, "added", {}), (11, 0, "closed", {})))
    span = bh.lead_span(runs, start="Todo")
    assert span["unknown_first"] is True


def test_lead_span_is_not_unknown_first_for_a_card_labelled_and_parented_before_its_add():
    runs = bh.intervals(_timeline(
        (10, 0, "label", {"name": "bug"}),
        (10, 0, "spec", {"spec": 5}),
        (10, 1, "added", {}),
        (10, 2, "status", {"status": "Todo"}),
        (11, 0, "closed", {}),
    ))
    assert bh.lead_span(runs, start="Todo")["unknown_first"] is False


def test_lead_span_is_unknown_first_for_an_incomplete_timeline():
    runs = bh.intervals(_timeline((10, 1, "added", {}), (10, 2, "status", {"status": "Todo"}), (11, 0, "closed", {})))
    assert bh.lead_span(runs, start="Todo", complete=False)["unknown_first"] is True


def test_lead_span_marks_an_approximate_boundary_inside_the_window():
    runs = bh.intervals(_timeline(
        (10, 0, "added", {}),
        (10, 1, "status", {"status": "Todo"}),
        (10, 30, "gone", {}),
        (10, 45, "added", {}),
        (11, 0, "closed", {}),
    ))
    assert bh.lead_span(runs, start="Todo")["approx"] is True


# SPEC 1218 #1256: the lead-time chart breaks a card's life into its columns, and counts the gate's share.
def test_a_gating_interval_opens_and_closes_with_its_instants():
    runs = bh.intervals(_timeline(
        (10, 0, "added", {}),
        (10, 1, "status", {"status": "Todo"}),
        (11, 0, "status", {"status": "Testing"}),
        (11, 45, "status", {"status": "Closed"}),
        (11, 45, "closed", {}),
    ))

    gating = next(run for run in runs if run["column"] == "Testing")

    assert (gating["from"], gating["to"]) == ("2026-10-01T11:00:00Z", "2026-10-01T11:45:00Z")
    obs = bh.lead_observation(runs, start="Todo")
    assert obs["columns"]["Testing"] == pytest.approx(0.75)
    assert obs["gate"] == pytest.approx(0.75)


def test_two_gating_stretches_separated_by_a_freeze_sum_to_both():
    runs = bh.intervals(_timeline(
        (10, 0, "added", {}),
        (10, 1, "status", {"status": "Todo"}),
        (11, 0, "status", {"status": "Testing"}),
        (11, 15, "label", {"name": "frozen:owner"}),
        (11, 45, "unlabel", {"name": "frozen:owner"}),
        (12, 0, "status", {"status": "Closed"}),
        (12, 0, "closed", {}),
    ))

    gating = [run for run in runs if run["column"] == "Testing"]
    assert len(gating) > 1, "the freeze ends the interval without changing the column"

    obs = bh.lead_observation(runs, start="Todo")
    stretches = [s for s in obs["stretches"] if s["column"] == "Testing"]
    assert len(stretches) == 1, "contiguous runs of one column are one stretch"
    assert stretches[0]["hours"] == pytest.approx(1.0), "the freeze inside Testing is still gate time"
    assert obs["gate"] == pytest.approx(1.0)


def test_a_card_that_never_gated_has_no_gating_hours():
    runs = bh.intervals(_timeline(
        (10, 0, "added", {}),
        (10, 1, "status", {"status": "Todo"}),
        (11, 0, "status", {"status": "Code Complete"}),
        (12, 0, "status", {"status": "Closed"}),
        (12, 0, "closed", {}),
    ))

    obs = bh.lead_observation(runs, start="Todo")

    assert "Testing" not in obs["columns"]
    assert obs["gate"] == 0.0


def test_the_columns_total_equals_the_measure_for_a_card_that_held_elsewhere_before_the_start():
    runs = bh.intervals(_timeline(
        (9, 0, "added", {}),
        (9, 30, "status", {"status": "Doing"}),
        (10, 0, "status", {"status": "Todo"}),
        (11, 0, "status", {"status": "Testing"}),
        (12, 0, "status", {"status": "Closed"}),
        (12, 0, "closed", {}),
    ))

    obs = bh.lead_observation(runs, start="Todo")

    assert sum(obs["columns"].values()) == pytest.approx(obs["flowing"] + obs["held"]), \
        "the stack and the measure beside it total the same hours"
    assert "Doing" not in obs["columns"], "the time before the start column is no band"
    assert obs["columns"]["Testing"] == pytest.approx(1.0)


def test_a_parked_run_with_a_hold_is_a_backlog_band_with_its_held_hours():
    runs = bh.intervals(_timeline(
        (10, 0, "added", {}),
        (10, 1, "status", {"status": "Todo"}),
        (11, 0, "status", {"status": "Backlog"}),
        (11, 0, "label", {"name": "frozen:owner"}),
        (12, 0, "unlabel", {"name": "frozen:owner"}),
        (12, 30, "status", {"status": "Todo"}),
        (13, 30, "status", {"status": "Closed"}),
        (13, 30, "closed", {}),
    ))

    obs = bh.lead_observation(runs, start="Todo")

    assert obs["columns"]["Backlog"] == pytest.approx(1.0), "a parked run with a hold is a band in its column"
    assert obs["held"] == pytest.approx(1.0), "the measure counts the parked hold as held time"
    assert sum(obs["columns"].values()) == pytest.approx(obs["flowing"] + obs["held"])
    names = [column["name"] for column in bh._columns.view_columns()]
    assert "Backlog" in names, "every band's column is a board column with a palette role"


def test_2582_one_record_identity_folds_once_and_a_new_one_folds_again():
    """AC F of #2582: the chart folds a record's intervals once per record identity, and a record that moved folds again."""
    events = _timeline((10, 0, "added", {}), (10, 5, "status", {"status": "In Progress"}))
    stamp = (234, T0)
    first = bh.intervals(events, stamp)
    assert bh.intervals([dict(e) for e in events], stamp) is first, "an unchanged record folded its intervals again"
    moved = _timeline((10, 0, "added", {}), (10, 5, "status", {"status": "In Progress"}), (11, 0, "closed", {}))
    later = bh.intervals(moved, (234, T0 + 60))
    assert later is not first and later[-1]["column"] != first[-1]["column"], "a changed record answered the runs before it"
