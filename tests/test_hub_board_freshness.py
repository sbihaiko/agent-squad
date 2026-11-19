"""The kanban names the read each age measures, marks an overdue board read, and never draws a closed card in a live lane (#1661).

The header's board age is the item read's own stamp (`board_cache["t"]`), never the browser's fetch time. The sidebar's
history read and the board item read are two facts with two ages: an aged history read never leaks into the board label.
A board item read past FULL_EVERY plus one full pass is marked overdue on the served board and the page. A closed issue
is drawn closed, whatever a stale recorded move says. The sidebar count and the lane count come from one read. A held card
says `running` only while its run has a live engine process (the one reader squad/reap.py uses); a dead holder reads
`held · run <id> ended <time>` in the neutral tone.
"""
import http.client
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = time.time()
RUN = "run-20261113-080000-abc123"


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


@pytest.fixture(scope="module")
def hub():
    # Pinned, not `setdefault`: in one pytest process another module may already have named another repository, and the
    # fast read keeps only this repository's rows, so an inherited SG_REPO would empty the snapshot (#985).
    pinned = {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4",
              "SG_CHECKOUT": tempfile.mkdtemp(), "SG_HUB_DIR": tempfile.mkdtemp()}
    previous = {k: os.environ.get(k) for k in pinned}
    os.environ.update(pinned)
    spec = importlib.util.spec_from_file_location("hub_board_freshness", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    for k, v in previous.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def card(n, status="Todo", **extra):
    """One board row as the pass stores it: `ot` is the instant the read that observed it began."""
    return {"n": n, "t": f"card {n}", "s": "open", "st": status, "l": [], "i": f"ITEM{n}", "ot": int(NOW) - 1000, **extra}


def _node(n, status="Todo"):
    """One open item of the fast read's snapshot for the repository this hub reads."""
    return {"updatedAt": "2026-11-01T10:00:00Z", "isArchived": False,
            "fieldValueByName": {"name": status, "updatedAt": "2026-11-01T10:00:00Z"},
            "content": {"id": f"I_{n}", "number": n, "fullDatabaseId": str(900000 + n), "state": "OPEN", "stateReason": None,
                        "updatedAt": "2026-11-01T10:00:00Z", "repository": {"nameWithOwner": "x/y"},
                        "labels": {"totalCount": 0, "nodes": []}}}


def _snapshot(*nodes):
    """One whole fast-read answer for the user root: a page no further page follows."""
    return {"data": {"rateLimit": {"cost": 1},
                     "user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(nodes)}}}}}


def _fast_read(hub, monkeypatch, *answers):
    """The fast pass wired to recorded snapshots, with its own fresh hour."""
    queue = list(answers)
    monkeypatch.setattr(hub, "_fast_root", lambda: "user")
    monkeypatch.setattr(hub, "FAST_BUDGET", hub.board_fast.Budget(600))
    monkeypatch.setattr(hub, "_graphql", lambda query, variables, timeout=None: queue.pop(0))


def _cache(hub, monkeypatch, items, t=None, moves=None):
    monkeypatch.setattr(hub, "board_cache", {"t": NOW if t is None else t, "items": items, "prs": [], "lc": {}, "err": None,
                                             "errs": [], "cases": [], "parts": {}, "lt": NOW})
    monkeypatch.setattr(hub, "read_moves", lambda: dict(moves or {}))


def _row(data, n):
    return next(x for g in data["groups"] for x in g["items"] if x["n"] == n)


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _run_folder(tmp_path, run_id, pid, started, moved):
    """A run folder whose last invocation names one engine process, as the record writes it."""
    runs = tmp_path / "runs"
    folder = runs / run_id
    folder.mkdir(parents=True)
    rec = folder / "record.jsonl"
    rec.write_text(json.dumps({"event": "run_start", "run_id": run_id, "ts": started}) + "\n"
                   + json.dumps({"event": "call_start", "pid": pid, "started_at": started, "ts": started}) + "\n")
    os.utime(rec, (moved, moved))
    return runs


# --- AC1: every age names its own read --------------------------------------------------------------------------

def test_a_history_read_aged_alone_never_ages_the_board_label(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(1)], t=NOW - 5)
    store = {"h": {"t": NOW - 8460, "events": []}}   # the timeline read, 141 minutes old
    monkeypatch.setattr(hub._hist, "load_store", lambda p: store)
    fact = hub.throughput_fact(now=NOW)
    assert fact["board"]["age_secs"] < 60, "the board item read is 5 s old: a history read cannot age it"
    assert fact["history"]["age_secs"] > 8000


def test_the_page_names_each_age_by_its_read(hub):
    page = hub.PAGE
    assert "board items read" in page, "the header names the board item read"
    assert "· read ${Math.floor(sp.age_secs" not in page, "the sidebar names no bare `read`: it names the history or the board read"
    assert "history read" in page


# --- AC2: the board item read is never printed as normal past its bound -----------------------------------------

def test_a_board_read_past_the_bound_is_marked_overdue(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(1)], t=NOW - (hub.FULL_EVERY + hub.FULL_DEADLINE + 60))
    assert hub.board()["overdue"] is True
    assert hub.throughput_fact(now=NOW)["board"]["overdue"] is True


def test_a_board_read_inside_the_bound_is_not_overdue(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(1)], t=NOW - 60)
    assert hub.board()["overdue"] is False


def test_the_page_draws_an_overdue_board_read_as_stale(hub):
    assert "b.overdue" in hub.PAGE, "the page reads the served overdue mark, never prints the age as normal"


def test_the_served_board_carries_the_overdue_mark_over_http(hub, monkeypatch):
    """The HTTP route serves the same mark: the board is read through the handler the page uses."""
    _cache(hub, monkeypatch, [card(1)], t=NOW - (hub.FULL_EVERY + hub.FULL_DEADLINE + 60))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
        conn.request("GET", "/api/board")
        body = json.loads(conn.getresponse().read())
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert body["overdue"] is True


# --- AC3: the closure wins over a stale recorded move -----------------------------------------------------------

def test_a_closed_card_is_never_drawn_in_a_live_lane_from_a_stale_move(hub, monkeypatch):
    # The latest read (began at NOW-100) closed the card; the loop's move to In Progress (NOW-500) is older than that read.
    items = [card(837, s="closed", c=iso(NOW - 60), ot=int(NOW) - 100, i="ITEM837")]
    moves = {"ITEM837": {"column": "In Progress", "at": iso(NOW - 500), "by": "loop"}}
    _cache(hub, monkeypatch, items, moves=moves)
    data = hub.board(dict(moves))
    live = [x["n"] for g in data["groups"] if not g["end"] for x in g["items"]]
    assert 837 not in live
    closed = next(g for g in data["groups"] if g["name"] == hub.COLUMN["issue_closed"])
    assert closed["n"] == 1


# --- AC4: the sidebar count and the lane count come from one read ------------------------------------------------

def test_the_sidebar_count_and_the_lane_count_agree_after_a_closure(hub, monkeypatch):
    items = [card(837, s="closed", c=iso(NOW - 60), l=["bug"], ot=int(NOW) - 100, i="ITEM837"),
             card(892, l=["bug"], ot=int(NOW) - 100, i="ITEM892")]
    moves = {"ITEM837": {"column": "Todo", "at": iso(NOW - 500), "by": "loop"}}
    _cache(hub, monkeypatch, items, moves=moves)
    bugs = hub.arrivals_fact(items, NOW, 4 * 3600, dict(moves))["bugs"]
    sidebar_open = bugs["total"] - bugs["closed"]
    data = hub.board(dict(moves))
    lane_open = sum(1 for g in data["groups"] if not g["end"] for x in g["items"] if "bug" in x["l"])
    assert sidebar_open == lane_open == 1


# --- AC5: the holder chip says running only for a live run ------------------------------------------------------

def test_a_held_card_whose_run_is_dead_reads_held_and_ended(hub, monkeypatch, tmp_path):
    runs = _run_folder(tmp_path, RUN, _dead_pid(), NOW - 600, NOW - 300)
    monkeypatch.setattr(hub, "own_roots", lambda: {runs})
    items = [card(241, st="In Progress", h={"run": RUN, "started": "2026-11-13T08:00:00Z"})]
    _cache(hub, monkeypatch, items)
    row = _row(hub.board(), 241)
    assert row["ho"].startswith(f"held · run {RUN} ended "), row["ho"]
    assert row["hs"] == "ended", "a dead run is not running, whatever the Project's Run field carries"


def test_a_held_card_whose_run_is_live_reads_running(hub, monkeypatch, tmp_path):
    runs = _run_folder(tmp_path, RUN, os.getpid(), NOW, NOW)
    # The chip's own rule is under test, not the host's process table: `run_live` answers live on a Linux runner and dead on
    # a macOS one for the same fixture, because it compares the pid's real start with the record's. The stub fixes the fact,
    # and `tests/test_reap.py` proves the predicate it stands for.
    real = hub.reaper()

    class _Live:
        NO_PID_IDLE_S = real.NO_PID_IDLE_S

        def run_live(self, folder, now=None):
            return True
    monkeypatch.setattr(hub, "reaper", lambda: _Live())
    monkeypatch.setattr(hub, "own_roots", lambda: {runs})
    items = [card(241, st="In Progress", h={"run": RUN, "started": "2026-11-13T08:00:00Z"})]
    _cache(hub, monkeypatch, items)
    row = _row(hub.board(), 241)
    assert row["ho"].startswith(f"held by {RUN} since "), row["ho"]
    assert row["hs"] == "running"


def test_the_card_chip_says_running_only_for_a_live_holder(hub):
    line = re.search(r"^const cards=.*$", hub.PAGE, re.M).group(0)
    script = (ROOT.joinpath("contrib", "squad-goal", "scripts", "board-lanes.js").read_text() + "const SLOTS=specSlots([]);\n"
              'const esc=s=>String(s);const isPR=n=>false;const colOf=()=>"#000000";const tint=()=>"";'
              'const lbc=()=>"";const gstrip=()=>"";const age=()=>"";const ageC=()=>"";const bo={labels:[]};const CN=["Todo"];\n'
              + line + '\nconst I=[{n:241,t:"dead",l:[],ho:"held · run r ended 2026-11-13 08:59 UTC",hs:"ended"},'
              '{n:242,t:"live",l:[],ho:"held by r since then",hs:"running"}];console.log(cards({name:"Todo"},I,I));\n')
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    # The chip names the holder on every card (#1664) and says `running` only while the run's process lives (#1661);
    # the full holder text is its tooltip.
    assert '<span class=hld title="held · run r ended 2026-11-13 08:59 UTC">Squad</span>' in out.stdout
    assert '<span class=hld title="held by r since then">Squad · running</span>' in out.stdout


# --- AC6: a card the cache has seen but not read is named, never silently missing ---------------------------------

def test_the_board_counts_a_card_it_has_seen_but_not_read(hub, monkeypatch):
    """The fast read sees a card the cache does not hold: the served board counts it, so the page never shows a board
    that silently lacks a card just added to the project (#985)."""
    _cache(hub, monkeypatch, [card(11, fd=900011)])
    _fast_read(hub, monkeypatch, _snapshot(_node(11), _node(12)))
    hub.fast_board_pass()
    assert hub.board()["pending_new"] == 1, "a card the fast read saw and the cache does not hold is counted"


def test_the_count_falls_to_zero_once_the_cache_holds_the_card(hub, monkeypatch):
    _cache(hub, monkeypatch, [card(11, fd=900011)])
    _fast_read(hub, monkeypatch, _snapshot(_node(11), _node(12)), _snapshot(_node(11), _node(12)))
    hub.fast_board_pass()
    assert hub.board()["pending_new"] == 1
    hub.board_cache["items"].append(card(12, fd=900012))   # the full read writes the card the fast read saw
    hub.fast_board_pass()
    assert hub.board()["pending_new"] == 0, "a card the cache now holds waits for no read"


def test_the_bread_line_names_the_cards_seen_but_not_read_in_the_stale_style(hub):
    """The honest bound is on the page: a card the fast snapshot carries and the cache does not hold is not drawn, and
    the header says so in the stale style, as an old read does."""
    assert "seen, not read yet" in hub.PAGE, "the bread line names the cards the fast read has seen but the cache has not read"
    # The interval runs with `b` bound to the #bread element, so the count must be kept in its own variable by the poll
    # handler: a read of `b.pending_new` there is always undefined and the line would never name a card (#985).
    assert "lastPending=b.pending_new||0" in hub.PAGE, "the poll keeps the served count for the bread line"
    line = next(ln for ln in hub.PAGE.splitlines() if "seen, not read yet" in ln)
    assert "lastPending" in line and "b.pending_new" not in line, \
        "the bread line reads the kept count, never the DOM element, and marks the bread stale as an old read does"
