"""Guard: issue #2702. The promotion pass emits `stg_release` when the dev-to-stg pull request merges and `prod_release`
when main moves, and a project with its own deploy step emits an event by the same command after its deploy and its green
e2e. One record holds the event id and the list of items, and `voiced` reads the record from its state directory.

The shell suites `contrib/squad-goal/scripts/test-promote.sh` and `test-promote-main-pass.sh` prove the two emissions
with a fake pass. These cases prove the record, the command, the dedupe of a repeated emission, and the daemon's read.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
MODULE = VOICE / "release_events.py"


def load(name, path):
    """Load a module of the voice directory fresh, under its own name, so one case's imports never answer another's."""
    old = sys.path[:]
    sys.path.insert(0, str(VOICE))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old


def lines(state):
    path = Path(state) / "release-events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_record_holds_the_event_id_and_the_items(tmp_path):
    rel = load("release_events", MODULE)
    record, created = rel.emit("stg_release", ["a1b2c3 Merge the fix", "d4e5f6 Land the doc"],
                               key="stg_release:a1b2c3", state_dir=str(tmp_path))
    assert created is True
    got = lines(tmp_path)
    assert len(got) == 1, got
    assert got[0]["id"] == "stg_release"
    assert got[0]["items"] == ["a1b2c3 Merge the fix", "d4e5f6 Land the doc"]
    assert got[0]["key"] == "stg_release:a1b2c3"
    assert got[0]["ts"], "the record carries the time it was emitted"


def test_a_second_emission_of_one_key_writes_no_second_record(tmp_path):
    rel = load("release_events", MODULE)
    first, created = rel.emit("prod_release", ["a1b2c3 Ship"], key="prod_release:a1b2c3", state_dir=str(tmp_path))
    again, created_again = rel.emit("prod_release", ["a1b2c3 Ship"], key="prod_release:a1b2c3", state_dir=str(tmp_path))
    assert created is True and created_again is False
    assert again["key"] == first["key"]
    assert len(lines(tmp_path)) == 1, "a repeated emission of one release appends nothing"


def test_a_project_emits_an_event_by_a_command(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env["VOICE_STATE"] = str(tmp_path)
    r = subprocess.run([sys.executable, str(MODULE), "emit", "--event", "stg_release",
                        "--key", "stg_release:77aa", "--item", "77aa Deploy and e2e green"],
                       capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    got = lines(tmp_path)
    assert [x["id"] for x in got] == ["stg_release"], got
    assert got[0]["items"] == ["77aa Deploy and e2e green"]


def test_voiced_reads_the_event_record(tmp_path):
    rel = load("release_events", MODULE)
    rel.emit("stg_release", ["a1b2c3 Merge the fix"], key="stg_release:a1b2c3", state_dir=str(tmp_path))
    rel.emit("prod_release", ["a1b2c3 Merge the fix"], key="prod_release:a1b2c3", state_dir=str(tmp_path))
    voiced = load("voiced", VOICE / "voiced.py")
    state = voiced.State(str(tmp_path))
    got = state.release_events()
    assert [x["id"] for x in got] == ["stg_release", "prod_release"], got
    assert got[0]["items"] == ["a1b2c3 Merge the fix"]


def test_the_writer_and_the_daemon_share_one_state_directory():
    rel = load("release_events", MODULE)
    voiced = load("voiced_shared", VOICE / "voiced.py")
    assert os.path.basename(rel.path()) == "release-events.jsonl"
    assert os.path.dirname(rel.path()) == voiced.DEFAULT_STATE, "the command and the daemon read one directory"


def test_a_command_with_no_item_is_refused(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env["VOICE_STATE"] = str(tmp_path)
    r = subprocess.run([sys.executable, str(MODULE), "emit", "--event", "stg_release"],
                       capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode != 0
    assert not (Path(tmp_path) / "release-events.jsonl").exists()
