"""The Runs tab lists the runs the loop registered, and only a run without a verdict is live (#1649).

`runs()` listed a run only when a dashboard server ran for it or the childcc pass held it, so a working run with no
server was missing from the roster while the live card read "none". The row's `live` field was `not gone`, and `run_gone`
answers False for a finished run, so a run that already carried a verdict was marked live.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
ENGINE = 99001
START = 1_700_000_000.0


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_runs_active", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HIST", tmp_path / "hist.json")
    monkeypatch.setattr(mod, "hist", {})
    monkeypatch.setattr(mod, "live", {})
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return mod


def call(pid, at):
    return {"ts": at, "event": "call_start", "node": "n", "entry": 1, "item": None, "session": "s", "pid": pid, "started_at": at}


def started(at):
    return {"ts": at, "event": "run_start", "run_id": "r", "request": "x"}


def verdict(at, v="completed", rc=0):
    return {"ts": at, "event": "verdict", "verdict": v, "reasons": [], "exit_code": rc}


def write(folder, events):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return folder


def engine(monkeypatch, hub, start=START):
    """One engine process, through the reader the roster uses: the process table for the command, `read_procs` for its start."""
    monkeypatch.setattr(hub, "process_table",
                        lambda: {ENGINE: hub.procs_mod().Proc(ENGINE, 1, ENGINE, start, "/p/.venv/bin/python -m squad start req")})
    monkeypatch.setattr(hub, "read_procs", lambda: {ENGINE: start})


def test_a_registered_run_with_no_server_is_in_the_roster(hub, tmp_path, monkeypatch):
    now = time.time()
    live = write(tmp_path / "runs" / "run-live", [started(now - 120), call(ENGINE, now - 60)])
    fin = [write(tmp_path / "runs" / f"run-done{i}", [started(now - 900), call(ENGINE + 1, now - 800), verdict(now - 700)]) for i in range(3)]
    (tmp_path / "state" / "active-runs.txt").write_text("\n".join([str(live), *map(str, fin)]) + "\n")
    engine(monkeypatch, hub)
    rows = {r["id"]: r for r in hub.runs()}
    assert set(rows) == {"run-live", "run-done0", "run-done1", "run-done2"}
    assert rows["run-live"]["live"] is True
    assert rows["run-live"]["running"] is True
    assert [i for i, r in rows.items() if r["live"]] == ["run-live"]


def test_a_finished_run_is_never_live(hub, tmp_path, monkeypatch):
    now = time.time()
    done = write(tmp_path / "runs" / "run-done", [started(now - 900), call(ENGINE, now - 800), verdict(now - 700)])
    (tmp_path / "state" / "active-runs.txt").write_text(f"{done}\n")
    engine(monkeypatch, hub)
    row = next(r for r in hub.runs() if r["id"] == "run-done")
    assert row["live"] is False and row["running"] is False


def test_a_relative_entry_resolves_under_the_checkout(hub, tmp_path, monkeypatch):
    now = time.time()
    write(tmp_path / "runs" / "run-rel", [started(now - 120), call(ENGINE, now - 60)])
    (tmp_path / "state" / "active-runs.txt").write_text("run-rel\n")
    engine(monkeypatch, hub)
    rows = [r for r in hub.runs() if r["id"] == "run-rel"]
    assert len(rows) == 1 and rows[0]["live"] is True


def test_a_run_whose_process_is_gone_is_not_live(hub, tmp_path, monkeypatch):
    now = time.time()
    write(tmp_path / "runs" / "run-dead", [started(now - 900), call(ENGINE, now - 800)])
    (tmp_path / "state" / "active-runs.txt").write_text("run-dead\n")
    other = ENGINE + 2   # a live reading that does not name the run's own process: the run was killed (#1226)
    monkeypatch.setattr(hub, "process_table",
                        lambda: {other: hub.procs_mod().Proc(other, 1, other, START, "/usr/bin/tail -f log")})
    monkeypatch.setattr(hub, "read_procs", lambda: {other: START})
    row = next(r for r in hub.runs() if r["id"] == "run-dead")
    assert row["live"] is False


def test_a_serverless_row_opens_through_the_ended_route(hub, tmp_path):
    now = time.time()
    folder = write(tmp_path / "runs" / "run-sheet", [started(now - 900), call(ENGINE + 1, now - 800), verdict(now - 700)])
    (tmp_path / "state" / "active-runs.txt").write_text(f"{folder}\n")
    seen = {}
    def start(f):
        seen["folder"] = f
        return 4321
    assert hub.open_ended("run-sheet", start=start) == 4321
    assert seen["folder"] == str(folder)
