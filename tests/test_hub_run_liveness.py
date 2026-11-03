"""The hub counts a run live only while the run's own process lives (#1226).

The run's own process is the pid its record names in the last `call_start` or `gate_start` after the last `run_start` or
`resume` (squad/session.py and squad/runners/dispatch.py write `os.getpid()` of the engine). A pid the system gave to a
process that started after that call is a reused pid, so it is not the run's. A run with no call yet is a launch: it is
live through the launch window, and a run that waits for a slot writes `ceiling_wait` every minute, so it stays inside
that window. A process whose command line only names the run folder (its dashboard server, a shell in its worktree) never
keeps a run live. Only the last verdict after the last start or resume is final (ADR-0167).
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
OLD = 3 * 3600


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
    spec = importlib.util.spec_from_file_location("hub_run_liveness", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rec(pid=None, pid_at=None, verdict=None):
    return {"verdict": verdict, "reasons": [], "pid": pid, "pid_at": pid_at}


# ---------------------------------------------------------------- the rule: run_gone
def test_a_run_whose_own_process_lives_is_live_whatever_its_age(hub):
    assert hub.run_gone(rec(4242, 1000.0), OLD, {4242: 900.0}) is False


def test_a_run_whose_own_process_is_dead_is_gone_at_once(hub):
    """The defect: a dead run read live for 30 minutes."""
    assert hub.run_gone(rec(4242, 1000.0), 60, {1: 0.0}) is True


def test_a_reused_pid_is_not_the_run_s_process(hub):
    """A process that started after the run's call is another process with the same pid."""
    assert hub.run_gone(rec(4242, 1000.0), 60, {4242: 1500.0}) is True


def test_a_pid_whose_start_the_system_did_not_give_still_counts_by_its_pid(hub):
    assert hub.run_gone(rec(4242, 1000.0), 60, {4242: None}) is False


def test_a_process_that_only_names_the_run_folder_does_not_keep_it_live(hub):
    """The defect: any process whose argv named the run folder kept a dead run live for ever. The rule reads pids only, so
    a dashboard server or a shell in the run's worktree has no say."""
    assert hub.run_gone(rec(4242, 1000.0), OLD, {77: 10.0, 78: 10.0}) is True


def test_a_run_with_a_verdict_is_never_gone(hub):
    assert hub.run_gone(rec(4242, 1000.0, verdict="failed"), OLD, {1: 0.0}) is False


def test_a_run_with_no_call_yet_is_live_through_its_launch_window(hub):
    assert hub.run_gone(rec(), 10, {1: 0.0}) is False
    assert hub.run_gone(rec(), hub.RUN_LAUNCH_GRACE - 1, {1: 0.0}) is False


def test_a_run_with_no_call_past_its_launch_window_is_gone(hub):
    assert hub.run_gone(rec(), hub.RUN_LAUNCH_GRACE + 1, {1: 0.0}) is True


def test_no_process_reading_judges_nothing(hub):
    """A `ps` that answered nothing is no evidence: the run is not called gone on it."""
    assert hub.run_gone(rec(4242, 1000.0), OLD, {}) is False
    assert hub.run_gone(rec(4242, 1000.0), OLD, None) is False


# ---------------------------------------------------------------- the record: which pid is the run's own
def write(folder, events, mtime=None):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "record.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in events))
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def call(pid, at, ev="call_start"):
    return {"ts": at, "event": ev, "node": "n", "entry": 1, "item": None, "session": "s", "pid": pid, "limit": None, "started_at": at}


def test_the_record_names_the_pid_of_its_last_call(hub, tmp_path):
    t = time.time() - 600
    f = write(tmp_path / "runs" / "run-a", [{"ts": t, "event": "run_start"}, call(11, t + 1), call(12, t + 2, "gate_start")])
    r = hub.scan(f)
    assert (r["pid"], r["pid_at"]) == (12, t + 2)


def test_a_pid_from_before_a_resume_is_not_the_resumed_run_s(hub, tmp_path):
    t = time.time() - 600
    f = write(tmp_path / "runs" / "run-b", [{"ts": t, "event": "run_start"}, call(11, t + 1), {"ts": t + 5, "event": "resume"}])
    assert hub.scan(f)["pid"] is None


def test_a_waiting_run_with_a_live_process_stays_live(hub, tmp_path):
    """A run waiting for a gate slot: its process lives, and its record moves with `ceiling_wait`."""
    t = time.time() - 600
    f = write(tmp_path / "runs" / "run-c", [{"ts": t, "event": "run_start"}, call(4242, t + 1),
                                           {"ts": t + 60, "event": "ceiling_wait", "ceiling": "caps.gates", "cap": 1, "live": 1, "holder": "x"}])
    r = hub.scan(f)
    assert hub.run_gone(r, OLD, {4242: t - 5}) is False


def test_a_waiting_run_before_its_first_call_stays_live(hub, tmp_path):
    t = time.time() - 30
    f = write(tmp_path / "runs" / "run-d", [{"ts": t, "event": "run_start"},
                                           {"ts": t + 20, "event": "ceiling_wait", "ceiling": "caps.runs", "cap": 6, "live": 6, "holder": "x"}])
    assert hub.run_gone(hub.scan(f), 10, {1: 0.0}) is False


# ---------------------------------------------------------------- the process reading
def test_the_process_reading_gives_each_pid_its_start(hub):
    out = ("  101 Tue Nov  3 10:00:00 2026\n"
           "  102 Tue Nov  3 10:05:07 2026\n"
           "  103 not a date\n"
           "garbage\n")
    procs = hub.proc_starts(out)
    assert procs[101] == time.mktime(time.strptime("Tue Nov  3 10:00:00 2026", "%a %b %d %H:%M:%S %Y"))
    assert procs[102] - procs[101] == 307
    assert procs[103] is None
    assert set(procs) == {101, 102, 103}


def test_the_live_reading_names_this_process_with_a_start_before_now(hub):
    procs = hub.read_procs()
    assert procs.get(os.getpid()) is not None and procs[os.getpid()] <= time.time()


# ---------------------------------------------------------------- the loop switch: loop_run_live
SQUAD = 99001


def rows():
    return [(SQUAD, 1, "uv run squad start --target /co x")]


def test_a_run_resumed_after_its_verdict_is_live_again(hub, tmp_path):
    """The defect: any verdict in the record read final, even one a later resume replaced (ADR-0167)."""
    t = time.time() - 600
    folder = tmp_path / "runs" / "run-r"
    write(folder, [{"ts": t, "event": "run_start"}, call(99000, t + 1),
                   {"ts": t + 2, "event": "verdict", "verdict": "failed", "reasons": [], "exit_code": 1},
                   {"ts": t + 3, "event": "resume"}, call(SQUAD, t + 4)])
    assert hub.loop_run_live(str(folder), rows()) is True


def test_a_resumed_run_before_its_first_call_is_a_launch(hub, tmp_path):
    t = time.time() - 600
    folder = tmp_path / "runs" / "run-s"
    write(folder, [{"ts": t, "event": "run_start"}, call(SQUAD, t + 1),
                   {"ts": t + 2, "event": "verdict", "verdict": "failed", "reasons": [], "exit_code": 1},
                   {"ts": t + 3, "event": "resume"}])
    assert hub.loop_run_live(str(folder), rows()) is True   # the record moved just now: a launch
    write(folder, [{"ts": t, "event": "run_start"}, call(SQUAD, t + 1), {"ts": t + 3, "event": "resume"}], mtime=t)
    assert hub.loop_run_live(str(folder), rows()) is False  # the pid before the resume is not the resumed run's


def test_a_verdict_after_the_last_start_is_final(hub, tmp_path):
    t = time.time() - 600
    folder = tmp_path / "runs" / "run-t"
    write(folder, [{"ts": t, "event": "run_start"}, {"ts": t + 1, "event": "resume"}, call(SQUAD, t + 2),
                   {"ts": t + 3, "event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0}])
    assert hub.loop_run_live(str(folder), rows()) is False


# ---------------------------------------------------------------- the hub reads the ceiling, never writes it (ADR-0162)
def test_reading_the_waits_removes_no_mark(hub, tmp_path):
    sys.path.insert(0, str(ROOT))
    try:
        from squad import caps as C
    finally:
        sys.path.remove(str(ROOT))
    folder = tmp_path / "caps"
    (folder / "waiting").mkdir(parents=True)
    (folder / "runs").mkdir()
    dead = folder / "waiting" / "runs-x.json"
    dead.write_text(json.dumps({"ceiling": "caps.runs", "holder": "x", "pid": 2 ** 22 + 7}))
    live = folder / "waiting" / "runs-y.json"
    live.write_text(json.dumps({"ceiling": "caps.runs", "holder": "y", "pid": os.getpid()}))
    lease = folder / "runs" / "z.json"
    lease.write_text(json.dumps({"holder": "z", "pid": 2 ** 22 + 7, "since": 0}))
    got = hub.caps_waiting(C, folder)
    assert got == [{"ceiling": "caps.runs", "holder": "y"}]
    assert dead.exists() and lease.exists(), "a read of the waits removed a mark or a lease"
