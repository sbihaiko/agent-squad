"""The mobile view as an installable web app under its profile path (#827).

The manifest, the icon and the service worker are driven through the mux under `/<profile>/mobile/`, so the path rewrite and
the content types are what the browser sees. The worker source is read as text: it caches the app shell and nothing else.
Fixture profiles over temp checkouts; no network, no browser.
"""
import http.client
import importlib.util
import json
import os
import re
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
PREFIX = "/alpha/mobile/"
SHELL = [PREFIX, PREFIX + "manifest.webmanifest", PREFIX + "icon.svg"]


def _profile(d: Path, slug: str, repo: str, checkout: Path) -> None:
    owner = repo.split("/")[0]
    (d / f"{slug}.sh").write_text(
        f': "${{SG_REPO:={repo}}}"; : "${{SG_OWNER:={owner}}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return r.status, r.getheader("Content-Type") or "", body


@pytest.fixture(scope="module")
def mux(tmp_path_factory):
    t = tmp_path_factory.mktemp("webapp")
    prof, base = t / "profiles", t / "state"
    prof.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (t / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, t / f"co-{slug}")
    (base / "alpha").mkdir(parents=True)
    env = {"SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(base)}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("hubmux_webapp", SCRIPTS / "hubmux.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    apps = mod.load_apps()
    srv = mod.server(0, apps)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def sw(mux):
    status, ct, body = get(mux, PREFIX + "sw.js")
    assert status == 200
    return body


def test_manifest_under_profile_path_names_that_path_for_id_start_and_scope(mux):
    status, ct, body = get(mux, PREFIX + "manifest.webmanifest")
    assert status == 200 and ct.split(";")[0] == "application/manifest+json"
    m = json.loads(body)
    assert m["id"] == PREFIX and m["start_url"] == PREFIX and m["scope"] == PREFIX
    assert m["display"] == "standalone"


def test_manifest_icon_is_served_under_the_profile_path(mux):
    m = json.loads(get(mux, PREFIX + "manifest.webmanifest")[2])
    src = m["icons"][0]["src"]
    assert src == PREFIX + "icon.svg"
    status, ct, body = get(mux, src)
    assert status == 200 and ct.split(";")[0] == "image/svg+xml" and body.lstrip().startswith("<svg")


def test_worker_is_served_as_javascript_under_the_profile_path(mux):
    status, ct, body = get(mux, PREFIX + "sw.js")
    assert status == 200
    assert ct.split(";")[0] in ("text/javascript", "application/javascript")


def test_page_links_manifest_and_registers_worker_with_the_profile_scope(mux):
    status, ct, body = get(mux, PREFIX)
    assert status == 200 and ct.startswith("text/html")
    assert f'rel=manifest href="{PREFIX}manifest.webmanifest"' in body
    assert f'"{PREFIX}sw.js"' in body
    assert f'scope: "{PREFIX}"' in body


def test_hub_without_mux_serves_the_same_webapp_paths(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_webapp", SCRIPTS / "hub.py")
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        m = json.loads(get(srv.server_port, "/mobile/manifest.webmanifest")[2])
        assert m["id"] == m["start_url"] == m["scope"] == "/mobile/"
        assert get(srv.server_port, "/mobile/sw.js")[0] == 200
        assert get(srv.server_port, "/mobile/icon.svg")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_worker_caches_exactly_the_app_shell(sw):
    m = re.search(r"const SHELL = (\[.*?\]);", sw, re.S)
    assert m, "the worker names its shell as one literal list"
    assert json.loads(m[1]) == SHELL


def test_worker_answers_only_shell_gets_and_never_routes_or_writes(sw):
    assert "/api/" not in sw
    assert "method" in sw and 'method !== "GET"' in sw
    assert sw.index("SHELL.includes") < sw.index("cache.put")
    assert "cache.add(" not in sw and "cache.addAll(" in sw


def test_reconnect_screen_is_in_the_shell_page(mux):
    body = get(mux, PREFIX)[2]
    assert 'id="reconnect"' in body
    assert 'id="retry"' in body
    assert "The hub does not answer" in body
