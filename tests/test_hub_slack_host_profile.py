"""GitHub issue #2820: the Communication theme of a host profile in the multi-repository hub.

hubmux.py serves every profile from one process. The Slack policy module loads through `squad_roots`, in order: SG_SQUAD,
SG_CHECKOUT, the profile's checkout, then the repository the hub file sits in. A host profile's checkout is the host
repository, and the hub tree holds no `squad` package either. The host profile file sets SG_SQUAD, but the hub instance
of that profile read only the mux process's own environment, so the policy module did not resolve: the read view
answered an error and the save answered "bad request".

The seam under test is the mux over a real loopback server: a profile whose checkout holds no `squad` package and whose
profile file names the package's root. Every file is a temporary fixture.
"""
import http.client
import importlib.util
import json
import pathlib
import shutil
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
}
LOCAL = {"version": 1}


def _live_tree(tmp_path):
    """A hub tree shaped as a host machine's: hub.py and hubmux.py in a tree that holds no `squad` package, every other
    script linked beside them."""
    root = tmp_path / "live"
    scripts = root / "contrib" / "squad-goal" / "scripts"
    scripts.mkdir(parents=True)
    for f in SCRIPTS.iterdir():
        if f.name in ("hub.py", "hubmux.py"):
            continue
        (scripts / f.name).symlink_to(f, target_is_directory=f.is_dir())
    for name in ("hub.py", "hubmux.py"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    return root, scripts


def write_profile(prof, slug, co, squad_root):
    (prof / f"{slug}.sh").write_text(
        f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; '
        f': "${{SG_CHECKOUT:={co}}}"; : "${{SG_SQUAD:={squad_root}}}"\n')


def request(port, method, path, body=None):
    """(status, body) of one request whose Host and Origin are the server's own."""
    head = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        c.request(method, path, body=None if body is None else json.dumps(body), headers=head)
        r = c.getresponse()
        return r.status, r.read().decode()
    finally:
        c.close()


@pytest.fixture
def mux(tmp_path, monkeypatch):
    """The hub over one host profile: its checkout holds no `squad` package, and its profile file names this tree's root.
    The mux process's own SG_SQUAD names a directory that holds no package, as a host machine's does."""
    root, scripts = _live_tree(tmp_path)
    assert not (root / "squad" / "__init__.py").is_file(), "the premise: the hub tree holds no squad package"
    nopkg = tmp_path / "nopkg"
    nopkg.mkdir()
    co = tmp_path / "co-host"
    co.mkdir()
    assert not (co / "squad" / "__init__.py").is_file(), "the premise: the host checkout holds no squad package"
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    write_profile(prof, "host", co, ROOT)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.setenv("SG_SQUAD", str(nopkg))
    for k in ("SG_CHECKOUT", "SG_PROJECTS", "SG_FORK_DIR", "SG_PROJECT"):   # the shell's own repository answers for no profile
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_host_profile", scripts / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    assert set(apps) == {"host"}, apps
    assert all(a.get("mod") for a in apps.values()), [(s, a.get("err")) for s, a in apps.items()]
    # The import-time pin, not the mux process's nopkg: this is what makes the policy module resolve (#2820).
    assert apps["host"]["mod"].SQUAD_ROOT == str(ROOT), apps["host"]["mod"].SQUAD_ROOT
    for app in apps.values():
        app["mod"].runs = lambda: []
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield srv.server_address[1], apps
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.heavy
def test_a_host_profile_answers_its_read_view(mux):
    """Criterion 2: GET /<host-profile>/api/slack/files answers the read view, from the policy module its profile file
    names, with no error."""
    port, _ = mux
    status, raw = request(port, "GET", "/host/api/slack/files")
    assert status == 200, raw
    out = json.loads(raw)
    assert out.get("err") is None, out
    assert {t["key"] for t in out["now"]} == {"Workspaces", "Channels", "Personas", "Events", "Files"}, out


@pytest.mark.heavy
def test_a_host_profile_answers_the_diff_of_a_dry_run_save(mux):
    """Criterion 3: POST with confirm false answers the diff of both files, validated by the policy module its profile
    file names."""
    port, _ = mux
    status, raw = request(port, "POST", "/host/api/slack/files",
                          {"committed": COMMITTED, "local": LOCAL, "confirm": False})
    assert status == 200, raw
    out = json.loads(raw)
    assert "err" not in out, out
    assert set(out["diff"]) == {"committed", "local"}, out
    assert out["diff"]["committed"], out


@pytest.mark.heavy
def test_squad_roots_keeps_the_callers_sg_squad_first(mux):
    """The pin belongs to hubmux, never to a shadowed argument: an explicit SG_SQUAD the caller passes stays first in
    the resolution order (#2820 review)."""
    _, apps = mux
    roots = apps["host"]["mod"].squad_roots("/tmp/explicit", "", "", "")
    assert roots[0] == "/tmp/explicit", roots


@pytest.mark.heavy
def test_squad_roots_takes_a_short_script_path(mux):
    """A script path with fewer than four parents gives its roots and no IndexError: a shallow path sits in no
    repository, so it adds none (#2875)."""
    _, apps = mux
    roots = apps["host"]["mod"].squad_roots("/tmp/explicit", "", "", "/x.py")
    assert roots == ["/tmp/explicit"], roots


@pytest.mark.heavy
def test_a_profile_that_names_no_sg_squad_inherits_none(tmp_path, monkeypatch):
    """The pin is unconditional: a profile whose file names no SG_SQUAD imports with an empty one, so it never reads
    the mux process's own package root (#2820 review)."""
    root, scripts = _live_tree(tmp_path)
    co = tmp_path / "co-plain"
    co.mkdir()
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    (prof / "plain.sh").write_text(
        f': "${{SG_REPO:=x/plain}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.setenv("SG_SQUAD", str(ROOT))   # the mux process's own root: a leak would be visible
    for k in ("SG_CHECKOUT", "SG_PROJECTS", "SG_FORK_DIR", "SG_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_plain_profile", scripts / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    app = mux.load_one("plain")
    assert app.get("mod"), app
    assert app["mod"].SQUAD_ROOT == "", app["mod"].SQUAD_ROOT
