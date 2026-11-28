"""The fast read of the open cards (SPEC 968), driven over recorded GraphQL answers through the board route."""
import importlib.util
import json
import os
import shutil
import subprocess
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
            "content": {"id": f"I_{n}", "number": n, "fullDatabaseId": str(fd if fd is not None else 900000 + n), "state": state,
                        "stateReason": reason, "updatedAt": "2026-11-01T10:00:00Z",
                        "repository": {"nameWithOwner": repo},
                        "labels": {"totalCount": len(labels), "nodes": [{"name": x} for x in labels]}}}


def page(nodes, more=None, cost=1):
    return {"data": {"rateLimit": {"cost": cost},
                     "user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": bool(more), "endCursor": more}, "nodes": nodes}}}}}


def project_item(hub, status="Closed", archived=False):
    return {"updatedAt": "2026-11-01T10:05:00Z", "isArchived": archived,
            "project": {"number": hub.PROJECT_NO, "owner": {"login": hub.OWNER}},
            "fieldValueByName": {"name": status, "updatedAt": "2026-11-01T10:05:00Z"} if status else None}


def issue(n, state="CLOSED", reason="COMPLETED", items=(), labels=()):
    return {"__typename": "Issue", "id": f"I_{n}", "number": n, "state": state, "stateReason": reason,
            "updatedAt": "2026-11-01T10:00:00Z", "repository": {"nameWithOwner": REPO},
            "labels": {"totalCount": len(labels), "nodes": [{"name": x} for x in labels]},
            "projectItems": {"totalCount": len(items), "nodes": list(items)}}


def batch(*issues):
    return {"data": {"rateLimit": {"cost": 1}, "nodes": list(issues)}}


def recorded(*answers):
    calls, queries = [], []
    queue = list(answers)

    def gql(query, variables, timeout=None):
        calls.append(variables)
        queries.append(query)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    gql.calls = calls
    gql.queries = queries
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


def test_an_abandon_leaves_the_open_columns_on_the_fast_read(board, monkeypatch):
    """The read filters out the closed statuses, so a card moved to Abandoned leaves the snapshot: the batched check
    names it abandoned and its row leaves the open columns (#976 supersedes the old expectation that the full read moves it)."""
    gql = recorded(page([node(11, status="Todo")]), page([]), batch(issue(11, "CLOSED", "NOT_PLANNED", [project_item(board, "Abandoned")])))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    board.fast_board_pass()
    assert "-status:Closed -status:Abandoned" in gql.calls[1]["q"]
    row = board.board_cache["items"][0]
    assert (row["st"], row["s"], row["cr"]) == ("Abandoned", "closed", "not_planned")
    assert _column(board, 11) is None


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


def test_unknown_returns_the_snapshot_row_the_cache_does_not_hold():
    """A card the snapshot carries and the cache does not is the one fact that asks for a full read (#985)."""
    rows = board_fast.rows_of([node(11, status="Doing"), node(12, status="Todo")], REPO)
    held = [card(11)]
    assert board_fast.unknown(rows, held) == [rows[1]], "the card the cache lacks is unknown; the held one is not"


def test_unknown_never_counts_another_repository_or_an_archived_item():
    """Another repository's card and an archived item are not cards this board lost, so neither asks for a full read."""
    rows = board_fast.rows_of([node(12, repo="other/repo"), node(13, archived=True)], REPO)
    assert board_fast.unknown(rows, [card(11)]) == [], "a row of another repository or an archived item is unknown to no board"


def test_the_join_is_by_full_database_id_never_by_number_alone(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing", fd=555)])))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Todo", "a number that names another database id is another card"


def test_a_card_read_from_the_rest_items_route_joins_its_fast_row(board, monkeypatch, tmp_path):
    """The cache row comes from the REST items program, the fast row from GraphQL: they join on the one database id."""
    jq = shutil.which("jq")
    assert jq, "jq runs the items program the hub reads with"
    rest = [{"content_type": "Issue", "id": "PVTI_11", "fields": [{"name": "Status", "value": {"name": {"raw": "Todo"}}}],
             "content": {"id": 911, "number": 11, "repository_url": f"https://api.github.com/repos/{REPO}", "title": "card 11",
                         "state": "OPEN", "state_reason": None, "closed_at": None, "labels": [], "sub_issues_summary": {},
                         "created_at": "2026-11-01T09:00:00Z"}}]
    src = tmp_path / "items.json"
    src.write_text(json.dumps(rest))
    out = subprocess.run([jq, "-c", board.board_items_jq(REPO), str(src)], capture_output=True, text=True, check=True).stdout
    rows = [json.loads(line) for line in out.splitlines() if "_page" not in line]
    assert rows[0]["fd"] == 911, "the items program keys a card by its REST content id, which is the database id"
    board.board_cache["items"] = [{**card(11, st="Todo"), "fd": rows[0]["fd"]}]
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing", fd="911")])))
    board.fast_board_pass()
    assert board.board_cache["items"][0]["st"] == "Doing"


def test_the_first_page_sends_no_after_cursor_on_the_wire(board, monkeypatch):
    import ghread
    gql = recorded(page([node(11, status="Doing")]))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    argv = ghread.graphql_argv(board_fast.query("user"), gql.calls[0])
    assert not any(a.startswith("after=") for a in argv), "a None cursor reaches GitHub as the string None"


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


def test_the_fast_read_reads_nothing_while_no_page_reads_the_profile(hub, monkeypatch):
    """The many-repository hub parks a profile's reads while no page reads it (#1790): the fast read is one of them (#3124)."""
    passes, pauses = [], []

    class Parked:
        def wanted(self): return False
        def pause(self, secs):
            pauses.append(secs)
            if len(pauses) == 3:
                raise StopIteration

    monkeypatch.setattr(hub, "fast_board_pass", lambda: passes.append(1))
    monkeypatch.setattr(hub, "READ_GATE", Parked())
    with pytest.raises(StopIteration):
        hub.refresh_fast()
    assert passes == [] and pauses == [hub.FAST_EVERY] * 3


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


def test_a_closed_card_leaves_the_open_columns_on_the_fast_read(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing")]), page([]),
                                                    batch(issue(11, "CLOSED", "COMPLETED", [project_item(board, "Closed")]))))
    board.fast_board_pass()
    board.fast_board_pass()
    row = board.board_cache["items"][0]
    assert (row["st"], row["s"], row["cr"]) == ("Closed", "closed", "completed")
    assert _column(board, 11) is None, "a closed card leaves the open columns on the fast read, not only on the full read"


def test_an_archived_card_leaves_the_board(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing")]), page([]),
                                                    batch(issue(11, "OPEN", None, [project_item(board, "Doing", archived=True)]))))
    board.fast_board_pass()
    board.fast_board_pass()
    assert board.board_cache["items"] == []


def test_a_card_removed_from_the_project_leaves_the_board(board, monkeypatch):
    monkeypatch.setattr(board, "_graphql", recorded(page([node(11, status="Doing")]), page([]), batch(issue(11, "OPEN", None, []))))
    board.fast_board_pass()
    board.fast_board_pass()
    assert board.board_cache["items"] == []


def test_a_held_card_missing_from_the_snapshot_is_checked_in_one_batched_read(board, monkeypatch):
    board.board_cache["items"] = [card(11), card(12), card(13)]
    gql = recorded(page([node(11, status="Doing"), node(12, status="Doing"), node(13, status="Doing")]), page([]),
                   batch(issue(11, "CLOSED", "COMPLETED", [project_item(board, "Closed")]),
                         issue(12, "CLOSED", "NOT_PLANNED", [project_item(board, "Abandoned")]),
                         issue(13, "OPEN", None, [project_item(board, "Doing", archived=True)])))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    board.fast_board_pass()
    assert len(gql.calls) == 3, "one snapshot, one batched check for the three cards: no call per card"
    assert all(f"I_{n}" in gql.queries[2] for n in (11, 12, 13))
    assert [i["n"] for i in board.board_cache["items"]] == [11, 12]


def test_an_unreadable_card_keeps_its_row_and_is_retried_in_the_pass(board, monkeypatch):
    gql = recorded(page([node(11, status="Doing")]), page([]), RuntimeError("HTTP 502"),
                   batch(issue(11, "CLOSED", "COMPLETED", [project_item(board, "Closed")])))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    board.fast_board_pass()
    assert len(gql.calls) == 4, "a failed batched answer is retried once within the pass"
    assert board.board_cache["items"][0]["s"] == "closed"


def test_an_unreadable_card_whose_retry_is_unfunded_waits_for_the_next_pass(board, monkeypatch):
    board.FAST_BUDGET = board_fast.Budget(2, clock=lambda: 0)
    gql = recorded(page([node(11, status="Doing")]), page([]), page([]),
                   batch(issue(11, "CLOSED", "COMPLETED", [project_item(board, "Closed")])))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    board.fast_board_pass()
    assert len(gql.calls) == 2, "the batched check is not sent once the hour's budget is spent"
    assert board.board_cache["items"][0]["s"] == "open", "an unreadable card keeps its row"
    board.FAST_BUDGET = board_fast.Budget(600, clock=lambda: 0)
    board.fast_board_pass()
    assert len(gql.calls) == 4 and board.board_cache["items"][0]["s"] == "closed"


def test_an_open_issue_not_on_the_project_is_never_read_or_added(board, monkeypatch):
    gql = recorded(page([node(11, status="Doing")]), page([]), batch(issue(11, "CLOSED", "COMPLETED", [project_item(board, "Closed")])))
    monkeypatch.setattr(board, "_graphql", gql)
    board.fast_board_pass()
    board.fast_board_pass()
    assert "I_13" not in gql.queries[2], "a repository issue the cache never held is not checked"
    assert [i["n"] for i in board.board_cache["items"]] == [11]


def test_the_full_read_runs_every_ten_to_fifteen_minutes(board):
    assert 600 <= board.FULL_EVERY <= 900
    assert board.FAST_EVERY == 15


def test_every_row_the_fast_read_joins_names_its_repository():
    """#1429: a cache reader places a row by its own stamp, so a row the fast read writes without `r` would be read as
    another board's card, or as a cache no build wrote. Every row it joins carries the repository it read."""
    rows = board_fast.rows_of([node(11, status="Doing")], REPO)
    assert [r["r"] for r in rows] == [REPO], "a joined row does not name its repository"
