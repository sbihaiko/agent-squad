"""Guards for the run-level heartbeat (issue #72): a wedged run is killed, not merely logged.

The pass acts on two signals together, so the cases below are the three the issue names: both hold (kill), the CPU moves
inside one long call (no kill), the record moves while the CPU stands still (no kill). A stub run replaces the process
tree and the kill, so the suite calls no model and starts no process.
"""
import io
import os
import signal

import pytest

from squad import heartbeat, record

MIN = 60.0
T0 = 1000000.0


def rec(tmp_path, *events, mtime=None):
    r = record.Record(tmp_path / "record.jsonl")
    for kind, fields in events:
        r.append(kind, **fields)
    p = tmp_path / "record.jsonl"
    p.touch()                                              # an empty record is a run with nothing in it yet
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


PID = os.getpid()      # the pass measures a pid it believes alive, so the fixture uses the test's own


def start(node="a", pid=None):
    return ("call_start", dict(node=node, entry=1, item=None, session="s1", pid=pid or PID, limit=600, started_at=T0))


def verdict():
    return ("verdict", dict(verdict="failed", exit_code=1, reasons=["killed: heartbeat_stale"], remains=[], folded=[]))


def runlist(tmp_path, folder):
    f = tmp_path / "active-runs.txt"
    f.write_text(f"{folder}\n")
    return f


def one_row(pid=None, cpu=10.0, ppid=1):
    return [(pid or PID, ppid, cpu)]


def pass_once(tmp_path, events, rows, now, mtime, stamps=None, kills=None, **kw):
    folder = tmp_path / "run-x"
    folder.mkdir(exist_ok=True)
    rec(folder, *events, mtime=mtime)
    return heartbeat.scan(runlist(tmp_path, folder), tmp_path, stamps or tmp_path / "hb", now=now,
                          rows=rows, kill=(lambda pid, sig: (kills or []).append((pid, sig))),
                          mark=lambda folder, reason: (kills or []).append(("mark", str(folder), reason)) or True, **kw)


@pytest.mark.heavy
def test_a_flat_tree_and_a_frozen_record_kill_the_run_and_name_the_reason(tmp_path):
    """Case one: both signals hold. The kill goes through squad.killed, the one writer, with the reason the issue names."""
    folder = tmp_path / "run-x"
    folder.mkdir()
    rec(folder, start("router"), mtime=T0)
    stamps = tmp_path / "hb"
    stamps.mkdir()
    (stamps / "run-x").write_text(f"10.0 {T0}\n")          # the pass before this one read the same 10 CPU seconds
    marks, kills = [], []
    out = io.StringIO()
    got = heartbeat.scan(runlist(tmp_path, folder), tmp_path, stamps, now=T0 + 6 * MIN, rows=one_row(),
                         kill=lambda pid, sig: kills.append((pid, sig)),
                         mark=lambda f, r: marks.append((str(f), r)), out=out)
    assert [(m[1]) for m in marks] == ["killed: heartbeat_stale"]
    assert got and got[0]["node"] == "router" and got[0]["cpu_s"] == 10.0
    assert signal.SIGTERM in [s for _, s in kills]            # the tree is killed, not merely reported
    assert out.getvalue().startswith("HEARTBEAT KILL run-x: flat at 10s")


def test_a_long_call_whose_cpu_moves_is_left_alone(tmp_path):
    """Case two: a full test suite writes no record event for minutes while its process works. The CPU moved, so no kill."""
    rows = [(PID, 1, 400.0)]                                 # 400 CPU seconds against the 10 the last pass read
    assert pass_once(tmp_path, [start()], rows, T0 + 6 * MIN, T0) == []


def test_a_moving_record_whose_cpu_stands_still_is_left_alone(tmp_path):
    """Case three: a run waiting on a person or a lock burns no CPU while its record keeps moving."""
    assert pass_once(tmp_path, [start()], one_row(), T0 + 6 * MIN, T0 + 5 * MIN + 1) == []


def test_the_first_reading_is_a_baseline_and_never_a_kill(tmp_path):
    """One reading cannot say a tree is flat: a pass proves flatness against the reading before it."""
    assert pass_once(tmp_path, [start()], one_row(), T0 + 6 * MIN, T0) == []


def test_a_run_that_already_has_a_verdict_is_not_touched_and_its_stamp_goes(tmp_path):
    folder = tmp_path / "run-x"
    folder.mkdir()
    rec(folder, start(), verdict(), mtime=T0)
    stamps = tmp_path / "hb"
    stamps.mkdir()
    (stamps / "run-x").write_text(f"10.0 {T0}\n")
    got = heartbeat.scan(runlist(tmp_path, folder), tmp_path, stamps, now=T0 + 9 * MIN, rows=one_row(),
                         kill=lambda *a: None, mark=lambda f, r: True)
    assert got == [] and not (stamps / "run-x").exists()


def test_a_run_with_no_live_call_is_read_on_nothing(tmp_path):
    """The record names a process only in `call_start`, so a run between calls is left alone: silence is not evidence."""
    assert pass_once(tmp_path, [], one_row(), T0 + 9 * MIN, T0) == []


@pytest.mark.heavy
def test_the_watchdog_stands_down_at_the_half_minute_it_is_told_to(tmp_path):
    """HB_FLAT_MIN and HB_RECORD_MIN are the knobs heartbeat.sh already passes to its own host check."""
    os.environ["HB_FLAT_MIN"] = "1"
    os.environ["HB_RECORD_MIN"] = "1"
    try:
        # 2 minutes of standstill: too short for the default 5, long enough for the 1 this pass was told
        got = pass_once(tmp_path, [start()], one_row(), T0 + 2 * MIN, T0)
        assert got == []                                       # the stamp was written on the first reading
        folder = tmp_path / "run-x"
        (tmp_path / "hb" / "run-x").write_text(f"10.0 {T0}\n")
        assert heartbeat.scan(runlist(tmp_path, folder), tmp_path, tmp_path / "hb", now=T0 + 2 * MIN, rows=one_row(),
                              kill=lambda *a: None, mark=lambda f, r: True)
    finally:
        del os.environ["HB_FLAT_MIN"], os.environ["HB_RECORD_MIN"]


def test_the_cpu_time_ps_prints_is_read_in_every_shape_it_has():
    assert heartbeat.parse_time("00:07") == 7.0
    assert heartbeat.parse_time("01:02:03") == 3723.0
    assert heartbeat.parse_time("2-01:00:00") == 176400.0
    assert heartbeat.parse_time("") == 0.0


def test_the_tree_is_every_descendant_of_the_call_not_the_call_alone():
    rows = [(10, 1, 5.0), (11, 10, 6.0), (12, 11, 7.0), (99, 1, 1000.0)]   # 99 is a sibling, never this run's
    assert sorted(heartbeat.process_tree(10, rows)) == [10, 11, 12]
    assert heartbeat.cpu_seconds(10, rows) == 18.0


def test_a_verdict_written_by_the_kill_is_what_the_record_carries(tmp_path):
    """The reason the pass writes is the one the issue names, through squad.killed, the one writer of that verdict."""
    folder = tmp_path / "run-x"
    folder.mkdir()
    rec(folder, start())
    from squad import killed
    assert killed.mark_killed(folder, "killed: heartbeat_stale") is True
    last = record.read(folder / "record.jsonl")[-1]
    assert last["event"] == "verdict" and last["verdict"] == "failed" and "killed: heartbeat_stale" in last["reasons"]


def test_the_usage_line_is_the_last_line_of_the_docstring():
    assert heartbeat.main([]) == 2


def _proc(pid, ppid, pgid, start, command):
    from squad.procs import Proc
    return Proc(pid=pid, ppid=ppid, pgid=pgid, start=start, command=command)


def test_a_run_kill_signals_the_owned_groups_of_its_tool_shells_and_reports_survivors():
    """#1993: the owned set is the engine's descendants with their start times and groups. A shell's group is signalled, a
    sibling is never touched, and the process that survives KILL is named."""
    table = {
        10: _proc(10, 1, 10, 100.0, "python3 -m squad start x"),
        20: _proc(20, 10, 20, 200.0, "zsh -c sleep 540"),
        21: _proc(21, 20, 20, 201.0, "sleep 540"),
        30: _proc(30, 1, 30, 300.0, "zsh -c sleep 9"),
    }
    owned = heartbeat.owned_procs(10, table)
    assert owned == {10: (100.0, 10), 20: (200.0, 20), 21: (201.0, 20)}
    live = dict(table)
    sent, groups = [], []

    def kill(pid, sig):
        sent.append((pid, sig))
        if pid != 21:
            live.pop(pid, None)

    def killpg(pg, sig):
        groups.append((pg, sig))
        for pid in [p for p, q in live.items() if q.pgid == pg and p != 21]:
            live.pop(pid)

    survivors = heartbeat.kill_owned(owned, read=lambda: live, kill=kill, killpg=killpg, sleep=lambda s: None)
    assert (20, signal.SIGTERM) in groups and (20, signal.SIGKILL) in groups
    assert all(pid != 30 for pid, _ in sent) and all(pg != 30 for pg, _ in groups)
    assert survivors == [21]


def test_a_run_kill_never_signals_a_pid_whose_start_time_changed():
    """#1993: a pid that now names a different process (a reused pid) is not the owned process and gets no signal."""
    owned = {20: (200.0, 20)}
    reused = {20: _proc(20, 1, 20, 999.0, "vim notes.txt")}
    sent, groups = [], []
    heartbeat.kill_owned(owned, read=lambda: reused, kill=lambda pid, sig: sent.append((pid, sig)),
                         killpg=lambda pg, sig: groups.append((pg, sig)), sleep=lambda s: None)
    assert sent == [] and groups == []
