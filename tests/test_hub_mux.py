"""One hub, one loopback port, one path per repository (#20).

The seam is HTTP: `hubmux.py` serves every profile the machine knows under `/<profile>`, the root page lists the
repositories and their live workers, and a path no profile owns answers 404 with that list. `hubctl.sh` takes a port as
proof only when the squad hub answers on it. Fixture profiles over temp checkouts; no network, no refresh threads.
"""
import http.server
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"


def _profile(d: Path, slug: str, repo: str, checkout: Path) -> None:
    owner = repo.split("/")[0]
    (d / f"{slug}.sh").write_text(
        f': "${{SG_REPO:={repo}}}"; : "${{SG_OWNER:={owner}}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    t = tmp_path_factory.mktemp("fleet")
    prof, base = t / "profiles", t / "state"
    prof.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (t / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, t / f"co-{slug}")
    (base / "alpha").mkdir(parents=True)
    (base / "alpha" / "active-runs.txt").write_text(f"{t}/co-alpha/runs/run-alpha-7\n")
    env = {"SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(base)}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("hubmux_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    srv = mux.server(0, mux.load_apps())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{srv.server_address[1]}", "port": srv.server_address[1], "env": env, "mux": mux}
    srv.shutdown()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_root_lists_every_repository_and_its_live_workers(fleet):
    code, body = _get(fleet["url"] + "/")
    assert code == 200
    assert 'href="/alpha"' in body and 'href="/beta"' in body
    assert "x/alpha" in body and "y/beta" in body
    assert "run-alpha-7" in body


def test_each_path_serves_its_own_repository(fleet):
    for slug, repo, other in (("alpha", "x/alpha", "y/beta"), ("beta", "y/beta", "x/alpha")):
        code, body = _get(f"{fleet['url']}/{slug}")
        assert code == 200
        assert repo in body and other not in body


def test_a_repository_page_calls_its_own_api(fleet):
    _, body = _get(fleet["url"] + "/alpha")
    assert '"/alpha/api/' in body
    assert '"/api/' not in body
    code, ver = _get(fleet["url"] + "/alpha/api/version")
    assert code == 200 and "v" in json.loads(ver)


def test_an_unknown_path_answers_404_with_the_list(fleet):
    code, body = _get(fleet["url"] + "/gamma")
    assert code == 404
    assert 'href="/alpha"' in body and 'href="/beta"' in body


def test_the_hub_names_itself_and_its_repositories(fleet):
    code, body = _get(fleet["url"] + "/api/hub")
    d = json.loads(body)
    assert code == 200 and d["hub"] == "squad-hub"
    assert [(r["slug"], r["repo"], r["live"]) for r in d["repos"]] == [
        ("alpha", "x/alpha", ["run-alpha-7"]), ("beta", "y/beta", [])]


def _hubctl(port, cwd, env):
    return subprocess.run(["bash", str(SCRIPTS / "hubctl.sh"), str(port)], cwd=cwd, capture_output=True, text=True,
                          timeout=30, env={**os.environ, **env, "SG_PROFILE": "beta"})


def test_hubctl_takes_a_running_hub_and_prints_the_repository_path(fleet, tmp_path):
    p = _hubctl(fleet["port"], tmp_path, fleet["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"http://127.0.0.1:{fleet['port']}/beta"


def test_hubctl_refuses_a_port_held_by_something_else(fleet, tmp_path):
    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        p = _hubctl(other.server_address[1], tmp_path, fleet["env"])
    finally:
        other.shutdown()
    assert p.returncode != 0
    assert "not the squad hub" in p.stderr


def test_a_restarted_hub_makes_an_open_page_reload(fleet):
    """The page reloads itself when /api/version changes. A restart that served an unchanged hub.py (only hubmux.py, panes.js or
    address.sh changed) must still change it, or the page keeps the panel built from the old code."""
    before = json.loads(_get(fleet["url"] + "/alpha/api/version")[1])["v"]
    time.sleep(0.01)
    again = fleet["mux"].server(0, fleet["mux"].load_apps())
    threading.Thread(target=again.serve_forever, daemon=True).start()
    try:
        after = json.loads(_get(f"http://127.0.0.1:{again.server_address[1]}/alpha/api/version")[1])["v"]
    finally:
        again.shutdown()
    assert isinstance(after, int) and after != before


FAKE_HUB = """
import http.server, json, os, sys
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"hub": "squad-hub", "pid": os.getpid(), "repos": [{"slug": "beta", "repo": "y/beta", "path": "/beta", "live": []}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hub_pid(port):
    try:
        return json.loads(_get(f"http://127.0.0.1:{port}/api/hub")[1])["pid"]
    except (OSError, ValueError, KeyError):
        return None


@pytest.fixture
def fake_hub_dir(fleet, tmp_path):
    """hubctl.sh and address.sh beside a hubmux.py that only answers /api/hub, so a restart is observable offline."""
    d = tmp_path / "scripts"
    d.mkdir()
    for f in ("hubctl.sh", "address.sh"):
        shutil.copy(SCRIPTS / f, d / f)
    (d / "hubmux.py").write_text(FAKE_HUB)
    started = []
    yield d, started
    for port in started:
        pid = _hub_pid(port)
        if pid:
            os.kill(pid, 15)


def _restart(d, port, env):
    return subprocess.run(["bash", str(d / "hubctl.sh"), "--restart", str(port)], cwd=d, capture_output=True, text=True,
                          timeout=60, env={**os.environ, **env, "SG_PROFILE": "beta"})


def test_hubctl_restart_replaces_the_running_hub_and_prints_its_url(fleet, fake_hub_dir):
    d, started = fake_hub_dir
    port = _free_port()
    started.append(port)
    first = subprocess.run(["bash", str(d / "hubctl.sh"), str(port)], cwd=d, capture_output=True, text=True, timeout=60,
                           env={**os.environ, **fleet["env"], "SG_PROFILE": "beta"})
    assert first.returncode == 0, first.stderr
    old = _hub_pid(port)
    assert old
    p = _restart(d, port, fleet["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"http://127.0.0.1:{port}/beta"
    new = _hub_pid(port)
    assert new and new != old
    with pytest.raises(ProcessLookupError):
        os.kill(old, 0)


def test_hubctl_restart_starts_a_hub_when_none_runs(fleet, fake_hub_dir):
    d, started = fake_hub_dir
    port = _free_port()
    started.append(port)
    p = _restart(d, port, fleet["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"http://127.0.0.1:{port}/beta"
    assert _hub_pid(port)


def test_hubctl_restart_never_stops_something_that_is_not_the_squad_hub(fleet, fake_hub_dir):
    d, _ = fake_hub_dir
    port = _free_port()
    other = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if _up(port):
                break
            time.sleep(0.1)
        p = _restart(d, port, fleet["env"])
        assert p.returncode != 0
        assert "not the squad hub" in p.stderr
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def _up(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0
