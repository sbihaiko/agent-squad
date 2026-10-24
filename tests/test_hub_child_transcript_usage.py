"""The hub counts a child session's tokens when its outcome never reached the record (#796).

A child that is killed, or still running, writes no `outcome` event, so the run's record carries no tokens for it. Its
session transcript does: Claude Code writes it under ~/.claude/projects, in a folder named for the child's working
directory (the run folder's ws/worktrees_child-<node>). The hub reads it for that child only. A child whose outcome is
in the record is counted from the record alone, so no tokens are counted twice. A transcript repeats one message on
several lines; each message id counts once.
"""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
RUN = "run-20261024-130008-6ac658"
KID = "route.router.1"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_child_transcript", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def layout(hub, tmp_path, monkeypatch):
    """The real layout: <checkout>/worktrees/run/<wt>/runs/<run>/record.jsonl, its ws/ child folder, and a projects dir."""
    run_dir = tmp_path / "ariel" / "worktrees" / "run" / "20261024-130006-13914-36be35" / "runs" / RUN
    (run_dir / "ws" / f"worktrees_child-{KID}").mkdir(parents=True)
    projects = tmp_path / "projects"
    monkeypatch.setattr(hub, "CLAUDE_PROJECTS", projects, raising=False)
    monkeypatch.setattr(hub, "HIST", tmp_path / "hist.json")
    monkeypatch.setattr(hub, "hist", {})
    monkeypatch.setattr(hub, "live", {})
    return run_dir, projects


def _transcript(projects, run_dir, kid=KID):
    cwd = os.path.join(str(run_dir), "ws", f"worktrees_child-{kid}")
    folder = projects / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    folder.mkdir(parents=True)
    return folder


def _usage_line(msg_id, model, inp, out, cr, cc):
    msg = {"id": msg_id, "model": model,
           "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}}
    return json.dumps({"type": "assistant", "message": msg}) + "\n"


def _record(run_dir, *events):
    rows = [{"ts": 1792857609.0, "event": "run_start", "run_id": RUN},
            {"ts": 1792857610.0, "event": "outcome", "node": "router", "entry": 1, "model": "claude-opus-5-5",
             "tokens_in": 18549, "tokens_out": 2153, "cache_read": 0, "spend": 0.19}]
    rows += list(events)
    rows.append({"ts": 1792857800.0, "event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["killed"]})
    (run_dir / "record.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return run_dir / "record.jsonl"


SPAWN = {"ts": 1792857611.0, "event": "spawn_accepted", "child": KID, "parent": "router", "depth": 1}


def test_an_aborted_child_is_counted_from_its_transcript_once_per_message(hub, layout):
    """RED before #796: the child wrote no outcome, so its haiku tokens never reached the run's per-model map.
    The first message appears on two lines and counts once; the synthetic line counts nothing."""
    run_dir, projects = layout
    rec = _record(run_dir, SPAWN)
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(
        _usage_line("msg_1", "claude-haiku-5-5", 10, 5, 100, 7)
        + _usage_line("msg_1", "claude-haiku-5-5", 10, 5, 100, 7)
        + _usage_line("msg_2", "claude-haiku-5-5", 20, 6, 200, 0)
        + _usage_line("msg_3", "<synthetic>", 0, 0, 0, 0))
    got = hub.scan(rec)
    haiku = got["mod"]["claude-haiku-5-5"]
    assert haiku["calls"] == 2, haiku
    assert haiku["in"] == 337, haiku          # fresh 10 + 7 + 20, cache reads 300 (inside tokens_in, as an outcome's is)
    assert haiku["out"] == 11, haiku
    assert haiku["cache"] == 300, haiku
    assert "<synthetic>" not in got["mod"], got["mod"]
    # fresh 37 at 0.1, cache reads 300 at 0.01 (the registry names no cache rate: the shared ratio), output 11 at 0.5, per 1M
    assert haiku["spend"] == pytest.approx(12.2 / 1e6, rel=1e-6), haiku
    assert got["spend"] == pytest.approx(0.19 + 12.2 / 1e6, rel=1e-3), got


def test_a_child_with_a_recorded_outcome_is_not_counted_twice(hub, layout):
    """The record's outcome is the truth for its child: the transcript of that child adds nothing."""
    run_dir, projects = layout
    outcome = {"ts": 1792857700.0, "event": "outcome", "node": KID, "entry": 1, "outcome": "pass", "model": "claude-haiku-5-5",
               "tokens_in": 999, "tokens_out": 9, "cache_read": 0, "spend": 0.5}
    rec = _record(run_dir, SPAWN, outcome)
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 100, 7))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["in"] == 999, got["mod"]
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]


def test_a_run_without_a_transcript_keeps_its_record_totals(hub, layout):
    """No transcript folder (the child never started, or its folder was cleaned): nothing is added, nothing fails."""
    run_dir, _ = layout
    rec = _record(run_dir, SPAWN)
    got = hub.scan(rec)
    assert set(got["mod"]) == {"claude-opus-5-5"}, got["mod"]


def test_the_day_stats_show_the_aborted_child_model(hub, layout, monkeypatch):
    """The panel reads stats(): the haiku child appears as its own model beside the router's opus."""
    run_dir, projects = layout
    rec = _record(run_dir, SPAWN)
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 100, 7))
    got = hub.scan(rec)
    run = {"t0": 1792857609.0, "t1": 1792857800.0, "dur": 3.0, "done": True, "spend": got["spend"], "sub": got["sub"],
           "api": got["api"], "mod": got["mod"], "id": RUN, "dir": str(run_dir)}
    monkeypatch.setattr(hub, "all_runs", lambda: [run])
    models = {m["model"] for m in hub.stats("day")["models"]}
    assert {"claude-opus-5-5", "claude-haiku-5-5"} <= models, models


def test_a_finished_run_cached_under_v15_is_scanned_again(hub, layout):
    """#796: the scan cache marker is v16, so a finished run cached under v15 is read again, with its child tokens."""
    run_dir, _ = layout
    rec = _record(run_dir, SPAWN)
    stale = {"v15": 1, "sentinel": True}
    hub.hist[str(rec)] = stale
    got = hub.scan(rec)
    assert got is not stale and "sentinel" not in got, got
    fresh = {"v16": 1, "sentinel": True}
    hub.hist[str(rec)] = fresh
    assert hub.scan(rec) is fresh
