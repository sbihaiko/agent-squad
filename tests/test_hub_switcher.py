"""The project switcher in every hub page header (#876).

Each hub page names its project and opens a switcher. The switcher reads the profile list per request, through `/api/hub`,
so a project added after the hub starts shows, each row carries its live run count and its worst alert as text, the
tailnet rule of each profile decides which rows a login sees, and the menu ends with "All projects". The pages poll the
list every 10 s, with no reload. Driven over HTTP through the mux, as test_hubmux_profiles.py is. No network.
"""
import http.client
import importlib.util
import json
import pathlib
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
MUX_PY = SCRIPTS / "hubmux.py"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
OTHER = "other@example.com"


def request(port, path, *, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return r.status, body


def write_profile(prof, slug, co):
    (prof / f"{slug}.sh").write_text(f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')


def make_checkout(root, name, allow):
    co = root / name
    (co / "docs" / "squad").mkdir(parents=True)
    (co / "docs" / "squad" / "defaults.yaml").write_text(f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps(allow)}\n")
    return co


def switcher(port, headers=None):
    status, body = request(port, "/api/hub", headers=headers)
    assert status == 200, body
    return {r["slug"]: r for r in json.loads(body)["repos"]}


@pytest.fixture
def mux(tmp_path, monkeypatch):
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    co = make_checkout(tmp_path, "co-alpha", [ANA])
    write_profile(prof, "alpha", co)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_switcher", MUX_PY)
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    for app in apps.values():
        app["mod"].runs = lambda: []
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield {"port": srv.server_address[1], "prof": prof, "tmp": tmp_path, "apps": apps}
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.heavy
def test_each_row_carries_its_live_count_and_its_worst_alert_as_text(mux):
    alpha = mux["apps"]["alpha"]["mod"]
    alpha.working = lambda rows: ["r1", "r2"]
    alpha.LOAD_STATE["facts"] = [{"text": "CPU 98%", "bad": True}, {"text": "RAM 40%", "bad": False}]
    rows = switcher(mux["port"])
    assert rows["alpha"]["live"] == ["r1", "r2"]
    assert rows["alpha"]["alert"] == "CPU 98%"


@pytest.mark.heavy
def test_a_row_with_no_alert_says_so_as_an_empty_text(mux):
    alpha = mux["apps"]["alpha"]["mod"]
    alpha.LOAD_STATE["facts"] = []
    assert switcher(mux["port"])["alpha"]["alert"] == ""


@pytest.mark.heavy
def test_a_project_added_after_start_joins_the_switcher_under_its_own_access_rule(mux):
    assert list(switcher(mux["port"], {"Host": TAILNET, "Tailscale-User-Login": ANA})) == ["alpha"]
    co = make_checkout(mux["tmp"], "co-beta", [OTHER])
    write_profile(mux["prof"], "beta", co)
    tn = {"Host": TAILNET, "Tailscale-User-Login": ANA}
    assert list(switcher(mux["port"], tn)) == ["alpha"]   # beta's own allow list does not admit ana
    assert list(switcher(mux["port"])) == ["alpha", "beta"]   # loopback sees every project


@pytest.mark.heavy
def test_a_changed_env_allow_list_takes_effect_on_the_next_request(mux):
    tn = {"Host": TAILNET, "Tailscale-User-Login": ANA}
    assert "alpha" in switcher(mux["port"], tn)
    (mux["tmp"] / "co-alpha" / ".env").write_text(f"SQUAD_TAILNET_HOST={TAILNET}\nSQUAD_TAILNET_ALLOW={OTHER}\n")
    status, body = request(mux["port"], "/api/hub", headers=tn)
    assert status == 403 and "login not allowed" in body


@pytest.mark.heavy
def test_the_desktop_page_has_the_switcher_and_polls_the_list(mux):
    status, body = request(mux["port"], "/alpha/")
    assert status == 200
    assert 'id=psw' in body
    assert "All projects" in body
    assert '"/" + "api/hub"' in body
    assert "setInterval" in body and "10000" in body


@pytest.mark.heavy
def test_the_mobile_page_has_the_switcher_sheet_and_polls_the_list(mux):
    status, body = request(mux["port"], "/alpha/mobile")
    assert status == 200
    assert 'id="psheet"' in body
    assert "All projects" in body
    assert '"/" + "api/hub"' in body
    assert "setInterval(" in body and "10000" in body
