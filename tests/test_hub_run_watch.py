"""Hub run watch: record changes refresh the live run list before its fallback poll."""
import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "checkout"))
    monkeypatch.setenv("SG_HUB_DIR", str(tmp_path / "hub"))
    spec = importlib.util.spec_from_file_location("hub_run_watch", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_event(record, event):
    with record.open("a") as stream:
        stream.write(json.dumps(event) + "\n")


def test_run_record_changes_refresh_the_live_list(hub, tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(hub, "project_dirs", lambda: [runs_dir])
    monkeypatch.setattr(
        hub,
        "runs",
        lambda: [{"id": row["id"]} for row in hub.all_runs() if not row["done"]],
    )

    hub.refresh_runs()
    state = hub.run_watch_state()
    record = runs_dir / "run-20261015-180805-watch" / "record.jsonl"
    record.parent.mkdir(parents=True)
    write_event(record, {"event": "run_start", "ts": 1.0})

    state = hub.run_watch_tick(state)
    assert hub.run_list() == [{"id": "run-20261015-180805-watch"}]

    write_event(record, {"event": "verdict", "verdict": "completed", "ts": 2.0})
    hub.run_watch_tick(state)
    assert hub.run_list() == []


def test_watch_loop_sees_a_project_dir_added_later(hub, tmp_path, monkeypatch):
    first, later = tmp_path / "a" / "runs", tmp_path / "b" / "runs"
    first.mkdir(parents=True)
    dirs = [first]
    seen = []
    stop = threading.Event()
    monkeypatch.setattr(hub, "RUN_WATCH_SECS", 0.01)
    monkeypatch.setattr(hub, "project_dirs", lambda: list(dirs))
    monkeypatch.setattr(hub, "runs", lambda: seen.append(1) or [])
    thread = threading.Thread(target=hub.watch_runs, args=(stop,), daemon=True)
    thread.start()
    try:
        record = later / "run-20261015-190000-late" / "record.jsonl"
        record.parent.mkdir(parents=True)
        write_event(record, {"event": "run_start", "ts": 1.0})
        dirs.append(later)
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
        thread.join(5)
    assert seen, "a project dir added after the thread started is not watched"
    assert not thread.is_alive()


def test_run_list_fallback_poll_refreshes_only_when_due(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(hub, "runs", lambda: calls.append(1) or [{"id": "r"}])
    monkeypatch.setattr(hub, "RUN_POLL_SECS", 100.0)
    monkeypatch.setattr(hub, "RUN_LIST_AT", 0.0)
    assert hub.run_list() == [{"id": "r"}]
    assert len(calls) == 1
    assert hub.run_list() == [{"id": "r"}]
    assert len(calls) == 1, "a list younger than RUN_POLL_SECS is not rebuilt"
    monkeypatch.setattr(hub, "RUN_POLL_SECS", 0.0)
    hub.run_list()
    assert len(calls) == 2, "a list older than RUN_POLL_SECS is rebuilt by the fallback poll"


def test_project_dirs_and_last_model_read_worktree_runs(hub, tmp_path, monkeypatch):
    # #580 keeps a loop run's folder in its own worktree, and a fork route's in its child tree (#585)
    co = tmp_path / "co"
    roots = [co / "runs", co / "worktrees/run/k1/runs", co / "worktrees/child/gui-9/runs"]
    for d in roots:
        d.mkdir(parents=True)
    monkeypatch.setattr(hub, "CHECKOUT", str(co))
    monkeypatch.setattr(hub, "sh", lambda *a: "")
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    assert set(hub.project_dirs()) == set(roots)
    rec = co / "worktrees/run/k1/runs/run-1/record.jsonl"
    rec.parent.mkdir()
    write_event(rec, {"event": "outcome", "node": "route.router.1", "model": "m-wt", "ts": 1.0})
    assert hub.last_coding_model() == "m-wt"
