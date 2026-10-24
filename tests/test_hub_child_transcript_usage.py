"""The hub counts a child session's tokens when its outcome never reached the record (#796).

A child that is killed, or still running, writes no `outcome` event, so the run's record carries no tokens for it. Its
session transcript does: Claude Code writes it under ~/.claude/projects, in a folder named for the child's working
directory (the run folder's ws/<logical>-<node>, named as squad/runners/children.py:workspace names it). The hub reads it for
that child only. A child whose outcome is in the record is counted from the record alone, so no tokens are counted twice.
A transcript repeats one message on several lines, and a subagent's transcript sits in a subfolder; each message id counts once.
"""
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
RUN = "run-20261024-130008-6ac658"
KID = "route.router.1"
DEFAULT_WS = "worktrees_child-" + KID   # the folder a child of the default template gets (no definition on the spawn event)


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4")):
            mp.setenv(k, os.environ.get(k, v))
        mp.setenv("SG_HUB_DIR", str(tmp_path_factory.mktemp("hub")))   # read at import; restored when the module's tests end
        spec = importlib.util.spec_from_file_location("hub_child_transcript", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


@pytest.fixture
def homes(hub, tmp_path, monkeypatch):
    """A projects dir for the transcripts, and empty hub caches, so no test reads another's state."""
    projects = tmp_path / "projects"
    monkeypatch.setattr(hub, "CLAUDE_PROJECTS", projects, raising=False)
    monkeypatch.setattr(hub, "HIST", tmp_path / "hist.json")
    monkeypatch.setattr(hub, "hist", {})
    monkeypatch.setattr(hub, "live", {})
    monkeypatch.setattr(hub, "USAGE_FILES", {})
    return projects


@pytest.fixture
def layout(hub, tmp_path, homes):
    """The real layout: <checkout>/worktrees/run/<wt>/runs/<run>/record.jsonl, its ws/ child folder, and a projects dir."""
    run_dir = tmp_path / "ariel" / "worktrees" / "run" / "20261024-130006-13914-36be35" / "runs" / RUN
    (run_dir / "ws" / DEFAULT_WS).mkdir(parents=True)
    return run_dir, homes


def _folder(projects, cwd):
    folder = projects / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _transcript(projects, run_dir, ws_name=DEFAULT_WS):
    return _folder(projects, os.path.join(str(run_dir), "ws", ws_name))


def _usage_line(msg_id, model, inp, out, cr, cc):
    msg = {"id": msg_id, "model": model,
           "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}}
    return json.dumps({"type": "assistant", "message": msg}) + "\n"


def _record(run_dir, *events, verdict=True):
    rows = [{"ts": 1792857609.0, "event": "run_start", "run_id": RUN},
            {"ts": 1792857610.0, "event": "outcome", "node": "router", "entry": 1, "model": "claude-opus-5-5",
             "tokens_in": 18549, "tokens_out": 2153, "cache_read": 0, "spend": 0.19}]
    rows += list(events)
    if verdict:
        rows.append({"ts": 1792857800.0, "event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["killed"]})
    (run_dir / "record.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return run_dir / "record.jsonl"


SPAWN = {"ts": 1792857611.0, "event": "spawn_accepted", "child": KID, "parent": "router", "depth": 1}


def _spawn(workspace, tools=("Write",), kid=KID):
    return {"ts": 1792857611.0, "event": "spawn_accepted", "child": kid, "parent": "router", "depth": 1,
            "definition": {"workspace": workspace, "tools": list(tools)}}


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
    """The panel reads stats(): the haiku child appears as its own model beside the router's opus. The local token ledger is not read."""
    run_dir, projects = layout
    monkeypatch.setattr(hub, "ledger_calls", lambda: [])
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


# --- (2) the child's real workspace folder ---------------------------------------------------------------------------------

@pytest.mark.parametrize("workspace, ws_name", [
    ("worktrees/review", "worktrees_review-" + KID),   # a template workspace other than worktrees/child
    (".", "target-" + KID),
    (None, "target-" + KID),
])
def test_the_transcript_is_found_in_the_folder_its_workspace_names(hub, layout, workspace, ws_name):
    """The folder is ws/<logical>-<node>, the rule of children.py:workspace; a folder the rule does not name is not read."""
    run_dir, projects = layout
    rec = _record(run_dir, _spawn(workspace))
    (_transcript(projects, run_dir, ws_name) / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    (_transcript(projects, run_dir) / "s0.jsonl").write_text(_usage_line("msg_9", "claude-haiku-5-5", 999, 9, 0, 0))   # the default folder: not this child's
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["in"] == 10, got["mod"]
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]


def test_a_symlinked_run_root_finds_the_folder_named_for_its_real_path(hub, tmp_path, layout):
    """macOS reaches /var through /private/var: the record path is the link, the folder is named for the real directory."""
    run_dir, projects = layout
    real_root = tmp_path / "ariel"
    link = tmp_path / "ariel_link"
    os.symlink(real_root, link)
    _record(run_dir, SPAWN)
    (_transcript(projects, run_dir) / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    rec_link = link / run_dir.relative_to(real_root) / "record.jsonl"
    got = hub.scan(rec_link)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]


def test_a_long_path_is_found_by_the_prefix_Claude_Code_kept(hub, tmp_path, homes):
    """Above 200 sanitized characters Claude Code cuts the name and adds a hash: the folder starts with the cut name."""
    run_dir = tmp_path / ("a" * 120) / ("b" * 120) / "runs" / RUN
    (run_dir / "ws" / DEFAULT_WS).mkdir(parents=True)
    rec = _record(run_dir, SPAWN)
    cwd = os.path.realpath(run_dir / "ws" / DEFAULT_WS)
    name = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    assert len(name) > 200, name
    folder = homes / (name[:200] + "-0a1b2c3d")
    folder.mkdir(parents=True)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]


def test_a_child_with_no_transcript_folder_is_named_once(hub, layout, capsys):
    """An accepted child with no outcome and no folder: one stderr line, however often it is asked for."""
    run_dir, _ = layout
    rec = _record(run_dir, _spawn("worktrees/review"))
    assert hub.child_transcript_usage(rec, KID, {"workspace": "worktrees/review", "tools": ["Write"]}) == {}
    hub.child_transcript_usage(rec, KID, {"workspace": "worktrees/review", "tools": ["Write"]})
    err = capsys.readouterr().err
    assert len([line for line in err.splitlines() if KID in line]) == 1, err


# --- (3) subagent transcripts ----------------------------------------------------------------------------------------------

def test_a_subagent_transcript_counts_and_a_repeated_message_counts_once(hub, layout):
    """Claude Code writes a Task subagent's transcript under the session folder: it counts, and a message id seen twice does not."""
    run_dir, projects = layout
    rec = _record(run_dir, SPAWN)
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    sub = folder / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.jsonl").write_text(_usage_line("msg_2", "claude-haiku-5-5", 20, 6, 0, 0)
                                       + _usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    haiku = got["mod"]["claude-haiku-5-5"]
    assert haiku["calls"] == 2, haiku
    assert haiku["in"] == 30, haiku


# --- (4) live runs follow their transcripts; per-file usage is cached ------------------------------------------------------

def test_a_live_run_follows_its_transcript_as_it_grows(hub, layout):
    """The record does not change while the child writes: the transcript's newest mtime is part of the cache key."""
    run_dir, projects = layout
    rec = _record(run_dir, SPAWN, verdict=False)
    folder = _transcript(projects, run_dir)
    s1 = folder / "s1.jsonl"
    s1.write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    os.utime(s1, (1792857700.0, 1792857700.0))
    assert hub.scan(rec)["mod"]["claude-haiku-5-5"]["calls"] == 1
    old_key = (str(s1), s1.stat().st_mtime, s1.stat().st_size)
    assert old_key in hub.USAGE_FILES
    with s1.open("a") as f:
        f.write(_usage_line("msg_2", "claude-haiku-5-5", 20, 6, 0, 0))
    os.utime(s1, (1792857900.0, 1792857900.0))
    assert hub.scan(rec)["mod"]["claude-haiku-5-5"]["calls"] == 2
    assert old_key not in hub.USAGE_FILES   # the file's older version is dropped from the cache


# --- the fix-round gap (#796): documented, not implemented ----------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="#796 gap: a fix round (entry 2) of a child whose entry 1 has an outcome is not counted when it is "
                   "killed before its own outcome, because the child is in done_kids and its transcript is never read. Documented, not implemented.")
def test_a_killed_fix_round_after_a_passed_entry_is_counted(hub, layout):
    run_dir, projects = layout
    outcome = {"ts": 1792857700.0, "event": "outcome", "node": KID, "entry": 1, "outcome": "pass", "model": "claude-haiku-5-5",
               "tokens_in": 999, "tokens_out": 9, "cache_read": 0, "spend": 0.5}
    rec = _record(run_dir, SPAWN, outcome)
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    (folder / "s2.jsonl").write_text(_usage_line("msg_fix", "claude-haiku-5-5", 50, 5, 0, 0))   # the killed fix round
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["in"] == 999 + 50, got["mod"]
