"""One hub, one loopback port, one path per repository (#20).

The seam is HTTP: `hubmux.py` serves every profile the machine knows under `/<profile>`, the root page lists the
repositories and their live workers, and a path no profile owns answers 404 with that list. `hubctl.sh` takes a port as
proof only when the squad hub answers on it. Fixture profiles over temp checkouts; no network, no refresh threads.
"""
import datetime
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
    apps = mux.load_apps()
    apps["alpha"]["mod"].runs = lambda: [_row("run-alpha-7", "running")]   # what the hub sees working; the live-run list is not read
    apps["beta"]["mod"].runs = lambda: []
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{srv.server_address[1]}", "port": srv.server_address[1], "env": env, "mux": mux}
    srv.shutdown()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
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


def test_a_test_card_link_in_the_json_carries_its_repository():
    """The link a test card opens is `/test/<id>` in the JSON of /api/runs: without the slug the mux read `test` as a
    repository and showed its index page where the log belonged."""
    spec = importlib.util.spec_from_file_location("hubmux_json_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps([{"id": "t1", "url": "/test/t1"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    stub = type("M", (), {"H": H})
    srv = mux.server(0, {"alpha": {"mod": stub, "repo": "x/alpha"}})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _, body = _get(f"http://127.0.0.1:{srv.server_address[1]}/alpha/api/runs")
    finally:
        srv.shutdown()
    assert json.loads(body)[0]["url"] == "/alpha/test/t1", body


def test_a_held_run_link_and_its_redirect_carry_their_repository():
    """A run the childcc pass holds is linked as `/held/<name>` and redirects to `/dash/<name>/`: without the slug the mux read
    `held` (then `dash`) as a repository and the diagram pane showed "no repository at /held"."""
    spec = importlib.util.spec_from_file_location("hubmux_held_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/held/"):
                self.send_response(302)
                self.send_header("Location", "/dash/r1/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps([{"id": "r1", "url": "/held/r1"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    stub = type("M", (), {"H": H})
    srv = mux.server(0, {"alpha": {"mod": stub, "repo": "x/alpha"}})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        _, body = _get(base + "/alpha/api/runs")
        try:
            urllib.request.build_opener(NoFollow).open(base + "/alpha/held/r1")
        except urllib.error.HTTPError as e:
            loc = e.headers["Location"]
    finally:
        srv.shutdown()
    assert json.loads(body)[0]["url"] == "/alpha/held/r1", body
    assert loc == "/alpha/dash/r1/", loc


def test_an_ended_run_link_in_a_page_and_its_redirect_carry_their_repository():
    """The mobile page links an ended run as `"/ended/" + id`: without the slug the mux read `ended` as a repository and
    the link answered "no repository at /ended". `/<slug>/ended/<id>` must reach the profile's ended-run page (#907)."""
    spec = importlib.util.spec_from_file_location("hubmux_ended_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    assert mux.rewrite(b'runRow(r, "/ended/" + id)', "alpha") == b'runRow(r, "/alpha/ended/" + id)'
    assert mux.rewrite(b"open('/ended/r1')", "alpha") == b"open('/alpha/ended/r1')"

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/ended/"):
                self.send_response(302)
                self.send_header("Location", "/dash/r1/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'<script>runRow(r, "/ended/" + id)</script>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    stub = type("M", (), {"H": H})
    srv = mux.server(0, {"alpha": {"mod": stub, "repo": "x/alpha"}})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    code, loc = None, None
    try:
        _, page = _get(base + "/alpha/mobile")
        try:
            with urllib.request.build_opener(NoFollow).open(base + "/alpha/ended/r1") as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code, loc = e.code, e.headers.get("Location")
    finally:
        srv.shutdown()
    assert '"/alpha/ended/" + id' in page, page
    assert code == 302, code
    assert loc == "/alpha/dash/r1/", loc


def test_a_real_profile_opens_its_ended_run_behind_the_prefix(tmp_path, monkeypatch):
    """The real hub.py (no stub) mounted as profile `alpha`: a run that ended today in its runs/ opens at `/alpha/ended/<id>`
    as a 302 to `/alpha/dash/<id>/`, never 404. The dashboard start is a no-op port (#907)."""
    t = tmp_path
    prof, base = t / "profiles", t / "state"
    prof.mkdir()
    (t / "co-alpha").mkdir()
    _profile(prof, "alpha", "x/alpha", t / "co-alpha")
    run = t / "co-alpha" / "runs" / "run-ended-1"
    run.mkdir(parents=True)
    now = datetime.datetime.now()
    midnight = datetime.datetime.combine(now.date(), datetime.time(0, 0)).timestamp()
    ended = max(now.timestamp() - 60, midnight + 1)   # a minute ago, but never before today's local midnight
    (run / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (
        {"ts": ended - 1, "event": "run_start", "pos": 1},
        {"ts": ended, "event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0, "pos": 2})))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    spec = importlib.util.spec_from_file_location("hubmux_ended_real", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    hub_mod = apps["alpha"]["mod"]
    monkeypatch.setattr(hub_mod, "project_dirs", lambda: [t / "co-alpha" / "runs"])
    monkeypatch.setattr(hub_mod, "refresh_runs", lambda: [])
    monkeypatch.setattr(hub_mod, "_start_dashboard", lambda folder: 4321)
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/alpha/ended/run-ended-1"
        try:
            with urllib.request.build_opener(NoFollow).open(url) as r:
                code, loc = r.status, None
        except urllib.error.HTTPError as e:
            code, loc = e.code, e.headers.get("Location")
    finally:
        srv.shutdown()
    assert code == 302, code
    assert loc == "/alpha/dash/run-ended-1/", loc


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


@pytest.mark.heavy
def test_the_identity_answers_inside_hubctls_bound_without_a_state_read(tmp_path, monkeypatch):
    """#1202: listing() calls live() and alert() for every profile, and each reads that profile's state. On a machine under
    load the /api/hub read passed hubctl.sh's 3 s bound, so a hub on the port read as a foreign listener and --restart
    refused to start the published copy. The identity query answers from the hub's loaded profiles, never from their state."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (tmp_path / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, tmp_path / f"co-{slug}")
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    spec = importlib.util.spec_from_file_location("hubmux_identity_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    reads = []

    def slow_runs():
        reads.append(1)
        time.sleep(2)   # two profiles at 2 s: listing() reads live() for each, past hubctl.sh's 3 s bound
        return []

    for slug in apps:
        monkeypatch.setattr(apps[slug]["mod"], "runs", slow_runs)
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        start = time.monotonic()
        code, body = _get(f"http://127.0.0.1:{srv.server_address[1]}/api/hub?identity=1", timeout=30)
        took = time.monotonic() - start
    finally:
        srv.shutdown()
    assert code == 200, body
    assert json.loads(body)["hub"] == "squad-hub", body
    assert took < 3, f"the identity read took {took:.1f} s, past hubctl.sh's 3 s bound"
    assert reads == [], "the identity read waited on a per-profile state read"


def _row(id_, state, kind="code"):
    return {"id": id_, "state": state, "kind": kind, "live": True, "age": 5}


def test_the_index_counts_only_the_runs_that_work(tmp_path, monkeypatch):
    """The live-run list keeps finished runs on purpose; the index counts what the hub sees working."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    (tmp_path / "co").mkdir()
    _profile(prof, "alpha", "x/alpha", tmp_path / "co")
    (base / "alpha").mkdir(parents=True)
    ids = ["run-a", "run-b", "run-c", "run-done-1", "run-done-2"]
    (base / "alpha" / "active-runs.txt").write_text("".join(f"{tmp_path}/co/runs/{i}\n" for i in ids))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    spec = importlib.util.spec_from_file_location("hubmux_live_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    rows = [_row("run-a", "running"), _row("run-b", "stalled"), _row("run-c", "starting"),
            _row("run-done-1", "verified"), _row("test-1", "running", "test")]
    monkeypatch.setattr(apps["alpha"]["mod"], "runs", lambda: rows)
    assert mux.live(apps["alpha"]) == ["run-a", "run-b", "run-c"]
    page = mux.root_page(apps, "squad hub").decode()
    head, block, runs = page.partition('<div class="runs">')
    assert '<span class="badge on">3 live</span>' in head, page   # the row states the count, and the finished run is out of it
    assert runs.split("</div>")[0].split() == ["run-a", "run-b", "run-c"], page
    assert "run-done" not in page


@pytest.fixture
def counted(tmp_path, monkeypatch):
    """Two profiles whose runs() counts its own reads, over a clock the test moves (#1848)."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (tmp_path / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, tmp_path / f"co-{slug}")
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    spec = importlib.util.spec_from_file_location("hubmux_runs_cache", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    reads, clock = [], [1000.0]
    monkeypatch.setattr(mux, "CLOCK", lambda: clock[0])
    for slug in apps:
        def runs(slug=slug):
            reads.append(slug)
            return [_row(f"run-{slug}-1", "running")]
        monkeypatch.setattr(apps[slug]["mod"], "runs", runs)
    return mux, apps, reads, clock


def test_the_index_reads_one_run_list_per_profile_per_poll_window(counted):
    """#1848: the index called runs() for every profile on every request -- five profiles, ten process snapshots, 775 ms
    -- and every open page polls /api/hub every 10 s. One read per profile per poll window now answers, keyed by profile;
    an entry as old as the window is read again, never served as fresh. The age is proved by an injected clock, never a
    sleep."""
    mux, apps, reads, clock = counted
    d = mux.listing(apps)
    assert reads == ["alpha", "beta"], "the first listing does not read each profile's run list once"
    assert [r["live"] for r in d["repos"]] == [["run-alpha-1"], ["run-beta-1"]]   # one entry per profile, never shared
    clock[0] += mux.RUNS_TTL - 0.001   # a second page's poll inside the window
    mux.listing(apps)
    assert reads == ["alpha", "beta"], "a poll inside the window rebuilt every profile's run list"
    clock[0] += 0.001   # the entry is now exactly as old as the index poll's own window
    mux.listing(apps)
    assert reads == ["alpha", "beta", "alpha", "beta"], "an entry at its window was served as fresh"


def test_the_api_hub_route_and_the_index_page_share_the_one_read(counted):
    """The route a page polls and the page it opens first both list every profile: neither rebuilds the run list while
    the other's read is inside the window."""
    mux, apps, reads, clock = counted
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        d = json.loads(_get(base + "/api/hub")[1])
        assert [r["live"] for r in d["repos"]] == [["run-alpha-1"], ["run-beta-1"]], d
        assert reads == ["alpha", "beta"], reads
        page = _get(base + "/")[1]
        assert "run-alpha-1" in page, page
        assert reads == ["alpha", "beta"], "/api/hub and the index page each rebuilt every profile's run list"
        clock[0] += mux.RUNS_TTL   # past the window: the next request reads each list again
        _get(base + "/api/hub")
        assert reads == ["alpha", "beta", "alpha", "beta"], "a request past the window served the old run list"
    finally:
        srv.shutdown()


def test_a_failed_run_read_is_not_served_as_fresh(counted):
    """A read that fails answers no live work for that request, and stores nothing: one error never hides a profile's
    runs for the rest of the window."""
    mux, apps, reads, clock = counted
    down = [True]

    def runs():
        reads.append("alpha")
        if down[0]:
            raise RuntimeError("the process table is unreadable")
        return [_row("run-alpha-1", "running")]

    apps["alpha"]["mod"].runs = runs
    assert mux.live(apps["alpha"], "alpha") == []   # the index shows no live work, it never fails the request
    down[0] = False
    assert mux.live(apps["alpha"], "alpha") == ["run-alpha-1"], "a failed read was cached and hid the profile's runs"
    assert reads == ["alpha", "alpha"], reads


def _hubctl(port, cwd, env):
    return subprocess.run(["bash", str(SCRIPTS / "hubctl.sh"), str(port)], cwd=cwd, capture_output=True, text=True,
                          timeout=30, env={**os.environ, **env, "SG_PROFILE": "beta"})


def test_hubctl_takes_a_running_hub_and_prints_the_repository_path(fleet, tmp_path):
    p = _hubctl(fleet["port"], tmp_path, fleet["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"http://127.0.0.1:{fleet['port']}/beta"


def test_the_index_row_states_its_count_and_keeps_the_run_ids_out_of_the_line(fleet):
    """An operator who opens a path the hub does not own gets the list, and a repository holding twenty runs must not
    bury it: the row says `N live`, the ids sit in a block of their own, and the two never share a line."""
    _, body = _get(fleet["url"] + "/gamma")
    head, block, runs = body.partition('<div class="runs">')
    assert '<div class="runs">' in body
    assert "1 live" in head and "run-alpha-7" not in head, head
    assert "run-alpha-7" in runs.split("</div>")[0]
    assert '<span class="badge">idle</span>' in body              # the repository with no run says so in words, not with a blank
    assert "x/alpha" in head and "y/beta" in body


def test_a_test_path_is_told_where_a_test_log_lives(fleet):
    """`/test/<id>` reaches the hub as a repository named `test`; the page must name the address that works instead of
    leaving the operator at a list of repositories and no way back."""
    code, body = _get(fleet["url"] + "/test/t1")
    assert code == 404
    assert "no repository at /test" in body
    assert "/alpha/test/&lt;id&gt;" in body, body


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
    """The page reloads itself when /api/version changes. A restart that served an unchanged hub.py (only hubmux.py, run-sheet.js or
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


def _listener_pid(port):
    """The pid that holds the listening socket: the hub hubctl.sh starts runs detached, so the test finds it by the port."""
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out.split() else None


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


@pytest.mark.heavy
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
    assert f"pid {old}" in p.stderr, p.stderr   # the restart says which pid it stopped (#1202)
    new = _hub_pid(port)
    assert new and new != old
    with pytest.raises(ProcessLookupError):
        os.kill(old, 0)


@pytest.mark.heavy
def test_hubctl_restart_stops_a_running_hubmux_and_names_its_pid(tmp_path):
    """#1202: the restart must work on the real hubmux.py, not a stand-in, and it must say which pid it stopped."""
    prof, home = tmp_path / "profiles", tmp_path / "home"
    prof.mkdir()
    home.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (tmp_path / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, tmp_path / f"co-{slug}")
    port = _free_port()
    # The hub's state stays in the temp home, and no SG_* of this shell reaches it (test-hubmux-check-board.sh's pattern).
    drop = {"SG_STATE", "SG_STATE_BASE", "SG_BOARD_CACHE", "SG_HUB_DIR", "SG_PROFILE", "SG_SHARED", "SG_CHECKOUT",
            "SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_PROJECTS", "SG_FORK_DIR"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    # The start path starts each profile's background reads and no daemon (#2492).
    env.update(HOME=str(home), SG_PROFILES_DIR=str(prof), SG_PROFILE="beta")
    hub = subprocess.Popen([sys.executable, str(SCRIPTS / "hubmux.py"), str(port)], cwd=SCRIPTS, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    listener = None
    try:
        for _ in range(150):
            if _up(port):
                break
            time.sleep(0.1)
        else:
            pytest.fail("the real hubmux never listened")
        for _ in range(100):
            try:
                if _get(f"http://127.0.0.1:{port}/api/hub?identity=1", timeout=3)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        p = subprocess.run(["bash", str(SCRIPTS / "hubctl.sh"), "--restart", str(port)], cwd=tmp_path,
                           capture_output=True, text=True, timeout=60, env=env)
        listener = _listener_pid(port)   # read before the asserts, so the teardown ends the hub the restart started
        assert p.returncode == 0, p.stderr
        assert p.stdout.strip() == f"http://127.0.0.1:{port}/beta"
        assert f"pid {hub.pid}" in p.stderr, p.stderr
        hub.wait(timeout=10)   # the hub this test started is gone
        assert listener and listener != hub.pid
        assert _get(f"http://127.0.0.1:{port}/api/hub?identity=1", timeout=5)[0] == 200
    finally:
        if hub.poll() is None:
            hub.kill()
            hub.wait()
        if listener:
            try:
                os.kill(listener, 15)
            except ProcessLookupError:
                pass


@pytest.mark.heavy
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


def test_each_profile_writes_its_own_board_cache(tmp_path, monkeypatch):
    """#512: hubmux pinned no SG_BOARD_CACHE, so every hub took the process's one, and all of them wrote one file."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (tmp_path / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, tmp_path / f"co-{slug}")
    foreign = tmp_path / "foreign" / "hub-board.json"
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.setenv("SG_BOARD_CACHE", str(foreign))   # what the shell that started hubmux exported
    spec = importlib.util.spec_from_file_location("hubmux_board_cache", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    a, b = apps["alpha"]["mod"].BOARD, apps["beta"]["mod"].BOARD
    assert a != b, f"two profiles on two boards share one cache: {a}"
    assert foreign not in (a, b), "a hub takes the cache of the shell that started hubmux"


def test_each_profile_reads_the_one_shared_quota_directory(tmp_path, monkeypatch):
    """#678: hubmux pinned no SG_SHARED, so a hub started from a shell without it wrote its quota cache in its profile's
    state directory, and the loop, which reads `$SG_SHARED/hub-quota.json`, read a cache days old."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug, repo in (("alpha", "x/alpha"), ("beta", "y/beta")):
        (tmp_path / f"co-{slug}").mkdir()
        _profile(prof, slug, repo, tmp_path / f"co-{slug}")
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_SHARED", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_shared", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    for slug in ("alpha", "beta"):
        assert apps[slug]["mod"].QUOTA == base / "_shared" / "hub-quota.json", apps[slug]["mod"].QUOTA


def test_every_ended_run_link_in_the_real_mobile_page_gains_the_profile_prefix():
    """The real hub-mobile.html builds its ended-run link; the mux must prefix every such link in that page, not only a
    stub's literal (#907)."""
    spec = importlib.util.spec_from_file_location("hubmux_real_page_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    html = (SCRIPTS / "hub-mobile.html").read_bytes().decode()
    assert html.count("/ended/") >= 1, "the mobile page has no ended-run link to check"
    out = mux.rewrite(html.encode(), "alpha").decode()
    assert out.count("/alpha/ended/") == html.count("/ended/") == out.count("/ended/"), (
        html.count("/ended/"), out.count("/alpha/ended/"), out.count("/ended/"))
