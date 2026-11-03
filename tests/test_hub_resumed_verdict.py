"""The hub reads a resumed run live, never from a stale verdict (#1229).

Only the last verdict after the last `run_start` or `resume` is final (ADR-0167). `scan()` reads the verdict of the last
invocation only, and the finished-run cache (`hub-history.json`) serves a run only while its record has the size and write
time it had when it was cached: a record that grew after its verdict (a resume) is read again. An unchanged finished run is
still served from the cache, with no parse.
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GHREAD_PY = SCRIPTS / "ghread.py"
ENGINE = 99001


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_resumed_verdict", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HIST", tmp_path / "hist.json")
    monkeypatch.setattr(mod, "hist", {})
    monkeypatch.setattr(mod, "live", {})
    return mod


def call(pid, at):
    return {"ts": at, "event": "call_start", "node": "n", "entry": 1, "item": None, "session": "s", "pid": pid, "started_at": at}


def verdict(at, v="failed", rc=1):
    return {"ts": at, "event": "verdict", "verdict": v, "reasons": ["x"], "exit_code": rc}


def write(folder, events):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "record.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in events))
    return f


def append(f, events):
    with open(f, "a") as h:
        h.write("".join(json.dumps(e) + "\n" for e in events))
    st = os.stat(f)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))   # a later write time, whatever the clock's grain


def finished(t):
    return [{"ts": t, "event": "run_start"}, call(ENGINE - 1, t + 1), verdict(t + 2)]


def test_a_run_resumed_after_its_verdict_reads_live(hub, tmp_path):
    """The defect: scan() took the last verdict anywhere in the record, so a resumed run read finished."""
    t = time.time() - 600
    folder = tmp_path / "runs" / "run-a"
    f = write(folder, finished(t) + [{"ts": t + 3, "event": "resume"}, call(ENGINE, t + 4)])
    r = hub.scan(f)
    assert (r["verdict"], r["t1"], r["exit_code"], r["reasons"]) == (None, None, None, []), r
    assert r["pid"] == ENGINE
    row, _ = hub.run_row(str(folder), "", {ENGINE: t})
    assert row["live"] is True and row["running"] is True, row


def test_a_verdict_after_the_last_resume_is_final(hub, tmp_path):
    t = time.time() - 600
    f = write(tmp_path / "runs" / "run-b", finished(t) + [{"ts": t + 3, "event": "resume"}, call(ENGINE, t + 4),
                                                          verdict(t + 5, "completed", 0)])
    r = hub.scan(f)
    assert (r["verdict"], r["t1"], r["exit_code"]) == ("completed", t + 5, 0), r


def test_a_cached_finished_run_whose_record_grew_is_read_again(hub, tmp_path):
    """The defect: hub-history.json served a run once it had a verdict, with no freshness check."""
    t = time.time() - 600
    folder = tmp_path / "runs" / "run-c"
    f = write(folder, finished(t))
    assert hub.scan(f)["verdict"] == "failed"
    assert str(f) in json.loads(hub.HIST.read_text()), "a finished run is cached"
    append(f, [{"ts": t + 3, "event": "resume"}, call(ENGINE, t + 4)])
    r = hub.scan(f)
    assert r["verdict"] is None and r["pid"] == ENGINE, r
    row, _ = hub.run_row(str(folder), "", {ENGINE: t})
    assert row["live"] is True and row["running"] is True, row


def test_a_cached_run_read_from_disk_whose_record_grew_is_read_again(hub, tmp_path):
    """The cache file outlives the hub: a restarted hub loads it and still notices the record grew."""
    t = time.time() - 600
    f = write(tmp_path / "runs" / "run-d", finished(t))
    hub.scan(f)
    hub.hist.clear(); hub.hist.update(json.loads(hub.HIST.read_text()))
    append(f, [{"ts": t + 3, "event": "resume"}, call(ENGINE, t + 4)])
    assert hub.scan(f)["verdict"] is None


def test_an_unchanged_finished_run_is_served_from_the_cache(hub, tmp_path, monkeypatch):
    """No extra cost: an unchanged finished run is never parsed again."""
    t = time.time() - 600
    f = write(tmp_path / "runs" / "run-e", finished(t))
    first = hub.scan(f)

    def no_parse(*a, **k):
        raise AssertionError("an unchanged finished run was parsed again")
    monkeypatch.setattr(hub, "child_groups", no_parse)
    monkeypatch.setattr(hub, "request_issues", no_parse)
    assert hub.scan(f) is first
    hub.hist.clear(); hub.hist.update(json.loads(hub.HIST.read_text()))
    assert hub.scan(f)["verdict"] == "failed"
