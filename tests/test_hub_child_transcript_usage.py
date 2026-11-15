"""The hub counts a child session's tokens when its outcome never reached the record (#796).

A child that is killed, or still running, writes no `outcome` event, so the run's record carries no tokens for it. Its
session transcript does: Claude Code writes it under ~/.claude/projects, in a folder named for the child's working
directory (the run folder's ws/<logical>-<node>, named as squad/runners/children.py:workspace names it). The hub reads it for
that child only. A child whose outcome is in the record is counted from the record alone, so no tokens are counted twice.
A transcript repeats one message on several lines, and a subagent's transcript sits in a subfolder; each message id counts once.
"""
import datetime as dt
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
    monkeypatch.setattr(hub, "FOLDERS", {})
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


def _usage_line(msg_id, model, inp, out, cr, cc, ts=None):
    msg = {"id": msg_id, "model": model,
           "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}}
    line = {"type": "assistant", "message": msg}
    if ts is not None:   # Claude Code stamps each line; a message after the child's last outcome is the one a killed fix round wrote
        line["timestamp"] = dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps(line) + "\n"


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


def test_a_finished_run_cached_under_v16_is_scanned_again(hub, layout):
    """#802, #1229: the scan cache marker is v19, so a finished run cached under v16 is read again, with its fix rounds and shared folders."""
    run_dir, _ = layout
    rec = _record(run_dir, SPAWN)
    stale = {"v16": 1, "sentinel": True}
    hub.hist[str(rec)] = stale
    got = hub.scan(rec)
    assert got is not stale and "sentinel" not in got, got
    st = rec.stat()
    fresh = {"v19": 1, "rec": [st.st_mtime_ns, st.st_size], "sentinel": True}
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


# --- (#802) the reviewer's points on the transcript reader -------------------------------------------------------------

def _long_run(tmp_path):
    """A run whose ws/ child folder sits past the 200-character project name cut; returns (run dir, the cut name prefix)."""
    run_dir = tmp_path / ("a" * 120) / ("b" * 120) / "runs" / RUN
    (run_dir / "ws" / DEFAULT_WS).mkdir(parents=True)
    name = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(run_dir / "ws" / DEFAULT_WS))
    assert len(name) > 200, name
    return run_dir, name[:200]


def test_a_read_only_child_is_found_in_the_workspace_its_template_names(hub, tmp_path, layout):
    """A child that may not write runs in its template's workspace, named under the run as ws/<logical> (children.py:workspace)."""
    run_dir, projects = layout
    review = run_dir / "ws" / "worktrees_review"
    review.mkdir(parents=True)
    rec = _record(run_dir, _spawn("worktrees/review", tools=("Read",)))
    (_folder(projects, review) / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]


def test_a_read_only_child_in_the_shared_target_is_not_counted_and_is_named(hub, tmp_path, layout, capsys):
    """The target is the owner's checkout, shared by every child: it is skipped and named on stderr, never counted."""
    run_dir, projects = layout
    rec = _record(run_dir, _spawn(None, tools=("Read",)))
    (_folder(projects, tmp_path / "ariel") / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert "claude-haiku-5-5" not in got["mod"], got["mod"]
    assert "shared target" in capsys.readouterr().err


def test_two_long_path_children_that_share_a_folder_are_not_counted(hub, tmp_path, homes):
    """Two children whose names differ past the 200-character cut map to one folder: it is no child's alone, so neither counts."""
    run_dir, prefix = _long_run(tmp_path)
    (run_dir / "ws" / "worktrees_child-route.router.2").mkdir(parents=True)
    rec = _record(run_dir, _spawn("worktrees/child", kid="route.router.1"), _spawn("worktrees/child", kid="route.router.2"))
    folder = homes / (prefix + "-0a1b2c3d")
    folder.mkdir(parents=True)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert "claude-haiku-5-5" not in got["mod"], got["mod"]


def test_a_long_path_matching_two_folders_is_not_matched(hub, tmp_path, homes):
    """Two folders start with the cut name: the child's folder is none of them."""
    run_dir, prefix = _long_run(tmp_path)
    rec = _record(run_dir, SPAWN)
    for suffix in ("-0a1b2c3d", "-9f8e7d6c"):
        folder = homes / (prefix + suffix)
        folder.mkdir(parents=True)
        (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert "claude-haiku-5-5" not in got["mod"], got["mod"]


def test_a_child_without_a_definition_and_two_folders_named_for_it_is_not_matched(hub, layout):
    """No definition: the folder must be the one named for the child. Two candidates are ambiguous, so neither is read."""
    run_dir, projects = layout
    (run_dir / "ws" / "worktrees_child-router.1").mkdir()
    (run_dir / "ws" / "review-router.1").mkdir()
    rec = _record(run_dir, {"ts": 1792857611.0, "event": "spawn_accepted", "child": "router.1", "parent": "router", "depth": 1})
    for ws_name in ("worktrees_child-router.1", "review-router.1"):
        (_transcript(projects, run_dir, ws_name) / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert "claude-haiku-5-5" not in got["mod"], got["mod"]


def test_a_transcript_that_vanishes_during_the_scan_does_not_fail_it(hub, layout, monkeypatch):
    """exists() is true, then the file is gone before stat(): the scan skips it and does not raise."""
    run_dir, projects = layout
    rec = _record(run_dir, SPAWN)
    f = _transcript(projects, run_dir) / "s1.jsonl"
    f.write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    real_stat, real_exists = os.stat, os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: True if os.fspath(p) == str(f) else real_exists(p))
    def vanished(p, *a, **k):
        if os.fspath(p) == str(f):
            raise FileNotFoundError(p)
        return real_stat(p, *a, **k)
    monkeypatch.setattr(os, "stat", vanished)
    got = hub.scan(rec)
    assert "claude-haiku-5-5" not in got["mod"], got["mod"]


def test_a_reader_after_a_writer_counts_the_writers_worktree_once(hub, layout):
    """A reader that runs after a writer reads the writer's worktree (children.py:workspace): one folder, its messages once."""
    run_dir, projects = layout
    reader = _spawn("worktrees/review", tools=("Read",), kid="route.router.2")
    reader["definition"]["after"] = [KID]
    rec = _record(run_dir, _spawn("worktrees/child"), reader)
    (_transcript(projects, run_dir) / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]
    assert got["mod"]["claude-haiku-5-5"]["in"] == 10, got["mod"]


# --- the fix-round gap (#796), closed by #802 -----------------------------------------------------------------------------

def test_a_killed_fix_round_after_a_passed_entry_is_counted(hub, layout):
    """Entry 1 passed (its outcome is in the record). Its fix round (entry 2) was killed before its own outcome: the messages
    after entry 1's outcome count, the messages before it do not (they are in the record already)."""
    run_dir, projects = layout
    outcome = {"ts": 1792857700.0, "event": "outcome", "node": KID, "entry": 1, "outcome": "pass", "model": "claude-haiku-5-5",
               "tokens_in": 999, "tokens_out": 9, "cache_read": 0, "spend": 0.5}
    rec = _record(run_dir, SPAWN, outcome)
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0, ts=1792857650.0))
    (folder / "s2.jsonl").write_text(_usage_line("msg_fix", "claude-haiku-5-5", 50, 5, 0, 0, ts=1792857800.0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["in"] == 999 + 50, got["mod"]
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 2, got["mod"]   # the recorded outcome, and the fix round's one message


# --- (#802, review round 2) read-only children resolve under the run's ws/, not the checkout ---------------------------

def test_a_read_only_child_is_found_under_the_run_ws_not_the_checkout(hub, tmp_path, layout):
    """children.py:workspace and engine.workspace_path name a read-only child's folder as <run>/ws/<logical>; a folder of the same
    name in the checkout is no child's, so it is not read."""
    run_dir, projects = layout
    rec = _record(run_dir, _spawn("worktrees/review", tools=("Read",)))
    (run_dir / "ws" / ("worktrees_review")).mkdir()
    (_transcript(projects, run_dir, "worktrees_review") / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    decoy = tmp_path / "ariel" / "worktrees" / "review"
    decoy.mkdir(parents=True)
    (_folder(projects, decoy) / "s9.jsonl").write_text(_usage_line("msg_9", "claude-haiku-5-5", 999, 9, 0, 0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]
    assert got["mod"]["claude-haiku-5-5"]["in"] == 10, got["mod"]


# --- (#802, review round 2) a folder shared by a writer and a reader is counted once, from the latest outcome ---------------

def _outcome(node, ts, entry=1):
    # a recorded outcome of another model, with no tokens: the haiku bucket then holds only what the transcript adds
    v = {"ts": ts, "event": "outcome", "node": node, "entry": entry, "outcome": "pass", "model": "claude-sonnet-5-5",
         "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "spend": 0.0}
    return v


def test_a_folder_shared_by_a_writer_and_its_reader_counts_each_message_after_the_latest_outcome(hub, layout):
    """The writer's outcome (t=650) and the reader's (t=750) both sit in the record. A message before 750 is already recorded by one
    of them, so it is not counted again; only the message after 750 is new. The folder is read once."""
    run_dir, projects = layout
    reader = _spawn("worktrees/review", tools=("Read",), kid="route.router.2")
    reader["definition"]["after"] = [KID]
    rec = _record(run_dir, _spawn("worktrees/child"), reader, _outcome(KID, 1792857650.0), _outcome("route.router.2", 1792857750.0))
    folder = _transcript(projects, run_dir)
    (folder / "s1.jsonl").write_text(_usage_line("msg_early", "claude-haiku-5-5", 10, 5, 0, 0, ts=1792857600.0)
                                     + _usage_line("msg_mid", "claude-haiku-5-5", 20, 6, 0, 0, ts=1792857700.0)
                                     + _usage_line("msg_late", "claude-haiku-5-5", 30, 7, 0, 0, ts=1792857800.0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]
    assert got["mod"]["claude-haiku-5-5"]["in"] == 30, got["mod"]


def test_a_non_numeric_outcome_time_does_not_fail_the_scan(hub, layout):
    """A record's outcome ts that is not a number (a string) must not raise TypeError when the folder's cutoff is taken."""
    run_dir, projects = layout
    reader = _spawn("worktrees/review", tools=("Read",), kid="route.router.2")
    reader["definition"]["after"] = [KID]
    rec = _record(run_dir, _spawn("worktrees/child"), reader, _outcome(KID, "not-a-time"), _outcome("route.router.2", 1792857750.0))
    (_transcript(projects, run_dir) / "s1.jsonl").write_text(_usage_line("msg_late", "claude-haiku-5-5", 30, 7, 0, 0, ts=1792857800.0))
    got = hub.scan(rec)
    assert got["mod"]["claude-haiku-5-5"]["calls"] == 1, got["mod"]


# --- (#802, review round 2) folder resolution is cached per (run, child) ----------------------------------------------------

def test_a_child_folder_is_resolved_once_per_run_and_child(hub, layout, monkeypatch):
    """The folder is worked out once for a child that has one; a second ask does not walk the filesystem again."""
    run_dir, projects = layout
    rec = _record(run_dir, SPAWN)
    (_transcript(projects, run_dir) / "s1.jsonl").write_text(_usage_line("msg_1", "claude-haiku-5-5", 10, 5, 0, 0))
    calls = []
    real = hub.transcript_folder
    monkeypatch.setattr(hub, "transcript_folder", lambda cwd: calls.append(cwd) or real(cwd))
    hub.child_transcript_usage(rec, KID, {"workspace": "worktrees/child", "tools": ["Write"]})
    hub.child_transcript_usage(rec, KID, {"workspace": "worktrees/child", "tools": ["Write"]})
    assert len(calls) == 1, calls
