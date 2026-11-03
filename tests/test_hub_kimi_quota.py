"""The usage panel reads Kimi's plan from Kimi's own usages document.

The hub's Usage panel asks each provider's CLI for its quota. Kimi publishes no CLI command for it, but the coding API
answers `/coding/v1/usages` with the 5 h window and the month's share, so the probe reads that document with the token
the bridge already keeps. A probe reads a credential and never writes one: an absent or expired token reads as no
reading with its reason, never as a guess.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
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


def test_the_https_read_takes_a_tls_context(hub):
    """A python.org build carries no CA bundle, so the probe builds its own context instead of trusting the default
    store. The system bundle answers where it exists; elsewhere the default store still builds a context."""
    ctx = hub.ca_context()
    assert ctx.verify_mode is not None
    assert ctx.check_hostname is True


def test_kimi_is_one_of_the_probed_subscriptions(hub):
    assert set(hub.SUBS) <= set(hub.QUOTA_PROBES)
    assert hub.QUOTA_PROBES["kimi"] is hub.q_kimi
