"""The hub's run row keeps a run's figures through one bad read, and its age says what it measures (#1124)."""
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GHREAD_PY = SCRIPTS / "ghread.py"
NOW = time.time()


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_row_read", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_record(folder, events):
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / "record.jsonl"
    record.write_text("".join(json.dumps(e) + "\n" for e in events))
    return record


def run_events(t0):
    return [
        {"ts": t0, "event": "run_start", "pos": 1},
        {"ts": t0 + 60, "event": "outcome", "node": "implement", "entry": 1, "spend": 7.1355,
         "model": "claude-sonnet-4-5", "tokens_in": 1000, "tokens_out": 500, "pos": 2},
    ]


def test_a_record_that_reads_empty_once_keeps_its_figures_and_its_age(hub, tmp_path):
    folder = tmp_path / "runs" / "run-a"
    record = write_record(folder, run_events(NOW - 3600))
    os.utime(record, (NOW - 600, NOW - 600))
    first, _ = hub.run_row(str(folder), "4321", [])
    assert first["spend"] > 0 and first["age"] >= 599
    record.write_text("")   # one read taken while the run is mid-write
    row, _ = hub.run_row(str(folder), "4321", [])
    assert row["spend"] == first["spend"]
    assert row["age"] >= 599
    assert row["state"] == "unreadable"


def test_a_record_that_has_never_had_an_event_is_the_starting_row(hub, tmp_path):
    folder = tmp_path / "runs" / "run-new"
    write_record(folder, [])
    row, _ = hub.run_row(str(folder), "", [])
    assert row["state"] == "starting" and row["spend"] == 0 and row["age"] == 0


def test_the_age_is_the_time_since_the_last_write_and_every_card_names_it(hub, tmp_path):
    folder = tmp_path / "runs" / "run-w"
    record = write_record(folder, run_events(NOW - 3600))
    os.utime(record, (NOW - 4, NOW - 4))
    row, _ = hub.run_row(str(folder), "4321", [])
    assert 3 <= row["age"] <= 5
    assert hub.PAGE.count("last write ${age(r.age)} ago") == 3
    assert not re.search(r"(?<!last write )\$\{age\(r\.age\)\} ago", hub.PAGE)
