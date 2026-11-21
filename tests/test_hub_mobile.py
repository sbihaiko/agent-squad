"""The hub's mobile page: five tabs, the links to and from the desktop page, the Monitor tab's facts (#826, #983).

The page and the status route are driven over HTTP, through the hub and through the mux under a profile path. The loop
stamp rule is tested as a function, with no server and no page loaded. The page holds no rule code (ADR-0137): a guard
names the board's columns and checks the mobile page holds none of them.
"""
import http.client
import importlib.util
import json
import pathlib
import re
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
MUX_PY = SCRIPTS / "hubmux.py"
GHREAD_PY = SCRIPTS / "ghread.py"
TABS = ["now", "work", "specs", "runs", "spend"]   # five tabs in spec order; the chat tab left with the session controls (Refs #2490, #2499)
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"

# The hub reads the process table through /bin/ps to find the loop, the runs and the sessions it serves.
pytestmark = pytest.mark.process_bound

BUG = {"n": 11, "i": 201, "t": "a bug", "s": "OPEN", "st": "Todo", "l": ["bug"]}
FEATURE = {"n": 12, "i": 202, "t": "a feature", "s": "OPEN", "st": "Todo", "l": ["enhancement"]}


def get(port, path, *, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return r.status, r.getheader("Content-Type") or "", body


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_mobile", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.board_cache = {"t": 1, "items": [dict(BUG), dict(FEATURE)], "prs": [], "err": None, "lc": {}, "errs": [], "cases": []}
    mod.LOOP_STAMP = tmp_path / "loop-stamp.json"
    monkeypatch.setattr(mod, "_loop_on", lambda pid_file=None: True)
    monkeypatch.setattr(mod, "run_list", lambda: [{"id": "r1", "live": True}, {"id": "r2", "live": False}])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def machine_fork(monkeypatch, tmp_path):
    fork = tmp_path / "machine-fork"
    (fork / "runs" / "run-20261028-000000-aaaaaa").mkdir(parents=True)
    monkeypatch.setenv("SG_FORK_DIR", str(fork))
    return fork


def test_the_hub_fixture_reads_no_run_folder_of_the_machine(machine_fork, hub):
    mod, _ = hub
    assert [d for d in mod.project_dirs() if str(d).startswith(str(machine_fork))] == []


def test_mobile_page_names_four_tabs_in_spec_order(hub):
    mod, port = hub
    status, ct, body = get(port, "/mobile")
    assert status == 200 and ct.startswith("text/html")
    assert [t for t in re.findall(r'data-tab="(\w+)"', body)] == TABS


def test_desktop_page_links_to_mobile_and_redirects_narrow_screens(hub):
    mod, port = hub
    _, _, desk = get(port, "/")
    assert 'id=mobile-link' in desk
    assert 'matchMedia("(max-width: 600px)")' in desk
    assert 'localStorage.getItem("squad-view")!=="desktop"' in desk
    assert "location.replace(" in desk


def test_desktop_page_carries_a_viewport_tag(hub):
    mod, port = hub
    _, _, desk = get(port, "/")
    assert 'name=viewport content="width=device-width,initial-scale=1"' in desk


def test_mobile_page_links_back_to_desktop_and_remembers_the_choice(hub):
    mod, port = hub
    _, _, body = get(port, "/mobile")
    assert 'id="to-desktop"' in body
    assert 'localStorage.setItem("squad-view","desktop")' in body


def test_the_mobile_page_carries_no_flow_chart_so_its_filters_have_no_control(hub):
    """#1356: the phone has no Flow view, so the cumulative flow's Type and visible states have no control there; the
    page says why, and no chart element reaches the phone."""
    mod, port = hub
    _, _, body = get(port, "/mobile")
    assert "id=\"flowb\"" not in body and "flowCFD" not in body and "id=cfdy" not in body, \
        "a flow chart or its filter reached the phone without a Flow view"
    assert "no Flow view" in body, "the mobile page does not say why the flow filters have no control"


def test_status_route_serves_since_unknown_without_a_stamp(hub):
    mod, port = hub
    status, ct, body = get(port, "/api/status")
    assert status == 200 and ct.startswith("application/json")
    assert json.loads(body)["loop"] == {"on": True, "since": None}


def test_status_route_serves_the_stamped_time(hub):
    mod, port = hub
    mod.LOOP_STAMP.write_text(json.dumps({"on": True, "since": "2026-10-28T09:00:00-03:00"}))
    assert json.loads(get(port, "/api/status")[2])["loop"] == {"on": True, "since": "2026-10-28T09:00:00-03:00"}


def test_status_route_has_the_five_facts(hub):
    mod, port = hub
    mod.LOAD_STATE["facts"] = [{"text": "codex 90%", "bad": True, "tip": "codex is near its limit"}, {"text": "ok", "bad": False}]
    s = json.loads(get(port, "/api/status")[2])
    assert set(s) >= {"loop", "live", "spend_today", "open_bugs", "warnings", "load"}
    assert s["live"] == 1
    assert s["open_bugs"] == 1
    assert s["warnings"] == [{"text": "codex 90%", "tip": "codex is near its limit"}]


def test_loop_stamp_rule_is_pure(hub):
    mod, _ = hub
    assert mod.loop_stamp_next(None, True, "T") == {"on": True, "since": None}
    assert mod.loop_stamp_next({"on": True, "since": "S"}, True, "T") == {"on": True, "since": "S"}
    assert mod.loop_stamp_next({"on": True, "since": "S"}, False, "T") == {"on": False, "since": "T"}


def test_loop_seen_stamps_each_change_with_the_local_time(hub):
    mod, _ = hub
    import datetime as dt
    first, later = 1793000000.0, 1793003600.0
    mod.loop_seen(True, first)
    assert json.loads(mod.LOOP_STAMP.read_text()) == {"on": True, "since": None}
    mod.loop_seen(False, later)
    assert json.loads(mod.LOOP_STAMP.read_text()) == {
        "on": False, "since": dt.datetime.fromtimestamp(later).astimezone().isoformat(timespec="seconds")}


def test_mobile_page_holds_no_board_rule_code(hub):
    mod, port = hub
    _, _, body = get(port, "/mobile")
    assert "import " not in body
    assert "<script src=" not in body
    for col in mod.BOARD_COLUMNS:
        assert col["name"] not in body


def test_mobile_page_keeps_no_credential(hub):
    mod, port = hub
    _, _, body = get(port, "/mobile")
    assert 'data-tab="now"' in body
    low = body.lower()
    assert "<form" not in low
    assert 'type="password"' not in low and "type=password" not in low
    assert "document.cookie" not in body


def test_mobile_page_opens_the_last_tab_and_switches_profile(hub):
    mod, port = hub
    _, _, body = get(port, "/mobile")
    assert 'getItem("squad-mobile-tab")' in body
    assert 'id="profile"' in body


def test_mobile_page_tells_unreachable_from_refused(hub):
    mod, port = hub
    _, _, body = get(port, "/mobile")
    assert "The hub does not answer" in body
    assert "The hub refused this login" in body


def test_refused_mobile_page_names_the_reason(hub, monkeypatch):
    mod, port = hub
    monkeypatch.setattr(mod, "access", lambda h, method: mod.Access(False, "login not on the allow list", "", True))
    status, ct, body = get(port, "/mobile", host=TAILNET)
    assert status == 403 and ct.startswith("text/html")
    assert "login not on the allow list" in body


@pytest.mark.heavy
def test_mux_serves_the_mobile_page_under_the_profile_path(tmp_path, monkeypatch):
    prof, base, co = tmp_path / "profiles", tmp_path / "state", tmp_path / "co-alpha"
    (co / "docs" / "squad").mkdir(parents=True)
    (co / "docs" / "squad" / "defaults.yaml").write_text(f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps([ANA])}\n")
    prof.mkdir()
    (prof / "alpha.sh").write_text(f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_mobile", MUX_PY)
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    for app in apps.values():
        app["mod"].runs = lambda: []
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        port = srv.server_address[1]
        status, _, body = get(port, "/alpha/mobile")
        assert status == 200 and [t for t in re.findall(r'data-tab="(\w+)"', body)] == TABS
        assert '"/alpha/api/status"' in body
        status, _, s = get(port, "/alpha/api/status")
        assert status == 200 and "loop" in json.loads(s)
    finally:
        srv.shutdown()
        srv.server_close()
