"""GitHub issue #3010: the Personas theme's read route in the hub (SPEC 3009).

One hub read route, `/api/personas`, answers the rows of the Settings > Personas theme: one row for each client and
persona id over every roster project, merged by the pure rules module `squad/personas.py`. The route reads the roster's
seats and the profile's client map; the page draws what it answers (ADR-0137).

The seams under test are the hub's own read, `personas_overview`, over plain seats and clients, and the route as a real
loopback server answers it. No browser runs here. Every id is a placeholder.
"""
import importlib.util
import json
import pathlib
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from squad import clients as CL

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def hub():
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_personas", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def seat(pid, personas, local=None):
    """One roster seat as the Slack roster hands it in: id, label, the two documents and their file names."""
    return {"id": pid, "label": pid, "committed": {"personas": personas}, "local": {"personas": local or []},
            "cf": "slack.yaml", "lf": f"{pid}.slack.local.yaml"}


def clients(*rows):
    return CL.normalize({"clients": list(rows)})


def test_the_read_answers_one_row_for_each_client_and_persona_id(hub):
    """#3010 acceptance 3: the route answers one group for each client, and one row for each persona id inside it."""
    seats = [seat("alpha", [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}])]
    got = hub.personas_overview(seats, clients({"id": "acme", "name": "Acme", "projects": ["alpha"]}))
    assert got["err"] is None, got
    assert [g["id"] for g in got["groups"]] == ["acme"], got
    assert [r["id"] for r in got["groups"][0]["rows"]] == ["hal"], got


def test_the_route_serves_the_read_over_loopback(hub):
    """#3010 acceptance 3: GET /api/personas answers JSON with the groups of the read."""
    mp = pytest.MonkeyPatch()
    mp.setattr(hub, "personas_overview",
               lambda: {"err": None, "groups": [{"id": "acme", "name": "Acme", "rows": [{"id": "hal"}]}]})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/personas", timeout=10) as r:
            got = json.loads(r.read())
    finally:
        srv.shutdown()
        srv.server_close()
        mp.undo()
    assert got == {"err": None, "groups": [{"id": "acme", "name": "Acme", "rows": [{"id": "hal"}]}]}, got


def test_the_route_reads_the_roster_and_the_client_map_when_called_with_no_argument(hub, monkeypatch):
    """The route calls the read with no argument: the seats come from the roster and the clients from the profile's
    local map, through the modules the hub already reads."""
    seats = [seat("alpha", [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}])]
    monkeypatch.setattr(hub, "_slack_roster_checked", lambda cache=0.0: (seats, None))
    monkeypatch.setattr(hub, "_clients_map_read", lambda: (clients({"id": "acme", "name": "Acme",
                                                                   "projects": ["alpha"]}), None))
    got = hub.personas_overview()
    assert [r["id"] for g in got["groups"] for r in g["rows"]] == ["hal"], got


def test_a_roster_read_that_fails_answers_its_reason_and_no_group(hub, monkeypatch):
    """A broken profile read is a message, never an empty roster drawn as a roster with no persona."""
    monkeypatch.setattr(hub, "_slack_roster_checked", lambda cache=0.0: ([], "the profile read failed: boom"))
    monkeypatch.setattr(hub, "_clients_map_read", lambda: ([], None))
    got = hub.personas_overview()
    assert got["groups"] == [] and "boom" in got["err"], got


def test_a_client_map_that_fails_names_its_reason_and_keeps_every_project_in_its_own_group(hub, monkeypatch):
    """A map that cannot be read groups no project, so each project forms its own group and its personas stay on the
    page."""
    seats = [seat("alpha", [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}])]
    monkeypatch.setattr(hub, "_slack_roster_checked", lambda cache=0.0: (seats, None))
    monkeypatch.setattr(hub, "_clients_map_read", lambda: ([], "the client map is not YAML"))
    got = hub.personas_overview()
    assert [g["id"] for g in got["groups"]] == ["alpha"], got
    assert "not YAML" in got["err"], got


def test_a_broken_rule_module_answers_no_internal_detail(hub, monkeypatch, capsys):
    """The route is unauthenticated, so a failure of the persona rules answers a stable message and keeps the exception's
    text, with its paths, in the hub's log."""
    seats = [seat("alpha", [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}])]
    clients_in = clients({"id": "acme", "name": "Acme", "projects": ["alpha"]})

    def boom():
        raise ImportError("/private/var/checkout holds no squad package")

    monkeypatch.setattr(hub, "personas_module", boom)
    got = hub.personas_overview(seats, clients_in)
    assert got["groups"] == [] and got["err"] == "the persona rules are unavailable", got
    assert "/private/var" in capsys.readouterr().err


def test_a_merge_that_raises_answers_no_internal_detail(hub, monkeypatch, capsys):
    """A rule module that raises answers the same stable message, and the hub's log names the failure."""
    seats = [seat("alpha", [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}])]
    clients_in = clients({"id": "acme", "name": "Acme", "projects": ["alpha"]})

    class Broken:
        @staticmethod
        def groups(_seats, _clients):
            raise KeyError("/private/var/checkout/squad/personas.py")

    monkeypatch.setattr(hub, "personas_module", lambda: Broken)
    got = hub.personas_overview(seats, clients_in)
    assert got["groups"] == [] and got["err"] == "the personas could not be merged", got
    assert "/private/var" in capsys.readouterr().err
