"""Guard: issue #2952. A session restarts when its project's `guard` block changes, and keeps when it did not.

Every case drives the module production runs, with the fake `claude` of tests/test_operators.py on the path, so no live
session, no network and no key is touched.
"""
import json
import re
import time
from pathlib import Path

from tests.test_operators import calls, hub, operators, record, registry, scenario, session, started

REPO = "acme/widget"
FIRST_ID = "aaaaaaaa-0000-0000-0000-000000000001"   # the id the fake CLI answers every `--bg` with
OTHER_ID = "bbbbbbbb-0000-0000-0000-000000000002"


def guard_block(tmp_path, op, block):
    """The project's active Slack revision, carrying one guard block."""
    path = Path(op.voiced.slack_record_path(str(tmp_path / "state"), REPO))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"repo": REPO, "sha": "a" * 40,
           "revision": {"guard": block,
                        "workspaces": [{"id": "T0TEAM0001", "channels": [{"id": "C0AAAAAA1", "name": "proj-widget"}]}]}}
    path.write_text(json.dumps(doc))


def setup(tmp_path, monkeypatch, block):
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, [REPO])
    registry(tmp_path, [(REPO, "C0AAAAAA1", "proj-widget")])
    guard_block(tmp_path, op, block)
    scenario(tmp_path, [])
    return op, found


def pass_for(tmp_path, op):
    return op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only=REPO)


def live(found, sid, started_at=None):
    return session(found[REPO], "acme-widget", sid, started=int(time.time()) if started_at is None else started_at)


def test_a_session_records_the_guard_hash_it_started_under(tmp_path, monkeypatch):
    """Criterion 1: a start records a sha256 of the project's guard block in its record entry."""
    op, _ = setup(tmp_path, monkeypatch, {"max_chars": 500})
    assert pass_for(tmp_path, op) == 0
    digest = record(tmp_path)["acme-widget"].get("guard")
    assert re.fullmatch(r"[0-9a-f]{64}", digest or ""), record(tmp_path)


def test_a_pass_restarts_a_session_whose_guard_block_changed(tmp_path, monkeypatch):
    """Criterion 2: a changed guard block stops the live session and starts its replacement in the same pass."""
    op, found = setup(tmp_path, monkeypatch, {"max_chars": 500})
    assert pass_for(tmp_path, op) == 0
    assert len(started(tmp_path)) == 1
    scenario(tmp_path, [live(found, FIRST_ID)])
    guard_block(tmp_path, op, {"max_chars": 900})
    assert pass_for(tmp_path, op) == 0
    assert f"stop {FIRST_ID}" in calls(tmp_path), calls(tmp_path)
    assert len(started(tmp_path)) == 2, f"the changed guard block started no replacement: {started(tmp_path)}"


def test_a_pass_keeps_a_session_whose_guard_block_did_not_change(tmp_path, monkeypatch):
    """Criterion 3: an unchanged guard block keeps the live session, with no stop and no new start."""
    op, found = setup(tmp_path, monkeypatch, {"max_chars": 500})
    assert pass_for(tmp_path, op) == 0
    scenario(tmp_path, [live(found, FIRST_ID)])
    guard_block(tmp_path, op, {"max_chars": 500})
    before = len(started(tmp_path))
    assert pass_for(tmp_path, op) == 0
    assert len(started(tmp_path)) == before, f"an unchanged guard block restarted the session: {started(tmp_path)}"
    assert not [c for c in calls(tmp_path) if c.startswith("stop")], calls(tmp_path)


def test_a_guard_restart_keeps_one_session_per_project(tmp_path, monkeypatch):
    """Criterion 4: a guard restart of a project with two live sessions stops the newer one, replaces the older, and
    starts exactly one session for the project."""
    op, found = setup(tmp_path, monkeypatch, {"max_chars": 500})
    assert pass_for(tmp_path, op) == 0
    before = len(started(tmp_path))
    now = int(time.time())
    scenario(tmp_path, [live(found, FIRST_ID, now - 60), live(found, OTHER_ID, now)])
    guard_block(tmp_path, op, {"max_chars": 900})
    assert pass_for(tmp_path, op) == 0
    assert len(started(tmp_path)) == before + 1, f"the project got {len(started(tmp_path)) - before} starts, not one"
    stopped = {c for c in calls(tmp_path) if c.startswith("stop ")}
    assert stopped == {f"stop {FIRST_ID}", f"stop {OTHER_ID}"}, stopped
