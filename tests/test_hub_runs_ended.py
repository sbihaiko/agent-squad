"""The mobile Runs tab's ended runs: today's list from the run folders, and the resolver that opens one read-only (#830)."""
import datetime
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GHREAD_PY = SCRIPTS / "ghread.py"
TODAY = datetime.date.today()
NOON_TODAY = datetime.datetime.combine(TODAY, datetime.time(12, 0)).timestamp()
EARLY_TODAY = datetime.datetime.combine(TODAY, datetime.time(0, 30)).timestamp()
LAST_NIGHT = datetime.datetime.combine(TODAY - datetime.timedelta(days=1), datetime.time(23, 59)).timestamp()


def write_run(runs, name, t_start, t_end=None):
    folder = runs / name
    folder.mkdir(parents=True)
    events = [{"ts": t_start, "event": "run_start", "pos": 1}]
    if t_end is not None:
        events.append({"ts": t_end, "event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0, "pos": 2})
    (folder / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return folder


def get(port, path):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read().decode()
    headers = dict(r.getheaders())
    c.close()
    return r.status, headers, body


@pytest.fixture
def runs(tmp_path):
    folder = tmp_path / "runs"
    folder.mkdir()
    return folder


@pytest.fixture
def hub(monkeypatch, tmp_path, runs):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_ended", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "project_dirs", lambda: [runs])
    monkeypatch.setattr(mod, "refresh_runs", lambda: [])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def dashboards(monkeypatch, hub):
    mod, _ = hub
    calls = []

    def fake_start(folder):
        calls.append(folder)
        return 4321

    monkeypatch.setattr(mod, "_start_dashboard", fake_start)
    return calls


def test_ended_today_lists_a_run_that_ended_on_the_local_day_with_its_end_time(hub, runs):
    mod, _ = hub
    write_run(runs, "run-today", NOON_TODAY - 3600, NOON_TODAY)
    [row] = mod.ended_today()
    assert row["id"] == "run-today"
    assert row["ended_at"] == NOON_TODAY


def test_each_row_carries_name_state_age_and_spend(hub, runs):
    mod, _ = hub
    write_run(runs, "run-today", NOON_TODAY - 3600, NOON_TODAY)
    [row] = mod.ended_today()
    assert {"label", "state", "badge", "age", "spend"} <= row.keys()
    assert row["age"] >= 0


def test_a_run_that_ended_yesterday_is_not_listed(hub, runs):
    mod, _ = hub
    write_run(runs, "run-old", LAST_NIGHT - 3600, LAST_NIGHT)
    assert mod.ended_today() == []


def test_the_day_is_the_machine_local_day_so_a_run_ending_just_after_midnight_is_listed(hub, runs):
    mod, _ = hub
    write_run(runs, "run-early", EARLY_TODAY - 600, EARLY_TODAY)
    assert [r["id"] for r in mod.ended_today()] == ["run-early"]


def test_a_run_with_no_verdict_is_not_listed_as_ended(hub, runs):
    mod, _ = hub
    write_run(runs, "run-live", NOON_TODAY - 60)
    assert mod.ended_today() == []


def test_the_api_serves_today_ended_runs_as_json(hub, runs):
    _, port = hub
    write_run(runs, "run-today", NOON_TODAY - 3600, NOON_TODAY)
    write_run(runs, "run-old", LAST_NIGHT - 3600, LAST_NIGHT)
    status, headers, body = get(port, "/api/ended")
    assert status == 200 and "json" in headers["Content-Type"]
    assert [r["id"] for r in json.loads(body)] == ["run-today"]


def test_the_resolver_opens_an_ended_run_of_today_on_its_dashboard_and_redirects(hub, runs, dashboards):
    _, port = hub
    folder = write_run(runs, "run-today", NOON_TODAY - 3600, NOON_TODAY)
    status, headers, _ = get(port, "/ended/run-today")
    assert status == 302
    assert headers["Location"] == "/dash/run-today/"
    assert dashboards == [str(folder)]


def test_the_resolver_refuses_a_run_that_did_not_end_today(hub, runs, dashboards):
    _, port = hub
    write_run(runs, "run-old", LAST_NIGHT - 3600, LAST_NIGHT)
    status, _, _ = get(port, "/ended/run-old")
    assert status == 404
    assert dashboards == []


def test_the_resolver_refuses_a_run_with_no_verdict(hub, runs, dashboards):
    _, port = hub
    write_run(runs, "run-live", NOON_TODAY - 60)
    status, _, _ = get(port, "/ended/run-live")
    assert status == 404
    assert dashboards == []


def test_the_resolver_filters_the_name_as_the_held_route_does(hub, runs, dashboards):
    _, port = hub
    write_run(runs, "run-today", NOON_TODAY - 3600, NOON_TODAY)
    status, _, _ = get(port, "/ended/..%2Frun-today")
    assert status == 404
    assert dashboards == []
