"""One kanban card syncs from GitHub at press time (#1668).

A press on a card's Sync control posts that card's number to `/api/sync`. The hub re-reads that one card from GitHub now
-- its column, labels, holder, blocking edges and sub-issue count -- and answers the rows it refreshed, their count, the
instant of the read, and an error when the read failed. A press never writes to GitHub: no move, no close, no edit. A
failed read answers its error and keeps the row the page already had. A card GitHub no longer carries keeps its last good
row and is named. The control is served in the page itself, and a press disables its button until the read answers.
"""
import http.client
import importlib.util
import json
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = time.time()


@pytest.fixture(scope="module")
def hub():
    """The hub module, imported once with a temp checkout and state of its own."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SG_REPO", "x/y")
        mp.setenv("SG_OWNER", "x")
        mp.setenv("SG_PROJECT", "4")
        mp.setenv("SG_CHECKOUT", tempfile.mkdtemp())
        mp.setenv("SG_HUB_DIR", tempfile.mkdtemp())
        mp.setenv("SG_STATE", tempfile.mkdtemp())
        mp.delenv("SG_PROFILE", raising=False)
        spec = importlib.util.spec_from_file_location("hub_card_sync", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


def card(n, status="Todo", **extra):
    """One board row as the pass stores it."""
    return {"n": n, "t": f"card {n}", "s": "open", "st": status, "l": [], "i": f"ITEM{n}", "ot": int(NOW) - 1000, **extra}


def fresh(n, **extra):
    """One card as the one-card reader answers it: the facts GitHub holds now."""
    return {"n": n, "r": "x/y", "t": f"card {n}", "s": "open", "cr": None, "c": None, "st": "In Progress",
            "l": ["bug"], "i": f"ITEM{n}", "si": {"total": 3, "completed": 1}, "b": [9],
            "o": "2026-11-01T00:00:00Z", "h": {"run": "run-x", "started": "2026-11-13T08:00:00Z"}, **extra}


def _cache(hub, monkeypatch, items):
    monkeypatch.setattr(hub, "board_cache", {"t": NOW - 60, "items": items, "prs": [], "lc": {}, "err": None,
                                             "errs": [], "cases": [], "parts": {}, "lt": NOW})
    monkeypatch.setattr(hub, "read_moves", lambda: {})
    monkeypatch.setattr(hub, "own_roots", lambda: set())


def _post(hub, path, body):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
        conn.request("POST", path, body=json.dumps(body),
                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{srv.server_port}"})
        r = conn.getresponse()
        answer = json.loads(r.read())
        conn.close()
        return r.status, answer
    finally:
        srv.shutdown()
        srv.server_close()


# --- AC1: the answer names the refreshed rows, their count, the read instant and the error -------------------------

def test_a_press_answers_the_refreshed_rows_their_count_and_the_instant(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5), card(6)])
    monkeypatch.setattr(hub, "read_one_card", lambda n: fresh(n))
    out = hub.sync_card(5)
    assert out["n"] == 5
    assert out["err"] is None
    assert out["count"] == 1
    assert [r["n"] for r in out["rows"]] == [5]
    assert out["at"], "the answer names the instant of the read"


def test_the_route_answers_a_press_over_http(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5)])
    monkeypatch.setattr(hub, "read_one_card", lambda n: fresh(n))
    status, body = _post(hub, "/api/sync", {"n": 5})
    assert status == 200
    assert body["count"] == 1
    assert body["rows"][0]["n"] == 5
    assert body["at"]


# --- AC2: the refreshed row draws the new column, labels, holder, edges and sub-issue count -----------------------

def test_the_refreshed_row_carries_the_new_column_labels_holder_edges_and_count(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5), card(6)])
    monkeypatch.setattr(hub, "read_one_card", lambda n: fresh(n))
    row = hub.sync_card(5)["rows"][0]
    assert row["st"] == "Doing", "the row draws the column read at press time (Status In Progress draws Doing)"
    assert row["l"] == ["bug"]
    assert row["b"] == [9], "the open blocking edges the read carried"
    assert row["sc"] == "1/3", "completed over total sub-issues"
    assert row["ho"].startswith("held · run run-x")


# --- AC3: the row carries its own read instant -------------------------------------------------------------------

def test_the_refreshed_row_carries_its_own_read_instant(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5), card(6)])
    monkeypatch.setattr(hub, "read_one_card", lambda n: fresh(n))
    out = hub.sync_card(5)
    assert out["rows"][0]["ri"] == out["at"], "the row names the instant of its own read"


def test_the_page_gives_the_synced_row_its_own_age_and_never_ages_the_board(hub):
    line = next((ln for ln in hub.PAGE.splitlines() if 'closest("#bbody .syncb")' in ln), "")
    assert line, "the page serves a Sync control on a card"
    assert "lastBoardT" not in line, "a synced row never claims the whole board was read at that instant"
    assert "data-ri" in hub.PAGE, "the row wears the instant of its own read as its age"


# --- AC4: a failed read keeps the row and names the error --------------------------------------------------------

def test_a_failed_read_keeps_the_row_and_names_the_error(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5, status="Todo")])

    def boom(n):
        raise RuntimeError("github timed out")

    monkeypatch.setattr(hub, "read_one_card", boom)
    out = hub.sync_card(5)
    assert out["err"] and "timed out" in out["err"]
    assert out["count"] == 0 and out["rows"] == []
    assert hub.board_cache["items"][0]["st"] == "Todo", "a failed read empties nothing"


# --- AC5: a card GitHub no longer carries keeps its last good row and is named -----------------------------------

def test_a_card_github_no_longer_carries_keeps_its_last_good_row_and_is_named(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5, status="In Progress")])

    def gone(n):
        raise hub.SyncMissing(f"GitHub no longer carries #{n}")

    monkeypatch.setattr(hub, "read_one_card", gone)
    out = hub.sync_card(5)
    assert out["missing"] is True
    assert out["count"] == 0
    assert [r["n"] for r in out["rows"]] == [5], "the last good row stays"
    assert "no longer carries" in (out.get("note") or "")
    assert hub.board_cache["items"][0]["n"] == 5, "the cache keeps the row it held"


# --- AC6: a press issues no write to GitHub ----------------------------------------------------------------------

def test_a_press_issues_no_write_to_github(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(5)])
    reads, writes = [], []
    answer = {"number": 5, "title": "card 5", "state": "open", "labels": ["bug"], "created_at": "2026-11-01T00:00:00Z",
              "blocked_by_read": True, "blockers": [{"number": 9, "state": "OPEN"}],
              "sub_issues_summary": {"total": 3, "completed": 1}}
    monkeypatch.setattr(hub.ghread, "read",
                        lambda route, *a, **k: reads.append(route) or hub.ghread.Read(kind=hub.ghread.ANSWER, answer=answer))
    monkeypatch.setattr(hub.ghread, "write", lambda *a, **k: writes.append(a))

    class Seam:
        def read_one(self, n):
            return "In Progress"

        def holder_one(self, n):
            return {}

        def __getattr__(self, name):
            def write(*a, **k):
                writes.append((name, a))
                raise AssertionError(f"a sync sent {name} to GitHub")
            return write

    monkeypatch.setattr(hub, "board_seam", lambda: Seam())
    hub.sync_card(5)
    assert reads == ["repos/x/y/issues/5"], "the press reads that one card from GitHub"
    assert not writes, f"a press never writes to GitHub: {writes}"


# --- AC7: the button is disabled while its press runs -------------------------------------------------------------

def test_the_button_is_disabled_while_its_press_runs(hub):
    line = next((ln for ln in hub.PAGE.splitlines() if 'closest("#bbody .syncb")' in ln), "")
    assert line, "the page serves a Sync control on a card"
    assert "disabled=true" in line
    assert line.index("disabled=true") < line.index("syncOne("), "the button is disabled before the read starts"


# --- AC8: the control is served in the page, with no second route -------------------------------------------------

def test_the_control_is_served_in_the_page_with_one_route(hub):
    assert 'fetch("/api/sync"' in hub.PAGE, "the control posts from the served page"
    assert hub.PAGE.count('fetch("/api/sync"') == 1, "one read route serves the control, and no second route does"
    assert "class=syncb" in hub.PAGE, "each card carries the control"


def test_the_read_stores_label_names_and_never_the_reads_own_objects(hub, monkeypatch):
    """#1682: `ghread` answers REST-shaped labels (`[{"name": ...}]`). Storing that list verbatim made every later board
    draw raise `unhashable type: 'dict'`, so the press answered `bad request` and the card stayed poisoned: the cache
    carries names, as every other reader of the same answer does."""
    class Answer:
        is_answer = True
        answer = {"number": 7, "title": "a card", "state": "open", "created_at": "2026-11-01T10:00:00Z",
                  "labels": [{"name": "bug"}, {"name": "ready-for-agent"}], "blocked_by_read": False}
        def line(self): return ""
    class Seam:
        def read_one(self, n): return "Todo"
        def holder_one(self, n): return None
    monkeypatch.setattr(hub.ghread, "read", lambda route: Answer())
    monkeypatch.setattr(hub, "board_seam", lambda: Seam())
    monkeypatch.setattr(hub, "board_cache", {"items": []})
    item = hub.read_one_card(7)
    assert item["l"] == ["bug", "ready-for-agent"], item["l"]
    assert hub._columns_module.view_column(item["st"], False, item["l"], None)
