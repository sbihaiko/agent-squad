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
import shutil
import subprocess
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


# --- #1685: the pressed row draws at its own scope ----------------------------------------------------------------

SYNC_STUB = """
const noop=()=>{};
global.mk=()=>({innerHTML:"",textContent:"",dataset:{},classList:{contains:()=>false},closest:()=>null,
  querySelector:()=>null,appendChild:noop,insertAdjacentHTML:noop,remove:noop});
global.document={querySelector:()=>null,createElement:()=>global.mk()};
global.CSS={escape:s=>s};
const esc=s=>String(s==null?"":s), age=()=>"now", $=()=>null, cur=null, pollBoard=noop;
const bo={labels:[],lc:{}}, syncInstant=d=>String((d&&d.at)||"");
"""

SYNC_CALL = """
const el=global.mk();
syncRow(el,{n:1,t:"a card",col:"Todo",labels:["bug"],b:[],bl:[],prf:[],ho:"",hc:"",hs:""});
console.log("ROW-OK");
"""


def test_a_pressed_row_draws_a_labelled_card_at_its_own_scope(hub):
    """#1685: `syncRow` called `lbc`, a name the draw function's own scope owns, so a press on a card that carried a label
    threw after the read had answered, and the page reported that throw as "the hub did not answer". The helpers the page
    serves run here against a labelled row, exactly as the browser runs them."""
    assert shutil.which("node"), "node is not installed: the shipped page cannot be run, and this guard never skips"
    page = hub.PAGE
    start = page.index("const syncFacts=")
    end = page.index('document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .syncb")')
    assert start < end, "the served page carries the sync helpers as one block"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(SYNC_STUB + page[start:end] + SYNC_CALL)
        path = f.name
    try:
        out = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert "ROW-OK" in out.stdout, f"a pressed row threw: {out.stdout[-300:]}{out.stderr[-500:]}"

# --- #1670: a spec press refreshes the spec and every sub-issue under it ------------------------------------------

def _tree(hub, monkeypatch, children, reads=None):
    """A tree as GitHub answers it: `children` maps an issue to its sub-issue numbers. The board holds every card named."""
    hub._sync_frontier.clear()
    names = set(children) | {c for subs in children.values() for c in subs}
    _cache(hub, monkeypatch, [card(x) for x in sorted(names)])

    def one(num):
        if reads is not None:
            reads.append(num)
        subs = children.get(num, [])
        return {**fresh(num), "l": ["spec"] if subs else ["bug"], "si": {"total": len(subs), "completed": 0}}

    monkeypatch.setattr(hub, "read_one_card", one)
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: ([{"number": c} for c in children.get(issue, [])], None))


def test_a_spec_press_refreshes_the_spec_and_every_sub_issue_recursively(hub, monkeypatch):
    reads = []
    _tree(hub, monkeypatch, {5: [6, 7], 7: [8]}, reads)
    out = hub.sync_card(5)
    assert sorted(reads) == [5, 6, 7, 8], "the spec, its sub-issues and their sub-issues are read"
    assert out["count"] == 4 and sorted(r["n"] for r in out["rows"]) == [5, 6, 7, 8]
    assert out["capped"] is None


def test_a_spec_press_reads_a_hand_attached_child_the_relation_names(hub, monkeypatch):
    reads = []
    _tree(hub, monkeypatch, {5: [9]}, reads)
    out = hub.sync_card(5)
    assert 9 in reads, "a child attached by hand is in the sub-issue relation, so it is read"
    assert out["count"] == 2


def test_a_plain_press_reads_only_that_card(hub, monkeypatch):
    reads, asked = [], []
    _cache(hub, monkeypatch, [card(5), card(6)])
    monkeypatch.setattr(hub, "read_one_card", lambda n: reads.append(n) or {**fresh(n), "l": ["bug"], "si": {"total": 2, "completed": 0}})
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: asked.append(issue) or ([{"number": 6}], None))
    out = hub.sync_card(5)
    assert reads == [5] and asked == [], "a plain card's sub-issues are not read"
    assert out["count"] == 1 and out["capped"] is None


def test_a_tree_past_the_depth_bound_says_so_and_the_next_press_continues(hub, monkeypatch):
    monkeypatch.setattr(hub, "SYNC_TREE_DEPTH", 1)
    _tree(hub, monkeypatch, {5: [6], 6: [7], 7: [8]})
    first = hub.sync_card(5)
    assert first["capped"] == "depth"
    assert sorted(r["n"] for r in first["rows"]) == [5, 6]
    second = hub.sync_card(5)
    assert 7 in [r["n"] for r in second["rows"]], "the next press reads past the bound"
    assert 8 not in [r["n"] for r in second["rows"]], "one press still stops at its own bound"


def test_a_tree_past_the_node_bound_says_so_and_the_next_press_continues(hub, monkeypatch):
    monkeypatch.setattr(hub, "SYNC_TREE_NODES", 3)
    _tree(hub, monkeypatch, {5: [6, 7, 8, 9, 10]})
    first = hub.sync_card(5)
    assert first["capped"] == "nodes" and first["count"] == 3
    second = hub.sync_card(5)
    assert sorted(r["n"] for r in second["rows"]) == [5, 8, 9], "the next press continues where the first stopped"
    assert second["capped"] == "nodes"


def test_the_page_names_the_cap_a_spec_press_passed(hub):
    assert "press again to continue" in hub.PAGE, "the sync line says a capped tree continues on the next press"


def test_a_wide_tree_stays_under_its_node_cap(hub, monkeypatch):
    reads = []
    _tree(hub, monkeypatch, {5: list(range(100, 300))}, reads)
    out = hub.sync_card(5)
    assert len(reads) <= hub.SYNC_TREE_NODES, "a wide tree is read no further than the node cap"
    assert out["capped"] == "nodes"


def test_a_wide_tree_stays_under_its_deadline(hub, monkeypatch):
    reads = []
    _tree(hub, monkeypatch, {5: list(range(100, 300))}, reads)
    monkeypatch.setattr(hub, "SYNC_TREE_SECONDS", 0.2)
    base = hub.read_one_card

    def slow(n):
        time.sleep(0.02)
        return base(n)

    monkeypatch.setattr(hub, "read_one_card", slow)
    started = time.monotonic()
    out = hub.sync_card(5)
    elapsed = time.monotonic() - started
    assert out["capped"] == "deadline"
    assert len(reads) <= 11, "the reads stop once the deadline has passed, one read past it at most"
    assert elapsed < 1.0, "the press answers near its deadline, not after the whole tree"


# --- #2495: a press writes the parent link its walk knows -----------------------------------------------------------

def test_a_spec_press_writes_the_spec_as_pa_on_each_sub_issue_row(hub, monkeypatch):
    _tree(hub, monkeypatch, {5: [6, 7]})
    out = hub.sync_card(5)
    assert {r["n"]: r.get("pa") for r in out["rows"] if r["n"] != 5} == {6: 5, 7: 5}, \
        "each sub-issue row a spec press reads names the spec as its parent"


def test_a_ticket_press_writes_pau_when_no_read_names_its_parent(hub, monkeypatch):
    _tree(hub, monkeypatch, {5: []})
    hub.sync_card(5)
    item = next(i for i in hub.board_cache["items"] if i["n"] == 5)
    assert item.get("pau") is True and "pa" not in item, "a ticket with no parent read cached is marked pau"


def test_a_ticket_press_keeps_the_pa_the_parent_read_named(hub, monkeypatch):
    _tree(hub, monkeypatch, {5: []})
    hub.board_cache["items"] = [{**i, "pa": 9} if i["n"] == 5 else i for i in hub.board_cache["items"]]
    hub.sync_card(5)
    item = next(i for i in hub.board_cache["items"] if i["n"] == 5)
    assert item.get("pa") == 9 and "pau" not in item, "a ticket press keeps the spec the parent read named"


def test_a_sub_issue_row_a_press_wrote_is_not_promotable(hub, monkeypatch):
    _tree(hub, monkeypatch, {5: [6]})

    def backlog(num):
        return {**{k: v for k, v in fresh(num).items() if k != "h"}, "st": "Backlog", "l": ["spec"] if num == 5 else []}

    monkeypatch.setattr(hub, "read_one_card", backlog)
    hub.sync_card(5)
    item = next(i for i in hub.board_cache["items"] if i["n"] == 6)
    assert hub.promotable(item) is False, "a sub-issue a press wrote is held by its spec, never promotable alone"
