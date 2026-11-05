"""The usage panel reads Kimi's plan from Kimi's own usages document.

The hub's Usage panel asks each provider's CLI for its quota. Kimi publishes no CLI command for it, but the coding API
answers `/coding/v1/usages` with the 5 h window and the month's share, so the probe reads that document with the token
the bridge already keeps. An expired token is renewed once through the bridge's own path, which writes its own store;
the probe writes no credential. A renewal that fails reads as no reading with its reason, never as a guess.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_kimi_quota", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _store(hub, monkeypatch, bridge=None, cli=None):
    d = Path(tempfile.mkdtemp())
    (bridge_file, cli_file) = d / "bridge.json", d / "cli.json"
    if bridge is not None:
        bridge_file.write_text(json.dumps(bridge))
    if cli is not None:
        cli_file.write_text(json.dumps(cli))
    monkeypatch.setattr(hub, "KIMI_AUTH", bridge_file)
    monkeypatch.setattr(hub, "KIMI_CLI_AUTH", cli_file)
    return bridge_file, cli_file


def _usages_doc():
    return {"limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                        "detail": {"limit": "100", "used": "24", "remaining": "76",
                                   "resetTime": "2026-11-03T07:18:43.414090Z"}}],
            "usages": {"limit_5h": {"used_ratio": 0.2394, "reset_time": "2026-11-03T07:18:42Z"},
                       "limit_month_total": {"used_ratio": 0.0295, "reset_time": "2026-12-04T00:00:00Z"},
                       "limit_month_code": {"used_ratio": 0.0295, "reset_time": "2026-12-04T00:00:00Z"}}}


def test_the_probe_reads_the_freshest_live_token(hub, monkeypatch):
    now = time.time()
    _store(hub, monkeypatch,
           bridge={"access": "expired-bridge", "expires": int((now - 600) * 1000)},       # the bridge keeps milliseconds
           cli={"access_token": "live-cli", "expires_at": int(now + 600)})                # the CLI keeps seconds
    assert hub.kimi_token() == "live-cli"


def test_two_live_tokens_take_the_later_one(hub, monkeypatch):
    now = time.time()
    _store(hub, monkeypatch,
           bridge={"access": "bridge-soon", "expires": int((now + 60) * 1000)},
           cli={"access_token": "cli-later", "expires_at": int(now + 600)})
    assert hub.kimi_token() == "cli-later"


def test_no_live_token_is_no_token(hub, monkeypatch):
    _store(hub, monkeypatch, bridge={"access": "gone", "expires": 1}, cli={"access_token": "gone", "expires_at": 1})
    assert hub.kimi_token() is None


def test_q_kimi_reads_the_five_hour_and_month_windows(hub, monkeypatch):
    _store(hub, monkeypatch, bridge={"access": "tok", "expires": int((time.time() + 600) * 1000)})
    monkeypatch.setattr(hub, "kimi_usages", lambda tok: _usages_doc())
    got = hub.q_kimi()
    assert [(w["label"], w["pct"]) for w in got["windows"]] == [("5 h", 23.9), ("month", 2.9)]
    assert got["windows"][0]["resets"] == 1793690322.0   # 2026-11-03T07:18:42Z


def test_a_missing_token_reads_as_no_reading_with_its_reason(hub, monkeypatch):
    _store(hub, monkeypatch)
    called = []
    monkeypatch.setattr(hub, "kimi_usages", lambda tok: called.append(tok) or {})
    got = hub.q_kimi()
    assert got["windows"] == [] and got["unavailable"] is True
    assert "token" in got["err"] and called == []


def _expired_stores(hub, monkeypatch):
    """Both stores hold an expired access token, and both hold a refresh token."""
    now = time.time()
    return _store(hub, monkeypatch,
                  bridge={"access": "expired-bridge", "expires": int((now - 600) * 1000), "refresh": "bridge-refresh"},
                  cli={"access_token": "expired-cli", "expires_at": int(now - 60), "refresh_token": "cli-refresh"})


class _Answer:
    """One bridge answer at the HTTP boundary: what the refresh call reads and lets go of."""

    def __init__(self, body=b'{"type":"message","content":[{"type":"text","text":"."}]}'):
        self.body = body

    def read(self): return self.body

    def __enter__(self): return self

    def __exit__(self, *a): return False


@pytest.fixture(autouse=True)
def _no_refresh_backoff(hub):
    """The refresh backoff is hub state, and one test's failed renewal must not silence the next test's."""
    getattr(hub, "KIMI_REFRESH_STATE", {}).update(until=0.0, reason=None)


def _bridge_answer(hub, monkeypatch, answer=None, on_call=None):
    """Stub the HTTP seam every refresh POST crosses, so the real `kimi_refresh` builds and sends its request.
    `on_call` runs as the bridge answers -- the bridge's own store write happens there, never in the probe."""
    sent = []

    def open_refresh(req, timeout=30):
        sent.append({"url": req.full_url, "method": req.get_method(),
                     "body": json.loads(req.data), "headers": dict(req.headers)})
        if on_call: on_call()
        return answer if answer is not None else _Answer()

    monkeypatch.setattr(hub, "kimi_refresh_open", open_refresh)
    return sent


def test_an_expired_token_refreshes_once_through_the_bridge_and_reads_usage(hub, monkeypatch):
    """The bridge renews the token only on a Kimi call, so the probe sends that one call on the bridge's own inference
    route and reads the store the bridge writes. It writes no credential itself, and it never refreshes twice."""
    bridge_file, cli_file = _expired_stores(hub, monkeypatch)

    def bridge_writes_its_own_store():   # this write is the BRIDGE's, made while it answers the call
        bridge_file.write_text(json.dumps({"access": "fresh-bridge", "refresh": "bridge-refresh-2",
                                           "expires": int((time.time() + 800) * 1000)}))

    sent = _bridge_answer(hub, monkeypatch, on_call=bridge_writes_its_own_store)
    read = []
    monkeypatch.setattr(hub, "kimi_usages", lambda tok: read.append(tok) or _usages_doc())
    cli_before = cli_file.read_bytes()
    got = hub.q_kimi()
    assert [(w["label"], w["pct"]) for w in got["windows"]] == [("5 h", 23.9), ("month", 2.9)]
    assert sent == [{"url": "http://127.0.0.1:18765/v1/messages", "method": "POST",
                     "body": {"model": "kimi-for-coding", "max_tokens": 1,
                              "messages": [{"role": "user", "content": "."}]},
                     "headers": {"Content-type": "application/json", "Anthropic-version": "2023-06-01"}}]
    assert read == ["fresh-bridge"]                  # read with the token the bridge wrote
    assert cli_file.read_bytes() == cli_before       # the probe wrote no credential
    assert json.loads(bridge_file.read_text())["access"] == "fresh-bridge"


def test_the_probe_writes_no_credential_and_says_the_store_is_still_empty(hub, monkeypatch):
    """The bridge answers but renews nothing: the probe writes neither store and reads as no reading whose reason names
    the store, never a guess."""
    bridge_file, cli_file = _expired_stores(hub, monkeypatch)
    bridge_before, cli_before = bridge_file.read_bytes(), cli_file.read_bytes()
    sent = _bridge_answer(hub, monkeypatch)
    read = []
    monkeypatch.setattr(hub, "kimi_usages", lambda tok: read.append(tok) or _usages_doc())
    got = hub.q_kimi()
    assert len(sent) == 1 and read == []
    assert bridge_file.read_bytes() == bridge_before and cli_file.read_bytes() == cli_before
    assert got["windows"] == [] and got["unavailable"] is True
    assert "still holds no live token" in got["err"]


def test_a_failed_refresh_reads_as_no_reading_with_its_reason(hub, monkeypatch):
    """A refresh the bridge refuses reads unavailable with the exception's own kind and message, never a guess."""
    _expired_stores(hub, monkeypatch)

    def down(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(hub, "kimi_refresh_open", down)
    read = []
    monkeypatch.setattr(hub, "kimi_usages", lambda tok: read.append(tok) or _usages_doc())
    got = hub.q_kimi()
    assert got["windows"] == [] and got["unavailable"] is True
    assert "refresh failed" in got["err"] and "HTTPError" in got["err"] and "503" in got["err"]
    assert read == []


def test_a_refresh_that_answers_no_message_reads_as_a_failure(hub, monkeypatch):
    """A 200 that is not a bridge message did not renew the token, so the row names a failed refresh, never a store
    that genuinely holds nothing."""
    _expired_stores(hub, monkeypatch)
    _bridge_answer(hub, monkeypatch, answer=_Answer(b'{"type":"error","error":{"message":"Rate limited"}}'))
    read = []
    monkeypatch.setattr(hub, "kimi_usages", lambda tok: read.append(tok) or _usages_doc())
    got = hub.q_kimi()
    assert got["windows"] == [] and got["unavailable"] is True
    assert "refresh failed" in got["err"] and "no message" in got["err"] and read == []


def test_a_failed_refresh_backs_off_so_polls_make_no_model_call(hub, monkeypatch):
    """The panel polls every couple of minutes; a failing renewal is remembered for a few minutes instead of making one
    Kimi call per poll."""
    _expired_stores(hub, monkeypatch)
    calls = []
    monkeypatch.setattr(hub, "kimi_refresh_open",
                        lambda req, timeout=30: calls.append(1) or (_ for _ in ()).throw(OSError("bridge down")))
    hub.q_kimi()
    second = hub.q_kimi()
    assert calls == [1] and "bridge down" in second["err"]


def test_a_cli_only_refresh_token_names_its_path_and_makes_no_call(hub, monkeypatch):
    """The bridge renews only from its own store; when only the CLI's store holds a refresh token the reason says so
    instead of claiming no store has one."""
    now = time.time()
    _store(hub, monkeypatch,
           bridge={"access": "expired-bridge", "expires": int((now - 600) * 1000)},
           cli={"access_token": "expired-cli", "expires_at": int(now - 60), "refresh_token": "cli-refresh"})
    calls = []
    monkeypatch.setattr(hub, "kimi_refresh_open", lambda req, timeout=30: calls.append(1))
    got = hub.q_kimi()
    assert calls == [] and got["unavailable"] is True
    assert "CLI" in got["err"] and "refresh token" in got["err"]


def test_no_refresh_token_is_no_refresh_call(hub, monkeypatch):
    """With no refresh token in either store there is nothing to renew, so the probe calls no refresh path and names
    both stores."""
    _store(hub, monkeypatch, bridge={"access": "gone", "expires": 1})
    calls = []
    monkeypatch.setattr(hub, "kimi_refresh_open", lambda req, timeout=30: calls.append(1))
    got = hub.q_kimi()
    assert calls == []
    assert got["windows"] == [] and got["unavailable"] is True
    assert "bridge" in got["err"] and "CLI" in got["err"]


def _bridge_server(hub, monkeypatch, status, body):
    """A real loopback server in the bridge's place, so the refresh request crosses a socket, not a stub."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            seen.append({"path": self.path, "method": "POST", "body": json.loads(self.rfile.read(n)),
                         "content-type": self.headers.get("content-type"),
                         "anthropic-version": self.headers.get("anthropic-version")})
            self.send_response(status)
            self.send_header("content-type", "application/json")
            if status // 100 == 3: self.send_header("location", "http://127.0.0.1:1/elsewhere")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a): pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("SG_CCP_PROXY", f"http://127.0.0.1:{srv.server_address[1]}")
    return srv, seen


def test_the_refresh_call_posts_a_one_token_kimi_message(hub, monkeypatch):
    """The refresh is the smallest Kimi call the bridge accepts, on its own inference route, and the proxy address is
    read per call so a configured port is the port used."""
    body = b'{"type":"message","content":[{"type":"text","text":"."}]}'
    srv, seen = _bridge_server(hub, monkeypatch, 200, body)
    try:
        hub.kimi_refresh()
    finally:
        srv.shutdown(); srv.server_close()
    assert seen == [{"path": "/v1/messages", "method": "POST",
                     "body": {"model": "kimi-for-coding", "max_tokens": 1,
                              "messages": [{"role": "user", "content": "."}]},
                     "content-type": "application/json", "anthropic-version": "2023-06-01"}]


def test_the_refresh_call_refuses_a_redirect(hub, monkeypatch):
    """The answer must be the bridge's own: a redirect is refused, never followed to another host."""
    srv, seen = _bridge_server(hub, monkeypatch, 302, b"")
    try:
        with pytest.raises(urllib.error.HTTPError) as e: hub.kimi_refresh()
    finally:
        srv.shutdown(); srv.server_close()
    assert e.value.code == 302
    assert len(seen) == 1


def test_the_https_read_takes_a_tls_context(hub):
    """A python.org build carries no CA bundle, so the probe builds its own context instead of trusting the default
    store. The system bundle answers where it exists; elsewhere the default store still builds a context."""
    ctx = hub.ca_context()
    assert ctx.verify_mode is not None
    assert ctx.check_hostname is True


def test_kimi_is_one_of_the_probed_subscriptions(hub):
    assert set(hub.SUBS) <= set(hub.QUOTA_PROBES)
    assert hub.QUOTA_PROBES["kimi"] is hub.q_kimi


def _good_reading(age):
    return {"windows": [{"label": "5 h", "pct": 41.0, "resets": time.time() + 3600}], "asof": time.time() - age}


def _kimi_row(hub):
    return next(g for g in hub.subs_gauges() if g["key"] == "kimi")


@pytest.mark.parametrize("one_pass", ["refresh_quota_once", "kimi_pass_once"])
def test_an_expired_token_keeps_the_last_kimi_reading(hub, monkeypatch, one_pass):
    """The bridge's access token lives fifteen minutes and only a kimi call refreshes it, so between calls the probe
    finds no live token. That is no new reading, never a reading that stopped tracking the account: the row keeps the
    last good windows (quota_row still ages them out) and names why it did not move."""
    _store(hub, monkeypatch, bridge={"access": "gone", "expires": 1})
    monkeypatch.setattr(hub, "quota", {"kimi": _good_reading(600)})
    monkeypatch.setattr(hub, "QUOTA", Path(tempfile.mkdtemp()) / "hub-quota.json")
    monkeypatch.setattr(hub, "QUOTA_PROBES", {"kimi": hub.q_kimi, "nvidia": hub.q_nvidia})
    monkeypatch.setattr(hub, "note_week", lambda k, r: None)
    getattr(hub, one_pass)()
    row = _kimi_row(hub)
    assert row["pct"] == 41.0 and row["window"] == "5 h"
    kept = json.loads(hub.QUOTA.read_text())["kimi"]
    assert [w["pct"] for w in kept["windows"]] == [41.0] and "token" in kept["err"]


def test_an_expired_token_with_no_last_reading_says_why(hub, monkeypatch):
    _store(hub, monkeypatch)
    monkeypatch.setattr(hub, "quota", {})
    monkeypatch.setattr(hub, "QUOTA", Path(tempfile.mkdtemp()) / "hub-quota.json")
    hub.kimi_pass_once()
    row = _kimi_row(hub)
    assert row["pct"] is None and "token" in row["why"]


def test_a_kept_reading_still_ages_out(hub, monkeypatch):
    _store(hub, monkeypatch)
    monkeypatch.setattr(hub, "quota", {"kimi": _good_reading(hub.QUOTA_MAX_AGE + 60)})
    monkeypatch.setattr(hub, "QUOTA", Path(tempfile.mkdtemp()) / "hub-quota.json")
    hub.kimi_pass_once()
    assert _kimi_row(hub)["pct"] is None


def test_an_aged_row_keeps_the_reason_the_probe_left(hub, monkeypatch):
    """A reading past the limit is no reading, and the row says when it was taken. The probe's reason rides along when
    it left one: an owner must be able to tell a broken probe from a provider that has not been used."""
    aged = dict(_good_reading(hub.QUOTA_MAX_AGE + 60),
                err="no live Kimi token: the bridge refreshes it on the next kimi call")
    monkeypatch.setattr(hub, "quota", {"kimi": aged})
    row = hub.quota_row("kimi")
    assert row["unavailable"] is True
    assert "no reading" in row["err"] and "token" in row["err"]


def test_an_aged_row_without_a_reason_says_only_the_age(hub, monkeypatch):
    monkeypatch.setattr(hub, "quota", {"kimi": _good_reading(hub.QUOTA_MAX_AGE + 60)})
    row = hub.quota_row("kimi")
    assert "no reading" in row["err"] and "token" not in row["err"]
