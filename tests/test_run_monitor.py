"""Guards for ticket #184: a run-scoped monitor starts with the run, logs each finding once, and never changes the run."""
import json
import os

from squad import defaults, launch, monitor, record
from squad.adapter import ScriptedAdapter
from squad.graph import from_doc
from tests.flows import agent, doc

CALL = dict(node="a", entry=1, item=None, session="s1", pid=os.getpid(), limit=10, started_at=1000.0)
END = dict(node="a", entry=1, outcome="pass", reasons=[], artifact=None, spend=0.1, turns=1, cap=1.0, elapsed=1.0)


class Ticker:
    """Stands in for the timer thread: keeps the watcher so a test steps it with a fake clock."""
    def __init__(self, fail=False):
        self.watcher, self.seconds, self.fail = None, None, fail

    def __call__(self, watcher, seconds):
        if self.fail:
            raise RuntimeError("no thread")
        self.watcher, self.seconds = watcher, seconds


def started(tmp_path, ticker, defaults_file=defaults.PATH, **kw):
    g = from_doc(doc([agent("a", output_key="x")], ["a"]))
    return launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": {"output": {"x": 1}}}), defaults=defaults_file,
                         opener=lambda *a, **k: None, ticker=ticker, **kw)


def rec(tmp_path):
    return record.Record(tmp_path / "r" / "record.jsonl")


def lines(tmp_path):
    p = tmp_path / "r" / "monitor.log"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_launch_starts_the_monitor_with_the_default_step(tmp_path):
    t = Ticker()
    started(tmp_path, t)
    assert t.watcher is not None and t.seconds == 30


def test_a_stuck_call_is_one_line_however_many_looks(tmp_path, capsys):
    t = Ticker()
    started(tmp_path, t)
    rec(tmp_path).append("call_start", **CALL)
    assert [t.watcher.step(now) for now in (1015.0, 1020.0, 1025.0)] == [True] * 3
    assert [x["finding"] for x in lines(tmp_path)] == ["stuck"]
    assert capsys.readouterr().out.count("monitor:") == 1


def test_a_dead_process_is_one_abandoned_line(tmp_path):
    t = Ticker()
    started(tmp_path, t)
    t.watcher.alive = lambda pid: False
    rec(tmp_path).append("call_start", **CALL)
    t.watcher.step(1001.0)
    t.watcher.step(1002.0)
    assert [x["finding"] for x in lines(tmp_path)] == ["abandoned"]


def test_a_deadline_that_did_not_fire_is_one_deadline_missed_line(tmp_path):
    t = Ticker()
    started(tmp_path, t)
    rec(tmp_path).append("call_start", **CALL)
    for now in (1015.0, 1100.0, 1200.0):
        t.watcher.step(now)
    assert [x["finding"] for x in lines(tmp_path)] == ["stuck", "deadline_missed"]


def test_a_healthy_run_writes_nothing_and_a_verdict_ends_the_monitor(tmp_path):
    t = Ticker()
    started(tmp_path, t)
    r = rec(tmp_path)
    r.append("call_start", **CALL)
    assert t.watcher.step(1005.0) is True and lines(tmp_path) == []
    r.append("outcome", **END)
    r.append("verdict", verdict="pass", exit_code=0, remains=[], folded={}, reasons=[])
    assert t.watcher.step(1006.0) is False
    assert [x["finding"] for x in lines(tmp_path)] == ["ended"]


def test_monitor_off_starts_no_timer_and_writes_no_log(tmp_path):
    t = Ticker()
    started(tmp_path, t, watch=False)
    off = tmp_path / "off.yaml"
    off.write_text(defaults.PATH.read_text().replace("monitor: true", "monitor: false"))
    started(tmp_path / "x", t, off)
    assert t.watcher is None and not (tmp_path / "r" / "monitor.log").exists()


def test_a_monitor_that_cannot_start_is_a_warning_and_the_run_goes_on(tmp_path, capsys):
    assert started(tmp_path, Ticker(fail=True)) is not None
    assert "the monitor did not start" in capsys.readouterr().err


def test_the_step_is_in_range_and_the_switch_is_a_bool():
    cfg = defaults.load()
    cfg["watch"]["monitor_seconds"] = 4
    assert "watch.monitor_seconds" in defaults.check(cfg)
    cfg["watch"].update(monitor_seconds=600, monitor="yes")
    assert defaults.check(cfg) == ["watch.monitor"]


def test_the_timer_thread_steps_until_the_watcher_says_stop():
    steps = []

    class W:
        def step(self, now):
            steps.append(now)
            return len(steps) < 3
    monitor.run_timer(W(), 5, clock=lambda: 7.0, wait=lambda s: None).join(2)
    assert steps == [7.0, 7.0, 7.0]


def at(r, event, ts, **f):
    r.append(event, ts=ts, **f)


def test_a_still_record_with_no_open_call_is_one_no_progress_line_per_stall(tmp_path):
    t = Ticker()
    started(tmp_path, t)
    r = rec(tmp_path)
    last = 5000.0
    at(r, "call_start", last, **{**CALL, "started_at": last})
    at(r, "outcome", last, **END)
    assert t.watcher.step(last + 100) is True and lines(tmp_path) == []
    t.watcher.step(last + monitor.NO_PROGRESS_SECONDS + 1)
    t.watcher.step(last + monitor.NO_PROGRESS_SECONDS + 50)
    assert [x["finding"] for x in lines(tmp_path)] == ["no_progress"]
    at(r, "call_start", last + 1000, **{**CALL, "started_at": last + 1000})
    t.watcher.step(last + 2000)     # a call is open: not a stall (it is stuck, a different finding)
    assert [x["finding"] for x in lines(tmp_path)] == ["no_progress", "stuck", "deadline_missed"]


def test_a_pause_is_one_held_line_with_the_node_and_the_run_is_not_stalled(tmp_path):
    t = Ticker()
    started(tmp_path, t)
    at(rec(tmp_path), "paused", 1000.0, node="a", scope="run", held=["a#1"], completed=[])
    for now in (1001.0, 9000.0):
        assert t.watcher.step(now) is True
    got = lines(tmp_path)
    assert [x["finding"] for x in got] == ["held"] and got[0]["node"] == "a" and got[0]["held"] == ["a#1"]


def test_a_verdict_is_one_ended_line_and_the_monitor_stops(tmp_path, capsys):
    t = Ticker()
    started(tmp_path, t)
    at(rec(tmp_path), "verdict", 1000.0, verdict="failed", exit_code=1, remains=[], folded={}, reasons=["deadline"])
    assert t.watcher.step(1001.0) is False
    got = lines(tmp_path)
    assert [x["finding"] for x in got] == ["ended"] and got[0]["verdict"] == "failed" and got[0]["reasons"] == ["deadline"]
    assert "ended" in capsys.readouterr().out
