"""Hub run watch: record changes refresh the live run list before its fallback poll."""
import importlib.util
import json
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
