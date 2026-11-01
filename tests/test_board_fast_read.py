"""The fast read of the open cards (SPEC 968), driven over recorded GraphQL answers through the board route."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
sys.path.insert(0, str(SCRIPTS))
import board_fast  # noqa: E402

REPO = "x/y"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", REPO)
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_fast_read", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def node(n, status="Todo", state="OPEN", reason=None, repo=REPO, archived=False, labels=(), fd=None):
    return {"updatedAt": "2026-11-01T10:00:00Z", "isArchived": archived,
            "fieldValueByName": {"name": status, "updatedAt": "2026-11-01T10:00:00Z"} if status else None,
            "content": {"number": n, "fullDatabaseId": fd if fd is not None else 900000 + n, "state": state,
                        "stateReason": reason, "updatedAt": "2026-11-01T10:00:00Z",
                        "repository": {"nameWithOwner": repo},
                        "labels": {"totalCount": len(labels), "nodes": [{"name": x} for x in labels]}}}


def page(nodes, more=None, cost=1):
    return {"data": {"rateLimit": {"cost": cost},
                     "user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": bool(more), "endCursor": more}, "nodes": nodes}}}}}


def recorded(*answers):
    calls = []
    queue = list(answers)

    def gql(query, variables, timeout=None):
        calls.append(variables)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    gql.calls = calls
    return gql


def card(n, st="Todo", state="closed", cr=None, labels=None, fd=None):
    return {"n": n, "t": f"card {n}", "s": state, "c": None, "st": st, "l": labels or [], "i": f"PVTI_{n}", "ot": 1,
            "fd": fd if fd is not None else 900000 + n}


def _column(hub, n):
    """The column the board route draws card `n` in: the group of the route's own answer that holds it."""
    for group in hub.board(moves={})["groups"]:
        if any(r.get("n") == n for r in group["items"]):
            return group["name"]
    return None


@pytest.fixture
def board(hub, monkeypatch):
    monkeypatch.setattr(hub, "REPO", REPO)
    monkeypatch.setattr(hub, "FAST_BUDGET", board_fast.Budget(600))
    monkeypatch.setattr(hub, "_fast_root", lambda: "user")
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [card(11, st="Todo")], "prs": [], "lc": {}, "err": None, "parts": {}})
    return hub


def test_a_status_move_shows_in_its_new_column_on_the_next_fast_read(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing")])))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Doing"
    assert _column(board, 11) == "Doing"


def test_a_close_with_no_status_change_moves_the_state_and_no_column(board, monkeypatch):
    board.board_cache["items"] = [card(11, st="Doing", state="open")]
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing", state="CLOSED", reason="COMPLETED")])))
    board.fast_board_pass()
    row = board.board_cache["items"][0]
    assert (row["st"], row["s"], row["cr"]) == ("Doing", "closed", "completed")
    assert _column(board, 11) is None, "a closed card leaves the open columns: closed is history, never a column (#969)"


def test_an_abandon_keeps_its_row_until_the_full_read(board, monkeypatch):
    """The read filters out the closed statuses, so a card moved to Abandoned is not in the snapshot: the full read moves it."""
    gql = recorded(page([]))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    assert "-status:Closed -status:Abandoned" in gql.calls[0]["q"]
    assert board.board_cache["items"][0]["st"] == "Todo"


def test_a_reopen_clears_the_close_reason_and_sets_the_state_open(board, monkeypatch):
    board.board_cache["items"] = [{**card(11, st="Todo", state="closed"), "cr": "not_planned"}]
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Todo", state="OPEN", reason=None)])))
    board.fast_board_pass()
    row = board.board_cache["items"][0]
    assert row["s"] == "open" and "cr" not in row


def test_a_failed_page_discards_the_whole_snapshot(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing")], more="c1"), RuntimeError("HTTP 502")))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Todo", "a failed later page leaves the earlier page's move unapplied"
    assert board.board_cache["parts"]["status"]["err"]


def test_a_partial_graphql_error_discards_the_whole_snapshot(board, monkeypatch):
    bad = {"data": {"user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": [node(11, status="Doing")]}}}},
           "errors": [{"message": "Something went wrong"}]}
    monkeypatch.setattr(board, "_graphql", recorded(bad))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Todo"


def test_a_card_whose_labels_are_cut_short_discards_the_snapshot(board, monkeypatch):
    long = node(11, status="Doing")
    long["content"]["labels"] = {"totalCount": 25, "nodes": [{"name": f"l{i}"} for i in range(20)]}
    monkeypatch.setattr(board, "_graphql", recorded(page([long])))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Todo"


def test_the_fast_read_never_adds_a_new_card(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing"), node(12, status="Todo")])))
    board.fast_board_pass()
    assert [i["n"] for i in board.board_cache["items"]] == [11]


def test_the_join_is_by_full_database_id_never_by_number_alone(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing", fd=555)])))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Todo", "a number that names another database id is another card"


def test_other_repositories_and_archived_items_are_not_joined(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing", repo="other/repo"),
                                                         node(11, status="Done", archived=True)])))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Todo"


def test_the_read_never_passes_a_rest_id_to_node():
    assert "node(id" not in board_fast.query("user") and "node(id" not in board_fast.query("organization")


def test_a_spent_hourly_budget_stops_the_read(board, monkeypatch):
    board.FAST_BUDGET = board_fast.Budget(1)
    gql = recorded(page([node(11, status="Doing")], more="c1", cost=1), page([node(11, status="Doing")]))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    assert len(gql.calls) == 1, "a read whose hour is spent sends no further page"
    assert board.board_cache["items"][0]["st"] == "Todo"


def test_a_failed_call_is_charged_to_the_hour(board, monkeypatch):
    board.FAST_BUDGET = board_fast.Budget(1)
    gql = recorded(RuntimeError("HTTP 502"), page([node(11, status="Doing")]))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    board.fast_board_pass()
    assert len(gql.calls) == 1, "a failed attempt spends a point, so the retry waits for the hour"


def test_the_fast_read_does_not_hold_the_board_lock_while_it_reads(board, monkeypatch):
    seen = []

    def gql(query, variables, timeout=None):
        seen.append(board._board_lock.locked())
        return page([node(11, status="Doing")])
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    assert seen == [False], "the lock covers the write only, so a full read is never blocked by a fast read in flight"


def test_the_fast_read_passes_on_its_own_every_fifteen_seconds(hub, monkeypatch):
    passes, sleeps = [], []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise StopIteration

    monkeypatch.setattr(hub, "fast_board_pass", lambda: passes.append(1))
    monkeypatch.setattr(hub.time, "sleep", fake_sleep)
    with pytest.raises(StopIteration):
        hub.refresh_fast()
    assert len(passes) == 2 and sleeps == [hub.FAST_EVERY] * 3


def test_a_fast_pass_that_raises_does_not_end_the_fast_loop(hub, monkeypatch):
    passes, sleeps = [], []

    def bad_pass():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("boom")

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise StopIteration

    monkeypatch.setattr(hub, "fast_board_pass", bad_pass)
    monkeypatch.setattr(hub.time, "sleep", fake_sleep)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None, "parts": {}})
    with pytest.raises(StopIteration):
        hub.refresh_fast()
    assert len(passes) == 2, "the next fast pass still runs after one raised"


def test_the_full_read_runs_every_ten_to_fifteen_minutes(board):
    assert 600 <= board.FULL_EVERY <= 900
    assert board.FAST_EVERY == 15
