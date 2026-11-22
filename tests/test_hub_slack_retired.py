"""GitHub issue #2701: the old Slack settings route and file are retired (story 46, SPEC 2682).

The old path was the per-profile `<profile>.slack.json`, its reader in the hub, the `/api/slack` route that served and
wrote it, and the one-time migration route that read it. This ticket retires all of them. A request to the old route,
and to the migration route, answers not found. The save and the edit live on the files route, which the new
Communication theme reads and writes. No code of the tree names the old file.

The seams under test are the hub's own routes over a real loopback server, and the tree's source text. Every id is a
placeholder and every file is a temporary fixture.
"""
import http.client
import importlib.util
import json
import pathlib
import threading
import tempfile
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
CODE_DIRS = (ROOT / "squad", ROOT / "contrib")

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "persona": "hal",
         "channels": [{"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak",
                       "roles": ["manager"], "announce": ["stg_release"]}]},
    ],
}
LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "hal",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen"}]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module comes from this tree. Every value is undone when
    the module ends."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_retired", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


@pytest.fixture
def server(hub):
    """The hub's own server on a loopback port, so every route answer is the one a browser gets."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def request(srv, method, path, body=None):
    """(status, body) of one request whose Host and Origin are the server's own."""
    port = srv.server_port
    head = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request(method, path, body=None if body is None else json.dumps(body), headers=head)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


# ---------------------------------------------------------------- the retired route

def test_the_old_settings_route_answers_not_found(server):
    """Criterion 1: a GET and a POST to the retired route answer not found, never a settings view and never a write."""
    assert request(server, "GET", "/api/slack")[0] == 404
    assert request(server, "POST", "/api/slack", {"prefix": "[bot]", "confirm": True})[0] == 404


def test_the_old_file_migration_route_is_retired_with_its_reader(server):
    """Criterion 4: the one-time migration route read the old file, so it retires with the reader and answers not found."""
    assert request(server, "GET", "/api/slack/migrate")[0] == 404


def test_the_hub_holds_no_reader_of_the_old_file(hub):
    """Criterion 2: the settings reader and the migration reader are gone from the hub's own surface."""
    for name in ("slack_file", "slack_read", "slack_view", "slack_set", "slack_migrate"):
        assert not hasattr(hub, name), f"the hub still holds {name}"


def test_no_code_of_the_tree_reads_the_old_slack_file():
    """Criterion 4: no Python source of the tree names the retired per-profile file."""
    offenders = []
    for base in CODE_DIRS:
        for path in base.rglob("*.py"):
            text = path.read_text(errors="replace")
            if ".slack.json" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


# ---------------------------------------------------------------- the new theme on one route

def test_the_files_route_carries_the_save_preview(server, tmp_path, monkeypatch, hub):
    """Criterion 5: the theme's save asks the files route. A preview answers the diff of both files and writes nothing."""
    import yaml
    (tmp_path / "checkout" / "docs" / "squad").mkdir(parents=True)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "checkout" / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(COMMITTED, sort_keys=False))
    (tmp_path / "profiles" / "4.slack.local.yaml").write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    monkeypatch.setattr(hub, "CHECKOUT", str(tmp_path / "checkout"))
    monkeypatch.setattr(hub, "PROFILE_NAME", "4")
    monkeypatch.setenv("SG_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("SG_PROJECT", "4")
    body = {"committed": COMMITTED, "local": LOCAL, "confirm": False}
    status, raw = request(server, "POST", "/api/slack/files", body)
    assert status == 200, raw
    out = json.loads(raw)
    assert "err" not in out, out
    assert set(out["diff"]) == {"committed", "local"}, out
    assert out["diff"]["committed"] == [] and out["diff"]["local"] == [], out


def test_the_page_saves_and_edits_over_the_files_route(hub):
    """Criterion 5: the theme's save, its diff and its edit all post to the files route, and none of them to the old one."""
    page = hub.PAGE
    assert page.count('fetch("/api/slack/files",{method:"POST"') >= 3, "a save, a diff or an edit left the files route"


def test_the_page_never_asks_the_retired_route(hub):
    """Criterion 5: the page loads and saves over the files route alone; it asks neither the old route nor the migration."""
    page = hub.PAGE
    assert '"/api/slack/migrate"' not in page
    assert 'fetch("/api/slack")' not in page
    assert '"/api/slack"' not in page
