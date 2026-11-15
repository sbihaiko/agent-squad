"""The hub's Loop tab (#1743): the loop of this profile as a hot-spot tree, with its own switch, `CPU sampling`.

The fold from process-table readings to the tree is a pure module beside the hub (ADR-0137), so its tests feed readings in
and read no real process table. The route is tested over HTTP: off is the default and reads nothing, a switch write is
audited, and a tailnet write is refused without its own grant, `profiler_write`. The page's renderer runs in node.
"""
import http.client
import importlib.util
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
TN_HOST = "box.tail1.ts.net"
S = "/Users/o/.claude/skills/squad-goal/scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def prof():
    return load("hub_profiler_under_test", SCRIPTS / "hub_profiler.py")


# ---------------------------------------------------------------- the fold (pure)
# One reading is {(pid, start time): (ppid, cpu seconds, command)}. A scope names the loop of one profile.
CO = "/Users/o/co"


def rd(rows, start=1.0):
    return {(pid, start): row for pid, row in rows.items()}


@pytest.fixture(scope="module")
def scope(prof):
    return prof.Scope(10, (S + "/", CO + "/runs/"))


FIRST = rd({
    1: (0, 9.0, "/sbin/launchd"),
    10: (1, 5.0, f"/bin/bash {S}/watchd.sh supervise"),
    11: (10, 2.0, f"bash {S}/watch.sh"),
    20: (1, 4.0, "bash /var/folders/ab/cd/T/tmp.Xy12/watch.sh"),
    30: (1, 1.0, "/Applications/Foo Bar.app/Contents/MacOS/Foo Bar --type=gpu"),
})
SECOND = rd({
    1: (0, 9.0, "/sbin/launchd"),
    10: (1, 5.0, f"/bin/bash {S}/watchd.sh supervise"),
    11: (10, 3.0, f"bash {S}/watch.sh"),
    12: (11, 0.5, "sed -n 1p /x/y"),                      # a short-lived helper: it is its driver's own time
    13: (11, 0.25, f"python3 {S}/janitor.py"),
    20: (1, 6.0, "bash /var/folders/ab/cd/T/tmp.Xy12/watch.sh"),
    21: (20, 0.0, "sleep 1"),
    30: (1, 1.5, "/Applications/Foo Bar.app/Contents/MacOS/Foo Bar --type=gpu"),
})


def node(tree, *names):
    for name in names:
        tree = next(c for c in tree["children"] if c["name"] == name)
    return tree


def names(tree):
    return [c["name"] for c in tree["children"]]


def test_a_process_whose_ancestry_names_a_driver_goes_under_that_driver(prof, scope):
    tree = prof.tree(prof.fold(FIRST, SECOND, scope))
    watch = node(tree, "watchd.sh", "watch.sh")
    assert watch["kind"] == "driver"
    assert watch["self_s"] == pytest.approx(1.5)          # its own second, plus the helper it started
    assert node(watch, "janitor.py")["self_s"] == pytest.approx(0.25)
    assert watch["total_s"] == pytest.approx(1.75)
    assert node(tree, "watchd.sh")["self_s"] == 0 and node(tree, "watchd.sh")["total_s"] == pytest.approx(1.75)


def test_a_short_lived_process_seen_once_counts_as_one_event(prof, scope):
    tree = prof.tree(prof.fold(FIRST, SECOND, scope))
    watch = node(tree, "watchd.sh", "watch.sh")
    assert watch["events"] == 1
    assert node(watch, "janitor.py")["events"] == 1
    assert tree["events"] == 0 and tree["total_events"] == 3


def test_the_first_reading_alone_is_a_baseline_with_no_event(prof, scope):
    acc = prof.fold(FIRST, FIRST, scope)
    tree = prof.tree(acc)
    assert tree["total_s"] == 0 and tree["total_events"] == 0 and tree["children"] == []
    assert prof.context(acc) == {"name": "outside the loop", "total_s": 0, "events": 0, "children": []}


def test_an_orphan_under_a_temp_folder_stays_in_the_tree_and_is_named(prof, scope):
    tree = prof.tree(prof.fold(FIRST, SECOND, scope))
    orphan = node(tree, "orphan watch.sh (temp folder)")
    assert orphan["kind"] == "orphan"
    assert orphan["self_s"] == pytest.approx(2.0) and orphan["events"] == 1


def test_the_same_script_under_a_live_parent_is_no_orphan(prof, scope):
    a = rd({5: (1, 0.0, f"bash {S}/test-watch-cycle.sh"), 6: (5, 1.0, "bash /private/tmp/x/watch.sh")})
    b = rd({5: (1, 0.0, f"bash {S}/test-watch-cycle.sh"), 6: (5, 2.0, "bash /private/tmp/x/watch.sh")})
    tree = prof.tree(prof.fold(a, b, scope))
    assert node(tree, "test-watch-cycle.sh")["kind"] == "suite"
    assert node(tree, "test-watch-cycle.sh", "watch.sh")["self_s"] == pytest.approx(1.0)


def test_every_process_outside_the_loop_counts_in_the_one_context_row(prof, scope):
    """The context row stands apart from the tree: the tree's total is the loop's alone."""
    a = {**FIRST, **rd({40: (1, 0.0, "/usr/bin/vim /tmp/watch.sh"), 41: (1, 0.0, "/x/.venv/bin/python -m pytest -q")})}
    b = {**SECOND, **rd({40: (1, 3.0, "/usr/bin/vim /tmp/watch.sh"), 41: (1, 1.0, "/x/.venv/bin/python -m pytest -q")})}
    acc = prof.fold(a, b, scope)
    tree = prof.tree(acc)
    assert names(tree) == ["orphan watch.sh (temp folder)", "watchd.sh"]
    assert tree["total_s"] == pytest.approx(3.75) and tree["share"] == 1
    ctx = prof.context(acc)
    assert ctx["name"] == "outside the loop" and ctx["total_s"] == pytest.approx(4.5)
    assert [(c["name"], c["total_s"]) for c in ctx["children"]] == [("vim", 3.0), ("python", 1.0), ("Foo Bar", 0.5)]


def test_the_root_is_the_supervisor_of_this_profile_and_its_descendants(prof, scope):
    """Another profile's supervisor runs the same scripts: it is told apart by its pid and counts in the context row."""
    a = rd({10: (1, 0.0, f"bash {S}/watchd.sh supervise"), 11: (10, 0.0, "/usr/bin/git fetch"),
            50: (1, 0.0, f"bash {S}/watchd.sh supervise"), 51: (50, 0.0, f"bash {S}/watch.sh")})
    b = rd({10: (1, 1.0, f"bash {S}/watchd.sh supervise"), 11: (10, 2.0, "/usr/bin/git fetch"),
            50: (1, 4.0, f"bash {S}/watchd.sh supervise"), 51: (50, 8.0, f"bash {S}/watch.sh")})
    acc = prof.fold(a, b, scope)
    tree = prof.tree(acc)
    assert names(tree) == ["watchd.sh"] and node(tree, "watchd.sh")["self_s"] == pytest.approx(3.0)
    assert [(c["name"], c["total_s"]) for c in prof.context(acc)["children"]] == [("another loop", 12.0)]


def test_a_pid_file_that_names_another_program_roots_nothing(prof):
    """A stale pid file can name a pid the system gave to another program."""
    a = rd({10: (1, 0.0, "/usr/bin/vim notes.txt"), 11: (10, 0.0, "sleep 5")})
    b = rd({10: (1, 1.0, "/usr/bin/vim notes.txt"), 11: (10, 1.0, "sleep 5")})
    acc = prof.fold(a, b, prof.Scope(10, (S + "/",)))
    assert prof.tree(acc)["children"] == [] and prof.context(acc)["total_s"] == pytest.approx(2.0)


def test_a_command_that_names_a_run_folder_of_this_profile_is_in_the_loop(prof, scope):
    engine = "/x/.venv/bin/python -m squad start --graph {}/runs/run-1/graph.json"
    a = rd({60: (1, 0.0, engine.format(CO)), 61: (1, 0.0, engine.format("/Users/o/other")), 62: (1, 0.0, f"node {CO}/runs/run-1/x.js")})
    b = rd({60: (1, 2.0, engine.format(CO)), 61: (1, 5.0, engine.format("/Users/o/other")), 62: (1, 1.0, f"node {CO}/runs/run-1/x.js")})
    acc = prof.fold(a, b, scope)
    tree = prof.tree(acc)
    assert names(tree) == ["engine", "unknown"], "a program of the loop that cannot be told is unknown"
    assert node(tree, "engine")["total_s"] == pytest.approx(2.0) and node(tree, "unknown")["kind"] == "unknown"
    assert prof.context(acc)["total_s"] == pytest.approx(5.0)


@pytest.mark.parametrize("command,name,kind", [
    ("/x/_bundled/claude --output-format stream-json", "child session", "run"),
    ("/x/.venv/bin/python -m squad start --graph g.json", "engine", "run"),
    (f"bash {S}/gate.sh", "gate.sh", "gate"),
    (f"bash {S}/fast-gate.sh --tree /x", "fast-gate.sh", "gate"),
    ("/x/.venv/bin/python -m pytest -n auto", "pytest", "gate"),
    (f"bash {S}/test-janitor-ccdefer.sh", "test-janitor-ccdefer.sh", "suite"),
    (f"/bin/bash {S}/autofrontier.sh", "autofrontier.sh", "driver"),
    ("bash /private/tmp/t/fastloop.sh", "fastloop.sh", "driver"),
    ("vim notes.txt", None, None),
    # the program a command runs, never an argument
    ("vim /tmp/watch.sh", None, None),
    ("bash /x/gate.sh pytest", "gate.sh", "gate"),
    (f"cat {S}/watch.sh", None, None),
    ("grep -rn pytest tests", None, None),
    ("less /x/_bundled/claude.log", None, None),
    ("python3 -c 'import pytest'", None, None),
    ("python3 /x/notes.py -m pytest", None, None),
    ("bash -c 'echo watch.sh'", None, None),
    # a wrapper is skipped
    (f"env SG_STATE=/x A=1 bash {S}/watch.sh", "watch.sh", "driver"),
    ("uv run --with pytest-xdist pytest -q", "pytest", "gate"),
    ("timeout -k 5 900 bash /x/gate.sh --tree /x", "gate.sh", "gate"),
    (f"nohup bash {S}/watchd.sh run", "watchd.sh", "driver"),
    ("sandbox-exec -f /x/child.sb /x/_bundled/claude -p", "child session", "run"),
    (f"rtk proxy bash {S}/board.sh --add 5", "board.sh", "driver"),
    (f"/Library/Python.app/Contents/MacOS/Python -I {S}/janitor.py", "janitor.py", "driver"),
    ("/x/.venv/bin/squad resume runs/run-1", "engine", "run"),
    ("/x/.venv/bin/python /x/.venv/bin/pytest -q", "pytest", "gate"),
    (f"bash {S}/janitor.sh", "janitor.sh", "driver"),
    # a shell's code string is searched, command by command
    (f"bash -c 'cd /x && bash {S}/fast-gate.sh --tree /x'", "fast-gate.sh", "gate"),
    ("/bin/zsh -lc 'python3 -m pytest -q tests'", "pytest", "gate"),
    # a process table line joins the arguments and drops the outer quotes: the code is the rest of the line after the flag
    ("bash -c bash /x/watch.sh arg0", "watch.sh", "driver"),
    ("bash -c cd /x && bash /x/watch.sh", "watch.sh", "driver"),
    ("bash -c echo " + "x" * 500 + "; bash /x/watch.sh", "watch.sh", "driver"),   # past the first 400 characters
    # a shell-typed code is one quoted word; the words after it are its parameters
    ("bash -c 'bash /x/watch.sh' arg0", "watch.sh", "driver"),
    ("bash -c 'echo hi; cat /x/watch.sh' arg0", None, None),
    # the flag is found at its own word, never inside an earlier argument
    ('env A="x -c y" bash -c echo "q; bash /x/watch.sh"', None, None),
])
def test_a_command_is_named_by_the_program_it_runs(prof, command, name, kind):
    assert prof.label(command) == ((name, kind) if name else None)


def test_the_rows_sort_by_inclusive_cost_and_carry_their_share(prof, scope):
    """An idle driver whose children are costly stands above a cheap leaf."""
    tree = prof.tree({(("idle", "driver"), ("costly", "gate")): [5.0, 0], (("leaf", "driver"),): [1.0, 0],
                      (("idle", "driver"), ("cheap", "gate")): [0.5, 0]})
    assert names(tree) == ["idle", "leaf"] and names(node(tree, "idle")) == ["costly", "cheap"]
    assert node(tree, "idle")["self_s"] == 0 and node(tree, "idle")["total_s"] == pytest.approx(5.5)
    tree = prof.tree(prof.fold(FIRST, SECOND, scope))
    totals = [c["total_s"] for c in tree["children"]]
    assert totals == sorted(totals, reverse=True)
    assert tree["children"][0]["share"] == pytest.approx(2.0 / 3.75, abs=1e-3)
    assert set(tree) == {"name", "kind", "self_s", "total_s", "share", "events", "total_events",
                         "starts_s", "total_starts_s", "children"}


def test_a_process_is_keyed_by_pid_and_start_time(prof, scope):
    """A reused pid is another process, though its CPU time is larger than the old one's."""
    a = {(7, 100.0): (1, 50.0, f"bash {S}/watch.sh")}
    b = {(7, 200.0): (1, 60.0, f"bash {S}/watch.sh")}
    watch = node(prof.tree(prof.fold(a, b, scope)), "watch.sh")
    assert watch["self_s"] == pytest.approx(60.0) and watch["events"] == 1
    same = node(prof.tree(prof.fold(a, {(7, 100.0): (1, 60.0, f"bash {S}/watch.sh")}, scope)), "watch.sh")
    assert same["self_s"] == pytest.approx(10.0) and same["events"] == 0


def test_the_window_keeps_the_last_span_and_restarts_on_reset(prof, scope):
    def reading(cpu, extra=()):
        return rd({9: (1, cpu, f"bash {S}/watch.sh"), **{p: (9, 0.0, "true") for p in extra}})
    w = prof.Window(span=10)
    w.reset(100.0)
    w.add(100.0, reading(1.0), scope)
    assert w.tree()["total_s"] == 0 and w.window_s(100.0) == 0
    w.add(101.0, reading(2.0, extra=(50,)), scope)
    w.add(102.0, reading(4.0, extra=(51,)), scope)
    assert node(w.tree(), "watch.sh")["self_s"] == pytest.approx(3.0) and node(w.tree(), "watch.sh")["events"] == 2
    assert w.since == 100.0 and w.window_s(102.0) == pytest.approx(2.0)
    w.add(111.5, reading(4.5), scope)                      # the 101 sample is now older than the span
    assert node(w.tree(), "watch.sh")["self_s"] == pytest.approx(2.5) and node(w.tree(), "watch.sh")["events"] == 1
    assert w.window_s(111.5) == pytest.approx(10.0)
    w.reset(200.0)
    assert w.tree()["children"] == [] and w.since == 200.0


def test_the_window_states_its_last_reading_the_measured_interval_and_the_failed_readings(prof, scope):
    w = prof.Window(span=10)
    w.reset(100.0)
    assert w.stats() == {"last_reading": None, "interval_s": None, "failed_readings": 0}
    w.add(100.0, rd({9: (1, 1.0, "true")}), scope)
    assert w.stats() == {"last_reading": 100.0, "interval_s": None, "failed_readings": 0}
    w.fail()
    w.add(102.5, rd({9: (1, 1.0, "true")}), scope)
    assert w.stats() == {"last_reading": 102.5, "interval_s": 2.5, "failed_readings": 1}
    w.reset(200.0)
    assert w.stats() == {"last_reading": None, "interval_s": None, "failed_readings": 0}


# ---------------------------------------------------------------- time in failed runs (#1751)
# The rows are the hub's folded record rows, one per run: the run fold (hub.scan) is the one reader of a record, and this
# fold is pure over what it answers. NOW is a fixed clock, so no test reads the machine's.
NOW = 1_700_000_000.0


def run_row(t0, t1, verdict, reasons=()):
    return {"t0": t0, "t1": t1, "verdict": verdict, "reasons": list(reasons)}


def test_the_table_counts_each_verdict_and_each_reason_of_a_failed_run(prof):
    rows = [run_row(NOW - 900, NOW - 300, "failed", ["gate:red", "review:changes"]),   # 600 s, two reasons
            run_row(NOW - 800, NOW - 200, "failed", ["gate:red"]),                     # 600 s, the same reason again
            run_row(NOW - 700, NOW - 100, "completed")]                                # 600 s, no reason
    t = prof.failed_run_table(rows, 3600, NOW)
    assert [(r["verdict"], r["reason"], r["count"], r["seconds"]) for r in t["rows"]] == [
        ("failed", "gate:red", 2, 1200.0),
        ("failed", "review:changes", 1, 600.0),
        ("completed", "", 1, 600.0)], "a failed run counts under every reason it names, and the rows sort by seconds"
    assert t["total_seconds"] == 1800.0 and t["failed_seconds"] == 1200.0 and t["runs"] == 3
    assert [r["share"] for r in t["rows"]] == [0.667, 0.333, 0.333], "the share is of every run second of the window"
    assert t["label"] == "Time in failed runs", "the view names time, never waste"


def test_a_run_with_no_verdict_counts_as_running_up_to_now(prof):
    t = prof.failed_run_table([run_row(NOW - 120, None, None)], 3600, NOW)
    assert [(r["verdict"], r["reason"], r["count"], r["seconds"]) for r in t["rows"]] == [("running", "", 1, 120.0)]


def test_a_run_that_ended_before_the_window_is_not_counted(prof):
    rows = [run_row(NOW - 7200, NOW - 5400, "failed", ["gate:red"]), run_row(NOW - 300, NOW - 60, "failed", ["gate:red"])]
    assert [(r["count"], r["seconds"]) for r in prof.failed_run_table(rows, 3600, NOW)["rows"]] == [(1, 240.0)]


def test_a_failed_run_with_no_reason_is_one_row_with_no_reason(prof):
    t = prof.failed_run_table([run_row(NOW - 60, NOW, "failed")], 3600, NOW)
    assert [(r["verdict"], r["reason"], r["seconds"]) for r in t["rows"]] == [("failed", "", 60.0)]


def test_an_empty_window_is_an_empty_table_and_no_share(prof):
    t = prof.failed_run_table([], 3600, NOW)
    assert t["rows"] == [] and t["total_seconds"] == 0 and t["runs"] == 0


def test_the_window_is_one_of_the_three_the_page_offers(prof):
    assert prof.RUN_WINDOWS == (3600, 86400, 172800)
    assert [prof.run_window(w) for w in (3600, 86400, 172800)] == [3600, 86400, 172800]
    assert [prof.run_window(w) for w in (None, "x", 7200)] == [86400, 86400, 86400], "an unread choice is the 24 hour window"


def record(folder, *events):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def ev(event, ts, **kw):
    return {"event": event, "pos": 1, "ts": ts, **kw}


@pytest.fixture
def records(served, monkeypatch, tmp_path):
    """A checkout whose runs folder the test owns: the fold reads only what the hub's run fold answers."""
    mod, _, _, _ = served
    monkeypatch.setattr(mod, "CHECKOUT", str(tmp_path))
    return mod, tmp_path / "runs"


def test_a_resumed_run_counts_its_last_verdict_after_the_last_start(records):
    mod, runs = records
    record(runs / "run-20261115-010101-aaaaaa",
           ev("run_start", NOW - 900), ev("verdict", NOW - 800, verdict="failed", exit_code=1, reasons=["gate:red"]),
           ev("resume", NOW - 700), ev("verdict", NOW - 100, verdict="completed", exit_code=0, reasons=[]))
    t = mod.profile_run_table(3600, NOW)
    assert [(r["verdict"], r["count"]) for r in t["rows"]] == [("completed", 1)], "the verdict before the resume is not final"
    assert t["failed_seconds"] == 0


def test_a_fixture_record_of_a_failed_run_gives_its_reasons_their_seconds(records):
    mod, runs = records
    record(runs / "run-20261115-010202-bbbbbb",
           ev("run_start", NOW - 600), ev("verdict", NOW - 300, verdict="failed", exit_code=1, reasons=["gate:red"]))
    record(runs / "run-20261115-010303-cccccc",
           ev("run_start", NOW - 400), ev("verdict", NOW - 100, verdict="failed", exit_code=1, reasons=["gate:red", "land:conflict"]))
    t = mod.profile_run_table(3600, NOW)
    assert [(r["verdict"], r["reason"], r["count"], r["seconds"]) for r in t["rows"]] == [
        ("failed", "gate:red", 2, 600.0), ("failed", "land:conflict", 1, 300.0)]


def test_a_fixture_record_with_no_verdict_is_a_running_run(records):
    mod, runs = records
    record(runs / "run-20261115-010404-dddddd", ev("run_start", NOW - 600), ev("call_start", NOW - 60))
    t = mod.profile_run_table(3600, NOW)
    assert [(r["verdict"], r["count"], r["seconds"]) for r in t["rows"]] == [("running", 1, 600.0)]


def test_a_running_run_whose_record_write_is_older_than_the_window_still_counts(records):
    """A run with no verdict is running up to now, so its old last write never ages it out of the window (AC3).
    The fold's rule is the timestamps: a run still running ends at `now`, whatever its record's write time."""
    mod, runs = records
    d = runs / "run-20261115-010505-eeeeee"
    record(d, ev("run_start", NOW - 200_000), ev("call_start", NOW - 190_000))
    os.utime(d / "record.jsonl", (NOW - 172800, NOW - 172800))   # a long think: no event for two days
    t = mod.profile_run_table(86400, NOW)
    assert [(r["verdict"], r["count"], r["seconds"]) for r in t["rows"]] == [("running", 1, 200_000.0)]


# ---------------------------------------------------------------- the route
@pytest.fixture(scope="module")
def served(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("profiler")
    state = tmp / "st"
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp)),
                 ("SG_CHECKOUT", str(tmp)), ("SG_HUB_DIR", str(tmp)), ("SG_STATE", str(state))):
        mp.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        mp.delenv(k, raising=False)
    gh = importlib.util.module_from_spec(importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py"))
    mp.setitem(sys.modules, "ghread", gh)
    gh.__spec__.loader.exec_module(gh)
    mod = load("hub_profiler_page", HUB_PY)
    state.mkdir(parents=True, exist_ok=True)
    tn = {"host": TN_HOST, "allow": ["alice@example.com"]}
    mod.tailnet_block = lambda checkout=None: dict(tn)
    mod.PROFILER_SECS = 0.01
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port, tn, state
    srv.shutdown()
    srv.server_close()
    mp.undo()


class Table:
    """A process table the test owns: every read is counted, and each read gives every process one more CPU second.
    The driver's command names the hub's own scripts folder, which is how the hub tells its loop."""

    def __init__(self):
        self.reads = 0
        self.rows = {8: (1, f"bash {SCRIPTS}/watch.sh"), 9: (1, "/Applications/Foo.app/Contents/MacOS/Foo")}
        self.before = None                                 # a hook a test sets: it runs inside each read

    def __call__(self):
        if self.before:
            self.before()
        self.reads += 1
        P = type("P", (), {})
        rows = {}
        for pid, (ppid, command) in self.rows.items():
            p = P()
            p.pid, p.ppid, p.cpu, p.command, p.start = pid, ppid, float(self.reads), command, 5.0
            rows[pid] = p
        return rows


@pytest.fixture
def hub(served, monkeypatch):
    mod, port, tn, state = served
    table = Table()
    monkeypatch.setattr(mod, "process_table", table)
    for key in ("loop_write", "profiler_write"):
        tn.pop(key, None)
    yield mod, port, tn, state, table
    table.before = None
    mod.profiler_set(False, "test", "loopback")
    wait(lambda: not mod.profiler_running())
    for name in ("profiler.json", "profiler-audit.jsonl", "watchd.pid"):
        (state / name).unlink(missing_ok=True)


def wait(done, secs=5.0):
    end = time.monotonic() + secs
    while not done():
        assert time.monotonic() < end, "the condition did not hold in time"
        time.sleep(0.01)


def ask(port, method, body=None, *, origin=None, host=None, path="/api/profiler"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json",
               "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    c.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def samplers():
    return [t for t in threading.enumerate() if t.name == "profiler" and t.is_alive()]


def audit(state):
    p = state / "profiler-audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


def as_tailnet(mod, monkeypatch):
    monkeypatch.setattr(mod, "access", lambda h, m: mod.Access(True, "", "alice@example.com", True))


def test_off_is_the_default_and_it_reads_no_process_table(hub):
    mod, port, _, _, table = hub
    code, v = ask(port, "GET")
    assert code == 200 and v["on"] is False and v["tree"] is None and v["context"] is None and v["since"] is None
    assert v["write"] is True
    time.sleep(0.05)
    assert table.reads == 0 and not mod.profiler_running() and samplers() == []


def test_switching_on_starts_the_sampler_and_the_route_returns_the_tree(hub):
    mod, port, _, state, table = hub
    code, out = ask(port, "POST", {"on": True, "client": "desktop"})
    assert (code, out.get("on")) == (200, True)
    wait(lambda: table.reads >= 3)
    code, v = ask(port, "GET")
    assert code == 200 and v["on"] is True and v["since"] and v["window_s"] >= 0
    watch = next(c for c in v["tree"]["children"] if c["name"] == "watch.sh")
    assert watch["self_s"] >= 2 and watch["kind"] == "driver"
    assert [c["name"] for c in v["tree"]["children"]] == ["watch.sh"], "a process outside the loop is no row of the tree"
    assert v["context"]["total_s"] >= 2 and [c["name"] for c in v["context"]["children"]] == ["Foo"]
    assert [(a["action"], a["who"], a["from"], a["client"]) for a in audit(state)] == [("on", "owner", "loopback", "desktop")]


def test_the_root_follows_the_supervisor_the_state_folder_names(hub):
    """The supervisor's command names no folder of this profile here: only its pid, read from watchd.pid, puts it in the tree."""
    mod, port, _, state, table = hub
    table.rows = {70: (1, "bash /elsewhere/watchd.sh supervise"), 71: (70, "sleep 5"), 80: (1, "bash /elsewhere/watchd.sh supervise")}
    (state / "watchd.pid").write_text("70\n")
    ask(port, "POST", {"on": True})
    wait(lambda: table.reads >= 3)
    v = ask(port, "GET")[1]
    assert [c["name"] for c in v["tree"]["children"]] == ["watchd.sh"]
    assert [c["name"] for c in v["context"]["children"]] == ["another loop"]
    assert v["tree"]["total_s"] == pytest.approx(2 * v["context"]["total_s"])


def test_the_answer_carries_the_last_reading_and_the_measured_interval(hub):
    mod, port, _, _, table = hub
    start = time.time()
    ask(port, "POST", {"on": True})
    wait(lambda: table.reads >= 3)
    v = ask(port, "GET")[1]
    assert start <= v["last_reading"] <= time.time()
    assert 0 < v["interval_s"] < 5 and v["failed_readings"] == 0


def test_a_failed_reading_is_counted_and_never_moves_the_last_reading(hub, monkeypatch):
    mod, port, _, _, _ = hub
    reads = []
    # The sampler's own reader is stubbed, never `process_table`: one shared tick keeps its snapshot for the profiler's interval
    # (#1746), so a stub under it is read once in a window and a cached tick answers the rest -- the count then depends on how
    # long the test takes. The failure this asserts is the sampler's, and it must be seen at every tick (#1790).
    monkeypatch.setattr(mod, "profiling_table", lambda: reads.append(1) or {})
    ask(port, "POST", {"on": True})
    wait(lambda: len(reads) >= 3)
    v = ask(port, "GET")[1]
    assert v["failed_readings"] >= 2 and v["last_reading"] is None and v["interval_s"] is None


def test_switching_off_stops_the_sampler_and_is_audited(hub):
    mod, port, _, state, table = hub
    ask(port, "POST", {"on": True})
    wait(lambda: table.reads >= 2)
    code, out = ask(port, "POST", {"on": False})
    assert (code, out.get("on")) == (200, False)
    assert out.get("sampler") == "ended", "the answer does not say the sampler ended"
    assert samplers() == [] and not mod.profiler_running(), "the off answer came before the sampler ended"
    seen = table.reads
    time.sleep(0.05)
    assert table.reads == seen
    assert [a["action"] for a in audit(state)] == ["on", "off"]
    assert ask(port, "GET")[1]["tree"] is None


def test_the_off_write_returns_only_when_the_sampler_has_ended(hub):
    """The sampler is inside a slow read when the off write comes: the write waits for it."""
    mod, _, _, _, table = hub
    inside = threading.Event()
    table.before = lambda: (inside.set(), time.sleep(0.2))
    mod.profiler_set(True, "test", "loopback")
    assert inside.wait(5)
    mod.profiler_set(False, "test", "loopback")
    assert samplers() == [], "the off write returned and a sampler still runs"


def test_an_off_that_interleaves_an_ensure_leaves_no_sampler(hub, monkeypatch):
    """An ensure reads the switch, and an off write lands before the ensure goes on. The switch is read under the lock
    that starts and ends the sampler, so the two never interleave: the off write ends whatever the ensure started."""
    mod, _, _, state, _ = hub
    (state / "profiler.json").write_text(json.dumps({"on": True}))
    real, read, go = mod.profiler_on, threading.Event(), threading.Event()

    def slow_on():
        on = real()
        if threading.current_thread().name == "ensure":   # the ensure stops here, the switch read and nothing started
            read.set()
            go.wait(5)
        return on
    monkeypatch.setattr(mod, "profiler_on", slow_on)
    ensure = threading.Thread(target=mod.profiler_ensure, name="ensure", daemon=True)
    off = threading.Thread(target=lambda: mod.profiler_set(False, "test", "loopback"), daemon=True)
    ensure.start()
    assert read.wait(5)
    off.start()
    off.join(0.2)                                          # an off that does not wait for the ensure has ended by now
    go.set()
    ensure.join(5)
    off.join(5)
    assert not ensure.is_alive() and not off.is_alive()
    assert real() is False and samplers() == [], "the switch is off and a sampler runs"


def test_a_write_that_changes_nothing_writes_no_audit_line(hub):
    _, port, _, state, _ = hub
    assert ask(port, "POST", {"on": False})[0] == 200
    assert audit(state) == []


def quiet_boot(mod, monkeypatch):
    """Stub every read `boot` starts but the sampler, so only the sampler is left to observe. The names come from `boot`
    itself, so a read added there is stubbed here too and the two start tests can never drift."""
    reads = re.findall(r"target=(\w+)", inspect.getsource(mod.boot))
    assert "refresh_board" in reads, "the stub list is not read from boot's own calls"
    for name in ("refresh_runs", "warm", *reads):
        monkeypatch.setattr(mod, name, lambda: None)


def test_a_hub_started_with_the_switch_on_runs_the_sampler(hub, monkeypatch):
    """The start path is `boot`, the one call of the hub's `__main__` before it serves. Its other reads are stubbed."""
    mod, port, _, state, table = hub
    (state / "profiler.json").write_text(json.dumps({"on": True}))
    quiet_boot(mod, monkeypatch)
    assert not mod.profiler_running()
    mod.boot()
    wait(lambda: table.reads >= 2)
    assert mod.profiler_running()


def test_a_hub_started_with_the_switch_off_starts_no_sampler(hub, monkeypatch):
    mod, _, _, _, table = hub
    quiet_boot(mod, monkeypatch)
    mod.boot()
    time.sleep(0.05)
    assert samplers() == [] and table.reads == 0


def test_a_bad_body_is_refused_and_changes_nothing(hub):
    mod, port, _, state, table = hub
    for body in ({}, {"on": "yes"}, [True]):
        assert ask(port, "POST", body)[0] == 400
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0


def test_a_cross_site_write_is_refused_and_writes_nothing(hub):
    mod, port, _, state, table = hub
    code, out = ask(port, "POST", {"on": True}, origin="https://evil.example")
    assert code == 403 and out["err"] == "forbidden"
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0


def test_a_tailnet_write_is_refused_with_its_reason_by_default(hub, monkeypatch):
    mod, port, _, state, table = hub
    as_tailnet(mod, monkeypatch)
    code, out = ask(port, "POST", {"on": True}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 403 and "SQUAD_TAILNET_PROFILER_WRITE" in out["reason"]
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0
    _, v = ask(port, "GET", origin=f"https://{TN_HOST}", host=TN_HOST)
    assert v["write"] is False and "SQUAD_TAILNET_PROFILER_WRITE" in v["reason"]


def test_the_loop_write_grant_does_not_open_the_profiler_switch(hub, monkeypatch):
    """The loop-write grant starts and stops the loop (ADR-0162): it is not widened to this switch."""
    mod, port, tn, state, table = hub
    as_tailnet(mod, monkeypatch)
    tn["loop_write"] = True
    code, out = ask(port, "POST", {"on": True}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 403 and "tailnet.profiler_write" in out["reason"]
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0
    assert ask(port, "GET", origin=f"https://{TN_HOST}", host=TN_HOST)[1]["write"] is False


def test_a_tailnet_write_runs_when_the_owner_grants_it(hub, monkeypatch):
    mod, port, tn, state, _ = hub
    as_tailnet(mod, monkeypatch)
    tn["profiler_write"] = True
    code, _ = ask(port, "POST", {"on": True}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 200
    assert [(a["who"], a["from"]) for a in audit(state)] == [("alice@example.com", "tailnet")]
    assert ask(port, "GET", origin=f"https://{TN_HOST}", host=TN_HOST)[1]["write"] is True


def test_the_loop_audit_holds_no_profiler_line(hub):
    """The loop reads its own state from the last line of its audit: a profiler change must never be that line."""
    _, port, _, state, _ = hub
    ask(port, "POST", {"on": True})
    assert not (state / "loop-audit.jsonl").exists()


def test_a_loop_audit_line_is_written_atomically(hub, monkeypatch, tmp_path):
    """ADR-0030: a write killed between its steps leaves the file as it was, never a half line."""
    mod = hub[0]
    p = tmp_path / "loop-audit.jsonl"
    p.write_text('{"action": "on"}\n')

    def interrupted(src, dst):
        raise OSError("killed before the rename")
    monkeypatch.setattr(mod.os, "replace", interrupted)
    with pytest.raises(OSError):
        mod.loop_audit_add("owner", "loopback", "off", path=p)
    assert p.read_text() == '{"action": "on"}\n', "an interrupted write changed the audit"


# ---------------------------------------------------------------- the pages
def page():
    return HUB_PY.read_text()


def test_the_tab_is_named_loop_and_stands_between_flow_and_settings_on_the_desktop_page(served):
    mod = served[0]
    top = mod.PAGE[mod.PAGE.index("<div class=tabs role=tablist>"):]
    top = top[:top.index("</div>")]
    assert re.findall(r"<button data-t=(\w+) role=tab>([^<]+)</button>", top) == [
        ("board", "Kanban"), ("runs", "Runs"), ("flow", "Flow"), ("prof", "Loop"), ("set", "Settings")]
    assert mod.PAGE.index("<section id=tab-flow") < mod.PAGE.index("<section id=tab-prof") < mod.PAGE.index("<section id=tab-set")
    assert "<section id=tab-prof class=pane aria-label=Loop>" in mod.PAGE
    assert re.search(r'const HASH=\{[^}]*prof:"#loop"[^}]*\},HASH_TAB=\{[^}]*"#loop":"prof"', mod.PAGE)
    assert "Profiler" not in re.sub(r"//[^\n]*|/\*.*?\*/", "", mod.PAGE), "the page still shows the old name"


def test_the_entry_stands_before_settings_on_the_mobile_page():
    """The phone has no Flow tab: its More menu holds the Lead Time chart and the link to Settings, so the entry stands
    between them. It names the desktop view before it navigates, as the Settings entry does (#1446)."""
    html = (SCRIPTS / "hub-mobile.html").read_text()
    menu = html[html.index('<div id="more-menu">'):]
    menu = menu[:menu.index("</div>")]
    assert re.findall(r">([^<>\n]+)</(?:button|a)>", menu) == ["Cost and usage", "Lead time", "Loop", "Settings"]
    assert re.search(r'<a class="more-link" id="more-loop" href="\./#loop">Loop</a>', menu)
    assert re.search(r'\$\("more-loop"\)\.onclick\s*=\s*\(\)\s*=>\s*localStorage\.setItem\("squad-view","desktop"\)', html)


def test_the_page_polls_only_while_the_tab_shows_and_the_switch_is_on():
    src = page()
    assert re.search(r'fetch\("/api/profiler\?w="\+profWin\)', src), "the read carries the window the owner chose"
    assert re.search(r'fetch\("/api/profiler",\{method:"POST"', src)
    assert re.search(r'^const profDue=\(tab,hidden,v\)=>tab==="prof"&&!hidden&&!!v&&v\.on;', src, re.M)


def run_js(expr):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = page()
    js = ("const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'})[c]);\n"
          + src[src.index("// profiler:start"):src.index("// profiler:end")] + f"\nprocess.stdout.write(String({expr}));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return r.stdout


def leaf(name, self_s, **kw):
    return {"name": name, "kind": "driver", "self_s": self_s, "total_s": self_s, "share": 0.25, "events": 4, "total_events": 4,
            "children": [], **kw}


VIEW = {"on": True, "since": 1.0, "window_s": 125, "write": True, "reason": "",
        "last_reading": 1700000000.0, "interval_s": 1.04, "failed_readings": 0,
        "context": {"name": "outside the loop", "total_s": 40.0, "events": 9, "children": [
            {"name": "Foo Bar", "total_s": 30.0, "events": 2}, {"name": "another loop", "total_s": 10.0, "events": 7}]},
        "tree": {"name": "loop", "kind": "root", "self_s": 0, "total_s": 8.0, "share": 1, "events": 0, "total_events": 12,
                 "children": [
                     leaf("watchd.sh", 0.0, total_s=6.0, share=0.75, events=0,
                          children=[leaf("watch.sh", 6.0, share=0.75, events=8)]),
                     leaf("orphan watch.sh (temp folder)", 2.0, kind="orphan")]}}


def test_the_poll_rule_is_false_off_the_tab_or_with_the_switch_off():
    out = json.loads(run_js('JSON.stringify([profDue("prof",false,{on:true}),profDue("flow",false,{on:true}),'
                            'profDue("prof",true,{on:true}),profDue("prof",false,{on:false}),profDue("prof",false,null)])'))
    assert out == [True, False, False, False, False]


def tree_part(out):
    return out[out.index("<table id=proft"):out.index("</table>") + 8]


def test_the_view_draws_one_row_per_node_with_its_bar_and_columns():
    out = tree_part(run_js(f"profHtml({json.dumps(VIEW)},new Set())"))
    assert [h for h in re.findall(r"<th[^>]*>([^<]+)</th>", out)] == ["Hot spot", "Self time", "Total time", "Starts/s (lower bound)"]
    rows = re.findall(r"<tr data-p=\"([^\"]*)\"", out)
    assert rows == ["loop", "loop/watchd.sh", "loop/orphan watch.sh (temp folder)"], "a closed node shows no child"
    assert re.search(r'data-p="loop/watchd\.sh".*?<i class=pbar style="width:75\.0%">', out, re.S)
    row = re.search(r'<tr data-p="loop/watchd\.sh".*?</tr>', out, re.S).group(0)
    assert re.findall(r"<td>([^<]*)</td>", row) == ["0 ms", "6.00 s", "0.00/s"], "self time, total time and starts a second"


def test_a_node_opens_to_its_children_and_closes_again():
    out = tree_part(run_js(f"profHtml({json.dumps(VIEW)},new Set(['loop/watchd.sh']))"))
    rows = re.findall(r"<tr data-p=\"([^\"]*)\"", out)
    assert rows == ["loop", "loop/watchd.sh", "loop/watchd.sh/watch.sh", "loop/orphan watch.sh (temp folder)"]
    assert re.search(r'data-p="loop/watchd\.sh" aria-expanded=true', out)
    assert re.search(r'data-p="loop/watchd\.sh/watch\.sh" aria-level=3', out)
    closed = tree_part(run_js(f"profHtml({json.dumps(VIEW)},new Set(['loop']))"))
    assert re.findall(r"<tr data-p=\"([^\"]*)\"", closed) == ["loop"], "the root closes too"


def test_the_context_row_stands_apart_from_the_tree_and_opens_to_its_applications():
    out = run_js(f"profHtml({json.dumps(VIEW)},new Set())")
    assert out.index("</table>") < out.index("<table id=profc"), "the context row is a row of the tree"
    ctx = out[out.index("<table id=profc"):]
    assert re.findall(r"<tr data-p=\"([^\"]*)\"", ctx) == ["context"] and "40.00 s" in ctx and "outside the loop" in ctx
    opened = run_js(f"profHtml({json.dumps(VIEW)},new Set(['context']))")
    ctx = opened[opened.index("<table id=profc"):]
    assert re.findall(r"<tr data-p=\"([^\"]*)\"", ctx) == ["context", "context/Foo Bar", "context/another loop"]
    assert "Foo Bar" not in tree_part(opened)


def test_the_view_states_the_reading_it_shows():
    out = run_js(f"profHtml({json.dumps(VIEW)},new Set())")
    note = re.search(r"<span class=lpn>(.*?)</span>", out, re.S).group(1)
    assert "125 s" in note and "1.04 s" in note and "No reading failed" in note
    assert re.search(r'<time datetime="2023-11-14T22:13:20\.000Z">', note), "the time of the last good reading"
    bad = run_js(f"profHtml({json.dumps({**VIEW, 'failed_readings': 3, 'last_reading': None, 'interval_s': None})},new Set())")
    note = re.search(r"<span class=lpn>(.*?)</span>", bad, re.S).group(1)
    assert "3 readings failed" in note and "No good reading yet" in note


def test_the_view_states_the_switch_and_an_off_switch_draws_no_tree():
    on = run_js(f"profHtml({json.dumps(VIEW)},new Set())")
    assert re.search(r'role=switch aria-label="CPU sampling" aria-checked="true" data-prof', on)
    assert re.search(r"<h2>Loop</h2>", on) and re.search(r"<span class=lpl[^>]*>CPU sampling</span>", on)
    off = run_js(f"profHtml({json.dumps({**VIEW, 'on': False, 'tree': None, 'context': None})},new Set())")
    assert re.search(r'aria-checked="false"', off) and "id=proft" not in off and "CPU sampling is off" in off
    ro = run_js(f"profHtml({json.dumps({**VIEW, 'write': False, 'reason': 'read-only from the tailnet'})},new Set())")
    assert re.search(r'data-prof disabled[^>]*title="read-only from the tailnet"', ro)


# ---------------------------------------------------------------- time in failed runs (#1751)
RUNS = {"label": "Time in failed runs", "window_s": 3600, "runs": 3, "total_seconds": 1800.0, "failed_seconds": 1200.0,
        "rows": [{"verdict": "failed", "reason": "gate:red", "count": 2, "seconds": 1200.0, "share": 0.667},
                 {"verdict": "failed", "reason": "review:changes", "count": 1, "seconds": 600.0, "share": 0.333},
                 {"verdict": "completed", "reason": "", "count": 1, "seconds": 600.0, "share": 0.333}]}


def runs_part(out):
    at = out.index("<table id=profr")
    return out[at:out.index("</table>", at) + 8]


def test_the_view_draws_the_time_in_failed_runs_table_by_verdict_and_reason():
    out = run_js(f"profHtml({json.dumps({**VIEW, 'runs': RUNS})},new Set())")
    part = runs_part(out)
    assert [h for h in re.findall(r"<th[^>]*>([^<]+)</th>", part)] == ["Verdict", "Runs", "Run time", "Share"]
    rows = re.findall(r"<td><b>([^<]*)</b>(?: <span class=prr>([^<]*)</span>)?</td><td>(\d+)</td><td>([^<]*)</td><td>([^<]*)</td>", part)
    assert rows == [("failed", "gate:red", "2", "20.0 min", "66.7%"), ("failed", "review:changes", "1", "10.0 min", "33.3%"),
                    ("completed", "", "1", "10.0 min", "33.3%")], "a failed run shows one row for each reason it names"
    assert "Time in failed runs" in out and "waste" not in out.lower(), "the view names time, never waste"
    assert "20.0 min in failed runs of 30.0 min of run time, 3 runs." in out


def test_the_view_offers_the_three_windows_and_marks_the_one_chosen():
    out = run_js(f"profHtml({json.dumps({**VIEW, 'runs': RUNS})},new Set())")
    opts = re.findall(r"<option value=(\d+)( selected)?>([^<]+)</option>", out)
    assert [(v, bool(sel), label) for v, sel, label in opts] == [("3600", False, "1 hour"), ("86400", True, "24 hours"),
                                                                ("172800", False, "48 hours")]
    assert re.search(r'<select data-profwin aria-label="The window of the Loop tab\'s file views">', out)


def test_the_table_shows_with_the_switch_off_and_states_an_empty_window():
    off = run_js(f"profHtml({json.dumps({**VIEW, 'on': False, 'tree': None, 'context': None, 'runs': RUNS})},new Set())")
    assert "id=profr" in off and "id=proft" not in off and "gate:red" in off
    empty = {"label": "Time in failed runs", "window_s": 3600, "runs": 0, "total_seconds": 0, "failed_seconds": 0, "rows": []}
    out = run_js(f"profHtml({json.dumps({**VIEW, 'on': False, 'tree': None, 'context': None, 'runs': empty})},new Set())")
    assert "No run in this window." in out


def test_the_route_answers_the_table_of_the_window_the_page_asked_for(hub):
    mod, port, _, state, table = hub
    now = time.time()
    record(state.parent / "runs" / "run-20261115-020202-eeeeee",
           ev("run_start", now - 600), ev("verdict", now - 300, verdict="failed", exit_code=1, reasons=["gate:red"]))
    code, v = ask(port, "GET")
    assert code == 200 and v["on"] is False and v["runs_window_s"] == 86400, "the table shows with the switch off"
    assert [(r["verdict"], r["reason"], r["count"]) for r in v["runs"]["rows"]] == [("failed", "gate:red", 1)]
    assert [w["s"] for w in v["runs_windows"]] == [3600, 86400, 172800]
    assert table.reads == 0, "the table reads files only"
    code, short = ask(port, "GET", path="/api/profiler?w=3600")
    assert code == 200 and short["runs_window_s"] == 3600 and short["runs"]["rows"] == v["runs"]["rows"]
    assert ask(port, "GET", path="/api/profiler?w=99")[1]["runs_window_s"] == 86400, "a choice the page cannot read is the default"


def test_the_route_folds_every_run_record_through_the_one_reader(hub, monkeypatch):
    """AC4: the route's table is what the hub's one reader of a record answers, `scan`, never a parse of its own and
    nothing from GitHub. The reader answers a reason this test writes nowhere on disk, and the route must show it."""
    mod, port, _, state, _ = hub
    now = time.time()
    d = state.parent / "runs" / "run-20261115-050505-ffffff"
    record(d, ev("run_start", now - 600), ev("verdict", now - 300, verdict="failed", exit_code=1, reasons=["gate:red"]))
    seen, real = [], mod.scan

    def counted(p):
        seen.append(str(p))
        if str(p) == str(d / "record.jsonl"):
            return {"t0": now - 100, "t1": now - 50, "verdict": "failed", "reasons": ["folded:only"]}
        return real(p)

    monkeypatch.setattr(mod, "scan", counted)
    code, v = ask(port, "GET")
    assert code == 200
    assert str(d / "record.jsonl") in seen, "the route reads every run record through the hub's one reader"
    assert ("failed", "folded:only") in [(r["verdict"], r["reason"]) for r in v["runs"]["rows"]], \
        "the route renders the reader's answer, never a second parse of the record"


# ---------------------------------------------------------------- the trip of one issue (#1769)
# A delivery is a run whose record holds a passed land and a verdict. The stages are the record's own spans: the wait for
# a slot before the first call, the child's coding, each review round's wait, each gate's start to its result, and the time
# from the last answer or review to the merge. The gates stage also takes every gate row of the window from gate-history.
def rec(start=1000.0, child="code.1", land_outcome="pass", verdict="completed", gates=True):
    """One fixture record of a delivery: every stage a known length, so the fold's answer is a worked example."""
    out = [ev("run_start", start),
           ev("call_start", start + 30, node="router", entry=1),           # wait: 30 s to the first call
           ev("spawn_accepted", start + 40, child=child),
           ev("call_start", start + 44, node=child, entry=1),
           ev("outcome", start + 244, node=child, entry=1, outcome="pass", elapsed=200.0),   # coding: 244 - 44 = 200 s
           ev("review_round", start + 300, node=child, round=1),           # review: 300 - 244 = 56 s
           ev("land", start + 450, node=child, outcome=land_outcome),
           ev("verdict", start + 500, verdict=verdict, exit_code=0, reasons=[])]
    if gates:
        out.insert(6, ev("gate_result", start + 400, node="final_gate", entry=1, item=None, outcome="pass"))
        out.insert(6, ev("gate_start", start + 310, node="final_gate", entry=1, item=None))   # gates: 400 - 310 = 90 s
    return out


def test_the_trip_spans_of_a_fixture_record_are_its_five_stages(prof):
    spans = prof.trip_spans(rec())
    assert spans == {"wait": 30.0, "coding": 200.0, "review": 56.0, "gates": 90.0, "landing": 150.0, "end": 1500.0}


def test_a_run_that_landed_nothing_is_no_delivery(prof):
    assert prof.trip_spans(rec(land_outcome="fail")) is None, "a run whose land failed delivered nothing"
    no_land = [e for e in rec() if e["event"] != "land"]
    assert prof.trip_spans(no_land) is None
    no_verdict = [e for e in rec() if e["event"] != "verdict"]
    assert prof.trip_spans(no_verdict) is None


def test_a_child_that_failed_and_retried_counts_its_whole_span_as_coding(prof):
    """A failed attempt is retried as another entry of the same child; its time is coding time, never a gap."""
    evs = [ev("run_start", 2000.0), ev("spawn_accepted", 2000.0, child="code.1"),
           ev("call_start", 2010.0, node="code.1", entry=1), ev("outcome", 2110.0, node="code.1", entry=1, outcome="fail"),
           ev("call_start", 2200.0, node="code.1", entry=2), ev("outcome", 2500.0, node="code.1", entry=2, outcome="pass"),
           ev("land", 2600.0, node="code.1", outcome="pass"), ev("verdict", 2610.0, verdict="completed", exit_code=0, reasons=[])]
    assert prof.trip_spans(evs)["coding"] == 490.0     # 2500 - 2010: the retry's wait and both entries


def test_the_percentile_is_the_linear_rule_the_doc_names(prof):
    assert prof.percentile([], 90) is None
    assert prof.percentile([10], 50) == 10
    assert prof.percentile([10, 20, 30], 50) == 20
    assert prof.percentile([10, 20, 30], 90) == pytest.approx(28.0), "the (n - 1) weight the trip table's p90 uses"
    assert prof.percentile([4, 1, 3, 2], 90) == pytest.approx(3.7)   # sorted 1,2,3,4: 1 + 3 * 0.9


TRIP_SPANS = [{"wait": 10.0, "coding": 100.0, "review": 20.0, "gates": None, "landing": 30.0, "end": NOW - 100},
              {"wait": 20.0, "coding": 200.0, "review": 40.0, "gates": 60.0, "landing": 60.0, "end": NOW - 200},
              {"wait": 30.0, "coding": 300.0, "review": 60.0, "gates": 120.0, "landing": 90.0, "end": NOW - 300}]


def test_each_stage_answers_its_median_and_p90_over_the_deliveries(prof):
    gates = [{"start": NOW - 500, "finish": NOW - 440},                    # 60 s, inside the window
             {"start": NOW - 9000, "finish": NOW - 8000}]                  # outside it: no observation
    t = prof.trip(TRIP_SPANS, gates, since=NOW - 3600)
    assert t["deliveries"] == 3 and t["bar"] is True
    rows = {r["stage"]: r for r in t["stages"]}
    assert list(rows) == ["wait", "coding", "review", "gates", "landing"]
    assert rows["wait"]["median_s"] == 20.0 and rows["wait"]["p90_s"] == pytest.approx(28.0)
    assert rows["coding"]["median_s"] == 200.0 and rows["coding"]["p90_s"] == pytest.approx(280.0)
    assert rows["review"]["median_s"] == 40.0 and rows["review"]["observations"] == 3
    assert rows["gates"]["median_s"] == 60.0 and rows["gates"]["observations"] == 3, \
        "the two deliveries that hold a gate and the one gate-history row of the window"
    assert rows["landing"]["p90_s"] == pytest.approx(84.0)
    assert [r["label"] for r in t["stages"]] == ["Wait for a dispatch", "Coding", "Review", "Gates", "Landing"]
    assert rows["coding"]["share"] == pytest.approx(0.526, abs=1e-3), "the share is the median over the sum of medians"


def test_fewer_than_three_deliveries_draw_no_bar(prof):
    t = prof.trip(TRIP_SPANS[:2], [], since=NOW - 3600)
    assert t["deliveries"] == 2 and t["bar"] is False, "a median of two is no profile"


def test_a_delivery_that_ended_before_the_window_is_not_folded(prof):
    old = {"wait": 10.0, "coding": 10.0, "review": 10.0, "gates": 10.0, "landing": 10.0, "end": NOW - 7200}
    assert prof.trip([*TRIP_SPANS, old], [], since=NOW - 3600)["deliveries"] == 3


TRIP = {"window_s": 3600, "since": NOW - 3600, "deliveries": 4, "bar": True,
        "stages": [{"stage": "wait", "label": "Wait for a dispatch", "observations": 4, "median_s": 20.0, "p90_s": 28.0, "share": 0.0526},
                   {"stage": "coding", "label": "Coding", "observations": 4, "median_s": 200.0, "p90_s": 280.0, "share": 0.5263},
                   {"stage": "review", "label": "Review", "observations": 3, "median_s": 40.0, "p90_s": 56.0, "share": 0.1053},
                   {"stage": "gates", "label": "Gates", "observations": 4, "median_s": 60.0, "p90_s": 60.0, "share": 0.1579},
                   {"stage": "landing", "label": "Landing", "observations": 2, "median_s": 60.0, "p90_s": 84.0, "share": 0.1579}]}


def trip_part(out):
    at = out.index("<section id=proftrip>")
    return out[at:out.index("</section>", at) + 10]


def test_the_view_draws_the_trip_as_one_stacked_bar_with_its_hover_and_table():
    out = trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP})},new Set())"))
    assert "<h3>The trip of an issue</h3>" in out and "4 deliveries in the last 1.0 h" in out
    segs = re.findall(r'<i class="ptb-seg pts-(\w+)" style="width:([\d.]+)%" title="([^"]*)"', out)
    assert [s[0] for s in segs] == ["wait", "coding", "review", "gates", "landing"]
    assert [float(s[1]) for s in segs] == [5.26, 52.63, 10.53, 15.79, 15.79], "each segment's width is its share of the medians"
    assert segs[1][2] == "Coding: median 3.3 min, p90 4.7 min (4 observations)", "the hover names the median and the p90"
    assert [h for h in re.findall(r"<th[^>]*>([^<]+)</th>", out)] == ["Stage", "Median", "p90", "Observations"]
    rows = re.findall(r'<tr data-p="trip/(\w+)"><td class=pn><span class=pnm>([^<]*)</span></td><td>([^<]*)</td><td>([^<]*)</td><td>(\d+)</td>', out)
    assert rows[1] == ("coding", "Coding", "3.3 min", "4.7 min", "4")
    assert rows[4] == ("landing", "Landing", "1.0 min", "1.4 min", "2")


def test_a_window_with_fewer_than_three_deliveries_says_so_and_draws_no_bar():
    thin = {**TRIP, "deliveries": 2, "bar": False}
    out = trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': thin})},new Set())"))
    assert "2 deliveries in the last 1.0 h: fewer than three, so no bar is drawn." in out
    assert "class=ptb " not in out and "ptb-seg" not in out, "fewer than three deliveries draw no bar"
    one = {**TRIP, "deliveries": 1, "bar": False}
    assert "1 delivery in " in trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': one})},new Set())"))


def test_the_trip_shows_with_the_switch_off_and_beside_the_failed_run_table():
    off = run_js(f"profHtml({json.dumps({**VIEW, 'on': False, 'tree': None, 'context': None, 'trip': TRIP, 'runs': RUNS})},new Set())")
    assert "id=proftrip" in off and "id=proftt" in off and "id=profr" in off and "<table id=proft " not in off
    on = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP})},new Set())")
    assert on.index("id=proftrip") < on.index("<table id=proft "), "the bar is the summary above the hot-spot tree"


def test_the_file_views_refresh_on_the_slow_cadence_with_the_switch_off():
    out = json.loads(run_js('JSON.stringify([profFileDue("prof",false,{on:false}),profFileDue("prof",false,{on:true}),'
                            'profFileDue("flow",false,{on:false}),profFileDue("prof",true,{on:false}),'
                            'profFileDue("prof",false,null)])'))
    assert out == [True, False, False, False, False]


def test_the_window_picker_names_every_file_view_of_the_tab():
    """One window choice moves the bar and the failed-run table alike, so its name says so (#1769)."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP, 'runs': RUNS})},new Set())")
    assert re.search(r"<select data-profwin aria-label=\"The window of the Loop tab's file views\">", out)
    assert len(re.findall(r"<select data-profwin", out)) == 1, "one window choice serves both file views"


def test_the_route_answers_the_trip_of_the_window_the_page_asked_for(hub):
    mod, port, _, state, table = hub
    now = time.time()
    runs = state.parent / "runs"
    for i, start in enumerate((now - 1000, now - 2000, now - 3000, now - 200_000)):   # the fourth ended two days ago
        record(runs / f"run-20261115-03030{i}-aaaaa{i}", *[dict(e, ts=e["ts"] - 1000 + start) for e in rec()])
    (state / "gate-history").write_text(
        "".join(f"{now - 200}\tfast\tpass\tabc\t{now - 260}\tall\troot\tpass\t-\t1\t0\t0\t0\tchild\t0.1\t0.2\n"
                for _ in range(2)))
    code, v = ask(port, "GET")
    assert code == 200 and v["trip"]["window_s"] == 86400 and v["trip"]["bar"] is True
    assert v["trip"]["deliveries"] == 3, "the delivery two days back is outside the day the page asked for"
    assert [s["stage"] for s in v["trip"]["stages"]] == ["wait", "coding", "review", "gates", "landing"]
    assert {r["stage"]: r["observations"] for r in v["trip"]["stages"]}["gates"] == 5, \
        "three deliveries hold a gate each, and the gate history adds its two rows of the window"
    assert table.reads == 0, "the trip reads files only, so it shows with the switch off"
    assert ask(port, "GET", path="/api/profiler?w=3600")[1]["trip"]["window_s"] == 3600


def test_the_trip_reads_the_gate_history_by_column_name(hub):
    """A gate row's reason may hold a tab; the columns after it keep their own names, never an index."""
    mod = hub[0]
    line = "100\tfast\tpass\tabc\t90\tall\troot\tpass\twhy\twith\ttabs\t1\t0\t0\t0\tchild\t0.1\t0.2"
    row = mod.gate_row(line)
    assert (row["start"], row["finish"], row["outcome"], row["caller"]) == ("90", "100", "pass", "child")
    assert row["reason"] == "why\twith\ttabs", "the free-text reason is rejoined, so later columns keep their place"
    assert mod.gate_row("100\tfast\tpass") is None, "a row too short to hold a start and a finish is no row"


def test_the_profile_doc_names_the_bar_as_the_live_form_of_its_trip_table():
    doc = (ROOT / "docs/loop-sequence.md").read_text()
    section = doc[doc.index("## 0."):doc.index("### Hot spots")]
    assert "trip bar" in section and "live form" in section, "the doc names the bar as the live form of its trip table"
    assert "hub_profiler.trip" in section and "gate-history" in section
    assert "fewer than three deliveries" in section


# ---------------------------------------------------------------- behind the many-repository hub
@pytest.fixture
def mux(tmp_path, monkeypatch):
    prof, co = tmp_path / "profiles", tmp_path / "co"
    prof.mkdir()
    co.mkdir()
    (prof / "alpha.sh").write_text(f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(tmp_path / "state"))
    hubmux = load("hubmux_profiler_under_test", SCRIPTS / "hubmux.py")
    apps = hubmux.load_apps()
    mod = apps["alpha"]["mod"]
    mod.runs = lambda: []
    mod.PROFILER_SECS = 0.01
    table = Table()
    mod.process_table = table
    srv = hubmux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield mod, srv.server_address[1], table, apps
    mod.profiler_set(False, "test", "loopback")
    wait(lambda: not mod.profiler_running())
    srv.shutdown()
    srv.server_close()


def test_a_profile_behind_the_many_repository_hub_takes_the_switch(mux):
    """The many-repository hub forwards a write by the name of its handler: a route it does not forward answers nothing."""
    mod, port, table, _ = mux
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/alpha/api/profiler", body=json.dumps({"on": True}),
              headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    assert (r.status, out.get("on")) == (200, True)
    wait(lambda: table.reads >= 2)
    c.request("GET", "/alpha/api/profiler", headers={"Host": f"127.0.0.1:{port}"})
    v = json.loads(c.getresponse().read())
    c.close()
    assert v["on"] is True and v["tree"]["name"] == "loop"


def test_the_many_repository_hub_starts_the_sampler_of_a_profile_only_when_a_page_reads_it(mux):
    """`start_profile` is what the many-repository hub's `__main__` runs for each profile, at its start and for a profile
    added later. A switch left on starts no reading there: the Loop tab's read is what starts the sampler (#1790)."""
    mod, port, table, apps = mux
    for name in SAMPLERS:
        setattr(mod, name, lambda: None)
    mod.PROFILER_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    mod.PROFILER_SWITCH.write_text(json.dumps({"on": True}))
    assert not mod.profiler_running()
    mux_mod = load("hubmux_profiler_start", SCRIPTS / "hubmux.py")
    mux_mod.start_profile(apps["alpha"])
    time.sleep(0.3)
    assert not mod.profiler_running() and table.reads == 0, "a switch left on read the process table with no page open"
    mux_get(port, "/alpha/api/profiler")
    wait(lambda: table.reads >= 2)


# ---------------------------------------------------------------- the background reads (#1790)
# A page read is what keeps a profile's background reads running. With no page, a reader does no work: the many-repository
# hub reads six of them per profile, and one of those reads the process table every five seconds.
SAMPLERS = ("refresh_prs", "refresh_quota", "refresh_board", "refresh_history", "load_sampler")


class StopSampling(Exception):
    """Ends a sampler loop a test started: the gate's pause raises it once the test is done with the loop."""


def gated(mod, idle):
    """A gate whose pause ends the loop on request, so a test leaves no thread behind. A sleep a page is keeping alive is
    cut to 20 ms: the read's own minute is the real cadence, which a test never waits."""
    class Gate(mod.ReadGate):
        def pause(self, secs):
            if getattr(self, "done", False):
                raise StopSampling
            if self.wanted():
                time.sleep(min(secs, 0.02))
            else:
                super().pause(secs)
            if getattr(self, "done", False):
                raise StopSampling
    g = Gate(idle=idle); g.done = False; return g


def drive(fn):
    try:
        fn()
    except StopSampling:
        pass


def mux_get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse(); body = r.read(); c.close()
    return r.status, body


def test_a_background_read_does_no_work_while_no_page_reads_the_profile(hub, monkeypatch):
    """The load sampler reads only while a page reads its profile: no read for the idle window means no work, a read makes
    it read at once, and it parks again when the page stops (#1790)."""
    mod = hub[0]
    ticks = []
    monkeypatch.setattr(mod, "load_tick", lambda: ticks.append("load"))
    monkeypatch.setattr(mod, "foot_refresh", lambda: ticks.append("foot"))
    monkeypatch.setattr(mod, "LOAD_SAMPLE_SECS", 0.02)
    gate = gated(mod, 0.15)
    monkeypatch.setattr(mod, "READ_GATE", gate)
    t = threading.Thread(target=drive, args=(mod.load_sampler,), daemon=True)
    t.start()
    try:
        time.sleep(0.3)
        assert ticks == [], f"{len(ticks)} reads with no page reading the profile"
        gate.touch()
        wait(lambda: len(ticks) >= 2)
        time.sleep(0.4)                                      # the page stops reading: the idle window passes
        parked = len(ticks)
        time.sleep(0.2)
        assert len(ticks) == parked, "the load sampler went on reading after the page stopped"
    finally:
        gate.done = True; gate.touch(); t.join(2)
    assert not t.is_alive()


def test_a_page_read_wakes_a_long_background_read_at_once(hub, monkeypatch):
    """The history read sleeps a minute or more between passes. A page read ends that sleep at once, so a page that opens
    on a cold profile is not a minute behind (#1790)."""
    mod = hub[0]
    passes = []
    monkeypatch.setattr(mod, "history_pass", lambda: passes.append(time.time()))
    monkeypatch.setattr(mod, "HISTORY_EVERY", 600)
    gate = gated(mod, 0.2)
    monkeypatch.setattr(mod, "READ_GATE", gate)
    t = threading.Thread(target=drive, args=(mod.refresh_history,), daemon=True)
    t.start()
    try:
        time.sleep(0.3)
        assert passes == [], "the history read ran with no page reading the profile"
        gate.touch()
        wait(lambda: passes)                                 # the 600 s sleep ended on the read, not after it
    finally:
        gate.done = True; gate.touch(); t.join(2)
    assert not t.is_alive()


def test_a_short_read_resumes_beside_a_long_one_after_an_idle_gap(hub, monkeypatch):
    """Each read keeps its own place, so a long sleeper never holds a short one: a page that comes back after an idle gap
    finds the fast readings moving again (#1790)."""
    mod = hub[0]
    ticks, passes = [], []
    monkeypatch.setattr(mod, "load_tick", lambda: ticks.append(1))
    monkeypatch.setattr(mod, "foot_refresh", lambda: None)
    monkeypatch.setattr(mod, "LOAD_SAMPLE_SECS", 0.02)
    monkeypatch.setattr(mod, "history_pass", lambda: passes.append(1))
    monkeypatch.setattr(mod, "HISTORY_EVERY", 600)
    gate = gated(mod, 0.15)
    monkeypatch.setattr(mod, "READ_GATE", gate)
    a = threading.Thread(target=drive, args=(mod.load_sampler,), daemon=True)
    b = threading.Thread(target=drive, args=(mod.refresh_history,), daemon=True)
    a.start(); b.start()
    try:
        gate.touch()
        wait(lambda: len(ticks) >= 2 and passes)             # both read while a page reads
        time.sleep(0.4)                                      # the page goes away
        parked = len(ticks)
        time.sleep(0.2)
        assert len(ticks) == parked, "the short read went on with no page"
        gate.touch()
        wait(lambda: len(ticks) > parked + 1 and len(passes) >= 2)   # and both come back on the next read
    finally:
        gate.done = True; gate.touch(); a.join(2); b.join(2)
    assert not a.is_alive() and not b.is_alive()


def test_the_process_table_read_stops_once_no_page_reads_the_profile(hub, monkeypatch):
    """The Loop tab's sampler is the one reading that costs most: it reads the process table every second. It runs while
    a page reads the profile and ends when none does (#1790)."""
    mod, port, _, _, table = hub
    gate = gated(mod, 0.2)
    monkeypatch.setattr(mod, "READ_GATE", gate)
    gate.touch()
    assert ask(port, "POST", {"on": True})[0] == 200
    wait(lambda: table.reads >= 2)
    time.sleep(0.45)                                         # no page reads the profile
    assert not mod.profiler_running(), "the process table read went on with no page reading the profile"
    seen = table.reads
    time.sleep(0.1)
    assert table.reads == seen
    gate.touch()                                             # the Loop tab's read starts it again
    ask(port, "GET")
    wait(lambda: table.reads > seen)


def test_a_page_read_starts_the_reads_of_the_profile_it_names(mux):
    """The many-repository hub parks a profile's reads at boot: a read of that profile's page is what starts them (#1790)."""
    _, port, _, apps = mux
    mux_mod = load("hubmux_read_gate", SCRIPTS / "hubmux.py")
    app = apps["alpha"]
    mux_mod.start_profile(app)
    assert not app["gate"].wanted(), "the profile reads at boot with no page open"
    assert mux_get(port, "/alpha/")[0] == 200
    assert app["gate"].wanted(), "a page read did not start the profile's reads"


def test_the_index_read_starts_the_reads_of_every_repository_it_lists(mux):
    """The index lists each repository's live work and its worst alert, so it reads each of them: its poll keeps their
    readings running (#1790)."""
    _, port, _, apps = mux
    mux_mod = load("hubmux_index_gate", SCRIPTS / "hubmux.py")
    mux_mod.start_profile(apps["alpha"])
    assert not apps["alpha"]["gate"].wanted()
    assert mux_get(port, "/api/hub")[0] == 200
    assert apps["alpha"]["gate"].wanted()


def test_the_identity_check_starts_no_read(mux):
    """`hubctl.sh` probes the identity on its own bound, and ADR-0124 keeps that answer off the profile's state reads: it
    starts no background read either (#1202, #1790)."""
    _, port, _, apps = mux
    mux_mod = load("hubmux_identity_gate", SCRIPTS / "hubmux.py")
    mux_mod.start_profile(apps["alpha"])
    assert mux_get(port, "/api/hub?identity=1")[0] == 200
    assert not apps["alpha"]["gate"].wanted(), "the identity check started the profile's reads"


def test_the_push_stream_subject_does_no_work_while_no_page_reads_the_profile(hub, monkeypatch):
    """The stream's subjects are read by one thread each, and the sessions one starts with the hub. With no page open it
    reads nothing: the session folders were scanned every 30 s around the clock, which is the cost #1790 measured."""
    mod = hub[0]
    reads = []

    class Versions:
        stop = False
        @classmethod
        def refresh_one(cls, name):
            if cls.stop:
                raise StopSampling
            reads.append(name)
        @classmethod
        def newer(cls, _):
            if cls.stop:
                raise StopSampling
            return {}
    monkeypatch.setattr(mod, "VERSIONS", Versions())
    monkeypatch.setattr(mod.hubstream, "TICK_SECONDS", 0.02)
    monkeypatch.setattr(mod.hubstream, "FIRST_POLL_SECONDS", 0.02)
    monkeypatch.setattr(threading, "excepthook", lambda _: None)   # the stop below ends the thread by an exception
    gate = gated(mod, 0.15)
    monkeypatch.setattr(mod, "READ_GATE", gate)
    t = mod._ticker("probe")
    try:
        wait(lambda: t.is_alive())
        time.sleep(0.2)
        assert reads == [], f"{len(reads)} reads with no page reading the profile"
        gate.touch()
        wait(lambda: len(reads) >= 2)                        # a page read puts the subject back to work at once
        time.sleep(0.4)                                      # the page stops reading: the idle window passes
        parked = len(reads)
        time.sleep(0.2)
        assert len(reads) == parked, "the subject went on reading after the page stopped"
    finally:
        gate.done = True; gate.touch(); Versions.stop = True
        wait(lambda: not t.is_alive(), 2)
    assert not t.is_alive()


# ---------------------------------------------------------------- the second review's findings
def test_the_supervisor_above_the_pid_file_s_process_is_this_loop(prof):
    """watchd.pid names the `run` child; its `supervise` parent is the same loop, never another one."""
    a = rd({10: (1, 0.0, f"bash {S}/watchd.sh supervise"), 11: (10, 0.0, f"bash {S}/watchd.sh run")})
    b = rd({10: (1, 1.0, f"bash {S}/watchd.sh supervise"), 11: (10, 2.0, f"bash {S}/watchd.sh run")})
    acc = prof.fold(a, b, prof.Scope(11, (f"{S}/",)))
    assert prof.context(acc)["total_s"] == 0
    assert names(prof.tree(acc)) == ["watchd.sh"]


def test_a_script_inside_a_quoted_argument_of_shell_code_names_nothing(prof):
    assert prof.label('bash -c echo "hello; bash /x/watch.sh"') is None
    assert prof.label("bash -c cd /x && bash /x/watch.sh") == ("watch.sh", "driver")


def test_a_second_off_returns_only_when_the_sampler_has_ended(hub, monkeypatch):
    mod = hub[0]
    release = threading.Event()
    old = threading.Thread(target=lambda: release.wait(5), daemon=True)
    old.start()
    monkeypatch.setitem(mod.PROFILER, "thread", old)
    monkeypatch.setitem(mod.PROFILER, "stop", threading.Event())
    first = threading.Thread(target=lambda: mod.profiler_set(False, "test", "loopback"), daemon=True)
    first.start()
    time.sleep(0.1)
    second = threading.Thread(target=lambda: mod.profiler_set(False, "test", "loopback"), daemon=True)
    second.start()
    second.join(0.5)
    waited = second.is_alive()
    release.set()
    first.join(3)
    second.join(3)
    assert waited, "the second off returned while the sampler still ran"
    assert not first.is_alive() and not second.is_alive() and not mod.profiler_running()


def test_an_off_write_whose_sampler_outlives_the_wait_says_so(hub, monkeypatch):
    """The join is bounded (ADR-0051): a read that never returns must not hold the request, and the answer says the
    sampler is still running."""
    mod, port, _, _, _ = hub
    release, inside = threading.Event(), threading.Event()

    def stuck():
        inside.set()
        release.wait(5)
    old = threading.Thread(target=stuck, daemon=True)
    old.start()
    assert inside.wait(5)
    monkeypatch.setitem(mod.PROFILER, "thread", old)
    monkeypatch.setitem(mod.PROFILER, "stop", threading.Event())
    monkeypatch.setattr(mod, "PROFILER_JOIN_S", 0.2)
    code, out = ask(port, "POST", {"on": False})
    release.set()
    old.join(3)
    assert code == 200 and out.get("sampler") == "running", "the answer does not say the sampler outlived the wait"
    assert "still running" in out.get("sampler_text", ""), "the answer carries no words for the sampler it left"


def test_a_sampler_stops_when_the_switch_file_says_off_whoever_wrote_it(hub):
    """Another hub process on this state folder, or a hand edit, writes the switch file; the sampler reads it as truth."""
    mod, port, _, state, table = hub
    assert ask(port, "POST", {"on": True})[0] == 200
    wait(lambda: table.reads >= 2)
    (state / "profiler.json").write_text(json.dumps({"on": False}))
    wait(lambda: not mod.profiler_running())


def test_a_quoted_argument_of_a_nested_shell_names_nothing(prof):
    assert prof.label("bash -c cd /x && bash -c echo \"z; bash /x/watch.sh\"") is None


def test_a_redirect_after_a_shell_typed_code_reads_the_line_as_the_process_table_prints_it(prof):
    """A quoted code followed by any shell operator is not the typed shape: the line names what it redirects from."""
    assert prof.label("bash -c 'echo hi' 2>/x/watch.sh") == ("watch.sh", "driver")
    assert prof.label("bash -c 'echo hi' > /x/branchland.sh") == ("branchland.sh", "driver")
    assert prof.label("bash -c cd /x && bash -c \"cd /y && bash /x/watch.sh\"") == ("watch.sh", "driver")


def test_a_reused_pid_in_the_pid_file_gives_this_loop_no_supervisor(prof):
    """A stale pid file whose pid another program took names no parent: the loop above it is another loop."""
    a = rd({30: (1, 0.0, f"bash {S}/watchd.sh run"), 31: (30, 0.0, "sleep 9")})
    b = rd({30: (1, 1.0, f"bash {S}/watchd.sh run"), 31: (30, 0.0, "sleep 9")})
    acc = prof.fold(a, b, prof.Scope(31, (f"{S}/",)))
    assert names(prof.tree(acc)) == []


def test_a_node_carries_the_starts_observed_under_it_a_second_over_the_window(prof, scope):
    """Observed starts a second: the sampler's own lower bound, so the page never reads a count as the whole truth."""
    cmd = f"bash {S}/watch.sh"
    w = prof.Window(span=10)
    w.reset(100.0)
    w.add(100.0, rd({9: (1, 0.0, cmd)}), scope)
    w.add(102.0, rd({9: (1, 0.0, cmd), 50: (9, 0.0, "true"), 51: (9, 0.0, "true")}), scope)
    t = w.tree()
    watch = node(t, "watch.sh")
    assert watch["starts_s"] == pytest.approx(1.0)            # its own two starts over the two-second window
    assert watch["total_starts_s"] == pytest.approx(1.0)      # with its children, none here
    assert t["starts_s"] == 0 and t["total_starts_s"] == pytest.approx(1.0)
    assert prof.tree(prof.fold(FIRST, SECOND, scope))["total_starts_s"] == 0, "no window, no rate: never a guessed one"


def test_no_node_carries_a_count_of_unseen_processes(prof, scope):
    """A pid gap carries no parent and no command, so the page never draws an unseen count under a node."""
    t = prof.tree(prof.fold(FIRST, SECOND, scope), window_s=10)

    def walk(n):
        yield n
        for k in n["children"]:
            yield from walk(k)
    assert all("unseen" not in n for n in walk(t)), "an unseen start has no node to sit under"


def test_the_starts_column_is_marked_a_lower_bound():
    """A process that lives between two readings is never seen, so the column is a floor, never a measure."""
    out = tree_part(run_js(f"profHtml({json.dumps(VIEW)},new Set())"))
    head = re.search(r"<th[^>]*>Starts/s \(lower bound\)</th>", out)
    assert head and "lower bound" in head.group(0)
