"""The phone Board after SPEC 2498 (#2501): Work draws its columns and its cards, and a card is a link to GitHub.

The card sheet, the Work filters and the column swipe left the page with the rewrite. The lane rule and the Work screen
are proven where they live: `tests/test_hub_mobile_lanes.py` runs the swimlane function under node, and
`tests/test_hub_mobile_work_links.py` boots the page and reads its cards, chips and spec headers.

This file keeps the board's page-level rules: the page reads only the routes the hub serves, the gesture module is
inlined and calls no network, the chat stayed off the page, and the mobile page carries one strict content policy.
"""
import http.client
import importlib.util
import os
import pathlib
import re
import tempfile
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
ROUTES = {"/api/status", "/api/hub", "/api/board", "/api/issue", "/api/subs", "/api/models", "/api/stats", "/api/runs", "/api/ended", "/api/version",
          "/api/promote", "/api/loop", "/api/profiler", "/api/pending", "/api/slack-link",
          "/api/load", "/api/chart/lead", "/api/chart/cfd", "/api/leadtime"}
# The board's move is a write (#829); the Monitor reads the desktop card's route (#983); the lead chart and its zoom read
# the routes the desktop draws (#1333); the Cumulative Flow screen reads the flow chart's own route (#2504).


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_board", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_mobile_page_reads_only_the_existing_routes(hub):
    found = set(re.findall(r'"(/api/[^"?]*)', hub.MOBILE_PAGE))
    assert found <= ROUTES, f"the mobile page reads routes the hub does not serve: {sorted(found - ROUTES)}"
    writes = set(re.findall(r'fetch\("(/api/[^"?]*)"[^)]*method:', hub.MOBILE_PAGE))
    assert writes == {"/api/promote", "/api/profiler"}, f"the mobile page writes other routes: {sorted(writes)}"
    assert 'fetch(path, { method: "POST"' in hub.MOBILE_PAGE and 'p && p.own ? "/api/loop" : "/" + loopSlug + "/api/loop"' in hub.MOBILE_PAGE, \
        "the loop page's write is the one project's own loop route (#1599)"
    # Four writes: the owner's move, the run screen's pause or resume, which posts the run's own control route through
    # the hub's forward route (#831), the loop switch (#923, ADR-0162), and the Loop tab's CPU sampling switch, which
    # posts the profiler route (#1754).
    # Each carries the word once: a route
    # reached through a variable path, or a method set after the call's own `)`, carries it once too often, whatever the
    # shape of the call (#829).
    assert 'fetch(runURLs(id).control, { method: "POST"' in hub.MOBILE_PAGE, "the run screen's write is not the run's control route"
    for gone in ('"/api/sessions/send"', '"/api/sessions/attach', '"/api/sessions/voice', '"/api/sessions/card/answer"'):
        assert gone not in hub.MOBILE_PAGE, f"the phone page still names {gone}"
    assert hub.MOBILE_PAGE.count("method:") == 4, "the mobile page holds a fifth write, or a method outside the four fetches"


def test_the_gesture_module_makes_no_network_call_and_imports_nothing():
    text = GESTURE_JS.read_text()
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import "):
        assert banned not in text, f"the gesture module holds {banned!r}"


def test_the_board_rules_are_inlined_not_fetched(hub):
    assert GESTURE_JS.read_text() in hub.MOBILE_PAGE
    assert "/*GESTURE*/" not in hub.MOBILE_PAGE
    assert "<script src=" not in hub.MOBILE_PAGE


def test_the_phone_page_holds_no_composer(hub):
    """The chat, its composer, its photo input and its send left the page with their rule module (#2490)."""
    body = hub.MOBILE_PAGE
    for name in ("termview-text", "termview-send", "termview-file", "termview-attach", "termview-attach-img",
                 "termview-attach-name", "termview-attach-x"):
        assert f'id="{name}"' not in body, f"the phone page still holds #{name}"
    assert not re.search(r"(?<![\w-])type=[\"']?file\b", body), "the phone page still holds a file input"
    assert "/api/sessions/send" not in body, "the phone page still names the send route"
    assert "/api/sessions/attach" not in body, "the phone page still names the attach route"


@pytest.fixture
def served(hub, monkeypatch):
    monkeypatch.setattr(hub, "run_list", lambda: [])
    monkeypatch.setattr(hub, "_loop_on", lambda pid_file=None: False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def test_the_content_security_line_admits_no_preview(served):
    c = http.client.HTTPConnection("127.0.0.1", served, timeout=10)
    c.request("GET", "/mobile/", headers={"Host": f"127.0.0.1:{served}"})
    r = c.getresponse()
    csp = r.getheader("Content-Security-Policy") or ""
    r.read()
    c.close()
    assert r.status == 200
    assert csp == ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self'"), csp
    assert "*" not in csp and "http:" not in csp and "https:" not in csp and "data:" not in csp
