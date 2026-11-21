"""Now lists the pending decisions across every date and shows one link to the project's Slack channel (SPEC 2498, #2500).

The pending read spans every date, so a needs-human run of a previous day still waits for the owner. A run whose decision
was handled, verified or reclassified is not pending, and a run resumed after its verdict is live again. The Slack link
resolves per profile from the connection and the daemon's channel map, with a read that never joins a channel and never
writes. A missing connection, channel or domain draws an unavailable state with no anchor.

The page is booted over a stub DOM, as the other phone suites do: the rules under test are the page's and the hub's, never
a stub of them. The Slack API and the Keychain are seams here, so the suite makes no Slack call.
"""
import http.client
import importlib.util
import json
import pathlib
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from tests.test_hub_mobile_move import run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS", "VOICE_STATE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_now_pending", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp_path


@pytest.fixture
def served(hub):
    """The hub's own HTTP handler on a loopback port: the two reads are checked through the routes the page calls."""
    mod, root = hub
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, root, srv.server_port
    srv.shutdown()
    srv.server_close()


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body or b"{}")


def run_folder(root, name, events, request="#2500"):
    d = root / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (d / "request.txt").write_text("Implement GitHub issue " + request + " in this worktree.\n")
    return d


def run_start(ts):
    return {"ts": ts, "event": "run_start", "pos": 1}


def verdict(ts, v="needs-human", reasons=None):
    return {"ts": ts, "event": "verdict", "verdict": v, "reasons": reasons or [], "exit_code": 10, "pos": 9}


OLD = time.time() - 2 * 86400


# --- the pending read

def test_a_needs_human_run_from_a_previous_day_shows(hub):
    mod, root = hub
    run_folder(root, "run-old", [run_start(OLD), verdict(OLD + 600)])
    rows = mod.pending_decisions()
    assert [r["id"] for r in rows] == ["run-old"]
    row = rows[0]
    assert row["label"] == "implement #2500"
    assert row["issue"] == 2500
    assert row["url"] == "https://github.com/x/y/issues/2500"
    assert row["chip"] == "needs a decision"
    assert row["spend"] == 0
    assert row["age"] >= 86400, "a decision from a previous day carries its own age"


def test_a_handled_run_does_not_show(hub):
    """The board's landing is what settles the decision: the run itself still reads as needs-human."""
    mod, root = hub
    folder = run_folder(root, "run-handled", [run_start(OLD), verdict(OLD + 60)])
    mod.board_cache = {"items": [], "landed": []}
    row, _ = mod.run_row(str(folder), "", {})
    assert row["state"] == "needs-human", "the fixture must reach the exclusion branch, not an empty scan"
    mod.board_cache = {"items": [], "landed": [{"n": 2500, "m": True}]}
    assert mod.pending_decisions() == []


def test_a_verified_run_does_not_show(hub):
    """Every issue the run names is closed on the board: the work was verified by hand, so the decision is settled."""
    mod, root = hub
    folder = run_folder(root, "run-verified", [run_start(OLD), verdict(OLD + 60)])
    mod.board_cache = {"items": [], "landed": []}
    row, _ = mod.run_row(str(folder), "", {})
    assert row["state"] == "needs-human", "the fixture must reach the exclusion branch, not an empty scan"
    mod.board_cache = {"items": [{"n": 2500, "s": "closed"}], "landed": []}
    assert mod.pending_decisions() == []


def test_a_resumed_run_does_not_show(hub):
    """A resume after the verdict makes the run live again: its last invocation has no verdict."""
    mod, root = hub
    folder = run_folder(root, "run-resumed", [run_start(OLD), verdict(OLD + 60),
                                              {"ts": OLD + 120, "event": "resume", "pos": 10}])
    assert mod.scan(folder / "record.jsonl")["verdict"] is None
    assert mod.pending_decisions() == []


def test_a_reclassified_run_does_not_show(hub):
    """A later invocation's own verdict replaces the decision: the run ended another way."""
    mod, root = hub
    folder = run_folder(root, "run-reclassified", [run_start(OLD), verdict(OLD + 60),
                                                   {"ts": OLD + 120, "event": "resume", "pos": 10},
                                                   verdict(OLD + 180, v="completed")])
    assert mod.scan(folder / "record.jsonl")["verdict"] == "completed"
    assert mod.pending_decisions() == []


def test_one_run_gives_one_row(hub):
    mod, root = hub
    folder = run_folder(root, "run-dup", [run_start(OLD), verdict(OLD + 60)])
    copy = root / "worktrees" / "run" / "w" / "runs" / "run-dup"
    copy.mkdir(parents=True)
    (copy / "record.jsonl").write_text((folder / "record.jsonl").read_text())
    (copy / "request.txt").write_text((folder / "request.txt").read_text())
    assert len(mod.pending_decisions()) == 1


def test_the_pending_route_serves_the_decision_of_a_previous_day(served):
    mod, root, port = served
    run_folder(root, "run-old", [run_start(OLD), verdict(OLD + 60)])
    status, body = get(port, "/api/pending")
    assert status == 200
    assert [r["id"] for r in body] == ["run-old"]


# --- the Slack link

class FakeSlack:
    """The Slack read seam: it records every method it is asked for, and answers auth.test only.

    `auth` is one answer, or a function of the token when each profile answers from its own connection."""

    def __init__(self, auth):
        self.auth = auth
        self.calls = []

    def __call__(self, token, method):
        self.calls.append(method)
        if method != "auth.test": return {"ok": False, "error": "unexpected"}
        return self.auth(token) if callable(self.auth) else self.auth


def connection(monkeypatch, hub, auth, repo="x/y", channel=None, profiles=None, tokens=None):
    """The connection seam: the daemon's channel map and the Keychain reader, never a Slack call.

    `profiles` writes a profile-qualified entry beside the repository entry, and `tokens` gives each profile its own
    connection token. With neither, the suite answers with one token and one repository entry."""
    mod, root = hub
    voice = root / "voice"
    voice.mkdir(exist_ok=True)
    table = {repo: channel} if channel else {}
    table.update({f"{p}:{repo}": ch for p, ch in (profiles or {}).items()})
    (voice / "channels.json").write_text(json.dumps(table))
    monkeypatch.setenv("VOICE_STATE", str(voice))
    monkeypatch.setattr(mod, "slack_token",
                        (lambda profile=None: (tokens or {}).get(profile or mod.profile_of()))
                        if tokens else (lambda profile=None: "xoxb-test"))
    fake = FakeSlack(auth)
    monkeypatch.setattr(mod, "slack_api", fake)
    return fake


AUTH = {"ok": True, "url": "https://example-ws.slack.com/"}
CHANNEL = {"id": "C0EXAMPLE1", "name": "proj-x"}


def test_the_slack_link_is_the_channel_archive_on_the_connection_domain(hub, monkeypatch):
    fake = connection(monkeypatch, hub, AUTH, channel=CHANNEL)
    link = hub[0].slack_link(refresh=True)
    assert link["state"] == "ok"
    assert link["url"] == "https://example-ws.slack.com/archives/C0EXAMPLE1"
    assert link["channel"] == "#proj-x"
    assert link["text"] == "Answer in Slack"
    assert fake.calls == ["auth.test"], "the hub's read joins no channel and lists none"


def test_the_link_is_unavailable_with_no_connection(hub, monkeypatch):
    connection(monkeypatch, hub, AUTH, channel=CHANNEL)
    monkeypatch.setattr(hub[0], "slack_token", lambda profile=None: None)
    link = hub[0].slack_link(refresh=True)
    assert link["state"] == "unavailable" and link["url"] is None
    assert "no connection" in link["text"]


def test_the_link_is_unavailable_when_the_connection_refuses(hub, monkeypatch):
    connection(monkeypatch, hub, {"ok": False, "error": "invalid_auth"}, channel=CHANNEL)
    link = hub[0].slack_link(refresh=True)
    assert link["state"] == "unavailable" and link["url"] is None


def test_the_link_names_a_host_with_no_keychain(hub, monkeypatch):
    connection(monkeypatch, hub, AUTH, channel=CHANNEL)
    monkeypatch.setattr(hub[0], "KEYCHAIN", False)
    link = hub[0].slack_link(refresh=True)
    assert link["state"] == "unavailable" and link["url"] is None
    assert "no keychain" in link["text"], "a host with no keychain reader is not the same as no connection"


def test_the_link_is_unavailable_with_no_domain(hub, monkeypatch):
    connection(monkeypatch, hub, {"ok": True}, channel=CHANNEL)
    link = hub[0].slack_link(refresh=True)
    assert link["state"] == "unavailable" and link["url"] is None
    assert "no workspace domain" in link["text"]


def test_the_link_is_unavailable_with_no_channel(hub, monkeypatch):
    connection(monkeypatch, hub, AUTH)
    link = hub[0].slack_link(refresh=True)
    assert link["state"] == "unavailable" and link["url"] is None
    assert "no channel" in link["text"]


def test_the_link_is_resolved_once_per_window(hub, monkeypatch):
    fake = connection(monkeypatch, hub, AUTH, channel=CHANNEL)
    first = hub[0].slack_link(refresh=True)
    second = hub[0].slack_link()
    assert first == second and fake.calls == ["auth.test"], "the address is cached, never resolved per request"


def test_each_profile_resolves_its_own_channel_and_domain(hub, monkeypatch):
    """Two profiles answer from two connections: the channel map and the kept answer are keyed by profile, never shared."""
    mod, _ = hub
    auth = {"tok-a": {"ok": True, "url": "https://alpha.slack.com/"},
            "tok-b": {"ok": True, "url": "https://beta.slack.com/"}}
    connection(monkeypatch, hub, lambda token: auth[token],
               profiles={"alpha": CHANNEL, "beta": {"id": "C0EXAMPLE2", "name": "proj-y"}},
               tokens={"alpha": "tok-a", "beta": "tok-b"})
    monkeypatch.setenv("SG_PROFILE", "alpha")
    a = mod.slack_link(refresh=True)
    monkeypatch.setenv("SG_PROFILE", "beta")
    b = mod.slack_link()   # a profile switch never reuses the other profile's window
    assert a["url"] == "https://alpha.slack.com/archives/C0EXAMPLE1"
    assert a["channel"] == "#proj-x"
    assert b["url"] == "https://beta.slack.com/archives/C0EXAMPLE2"
    assert b["channel"] == "#proj-y"


def test_the_slack_link_route_serves_an_unavailable_state_with_no_address(served, monkeypatch):
    mod, _, port = served
    monkeypatch.setattr(mod, "slack_token", lambda profile=None: None)
    status, body = get(port, "/api/slack-link")
    assert status == 200
    assert body["state"] == "unavailable" and body["url"] is None


# --- the page

PENDING_ROW = {"id": "run-old", "label": "implement #2500", "chip": "needs a decision", "issue": 2500,
               "url": "https://github.com/x/y/issues/2500", "spend": 6.22, "age": 1320}
SLACK_OK = {"state": "ok", "url": "https://example-ws.slack.com/archives/C0EXAMPLE1", "channel": "#proj-x",
            "text": "Answer in Slack"}
SLACK_OFF = {"state": "unavailable", "url": None, "channel": None, "text": "Slack link unavailable: no connection."}


def boot(hub, routes):
    return run_page(hub.MOBILE_PAGE, """
      await loadNeeds();
      const a = $("needs-list").querySelector("a");
      const s = $("needs-slack").querySelector("a");
      return { rows: $("needs-list").querySelectorAll("a").length, href: a && a.href,
               label: a && a.textContent, slack: s && s.href,
               off: $("needs-slack-off") ? $("needs-slack-off").textContent : "" };
    """, routes=routes)


def test_now_draws_a_needs_you_row_and_the_slack_link(hub):
    out = boot(hub[0], {"/api/pending": [{"body": json.dumps([PENDING_ROW])}],
                        "/api/slack-link": [{"body": json.dumps(SLACK_OK)}]})
    assert out["rows"] == 1
    assert out["href"] == "https://github.com/x/y/issues/2500"
    assert "implement #2500" in out["label"] and "needs a decision" in out["label"]
    assert out["slack"] == "https://example-ws.slack.com/archives/C0EXAMPLE1"
    assert "?" not in out["href"] and "?" not in out["slack"], "a link that leaves the page is a plain address"


def test_an_unavailable_slack_link_draws_no_anchor(hub):
    out = boot(hub[0], {"/api/pending": [{"body": "[]"}],
                        "/api/slack-link": [{"body": json.dumps(SLACK_OFF)}]})
    assert out["rows"] == 0
    assert out["slack"] is None, "an unavailable link is no anchor"
    assert "no connection" in out["off"]


def test_an_unavailable_state_with_a_stale_address_draws_no_anchor(hub):
    """The state decides the anchor, never the address alone: a stale address under the unavailable state is no link."""
    stale = {**SLACK_OK, "state": "unavailable"}
    out = boot(hub[0], {"/api/pending": [{"body": "[]"}],
                        "/api/slack-link": [{"body": json.dumps(stale)}]})
    assert out["slack"] is None, "the unavailable state draws no anchor however its address reads"


def test_a_row_with_no_http_address_stays_plain_text(hub):
    row = {**PENDING_ROW, "url": "javascript:alert(1)"}
    out = boot(hub[0], {"/api/pending": [{"body": json.dumps([row])}],
                        "/api/slack-link": [{"body": json.dumps(SLACK_OFF)}]})
    assert out["rows"] == 0, "a row whose address is not http(s) is no anchor"


def test_the_now_reads_wait_for_the_now_tab(hub):
    """The page boots on Work here: neither read runs until the owner opens Now."""
    out = run_page(hub[0].MOBILE_PAGE, """
      return { pending: __state.gets["/api/pending"] || 0, slack: __state.gets["/api/slack-link"] || 0 };
    """, routes={}, hash="#/work")
    assert out == {"pending": 0, "slack": 0}


def test_a_previous_day_needs_human_run_reaches_the_page(hub):
    mod, root = hub
    run_folder(root, "run-old", [run_start(OLD), verdict(OLD + 60)])
    rows = mod.pending_decisions()
    out = boot(mod, {"/api/pending": [{"body": json.dumps(rows)}],
                     "/api/slack-link": [{"body": json.dumps(SLACK_OK)}]})
    assert out["rows"] == 1 and out["href"] == "https://github.com/x/y/issues/2500"
