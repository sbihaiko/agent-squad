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
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_freshness", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def card(n, status="Todo", **extra):
    """One board row as the pass stores it: `ot` is the instant the read that observed it began."""
    return {"n": n, "t": f"card {n}", "s": "open", "st": status, "l": [], "i": f"ITEM{n}", "ot": int(NOW) - 1000, **extra}


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
    sidebar_open = hub.arrivals_fact(items, NOW, 4 * 3600, dict(moves))["bugs"]["open"]
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
    assert '<span class=hld title="held · run r ended 2026-11-13 08:59 UTC">held · run r ended 2026-11-13 08:59 UTC</span>' in out.stdout
    assert '<span class=hld title="held by r since then">running</span>' in out.stdout
