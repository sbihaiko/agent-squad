"""Hub subscriptions card: the grok weekly credits are read from further back than the last 3 MB (#1526).

The grok CLI writes `creditUsagePercent` only when its billing extension runs, and the unified log grows by the
second. A tail reading of 3 MB called the probe blind while the credit line was on disk, a few minutes earlier.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

CREDIT = {"ts": "2026-10-11T13:13:53.420Z", "ctx": {"config": {
    "creditUsagePercent": 100.0,
    "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                      "start": "2026-10-08T18:51:01.060398+00:00", "end": "2026-10-15T18:51:01.060398+00:00"}}}}
FILLER = json.dumps({"ts": "2026-10-11T13:14:00.000Z", "msg": "x" * 400})


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_grok_quota_scan", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def log(hub, tmp_path, monkeypatch):
    p = tmp_path / "unified.jsonl"
    monkeypatch.setattr(hub, "GROK_LOG", p)
    monkeypatch.setattr(hub, "GROK_KEPT", tmp_path / "hub-grok.json")   # no reading kept from another test (#553)
    # The credit line's period has ended, so q_grok asks for a refresh. The live script starts the grok TUI on a pty,
    # and on a loaded machine that call ran past the 20 s cap (#2709 landing, no message: the cap names the call).
    # The stub stands in for the script and stamps a marker, so a case proves the refresh still ran through this seam.
    mark = tmp_path / "refreshed"
    stub = tmp_path / "grokrefresh-stub.sh"
    stub.write_text(f"#!/bin/sh\n: > '{mark}'\nexit 0\n")
    monkeypatch.setattr(hub, "GROK_REFRESH", stub)
    return p


def _filler_mb(p, mb):
    with p.open("a") as f:
        f.write((FILLER + "\n") * int(mb * 1_000_000 / (len(FILLER) + 1)))


def test_a_credit_line_older_than_the_old_three_megabyte_window_is_found(hub, log):
    log.write_text(json.dumps(CREDIT) + "\n")
    _filler_mb(log, 5)                       # 5 MB of log after the credit line
    r = hub.q_grok()
    assert r["windows"] == [{"label": "weekly", "pct": 100.0, "resets": 1792090261.060398}]
    assert r["asof"] == pytest.approx(1791724433.42)


def test_the_newest_credit_line_wins_when_several_are_in_range(hub, log):
    old = json.loads(json.dumps(CREDIT))
    old["ctx"]["config"]["creditUsagePercent"] = 40.0
    log.write_text(json.dumps(old) + "\n")
    _filler_mb(log, 5)
    with log.open("a") as f:
        f.write(json.dumps(CREDIT) + "\n")
    assert hub.q_grok()["windows"][0]["pct"] == 100.0


def test_a_line_split_across_the_chunk_boundary_is_not_half_parsed(hub, log):
    log.write_text(json.dumps(CREDIT)[:-12] + "\n")   # a truncated leading line, then a full one
    with log.open("a") as f:
        f.write(json.dumps(CREDIT) + "\n")
    assert hub.q_grok()["windows"][0]["pct"] == 100.0


def test_a_credit_line_a_read_boundary_cuts_in_half_is_still_found(hub, log):
    """The 4 MB read starts 10 bytes inside the only credit line, so the fragment it drops is this line's tail and the
    older read stops at its head: neither half parses. A scan that cuts at its own boundaries calls the log blind
    while the payload is in it, which is the reading this probe exists to avoid."""
    body = json.dumps(CREDIT) + "\n"
    pad = 4_000_010 - len(body)                        # 10 bytes past a whole 4 MB read
    log.write_text(body + (FILLER + "\n") * (pad // (len(FILLER) + 1)) + "x" * (pad % (len(FILLER) + 1)))
    assert log.stat().st_size == 4_000_010
    r = hub.q_grok()
    assert [w["pct"] for w in r["windows"]] == [100.0], r


def test_a_log_with_no_credit_line_reads_unavailable_and_names_its_first_entry(hub, log):
    _filler_mb(log, 1)
    r = hub.q_grok()
    assert r["windows"] == [] and r["unavailable"] is True
    assert "since its first entry at 2026-10-11T13:14:00.000Z" in r["err"] and "64 MB" not in r["err"]


def test_a_missing_log_does_not_raise(hub, log):
    assert hub.q_grok()["unavailable"] is True


def test_a_due_period_refreshes_through_the_injected_script(hub, log, tmp_path):
    """The scan asks for a refresh when the kept period has ended, and it runs that refresh through GROK_REFRESH, never
    a path of its own. The fixture points GROK_REFRESH at a stub; the marker proves the seam is the one that ran."""
    log.write_text(json.dumps(CREDIT) + "\n")
    _filler_mb(log, 5)
    assert hub.q_grok()["windows"][0]["pct"] == 100.0
    assert (tmp_path / "refreshed").exists(), "the due refresh did not run the injected script"
