"""The hub's Loop tab (#1743): the loop of this profile as a hot-spot tree, with its own switch, `CPU sampling`.

The fold from process-table readings to the tree is a pure module beside the hub (ADR-0137), so its tests feed readings in
and read no real process table. The route is tested over HTTP: off is the default and reads nothing, a switch write is
audited, and a tailnet write is refused without its own grant, `profiler_write`. The page's renderer runs in node.
"""
import datetime as dt
import http.client
import importlib.util
import inspect
import json
import os
import pathlib
import re
import shutil
import stat
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


# ---------------------------------------------------------------- the other profiles' loops (#1753)
# With several profiles on one machine, the context row splits: one row for each other profile's loop, told by that
# profile's supervisor pid and run folders, and one row for the rest, grouped by application.
OTHERS = (("ariel", 50, ("/Users/o/ariel/runs/",)), ("zesty", 80, ("/Users/o/zesty/runs/",)))


def test_two_profiles_that_run_the_same_script_give_two_rows_one_under_each_loop(prof):
    scope = prof.Scope(10, (S + "/", CO + "/runs/"), OTHERS)
    a = rd({10: (1, 0.0, f"bash {S}/watchd.sh supervise"),
            50: (1, 0.0, f"bash {S}/watchd.sh supervise"), 51: (50, 0.0, f"bash {S}/watch.sh"),
            80: (1, 0.0, f"bash {S}/watchd.sh supervise"), 81: (80, 0.0, f"bash {S}/watch.sh")})
    b = rd({10: (1, 1.0, f"bash {S}/watchd.sh supervise"),
            50: (1, 2.0, f"bash {S}/watchd.sh supervise"), 51: (50, 3.0, f"bash {S}/watch.sh"),
            80: (1, 4.0, f"bash {S}/watchd.sh supervise"), 81: (80, 5.0, f"bash {S}/watch.sh")})
    acc = prof.fold(a, b, scope)
    assert names(prof.tree(acc)) == ["watchd.sh"], "the tree stays this profile's loop alone"
    assert [(c["name"], c["kind"], c["total_s"]) for c in prof.context(acc)["children"]] == [
        ("loop zesty", "loop", 9.0), ("loop ariel", "loop", 5.0)]


def test_an_other_profile_s_loop_is_told_by_its_run_folders_alone(prof):
    """The process names another profile's run folder, with no supervisor above it: it is that loop's work, not the rest."""
    scope = prof.Scope(None, (), OTHERS)
    ariel = "/x/.venv/bin/python -m squad start --graph /Users/o/ariel/runs/run-1/graph.json"
    zesty = "node /Users/o/zesty/runs/run-2/x.js"
    a = rd({60: (1, 0.0, ariel), 61: (1, 0.0, zesty)})
    b = rd({60: (1, 1.0, ariel), 61: (1, 4.0, zesty)})
    assert [(c["name"], c["total_s"]) for c in prof.context(prof.fold(a, b, scope))["children"]] == [
        ("loop zesty", 4.0), ("loop ariel", 1.0)]


def test_the_rest_of_the_context_row_stays_one_row_per_application(prof):
    scope = prof.Scope(10, (S + "/", CO + "/runs/"), OTHERS)
    a = rd({10: (1, 0.0, f"bash {S}/watchd.sh supervise"), 50: (1, 0.0, f"bash {S}/watchd.sh supervise"),
            90: (1, 0.0, "bash /elsewhere/watchd.sh supervise"), 30: (1, 0.0, "/Applications/Foo Bar.app/Contents/Foo Bar"),
            40: (1, 0.0, "/usr/bin/vim /tmp/x")})
    b = rd({10: (1, 1.0, f"bash {S}/watchd.sh supervise"), 50: (1, 2.0, f"bash {S}/watchd.sh supervise"),
            90: (1, 3.0, "bash /elsewhere/watchd.sh supervise"), 30: (1, 4.0, "/Applications/Foo Bar.app/Contents/Foo Bar"),
            40: (1, 5.0, "/usr/bin/vim /tmp/x")})
    assert [(c["name"], c["kind"], c["total_s"]) for c in prof.context(prof.fold(a, b, scope))["children"]] == [
        ("vim", "other", 5.0), ("Foo Bar", "other", 4.0), ("another loop", "loop", 3.0), ("loop ariel", "loop", 2.0)]


def test_a_refused_cpu_read_lowers_the_coverage_and_adds_no_zero_cost_row(prof, scope):
    """A process whose CPU read the system refuses is seen and not read: it lowers the coverage, and it is no row."""
    a = rd({10: (1, 0.0, f"bash {S}/watchd.sh supervise"), 11: (10, 0.0, f"bash {S}/watch.sh"),
            30: (1, 1.0, "/Applications/Foo Bar.app/Contents/Foo Bar")})
    b = rd({10: (1, 1.0, f"bash {S}/watchd.sh supervise"), 11: (10, 2.0, f"bash {S}/watch.sh"),
            12: (11, None, f"python3 {S}/janitor.py"),                 # the task read is refused
            30: (1, 1.5, "/Applications/Foo Bar.app/Contents/Foo Bar"),
            40: (1, None, "/usr/bin/vim /tmp/watch.sh")})              # the task read is refused
    acc = prof.fold(a, b, scope)
    assert names(prof.tree(acc)) == ["watchd.sh"]
    assert node(prof.tree(acc), "watchd.sh", "watch.sh")["children"] == [], "a refused process is no zero-cost row"
    assert [c["name"] for c in prof.context(acc)["children"]] == ["Foo Bar"]
    w = prof.Window()
    w.add(1.0, a, scope)
    w.add(2.0, b, scope)
    s = w.stats()
    assert (s["processes_read"], s["processes_seen"]) == (6, 8), "eight processes seen, six of them read with their CPU"
    assert s["coverage"] == pytest.approx(0.75)


def test_a_window_that_read_every_process_answers_the_full_coverage(prof, scope):
    w = prof.Window()
    w.add(1.0, FIRST, scope)
    w.add(2.0, SECOND, scope)
    s = w.stats()
    assert s["processes_read"] == s["processes_seen"] == len(FIRST) + len(SECOND) and s["coverage"] == 1.0


def test_the_coverage_reads_one_snapshot_of_the_window_while_the_sampler_prunes_it(prof, scope):
    """The sampler thread drops readings past the span while a GET reads the stats: read and seen come from one copy
    of the window, so the pair never mixes two window sizes (CI: 14 seen against 9 read)."""
    import collections

    class Pruning(collections.deque):
        def __iter__(self):   # the sampler prunes the oldest reading between two walks of the window
            items = list(super().__iter__())
            if len(self) > 1:
                self.popleft()
            return iter(items)

    w = prof.Window()
    w.add(1.0, FIRST, scope)
    w.add(2.0, SECOND, scope)
    w.add(3.0, FIRST, scope)
    w.cov = Pruning(w.cov)
    s = w.stats()
    assert s["coverage"] == 1.0 and s["processes_read"] == s["processes_seen"], s


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


def test_the_label_memo_answers_the_fold_the_plain_path_answers(prof, scope):
    """The memo is a cache over the same parser: one reading folds to the same tree with and without it, warm or cold."""
    plain = prof.fold(FIRST, SECOND, scope, memo=None)
    memo = {}
    assert prof.fold(FIRST, SECOND, scope, memo=memo) == plain
    assert prof.tree(prof.fold(FIRST, SECOND, scope, memo=memo)) == prof.tree(plain), \
        "a warm memo answers the reading the cold one answered"


def test_a_label_is_built_once_per_distinct_argv(prof, scope, monkeypatch):
    """Each process of a reading is labelled once; the next reading of the same argv reuses that label, and an exec on
    the same pid names a new argv, so it is labelled then."""
    calls = []
    real = prof._parse

    def counted(command):
        calls.append(command)
        return real(command)
    monkeypatch.setattr(prof, "_parse", counted)
    a = rd({10: (1, 0.0, f"/bin/bash {S}/watchd.sh supervise"), 11: (10, 0.0, f"bash {S}/watch.sh")})
    b = rd({10: (1, 1.0, f"/bin/bash {S}/watchd.sh supervise"), 11: (10, 1.0, f"bash {S}/watch.sh")})
    c = rd({10: (1, 2.0, f"/bin/bash {S}/watchd.sh supervise"), 11: (10, 2.0, f"bash {S}/gate.sh")})
    memo = {}
    prof.fold(a, b, scope, memo=memo)
    assert sorted(calls) == sorted({row[2] for row in b.values()}), "one label per distinct argv of the reading"
    calls.clear()
    prof.fold(b, c, scope, memo=memo)
    assert calls == [f"bash {S}/gate.sh"], "the same argv reuses its label; an exec names a new one"


def test_the_memo_drops_a_process_that_leaves_the_reading(prof, scope):
    """The memo follows the table: a process the reading no longer holds leaves the memo with it, and it keeps no argv."""
    memo = {}
    prof.fold(FIRST, SECOND, scope, memo=memo)
    assert any(key[0] == 20 for key in memo)
    gone = {k: v for k, v in SECOND.items() if k[0] != 20}
    prof.fold(SECOND, gone, scope, memo=memo)
    assert not any(key[0] == 20 for key in memo), "a process that left the reading is dropped"
    assert all(isinstance(key, tuple) and len(key) == 4 for key in memo), "the key is (pid, start, length, hash)"
    assert not any(isinstance(part, str) and "/" in part for key in memo for part in key), "no argv is kept"


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
    assert w.stats() == {"last_reading": None, "interval_s": None, "failed_readings": 0,
                         "processes_read": 0, "processes_seen": 0, "coverage": None}
    w.add(100.0, rd({9: (1, 1.0, "true")}), scope)
    assert w.stats() == {"last_reading": 100.0, "interval_s": None, "failed_readings": 0,
                         "processes_read": 1, "processes_seen": 1, "coverage": 1.0}
    w.fail()
    w.add(102.5, rd({9: (1, 1.0, "true")}), scope)
    assert w.stats() == {"last_reading": 102.5, "interval_s": 2.5, "failed_readings": 1,
                         "processes_read": 2, "processes_seen": 2, "coverage": 1.0}
    w.reset(200.0)
    assert w.stats() == {"last_reading": None, "interval_s": None, "failed_readings": 0,
                         "processes_read": 0, "processes_seen": 0, "coverage": None}


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
        self.refused = set()                               # the pids whose CPU read the system refuses
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
            p.cpu_known = pid not in self.refused
            rows[pid] = p
        return rows


@pytest.fixture
def hub(served, monkeypatch):
    mod, port, tn, state = served
    # The record's rows live in the module's state and every fixture starts with no record: a case that writes one must
    # never hand its rows to the next (#1768).
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    table = Table()
    monkeypatch.setattr(mod, "process_table", table)
    for key in ("loop_write", "profiler_write"):
        tn.pop(key, None)
    yield mod, port, tn, state, table
    table.before = None
    mod.profiler_set(False, "test", "loopback")
    wait(lambda: not mod.profiler_running())
    for name in ("profiler.json", "profiler-audit.jsonl", "watchd.pid", "load-record.tsv"):
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


def test_two_readings_of_one_window_are_never_closer_than_one_period(hub, monkeypatch):
    mod, port, _, _, table = hub
    window, calls, intervals = mod.PROFILER["window"], [], []
    add = window.add

    def spy(now, reading, scope):
        add(now, reading, scope)
        intervals.append(window.interval)

    def read():
        calls.append(1)
        if len(calls) == 1:   # the first read overruns the period; the next answers at once
            time.sleep(0.05)
        return table()

    monkeypatch.setattr(window, "add", spy)
    monkeypatch.setattr(mod, "profiling_table", read)
    ask(port, "POST", {"on": True})
    wait(lambda: len(intervals) >= 2)
    assert intervals[1] >= mod.PROFILER_SECS


def test_the_scope_names_each_other_profile_s_loop_by_its_supervisor_and_run_folders(hub, tmp_path, monkeypatch):
    """The hub serves every profile, so this tab's context row names each other profile's loop: its supervisor's pid from
    that profile's own state folder, and the folders only that profile's work names."""
    mod, *_ = hub
    state = tmp_path / "zesty-state"
    state.mkdir()
    (state / "watchd.pid").write_text("4243 supervise\n")
    (mod.STATE / "watchd.pid").write_text("7777 supervise\n")
    monkeypatch.setattr(mod, "project_contexts", lambda: [
        {"slug": "alpha", "state": str(mod.STATE), "checkout": mod.CHECKOUT, "own": True},
        {"slug": "zesty", "state": str(state), "checkout": "/Users/o/zesty", "own": False}])
    mod.PROFILER_OTHERS.update(at=0.0, rows=())
    sc = mod.profiler_scope()
    assert sc.supervisor == 7777, "this profile's own supervisor is the pid in its own state folder"
    assert [name for name, _, _ in sc.others] == ["zesty"], "this profile is never one of the others"
    assert 7777 not in [pid for _, pid, _ in sc.others]
    assert sc.others == (
        ("zesty", 4243, ("/Users/o/zesty/", "/Users/o/zesty/runs/", "/Users/o/zesty/worktrees/run/",
                         "/Users/o/zesty/worktrees/child/", "/Users/o/zesty/contrib/squad-goal/scripts/")),)


def test_a_seam_that_refuses_keeps_the_last_good_other_loops(hub, monkeypatch):
    """A transient refusal is never answered as one profile on the machine: the rows last read stand."""
    mod, *_ = hub
    good = (("zesty", 4243, ("/Users/o/zesty/",)),)

    def refuse():
        raise OSError("the seam refused")

    monkeypatch.setattr(mod, "project_contexts", refuse)
    mod.PROFILER_OTHERS.update(at=0.0, rows=good)
    assert mod.profiler_others(now=1e9) == good


def test_an_other_profile_s_work_is_named_by_its_checkout_and_its_scripts(hub, tmp_path, monkeypatch):
    """Two profiles run the same scripts from their own checkouts: a process that names the other checkout is that
    profile's loop, never the row for the rest of the machine."""
    mod, *_ = hub
    state = tmp_path / "zesty-state"
    state.mkdir()
    (state / "watchd.pid").write_text("50\n")
    monkeypatch.setattr(mod, "project_contexts", lambda: [
        {"slug": "zesty", "state": str(state), "checkout": "/Users/o/zesty", "own": False}])
    mod.PROFILER_OTHERS.update(at=0.0, rows=())
    scope = mod.profiler_scope()
    script = "/Users/o/zesty/contrib/squad-goal/scripts/watch.sh"
    a = {(90, 1.0): (1, 0.0, f"bash {script}")}
    b = {(90, 1.0): (1, 2.0, f"bash {script}")}
    ctx = mod.hub_profiler.context(mod.hub_profiler.fold(a, b, scope))
    assert [(c["name"], c["kind"], c["total_s"]) for c in ctx["children"]] == [("loop zesty", "loop", 2.0)]


def test_the_answer_splits_the_context_row_by_each_other_profile_s_loop(hub, tmp_path, monkeypatch):
    mod, port, _, state, table = hub
    other = tmp_path / "zesty-state"
    other.mkdir()
    (other / "watchd.pid").write_text("50\n")
    (state / "watchd.pid").write_text("8\n")
    monkeypatch.setattr(mod, "project_contexts", lambda: [
        {"slug": "zesty", "state": str(other), "checkout": "/Users/o/zesty", "own": False}])
    mod.PROFILER_OTHERS.update(at=0.0, rows=())
    table.rows = {8: (1, f"bash {SCRIPTS}/watchd.sh supervise"), 50: (1, f"bash {SCRIPTS}/watchd.sh supervise"),
                  51: (50, f"bash {SCRIPTS}/watch.sh"), 9: (1, "/Applications/Foo.app/Contents/MacOS/Foo")}
    ask(port, "POST", {"on": True})
    wait(lambda: table.reads >= 3)
    v = ask(port, "GET")[1]
    assert [c["name"] for c in v["tree"]["children"]] == ["watchd.sh"], "another profile's loop is no node of this tree"
    assert [(c["name"], c["kind"]) for c in v["context"]["children"]] == [("loop zesty", "loop"), ("Foo", "other")]


def test_a_refused_cpu_read_lowers_the_coverage_in_the_answer(hub):
    """The answer says how much of the machine it could read: the processes read with their CPU over every process seen."""
    mod, port, _, _, table = hub
    table.refused = {9}
    ask(port, "POST", {"on": True})
    wait(lambda: table.reads >= 3)
    v = ask(port, "GET")[1]
    assert v["processes_seen"] == 2 * v["processes_read"] and v["processes_read"] >= 1
    assert v["coverage"] == pytest.approx(0.5)
    assert [c["name"] for c in v["context"]["children"]] == [], "a refused read adds no zero-cost row"


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
    """The desktop page's source with the shared Loop view it inlines (#1754)."""
    return HUB_PY.read_text() + (SCRIPTS / "hub-profiler.js").read_text() + (SCRIPTS / "hub-profiler.css").read_text()


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
    between them. The entry opens the Loop layer on the phone (#1754); it is no link to the desktop view."""
    html = (SCRIPTS / "hub-mobile.html").read_text()
    menu = html[html.index('<div id="more-menu">'):]
    menu = menu[:menu.index("</div>")]
    assert re.findall(r">([^<>\n]+)</(?:button|a)>", menu) == ["Cost and usage", "Lead time", "Loop", "Settings"]
    assert re.search(r'<button id="more-loop" data-more="profiler" type="button">Loop</button>', menu), "the entry opens a layer, not a link"
    assert 'more-loop").onclick' not in html, "the entry is no longer a link to the desktop view"


def test_the_page_polls_only_while_the_tab_shows_and_the_switch_is_on():
    src = page()
    assert re.search(r'fetch\("/api/profiler\?w="\+profWin\)', src), "the read carries the profiler's window"
    assert re.search(r'fetch\("/api/profiler",\{method:"POST"', src)
    assert re.search(r'^const profDue=\(tab,hidden,v\)=>tab==="prof"&&!hidden&&!!v&&v\.on;', src, re.M)


def run_js(expr):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    js = ("const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'})[c]);\n"
          + (SCRIPTS / "hub-profiler.js").read_text() + f"\nprocess.stdout.write(String({expr}));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return r.stdout


def leaf(name, self_s, **kw):
    return {"name": name, "kind": "driver", "self_s": self_s, "total_s": self_s, "share": 0.25, "events": 4, "total_events": 4,
            "children": [], **kw}


VIEW = {"on": True, "since": 1.0, "window_s": 125, "write": True, "reason": "",
        "last_reading": 1700000000.0, "interval_s": 1.04, "failed_readings": 0,
        "processes_read": 21, "processes_seen": 21, "coverage": 1.0,
        "context": {"name": "outside the loop", "total_s": 40.0, "events": 9, "children": [
            {"name": "Foo Bar", "kind": "other", "total_s": 30.0, "events": 2},
            {"name": "another loop", "kind": "loop", "total_s": 10.0, "events": 7}]},
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


def test_the_view_states_how_much_of_the_machine_it_could_read():
    def note(v):
        return re.search(r"<span class=lpn>(.*?)</span>", run_js(f"profHtml({json.dumps(v)},new Set())"), re.S).group(1)
    assert "Read 94.0% of the processes." in note({**VIEW, "coverage": 0.94})
    assert "Read every process." in note({**VIEW, "coverage": 1.0})


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


def test_the_trip_spans_of_a_fixture_record_are_its_stages(prof):
    spans = prof.trip_spans(rec())
    assert spans == {"queue": None, "wait": 30.0, "coding": 200.0, "between": 0.0, "review": 56.0, "gates": 90.0,
                     "landing": 150.0, "landing_stint": None, "landing_from": None, "missing": ["todo", "closed"], "end": 1500.0}, \
        "with no card read, queue and the Landing stint are None and both card moves are missing; the one child holds no gap, so between is a measured zero"
    assert "after" not in spans, "the time after the verdict is no stage of its own (#2292): the card's Landing stint holds it"


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


def test_the_fold_names_the_wait_before_the_run_and_between_two_children(prof):
    """#1894: a card's trip is longer than its run's span. The wait before the run and the gap between two children are
    stages of their own, never folded away: a 40 min queue and a 20 min gap come out as 40 and 20 minutes."""
    todo = 10_000.0
    evs = [ev("run_start", todo + 2400.0),                                      # queue: the 40 min before the run
           ev("spawn_accepted", todo + 2405.0, child="code.1"),
           ev("call_start", todo + 2410.0, node="code.1", entry=1),
           ev("outcome", todo + 3010.0, node="code.1", entry=1, outcome="pass"),  # code.1's span: 600 s
           ev("spawn_accepted", todo + 4000.0, child="code.2"),
           ev("call_start", todo + 4210.0, node="code.2", entry=1),              # between: 4210 - 3010 = 1200 s
           ev("outcome", todo + 4810.0, node="code.2", entry=1, outcome="pass"),
           ev("land", todo + 4900.0, node="code.2", outcome="pass"),
           ev("verdict", todo + 5000.0, verdict="completed", exit_code=0, reasons=[])]
    spans = prof.trip_spans(evs, {"todo": todo, "closed": todo + 5600.0, "landing": 600.0})
    assert spans["queue"] == 2400.0, "the 40 minutes the card waited before its run started"
    assert spans["between"] == 1200.0, "the 20 minutes between code.1's last answer and code.2's first call"
    assert spans["landing_stint"] == 600.0, "the card's 10 minutes in Landing, read from the board history (#2292)"


def test_a_card_that_waits_for_nothing_answers_zeros_never_a_missing_stage(prof):
    """The waiting stages are named on every delivery: a card that never waited answers 0, never a missing stage."""
    spans = prof.trip_spans(rec(), {"todo": 1000.0, "closed": 1500.0, "landing": 0.0})
    assert (spans["queue"], spans["between"], spans["landing_stint"]) == (0.0, 0.0, 0.0)


def test_a_card_with_no_record_answers_unknown_and_names_both_missing_moves(prof):
    spans = prof.trip_spans(rec())
    assert (spans["queue"], spans["landing_stint"]) == (None, None), "a card the board never read is unknown, never zero"
    assert spans["missing"] == ["todo", "closed"], "the card's Todo and Closed moves are the inputs it lacks"


def test_a_card_with_both_moves_names_no_missing_input(prof):
    assert prof.trip_spans(rec(), {"todo": 1000.0, "closed": 1500.0})["missing"] == []


def test_a_fixture_delivery_bar_total_equals_the_stages_its_columns_hold(prof):
    """The bar total is the sum of the stage medians of the one delivery, a column's segment holding all of its stages,
    so no measurement the panel draws leaves it. The record's merge into the wave stands in Doing, and the card's 10 s
    Landing stint after the verdict stands in Landing (#2292)."""
    evs = [ev("run_start", 100.0), ev("spawn_accepted", 110.0, child="code.1"),
           ev("call_start", 110.0, node="router", entry=1),
           ev("call_start", 110.0, node="code.1", entry=1), ev("outcome", 320.0, node="code.1", entry=1, outcome="pass"),
           ev("land", 330.0, node="code.1", outcome="pass"), ev("verdict", 330.0, verdict="completed", exit_code=0, reasons=[])]
    card = {"todo": 0.0, "closed": 340.0, "landing": 10.0}
    t = prof.trip([prof.trip_spans(evs, card)], [], since=0.0)
    bar_total = sum(c["sum_s"] for c in t["columns"] if c["sum_s"] is not None)
    # Every stage of the one delivery, named so the 340 s is no coincidence of this fixture: queue 100 s, wait 10 s,
    # coding 210 s, between 0 s, the merge into the wave 10 s, and the card's 10 s Landing stint. `review` and `gates`
    # read no time here, and no gate of the wave is read, so Gating reads none.
    assert {c["column"]: c["sum_s"] for c in t["columns"]} == {
        "todo": 110.0, "doing": 220.0, "review": None, "gating": None, "landing": 10.0}, \
        "each column's segment is its own stages: the queue 100 s and the dispatch wait 10 s, coding 210 s, the " \
        "0 s between two children and the 10 s merge in Doing, the 10 s Landing stint; absent stages read no time"
    assert bar_total == card["closed"] - card["todo"] == 340.0, \
        "the bar covers the card's whole span: the time after the verdict is the card's Landing stint"


def test_the_percentile_is_the_linear_rule_the_doc_names(prof):
    assert prof.percentile([], 90) is None
    assert prof.percentile([10], 50) == 10
    assert prof.percentile([10, 20, 30], 50) == 20
    assert prof.percentile([10, 20, 30], 90) == pytest.approx(28.0), "the (n - 1) weight the trip table's p90 uses"
    assert prof.percentile([4, 1, 3, 2], 90) == pytest.approx(3.7)   # sorted 1,2,3,4: 1 + 3 * 0.9


TRIP_SPANS = [{"queue": 2400.0, "wait": 10.0, "coding": 100.0, "between": 60.0, "review": 20.0, "gates": None,
               "landing": 30.0, "landing_stint": 30.0, "end": NOW - 100},
              {"queue": 2400.0, "wait": 20.0, "coding": 200.0, "between": 120.0, "review": 40.0, "gates": 60.0,
               "landing": 60.0, "landing_stint": 60.0, "end": NOW - 200},
              {"queue": 2400.0, "wait": 30.0, "coding": 300.0, "between": 180.0, "review": 60.0, "gates": 120.0,
               "landing": 90.0, "landing_stint": 90.0, "end": NOW - 300}]


def definition_statuses():
    """The board definition's column names, in its order, read from the JSON the board ships (#2199).

    This reads the shipped file itself, never `board_columns`, the module the panel imports: the two sides of the order
    test share no code path, so a change that moves a column in the definition moves only one side of the comparison."""
    return [s["name"] for s in json.loads((SCRIPTS / "board-definition.json").read_text())["statuses"]]


def test_the_board_definition_states_the_column_order_this_file_holds():
    """The expected order is a literal, and the definition is cross-checked against it (#2199). A definition that
    reorders or loses a column fails here, so the panel test below can never agree with a moved board."""
    assert definition_statuses() == ["Backlog", "Todo", "Doing", "Review", "Landing", "Gating", "Closed", "Abandoned"]


# The delivery columns the panel draws, in the board's order (#2199): a literal the panel never reads, so the order test
# below compares two independent sources. Backlog is not in a delivery's trip, Abandoned is not delivered, and Closed
# draws no segment and no row because the time after the verdict is not a column the card waits in.
TRIP_DRAWN = ("Todo", "Doing", "Review", "Landing", "Gating")


def test_the_trip_panel_lists_its_columns_in_the_board_definitions_order(prof):
    """#2199: the panel's order is the board's order. The literal is pinned to the definition above, so Landing stands
    before Gating here only because the board puts it there; a panel that keeps its own list fails this test."""
    assert set(TRIP_DRAWN) <= set(definition_statuses()), \
        "every column this literal names is a definition column: a typo here would blame the panel for a test fault"
    assert all(prof.TRIP_COLUMN_STAGES.get(n) for n in TRIP_DRAWN), \
        "every column this literal names folds a real stage in the trip's table, so the panel draws it"
    t = prof.trip(TRIP_SPANS, [], since=NOW - 3600)
    labels = [r["label"] for r in t["columns"]]
    assert labels == list(TRIP_DRAWN), "the panel's rows follow the board's column order, never a list of its own"
    assert [r["column"] for r in t["columns"]] == [n.lower() for n in labels]


def test_a_board_column_the_trip_has_no_stage_for_is_refused_never_dropped(prof):
    """#2199: a new board column is ordered by the definition, so the panel must not lose it in silence: a column the
    trip's stage table does not name raises, and the reader names the column that needs a stage."""
    data = json.loads((SCRIPTS / "board-definition.json").read_text())
    data["statuses"].append({"name": "Escalated", "color": "RED", "fact": "closed", "terminal": False})
    with pytest.raises(ValueError, match="Escalated"):
        prof.trip_columns(json.dumps(data))


def test_the_trip_panel_draws_no_closed_column(prof):
    """#2199: the time after the verdict is not a column the card waits in, so the panel draws no Closed segment and no
    Closed row, and the columns it draws are every definition column the trip's one stage table folds a stage for, and
    only those. This file keeps no exclusion list: a new column that the table maps to a stage is drawn, and a column it
    maps to None is not, so a future column needs no edit here."""
    t = prof.trip(TRIP_SPANS, [], since=NOW - 3600)
    assert "closed" not in [r["column"] for r in t["columns"]]
    assert "Closed" not in [r["label"] for r in t["stages"]]
    drawn = {s["name"] for s in prof._board_columns.shipped_columns()
             if prof.TRIP_COLUMN_STAGES.get(s["name"])}
    assert {r["label"] for r in t["columns"]} == drawn, \
        "the panel draws every definition column the trip's table folds a stage for, and no other"


def test_the_trip_table_names_every_definition_column_and_folds_none_for_a_column_a_delivery_never_stands_in(prof):
    """#2199: the trip's stage table is the one membership list, so it names every column the definition ships (a column
    it misses raises, never drops), and the columns it maps to no stage are exactly the three a delivery never stands in:
    Backlog before a dispatch, Closed after the verdict, and Abandoned. A new column mapped to None breaks this test, so
    the author states why the panel leaves it out."""
    assert set(prof.TRIP_COLUMN_STAGES) == set(definition_statuses()), \
        "the table names every definition column and no column the definition lost"
    assert {n for n, stages in prof.TRIP_COLUMN_STAGES.items() if not stages} == {"Backlog", "Closed", "Abandoned"}, \
        "only these three columns are left out of the trip, each for a reason this test names"


def test_the_runs_own_gates_fold_into_the_column_the_card_holds_while_they_run(prof):
    """#2199: the record's gates run inside the run, so they fold into Doing, the column the card holds while they run.
    Gating is the wave's gate, read from the gate history; with none, it reads no gate at all."""
    spans = [{**s, "gates": 90.0} for s in TRIP_SPANS]
    rows = {r["column"]: r for r in prof.trip(spans, [], since=NOW - 3600)["columns"]}
    assert rows["gating"]["observations"] == 0 and rows["gating"]["median_s"] is None, \
        "the run's own gates never fold into Gating"
    assert rows["doing"]["sum_s"] == 200.0 + 120.0 + 90.0 + 60.0, \
        "the gates fold into Doing: coding 200 s and between 120 s, the gate's 90 s and the 60 s merge into the wave"


def wave_row(start, finish, caller="tip", waves=("run-a",), card=None, wait=0.0):
    """One gate-history row as `hub.trip_gates` answers it, with the wave link the attempt journal gives it."""
    return {"start": start, "finish": finish, "caller": caller, "wait": wait,
            "link": {"waves": list(waves), "card": card} if waves or card else None}


WAVES = [{"wave": "run-a", "issues": [1]}, {"wave": "run-b", "issues": [2]}, {"wave": "run-c", "issues": [3]}]


def test_the_wave_gate_folds_into_gating(prof):
    """#2292: Gating is the wave's own gate before the push (ADR-0170): for each delivery, the slot wait plus the run of
    the tip and landing rows of the wave that carried it. Every column counts deliveries, Gating too."""
    gates = [wave_row(NOW - 500, NOW - 440, waves=(w["wave"],), wait=30.0) for w in WAVES]
    rows = {r["column"]: r for r in prof.trip(TRIP_SPANS, gates, since=NOW - 3600, waves=WAVES)["columns"]}
    assert (rows["gating"]["observations"], rows["gating"]["median_s"]) == (3, 90.0), \
        "each wave's 30 s slot wait and 60 s run stand in Gating; the record's own gates stand in Doing"


def test_gating_reads_only_the_tip_and_landing_rows_of_the_wave_that_carried_the_issue(prof):
    """#2292: child, entry and other rows never count; a row of another wave never counts; a landing row counts for the
    card it names, after the run's verdict; a row whose link the journal does not name adds nothing, never a zero."""
    one = [TRIP_SPANS[0]]
    end = one[0]["end"]

    def gating(gates, waves=WAVES[:1]):
        return {r["column"]: r for r in prof.trip(one, gates, since=NOW - 3600, waves=waves)["columns"]}["gating"]
    for caller in ("child", "entry", "other"):
        assert gating([wave_row(end, end + 60, caller=caller)])["median_s"] is None, f"a {caller} row never counts"
    assert gating([wave_row(end, end + 60, waves=("run-z",))])["median_s"] is None, "another wave's gate never counts"
    assert gating([wave_row(end, end + 60, waves=())])["observations"] == 0, \
        "a row with no wave link adds nothing: no reading, never a zero"
    assert gating([wave_row(end + 10, end + 70, caller="landing", waves=(), card=1)])["median_s"] == 60.0, \
        "a landing gate counts for the card it names"
    assert gating([wave_row(end - 100, end - 40, caller="landing", waves=(), card=1)])["median_s"] is None, \
        "a landing gate of the card before this run's verdict belongs to an earlier attempt"
    both = [wave_row(end, end + 60, wait=20.0), wave_row(end + 100, end + 130, caller="landing", waves=(), card=1)]
    assert gating(both)["median_s"] == 110.0, "one delivery's Gating is the sum of its wave's gates: 20 + 60 + 30 s"
    assert gating([wave_row(end, end + 60)], waves=None)["median_s"] is None, "a fold with no wave read reads no Gating"


def test_the_wire_keeps_the_drawn_column_rows_under_stages(prof):
    """The wire keeps `stages` as the drawn board-column rows, in board order, each with the keys an older reader of
    `GET /api/profiler` reads, so the payload gains `columns` and breaks no reader of `stages`. No Closed row (#2199).
    The time keys are the four readings of #2280, never a p90."""
    t = prof.trip(TRIP_SPANS, [], since=NOW - 3600)
    assert [r["column"] for r in t["stages"]] == ["todo", "doing", "review", "landing", "gating"], \
        "five rows, in the board's order, with no Closed row"
    assert all(set(r) == {"column", "label", "observations", "min_s", "median_s", "p85_s", "max_s"} for r in t["stages"]), \
        "a stages row keeps exactly the keys the older reader reads"
    todo = t["stages"][0]
    assert (todo["label"], todo["min_s"], todo["median_s"], todo["p85_s"], todo["max_s"], todo["observations"]) == \
        ("Todo", 2400.0, 2400.0, 2400.0, 2400.0, 3), \
        "the Todo row answers its larger stage, the queue at 40 min over three deliveries"


def test_the_record_stages_live_under_their_own_key(prof):
    """The record's own per-stage rows move to `record_stages`, in the record's order, so the new fold keeps a readable
    per-stage table without changing what `stages` means on the wire."""
    t = prof.trip(TRIP_SPANS, [], since=NOW - 3600)
    assert [(r["stage"], r["label"]) for r in t["record_stages"]] == list(prof.TRIP_STAGES_LABELLED)
    rows = {r["stage"]: r for r in t["record_stages"]}
    assert rows["queue"]["median_s"] == 2400.0 and rows["wait"]["median_s"] == 20.0
    assert rows["wait"]["label"] == "Wait for a dispatch"
    assert set(rows["wait"]) == {"stage", "label", "observations", "min_s", "median_s", "p85_s", "max_s", "share"}, \
        "the per-stage row keeps the keys the fold answers"


def test_the_wire_holds_the_drawn_column_rows_even_on_an_empty_window(prof):
    """A window with no delivery still answers the drawn rows, each with no reading, so a cached page reads a table of the
    board's own columns and never an absent key."""
    t = prof.trip([], [], 0.0)
    assert [r["column"] for r in t["stages"]] == ["todo", "doing", "review", "landing", "gating"]
    assert all(set(r) == {"column", "label", "observations", "min_s", "median_s", "p85_s", "max_s"} for r in t["stages"])
    assert t["stages"][0]["median_s"] is None and t["stages"][0]["observations"] == 0


def test_a_tie_in_a_column_hands_the_row_to_the_first_stage_in_board_order(prof):
    """A tie goes to the first stage of the column's own order, `queue` before `wait` and `coding` before `between`; a
    known zero beats an unknown stage, because zero seconds is a reading and None is none."""
    tie = [{"queue": 20.0, "wait": 20.0, "coding": 5.0, "between": 5.0, "review": 1.0, "gates": 1.0,
            "landing": 1.0, "landing_stint": None, "end": NOW - 100}] * 3
    rows = {r["column"]: r for r in prof.trip(tie, [], since=NOW - 3600)["columns"]}
    assert (rows["todo"]["stage"], rows["todo"]["beside"]["stage"]) == ("queue", "wait")
    assert (rows["doing"]["stage"], rows["doing"]["beside"]["stage"]) == ("coding", "between")
    zero = [{**s, "queue": None, "wait": 0.0} for s in tie]
    todo = {r["column"]: r for r in prof.trip(zero, [], since=NOW - 3600)["columns"]}["todo"]
    assert (todo["stage"], todo["median_s"], todo["beside"]) == ("wait", 0.0, None), \
        "a known zero is the row's reading; the unknown stage stands nowhere"


def test_each_column_answers_its_median_and_p85_over_the_deliveries(prof):
    gates = [wave_row(NOW - 500, NOW - 440),                               # 60 s, the wave of the first delivery
             wave_row(NOW - 9000, NOW - 8000, caller="child")]             # a child's own gate: no observation
    t = prof.trip(TRIP_SPANS, gates, since=NOW - 3600, waves=WAVES)
    assert t["deliveries"] == 3 and t["bar"] is True
    rows = {r["column"]: r for r in t["columns"]}
    assert rows["doing"]["median_s"] == 200.0 and rows["doing"]["p85_s"] == pytest.approx(270.0)
    assert rows["review"]["median_s"] == 40.0 and rows["review"]["observations"] == 3
    assert rows["gating"]["median_s"] == 60.0 and rows["gating"]["observations"] == 1, \
        "Gating reads the one delivery whose wave ran a tip gate; the record's own gates stand in Doing (#2292)"
    assert rows["landing"]["p85_s"] == pytest.approx(81.0)
    assert "closed" not in rows, "Closed draws no column (#2199)"
    assert (rows["doing"]["parallelism"], rows["doing"]["effective_s"]) == (1.0, 200.0), \
        "no card runs are read here, so every column runs one issue at a time and its effective time is its median"
    assert rows["doing"]["share"] == pytest.approx(200.0 / 2760.0, abs=1e-3), \
        "the share is the column's effective time over the total (#2256)"
    assert rows["todo"]["share"] == pytest.approx(2400.0 / 2760.0, abs=1e-3), \
        "the queue and the dispatch wait share one column, and its median takes the bar"


def test_the_todo_row_reads_the_queue_with_the_wait_for_a_dispatch_beside_it(prof):
    """#2152: the column's larger reading is its median and p85, and the other stage stands beside it with its own four
    readings (#2280)."""
    todo = {r["column"]: r for r in prof.trip(TRIP_SPANS, [], since=NOW - 3600)["columns"]}["todo"]
    assert (todo["stage"], todo["median_s"], todo["p85_s"]) == ("queue", 2400.0, 2400.0)
    assert todo["beside"] == {"stage": "wait", "label": "Wait for a dispatch", "observations": 3,
                              "min_s": 10.0, "median_s": 20.0, "p85_s": pytest.approx(27.0), "max_s": 30.0}


def test_the_doing_row_reads_coding_with_the_span_between_two_children_beside_it(prof):
    doing = {r["column"]: r for r in prof.trip(TRIP_SPANS, [], since=NOW - 3600)["columns"]}["doing"]
    assert (doing["stage"], doing["median_s"], doing["p85_s"]) == ("coding", 200.0, pytest.approx(270.0))
    assert doing["beside"] == {"stage": "between", "label": "Between two children", "observations": 3,
                               "min_s": 60.0, "median_s": 120.0, "p85_s": pytest.approx(162.0), "max_s": 180.0}


def test_a_column_shows_its_larger_reading_and_the_other_stage_beside_it(prof):
    """The larger reading of the two is the row's median, whichever stage it comes from."""
    spans = [{"queue": 10.0, "wait": 100.0, "coding": 100.0, "between": 5.0, "review": 20.0, "gates": 10.0,
              "landing": 30.0, "landing_stint": 30.0, "end": NOW - 100}] * 3
    rows = {r["column"]: r for r in prof.trip(spans, [], since=NOW - 3600)["columns"]}
    assert (rows["todo"]["stage"], rows["todo"]["median_s"]) == ("wait", 100.0)
    assert (rows["todo"]["beside"]["stage"], rows["todo"]["beside"]["median_s"]) == ("queue", 10.0)
    assert (rows["doing"]["stage"], rows["doing"]["beside"]["stage"]) == ("coding", "between")


def test_every_drawn_stage_lands_in_one_of_the_columns_the_panel_draws(prof):
    """#2152, #2199: the bar folds one segment per drawn column, so no measurement the panel draws leaves it, even a stage
    a column does not name beside its median. One delivery with every stage a distinct length: the drawn column sums add
    up to every stage, the card's Landing stint included (#2292). The run's own gates stand in Doing and the wave's gate
    in Gating."""
    one = [{"queue": 1.0, "wait": 2.0, "coding": 4.0, "between": 8.0, "review": 16.0, "gates": 32.0,
            "landing": 64.0, "landing_stint": 128.0, "end": NOW - 100}] * 3
    t = prof.trip(one, [wave_row(NOW - 200, NOW - 100)], since=NOW - 3600, waves=[WAVES[0]] * 3)   # a 100 s wave gate
    total = sum(c["sum_s"] for c in t["columns"] if c["sum_s"] is not None)
    assert total == 1 + 2 + 4 + 8 + 16 + 32 + 64 + 128 + 100, \
        "every drawn stage stands in a column, so its median adds to the bar, the 128 s Landing stint too"
    assert "after" not in {r["stage"] for r in t["record_stages"]}, \
        "the time after the verdict leaves the record's own stages: the Landing stint measures it (#2292)"


def test_a_run_that_names_no_card_reads_no_column_from_the_card(prof):
    """Criterion 5: the card's own transitions fill the queue. A run that names no card reads no queue: its Todo row
    reads the dispatch wait alone."""
    spans = [{k: v for k, v in s.items() if k not in ("queue", "landing_stint")} for s in TRIP_SPANS]
    rows = {r["column"]: r for r in prof.trip(spans, [], since=NOW - 3600)["columns"]}
    assert (rows["todo"]["stage"], rows["todo"]["median_s"]) == ("wait", 20.0)
    assert rows["todo"]["beside"] is None, "the card's queue is unknown, so no second reading stands beside the wait"


def test_fewer_than_three_deliveries_draw_no_bar(prof):
    t = prof.trip(TRIP_SPANS[:2], [], since=NOW - 3600)
    assert t["deliveries"] == 2 and t["bar"] is False, "a median of two is no profile"


def test_a_delivery_that_ended_before_the_window_is_not_folded(prof):
    old = {"wait": 10.0, "coding": 10.0, "review": 10.0, "gates": 10.0, "landing": 10.0, "end": NOW - 7200}
    assert prof.trip([*TRIP_SPANS, old], [], since=NOW - 3600)["deliveries"] == 3


# ---------------------------------------------------------------- what each column consumes (#2227)
# Three lines stand beside the bar: Tokens, CPU and RAM. A sample goes to the column the card stood in at the sample's
# time: a column run covers [start, end), and the sample's position is (t - start) / (end - start). The column is cut into
# TRIP_SERIES_POINTS bins and each bin's point is the mean of what it holds, so two deliveries of different lengths fold by
# position. Tokens are tokens per minute of the column, and the table's own number is tokens per issue. CPU and RAM are
# shares of the whole machine, read from the minute record; a share of 50% is 0.5.
CS = 1_800_000_000.0            # the window these fixtures fold: one hour, CS to CS + 3600


def card(*runs):
    return [{"column": c, "start": CS + a, "end": CS + b} for c, a, b in runs]


def span_at(end):
    return {"queue": 60.0, "wait": 10.0, "coding": 60.0, "between": 0.0, "review": 10.0, "gates": None,
            "landing": 10.0, "landing_stint": 10.0, "end": CS + end}


def every_minute(upto, load=4.0, cpu=50.0, ram=40.0):
    return [{"at": CS + t, "load": load, "cpu": cpu, "ram": ram} for t in range(60, int(upto), 60)]


# ---------------------------------------------------------------- effective time per delivery (#2256)
# A column's width is its time per issue divided by its parallelism: the mean number of issues standing in the column
# at the same time, over the minutes in which at least one stands. The runs are the card's own column runs, the spans
# the chart already folds, so the fold adds no reader of GitHub.
def doing_column(prof, spans, cards):
    return {r["column"]: r for r in prof.trip(spans, [], since=CS, cards=cards)["columns"]}["doing"]


def test_a_column_divides_its_time_per_issue_by_the_issues_that_stand_in_it_at_once(prof):
    """Two issues stand in Doing for their whole 30 min, so the column runs two at a time: parallelism 2.0, and the
    effective time per delivery is half the 30 min median."""
    spans = [{"queue": None, "wait": None, "coding": 1800.0, "between": 0.0, "review": None, "gates": None,
              "landing": None, "landing_stint": None, "end": CS + 1800.0}] * 2
    both = [[{"column": "Doing", "start": CS, "end": CS + 1800.0}] for _ in range(2)]
    doing = doing_column(prof, spans, both)
    assert doing["median_s"] == 1800.0, "time per issue is the median the chart shows today"
    assert doing["parallelism"] == pytest.approx(2.0), "two issues stand in the column at every minute"
    assert doing["effective_s"] == pytest.approx(900.0), "two at a time deliver the column's time in half"
    assert doing["share"] == pytest.approx(1.0), "the one column with a reading takes the whole bar"


def test_a_column_in_which_no_two_issues_overlap_reads_parallelism_one(prof):
    """Two issues that follow each other in Doing never stand there together: parallelism 1.0, and the effective time
    is the time per issue."""
    spans = [{"queue": None, "wait": None, "coding": 600.0, "between": 0.0, "review": None, "gates": None,
              "landing": None, "landing_stint": None, "end": CS + 1200.0}] * 2
    cards = [[{"column": "Doing", "start": CS, "end": CS + 600.0}],
             [{"column": "Doing", "start": CS + 600.0, "end": CS + 1200.0}]]
    doing = doing_column(prof, spans, cards)
    assert doing["parallelism"] == pytest.approx(1.0), "no two issues overlap, so one stands at a time"
    assert doing["effective_s"] == pytest.approx(600.0), "one at a time delivers the time per issue"


def test_the_trip_payload_carries_tokens_cpu_and_ram_for_each_column(prof):
    out = prof.trip([span_at(2400.0)], [], since=CS,
                    cards=[card(("Todo", 0.0, 600.0), ("Doing", 600.0, 1800.0), ("Review", 1800.0, 2400.0))],
                    calls=[[{"at": CS + 300.0, "tokens": 600}, {"at": CS + 1200.0, "tokens": 2400}]],
                    loads=every_minute(3600.0), now=CS + 3600.0, cores=8, mem_gb=32.0)
    todo = {r["column"]: r for r in out["columns"]}["todo"]
    assert {"tokens", "tokens_per_issue", "cpu", "ram"} <= set(todo), "each column carries the three lines and its rate"
    # 600 tokens at 300 s of a 600 s run: the 0.5 position is bin 4, whose 75 s hold 1.25 min, so 600 / 1.25 = 480 a
    # minute; the column's mean is 600 tokens over its 10 minutes, and the issue cost 600.
    assert todo["tokens"] == {"points": [[0.5625, 480.0]], "mean": 60.0}
    assert todo["tokens_per_issue"] == 600.0
    assert todo["cpu"] == {"points": todo["cpu"]["points"], "mean": 0.5}, "50% of the machine is a 0.5 share"
    assert todo["cpu"]["mean"] == 0.5 and todo["ram"]["mean"] == 0.4
    doing = {r["column"]: r for r in out["columns"]}["doing"]
    assert doing["tokens"]["mean"] == 120.0, "2400 tokens over the 20 minutes of Doing"
    assert (out["peak_load"], out["cores"], out["mem_gb"]) == (4.0, 8, 32.0)


def test_the_payload_names_the_three_ticks_of_each_lane(prof):
    """#2228: the page draws the payload's ticks, so the fold names the three of the Tokens lane. The Machine lane scales
    its own series (#2254), so the fold names no machine_ticks."""
    out = prof.trip([span_at(2400.0)], [], since=CS,
                    cards=[card(("Todo", 0.0, 600.0), ("Doing", 600.0, 1800.0), ("Review", 1800.0, 2400.0))],
                    calls=[[{"at": CS + 300.0, "tokens": 600}, {"at": CS + 1200.0, "tokens": 2400}]],
                    loads=[], now=CS + 3600.0)
    assert out["tokens_ticks"] == [0, 500.0, 1000.0], \
        "a round ceiling above the window's highest rate, 960 a minute, and its half, over the zero"
    assert "machine_ticks" not in out, "the fold emits no machine_ticks: each lane scales its own series"


def test_a_call_counts_in_the_column_of_its_end_time(prof):
    evs = [ev("run_start", CS + 100.0),
           ev("call_start", CS + 150.0, node="code.1", entry=1),
           ev("outcome", CS + 250.0, node="code.1", entry=1, outcome="pass", tokens_in=10, tokens_out=5, cache_read=2),
           ev("land", CS + 300.0, node="code.1", outcome="pass"),
           ev("verdict", CS + 300.0, verdict="completed", exit_code=0, reasons=[])]
    assert prof.trip_calls(evs) == [{"at": CS + 250.0, "tokens": 17}], \
        "the call is read at its end, with input, output and cache reads; its start is no time of its tokens"


def test_two_deliveries_of_different_lengths_fold_by_position(prof):
    out = prof.trip([span_at(600.0), span_at(1200.0)], [], since=CS,
                    cards=[card(("Doing", 0.0, 600.0)), card(("Doing", 0.0, 1200.0))],
                    calls=[[{"at": CS + 150.0, "tokens": 300}], [{"at": CS + 300.0, "tokens": 600}]],
                    loads=[], now=CS + 3600.0)
    doing = {r["column"]: r for r in out["columns"]}["doing"]
    assert doing["tokens"]["points"] == [[0.3125, 240.0]], \
        "a quarter into either run is one bin: 900 tokens over its 3.75 minutes"
    assert doing["tokens"]["mean"] == 30.0 and doing["tokens_per_issue"] == 450.0, "900 tokens over 30 minutes, for 2 issues"


def test_cpu_and_ram_are_shares_and_the_payload_carries_the_memory(prof):
    out = prof.trip([span_at(600.0)], [], since=CS, cards=[card(("Doing", 0.0, 600.0))], calls=[[]],
                    loads=every_minute(660.0, cpu=80.0, ram=20.0), now=CS + 3600.0, mem_gb=64.0)
    doing = {r["column"]: r for r in out["columns"]}["doing"]
    assert doing["cpu"]["mean"] == 0.8 and doing["ram"]["mean"] == 0.2, "each reading is a share from 0 to 1"
    assert all(0.0 <= v <= 1.0 for _, v in doing["cpu"]["points"] + doing["ram"]["points"])
    assert out["mem_gb"] == 64.0, "the page reads the machine's memory for the RAM hover"


def test_a_column_with_no_sample_carries_no_point_and_no_mean(prof):
    out = prof.trip([span_at(600.0)], [], since=CS, cards=[card(("Todo", 0.0, 600.0))], calls=[[]],
                    loads=[{"at": CS + 900.0, "load": 4.0, "cpu": 50.0, "ram": 40.0}],
                    now=CS + 3600.0)
    todo = {r["column"]: r for r in out["columns"]}["todo"]
    assert todo["cpu"] == {"points": [], "mean": None} and todo["ram"] == {"points": [], "mean": None}, \
        "the only record stands outside Todo, so the column holds no point and no mean, never a zero"
    assert todo["tokens"] == {"points": [], "mean": None}, "no model call is no reading of a rate"


def test_a_column_with_no_model_call_carries_zero_tokens_per_issue(prof):
    out = prof.trip([span_at(600.0)], [], since=CS, cards=[card(("Todo", 0.0, 600.0))], calls=[[]],
                    loads=[], now=CS + 3600.0)
    rows = {r["column"]: r for r in out["columns"]}
    assert rows["todo"]["tokens_per_issue"] == 0.0, "a column with no model call spends a true zero"
    assert rows["doing"]["tokens_per_issue"] == 0.0, "a column no delivery stood in spends a zero too"


def test_a_delivery_that_re_enters_a_column_counts_one_visit_for_each_run(prof):
    """Todo → Doing → Todo: the card stands in Todo twice, so the column's rate divides the two visits' 2000 tokens by
    the two visits, never by the one issue that made them."""
    out = prof.trip([span_at(2400.0)], [], since=CS,
                    cards=[card(("Todo", 0.0, 600.0), ("Doing", 600.0, 1200.0), ("Todo", 1200.0, 2400.0))],
                    calls=[[{"at": CS + 300.0, "tokens": 1000}, {"at": CS + 1800.0, "tokens": 1000}]],
                    loads=[], now=CS + 3600.0)
    todo = {r["column"]: r for r in out["columns"]}["todo"]
    assert todo["tokens_per_issue"] == 1000.0, "the column's tokens over its two visits, one visit for each run"


def test_a_review_call_of_two_parallel_reviews_lands_on_the_narrowest_run(prof):
    """Two cards stand in Review when one ledger line lands, and the ledger names no delivery: the line goes to the
    narrowest Review run that holds it, counted once, never to whichever run the fold met first."""
    out = prof.trip([span_at(1800.0), span_at(1800.0)], [], since=CS,
                    cards=[card(("Review", 0.0, 1800.0)), card(("Review", 600.0, 1800.0))],
                    calls=[[], []], review_calls=[{"at": CS + 1200.0, "tokens": 500}],
                    loads=[], now=CS + 3600.0)
    review = {r["column"]: r for r in out["columns"]}["review"]
    assert review["tokens"]["points"] == [[0.5625, 80.0]], \
        "the 1200 s run is narrower than the 1800 s one, so the line at 0.5 of it counts once, over the bin's 6.25 min"
    assert review["tokens_per_issue"] == 250.0, "the line is counted once, over the column's two visits"


def test_a_refused_caller_reads_no_count_of_the_records_minutes(prof):
    out = prof.trip([span_at(600.0)], [], since=CS, cards=[card(("Doing", 0.0, 600.0))], calls=[[]],
                    loads=None, now=CS + 3600.0)
    assert out["peak_load"] is None and out["no_record_minutes"] is None, \
        "a record the caller may not read asserts no count of its minutes, never the whole window"


def test_the_payload_carries_the_peak_the_cores_and_the_minutes_with_no_record(prof):
    rows = every_minute(3001.0)
    rows[0]["load"] = 14.0
    out = prof.trip([span_at(600.0)], [], since=CS, cards=[card(("Doing", 0.0, 600.0))], calls=[[]],
                    loads=rows, now=CS + 3600.0, cores=10)
    assert out["peak_load"] == 14.0, "the window's highest reading"
    assert out["cores"] == 10
    assert out["no_record_minutes"] == 10, "50 of the window's 60 minutes hold a record"


def trip_iso(t):
    return dt.datetime.fromtimestamp(t, dt.UTC).isoformat()


def test_the_cards_column_runs_come_from_the_board_transitions(hub):
    """The sample's column is the card's own move, read from the board's transition record: each run is one entry of the
    card into a column, from that move to the next, and the last run is open to the window's end."""
    mod, *_ = hub
    base = 1_800_000_000.0
    store = {7: {"events": [{"t": trip_iso(base), "type": "added", "project": 4},
                            {"t": trip_iso(base + 60), "type": "status", "status": "Todo"},
                            {"t": trip_iso(base + 660), "type": "status", "status": "Doing"},
                            {"t": trip_iso(base + 1860), "type": "status", "status": "Review"}]}}
    runs = mod.card_column_runs(store, {7}, since=base, now=base + 3660)
    assert runs[7] == [{"column": "Todo", "start": base + 60, "end": base + 660},
                       {"column": "Doing", "start": base + 660, "end": base + 1860},
                       {"column": "Review", "start": base + 1860, "end": base + 3660}], \
        "each run ends where the next column opens, and the open one ends at the window's own end"
    assert runs.get(8) is None, "a card the record never saw answers no runs"


def test_the_route_carries_the_tokens_cpu_and_ram_of_each_column(hub):
    """The whole seam: a run record, a load record and a transition record in the fixture state folder give the trip's
    three lines, folded by the rule layer and carried on /api/profiler."""
    mod, port, _, state, _ = hub
    now = time.time()
    runs = pathlib.Path(mod.CHECKOUT) / "runs"
    d = runs / "run-t2227"
    d.mkdir(parents=True, exist_ok=True)
    hist = state.parent / "hub-history.x_y.jsonl"
    try:
        (d / "request.txt").write_text("Refs #7\n")
        (d / "record.jsonl").write_text("\n".join(json.dumps(e) for e in (
            {"event": "run_start", "pos": 1, "ts": now - 1400.0},
            {"event": "call_start", "pos": 2, "ts": now - 1350.0, "node": "code.1", "entry": 1},
            {"event": "outcome", "pos": 3, "ts": now - 1200.0, "node": "code.1", "entry": 1, "outcome": "pass",
             "tokens_in": 300, "tokens_out": 150, "cache_read": 50},
            {"event": "land", "pos": 4, "ts": now - 1100.0, "node": "code.1", "outcome": "pass"},
            {"event": "verdict", "pos": 5, "ts": now - 1000.0, "verdict": "completed", "exit_code": 0, "reasons": []})) + "\n")
        hist.write_text(json.dumps({"k": "issue", "n": 7, "events": [
            {"t": trip_iso(now - 2000.0), "type": "added", "project": 4},
            {"t": trip_iso(now - 1900.0), "type": "status", "status": "Todo"},
            {"t": trip_iso(now - 1500.0), "type": "status", "status": "Doing"}], "complete": True}) + "\n")
        rows = [f"{now - 1800.0 + 60 * k:.3f}\t4.00\t50.0\t40.0" for k in range(31)]
        (state / "load-record.tsv").write_text("\n".join(rows) + "\n")
        code, v = ask(port, "GET")
        assert code == 200
        trip = v["trip"]
        cols = {r["column"]: r for r in trip["columns"]}
        todo, doing = cols["todo"], cols["doing"]
        assert todo["tokens_per_issue"] == 0.0 and todo["tokens"]["mean"] is None, "no call ended in Todo"
        assert doing["tokens_per_issue"] == 500.0, "the call's 300 in, 150 out and 50 cache reads land in Doing"
        assert doing["tokens"]["mean"] > 0 and doing["tokens"]["points"], "a rate over the minutes spent in Doing"
        assert doing["cpu"]["mean"] == 0.5 and doing["ram"]["mean"] == 0.4, "the minute record, as shares of the machine"
        assert trip["cores"] >= 1 and trip["mem_gb"] > 0, "the payload names the machine it reads"
        assert trip["peak_load"] == 4.0 and trip["no_record_minutes"] > 0, "the load facts the page keeps in one sentence"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        hist.unlink(missing_ok=True)
        (state / "load-record.tsv").unlink(missing_ok=True)


def test_the_machines_memory_is_a_gib_figure(hub, monkeypatch):
    """The RAM hover names the size the reader's OS shows: 16 GiB is 16.0, never the decimal 17.2."""
    mod, *_ = hub
    monkeypatch.setattr(mod, "_sysctl_u64", lambda name: 16 * 1024 ** 3)
    assert mod.mem_total_gb() == 16.0, "the machine's memory in gibibytes, the figure the OS shows"


def test_the_machines_memory_has_a_figure_without_mach_and_without_psutil(hub, monkeypatch):
    """A Linux runner has no Mach sysctl and may have no psutil: the POSIX page count still names the machine's memory,
    so the trip payload carries a size on every machine the suite runs on."""
    mod, *_ = hub
    monkeypatch.setattr(mod, "_sysctl_u64", lambda name: 0)
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert mod.mem_total_gb() > 0, "the POSIX page count answers when the kernel door and psutil do not"


def test_a_refused_trip_view_reads_no_minute_count_of_the_record(hub):
    """A caller the hub refused never stats the minute record, so its trip asserts no count of the missing minutes."""
    mod, *_ = hub
    trip = mod.trip_view(3600, allowed=False)
    assert trip["peak_load"] is None and trip["no_record_minutes"] is None, \
        "the record is unread, so the payload names neither a peak nor a count of silent minutes"


# The view fixture is the fold's own answer over four worked deliveries and one wave gate, never a hand-written share,
# so the widths the page paints are the widths `trip` emits. The medians the four spans give: queue 2400, wait 25,
# coding 250, between 150, review 50, gates 120 and the merge into the wave 75 (these stand in Doing), the Landing
# stint 75, and the one tip gate of the fourth delivery's wave adds a 60 s gate in Gating. The fixture reads no card run, so every column runs one issue at a time and its
# effective time is its median: 2400, 250, 50, 75 and 60 s, a total of 2835, with the shares 0.8466, 0.0882, 0.0176,
# 0.0265 and 0.0212 (#2256).
TRIP_FOLD = load("hub_profiler_trip_fixture", SCRIPTS / "hub_profiler.py")
TRIP = {**TRIP_FOLD.trip([*TRIP_SPANS,
                          {"queue": 2400.0, "wait": 40.0, "coding": 400.0, "between": 240.0, "review": 80.0,
                           "gates": 180.0, "landing": 120.0, "landing_stint": 120.0, "end": NOW - 400}],
                         [{"start": NOW - 260, "finish": NOW - 200, "caller": "tip", "wait": 0.0,
                           "link": {"waves": ["run-d"], "card": None}}], since=NOW - 3600,
                         waves=[None, None, None, {"wave": "run-d", "issues": [4]}]),
        "window_s": 3600, "since": NOW - 3600}


def trip_part(out):
    at = out.index("<section id=proftrip>")
    return out[at:out.index("</section>", at) + 10]


def test_the_view_draws_the_trip_as_five_rows_named_for_the_columns_and_five_segments():
    out = trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP})},new Set())"))
    assert "<h3>The trip of an issue</h3>" in out and "4 deliveries in the last 1.0 h" in out
    assert "the bar folds 5 segments, one per board column" in out, "the caption counts the segments it draws (#2199)"
    segs = re.findall(r'<rect class="ptb-seg ptc pts-(\w+)" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"[^>]*>'
                      r'<title>([^<]*)</title></rect>', out)
    assert [s[0] for s in segs] == ["todo", "doing", "review", "landing", "gating"], \
        "the bar holds one segment per drawn column, in board order, with none for Closed"
    widths = [float(s[2]) for s in segs]
    assert TRIP["columns"][0]["share"] == pytest.approx(0.8466, abs=1e-4), "the fold's own share of Todo"
    assert widths == pytest.approx(drawn_widths(TRIP["columns"], out, 860), abs=0.2), \
        "each segment's width follows the effective-time share the fold emits, and none is narrower than its label (#2278)"
    assert sum(widths) == pytest.approx(860, abs=0.5), "the segments still divide the whole plot"
    assert segs[1][3] == "Doing: median 4.2 min, p85 5.9 min (4 observations)", \
        "the hover names the median, the p85 and the count, and no other stage (#2280)"
    assert [h for h in re.findall(r"<th[^>]*>([^<]+)</th>", out)] == TRIP_HEADS
    rows = re.findall(r'<tr data-p="trip/(\w+)"><td class=pn><i class="ptk ptk-(\w+)( ptk-none)?" aria-hidden=true></i><span class=pnm>([^<]*)</span></td>'
                      + r"<td>([^<]*)</td>" * 9, out)
    assert [r[3] for r in rows] == ["Todo", "Doing", "Review", "Landing", "Gating"], \
        "five rows, each named for its board column, in board order, with no Closed row"
    assert rows[0][3:] == ("Todo", "40.0 min", "40.0 min", "40.0 min", "40.0 min", "×1.0", "40.0 min", "0",
                           "&#8212;", "&#8212;"), \
        "the Todo row reads the queue alone; the fixture folds no call and no load, so its rates read a zero and two dashes"
    assert rows[1][3:] == ("Doing", "1.7 min", "4.2 min", "5.9 min", "6.7 min", "×1.0", "4.2 min", "0",
                           "&#8212;", "&#8212;"), \
        "the four time cells read the one stage: coding's 100, 250, 355 and 400 s over the four deliveries"
    assert (rows[3][4], rows[3][5], rows[3][6], rows[3][7]) == ("30.0 s", "1.3 min", "1.8 min", "2.0 min"), \
        "Landing stands before Gating, where the board puts it"
    assert (rows[3][8], rows[3][9]) == ("×1.0", "1.3 min"), \
        "the parallelism and the effective time keep the value and the format of #2256"
    assert rows[4][4:8] == ("1.0 min", "1.0 min", "1.0 min", "1.0 min"), \
        "Gating reads the wave gate, never the run's own gate"
    assert [r[1] for r in rows] == [s[0] for s in segs], "a row's swatch is the color of its segment, in the same order: the table is the bar's legend"
    assert all(r[0] == r[1] for r in rows), "the swatch of a row names the column the row names"


def test_a_column_with_no_median_draws_a_hollow_swatch_and_no_colour_segment():
    none = {**TRIP, "columns": [{**c, "median_s": None} if c["column"] == "review" else c for c in TRIP["columns"]]}
    out = trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': none})},new Set())"))
    row = re.search(r'<tr data-p="trip/review"><td class=pn><i class="([^"]*)" aria-hidden=true></i>', out)
    assert row and "ptk-review" in row.group(1) and "ptk-none" in row.group(1), "a column with no median keeps its color as an outline, never a reading"
    assert "ptb-seg pts-review" not in out, "a column with no median paints no colour, so no 4 px ghost shows a colour it has no time for"
    assert len(re.findall(r'<rect class="ptb-seg', out)) == 5, "the bar still holds one segment per drawn column: the empty column takes no colour"
    assert out.count("ptk-none") == 1, "only the column without a median is hollow"


def test_a_zero_median_draws_no_ghost_segment_either():
    zero = {**TRIP, "columns": [{**c, "median_s": 0.0} if c["column"] == "review" else c for c in TRIP["columns"]]}
    out = trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': zero})},new Set())"))
    assert "ptb-seg pts-review" not in out, "the bar's own min-width would draw 4 px of a column that has no time in it"
    row = re.search(r'<tr data-p="trip/review"><td class=pn><i class="([^"]*)" aria-hidden=true></i>', out)
    assert row and "ptk-none" in row.group(1), "a zero median reads as hollow in the legend too"


def test_the_swatch_rules_are_shipped_with_the_widget():
    src = page()
    for rule in ("#proftrip .ptk{", "background:currentColor", "min-width:4px", "gap:1px",
                 "#proftrip .ptk.ptk-none{background:none", "box-shadow:inset 0 0 0 1px currentColor"):
        assert rule in src, f"the trip's swatch rules are what makes a colour readable: {rule} is missing"


def test_each_of_the_drawn_columns_draws_its_own_colour_for_its_row_and_its_segment():
    src = page()
    for column in ("todo", "doing", "review", "gating", "landing"):
        assert f"#proftrip .pts-{column}{{" in src and f"#proftrip .ptk-{column}{{" in src, \
            f"the column {column} draws its own colour, so its row and its segment read as one"


def test_a_window_with_fewer_than_three_deliveries_says_so_and_draws_no_bar():
    thin = {**TRIP, "deliveries": 2, "bar": False}
    out = trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': thin})},new Set())"))
    assert "2 deliveries in the last 1.0 h: fewer than three, so no bar is drawn." in out
    assert "class=ptb " not in out and "ptb-seg" not in out, "fewer than three deliveries draw no bar"
    one = {**TRIP, "deliveries": 1, "bar": False}
    assert "1 delivery in " in trip_part(run_js(f"profHtml({json.dumps({**VIEW, 'trip': one})},new Set())"))


def test_the_trip_shows_with_the_switch_off():
    off = run_js(f"profHtml({json.dumps({**VIEW, 'on': False, 'tree': None, 'context': None, 'trip': TRIP})},new Set())")
    assert "id=proftrip" in off and "id=proftt" in off and "<table id=proft " not in off
    on = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP})},new Set())")
    assert on.index("id=proftrip") < on.index("<table id=proft "), "the bar is the summary above the hot-spot tree"


def test_the_file_views_refresh_on_the_slow_cadence_with_the_switch_off():
    out = json.loads(run_js('JSON.stringify([profFileDue("prof",false,{on:false}),profFileDue("prof",false,{on:true}),'
                            'profFileDue("flow",false,{on:false}),profFileDue("prof",true,{on:false}),'
                            'profFileDue("prof",false,null)])'))
    assert out == [True, False, False, False, False]


def test_the_route_answers_the_trip_of_the_window_the_page_asked_for(hub):
    mod, port, _, state, table = hub
    now = time.time()
    runs = state.parent / "runs"
    for i, start in enumerate((now - 1000, now - 2000, now - 3000, now - 200_000)):   # the fourth ended two days ago
        record(runs / f"run-20261115-03030{i}-aaaaa{i}", *[dict(e, ts=e["ts"] - 1000 + start) for e in rec()])
    (state / "gate-history").write_text(
        "".join(f"{now - 200}\tfast\tpass\tabc\t{now - 260}\tall\troot\tpass\t-\t1\t0\t0\t0\tchild\t0.1\t0.2\n"
                for _ in range(2))
        # #1798: a reused entry ran no battery, so its row is no gate observation of the window.
        + f"{now - 100}\tfast\tpass\tabc\t{now - 100}\t\t\tpass\t-\t-\t-\t0\t-\tentry\t0.1\t0.2\treused\n")
    code, v = ask(port, "GET")
    assert code == 200 and v["trip"]["window_s"] == 86400 and v["trip"]["bar"] is True
    assert v["trip"]["deliveries"] == 3, "the delivery two days back is outside the day the page asked for"
    assert [c["column"] for c in v["trip"]["columns"]] == ["todo", "doing", "review", "landing", "gating"]
    assert {c["column"]: c["observations"] for c in v["trip"]["columns"]}["gating"] == 0, \
        "a child's gate rows never stand in Gating, and the run's own three gates stand in Doing (#2292)"
    assert v["trip"]["columns"][0]["stage"] == "wait", "a run that names no card still reads its dispatch wait"
    cols = {c["column"]: c for c in v["trip"]["columns"]}
    assert cols["doing"]["sum_s"] == 200.0 + cols["doing"]["beside"]["median_s"] + 90.0 + 150.0, \
        "Doing's segment sums coding 200 s, the 0 s between two children, the run's own 90 s gate and its 150 s merge"
    assert cols["todo"]["sum_s"] == cols["todo"]["median_s"] == 30.0 and cols["todo"]["beside"] is None, \
        "with no card, the Todo segment is the dispatch wait alone"
    assert sum(c["share"] for c in v["trip"]["columns"]) == pytest.approx(1.0, abs=1e-3), \
        "the five rounded shares divide the whole bar, so no measurement the panel draws leaves it"
    assert table.reads == 0, "the trip reads files only, so it shows with the switch off"
    assert ask(port, "GET", path="/api/profiler?w=3600")[1]["trip"]["window_s"] == 3600


def test_the_trip_folds_the_wait_of_the_card_the_run_works(hub):
    """#1894, #2292: the card's own transitions give the wait outside the run: 40 min in the queue before it started,
    and the card's Landing stint from its move into Landing to its Closed move. The run names its issue in request.txt."""
    mod, port, _, state, _ = hub
    now = float(int(time.time()))   # whole seconds: the card's transitions are ISO stamps, never a fraction
    run = state.parent / "runs" / "run-20261115-060606-aaaaaa"
    start = now - 3600
    record(run, *[dict(e, ts=e["ts"] - 1000 + start) for e in rec()])
    (run / "request.txt").write_text("issue #7\n")
    def iso(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    path = mod._hist.history_path(mod.BOARD, mod.REPO)
    mod._hist.record_issue(path, 7, iso(now), iso(now), [
        {"t": iso(start - 2400), "type": "added"}, {"t": iso(start - 2400), "type": "status", "status": "Todo"},
        {"t": iso(start), "type": "status", "status": "Doing"}, {"t": iso(start + 500), "type": "status", "status": "Landing"},
        {"t": iso(start + 800), "type": "closed"}],
        True, now)
    code, v = ask(port, "GET")
    rows = {c["column"]: c for c in v["trip"]["columns"]}
    assert code == 200 and rows["todo"]["median_s"] == 2400.0, "the 40 min the card waited in the queue"
    assert rows["todo"]["beside"]["stage"] == "wait", "the dispatch wait stands beside the queue in the Todo row"
    assert rows["landing"]["median_s"] == 300.0, \
        "the card's 300 s from its move into Landing to its Closed move stands in Landing (#2292)"
    assert rows["doing"]["beside"]["median_s"] == 0.0, "the run's one child holds no gap"


def wave_gate_line(finish, start, sha, caller, wait, run_id="-"):
    """One gate-history line in the shape `gate.sh` writes today: the runner fields last, the slot wait in its field."""
    return (f"{finish}\tfast\tpass\t{sha}\t{start}\tsel\tdelta\tpass\t-\truff\t-\t3\t-\t{caller}\t1.0\t1.0\tgated\t"
            f"tree\t{wait}\t{finish - start}\t0\tno\tlocal\t{run_id}\n")


def test_the_trip_reads_landing_from_the_board_and_gating_from_the_wave_that_carried_the_issue(hub, monkeypatch,
                                                                                               tmp_path):
    """#2292, end to end: two runs work issue #8. The first one's Landing stint went back to Todo, so it is rework and
    that run is no delivery; the second run delivered the issue. Landing is the second stint up to the start of its
    wave's tip gate: the move into Landing at now - 4500 to the gate's slot wait at now - 4440, 60 s. Gating is that
    gate, its 40 s slot wait plus its 100 s run, linked by the attempt journal; the first wave's gate and a child's gate
    never count. Landing and Gating never exceed the 300 s from the move into Landing to the Closed move."""
    mod, port, _, state, _ = hub
    monkeypatch.setattr(mod, "CHECKOUT", str(tmp_path))   # a run folder of this case alone: the served hub is shared
    now = float(int(time.time()))
    a, b = tmp_path / "runs" / "run-20261115-070707-bbbbbb", tmp_path / "runs" / "run-20261115-080808-cccccc"
    for run, start in ((a, now - 7000), (b, now - 5000)):
        record(run, *[dict(e, ts=e["ts"] - 1000 + start) for e in rec()])
        (run / "request.txt").write_text("issue #8\n")

    def iso(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    mod._hist.record_issue(mod._hist.history_path(mod.BOARD, mod.REPO), 8, iso(now), iso(now), [
        {"t": iso(now - 7100), "type": "added"}, {"t": iso(now - 7100), "type": "status", "status": "Todo"},
        {"t": iso(now - 7000), "type": "status", "status": "Doing"},
        {"t": iso(now - 6500), "type": "status", "status": "Landing"},     # run a's verdict
        {"t": iso(now - 5900), "type": "status", "status": "Todo"},        # back to work: 600 s of rework
        {"t": iso(now - 5000), "type": "status", "status": "Doing"},
        {"t": iso(now - 4500), "type": "status", "status": "Landing"},     # run b's verdict
        {"t": iso(now - 4200), "type": "closed"}], True, now)
    (state / "gate-history").write_text(
        wave_gate_line(int(now - 6300), int(now - 6400), "aaa", "tip", 10)        # run a's wave: no delivery
        + wave_gate_line(int(now - 4300), int(now - 4400), "bbb", "tip", 40)      # run b's wave: Gating
        + wave_gate_line(int(now - 4250), int(now - 4290), "bbb", "child", 5))    # a child's gate never counts
    journal = mod._flow.history_path(mod.BOARD, mod.REPO)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("".join(json.dumps({"k": "gate_attempt", "n": 0, "tip": sha, "t": int(t), "v": "pass",
                                           "aid": "-", "ord": 1, "w": w}) + "\n"
                               for sha, t, w in (("aaa", now - 6300, a.name), ("bbb", now - 4300, b.name))))
    code, v = ask(port, "GET")
    rows = {c["column"]: c for c in v["trip"]["columns"]}
    assert code == 200 and v["trip"]["deliveries"] == 1, "the run whose issue another run delivered is no delivery"
    assert rows["landing"]["median_s"] == 60.0, \
        "the stint that went on to Closed, up to the start of its wave's gate; never the stint that went back"
    assert (rows["gating"]["median_s"], rows["gating"]["observations"]) == (140.0, 1), \
        "the delivering wave's tip gate, slot wait plus run; the first wave's gate and the child's gate never count"
    assert rows["landing"]["median_s"] + rows["gating"]["median_s"] <= 300.0, \
        "Landing and Gating count no second twice: together they fit in the move into Landing to the Closed move"
    assert (v["trip"]["rework"]["count"], v["trip"]["rework"]["median_min"]) == (1, 10.0), \
        "the stint that went back to Todo is one rework stint of 10 minutes"
    assert "after" not in {r["stage"] for r in v["trip"]["record_stages"]}


def test_the_landing_stint_ends_where_the_first_linked_gate_of_its_wave_starts(prof):
    """#2292: Landing is the card's time in the wave before its gate starts; Gating holds the gate and its slot wait."""
    gates = [{"caller": "tip", "start": 1100.0, "finish": 1200.0, "wait": 30.0, "link": {"waves": ["w1"]}},
             {"caller": "landing", "start": 1250.0, "finish": 1280.0, "wait": 0.0, "link": {"waves": ["w1"]}},
             {"caller": "child", "start": 1010.0, "finish": 1020.0, "wait": 0.0, "link": {"waves": ["w1"]}}]
    assert prof.landing_until_gate(300.0, 1000.0, gates, "w1", (), 990.0) == 70.0, \
        "the stint ends at the tip gate's slot wait, 1070; a child's gate never ends it"
    assert prof.landing_until_gate(300.0, 1000.0, gates, "w1", (), 990.0) + prof.wave_gate(gates, "w1", (), 990.0) \
        <= 300.0, "Landing plus Gating fit in the stint"
    assert prof.landing_until_gate(300.0, 1000.0, gates, "w2", (), 990.0) == 300.0, \
        "with no linked gate, the stint runs to its forward move"
    assert prof.landing_until_gate(300.0, 1000.0, [dict(gates[0], start=980.0, finish=1100.0)], "w1", (), 990.0) == 0.0, \
        "a gate that starts before the stint leaves zero"
    assert prof.landing_until_gate(300.0, 1000.0, [dict(gates[0], start=1400.0, finish=1500.0)], "w1", (), 990.0) == 300.0, \
        "a gate that starts after the stint does not shorten it"
    assert prof.landing_until_gate(None, 1000.0, gates, "w1", (), 990.0) is None, "no stint is no reading, never 0"
    assert prof.landing_until_gate(300.0, None, gates, "w1", (), 990.0) == 300.0, "no stint start keeps the whole stint"


def test_a_landing_stint_that_went_on_to_closed_is_the_delivery_and_one_that_went_back_is_rework(prof):
    runs = [{"column": "Doing", "start": 0, "end": 100}, {"column": "Landing", "start": 100, "end": 160},
            {"column": "Review", "start": 160, "end": 200}, {"column": "Landing", "start": 200, "end": 250},
            {"column": "Landing", "start": 250, "end": 260},          # a label change splits a run, never a stint
            {"column": "Gating", "start": 260, "end": 300}, {"column": "Closed", "start": 300, "end": None}]
    out = prof.landing_stints(runs)
    assert (out["delivered"], out["closed"]) == (60, 300), \
        "the stint into Gating that went on to Closed: 200 to 260 s, the forward move ends it"
    assert [(r["seconds"], r["to"]) for r in out["rework"]] == [(60, "Review")], "the stint that went back is rework"
    open_ = prof.landing_stints([{"column": "Landing", "start": 0, "end": None}])
    assert (open_["delivered"], open_["rework"]) == (None, []), "an open stint is neither a delivery nor rework"
    gone = prof.landing_stints([{"column": "Landing", "start": 0, "end": 10}, {"column": "Gating", "start": 10,
                                                                               "end": 20}, {"column": "Todo",
                                                                                            "start": 20, "end": None}])
    assert gone["delivered"] is None, "a stint whose card went from Gating back to work never reached Closed"


def test_only_the_last_run_before_the_closed_move_delivers_its_issue(prof):
    ends, issues = [100.0, 200.0, 400.0, None, 50.0], [[1], [1], [1], [1], []]
    assert prof.trip_deliveries(ends, issues, {1: 300.0}) == [False, True, False, False, True], \
        "the last verdict before Closed delivers; one after it, a run with no verdict and none earlier do not"
    assert prof.trip_deliveries([100.0, 200.0], [[2], [2]], {}) == [True, True], \
        "with no Closed move read, nothing says another run delivered the issue"
    assert prof.trip_deliveries([100.0, 200.0], [[1, 3], [1]], {1: 300.0}) == [True, True], \
        "a run that delivers one issue it names is a delivery"


def test_a_gate_row_learns_its_wave_from_the_attempt_journal(prof):
    """#2292: the gate-history row names no wave; the attempt journal names it with the row's sha and finish. A batch
    tip gate names its runs in its run id. A row the journal does not name has no link."""
    links = prof.gate_links([{"k": "gate_attempt", "n": 0, "tip": "abc", "t": 100, "w": "run-x"},
                             {"k": "gate_attempt", "n": 7, "tip": "def", "t": 200, "w": ""},
                             {"k": "gate_reuse", "n": 0, "tip": "zzz", "t": 300}])
    assert prof.gate_row_link({"sha": "abc", "finish": "100"}, links) == {"waves": ["run-x"], "card": None}
    assert prof.gate_row_link({"sha": "def", "finish": "200"}, links) == {"waves": [], "card": 7}
    assert prof.gate_row_link({"sha": "zzz", "finish": "300"}, links) is None, "a reuse marker is no attempt"
    assert prof.gate_row_link({"sha": "abc", "finish": "101"}, links) is None, "another finish is another gate"
    batch = prof.gate_row_link({"sha": "q", "finish": "5", "run_id": "batch:2:run-a,run-b"}, links)
    assert batch == {"waves": ["run-a", "run-b"], "card": None}, "a batch tip gate is the gate of each run it names"


def test_the_payload_carries_one_rework_summary_and_no_after_stage(prof):
    rework = [{"end": NOW - 100, "seconds": 600.0}, {"end": NOW - 200, "seconds": 1200.0},
              {"end": NOW - 9000, "seconds": 60.0}]                       # outside the window
    t = prof.trip(TRIP_SPANS, [], since=NOW - 3600, rework=rework)
    assert (t["rework"]["count"], t["rework"]["median_min"]) == (2, 15.0)
    assert "no column" in t["rework"]["text"], "the line says rework counts in no column"
    assert sum(c["sum_s"] or 0 for c in t["columns"]) == sum(
        c["sum_s"] or 0 for c in prof.trip(TRIP_SPANS, [], since=NOW - 3600)["columns"]), "rework adds to no column"
    assert "after" not in dict(prof.TRIP_STAGES_LABELLED), "the time after the verdict is no record stage (#2292)"
    assert "landing" in prof.TRIP_COLUMN_STAGES["Doing"], "the record's merge into the wave folds into Doing"
    assert prof.TRIP_COLUMN_STAGES["Landing"] == (prof.LANDING_STINT,)
    assert prof.trip(TRIP_SPANS, [], since=NOW - 3600)["rework"]["count"] == 0


def test_the_view_prints_the_rework_line_under_the_trip_table():
    t = {**TRIP, "rework": {"count": 2, "median_min": 15.0, "text": "Rework: 2 stints, median 15.0 min."}}
    for phone in ("false", "true"):
        out = run_js(f"profTrip({json.dumps(t)},{phone})")
        assert "<p class=tprw>Rework: 2 stints, median 15.0 min.</p>" in out, "the line the rule layer words"
        assert out.index("</table>") < out.index("class=tprw"), "the line stands under the trip table"


def test_the_trip_reads_the_gate_history_by_column_name(hub):
    """A gate row's reason may hold a tab; the columns after it keep their own names, never an index."""
    mod = hub[0]
    line = "100\tfast\tpass\tabc\t90\tall\troot\tpass\twhy\twith\ttabs\t1\t0\t0\t0\tchild\t0.1\t0.2"
    row = mod.gate_row(line)
    assert (row["start"], row["finish"], row["outcome"], row["caller"]) == ("90", "100", "pass", "child")
    assert row["reason"] == "why\twith\ttabs", "the free-text reason is rejoined, so later columns keep their place"
    assert row.get("mode", "") == "", "a row from before the entry mode answers none (#1798)"
    assert mod.gate_row("100\tfast\tpass") is None, "a row too short to hold a start and a finish is no row"


def test_a_gate_history_row_names_its_entry_mode_and_an_older_row_still_parses(hub):
    """#1798: the wave entry's mode is the row's seventeenth field; a row from before it parses as it always did."""
    mod = hub[0]
    for mode, caller in (("gated", "entry"), ("reused", "entry"), ("skipped", "entry")):
        row = mod.gate_row(f"100\tfast\tpass\tabc\t90\t\t\tpass\t-\t-\t-\t0\t-\t{caller}\t0.1\t0.2\t{mode}")
        assert (row["mode"], row["caller"], row["outcome"]) == (mode, caller, "pass"), \
            f"a {mode} row keeps its columns after the reason-free field"
    # An older row's last field is a load reading, never a mode, so its seven tail columns keep their place.
    old = mod.gate_row("100\tfast\tpass\tabc\t90\tall\troot\tpass\twhy\twith\ttabs\t1\t0\t0\t0\tchild\t0.1\t0.2")
    assert old["caller"] == "child" and old.get("mode", "") == "", "an older row still answers its own columns"
    # A reason that holds a tab in a new row keeps the mode at the end, so the tail still names itself.
    new = mod.gate_row("100\tfast\tpass\tabc\t90\tall\troot\tpass\twhy\twith\ttabs\t1\t0\t0\t0\tentry\t0.1\t0.2\tgated")
    assert (new["reason"], new["caller"], new["mode"]) == ("why\twith\ttabs", "entry", "gated")


def test_the_slot_wait_split_names_the_todo_wait_each_holder_kind_carries(prof):
    """#2286: each frontier pass that refuses a startable card for "no slot" names the holder of each run slot, and the
    fold gives the minutes of Todo wait per holder kind, the largest first. A record speaks from its own instant to the
    next record, and the last one to now; every kind a record names accrues the whole stretch it holds a slot in."""
    recs = [{"at": 1000.0, "free": 0, "cap": 3, "todo": 5,
             "holders": [{"name": "run-a", "kind": "doing"}, {"name": "run-b", "kind": "doing"},
                         {"name": "run-c", "kind": "doing"}]},
            {"at": 1600.0, "free": 0, "cap": 3, "todo": 4,
             "holders": [{"name": "run-d", "kind": "gates"}, {"name": "run-e", "kind": "gates"},
                         {"name": "run-f", "kind": "autobug"}]}]
    states = [{"start": 1000.0, "end": 2200.0, "state": "active"},
              {"start": 2200.0, "end": 2800.0, "state": "draining"}]
    out = prof.slot_wait(recs, states, 1000.0, 2800.0)
    minutes = {line["kind"]: line["minutes"] for line in out["lines"]}
    assert out["refusals"] == 2, "both refusals of the window are counted"
    assert minutes["doing"] == 10.0, "three doing holders for the first 600 s, 10 minutes"
    assert minutes["gates"] == 15.0 and minutes["autobug"] == 15.0, \
        "the last record speaks from 1600 to now, one gap cap at most"
    assert minutes["drained"] == 10.0, "the drain ran 2200 to 2800 while a startable card waited in Todo"
    order = [line["minutes"] for line in out["lines"]]
    assert order == sorted(order, reverse=True), f"the lines come largest first: {out['lines']}"
    assert {line["kind"]: line["passes"] for line in out["lines"]}["gates"] == 1, "one refusal names the gates kind"


def test_a_drain_that_starts_after_the_last_refusal_still_owes_its_minutes(prof):
    """#2286: a loop stopped at 1000 s owes its drained minutes to the Todo wait the last refusal named, even when that
    refusal stands before the window's own start: no pass writes a record while the loop is stopped."""
    recs = [{"at": 900.0, "free": 0, "cap": 3, "todo": 4, "holders": [{"name": "run-a", "kind": "doing"}]}]
    states = [{"start": 1000.0, "end": 1900.0, "state": "off"}]
    out = prof.slot_wait(recs, states, 950.0, 1900.0)
    assert out["drained_minutes"] == 15.0, "the stopped stretch of 900 s is a drained Todo wait"


def test_a_silent_loop_with_the_switch_on_counts_as_a_drained_wait(prof):
    """#2286: a loop the strip reads as `down` steps no pass, so the last refusal's startable card waits for it too."""
    recs = [{"at": 1000.0, "free": 0, "cap": 3, "todo": 2, "holders": [{"name": "run-a", "kind": "landing"}]}]
    states = [{"start": 1000.0, "end": 1600.0, "state": "active"},
              {"start": 1600.0, "end": 2200.0, "state": "down"}]
    out = prof.slot_wait(recs, states, 1000.0, 2200.0)
    assert out["drained_minutes"] == 10.0, "the silent stretch is a drained wait, counted once"


def test_no_drained_minute_counts_without_a_startable_todo_card(prof):
    """#2286: the drained minutes count only while a startable card waited in Todo, so a record that names none adds none."""
    recs = [{"at": 1000.0, "free": 0, "cap": 3, "todo": 0,
             "holders": [{"name": "run-a", "kind": "landing"}]}]
    states = [{"start": 1000.0, "end": 2000.0, "state": "off"}]
    out = prof.slot_wait(recs, states, 1000.0, 2000.0)
    assert out["drained_minutes"] == 0.0, "no Todo card waited, so no minute is a drained wait"


def test_a_corrupt_or_timeless_slot_record_line_is_dropped_whole(prof):
    """#2286: a half-written line is no record, and no line stops the reader of the file the page draws."""
    text = '{"at": 1000.0, "free": 0, "cap": 3, "todo": 4, "holders": []}\nnot json\n{"free": 1}\n'
    assert [r["at"] for r in prof.slot_records(text)] == [1000.0]
    assert prof.slot_records("") == [] and prof.slot_records(None) == []


def test_a_refusal_record_speaks_for_its_gap_cap_only(prof):
    """#2286: a stale record never claims the hours after it, so a pass that stopped writing reads as no wait, not as one
    holder pinning every slot for a day."""
    recs = [{"at": 1000.0, "free": 0, "cap": 3, "todo": 3,
             "holders": [{"name": "run-a", "kind": "gates"}]}]
    out = prof.slot_wait(recs, [], 1000.0, 1000.0 + 7200)
    minutes = {line["kind"]: line["minutes"] for line in out["lines"]}
    assert minutes["gates"] == prof.SLOT_GAP_CAP_S / 60.0, "the record speaks for one gap cap, never two hours"


SLOT_WAIT = {"window_s": 86400, "refusals": 12, "drained_minutes": 90.0,
             "lines": [{"kind": "gates", "label": "Waiting for a gate slot", "minutes": 300.0, "passes": 8},
                       {"kind": "landing", "label": "Finished, waiting for its landing", "minutes": 120.0, "passes": 4},
                       {"kind": "drained", "label": "Loop drained or stopped", "minutes": 90.0, "passes": 2},
                       {"kind": "doing", "label": "A run with a card in Doing", "minutes": 30.0, "passes": 1}]}


def test_the_loop_page_shows_the_slot_wait_split_under_the_trip_table():
    """#2286: the split stands under the trip table, one line per holder kind, the largest first."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP, 'slot_wait': SLOT_WAIT})},new Set())")
    at = out.index("<section id=profslots>")
    assert at > out.index("<table id=proftt"), "the split stands under the trip table"
    assert at < out.index("<table id=proft ", out.index("</table>", out.index("<table id=proftt"))), \
        "the split stands above the hot-spot tree"
    rows = re.findall(r'<tr data-p="slot/(\w+)"><td class=pnm>([^<]*)</td><td>([^<]*)</td><td>(\d+)</td></tr>', out[at:])
    assert [r[0] for r in rows] == ["gates", "landing", "drained", "doing"], \
        "one line per holder kind, the largest first, in the order the payload gives"
    assert rows[0][1:] == ("Waiting for a gate slot", "5.0 h", "8"), "the line names the kind, its minutes and its passes"
    assert "12 refusals" in out[at:], "the block names the refusals it folded"


def test_the_slot_wait_split_names_the_drained_minutes_apart():
    """#2286: the record counts the minutes the loop was drained or stopped while a card waited, and the page prints
    that number apart from the holder lines."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP, 'slot_wait': SLOT_WAIT})},new Set())")
    at = out.index("<section id=profslots>")
    caption = out[at:out.index("<table", at)]
    assert "drained or stopped" in caption and "1.5 h" in caption, \
        f"the caption names the 90 drained minutes of the payload: {caption}"


def test_the_slot_wait_split_is_drawn_only_under_a_trip_table():
    """#2286: the split stands under the trip table, so a view with no trip draws no holder line, and the file view
    draws the split only where it draws that table."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'trip': None, 'slot_wait': SLOT_WAIT})},new Set())")
    assert "<section id=profslots>" not in out, "no trip table above it, so the split stays off the page"
    file_view = {**VIEW, "on": False, "tree": None, "trip": TRIP, "slot_wait": SLOT_WAIT}
    out = run_js(f"profHtml({json.dumps(file_view)},new Set())")
    at = out.index("<section id=profslots>")
    assert at > out.index("<table id=proftt"), "the file view draws the split under the trip table, never apart"


def test_the_slot_wait_split_says_so_when_no_refusal_was_recorded():
    """#2286: a window with no record draws no invented line, so an empty window reads as no measure."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP, 'slot_wait': {'window_s': 86400, 'refusals': 0, 'lines': [], 'drained_minutes': 0.0}})},new Set())")
    at = out.index("<section id=profslots>")
    assert "No refused dispatch" in out[at:], "a window with no refusal names that, and draws no empty table"
    assert 'data-p="slot/' not in out[at:]


def test_the_route_carries_the_slot_wait_split_of_the_records_it_reads(hub):
    """#2286: the payload folds the refusal records the frontier wrote, keyed to the page's window, and a caller the
    hub refused reads no record, so its payload names no split."""
    mod, port, _, state, _ = hub
    now = time.time()
    path = state / "slot-refusals.jsonl"
    recs = [{"at": now - 600, "free": 0, "cap": 3, "todo": 4,
             "holders": [{"name": "run-a", "kind": "gates"}, {"name": "run-b", "kind": "gates"}]},
            {"at": now - 300, "free": 0, "cap": 3, "todo": 2,
             "holders": [{"name": "run-c", "kind": "landing"}]}]
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    try:
        t0 = time.time()
        code, v = ask(port, "GET")
        t1 = time.time()
        assert code == 200 and v["slot_wait"]["refusals"] == 2
        assert {line["kind"] for line in v["slot_wait"]["lines"]} == {"gates", "landing"}
        # The route reads its own `now` inside the call, so the minutes it carries are pinned to the clock the call ran
        # between: the first record speaks 300 s to the second, and the second speaks the stretch that ended at that
        # `now` (at most the gap cap).
        route = {line["kind"]: line["minutes"] for line in v["slot_wait"]["lines"]}
        assert route["gates"] == 5.0, "the first refusal's 300 s to the second are 5 minutes of gate wait"
        # The route's own now lies inside [t0, t1], and its minutes are rounded to one decimal: the value the same clock
        # gives is the value the route carries.
        assert route["landing"] == round(((t0 + t1) / 2 - (now - 300)) / 60, 1), \
            f"the last refusal speaks from its instant to the route's own now, never to another clock: {route}"
        # The fold on a frozen clock: the first record speaks its five minutes to the second record, and the second
        # record speaks to the `now` handed in, one gap cap at most.
        out = mod.slot_wait_view(86400, now, [], allowed=True)
        assert {line["kind"]: line["minutes"] for line in out["lines"]} == {"gates": 5.0, "landing": 5.0}
    finally:
        path.unlink(missing_ok=True)
    assert mod.slot_wait_view(86400, now, [], allowed=False) is None, "a refused caller gets no split"


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


def test_each_profile_s_tab_keeps_its_own_switch_and_window_over_one_shared_scan(tmp_path, monkeypatch):
    """Two profiles of one hub share the machine's process table, never their switch, their window or their scope: one
    scan answers both ticks, and each tab names the other profile's loop in its context row (#1746, #1753)."""
    prof, co_a, co_z = tmp_path / "profiles", tmp_path / "a", tmp_path / "z"
    for d in (prof, co_a, co_z):
        d.mkdir()
    for slug, co in (("alpha", co_a), ("zesty", co_z)):
        (prof / f"{slug}.sh").write_text(
            f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(tmp_path / "state"))
    apps = load("hubmux_profiler_two", SCRIPTS / "hubmux.py").load_apps()
    a, z = apps["alpha"]["mod"], apps["zesty"]["mod"]
    table = Table()
    monkeypatch.setitem(sys.modules["procscan"]._STATE, "snap", None)   # no scan a test before this one left behind
    monkeypatch.setitem(sys.modules["procscan"]._STATE, "at", None)
    for m in (a, z):
        m.runs = lambda: []
        m.process_table = table
        m.PROFILER_OTHERS.update(at=0.0, rows=())
    for m, pid in ((a, 10), (z, 20)):
        pathlib.Path(m.STATE).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(m.STATE) / "watchd.pid").write_text(f"{pid}\n")
    assert pathlib.Path(a.PROFILER_SWITCH).parent != pathlib.Path(z.PROFILER_SWITCH).parent, "each tab keeps its own switch"
    assert a.PROFILER["window"] is not z.PROFILER["window"], "each tab folds its own window"
    assert a.profiler_scope().others[0][:2] == ("zesty", 20) and z.profiler_scope().others[0][:2] == ("alpha", 10)
    before = table.reads
    now = time.time()
    a.profiling_table(now)
    z.profiling_table(now)
    assert table.reads == before + 1, "one scan of the process table answers every profile of the tick"


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
    table.before = lambda: time.sleep(0.25) if table.reads == 0 else None   # a loaded machine: the first read outlasts the idle bound (#2013)
    gate.touch()
    assert ask(port, "POST", {"on": True})[0] == 200
    wait(lambda: gate.touch() or table.reads >= 2)           # a page keeps reading until two reads are seen, however slow
    wait(lambda: not mod.profiler_running())                 # no page reads the profile: the sampler ends by itself
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


def test_an_unanswered_subject_rests_at_the_tick_after_the_startup_window(hub, monkeypatch):
    """A subject whose reader answers None never publishes, so the fast first-read period must end: kept forever it read
    fifty times a second for as long as a page read the profile, and the mux burned a whole core (#1875)."""
    mod = hub[0]
    pauses = []

    class Versions:
        @classmethod
        def refresh_one(cls, name):
            pass
        @classmethod
        def newer(cls, _):
            return {}          # the subject never answers

    monkeypatch.setattr(mod, "VERSIONS", Versions())
    monkeypatch.setattr(mod.hubstream, "TICK_SECONDS", 7.0)
    monkeypatch.setattr(mod.hubstream, "FIRST_POLL_SECONDS", 0.01)
    monkeypatch.setattr(mod.hubstream, "FIRST_EVENT_SECONDS", 0.0)   # the startup window is over before the first tick
    monkeypatch.setattr(mod, "READ_GATE", None)                      # a page reads the profile: read_wanted() is true

    def pause(secs):
        pauses.append(secs)
        if len(pauses) >= 2:
            raise StopSampling
    monkeypatch.setattr(mod, "read_pause", pause)
    monkeypatch.setattr(threading, "excepthook", lambda _: None)
    t = mod._ticker("probe")
    wait(lambda: not t.is_alive(), 2)
    assert pauses and pauses[0] == 7.0, f"the reader asked {pauses[:2]} s: the fast first-read period never ended"


def test_every_subject_answers_at_the_tick_whatever_its_read_costs(hub, monkeypatch):
    """#1896: `board_served` costs hundreds of milliseconds, and #1879 made it rest 5 s to save them. A rest longer than
    the page's own wait is a page that shows an old board: two stream tests hold a change to a 4 s budget. So every
    subject answers at the tick, and the cost is fixed in the read itself, never in the period that delays the page."""
    mod = hub[0]

    class Versions:
        @classmethod
        def refresh_one(cls, name):
            pass
        @classmethod
        def newer(cls, _):
            return {}

    monkeypatch.setattr(mod, "VERSIONS", Versions())
    monkeypatch.setattr(mod.hubstream, "TICK_SECONDS", 1.0)
    monkeypatch.setattr(mod.hubstream, "FIRST_EVENT_SECONDS", 0.0)   # the startup window is over before the first tick
    monkeypatch.setattr(mod, "READ_GATE", None)                      # a page reads the profile: read_wanted() is true
    monkeypatch.setattr(threading, "excepthook", lambda _: None)

    def first_period(name):
        pauses = []

        def pause(secs):
            pauses.append(secs)
            if len(pauses) >= 2:
                raise StopSampling
        monkeypatch.setattr(mod, "read_pause", pause)
        monkeypatch.setitem(mod._TICKERS, name, None)
        t = mod._ticker(name)
        wait(lambda: not t.is_alive(), 2)
        return pauses[0]

    assert first_period("board") == 1.0, "the board answers at the tick: a page holding an old board is the defect"
    assert first_period("runs") == 1.0, "the run list answers at the tick with the board"
    assert first_period("sessions") == 1.0, "a subject that reads in milliseconds keeps the tick"


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


# ---------------------------------------------------------------- the loop state strip (#1765)
# The strip folds two records the loop already writes: the switch audit (`loop-audit.jsonl`, one line per change with its
# writer, origin and issue) and the dated step records (`steps.tsv`, #1750). A stretch where the switch says on and no step
# started for longer than the bound is `down`, never active. The fold is pure: fixture lists in, rows out, no clock and no
# file. The marks are the same two sources plus the landing ledger and the promotion log the loop already writes.
W0 = 1_800_000_000                    # the instant the window ends in these fixtures
H = 3600


def sw(at, action, who="owner", frm="loopback", issue=None):
    return {"at": at, "action": action, "who": who, "from": frm, "issue": issue}


def stp(name, a, b=None, state="done", rc=0):
    return {"id": f"{name}-{a}", "name": name, "start": a, "end": b, "state": state, "rc": rc, "reason": None}


def beat(a, b, by=120):
    """One step every `by` seconds: a loop that runs its cycle."""
    return [stp("janitor", t, t + 5) for t in range(a, b + 1, by)]


def states(rows):
    return [(r["state"], r["start"], r["end"]) for r in rows]


def test_the_strip_folds_the_audit_and_the_step_records_into_the_four_states(prof):
    audit = [sw(W0 - 4 * H, "start", who="owner", frm="loopback"), sw(W0 - 2 * H, "stop-now", who="owner", frm="tailnet")]
    out = prof.strip(audit, beat(W0 - 4 * H, W0 - 2 * H), [], [], 4 * H, W0)
    assert states(out["rows"]) == [("active", W0 - 4 * H, W0 - 2 * H), ("off", W0 - 2 * H, W0)]


def test_a_silent_stretch_with_the_switch_on_is_down(prof):
    """The switch says on and no step started for longer than the bound: the loop died by itself, so it is down."""
    audit = [sw(W0 - 4 * H, "start")]
    steps = beat(W0 - 4 * H, W0 - 3 * H) + [stp("janitor", W0 - 3 * H, W0 - 3 * H + 5)]
    out = prof.strip(audit, steps, [], [], 4 * H, W0)
    tail = out["rows"][-1]
    last = W0 - 3 * H
    assert tail["state"] == "down" and tail["start"] == last + prof.DOWN_GAP_S and tail["end"] == W0
    assert out["shares"]["down"] == W0 - (last + prof.DOWN_GAP_S)


def test_a_window_that_starts_between_audit_lines_takes_the_earlier_state(prof):
    """The first row starts at the window's own start, in the state the last earlier audit line left."""
    audit = [sw(W0 - 10 * H, "start"), sw(W0 - H, "drain", who="loop", frm="restart", issue=1234)]
    out = prof.strip(audit, beat(W0 - 4 * H - 300, W0), [], [], 4 * H, W0)
    assert out["rows"][0] == {"start": W0 - 4 * H, "end": W0 - H, "state": "active"}
    assert out["rows"][-1] == {"start": W0 - H, "end": W0, "state": "draining"}


def test_a_window_with_no_audit_line_before_it_is_a_gap_with_its_reason(prof):
    """No audit line at or before the window is no source data, never a guessed state."""
    audit = [sw(W0 - H, "start")]
    out = prof.strip(audit, beat(W0 - 4 * H, W0), [], [], 4 * H, W0)
    first = out["rows"][0]
    assert first["state"] == "gap" and first["start"] == W0 - 4 * H and first["end"] == W0 - H
    assert first["reason"] and "audit" in first["reason"]
    assert out["rows"][-1]["state"] == "active"


def test_a_stretch_with_no_step_record_before_the_first_one_is_a_gap_named_by_its_reason(prof):
    """A cut steps file holds no record of an old stretch: its silence is no evidence of down."""
    audit = [sw(W0 - 10 * H, "start")]
    out = prof.strip(audit, beat(W0 - H, W0), [], [], 4 * H, W0)
    first = out["rows"][0]
    assert first["state"] == "gap" and first["end"] == W0 - H and "step" in first["reason"]
    assert out["rows"][-1]["state"] == "active"


def test_the_four_shares_add_up_to_the_window(prof):
    audit = [sw(W0 - 4 * H, "start"), sw(W0 - 3 * H, "drain"), sw(W0 - 2 * H, "stop-now"),
             sw(W0 - H, "start")]
    out = prof.strip(audit, beat(W0 - 4 * H, W0), [], [], 4 * H, W0)
    assert set(out["shares"]) == set(prof.LOOP_STATES)
    assert sum(out["shares"].values()) == pytest.approx(4 * H)
    assert out["gap_s"] == 0


def test_a_gap_is_counted_apart_from_the_four_shares(prof):
    audit = [sw(W0 - H, "start")]
    out = prof.strip(audit, beat(W0 - 4 * H, W0), [], [], 4 * H, W0)
    covered = sum(out["shares"].values())
    assert out["gap_s"] == 3 * H and covered + out["gap_s"] == pytest.approx(4 * H)


def test_a_step_long_before_the_window_leaves_no_row_outside_it(prof):
    """The step record is silent past the bound at the window's own start: the stretch is down from there, never a row
    that reaches back before the window, so the shares still add up to the window."""
    audit = [sw(W0 - 4 * H, "start")]
    out = prof.strip(audit, [stp("janitor", W0 - 5 * H, W0 - 5 * H + 5), stp("janitor", W0 - 3 * H, W0 - 3 * H + 5)],
                     [], [], 4 * H, W0)
    assert all(W0 - 4 * H <= r["start"] < r["end"] <= W0 for r in out["rows"]), states(out["rows"])
    assert sum(out["shares"].values()) + out["gap_s"] == pytest.approx(4 * H)
    assert states(out["rows"]) == [("down", W0 - 4 * H, W0 - 3 * H), ("active", W0 - 3 * H, W0 - 3 * H + 600),
                                    ("down", W0 - 3 * H + 600, W0)]


def test_the_marks_name_each_switch_change_and_each_delivery(prof):
    audit = [sw(W0 - 100 * H, "start", who="owner", frm="loopback"),           # before the window: no mark
             sw(W0 - 3 * H, "drain", who="loop", frm="restart", issue=1234),
             sw(W0 - 2 * H, "stop-now", who="owner", frm="tailnet")]
    landings = [{"at": W0 - 2 * H - 60, "branch": "wave/7-a", "sha": "abc123", "issue": 7}]
    promotions = [{"at": W0 - H, "branch": "stg", "sha": "def456"}, {"at": W0 - H + 60, "branch": "main", "sha": "def456"}]
    marks = prof.strip_marks(audit, landings, promotions, W0 - 4 * H, W0)
    assert [(m["kind"], m["at"]) for m in marks] == [
        ("switch", W0 - 3 * H), ("landing", W0 - 2 * H - 60), ("switch", W0 - 2 * H), ("promotion", W0 - H),
        ("promotion", W0 - H + 60)]
    drain = marks[0]
    assert (drain["who"], drain["from"], drain["issue"], drain["state"]) == ("loop", "restart", 1234, "draining")
    assert marks[1]["issue"] == 7 and marks[1]["branch"] == "wave/7-a"
    assert [m["branch"] for m in marks if m["kind"] == "promotion"] == ["stg", "main"]


def test_the_strip_window_is_four_eight_or_twenty_four_hours(prof):
    assert prof.STRIP_WINDOWS == (4 * H, 8 * H, 24 * H)
    assert [prof.strip_window(w) for w in prof.STRIP_WINDOWS] == [4 * H, 8 * H, 24 * H]
    assert [prof.strip_window(w) for w in (None, "x", 2 * H)] == [24 * H, 24 * H, 24 * H]
    assert prof.strip([], [], [], [], 4 * H, W0)["window_s"] == 4 * H, "the answer names the window it covers"


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "+00:00"


def test_the_route_answers_the_strip_from_the_files_with_the_switch_off(hub):
    """The strip reads the loop's own records only: the switch is off and no process table is read."""
    mod, port, _, state, table = hub
    now = time.time()
    (state / "loop-audit.jsonl").write_text(
        json.dumps({"at": iso(now - 4 * H - 600), "who": "owner", "from": "loopback", "action": "start"}) + "\n")
    rows = []
    for i, t in enumerate(range(int(now - 4 * H), int(now) - 60, 120)):
        rows.append(f"start\ts{i}\tjanitor\t{t}\t-\t-")
        rows.append(f"end\ts{i}\tjanitor\t{t + 5}\tdone\t0")
    (state / "steps.tsv").write_text("\n".join(rows) + "\n")
    (state / "landed.tsv").write_text(f"{int(now - 600)}\twave/7-a\tabc123\t7\n")
    (state / "promote.log").write_text(
        f"{iso(now - 300)} promote-pass: def456 not promoted: CI is red\n"
        f"{iso(now - 240)} promote-main-pass: def456789 promoted: the complete tier is green, main is at def456789\n")
    code, v = ask(port, "GET", path="/api/profiler?sw=14400")
    assert code == 200 and v["on"] is False
    strip = v["strip"]
    assert strip["window_s"] == 14400 and strip["end"] >= now - 5
    assert [r["state"] for r in strip["rows"]][0] == "active" and strip["rows"][-1]["state"] == "active"
    assert set(strip["shares"]) == set(mod.hub_profiler.LOOP_STATES)
    assert [m["kind"] for m in strip["marks"]] == ["landing", "promotion"], "a refusal is no promotion mark"
    assert strip["marks"][0]["issue"] == 7 and strip["marks"][1]["branch"] == "main"
    assert [w["s"] for w in strip["windows"]] == [14400, 28800, 86400]
    assert ask(port, "GET", path="/api/profiler?sw=99")[1]["strip"]["window_s"] == 86400
    assert table.reads == 0, "the strip reads files only"


def test_a_draining_stretch_that_stops_stepping_is_down(prof):
    """The switch still says on while draining, so a drain whose steps stop is down past the bound, never draining."""
    audit = [sw(W0 - 4 * H, "start"), sw(W0 - 2 * H, "drain", who="loop", frm="restart", issue=7)]
    steps = beat(W0 - 4 * H, W0 - 2 * H) + [stp("janitor", W0 - 2 * H + 120, W0 - 2 * H + 125)]
    out = prof.strip(audit, steps, [], [], 4 * H, W0)
    assert states(out["rows"]) == [("active", W0 - 4 * H, W0 - 2 * H),
                                   ("draining", W0 - 2 * H, W0 - 2 * H + 720),
                                   ("down", W0 - 2 * H + 720, W0)]
    assert out["shares"]["down"] == W0 - (W0 - 2 * H + 720)


def test_a_drain_that_never_steps_again_is_down_not_draining(prof):
    """A drain line followed by silence is a loop that died, drawn down for the whole stretch."""
    out = prof.strip([sw(0, "drain", who="loop", frm="restart")], [], [], [], 4 * H, 4 * H)
    assert any(r["state"] == "down" for r in out["rows"]), out["rows"]
    assert out["shares"]["draining"] == 0


def test_a_missing_step_record_is_a_gap_never_down(prof):
    """No step record at all is no source data: the stretch is a gap with its reason, apart from the four shares."""
    out = prof.strip([sw(W0 - 4 * H, "start")], None, [], [], 4 * H, W0)
    assert out["rows"] == [{"start": W0 - 4 * H, "end": W0, "state": "gap", "reason": "no step record"}]
    assert out["gap_s"] == 4 * H and sum(out["shares"].values()) == 0


def test_a_prefixed_promotion_line_still_marks_its_delivery(hub, monkeypatch):
    """A writer that wraps the line must not hide the promotion: the nearest date before the marker is the mark's own."""
    mod, _, _, state, _ = hub
    monkeypatch.setenv("SG_MAIN_BRANCH", "trunk")
    monkeypatch.setenv("SG_STG_BRANCH", "stage")
    now = int(time.time())
    (state / "promote.log").write_text(
        f"[{iso(now - 300)}] promote-pass: aaa111 promoted: the CI checks carried the full pass\n"
        f"note: {iso(now - 240)} promote-main-pass: bbb222 promoted: main moved to bbb222\n"
        f"{iso(now - 180)} promote-pass: ccc333 not promoted: CI is red\n")
    out = mod.strip_promotions()
    assert [(p["branch"], p["sha"]) for p in out] == [("stage", "aaa111"), ("trunk", "bbb222")], "each marker names its branch, a refusal is no mark"


STRIP = {"window_s": 14400, "start": W0 - 14400, "end": W0,
         "windows": [{"s": 14400, "label": "4 hours"}, {"s": 28800, "label": "8 hours"},
                     {"s": 86400, "label": "24 hours"}],
         "labels": {"active": "Active", "draining": "Draining", "off": "Off", "down": "Down"},
         "rows": [{"start": W0 - 14400, "end": W0 - 5400, "state": "active"},
                  {"start": W0 - 5400, "end": W0 - 1800, "state": "down"},
                  {"start": W0 - 1800, "end": W0 - 900, "state": "gap", "reason": "no step record in this stretch"},
                  {"start": W0 - 900, "end": W0, "state": "active"}],
         "shares": {"active": 9000, "draining": 0, "off": 0, "down": 3600}, "gap_s": 1800,
         "marks": [{"at": W0 - 10000, "kind": "switch", "action": "drain", "state": "draining", "who": "loop",
                    "from": "restart", "issue": 1234},
                   {"at": W0 - 8000, "kind": "landing", "branch": "wave/7-a", "sha": "abc123", "issue": 7},
                   {"at": W0 - 4000, "kind": "promotion", "branch": "stg", "sha": "def456"}]}


def test_the_page_names_no_gap_sentence_and_draws_no_failed_runs_table():
    """The owner read the gap sentence and the rows of long verdict reasons as noise, and the loop state panel as noise with
    them, so the page draws none of it: the hub still folds the strip's rows for its own readers."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'strip': STRIP, 'runs': RUNS})},new Set())")
    assert "of the window has no source data" not in out, "the gap sentence is gone"
    assert "id=profr" not in out and "Time in failed runs" not in out, "the failed-runs panel is gone"
    assert "id=profstrip" not in out and "Loop state" not in out, "the loop state panel is gone"


def test_the_phone_reaches_the_loop_tab():
    """The phone's More menu opens the Loop view on the phone itself (#1754), drawn by the desktop's render function."""
    html = (SCRIPTS / "hub-mobile.html").read_text()
    assert re.search(r'id="more-loop" data-more="profiler"', html), "the phone opens its own Loop layer"
    assert "/*PROFILER_JS*/" in html, "the layer inlines the shared render function"


# The gates of the strip were folded for the same window (#1767): the fold stays in the route, and the page draws it no more.
# The fold from the gate-history lines is a pure function of this rule layer, so its tests feed lines in and read no file.
GATE_LINES = [
    f"{W0 - 12000}\tgate\tpass\tabc\t{W0 - 12060}\tall\troot\tpass\t-\truff mypy pytest\t-\t2\t-\tchild\t1.2\t3.4\n",
    f"{W0 - 9000}\tfast\tfail\tdef\t{W0 - 9060}\tdelta\troot\tfail\texit 1\tpytest\t-\t1\tred\ttip\t4.0\t-\n",
    f"{W0 - 6000}\tgate\tunproven\tghi\t{W0 - 6900}\tall\troot\tunproven\tbounded by 900 s\tpytest\t-\t2\t-\tentry\t2.0\t7.5\n",
    "not a gate history line\n",
    f"{W0 - 5000}\tfast\tpass\tjkl\t{W0 - 5990}\tall\troot\tpass\t-\tpytest\t-\t1\t-\tchild\n",
    f"{W0 - 4000}\tfast\tpass\tmno\t{W0 - 3000}\tall\troot\tpass\t-\tpytest\t-\t1\t-\tchild\t-\t-\n",
]


def test_the_gate_marks_fold_one_mark_per_finished_gate_and_skip_a_malformed_line(prof):
    """A gate-history line becomes one mark at its finish: kind, scope, seconds, the reason of a fail or unproven outcome
    and the load at its start and finish. A line the fold cannot read is no mark, and a pass carries no reason."""
    out = prof.gate_marks(GATE_LINES, W0 - 4 * H, W0)
    assert [m["outcome"] for m in out["marks"]] == ["pass", "fail", "unproven", "pass"], \
        "the malformed line and the finish before the start are no marks"
    assert out["counts"] == {"pass": 2, "fail": 1, "unproven": 1}, "the counts are of the window only"
    assert out["marks"][0] == {"at": W0 - 12000, "kind": "gate", "scope": "root", "outcome": "pass", "seconds": 60.0,
                               "reason": None, "load_start": 1.2, "load_finish": 3.4}
    assert out["marks"][1] == {"at": W0 - 9000, "kind": "fast", "scope": "root", "outcome": "fail", "seconds": 60.0,
                               "reason": "exit 1", "load_start": 4.0, "load_finish": None}
    assert out["marks"][2]["reason"] == "bounded by 900 s" and out["marks"][2]["load_finish"] == 7.5
    assert out["marks"][3]["load_start"] is None, "a row from before the load fields has no load reading"


def test_the_gate_marks_fold_only_the_window_it_is_given(prof):
    """A gate that finished before the window is no mark and no count: the marks share the strip's own window."""
    out = prof.gate_marks(GATE_LINES, W0 - 8000, W0)
    assert [m["outcome"] for m in out["marks"]] == ["unproven", "pass"]
    assert out["counts"] == {"pass": 1, "fail": 0, "unproven": 1}


def test_the_route_carries_the_gate_marks_of_the_strip_window(hub):
    """The /api/profiler answer names the gates of the strip's window, folded from the gate-history file."""
    mod, port, _, state, table = hub
    now = time.time()
    (state / "gate-history").write_text(
        f"{int(now - 600)}\tgate\tpass\tabc\t{int(now - 660)}\tall\troot\tpass\t-\tpytest\t-\t2\t-\tchild\t1.0\t2.0\n"
        f"{int(now - 300)}\tfast\tfail\tdef\t{int(now - 330)}\tdelta\troot\tfail\texit 1\tpytest\t-\t1\t-\ttip\t-\t-\n"
        "not a gate history line\n")
    code, v = ask(port, "GET", path="/api/profiler?sw=14400")
    assert code == 200
    gates = v["strip"]["gates"]
    assert [m["outcome"] for m in gates["marks"]] == ["pass", "fail"]
    assert gates["counts"] == {"pass": 1, "fail": 1, "unproven": 0}
    assert gates["marks"][1]["reason"] == "exit 1"
    assert table.reads == 0, "the gate marks read files only"


# ---------------------------------------------------------------- the machine load (#1768)
# Under the tab's header, on its own window, the line of the machine's one-minute load average, from the record the
# hub's load monitor appends once a minute (time, load average, CPU share). The fold is pure: fixture rows in, no clock
# and no file. A minute no record covers is a gap, never a zero: an idle machine and an unread minute are different facts.
LBASE = 1_800_000_000


def lrow(at, load, cpu=50.0):
    return {"at": at, "load": load, "cpu": cpu}


def flat(out):
    return [p for segment in out["segments"] for p in segment]


def test_the_load_line_is_the_minute_record_over_the_window(prof):
    rows = [lrow(LBASE - 300, 3.0), lrow(LBASE - 240, 4.0), lrow(LBASE - 180, 5.0)]
    out = prof.load_chart(rows, LBASE - 600, LBASE, cores=8)
    assert out["label"] == "Machine load" and out["cores"] == 8
    assert [(p["at"], p["load"]) for p in out["segments"][0]] == [
        (LBASE - 300, 3.0), (LBASE - 240, 4.0), (LBASE - 180, 5.0)]
    assert out["peak"] == 5.0, "the core reference never widens the line's own peak"
    assert out["gaps"][0] == {"start": LBASE - 600, "end": LBASE - 360,
                              "reason": "no load record before the first one in this stretch"}


def test_a_minute_with_no_record_is_a_gap_never_zero(prof):
    """A hole in the record is a gap with its reason: no record must never draw as an idle machine."""
    rows = [lrow(LBASE - 600, 1.0), lrow(LBASE - 540, 2.0), lrow(LBASE - 300, 9.0), lrow(LBASE - 240, 9.0)]
    out = prof.load_chart(rows, LBASE - 600, LBASE, cores=4)
    assert [p["load"] for p in flat(out)] == [1.0, 2.0, 9.0, 9.0], "no point stands in for the missing minutes"
    assert {"start": LBASE - 540, "end": LBASE - 360, "reason": "no load record"} in out["gaps"]
    assert out["gap_s"] == 180 + 240, "the two uncovered stretches, and nothing else"


def test_a_record_one_cadence_apart_leaves_no_hole_and_two_leaves_one_minute(prof):
    """A line covers the minute before it: the next line one cadence later leaves no hole, and a line two cadences later
    leaves exactly the minute between them a gap, never a zero (AC3)."""
    one = prof.load_chart([lrow(LBASE - 120, 1.0), lrow(LBASE - 60, 2.0)], LBASE - 120, LBASE, cores=4)
    assert [p["load"] for p in flat(one)] == [1.0, 2.0] and one["gaps"] == [], "a minute apart is continuous"
    two = prof.load_chart([lrow(LBASE - 120, 1.0), lrow(LBASE, 2.0)], LBASE - 120, LBASE, cores=4)
    assert [[p["load"] for p in s] for s in two["segments"]] == [[1.0], [2.0]], "the missing minute draws no point"
    assert two["gaps"] == [{"start": LBASE - 120, "end": LBASE - 60, "reason": "no load record"}]
    assert two["gap_s"] == 60


def test_a_record_with_no_load_breaks_the_line_and_draws_no_value(prof):
    rows = [lrow(LBASE - 600, 1.0), lrow(LBASE - 540, None), lrow(LBASE - 480, 3.0)]
    out = prof.load_chart(rows, LBASE - 600, LBASE, cores=4)
    assert [[p["load"] for p in s] for s in out["segments"]] == [[1.0], [3.0]], "an unreadable field draws no point"
    assert {"start": LBASE - 600, "end": LBASE - 540, "reason": "no load record"} in out["gaps"]


def test_no_minute_record_is_one_gap_with_its_reason(prof):
    for rows in ([], None):
        out = prof.load_chart(rows, LBASE - 600, LBASE, cores=8)
        assert out["segments"] == [] and out["peak"] == 0
        assert out["gaps"] == [{"start": LBASE - 600, "end": LBASE, "reason": "no load record"}]
        assert out["gap_s"] == 600


def test_a_record_outside_the_window_is_not_drawn(prof):
    rows = [lrow(LBASE - 800, 1.0), lrow(LBASE - 300, 2.0), lrow(LBASE + 300, 3.0)]
    out = prof.load_chart(rows, LBASE - 600, LBASE, cores=8)
    assert [p["load"] for p in flat(out)] == [2.0]


def test_a_record_line_reads_time_load_and_cpu_share(prof):
    assert prof.load_row("1800000000.000\t6.25\t78.5") == {"at": 1_800_000_000.0, "load": 6.25, "cpu": 78.5, "ram": None}
    assert prof.load_row("1800000000.000\t-\t-\t-") == {"at": 1_800_000_000.0, "load": None, "cpu": None, "ram": None}
    assert prof.load_row("garbage") is None and prof.load_row("") is None


def test_a_record_line_reads_the_memory_share_and_an_old_line_reads_none(prof):
    assert prof.load_row("1800000000.000\t6.25\t78.5\t61.2")["ram"] == 61.2
    assert prof.load_row("1800000000.000\t6.25\t78.5")["ram"] is None, "an old-shape line names no memory, never zero"
    assert prof.load_row("1800000000.000\t6.25\t78.5\t-")["ram"] is None, "a failed memory source writes no value"


def test_the_core_count_is_the_reference_line_and_one_core_is_the_floor(prof):
    out = prof.load_chart([lrow(LBASE - 90, 16.0)], LBASE - 600, LBASE, cores=16)
    assert out["cores"] == 16 and out["peak"] == 16.0
    assert prof.load_chart([], LBASE - 600, LBASE, cores=0)["cores"] == 1, "a machine that cannot say its cores still draws one"


def test_a_machine_load_payload_is_never_drawn_as_a_chart():
    """#2228: the Loop page draws no Machine Load chart. The payload may still carry the fold; no view draws it."""
    over = {**LOAD, "peak": 32.0, "cores": 8, "gap_s": 0, "gaps": [],
            "segments": [[{"at": LOAD["start"], "load": 32.0, "cpu": 90.0}]]}
    out = run_js(f"profHtml({json.dumps({**VIEW, 'strip': STRIP, 'load': over, 'trip': TRIP})},new Set())")
    assert "id=profload" not in out and "Machine load" not in out, "the Machine Load chart is gone"
    assert "class=plc" not in out and "saturation &middot;" not in out, "the core reference line went with the chart"


def test_the_route_answers_the_machine_load_on_the_strips_own_window(hub):
    """The load is the rule layer's fold of the minute record: its rows are in the answer, on the strip's own window, so
    a column of the page is one instant in both. A file view: no process table."""
    mod, port, _, state, table = hub
    now = time.time()
    rows = [f"{now - 600 + i * 60:.3f}\t{2.0 + i:.2f}\t{50.0 + i:.1f}" for i in range(11)]
    (state / "load-record.tsv").write_text("\n".join(rows) + "\n")
    mod.LOAD_STATE.update(record_rows=None, record_stamp=None)
    code, v = ask(port, "GET", path="/api/profiler?sw=14400")
    assert code == 200
    load = v["load"]
    assert load["start"] == v["strip"]["start"] and load["end"] == v["strip"]["end"], "one column, one instant"
    assert load["label"] == "Machine load" and load["cores"] == (os.cpu_count() or 1)
    assert len(flat(load)) >= 10 and load["peak"] > 0 and load["gaps"], "an unrecorded head is a gap, never a zero"
    assert [p["load"] for p in flat(load)] == [2.0 + i for i in range(11)], "the points are the file's own lines"
    assert table.reads == 0, "the load line reads files only"


# The load monitor's own minute record: one line a minute, written atomically and bounded to a fixed age, with the CPU
# sampling switch either way. The CPU share is the mean of the tick samples the monitor already took, so the record adds
# no second reader of the machine.
def test_the_minute_record_carries_time_load_and_cpu_share(hub):
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
    for i, (at, cpu) in enumerate(((LBASE, 80.0), (LBASE + 30, 40.0), (LBASE + 60, 20.0))):
        mod.load_tick({"cpu": cpu, "ram": 50.0, "gpu": None}, now=at, load=3.0 + i)
    lines = [ln.split("\t") for ln in path.read_text().splitlines()]
    assert len(lines) == 2, "the first tick writes a line, and the next one waits the minute"
    assert (float(lines[0][0]), lines[0][1], lines[0][2]) == (LBASE, "3.00", "-"), \
        "the first line of a cold record has one sample, so it names no share"
    assert (float(lines[1][0]), lines[1][1], lines[1][2]) == (LBASE + 60, "5.00", "30.0"), "the share is the tick samples' mean"


def test_the_minute_record_line_carries_the_memory_share_beside_load_and_cpu(hub):
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    for i, (at, cpu, ram) in enumerate(((LBASE, 80.0, 50.0), (LBASE + 30, 40.0, 60.0), (LBASE + 60, 20.0, 70.0))):
        mod.load_tick({"cpu": cpu, "ram": ram, "gpu": None}, now=at, load=3.0 + i)
    lines = [ln.split("\t") for ln in path.read_text().splitlines()]
    assert lines[0][1:] == ["3.00", "-", "50.0"], "the first line carries the memory of its own tick beside load and CPU"
    assert lines[1][1:] == ["5.00", "30.0", "70.0"], "the memory is the reading of the tick that wrote the line"


def test_a_failed_memory_source_writes_no_memory_value_and_keeps_load_and_cpu(hub):
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    for i, (at, cpu) in enumerate(((LBASE, 80.0), (LBASE + 30, 40.0), (LBASE + 60, 20.0))):
        mod.load_tick({"cpu": cpu, "ram": None, "gpu": None}, now=at, load=3.0 + i)
    lines = [ln.split("\t") for ln in path.read_text().splitlines()]
    assert lines[1][1:] == ["5.00", "30.0", "-"], "a memory source that fails writes no value; load and CPU stay"


def test_an_old_shape_line_keeps_its_place_and_names_no_memory(hub):
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE - H}\t1.00\t10.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    rows = mod.load_record_add(LBASE, 2.0, 20.0, 45.0)
    assert [r["ram"] for r in rows] == [None, 45.0], "the old line reads with no memory, never zero"


def test_the_minute_record_drops_the_lines_past_its_fixed_age(hub):
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE - 25 * H}\t1.00\t10.0\n{LBASE - H}\t2.00\t20.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
    mod.load_tick({"cpu": 30.0, "ram": None, "gpu": None}, now=LBASE, load=3.0)
    ats = [float(ln.split("\t")[0]) for ln in path.read_text().splitlines()]
    assert ats == [LBASE - H, LBASE], "a line older than the fixed age leaves, the rest stays"


def test_the_minute_record_is_capped_in_lines_as_well_as_in_age(hub):
    """The file has a hard line cap beside the age (#1768): the age is read as cadence times the cap, so a cadence shorter
    than the one the age was written for can never grow the file past LOAD_RECORD_MAX_LINES."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    assert mod.LOAD_RECORD_MAX_LINES == mod.LOAD_RECORD_MAX_AGE // mod.LOAD_RECORD_SECS
    n, first = mod.LOAD_RECORD_MAX_LINES + 5, LBASE - 3000
    path.write_text("".join(f"{first + i:.3f}\t1.00\t10.0\n" for i in range(n)))
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
    mod.load_tick({"cpu": 30.0, "ram": None, "gpu": None}, now=LBASE, load=2.0)
    lines = path.read_text().splitlines()
    assert len(lines) == mod.LOAD_RECORD_MAX_LINES, "the oldest lines leave at the cap, not only at the age"
    assert float(lines[-1].split("\t")[0]) == LBASE, "the new line is the newest"
    assert float(lines[0].split("\t")[0]) == first + 6, "the six oldest left, and the newest are the ones kept"


def test_an_interrupted_minute_record_leaves_the_old_record_whole(hub, monkeypatch):
    """ADR-0030: the write is a temp file and a rename, so an interrupted hub leaves the record as it was, never half a line."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE - H}\t1.00\t10.0\n")
    before = path.read_bytes()

    def boom(*a, **k):
        raise OSError("interrupted")
    monkeypatch.setattr(mod.os, "replace", boom)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE, load=2.0)
    assert path.read_bytes() == before


def test_the_minute_record_write_syncs_the_file_and_its_folder_before_it_is_visible(hub, monkeypatch):
    """ADR-0030: the write is durable before it is visible, so a power cut leaves the old record or the whole new one,
    never a torn line. The temp file is synced, then renamed, then its folder is synced (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
    seen, real = [], mod.os.fsync

    def spy(fd):
        seen.append("dir" if stat.S_ISDIR(mod.os.fstat(fd).st_mode) else "file")
        return real(fd)
    monkeypatch.setattr(mod.os, "fsync", spy)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE, load=2.0)
    assert seen == ["file", "dir"], f"the temp file and then its folder are synced, in that order: {seen}"
    assert path.read_text().splitlines()[0].split("\t")[1] == "2.00", "the synced bytes are the record"


def test_the_minute_record_is_written_with_the_cpu_sampling_switch_either_way(hub, monkeypatch):
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    for on in (False, True):
        monkeypatch.setattr(mod, "profiler_on", lambda on=on: on)
        path.unlink(missing_ok=True)
        mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
        mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE + on, load=4.0)
        assert path.is_file() and len(path.read_text().splitlines()) == 1, f"the record is this monitor's own: switch {on}"


def test_the_record_takes_the_tick_s_own_sample_and_load_read(hub, monkeypatch):
    """The record adds no reader of its own: a tick that was given its sample and load reads neither the CPU nor the load
    average a second time."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0)
    monkeypatch.setattr(mod, "SOURCES", {"cpu": lambda: (_ for _ in ()).throw(AssertionError("a second CPU read")),
                                         "ram": lambda: None, "gpu": lambda: None})
    monkeypatch.setattr(mod, "_loadavg", lambda: (_ for _ in ()).throw(AssertionError("a second load read")))
    mod.load_tick({"cpu": 42.0, "ram": None, "gpu": None}, now=LBASE, load=1.5)
    assert path.read_text().split("\t")[1:] == ["1.50", "-", "-\n"], "the tick's own load, and no share on a cold record"


def test_a_failed_write_keeps_the_pending_window_one_minute(hub, monkeypatch):
    """A state folder this hub cannot write degrades to a gap: the failed minute still advances, and the next line's share
    is the mean of the ticks since it, never a mean over the whole failed stretch (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    real = mod.os.replace

    def boom(*a, **k):
        raise OSError("read-only state folder")
    monkeypatch.setattr(mod.os, "replace", boom)
    mod.load_tick({"cpu": 90.0, "ram": None, "gpu": None}, now=LBASE, load=9.0)
    assert mod.LOAD_STATE["record_at"] == LBASE, "the failed minute advanced"
    assert (mod.LOAD_STATE["record_cpu_sum"], mod.LOAD_STATE["record_cpu_n"]) == (0.0, 0), \
        "the failed minute's samples never join the next line"
    monkeypatch.setattr(mod.os, "replace", real)
    mod.load_tick({"cpu": 30.0, "ram": None, "gpu": None}, now=LBASE + 60, load=3.0)
    assert path.read_text().splitlines()[0].split("\t")[2] == "30.0", "the share is the ticks since the failed minute"


def test_a_restarted_hub_writes_no_second_line_for_a_minute_the_record_holds(hub):
    """A hub restarted inside a minute the record already holds writes no second line for it: the cadence stays one line
    a minute across a restart (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE:.3f}\t1.00\t10.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE + 30, load=2.0)
    assert len(path.read_text().splitlines()) == 1, "the minute is already written"


def test_a_restart_after_a_stale_minute_writes_the_next_line_at_once(hub):
    """A restart whose last line is older than the cadence writes the first tick's line at once (#1768): the chart's
    first point after a restart is never held another minute past a record that already went stale."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE - 600:.3f}\t1.00\t10.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE, load=2.0)
    assert [float(ln.split("\t")[0]) for ln in path.read_text().splitlines()] == [LBASE - 600, LBASE], \
        "the stale minute wrote its line on the first tick"


def test_the_pending_cpu_share_is_a_running_sum_and_a_count(hub):
    """The minute's pending samples are a running sum and a count, never a list that grows with the tick rate (#1768):
    a sampler set to a tiny interval can never grow the hub's memory for the whole minute."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    for i, cpu in enumerate((10.0, 20.0, 60.0)):
        mod.load_tick({"cpu": cpu, "ram": None, "gpu": None}, now=LBASE + i, load=1.0)
    assert "record_cpu" not in mod.LOAD_STATE, "no unbounded list of pending samples"
    assert (mod.LOAD_STATE["record_cpu_sum"], mod.LOAD_STATE["record_cpu_n"]) == (80.0, 2), \
        "the first tick wrote its line; the two after it are pending"
    mod.load_tick({"cpu": 5.0, "ram": None, "gpu": None}, now=LBASE + 60, load=1.0)
    assert (mod.LOAD_STATE["record_cpu_sum"], mod.LOAD_STATE["record_cpu_n"]) == (0.0, 0), "the written minute cleared"
    assert path.read_text().splitlines()[-1].split("\t")[2] == "28.3", "the share is the sum over the count"


def test_a_clock_that_steps_back_never_leaves_the_record_out_of_order(hub):
    """A line after `now` is a leftover of a clock that stepped back: it leaves with the next write, so the chart's
    minutes run forward (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE + 3600:.3f}\t9.00\t90.0\n{LBASE - 120:.3f}\t1.00\t10.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    mod.load_tick({"cpu": 30.0, "ram": None, "gpu": None}, now=LBASE, load=2.0)
    ats = [float(ln.split("\t")[0]) for ln in path.read_text().splitlines()]
    assert ats == [LBASE - 120, LBASE], "the line after now left and the record stays in time order"


def test_a_forward_clock_step_keeps_the_lines_before_it(hub):
    """A clock that jumps forward skips minutes; the lines the record already holds are real minutes of the machine's
    past, so the step keeps them and the chart draws the skipped stretch as the gap it is (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE - 120:.3f}\t1.00\t10.0\n{LBASE - 60:.3f}\t1.50\t20.0\n")
    step = LBASE + 3600
    rows = mod.load_record_add(step, 4.0, 40.0)
    ats = [float(ln.split("\t")[0]) for ln in path.read_text().splitlines()]
    assert ats == [LBASE - 120, LBASE - 60, step], "the lines before the step stay, in order"
    assert [r["at"] for r in rows] == ats, "the rows the write returns are the ones it kept"


def test_the_minute_record_write_reads_the_tail_it_can_keep_never_the_whole_file(hub, monkeypatch):
    """A write keeps the newest `LOAD_RECORD_MAX_LINES` lines, so it reads that many from the file's end: the whole-file
    reader never runs on the write path (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text("".join(f"{LBASE - 600 + i:.3f}\t1.00\t10.0\n" for i in range(20)))
    monkeypatch.setattr(mod, "_read", lambda p: (_ for _ in ()).throw(AssertionError("a whole-file read")))
    rows = mod.load_record_add(LBASE, 2.0, 20.0)
    assert len(rows) == 21
    assert float(path.read_text().splitlines()[-1].split("\t")[0]) == LBASE


def test_one_load_read_a_tick_serves_every_profile_and_the_record(hub, monkeypatch):
    """The sampler's tick, given its sample, reads the machine's load once and the CPU never: the other profiles' reads
    and the record's line take that one read, so the record adds no reader of the machine of its own (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    reads, cpu_reads = [], []

    def loadavg():
        reads.append(1)
        return 2.5
    monkeypatch.setattr(mod, "_loadavg", loadavg)
    monkeypatch.setattr(mod, "SOURCES", {"cpu": lambda: cpu_reads.append(1), "ram": None, "gpu": None})
    assert [mod.load_average(LBASE) for _ in range(3)] == [2.5, 2.5, 2.5], "three profiles read in one tick"
    sample = {"cpu": 30.0, "ram": None, "gpu": None}
    mod.load_tick(sample, now=LBASE)                                   # the tick that turns the minute
    mod.load_tick(sample, now=LBASE + mod.LOAD_SAMPLE_SECS)            # the next tick, past the cache's window
    assert (len(reads), len(cpu_reads)) == (1, 0), "one load read for the minute, and no CPU read beside it"
    assert path.read_text().split("\t")[1] == "2.50", "the record's line took the minute's read"


def test_a_tick_period_that_divides_no_minute_still_writes_one_line_a_minute_on_the_minute(hub):
    """A line sits on its minute: a tick every seven seconds writes one line a minute (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    for i in range(26):
        mod.load_tick({"cpu": 40.0, "ram": None, "gpu": None}, now=LBASE + 7 * i, load=2.0)
    ats = [float(ln.split("\t")[0]) for ln in path.read_text().splitlines()]
    assert ats == [LBASE, LBASE + 60, LBASE + 120], "one line on each minute, none on the tick's drift"


def test_the_record_write_keeps_its_cap_whatever_the_width_of_its_lines(hub):
    """The write keeps every line the cap can hold, however wide the lines are (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    n, pad = mod.LOAD_RECORD_MAX_LINES - 1, "p" * 200
    path.write_text("".join(f"{LBASE - (n - i) * 60:.3f}\t1.00\t10.0\t{pad}\n" for i in range(n)))
    rows = mod.load_record_add(LBASE, 2.0, 20.0)
    assert len(rows) == mod.LOAD_RECORD_MAX_LINES, "every line the cap can keep is kept, however wide"


def test_a_warm_record_drops_the_lines_after_now_as_the_writer_does(hub):
    """A line after now is a clock's leftover: the warm rows leave it out, as the writer does (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE:.3f}\t1.00\t10.0\n{LBASE + 3600:.3f}\t9.00\t90.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    mod.load_tick({"cpu": 30.0, "ram": None, "gpu": None}, now=LBASE + 10, load=2.0)
    assert [r["at"] for r in mod.LOAD_STATE["record_rows"]] == [LBASE], "a line after now is no row of this clock"


def test_a_tick_with_no_readable_load_writes_no_line(hub, monkeypatch):
    """A host that cannot read its load writes no line, so the chart shows a gap, not dashes (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    monkeypatch.setattr(mod, "_loadavg", lambda: None)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE)
    assert not path.exists(), "no readable load, no line"


def test_a_restart_after_a_stale_record_writes_its_line_with_no_share(hub):
    """A stale record is a cold start: one sample is no minute's mean, so the line names no share (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE - 600:.3f}\t1.00\t10.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    mod.load_tick({"cpu": 42.0, "ram": None, "gpu": None}, now=LBASE, load=2.0)
    assert path.read_text().splitlines()[-1].split("\t")[2] == "-", "one sample after a stale record names no share"


def test_the_load_is_read_only_on_the_tick_that_turns_the_minute(hub, monkeypatch):
    """A tick inside a minute the record holds reads no load (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.write_text(f"{LBASE:.3f}\t1.00\t10.0\n")
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    monkeypatch.setattr(mod, "_loadavg", lambda: (_ for _ in ()).throw(AssertionError("a load read inside the minute")))
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE + 10)


def test_the_load_view_folds_the_rows_the_sampler_kept(hub, monkeypatch):
    """The view takes the rows the sampler parsed and kept: it adds no second read of the record file per page poll
    (#1768)."""
    mod, _, _, state, _ = hub
    (state / "load-record.tsv").unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE, load=3.0)
    monkeypatch.setattr(mod, "load_record_rows",
                        lambda: (_ for _ in ()).throw(AssertionError("a second read of the record")))
    out = mod.load_view({"start": LBASE - 300, "end": LBASE})
    assert [p["load"] for seg in out["segments"] for p in seg] == [3.0]


def test_a_poll_re_reads_the_record_once_its_identity_moved(hub):
    """A poll re-reads the record only when its identity moved, so another writer's line shows (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE, load=3.0)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{LBASE + 60:.3f}\t9.00\t90.0\n")
    out = mod.load_view({"start": LBASE - 300, "end": LBASE + 120})
    assert [p["load"] for seg in out["segments"] for p in seg] == [3.0, 9.0], "the poll shows the line another writer appended"


def test_a_poll_with_an_unmoved_record_reads_nothing(hub, monkeypatch):
    """An unmoved record costs a poll one stat and no read (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None, record_stamp=None)
    mod.load_tick({"cpu": 50.0, "ram": None, "gpu": None}, now=LBASE, load=3.0)
    monkeypatch.setattr(mod, "load_record_rows",
                        lambda: (_ for _ in ()).throw(AssertionError("a re-read of an unmoved record")))
    out = mod.load_view({"start": LBASE - 300, "end": LBASE + 120})
    assert [p["load"] for seg in out["segments"] for p in seg] == [3.0]


def test_the_first_line_of_a_cold_record_carries_no_cpu_share(hub):
    """A cold record has one tick's CPU reading, which is no minute's mean: its first line carries no share rather than a
    figure the next minute would contradict (#1768)."""
    mod, _, _, state, _ = hub
    path = state / "load-record.tsv"
    path.unlink(missing_ok=True)
    mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
    mod.load_tick({"cpu": 80.0, "ram": None, "gpu": None}, now=LBASE, load=3.0)
    assert path.read_text().splitlines()[0].split("\t")[1:] == ["3.00", "-", "-"], \
        "one sample is no minute mean, so the first line names no share"
    mod.load_tick({"cpu": 20.0, "ram": None, "gpu": None}, now=LBASE + 60, load=2.0)
    assert path.read_text().splitlines()[1].split("\t")[2] == "20.0", "the second line carries the minute's mean"


def test_a_check_hub_on_a_scratch_state_folder_writes_the_record_s_first_line(tmp_path, monkeypatch):
    """A check hub is hubmux.py on any port but the live one, and it keeps its state out of the live hub: a scratch state
    folder proves the load monitor appends the record's first line by itself, with no page and no profiler switch."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    co = tmp_path / "co"
    (co / "docs" / "squad").mkdir(parents=True)
    (prof / "alpha.sh").write_text(f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; '
                                   f': "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_load_record", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    mod = apps["alpha"].get("mod")
    assert mod is not None, apps["alpha"].get("err")
    mod.LOAD_RECORD_SECS = 0.02
    mod.foot_refresh = lambda: None                       # the record is the only pass this test asks for
    mod.SOURCES = {"cpu": lambda: 40.0, "ram": lambda: 50.0, "gpu": lambda: None}

    done = threading.Event()

    def pause(secs):
        if done.is_set() or mod.load_record_path().is_file():
            raise StopSampling
        time.sleep(min(secs, 0.01))
    monkeypatch.setattr(mod, "read_pause", pause)
    monkeypatch.setattr(threading, "excepthook", lambda _: None)
    for name in mux.SAMPLERS:
        if name != "load_sampler":
            monkeypatch.setattr(mod, name, lambda: None)

    def samplers():
        return [t for t in threading.enumerate() if t.name.endswith(":load_sampler")]
    before = set(samplers())
    mux.start_profile(apps["alpha"])
    started = [t for t in samplers() if t not in before]
    try:
        mux.touch_profile(apps["alpha"])                    # a page read: the profile's own reads run
        wait(lambda: mod.load_record_path().is_file() and mod.load_record_path().read_text().strip())
        assert str(mod.load_record_path()).startswith(str(base)), "the record lands in the profile's own state folder"
        first = mod.load_record_path().read_text().splitlines()[0].split("\t")
        assert len(first) == 4 and float(first[0]) > 0 and float(first[1]) >= 0 and first[2] == "-", \
            "the first line of a cold record carries the load and no CPU share"
    finally:
        done.set()                                          # the loop ends at its next pause, whatever this test saw
        for t in started:
            t.join(5)
        assert not [t for t in started if t.is_alive()], "the test leaves no sampler thread behind"


def test_two_profiles_keep_their_minute_records_in_their_own_state_folders(tmp_path, monkeypatch):
    """The record is the profile's, never the hub's (#1768): each profile writes its line in its own state folder, and
    the path is resolved when the record is used, so two profiles of one hub never share one file."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug in ("alpha", "beta"):
        co = tmp_path / f"co-{slug}"
        (prof / f"{slug}.sh").write_text(f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; '
                                         f': "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_two_records", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    mods = {slug: apps[slug].get("mod") for slug in ("alpha", "beta")}
    assert all(mods.values()), [(s, apps[s].get("err")) for s in apps]
    assert mods["alpha"].load_record_path() != mods["beta"].load_record_path(), "two profiles, two records"
    for i, (slug, mod) in enumerate(mods.items()):
        mod.LOAD_STATE.update(record_at=None, record_cpu_sum=0.0, record_cpu_n=0, record_rows=None)
        mod.load_tick({"cpu": 10.0 + 20.0 * i, "ram": None, "gpu": None}, now=LBASE, load=1.0 + i)
        path = mod.load_record_path()
        assert str(path).startswith(str(base)), f"{slug}'s record lands in its own state folder"
        assert path.read_text().splitlines() == [f"{LBASE:.3f}\t{1.0 + i:.2f}\t-\t-"], \
            f"{slug} wrote its own line, never the other profile's (a cold first line carries no share)"


LOAD = {"label": "Machine load", "start": W0 - 14400, "end": W0, "window_s": 14400, "cores": 8, "cadence": 60,
        "peak": 9.5, "gap_s": 1740,
        "segments": [[{"at": W0 - 14400, "load": 2.0, "cpu": 40.0}, {"at": W0 - 14340, "load": 4.0, "cpu": 60.0}],
                     [{"at": W0 - 12600, "load": 9.5, "cpu": 90.0}]],
        "gaps": [{"start": W0 - 14340, "end": W0 - 12600, "reason": "no load record"}]}


# ---------------------------------------------------------------- the trip chart (#2228)
# One tall chart over the trip's columns: a Tokens lane and a Machine lane. The fixture is the fold's own answer over three
# identical deliveries, so every width, mean and tick the page draws is the payload's, never a second arithmetic.
def chart_trip():
    """Three deliveries of 2400 s each, in Todo, Doing and Review, with a call in Todo and one in Doing and the minute
    record's 39 rows of the hour. The fold's own answer is the view's fixture."""
    span = span_at(2400.0)
    runs = card(("Todo", 0.0, 600.0), ("Doing", 600.0, 1800.0), ("Review", 1800.0, 2400.0))
    calls = [{"at": CS + 300.0, "tokens": 600}, {"at": CS + 1200.0, "tokens": 2400}]
    return {**TRIP_FOLD.trip([span, span, span], [], since=CS, cards=[runs, runs, runs], calls=[calls, calls, calls],
                             loads=every_minute(2400.0), now=CS + 3600.0, cores=8, mem_gb=32.0),
            "window_s": 3600, "since": CS}


CHART = chart_trip()

# The trip table's one header (#2280): the stage first, the four readings of its larger stage, then the two #2256
# columns the owner kept, then the three costs. Both screens print this list.
TRIP_HEADS = ["Stage", "Min", "Time per issue", "p85", "Max", "Parallelism", "Effective time", "Tokens / issue",
              "CPU mean", "RAM mean"]


def chart_svg(out):
    at = out.index("<svg id=proftc")
    return out[at:out.index("</svg>", at) + 6]


def chart_js(v=None):
    return run_js(f"profHtml({json.dumps({**VIEW, 'trip': v or CHART})},new Set())")


def trip_row(out, col):
    m = re.search(r'<tr data-p="trip/' + col + r'">(.*?)</tr>', out, re.S)
    return [re.sub(r"<[^>]+>", "", td) for td in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]


# ---------------------------------------------------------------- the trip chart's layout (#2253)
def chart_metrics():
    """The layout constants the page declares: the user-unit font size of the chart's text, the per-character width
    that size reserves, and the left gutter its axis values take."""
    src = (SCRIPTS / "hub-profiler.js").read_text()
    font = float(re.search(r"PROF_TRIP_FONT=([\d.]+)", src).group(1))
    ratio = float(re.search(r"PROF_TRIP_CHAR=PROF_TRIP_FONT\*([\d.]+)", src).group(1))
    return (ratio * font, float(re.search(r"PROF_TRIP_GUTTER=(\d+)", src).group(1)), font)


def label_floors(svg):
    """The least width each drawn column takes (#2278): the wider line of its label, the name or the small time under
    it, by the page's own per-character estimate, plus the 13 units of room the page keeps around it."""
    char, _, font = chart_metrics()
    names = dict(re.findall(r'<text class="ptlb pts-(\w+)"[^>]*>([^<]*)</text>', svg))
    times = {c: (float(z), t) for c, z, t in re.findall(r'<text class="ptls pts-(\w+)"[^>]*font-size=(\d+)>([^<]*)</text>', svg)}
    return {c: max(len(names[c]) * char, len(times[c][1]) * char * times[c][0] / font) + 13 for c in names}


def drawn_widths(cols, svg, span):
    """The widths the rule of #2278 gives, worked here apart from the page: a column with a share is never narrower
    than its label; the columns at their floor take it first and the others divide the rest by their shares."""
    floors = label_floors(svg)
    raw = [max(0.0, c["share"] or 0.0) for c in cols]
    low = [floors[c["column"]] if r > 0 else 0.0 for c, r in zip(cols, raw, strict=True)]
    held = set()
    while True:
        free = span - sum(low[i] for i in held)
        total = sum(r for i, r in enumerate(raw) if i not in held) or 1
        w = [low[i] if i in held else r / total * free for i, r in enumerate(raw)]
        short = [i for i in range(len(w)) if i not in held and w[i] < low[i]]
        if not short:
            return w
        held.add(short[0])


def band_starts(cols, svg, gutter):
    w = drawn_widths(cols, svg, 1000 - gutter)
    return [gutter + sum(w[:i]) for i in range(len(w))]


def text_boxes(out):
    """Every text the chart lays out as a box in the SVG's user units: its x-range from its anchor, and its width from
    the page's own per-character estimate. The chart sizes every glyph in SVG user units, so each box scales with the
    viewBox and the same boxes hold on screen at 390 px and at 1440 px."""
    char, _, font = chart_metrics()
    rows = []
    for m in re.finditer(r'<text class=(?:"([^"]*)"|([\w-]+))([^>]*)>([^<]*)</text>', chart_svg(out)):
        cls, attrs, text = (m.group(1) or m.group(2)), m.group(3), m.group(4)
        x = float(re.search(r'\bx="([\d.]+)"', attrs).group(1))
        y = float(re.search(r'\by="([\d.]+)"', attrs).group(1))
        anchor = (re.search(r"text-anchor=(\w+)", attrs) or [None, "start"])[1]
        size = re.search(r'\bfont-size="?([\d.]+)"?', attrs)
        plain = re.sub(r"&(?:#\d+|\w+);", "~", text)   # one entity is one glyph
        w = len(plain) * char * (float(size.group(1)) / font if size else 1)
        x0 = x - w if anchor == "end" else x - w / 2 if anchor == "middle" else x
        rows.append({"cls": cls, "text": text, "x0": x0, "x1": x0 + w, "y": y})
    return rows


def test_the_chart_draws_a_tokens_lane_and_a_machine_lane_each_with_its_values():
    """AC1 (#2255) and AC3 (#2254): two lanes. The Tokens lane prints three short values once in k or M, so it never
    prints a raw count; the Machine lane states the real percent of its two series at its ends, since each series
    fills the lane from its own lowest reading to its own highest."""
    out = chart_svg(chart_js())
    assert ">Tokens<" in out and ">Machine<" in out, "one lane for the tokens a minute and one for the machine"
    ticks = re.findall(r"<text class=ptt[^>]*>([^<]+)</text>", out)
    assert [t for t in ticks if t.endswith("k")] == ["0k", "0.5k", "1k"], \
        "three Tokens values in short units, and the top one stands above the token rate the payload holds"
    ends = re.findall(r'<text class="ptv (?:ptvc|ptvr)"[^>]*>([^<]+)</text>', out)
    assert ends == ["CPU 50%", "RAM 40%", "CPU 50%", "RAM 40%"], \
        "the Machine lane states the real lowest and highest percent for CPU and for RAM at its ends"
    assert "k tokens / min" in out and "min-max per line" in out, \
        "the Tokens lane names its unit, and the Machine lane names its per-line scale in the gutter"


def test_a_narrow_column_takes_a_leader_line_to_its_band():
    """AC1: a column too narrow for its label inside the chart carries a guide from its band to the label below."""
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["label"] = col["label"] + " " + "x" * 40   # five labels no plot can hold side by side
    svg = chart_svg(chart_js(v))
    assert re.findall(r'<line class="ptg pts-(\w+)"', svg) == ["todo", "doing", "review", "landing"], \
        "each narrow column with a band carries a leader line, and a column of no width gets none"
    assert re.findall(r'<line class="ptg pts-(\w+)"', chart_svg(chart_js())) == [], \
        "a column as wide as its label needs no leader line (#2278)"


def test_no_chart_text_overlaps_another_label_or_a_number_at_390_px_and_at_1440_px():
    """AC1 (#2253): every label, mean and axis value keeps its own box at the phone width and at the desktop width.
    The chart sizes its text in SVG user units, so each glyph box scales with the viewBox; the test measures the boxes
    in pixels at both widths and fails on any overlap."""
    _, _, font = chart_metrics()
    svg = chart_svg(chart_js())
    assert f"font-size={font:g}" in svg, "the chart sizes its text in SVG user units"
    css = chart_css()
    assert not re.search(r"#proftrip[^{]*\.pt(?:t|lb|lm|n)[^{]*\{[^}]*font-size", css), \
        "no CSS length overrides the chart text: a caption size does not scale with the viewBox"
    boxes = text_boxes(chart_js())
    assert boxes, "the chart lays out text to check"
    for width in (390, 1440):
        scale = width / 1000.0
        bad = [(a["text"], b["text"]) for i, a in enumerate(boxes) for b in boxes[i + 1:]
               if abs(a["y"] - b["y"]) * scale < 13 * scale
               and a["x0"] * scale < b["x1"] * scale - scale and b["x0"] * scale < a["x1"] * scale - scale]
        assert not bad, f"these texts share a box at {width} px: {bad}"


def test_each_column_prints_its_mean_inside_its_lane():
    """AC2 (#2253): every column prints the mean of each line inside its lane, at the top of the column. A narrow
    column takes a shorter copy so the number stays in the lane and never runs under the chart."""
    src = (SCRIPTS / "hub-profiler.js").read_text()
    lane = float(re.search(r"PROF_TRIP_LANE=(\d+)", src).group(1))
    top = float(re.search(r"PROF_TRIP_TOP=(\d+)", src).group(1))
    gap = float(re.search(r"PROF_TRIP_GAP=(\d+)", src).group(1))
    tok_top, mac_top = top, top + lane + gap
    means = re.findall(r'<text class="ptlm (ptlmt|ptlmc|ptlmr)" x="[\d.]+" y="([\d.]+)"[^>]*>([^<]*)</text>',
                       chart_svg(chart_js()))
    where = {}
    for line, y, text in means:
        where.setdefault(line, []).append((float(y), text))
    for line in ("ptlmt", "ptlmc", "ptlmr"):
        assert line in where, f"every column prints its {line} mean inside its lane"
    assert sum(tok_top < y < tok_top + lane for y, _ in where["ptlmt"]) == 5, \
        "every column prints its token mean in the Tokens lane"
    assert sum(mac_top < y < mac_top + lane for y, _ in where["ptlmc"]) == 5, \
        "every column prints its CPU mean in the Machine lane"
    assert sum(mac_top < y < mac_top + lane for y, _ in where["ptlmr"]) == 5, \
        "every column prints its RAM mean in the Machine lane"
    todo = [where[k][0][1] for k in ("ptlmt", "ptlmc", "ptlmr")]
    assert todo == ["mean 60", "CPU 50%", "RAM 40%"], "the wide column prints the full copy"
    assert where["ptlmt"][2][1] == "&#8212;", "a narrow column prints the short copy inside its lane"
    labels = dict(re.findall(r'<text class="ptlb pts-(\w+)"[^>]*>([^<]*)</text>', chart_svg(chart_js())))
    assert labels["review"] == "Review", "the leader row carries the column's name alone"
    assert "mean" not in "".join(labels.values()), "no mean stands under the chart: every mean is in its lane"


def test_the_machine_lane_draws_the_ram_line_where_a_reading_exists_and_states_why_none_does():
    """AC3 (#2253): the payload's RAM points draw the dashed line; a window with no memory reading draws no line and
    the page states the reason."""
    svg = chart_svg(chart_js())
    assert re.search(r'<polyline class="ptl-ram"', svg), "the payload holds RAM points, so the lane draws them"
    assert re.search(r'<polyline class="ptl-cpu"', svg), "the CPU line is drawn apart from the RAM line"
    no_ram = json.loads(json.dumps(CHART))
    for c in no_ram["columns"]:
        c["ram"] = {"points": [], "mean": None}
    no_ram.update(ram_minutes=0, window_minutes=no_ram.get("window_minutes") or 60)
    page = chart_js(no_ram)
    assert "No RAM reading in this window" in page, "the page names why every RAM cell reads a dash"
    assert not re.search(r'class="ptl-ram"', chart_svg(page)), "no RAM point draws no RAM line"


def test_a_line_joins_adjacent_columns_and_an_empty_column_is_marked_no_record():
    """AC4 (#2253): one line runs out of a column into the next; a column with no point breaks the line and the page
    marks that stretch as no record. The mark is a band over the lane, never a stroke on the data scale, so it can
    never read as a zero or as a middle-of-scale sample."""
    src = (SCRIPTS / "hub-profiler.js").read_text()
    lane = float(re.search(r"PROF_TRIP_LANE=(\d+)", src).group(1))
    top = float(re.search(r"PROF_TRIP_TOP=(\d+)", src).group(1))
    svg = chart_svg(chart_js())
    tok = re.search(r'<polyline class="ptl-tok" points="([^"]+)"', svg)
    assert tok, "the token line is one polyline over the columns that hold a point"
    gutter = chart_metrics()[1]
    todo_end = gutter + CHART["columns"][0]["share"] * (1000 - gutter)
    assert max(float(p.split(",")[0]) for p in tok.group(1).split()) > todo_end + 1, \
        "the line crosses out of Todo into Doing, so no gap stands between two adjacent columns with points"
    v = json.loads(json.dumps(CHART))
    v["columns"][2]["tokens"] = {"points": [], "mean": None}
    holed = chart_svg(chart_js(v))
    mark = re.search(r'<rect class="ptgn pts-review" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)">'
                     r'<title>([^<]*)</title></rect>', holed)
    assert mark, "the column with no Tokens point is marked as no record, never drawn as zero"
    assert (float(mark.group(2)), float(mark.group(4))) == (top, lane), \
        "the mark spans the Tokens lane from its top, so it is not on the data scale"
    assert "no record" in mark.group(5)


def test_the_machine_axis_scales_its_percent_ticks_over_the_lane():
    """AC3 (#2254): the Machine lane's values stand at the lane's own ends: two readings at its top and two at its
    bottom, so a normalized point never hides the percent it came from."""
    c = trip_consts()
    mac_top = c["top"] + c["lane"] + c["gap"]
    mac_bot = mac_top + c["lane"]
    ends = [(cls, float(y), text) for cls, y, text in re.findall(
        r'<text class="ptv (ptvc|ptvr)"[^>]*y="([\d.]+)"[^>]*>([^<]+)</text>', chart_svg(chart_js()))]
    assert [text for cls, _, text in ends if cls == "ptvc"] == ["CPU 50%", "CPU 50%"], \
        "the CPU end values stand at the lane's two ends"
    top = [y for _, y, _ in ends if abs(y - mac_top) <= 17]
    bottom = [y for _, y, _ in ends if abs(y - mac_bot) <= 17]
    assert len(top) == 2 and len(bottom) == 2, \
        "two readings stand at the lane's top and two at its bottom, one per series"


def test_the_machine_lane_scales_each_series_from_its_own_lowest_to_its_own_highest():
    """AC1-AC4 (#2254): CPU and RAM each take their own lowest and highest reading in the window. A CPU series of 40%
    and 60% reaches the lane's bottom at 40% and its top at 60%, and a RAM series of 20% and 30% spans the same lane on
    its own pair, so the two series never share one minimum and maximum. Each end states the real percent of the
    machine, a hover on a point names the real percent, and the table keeps the real mean."""
    c = trip_consts()
    scale_top = c["top"] + c["lane"] + c["gap"] + c["pad"]
    scale_bot = c["top"] + c["lane"] + c["gap"] + c["lane"] - c["pad"]
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0].update(cpu={"points": [[0.5, 0.40]], "mean": 0.40}, ram={"points": [[0.5, 0.20]], "mean": 0.20})
    v["columns"][1].update(cpu={"points": [[0.5, 0.60]], "mean": 0.60}, ram={"points": [[0.5, 0.30]], "mean": 0.30})
    out = chart_js(v)
    svg = chart_svg(out)

    def ys(cls):
        m = re.search(r'<polyline class="' + cls + r'" points="([^"]+)"', svg)
        assert m, f"the {cls} line is drawn over the two columns that hold a point"
        return [float(p.split(",")[1]) for p in m.group(1).split()]
    assert ys("ptl-cpu") == [pytest.approx(scale_bot, abs=0.1), pytest.approx(scale_top, abs=0.1)], \
        "a 40% reading sits at the scale's bottom and a 60% reading at its top"
    assert ys("ptl-ram") == [pytest.approx(scale_bot, abs=0.1), pytest.approx(scale_top, abs=0.1)], \
        "RAM spans the scale on its own 20% to 30% pair, never on CPU's 40% to 60%"
    ends = sorted(re.findall(r'<text class="ptv (ptvc|ptvr)"[^>]*>([^<]+)</text>', svg))
    assert ends == sorted([("ptvc", "CPU 40%"), ("ptvc", "CPU 60%"), ("ptvr", "RAM 20%"), ("ptvr", "RAM 30%")]), \
        "each lane end states the real lowest and highest percent, for CPU and for RAM"
    assert re.search(r'<circle class=ptph[^>]*><title>Todo: CPU 40\.0% of the whole machine</title></circle>', svg), \
        "a hover on the CPU point shows the real percent, never the normalized value"
    assert re.search(r'<circle class=ptph[^>]*><title>Doing: RAM 30\.0% of the whole machine</title></circle>', svg), \
        "a hover on the RAM point shows the real percent"
    rows = {col: trip_row(out, col) for col in ("todo", "doing")}
    assert (rows["todo"][8], rows["doing"][8]) == ("40%", "60%"), "the table keeps the real CPU mean"
    assert (rows["todo"][9], rows["doing"][9]) == ("20%", "30%"), "the table keeps the real RAM mean"


def test_a_machine_series_with_one_reading_draws_a_flat_line_at_mid_height():
    """AC5 (#2254): a series whose window holds one value draws a flat line at the lane's middle, so one reading never
    reads as the lane's top or as its bottom."""
    c = trip_consts()
    mid = c["top"] + c["lane"] + c["gap"] + c["lane"] / 2
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [[0.5, 0.55]], "mean": 0.55}
    ys = [float(p.split(",")[1]) for p in re.search(
        r'<polyline class="ptl-cpu" points="([^"]+)"', chart_svg(chart_js(v))).group(1).split()]
    assert len(ys) == len(v["columns"]) and all(y == pytest.approx(mid, abs=0.1) for y in ys), \
        "one reading in the window draws a flat line at mid height"


def test_the_machine_lane_states_its_ends_from_the_readings_it_holds():
    """AC3 (#2254): the lane's ends state the real lowest and highest percent of the readings it holds, a point and a
    column's mean alike. A mean with no point sets an end and draws a tick, so no reading the table prints goes unseen,
    and only a series with no reading at all reads a dash at both ends."""
    c = trip_consts()
    scale_bot = c["top"] + c["lane"] + c["gap"] + c["lane"] - c["pad"]
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0]["cpu"] = {"points": [[0.5, 0.40]], "mean": 0.40}
    v["columns"][1]["cpu"] = {"points": [[0.5, 0.60]], "mean": 0.60}
    v["columns"][2]["cpu"] = {"points": [], "mean": 0.20}
    svg = chart_svg(chart_js(v))
    ends = re.findall(r'<text class="ptv (ptvc|ptvr)"[^>]*>([^<]+)</text>', svg)
    assert [t for cls, t in ends if cls == "ptvc"] == ["CPU 60%", "CPU 20%"], \
        "the highest reading stands at the lane's top and the lowest, a mean with no point, at its bottom"
    assert [t for cls, t in ends if cls == "ptvr"] == ["RAM &#8212;", "RAM &#8212;"], \
        "a series with no reading in the window reads a dash at both ends"
    ys = [float(y) for y in re.findall(r'<line class="ptmk ptmkc" x1="[\d.]+" y1="([\d.]+)"', svg)]
    assert ys == [pytest.approx(scale_bot, abs=0.1)], \
        "the mean with no point draws a tick at its value on the lane, and the two points draw the line"


def test_a_machine_series_with_a_mean_and_no_point_draws_a_tick_and_states_the_mean_at_both_ends():
    """AC3 (#2254): a window whose series holds a mean and no point draws a tick at the mean on the lane, and the
    lane's ends state that mean, so the page never hides a value the table prints. A series with no reading at all
    still reads a dash at both ends."""
    c = trip_consts()
    mac_top = c["top"] + c["lane"] + c["gap"]
    scale_top = mac_top + c["pad"]
    scale_bot = mac_top + c["lane"] - c["pad"]
    mid = mac_top + c["lane"] / 2
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0]["cpu"] = {"points": [], "mean": 0.40}
    v["columns"][1]["cpu"] = {"points": [], "mean": 0.50}
    v["columns"][2]["cpu"] = {"points": [], "mean": 0.60}
    svg = chart_svg(chart_js(v))
    ends = re.findall(r'<text class="ptv (ptvc|ptvr)"[^>]*>([^<]+)</text>', svg)
    assert [t for cls, t in ends if cls == "ptvc"] == ["CPU 60%", "CPU 40%"], \
        "the lane's ends state the highest and the lowest mean it holds"
    assert [t for cls, t in ends if cls == "ptvr"] == ["RAM &#8212;", "RAM &#8212;"], \
        "a series with no reading in the window still reads a dash at both ends"
    ys = sorted(float(y) for y in re.findall(r'<line class="ptmk ptmkc" x1="[\d.]+" y1="([\d.]+)"', svg))
    assert ys == [pytest.approx(scale_top, abs=0.1), pytest.approx(mid, abs=0.1),
                  pytest.approx(scale_bot, abs=0.1)], \
        "each mean with no point draws a tick at its own value on the lane"
    assert not re.search(r'<circle class="ptl-cpu"|<polyline class="ptl-cpu"', svg), \
        "a series with no point draws no line; the mean is a tick"
    rows = {c: trip_row(chart_js(v), c) for c in ("todo", "doing")}
    assert rows["todo"][8] == "40%", "the table prints the mean the tick marks"


def test_the_phone_lane_states_a_mean_that_has_no_point():
    """AC3 (#2254): the phone's Machine lane states a series' mean with no point in its legend, and draws a tick at
    that mean in the column, so the narrow screen never hides a value the table prints."""
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0]["cpu"] = {"points": [], "mean": 0.40}
    v["columns"][1]["cpu"] = {"points": [], "mean": 0.60}
    out = run_js(f"profTrip({json.dumps(v)},true)")
    legend = re.sub(r"<[^>]+>", "", re.search(r"<span class=ptlkey>(.*?)</span></div>", out, re.S).group(1))
    assert legend.split() == ["CPU", "40%", "to", "60%", "RAM", "&#8212;"], \
        "the phone legend states the mean's lowest and highest reading"
    assert len(re.findall(r'<line class="ptl-mean ptl-cpu"', out)) == 2, \
        "each column whose CPU holds a mean and no point draws a tick at that mean"


def test_the_machine_lane_ignores_the_windows_machine_ticks():
    """AC3 (#2254): the fold no longer emits machine_ticks, and each lane scales its series from its own readings, so a
    payload that still carries the key changes nothing the wide chart or the phone lane draws."""
    assert chart_svg(chart_js({**CHART, "machine_ticks": [0, 25, 50]})) == chart_svg(chart_js()), \
        "the fold's machine_ticks no longer set the wide lane's scale"
    wide = chart_js({**CHART, "machine_ticks": [0, 25, 50]})
    assert wide == chart_js(), "the wide chart takes no reading from the fold's machine_ticks"
    assert run_js(f"profTrip({json.dumps({**CHART, 'machine_ticks': [0, 25, 50]})},true)") == \
        run_js(f"profTrip({json.dumps(CHART)},true)"), "the phone lane takes no reading from the fold's machine_ticks"


def test_a_machine_series_with_one_reading_in_one_column_draws_its_dot_at_mid_height():
    """AC5 (#2254): the realistic single-reading window holds one CPU point in one column and none in the rest. The
    lane draws that reading as a dot at the lane's middle and states the same value at both ends, so one reading
    reads as neither the lane's top nor its bottom."""
    c = trip_consts()
    mid = c["top"] + c["lane"] + c["gap"] + c["lane"] / 2
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0]["cpu"] = {"points": [[0.5, 0.55]], "mean": 0.55}
    svg = chart_svg(chart_js(v))
    dot = re.search(r'<circle class="ptl-cpu" cx="[\d.]+" cy="([\d.]+)"', svg)
    assert dot, "a run of one point draws a dot, because a polyline of one coordinate draws nothing"
    assert float(dot.group(1)) == pytest.approx(mid, abs=0.1), "the single reading sits at the lane's middle"
    assert re.findall(r'<text class="ptv ptvc"[^>]*>([^<]+)</text>', svg) == ["CPU 55%", "CPU 55%"], \
        "both ends state the one reading"


def test_each_axis_value_prints_once():
    """AC3 (#2254): the Tokens lane's three values print once, and the Machine lane's four end values print once
    each, in the gutter."""
    svg = chart_svg(chart_js())
    assert re.findall(r"<text class=ptt[^>]*>([^<]+)</text>", svg) == ["0k", "0.5k", "1k"], \
        "the Tokens lane's three axis values, each printed once"
    assert re.findall(r'<text class="ptv (?:ptvc|ptvr)"[^>]*>([^<]+)</text>', svg) == \
        ["CPU 50%", "RAM 40%", "CPU 50%", "RAM 40%"], "the Machine lane's four end values, each printed once"


def test_token_counts_print_in_k_or_m_units_on_the_axis_and_in_the_table():
    """AC6: the Tokens axis and the table print a count in k or M, never the raw integer."""
    out = chart_js()
    svg = chart_svg(out)
    assert ">1k</text>" in svg and ">1000" not in svg, "the axis rounds 1000 to 1k"
    assert (trip_row(out, "todo")[7], trip_row(out, "doing")[7]) == ("600", "2.4k"), \
        "the table prints 2400 in k units"
    big = json.loads(json.dumps(CHART))
    by = {c["column"]: c for c in big["columns"]}
    by["todo"]["tokens_per_issue"], by["doing"]["tokens_per_issue"] = 6310834.0, 834000.0
    rows = {c: trip_row(chart_js(big), c)[7] for c in ("todo", "doing")}
    assert rows == {"todo": "6.3M", "doing": "834k"}, "a million prints in M, a large thousand count in k"


def test_the_phone_table_names_the_same_columns_as_the_desktop_and_prints_k_units():
    """#2253, #2280: the phone table carries the same columns in the same order as the desktop table, and the same k or M
    token units, so the two tables never drift and no raw count stands in either."""
    out = run_js(f"profTrip({json.dumps(CHART)},true)")
    at = out.index("<table id=proftt")
    heads = [re.sub(r"<[^>]+>", "", h)
             for h in re.findall(r"<th[^>]*>(.*?)</th>", out[at:out.index("</table>", at)], re.S)]
    assert heads == TRIP_HEADS, heads
    cells = {c: [re.sub(r"<[^>]+>", "", td) for td in
                 re.findall(r"<td[^>]*>(.*?)</td>", re.search(r'<tr data-p="trip/' + c + r'">(.*?)</tr>', out, re.S).group(1), re.S)]
             for c in ("todo", "doing")}
    assert (cells["todo"][7], cells["doing"][7]) == ("600", "2.4k"), \
        "the phone's Tokens / issue cell prints 2400 in k units, never the raw integer"


def test_the_phone_lanes_mark_a_column_with_no_point_as_no_record():
    """AC4 (#2253): the phone lanes break the line at a column with no point and mark that stretch as no record, so
    the gap never reads as a zero or as a sample."""
    v = json.loads(json.dumps(CHART))
    v["columns"][2]["tokens"] = {"points": [], "mean": None}
    out = run_js(f"profTrip({json.dumps(v)},true)")
    mark = re.search(r'<rect class="ptl-nr"[^>]*><title>([^<]*)</title></rect>', out)
    assert mark and "no record" in mark.group(1), "the empty column's stretch is marked, never drawn as a zero"


def test_the_phone_machine_lane_draws_one_reading_at_mid_height_and_names_it_in_the_legend():
    """AC3 and AC5 (#2254): the phone lane and its legend state the real ends of each line. A line with one reading in
    the window says so in the legend, draws that reading at the lane's middle, and the lane's accessibility label
    names the flat case."""
    h = float(re.search(r"PROF_MACH_H=(\d+)", (SCRIPTS / "hub-profiler.js").read_text()).group(1))
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0]["cpu"] = {"points": [[0.5, 0.55]], "mean": 0.55}
    out = run_js(f"profTrip({json.dumps(v)},true)")
    assert "CPU 55% (single reading)" in out, "the legend names the one reading instead of a range of one value"
    assert "CPU 55% to 55%" not in out, "a range of one value never prints as a range"
    dot = re.search(r'<circle class="ptl ptl-cpu" cx="[\d.]+" cy="([\d.]+)"', out)
    assert dot, "a run of one point draws a dot on the phone lane"
    assert float(dot.group(1)) == pytest.approx(h / 2, abs=0.1), "one reading sits at the lane's middle"
    assert "mid height when it holds one reading" in out, "the lane's accessibility label names the flat case"
    assert "min-max per line" in out, "the phone lane names its per-line scale, never a whole-machine percent scale"


def test_the_phone_machine_legend_states_a_mean_that_has_no_point():
    """AC3 (#2254): the phone legend states every reading its line holds, a point and a mean alike, so a mean with no
    point states its percent in the legend and draws a tick in its column; the table still prints the mean."""
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": 0.40}
        col["ram"] = {"points": [], "mean": 0.20}
    out = run_js(f"profTrip({json.dumps(v)},true)")
    legend = re.search(r"<span class=ptlkey>(.*?)</span></div>", out, re.S).group(1)
    assert re.sub(r"<[^>]+>", "", legend).split() == \
        ["CPU", "40%", "(single", "reading)", "RAM", "20%", "(single", "reading)"], \
        "the legend states the percent of each series the lane holds by its mean"
    assert not re.search(r'<circle class="ptl ptl-(?:cpu|ram)"|<polyline class="ptl ptl-(?:cpu|ram)"', out), \
        "a series with no point draws no line on the phone lane"
    assert len(re.findall(r'<line class="ptl-mean ptl-cpu"', out)) == 4, \
        "each of the four columns with width draws a CPU tick; a column of no width has no lane to draw on"
    assert "<td>40%</td><td>20%</td>" in out, "the phone table still prints the mean the payload holds"


def test_both_charts_state_one_reading_with_one_precision():
    """AC3 (#2254): one precision helper formats an end reading for both lanes, so the wide chart's end and the phone
    legend's end state the same string for the same reading. A reading that rounds to a whole percent never reads with
    a decimal in one lane and without it in the other."""
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": None}
        col["ram"] = {"points": [], "mean": None}
    v["columns"][0]["cpu"] = {"points": [[0.5, 0.405]], "mean": 0.405}
    v["columns"][1]["cpu"] = {"points": [[0.5, 0.604]], "mean": 0.604}
    wide = re.findall(r'<text class="ptv ptvc"[^>]*>CPU ([^<]+)</text>', chart_svg(chart_js(v)))
    legend = re.sub(r"<[^>]+>", "", re.search(
        r"<span class=ptlkey>(.*?)</span></div>", run_js(f"profTrip({json.dumps(v)},true)"), re.S).group(1))
    assert wide == ["60%", "41%"], "the wide lane states each end with the shared precision"
    assert legend.split() == ["CPU", "41%", "to", "60%", "RAM", "&#8212;"], \
        "the phone legend states the same end strings for the same readings"


def test_the_wide_end_and_the_phone_legend_print_one_string_for_a_four_tenths_reading():
    """AC3 (#2254): one precision helper formats a reading for both lanes. A reading of 0.400 reads the same string in
    the wide lane's end and in the phone's legend, never with a decimal in one and without it in the other."""
    v = json.loads(json.dumps(CHART))
    for col in v["columns"]:
        col["cpu"] = {"points": [], "mean": 0.400}
        col["ram"] = {"points": [], "mean": None}
    wide = re.findall(r'<text class="ptv ptvc"[^>]*>CPU ([^<]+)</text>', chart_svg(chart_js(v)))
    legend = re.sub(r"<[^>]+>", "", re.search(
        r"<span class=ptlkey>(.*?)</span></div>", run_js(f"profTrip({json.dumps(v)},true)"), re.S).group(1))
    assert wide == ["40%", "40%"], "the wide lane states the mean with the shared precision"
    assert "CPU 40% (single reading)" in " ".join(legend.split()), \
        "the phone legend states the same string for the same reading"
    assert "40.0%" not in " ".join(wide) + " ".join(legend.split()), \
        "no lane prints a decimal the other drops"


def test_no_line_takes_the_color_of_a_column_band():
    """AC7: the three lines hold their own colors, none of them a band's."""
    svg = chart_svg(chart_js())
    assert not re.search(r'class="ptl-[a-z]+ pts-\w+', svg), "a line carries no column class"
    css = (SCRIPTS / "hub-profiler.css").read_text()
    line_vars = set()
    for cls in ("ptl-tok", "ptl-cpu", "ptl-ram"):
        m = re.search(r"#proftrip \." + cls + r"\{[^}]*stroke:var\((--[\w-]+)\)", css)
        assert m, f"the stylesheet names the {cls} line's colour"
        line_vars.add(m.group(1))
    band_vars = set(re.findall(r"#proftrip \.ptc\.pts-\w+\{color:var\((--[\w-]+)\)\}", chart_css())
                    or re.findall(r"#proftrip \.pts-\w+\{background:var\((--[\w-]+)\)\}", chart_css()))
    assert line_vars and band_vars and not (line_vars & band_vars), \
        f"a line takes a band's colour: {line_vars & band_vars}"
    assert re.search(r"#proftrip \.ptl-ram\{[^}]*stroke-dasharray", css), "the RAM line stays dashed, the CPU line solid"


def chart_css():
    return (SCRIPTS / "hub-profiler.css").read_text()


def test_the_width_of_each_column_follows_the_share_the_fold_emits():
    """AC13 of the spec, kept: each band opens where the one before it ends, past the axis gutter, and its width is the
    fold's own share."""
    gutter = chart_metrics()[1]
    span = 1000 - gutter
    bands = re.findall(r'<rect class="ptc pts-(\w+)" x="([\d.]+)" width="([\d.]+)"', chart_svg(chart_js()))
    assert [b[0] for b in bands] == ["todo", "doing", "review", "landing", "gating"]
    svg = chart_svg(chart_js())
    assert [float(b[1]) for b in bands] == pytest.approx(band_starts(CHART["columns"], svg, gutter), abs=0.2), \
        "a band opens where the one before it ends"
    assert [float(b[2]) for b in bands] == pytest.approx(drawn_widths(CHART["columns"], svg, span), abs=0.2), \
        "a band's width is the share the fold emits, never a second arithmetic"


def test_each_column_label_states_the_effective_time_and_the_parallelism():
    """#2256: three deliveries stand in Doing at once, so its 60 s time per issue reads 20 s at ×3.0, and the label
    carries both numbers."""
    labels = dict(re.findall(r'<text class="ptls pts-(\w+)"[^>]*>([^<]*)</text>', chart_svg(chart_js())))
    assert labels["doing"] == "20.0 s · ×3.0", "the label shows the effective time and the parallelism beside it"
    assert labels["landing"] == "10.0 s · ×1.0", "a column the board recorded no run for runs one at a time"


def test_the_trip_table_keeps_the_three_columns_of_2256_and_drops_p90_and_the_count():
    """#2280: the owner kept `Time per issue`, `Parallelism` and `Effective time` with their header, value and format,
    so only `p90` and `Observations` leave the table."""
    out = chart_js()
    at = out.index("<table id=proftt")
    heads = [re.sub(r"<[^>]+>", "", h)
             for h in re.findall(r"<th[^>]*>(.*?)</th>", out[at:out.index("</table>", at)], re.S)]
    assert [h for h in heads if h in ("Time per issue", "Parallelism", "Effective time")] == \
        ["Time per issue", "Parallelism", "Effective time"], heads
    assert not [h for h in heads if h in ("p90", "Observations")], heads


def test_the_subtitle_states_the_effective_time_rule_in_one_sentence():
    """#2256: the page states why a column's width is not its time per issue."""
    out = chart_js()
    assert "Each column's width is its time per issue divided by its parallelism, the mean number of issues " \
           "standing in it at once." in out


# ---------------------------------------------------------------- the trip table's order (#2280)
def test_the_trip_table_orders_the_stage_the_four_times_and_the_three_costs():
    """#2280: the desktop table and the phone table name one set of columns in one order, the time cells read the
    column's own stage, and no cell holds two facts."""
    out = chart_js()
    at = out.index("<table id=proftt")
    desk = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<th[^>]*>(.*?)</th>", out[at:out.index("</table>", at)], re.S)]
    phone_out = run_js(f"profTrip({json.dumps(CHART)},true)")
    pat = phone_out.index("<table id=proftt")
    phone = [re.sub(r"<[^>]+>", "", h)
             for h in re.findall(r"<th[^>]*>(.*?)</th>", phone_out[pat:phone_out.index("</table>", pat)], re.S)]
    assert desk == TRIP_HEADS and phone == TRIP_HEADS, (desk, phone)
    assert trip_row(out, "todo") == ["Todo", "1.0 min", "1.0 min", "1.0 min", "1.0 min", "×3.0", "20.0 s",
                                     "600", "50%", "40%"], \
        "the four time cells read the column's stage: three like deliveries give one value four times"
    assert "&middot;" not in "".join(trip_row(out, "doing")), "each cell holds one value, never two facts"


def test_a_column_with_no_observation_prints_a_dash_in_each_time_cell():
    """#2280: a column the window never observed has no reading, so each of its four time cells prints a dash, never a
    zero and never a blank."""
    out = chart_js()
    row = trip_row(out, "gating")
    assert row[:5] == ["Gating", "&#8212;", "&#8212;", "&#8212;", "&#8212;"], row
    assert row[5:7] == ["&#8212;", "&#8212;"], "the parallelism and the effective time read a dash too"
    assert row[8:] == ["&#8212;", "&#8212;"], "the two machine means read a dash too"


def test_the_bar_segment_hover_reads_the_median_the_p85_and_the_count():
    """#2280: the hover of a bar segment names the median, then the p85, then the observation count, on the one stage
    the column answers."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'trip': TRIP})},new Set())")
    titles = dict(re.findall(r'<rect class="ptb-seg ptc pts-(\w+)"[^>]*><title>([^<]*)</title></rect>', out))
    assert titles["doing"] == "Doing: median 4.2 min, p85 5.9 min (4 observations)", titles.get("doing")


def test_the_trip_fold_answers_the_min_median_p85_and_max_of_the_larger_stage(prof):
    """#2280: one stage answers the column, and all four readings come from that stage, so Min, Median, p85 and Max never
    mix the two stages a column holds. Doing's coding runs 100, 200 and 300 s; its between runs 60, 120 and 180 s."""
    rows = {r["column"]: r for r in prof.trip(TRIP_SPANS, [], since=NOW - 3600)["columns"]}
    doing = rows["doing"]
    assert (doing["stage"], doing["min_s"], doing["median_s"], doing["p85_s"], doing["max_s"]) == \
        ("coding", 100.0, 200.0, pytest.approx(270.0), 300.0), doing
    assert (doing["beside"]["stage"], doing["beside"]["min_s"], doing["beside"]["p85_s"]) == \
        ("between", 60.0, pytest.approx(162.0)), "the stage beside the column keeps its own four readings"
    gating = rows["gating"]
    assert (gating["observations"], gating["min_s"], gating["median_s"], gating["p85_s"], gating["max_s"]) == \
        (0, None, None, None, None), "no observation is no reading"


def test_the_trip_payload_carries_no_p90_and_no_observations_note(prof):
    """#2280: the trip answers p85 everywhere, so no field of the payload is named p90, and the note that explained the
    removed Observations header is gone."""
    t = prof.trip(TRIP_SPANS, [{"start": NOW - 500, "finish": NOW - 440}], since=NOW - 3600)

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    assert "p90_s" not in set(keys(t)), "the payload names no p90 field in any row"
    assert "observations_note" not in t


def test_one_sentence_under_the_table_states_the_peak_the_saturation_and_the_silent_minutes():
    """AC8 of the spec: the peak load, the saturation at the core count and the minutes with no load record, in one
    sentence. The sentence stands in the note under the table, so the chart opens as its mock opens (#2282)."""
    out = chart_js()
    sentence = "Peak load 4.00 against saturation at 8 cores; 21 minutes carried no load record."
    assert sentence in out
    assert out.index("</table><p class=ptfoot>") < out.index(sentence), "the machine facts stand in the note under the table"
    assert sentence not in re.search(r"<p class=tpn>(.*?)</p>", out, re.S).group(1), "the subtitle never restates them"


def test_the_page_states_that_cpu_and_ram_are_readings_of_the_whole_machine():
    """AC9 of the spec: the page says what the two Machine lines measure."""
    out = chart_js()
    assert "CPU and RAM are readings of the whole machine" in out


def test_a_line_with_no_data_draws_nothing_there_and_the_table_prints_a_dash():
    """AC10 of the spec: a column with no sample of a line holds no point, so nothing is drawn and the cell reads a
    dash."""
    out = chart_js()
    row = trip_row(out, "gating")
    assert row[8:10] == ["&#8212;", "&#8212;"], "no mean of CPU and RAM reads a dash, never a zero"
    svg = chart_svg(out)
    assert not re.search(r'ptgn pts-gating', svg), "a column of no width has no stretch to mark"


def test_the_ram_hover_names_the_value_in_gigabytes_once_on_the_legend():
    """AC11 of the spec: the machine's gigabytes show on the RAM legend entry in the Machine lane's gutter, never on a
    column's percent."""
    src = chart_js()
    assert re.search(r"<line class=\"ptkey ptl-ram\"[^>]*><title>the machine holds 32.0 GB of memory</title></line>",
                     chart_svg(src)), "the RAM legend entry's hover names the machine's memory"
    plain = re.sub(r"<title>[^<]*</title>", "", chart_svg(src))
    assert "GB" not in plain, "no line or value inside the lane claims gigabytes"


def test_the_chart_draws_the_ticks_and_the_points_the_payload_holds():
    """AC12 of the spec: the page draws what the payload holds; a point sits at its position in its band and its
    height on the payload's own scale."""
    gutter = chart_metrics()[1]
    span = 1000 - gutter
    out = chart_svg(chart_js())
    todo = CHART["columns"][0]
    x0, w = gutter, drawn_widths(CHART["columns"], out, span)[0]
    assert todo["tokens"]["points"] == [[0.5625, 480.0]]
    line = re.search(r'<polyline class="ptl-tok" points="([\d.]+),([\d.]+)', out)
    assert (float(line.group(1)), float(line.group(2))) == \
        (pytest.approx(x0 + 0.5625 * w, abs=0.1), pytest.approx(trip_consts()["top"] + trip_consts()["pad"]
                                                 + (trip_consts()["lane"] - 2 * trip_consts()["pad"]) * (1 - 480 / 1000), abs=0.1)), \
        "the first Tokens point sits at its position in Todo and its height on the payload's own scale"


def test_a_column_point_with_no_neighbour_draws_a_dot_instead_of_nothing():
    """A line of one point draws nothing as a polyline, so the page draws a dot there; adjacent columns draw a line."""
    v = json.loads(json.dumps(CHART))
    for c in v["columns"]:
        c["tokens"] = {"points": [], "mean": None}
    v["columns"][2]["tokens"] = {"points": [[0.5, 80.0]], "mean": 80.0}   # Review alone
    svg = chart_svg(chart_js(v))
    assert re.search(r'<circle class="ptl-tok" cx="[\d.]+" cy="[\d.]+" r=2.5>', svg), "an isolated point draws a dot"
    assert re.search(r'<polyline class="ptl-cpu" points="[^"]+ [^"]+"', svg), "the samples of adjacent columns draw a line"


def test_the_fold_sets_the_tokens_top_tick_above_every_point_it_emits():
    """The Tokens top tick is a round number strictly above every rate the columns hold, so no point the fold emits
    reaches the lane's edge or its clamp."""
    rates = [v for c in CHART["columns"] for _, v in c["tokens"]["points"]]
    assert rates and CHART["tokens_ticks"][2] > max(rates) and CHART["tokens_ticks"][:2] == [0, 500.0]
    over = {**CHART, "tokens_ticks": [0, 100.0, 200.0]}
    assert chart_svg(chart_js(over)).count('points="') >= 1, "a malformed top tick still draws the page's lanes"


def test_the_machine_lane_keeps_the_machines_scale_though_the_peak_stands_over_the_core_count():
    """The sentence names a peak load above the core count, and the Machine lane keeps the scale of its CPU and RAM
    readings, never one scaled to the peak (#2254)."""
    over = {**CHART, "peak_load": 32.0, "cores": 8}
    out = chart_js(over)
    assert "Peak load 32.00 against saturation at 8 cores" in out, "the sentence names the peak over the core count"
    ends = re.findall(r'<text class="ptv (?:ptvc|ptvr)"[^>]*>([^<]+)</text>', chart_svg(out))
    assert ends == ["CPU 50%", "RAM 40%", "CPU 50%", "RAM 40%"], \
        "the line ends keep the CPU and RAM readings, never the peak load"
    assert chart_svg(out) == chart_svg(chart_js()), "a peak above the core count moves neither scale nor point"


def test_a_wide_column_with_no_point_on_a_line_prints_a_dash_for_that_mean():
    """AC10 and AC2: a line with no point in a wide column reads a dash in its mean, never a value alone."""
    v = json.loads(json.dumps(CHART))
    v["columns"][0]["ram"] = {"points": [], "mean": None}
    means = re.findall(r'<text class="ptlm (ptlmt|ptlmc|ptlmr)"[^>]*>([^<]*)</text>', chart_svg(chart_js(v)))
    assert ("ptlmr", "RAM &#8212;") in means, "the RAM line draws nothing and its mean reads a dash"


def test_a_column_mean_divides_by_the_minutes_spent_in_that_column_alone(prof):
    """AC8 (#2253): Todo holds 60 minutes and Doing one. Each 600-token call reads its own column's mean, never a rate
    diluted by the other column's minutes."""
    out = prof.trip([span_at(3660.0)], [], since=CS,
                    cards=[card(("Todo", 0.0, 3600.0), ("Doing", 3600.0, 3660.0))],
                    calls=[[{"at": CS + 1800.0, "tokens": 600}, {"at": CS + 3630.0, "tokens": 600}]],
                    loads=[], now=CS + 3720.0)
    rows = {r["column"]: r for r in out["columns"]}
    assert rows["todo"]["tokens"]["mean"] == 10.0, "600 tokens over Todo's own 60 minutes"
    assert rows["doing"]["tokens"]["mean"] == 600.0, "600 tokens over Doing's own one minute, never over 61"


def test_every_column_counts_deliveries_so_the_header_carries_no_note(prof):
    """AC9 (#2253), #2292: Gating folds one reading per delivery, the sum of its wave's gates, so every row counts
    deliveries and the payload names no difference for the header."""
    gates = [wave_row(CS + 610, CS + 670), wave_row(CS + 700, CS + 760)]
    t = prof.trip([span_at(600.0)], gates, since=CS, cards=[card(("Doing", 0.0, 600.0))], calls=[[]], loads=[],
                  now=CS + 3600.0, waves=[WAVES[0]])
    rows = {r["column"]: r for r in t["columns"]}
    assert rows["doing"]["observations"] == 1 and rows["gating"]["observations"] == 1, \
        "Doing and Gating each count the one delivery"
    assert rows["gating"]["median_s"] == 120.0, "the delivery's two 60 s wave gates add up"
    assert "observations_note" not in t, "no column counts another thing, so the payload names no note for the header"


def test_a_load_reading_above_the_sane_bound_is_invalid_not_the_peak(prof):
    """AC10 (#2253): 214.02 on 8 cores is no saturation reading. The fold keeps the highest sane reading as the peak,
    reports the rest as invalid, and the page prints that."""
    loads = [{"at": CS + 60, "load": 9.5, "cpu": 50.0, "ram": 40.0},
             {"at": CS + 120, "load": 214.02, "cpu": 50.0, "ram": 40.0}]
    out = prof.trip([span_at(600.0)], [], since=CS, cards=[card(("Doing", 0.0, 600.0))], calls=[[]], loads=loads,
                    now=CS + 3600.0, cores=8)
    assert out["peak_load"] == 9.5, "the invalid reading is never the peak"
    assert (out["invalid_loads"], out["invalid_peak"], out["load_bound"]) == (1, 214.02, 80.0), \
        "the fold counts the reading over ten times the core count and names its bound"
    page = chart_js({**CHART, **{k: out[k] for k in ("peak_load", "invalid_loads", "invalid_peak", "load_bound")}})
    assert "1 reading above the sane bound 80 reported invalid" in page, "the page reports the reading as invalid"


def test_a_refused_profiler_poll_never_stats_the_minute_record(hub, monkeypatch):
    """The load is folded only for a caller the hub allows (#1768): a refused poll touches no record file, so the stat
    is the cost of a drawn chart and never of a request the hub already refused."""
    mod, _, _, _, _ = hub
    monkeypatch.setattr(mod, "load_record_stamp",
                        lambda: (_ for _ in ()).throw(AssertionError("a stat of the record")))
    out = mod.profiler_view(mod.Access(False, "not on the loopback and not allowed", "", True), 3600, 14400)
    assert out["strip"] is None and out["load"] is None, "a refused caller draws no strip and no load"


def test_an_allowed_poll_folds_the_load_because_the_api_carries_it_with_the_switch_off(hub, monkeypatch):
    """The route folds the minute record with the CPU sampling switch off, so its answer carries it though no view draws
    a load chart (#1768, #2228): the file view is not the switch's, as the strip is not."""
    mod, _, _, _, _ = hub
    out = mod.profiler_view(mod.Access(True, "", "", False), 3600, 14400)
    assert out["load"] is not None and out["load"]["start"] == out["strip"]["start"], "the chart's window is the strip's"


# ---------------------------------------------------------------- the run slots in use (#1766)
# The step line of the run slots in use, from zero to the run cap, folded from the run records. A
# run holds a slot from its start to its verdict; a verdict before a later start is history, so a resumed run is a second
# invocation (ADR-0167). The cap is a step too: each `run_start` records the caps in force at its own start, and the project
# file that may lower it is a mark at its write time. The fold is pure: fixture lists in, rows out, no clock and no file.
def inv(a, b=None):
    return {"start": a, "end": b}


def capmark(at, runs):
    return {"at": at, "runs": runs}


def steps_of(out):
    return [(r["slots"], r["start"], r["end"]) for r in out["rows"]]


def test_the_slots_fold_draws_a_step_line_from_zero_to_the_cap(prof):
    invs = [inv(W0 - 3 * H, W0 - 2 * H), inv(W0 - 2 * H + 600, W0 - 5400), inv(W0 - 1800)]
    out = prof.run_slots(invs, [capmark(W0 - 4 * H, 3)], 4 * H, W0)
    assert steps_of(out) == [
        (0, W0 - 4 * H, W0 - 3 * H),
        (1, W0 - 3 * H, W0 - 2 * H),
        (0, W0 - 2 * H, W0 - 2 * H + 600),
        (1, W0 - 2 * H + 600, W0 - 5400),
        (0, W0 - 5400, W0 - 1800),
        (1, W0 - 1800, W0)], "a run holds its slot from its start to its verdict; a run with no verdict holds it to now"
    assert out["window_s"] == 4 * H and out["start"] == W0 - 4 * H and out["end"] == W0
    assert out["peak"] == 1 and out["top"] == 3
    assert [c["cap"] for c in out["ceilings"]] == [3] and out["label"] == "Run slots in use"


def test_two_runs_at_once_count_two_slots_and_a_count_above_the_cap_stays_raw(prof):
    """The line shows the raw slots in use, so a run queued at the cap stays visible; the cap is its own step line."""
    invs = [inv(W0 - 3 * H, W0 - H) for _ in range(4)]
    assert [r["slots"] for r in prof.run_slots(invs, [capmark(W0 - 4 * H, 4)], 4 * H, W0)["rows"]] == [0, 4, 0]
    out = prof.run_slots(invs, [capmark(W0 - 4 * H, 2)], 4 * H, W0)
    assert [r["slots"] for r in out["rows"]] == [0, 4, 0], "a count above the cap is never lowered: the queued run shows"
    assert out["peak"] == 4 and out["top"] == 4, "the peak and the scale hold the raw count, never the lowered one"
    assert [c["cap"] for c in out["ceilings"]] == [2], "the cap is drawn apart, as the ceiling it is"


def test_a_cap_change_in_the_window_is_a_step_at_its_own_time(prof):
    out = prof.run_slots([inv(W0 - 3 * H, W0 - H)], [capmark(W0 - 4 * H, 6), capmark(W0 - 90 * 60, 2)], 4 * H, W0)
    assert [(c["cap"], c["start"], c["end"]) for c in out["ceilings"]] == [
        (6, W0 - 4 * H, W0 - 90 * 60), (2, W0 - 90 * 60, W0)], "the ceiling is a step at the time the cap changed"
    assert out["top"] == 6, "the line's scale holds the highest ceiling of the window"


def test_a_cap_mark_before_the_window_anchors_the_ceiling_at_its_start(prof):
    out = prof.run_slots([], [capmark(W0 - 8 * H, 4)], 4 * H, W0)
    assert out["ceilings"] == [{"start": W0 - 4 * H, "end": W0, "cap": 4}]
    assert steps_of(out) == [(0, W0 - 4 * H, W0)]


def test_a_window_with_no_run_record_is_a_gap_with_its_reason(prof):
    out = prof.run_slots(None, [capmark(W0 - 4 * H, 3)], 4 * H, W0)
    assert out["rows"] == [] and out["gaps"] == [{"start": W0 - 4 * H, "end": W0, "reason": "no run record"}]
    assert [c["cap"] for c in out["ceilings"]] == [3], "the ceiling is drawn even where no run record exists"


def test_a_record_that_cannot_be_read_gaps_only_its_own_stretch(prof):
    """A part of the window with no readable record is a gap: the stretch the unreadable run covers up to the next
    readable evidence, never the whole tail, so the readable run after it still draws its slots."""
    out = prof.run_slots([inv(W0 - H, W0)], [capmark(W0 - 4 * H, 3)], 4 * H, W0,
                         unreadable=[{"start": W0 - 3 * H, "reason": "run-1: record unreadable"}])
    assert [(g["start"], g["end"], g["reason"]) for g in out["gaps"]] == [
        (W0 - 3 * H, W0 - H, "run-1: record unreadable")]
    assert steps_of(out) == [(0, W0 - 4 * H, W0 - H), (1, W0 - H, W0)], "the records that did read still draw their slots"


def test_a_gap_runs_to_the_window_end_where_no_readable_record_covers_the_tail(prof):
    """A gap with no readable record after it runs to the window's end: nothing caps it short of the tail it hides."""
    out = prof.run_slots([inv(W0 - 4 * H, W0 - 3 * H)], [capmark(W0 - 4 * H, 3)], 4 * H, W0,
                         unreadable=[{"start": W0 - 2 * H, "reason": "run-2: record unreadable"}])
    assert [(g["start"], g["end"]) for g in out["gaps"]] == [(W0 - 2 * H, W0)]


def test_a_readable_run_across_a_gap_keeps_its_own_stretch_out_of_it(prof):
    """A readable run that covers the gap's start speaks for its own stretch: the gap is only where no readable record
    is, so the readable slots are never hidden under it."""
    out = prof.run_slots([inv(W0 - 4 * H, W0 - 2 * H), inv(W0 - H, W0)], [capmark(W0 - 4 * H, 3)], 4 * H, W0,
                         unreadable=[{"start": W0 - 3 * H, "reason": "run-1: record unreadable"}])
    assert [(g["start"], g["end"]) for g in out["gaps"]] == [(W0 - 2 * H, W0 - H)]
    assert steps_of(out) == [(1, W0 - 4 * H, W0 - 2 * H), (0, W0 - 2 * H, W0 - H), (1, W0 - H, W0)]


def test_an_unreadable_record_with_no_known_start_gaps_the_whole_window(prof):
    out = prof.run_slots([], [], 4 * H, W0, unreadable=[{"start": None, "reason": "run-1: record unreadable"}])
    assert out["gaps"] == [{"start": W0 - 4 * H, "end": W0, "reason": "run-1: record unreadable"}]


def test_cap_marks_and_slots_share_one_half_open_window(prof):
    """`[start, end)`: a mark at the window's own start anchors it; a mark at its end is outside the window, so the
    value in force before the end runs to the end."""
    out = prof.run_slots([], [capmark(W0 - 4 * H, 2), capmark(W0 - 2 * H, 5), capmark(W0, 9)], 4 * H, W0)
    assert [(c["cap"], c["start"], c["end"]) for c in out["ceilings"]] == [
        (2, W0 - 4 * H, W0 - 2 * H), (5, W0 - 2 * H, W0)]


def test_the_current_cap_heads_the_ceiling_until_a_mark_in_the_window_steps_it(prof):
    """With no mark before the window, the current cap heads it; each in-window mark steps the ceiling at its own time."""
    out = prof.run_slots([], [capmark(W0 - 2 * H, 2)], 4 * H, W0, cap=6)
    assert [(c["cap"], c["start"], c["end"]) for c in out["ceilings"]] == [
        (6, W0 - 4 * H, W0 - 2 * H), (2, W0 - 2 * H, W0)]
    only = prof.run_slots([], [], 4 * H, W0, cap=6)
    assert only["ceilings"] == [{"start": W0 - 4 * H, "end": W0, "cap": 6}], "with no mark at all it anchors the window"


def test_a_mid_window_cap_change_steps_at_its_own_mark(prof):
    out = prof.run_slots([], [capmark(W0 - 2 * H, 5)], 4 * H, W0, cap=3)
    assert [(c["cap"], c["start"], c["end"]) for c in out["ceilings"]] == [
        (3, W0 - 4 * H, W0 - 2 * H), (5, W0 - 2 * H, W0)]


def test_a_mark_in_the_window_with_no_current_cap_starts_its_row_at_the_mark(prof):
    out = prof.run_slots([], [capmark(W0 - 2 * H, 5)], 4 * H, W0)
    assert [(c["cap"], c["start"], c["end"]) for c in out["ceilings"]] == [(5, W0 - 2 * H, W0)], "no head is invented before it"


def test_a_resumed_run_is_two_invocations_so_the_verdict_before_the_resume_frees_the_slot(records):
    mod, runs = records
    d = runs / "run-20261115-020101-aaaaaa"
    record(d, ev("run_start", NOW - 3600, caps={"runs": 3}), ev("verdict", NOW - 3000, verdict="failed", exit_code=1),
           ev("resume", NOW - 1800), ev("verdict", NOW - 600, verdict="completed", exit_code=0))
    invs, caps = mod.run_invocations(d / "record.jsonl")
    assert [(x["start"], x["end"]) for x in invs] == [(NOW - 3600, NOW - 3000), (NOW - 1800, NOW - 600)]
    assert caps == [{"at": NOW - 3600, "runs": 3}], "the caps a run_start records mark the ceiling at that run's start"


def test_a_resume_closes_the_open_run_at_its_own_time(records):
    mod, runs = records
    d = runs / "run-20261115-020909-kkkkkk"
    record(d, ev("run_start", 100, caps={"runs": 2}), ev("resume", 200))
    invs, _ = mod.run_invocations(d / "record.jsonl")
    assert [(x["start"], x["end"]) for x in invs] == [(100, 200), (200, None)], "one open invocation, closed at the resume"


def test_the_last_verdict_before_the_next_start_ends_the_invocation(records):
    mod, runs = records
    d = runs / "run-20261115-021010-llllll"
    record(d, ev("run_start", 100), ev("verdict", 150, verdict="failed", exit_code=1),
           ev("verdict", 180, verdict="failed", exit_code=1), ev("resume", 300),
           ev("verdict", 400, verdict="completed", exit_code=0))
    invs, _ = mod.run_invocations(d / "record.jsonl")
    assert [(x["start"], x["end"]) for x in invs] == [(100, 180), (300, 400)], "the latest verdict before a start ends it"


def test_a_record_with_no_final_verdict_is_live_to_now(records):
    mod, runs = records
    d = runs / "run-20261115-020202-bbbbbb"
    record(d, ev("run_start", NOW - 600), ev("call_start", NOW - 60))
    assert [(x["start"], x["end"]) for x in mod.run_invocations(d / "record.jsonl")[0]] == [(NOW - 600, None)]


def test_slot_view_folds_the_runs_of_this_checkout(records):
    mod, runs = records
    record(runs / "run-20261115-020303-cccccc",
           ev("run_start", NOW - 1800, caps={"runs": 2}), ev("verdict", NOW - 900, verdict="completed", exit_code=0))
    out = mod.slot_view(14400, NOW)
    assert out["window_s"] == 14400 and steps_of(out)[-1][0] == 0
    assert any(r["slots"] == 1 for r in out["rows"]), "the run's own stretch holds one slot"
    assert [c["cap"] for c in out["ceilings"]] == [2], "the cap the run_start recorded is the ceiling"


def test_an_unrelated_project_settings_edit_adds_no_cap_step(records, tmp_path):
    """A cap change shows at the time a `run_start` recorded it. The project settings file's write time is no cap
    change: an edit to another key, with an older cap value in the file, draws no step."""
    mod, runs = records
    record(runs / "run-20261115-020606-hhhhhh",
           ev("run_start", NOW - 1800, caps={"runs": 2}), ev("verdict", NOW - 900, verdict="completed", exit_code=0))
    p = tmp_path / "docs" / "squad" / "defaults.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("caps:\n  runs: 6\n")
    os.utime(p, (NOW - 600, NOW - 600))
    out = mod.slot_view(14400, NOW)
    assert [c["cap"] for c in out["ceilings"]] == [2], "the recorded cap anchors; the file's mtime is no cap change"


def test_an_orphan_resume_still_records_a_slot(records):
    """A record truncated before its `run_start` names no cap, so the resume's own stretch is the only evidence: the
    slot shows from the resume, and the caller's current cap anchors the ceiling."""
    mod, runs = records
    d = runs / "run-20261115-020404-eeeeee"
    record(d, ev("resume", NOW - 1800), ev("call_start", NOW - 60))
    invs, caps = mod.run_invocations(d / "record.jsonl")
    assert [(x["start"], x["end"]) for x in invs] == [(NOW - 1800, None)], "the slot shows from the resume"
    assert caps == [], "a resume records no caps: no cap evidence"


def test_a_lost_record_is_a_gap_and_a_launching_run_is_not(records):
    """A folder past its launch window with no record is a lost record, a gap with its name; a folder still launching
    has no record yet, and no slot drawn is no gap."""
    mod, runs = records
    record(runs / "run-20261115-020505-aaaaaa",
           ev("run_start", NOW - 1800), ev("verdict", NOW - 900, verdict="completed", exit_code=0))
    lost = runs / "run-20261115-020606-ffffff"
    lost.mkdir(parents=True)
    os.utime(lost, (NOW - 86400, NOW - 86400))
    fresh = runs / "run-20261115-020707-gggggg"
    fresh.mkdir(parents=True)
    os.utime(fresh, (NOW - 60, NOW - 60))
    out = mod.slot_view(14400, NOW)
    reasons = [g["reason"] for g in out["gaps"]]
    assert any("ffffff" in r for r in reasons), "a lost record past its launch window is a gap with its name"
    assert not any("gggggg" in r for r in reasons), "a run still launching is never a gap"
    assert not any("aaaaaa" in r for r in reasons), "a readable record is no gap"


def test_the_route_answers_the_slots_on_the_strip_s_own_axis(hub):
    """One instant is one column: the strip and the slots are folded from one `now`, so they share their window's ends."""
    mod, port, _, _, table = hub
    now = time.time()
    d = pathlib.Path(mod.CHECKOUT) / "runs" / "run-20261115-030303-dddddd"
    try:
        record(d, ev("run_start", now - 1800), ev("call_start", now - 60))
        code, v = ask(port, "GET", path="/api/profiler?sw=14400")
        assert code == 200 and v["on"] is False
        slots, strip = v["slots"], v["strip"]
        assert slots["start"] == strip["start"] and slots["end"] == strip["end"], "a column is one instant in both"
        assert slots["window_s"] == strip["window_s"] == 14400
        assert [r["slots"] for r in slots["rows"]][-1] == 1, "the running run holds its slot to now"
        assert table.reads == 0, "the slots read run records only"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_route_answers_the_rule_layers_own_slots_object(hub):
    """AC4: the fold is pure in the rule layer and the route carries its rows. The slots object in the /api/profiler
    answer is `run_slots` over the same fixture, with its rows, ceilings, gaps, peak, top and cap."""
    mod, port, _, _, table = hub
    now = time.time()
    d = pathlib.Path(mod.CHECKOUT) / "runs" / "run-20261115-030505-dddddd"
    try:
        record(d, ev("run_start", now - 1800, caps={"runs": 3}), ev("verdict", now - 900, verdict="completed", exit_code=0))
        code, v = ask(port, "GET", path="/api/profiler?sw=14400")
        assert code == 200
        slots = v["slots"]
        assert {"rows", "ceilings", "gaps", "peak", "top", "cap"} <= set(slots), "the fold's own keys reach the page"
        invs, caps = [], []
        for run in sorted((pathlib.Path(mod.CHECKOUT) / "runs").glob("run-*")):
            got = mod.run_invocations(run / "record.jsonl")
            if got is not None:
                invs.extend(got[0]); caps.extend(got[1])
        expected = mod.hub_profiler.run_slots(invs, caps, 14400, slots["end"], cap=(mod.loop_caps() or {}).get("runs"))
        assert slots == expected, "the route answers the rule layer's fold, key for key"
        assert table.reads == 0, "the slots read run records only"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_window_with_no_readable_record_gaps_the_whole_window(records):
    """No readable record at all: the gaps still cover the window, even beside a lost folder that names its own stretch,
    so the chart never draws a stretch of zeros it cannot see."""
    mod, runs = records
    lost = runs / "run-20261115-020810-iiiiii"
    lost.mkdir(parents=True)
    os.utime(lost, (NOW - 1200, NOW - 1200))
    out = mod.slot_view(14400, NOW)
    assert out["rows"] == [], "no readable record draws no line of zeros"
    assert out["gaps"][0]["start"] == NOW - 14400 and out["gaps"][-1]["end"] == NOW
    at = NOW - 14400
    for g in out["gaps"]:
        assert g["start"] <= at, "the gaps leave no stretch of the window uncovered"
        at = max(at, g["end"])
    assert at == NOW


def test_the_launch_bound_is_the_janitors_own(served):
    """`NEVER_STARTED_S` is the janitor's rule-J bound: a run folder the chart calls lost is the one the janitor drops,
    so the two never drift apart."""
    from squad import caps
    assert served[0].NEVER_STARTED_S == caps.LAUNCH_GRACE


SLOTS = {"window_s": 14400, "start": W0 - 14400, "end": W0, "label": "Run slots in use", "cap": 3, "peak": 2, "top": 3,
         "rows": [{"start": W0 - 14400, "end": W0 - 5400, "slots": 0},
                  {"start": W0 - 5400, "end": W0 - 1800, "slots": 2},
                  {"start": W0 - 1800, "end": W0, "slots": 1}],
         "ceilings": [{"start": W0 - 14400, "end": W0 - 3600, "cap": 3}, {"start": W0 - 3600, "end": W0, "cap": 2}],
         "gaps": [{"start": W0 - 1800, "end": W0, "reason": "no run record"}]}


def test_the_run_slots_panel_is_gone_from_the_page():
    """The owner removed the run-slots panel: the tab reads the load line and the trip bar, and no slot chart stands above
    them. The slots payload may still reach the page; no view draws it."""
    out = run_js(f"profHtml({json.dumps({**VIEW, 'strip': STRIP, 'slots': SLOTS, 'trip': TRIP})},new Set())")
    assert "id=profslots" not in out, "the run-slots panel is still drawn"
    assert "Run slots in use" not in out and "at most" not in out, "a caption of the removed panel is still drawn"
    assert "id=proftrip" in out, "the trip bar went with the panel"


def test_the_cpu_sampling_switch_ends_the_page():
    """The switch that turns sampling on or off follows the readings it controls: it stands after the load line, the trip
    bar and the hot-spot table, never above them."""
    on = run_js(f"profHtml({json.dumps({**VIEW, 'slots': SLOTS, 'trip': TRIP})},new Set())")
    assert re.search(r'role=switch aria-label="CPU sampling"', on), "the switch left the page"
    assert on.index("id=proftrip") < on.index('aria-label="CPU sampling"'), "the switch stands above the trip bar"
    assert on.index("id=proft") < on.rindex('aria-label="CPU sampling"'), "the switch stands above the hot-spot table"
    assert re.search(r"<span class=lpl[^>]*>CPU sampling</span>", on), "the switch lost its label"


# ---------------------------------------------------------------- the wall time of the steps and the gates (#1748)

@pytest.fixture(scope="module")
def steprec():
    return load("steprec_under_test", SCRIPTS / "steprec.py")


WALL_NOW = 1_800_000_000


def step_lines(*steps):
    """(id, name, start, end-or-None, state, detail) -> the steps.tsv lines a loop writes for them."""
    out = []
    for sid, name, start, end, state, detail in steps:
        out.append(f"start\t{sid}\t{name}\t{start}\t-\t-")
        if end is not None:
            out.append(f"end\t{sid}\t{name}\t{end}\t{state}\t{detail}")
    return out


def test_the_wall_window_is_one_of_ten_minutes_one_hour_or_a_day_and_defaults_to_an_hour(prof):
    assert prof.wall_window(600) == 600 and prof.wall_window(86400) == 86400, "the three windows the page offers hold"
    assert prof.wall_window(None) == 3600, "a missing window reads the hour"
    assert prof.wall_window(999) == 3600, "a window the page does not offer reads the hour, never an arbitrary span"


def test_the_step_wall_lists_each_lane_sorted_by_total_with_count_median_and_p90(prof, steprec):
    lines = step_lines(
        ("a", "childcc", WALL_NOW - 500, WALL_NOW - 490, "done", "0"),   # 10 s, fast lane
        ("b", "childcc", WALL_NOW - 400, WALL_NOW - 370, "done", "0"),   # 30 s, fast lane
        ("c", "janitor", WALL_NOW - 300, WALL_NOW - 200, "done", "0"),   # 100 s, slow lane
        ("d", "promote", WALL_NOW - 100, WALL_NOW - 90, "done", "0"),    # 10 s, slow lane
        ("e", "promote", WALL_NOW - 80, WALL_NOW - 70, "done", "1"),     # 10 s, slow lane
    )
    out = prof.step_wall(steprec.fold(lines), WALL_NOW - 600, WALL_NOW)
    slow = {r["name"]: r for r in out["slow"]}
    assert [r["name"] for r in out["slow"]] == ["janitor", "promote"], "the slow lane sorts by total, the largest first"
    assert slow["janitor"] == {"lane": "slow", "name": "janitor", "count": 1, "total_s": 100.0, "median_s": 100.0, "p90_s": 100.0}
    assert slow["promote"]["count"] == 2 and slow["promote"]["total_s"] == 20.0, "two runs of one step total together"
    fast = out["fast"][0]
    assert fast == {"lane": "fast", "name": "childcc", "count": 2, "total_s": 40.0, "median_s": 20.0, "p90_s": 28.0}, \
        "the median and p90 are the linear interpolation over the two runs"


def test_the_step_wall_shows_a_running_step_with_its_age_and_leaves_a_skipped_step_out(prof, steprec):
    lines = step_lines(
        ("r", "janitor", WALL_NOW - 42, None, "", ""),                  # still running: no finish line
        ("s", "promote", WALL_NOW - 60, WALL_NOW - 59, "skipped", "a pass in flight holds its lock (exit 76)"),
    )
    out = prof.step_wall(steprec.fold(lines), WALL_NOW - 600, WALL_NOW)
    assert out["running"] == [{"lane": "slow", "name": "janitor", "age_s": 42.0}], "a running step names its age"
    assert out["slow"] == [] and out["fast"] == [], "a skipped step ran for no time and joins no row"


def test_the_cycle_time_is_the_gap_between_heartbeat_starts_of_the_slow_lane(prof, steprec):
    lines = step_lines(*[(f"h{t}", "heartbeat.sh", t, t + 1, "done", "0") for t in (1000, 1090, 1200)])
    out = prof.cycle_time(steprec.fold(lines), 0)
    assert out == {"n": 2, "median_s": 100.0, "p90_s": 108.0}, "two gaps of 90 s and 110 s give a median of 100 s and a p90 of 108 s"


def test_the_cycle_time_says_nothing_with_fewer_than_two_heartbeats(prof, steprec):
    out = prof.cycle_time(steprec.fold(step_lines(("h", "heartbeat.sh", 1000, 1001, "done", "0"))), 0)
    assert out == {"n": 0, "median_s": None, "p90_s": None}, "one start gives no gap, and no gap is no cycle time"


def test_the_gate_wall_groups_by_kind_and_outcome_with_the_mean_load_at_start_and_finish(prof):
    gates = [
        {"start": WALL_NOW - 30, "finish": WALL_NOW - 20, "kind": "gate", "outcome": "pass", "load_start": 1.0, "load_finish": 2.5},
        {"start": WALL_NOW - 60, "finish": WALL_NOW - 40, "kind": "gate", "outcome": "pass", "load_start": 3.0, "load_finish": None},
        {"start": WALL_NOW - 90, "finish": WALL_NOW - 80, "kind": "gate", "outcome": "fail", "load_start": None, "load_finish": None},
    ]
    out = prof.gate_wall(gates, WALL_NOW - 600)
    passed = next(r for r in out if r["outcome"] == "pass")
    assert passed == {"kind": "gate", "outcome": "pass", "count": 2, "total_s": 30.0, "median_s": 15.0, "p90_s": 19.0,
                      "load_start": 2.0, "load_finish": 2.5}, "the mean load ignores a row with no reading"
    failed = next(r for r in out if r["outcome"] == "fail")
    assert failed["load_start"] is None and failed["load_finish"] is None, "no reading at all is no mean, never a zero"


def test_the_gate_wall_drops_a_gate_that_finished_before_the_window(prof):
    gates = [{"start": WALL_NOW - 5000, "finish": WALL_NOW - 4990, "kind": "gate", "outcome": "pass", "load_start": 1, "load_finish": 1}]
    assert prof.gate_wall(gates, WALL_NOW - 600) == [], "a gate outside the window is no row"


def gate_line(finish, start, kind="gate", outcome="fail", load_start="1.5", load_finish="2.25"):
    """The sixteen fields gate.sh writes to the gate history, in the order hub_profiler.GATE_HISTORY_FIELDS names them."""
    return "\t".join([str(finish), kind, "fail", "abc123", str(start), "full", "full", outcome, "reason text", "3", "0", "2", "0",
                      "me", load_start, load_finish])


def test_the_gate_rows_read_a_history_line_and_skip_a_line_that_is_no_whole_row(prof):
    lines = [gate_line(WALL_NOW - 20, WALL_NOW - 30), "1\tgate", gate_line("x", WALL_NOW - 30),
             gate_line(WALL_NOW - 20, WALL_NOW - 30, outcome="bogus")]
    rows = prof.gate_rows(lines)
    assert rows == [{"start": WALL_NOW - 30.0, "finish": WALL_NOW - 20.0, "kind": "gate", "outcome": "fail",
                     "load_start": 1.5, "load_finish": 2.25}], "a short line, a finish that is no number and an unknown outcome are no row"


def gate_queue_line(finish, start, tree="t-a", wait="30", wall="60", cpu="12", reused="no", caller="child",
                    outcome="pass", reason="-", kind="fast", mode=None):
    """The twenty-two fields gate.sh writes to the gate history, in the order hub_profiler.GATE_HISTORY_FIELDS names them:
    the entry mode (#1798) sits before the queue fields, and a reused verdict is mode `reused`."""
    mode = mode or ("reused" if reused == "yes" else "gated")
    return "\t".join([str(finish), kind, "pass", "abc123", str(start), "delta", "root", outcome, reason, "pytest", "-", "1",
                      "-", caller, "1.0", "2.0", mode, tree, wait, wall, cpu, reused])


def test_the_gate_row_names_the_tree_the_wait_the_wall_the_cpu_and_the_reuse(prof):
    """(#1856) The fields after the load tell a gate stage's queue time from its work: the tree the gate read, the seconds
    it waited for the lock and its slots, its wall time, the CPU its command spent, and whether a green verdict answered
    instead of a battery (ADR-0168). A row from before them answers '' for each, never a reading it does not hold."""
    row = prof.gate_row(gate_queue_line(1000, 900))
    assert (row["tree"], row["wait"], row["wall"], row["cpu"], row["reused"]) == ("t-a", "30", "60", "12", "no")
    assert row["mode"] == "gated" and row["caller"] == "child", "the entry mode keeps its place before the queue fields"
    assert prof.GATE_HISTORY_FIELDS[-7:-2] == ("tree", "wait", "wall", "cpu", "reused"), \
        "the queue fields still end five before the row's last field, the CI run id (#1927)"
    old = prof.gate_row(gate_line(1000, 900))
    assert (old["tree"], old["wait"], old["wall"], old["cpu"], old["reused"]) == ("", "", "", "", ""), \
        "a row from before the fields answers '' for each, never a reading it does not hold"
    split = prof.gate_row(gate_queue_line(1000, 900, reason="red\tstands"))
    assert split["reason"] == "red\tstands" and split["tree"] == "t-a" and split["reused"] == "no" and split["mode"] == "gated", \
        "a reason that holds a tab is rejoined, so the columns after it keep their place"


def test_the_gate_queue_splits_a_gate_stage_into_lock_wait_and_work(prof):
    """(#1856) Over one window, by finish: the share of the gate stage that is lock wait and the number of distinct trees
    that paid a gate. The rows answer directly; nothing here is inferred from a row that carries no reading."""
    lines = [
        gate_queue_line(WALL_NOW - 20, WALL_NOW - 90, tree="t-a", wait="30", wall="60"),
        gate_queue_line(WALL_NOW - 30, WALL_NOW - 60, tree="t-b", wait="10", wall="20"),
        gate_queue_line(WALL_NOW - 40, WALL_NOW - 40, tree="t-a", wait="0", wall="0", reused="yes"),
        gate_line(WALL_NOW - 50, WALL_NOW - 60, outcome="fail"),            # a row from before the new fields
        gate_queue_line(WALL_NOW - 5000, WALL_NOW - 5060, tree="t-c"),      # finished before the window
        "not a gate history line",
    ]
    out = prof.gate_queue(lines, WALL_NOW - 600, WALL_NOW)
    assert out["rows"] == 4 and out["trees"] == 2, "two distinct trees paid a gate; the old row names no tree"
    assert out["trees_unnamed"] == 1 and out["wait_unknown"] == 1, "a row that carries no reading is counted, never guessed"
    assert (out["wait_s"], out["wall_s"], out["stage_s"]) == (40.0, 90.0, 130.0), \
        "the wait sums the rows that name one; an old row's wall is its own run time, which is what the field holds"
    assert out["wait_share"] == 0.3077, "40 s of wait inside a 130 s stage"
    assert out["reused"] == 1, "one gate's verdict was reused, so the saved gate is counted"


def test_the_gate_queue_names_no_share_when_no_gate_paid_the_window(prof):
    """A window no gate finished in answers no share, never a zero: an idle window and a queue of no seconds differ."""
    out = prof.gate_queue([gate_queue_line(WALL_NOW - 5000, WALL_NOW - 5060)], WALL_NOW - 600, WALL_NOW)
    assert out["rows"] == 0 and out["trees"] == 0 and out["wait_share"] is None


def test_the_wall_view_carries_the_gate_queue_of_its_window(prof):
    out = prof.wall([], [gate_queue_line(WALL_NOW - 20, WALL_NOW - 90, wait="30", wall="60")], 600, WALL_NOW)
    assert out["queue"]["wait_s"] == 30.0 and out["queue"]["trees"] == 1 and out["queue"]["wait_share"] == 0.3333


def test_the_wall_view_reads_the_state_folder_files_into_the_one_fold(served):
    mod, _, _, state = served
    state.mkdir(parents=True, exist_ok=True)
    (state / "steps.tsv").write_text("\n".join(step_lines(
        ("c", "janitor", int(NOW) - 100, int(NOW) - 70, "done", "0"), ("r", "promote", int(NOW) - 42, None, "", ""))) + "\n")
    (state / "gate-history").write_text(gate_line(NOW - 20, NOW - 30, outcome="pass") + "\n")
    out = mod.wall_view(600, NOW)
    assert [r["name"] for r in out["slow"]] == ["janitor"] and out["slow"][0]["total_s"] == 30.0
    assert out["running"] == [{"lane": "slow", "name": "promote", "age_s": 42.0}]
    assert out["gates"] == [{"kind": "gate", "outcome": "pass", "count": 1, "total_s": 10.0, "median_s": 10.0, "p90_s": 10.0,
                             "load_start": 1.5, "load_finish": 2.25}]


def test_the_wall_view_reads_only_the_tail_of_a_file_and_drops_the_cut_line(served, monkeypatch):
    mod, _, _, state = served
    state.mkdir(parents=True, exist_ok=True)
    lines = [gate_line(NOW - 100 * k, NOW - 100 * k - 10) for k in range(5, 0, -1)]   # oldest first
    (state / "gate-history").write_text("\n".join(lines) + "\n")
    width = len(lines[0]) + 1
    monkeypatch.setattr(mod, "WALL_TAIL_BYTES", 2 * width + 10)
    out = mod.wall_view(86400, NOW)
    assert sum(r["count"] for r in out["gates"]) == 2, "the two whole lines at the end are read, the cut line before them is dropped"


def test_the_wall_view_folds_the_step_records_and_the_gate_history_into_one_answer(prof, steprec):
    steps = steprec.fold(step_lines(("c", "janitor", WALL_NOW - 100, WALL_NOW - 70, "done", "0"),
                                    ("h1", "heartbeat.sh", WALL_NOW - 300, WALL_NOW - 299, "done", "0"),
                                    ("h2", "heartbeat.sh", WALL_NOW - 120, WALL_NOW - 119, "done", "0")))
    out = prof.wall(steps, [gate_line(WALL_NOW - 20, WALL_NOW - 30, outcome="pass")], 600, WALL_NOW)
    assert out["window_s"] == 600 and out["since"] == WALL_NOW - 600
    assert out["cycle"] == {"n": 1, "median_s": 180.0, "p90_s": 180.0}, "two heartbeats in the window are one gap of 180 s"
    assert [r["name"] for r in out["slow"]] == ["janitor", "heartbeat.sh"], "the slow lane sorts by total, the janitor first"
    assert out["gates"][0]["outcome"] == "pass" and out["gates"][0]["count"] == 1
    assert out["running"] == [], "no step is in flight"


WALL = {"window_s": 3600, "since": W0 - 3600, "label": "1 hour", "cycle": {"n": 4, "median_s": 90.0, "p90_s": 120.0},
        "slow": [{"lane": "slow", "name": "janitor", "count": 3, "total_s": 90.0, "median_s": 30.0, "p90_s": 40.0}],
        "fast": [{"lane": "fast", "name": "childcc", "count": 2, "total_s": 40.0, "median_s": 20.0, "p90_s": 28.0}],
        "running": [{"lane": "slow", "name": "promote", "age_s": 42.0}],
        "gates": [{"kind": "gate", "outcome": "pass", "count": 2, "total_s": 30.0, "median_s": 15.0, "p90_s": 19.0,
                   "load_start": 2.0, "load_finish": 2.5}]}
WALL_WINDOWS = [{"s": 600, "label": "10 minutes"}, {"s": 3600, "label": "1 hour"}, {"s": 86400, "label": "24 hours"}]


def test_the_wall_view_is_gone_from_the_page():
    """The owner removed the wall-time panel: it read as noise over the trip bar and the hot spots."""
    part = run_js(f"profHtml({json.dumps({**VIEW, 'wall': WALL, 'wall_windows': WALL_WINDOWS, 'trip': TRIP})},new Set())")
    assert "id=profwall" not in part, "the wall-time panel is not drawn"
    assert "Wall time of each step" not in part, "its title is gone with it"
    assert "data-wall" not in part, "no window control of it stays"
    assert "id=proftrip" in part, "the trip bar still stands"


def test_the_loop_tab_polls_without_the_wall_window():
    js = (SCRIPTS / "hub-profiler.js").read_text()
    assert "wallWin" not in js and "profWall" not in js, "the page names no wall window: the panel is gone"
    assert re.search(r'fetch\("/api/profiler\?w="\+profWin\)', js), "the poll carries the profiler's window alone"


def test_the_gate_row_names_the_runner_and_the_ci_run_id(prof):
    """(#1927) A gate row carries the proof that produced it and the CI run a `ci` proof names. A row from before the two
    fields answers '' for each, never a runner it does not hold."""
    row = prof.gate_row(gate_queue_line(1000, 900) + "\tci\t4242")
    assert (row["runner"], row["run_id"]) == ("ci", "4242")
    assert row["tree"] == "t-a" and row["reused"] == "no", "the fields before the runner keep their place"
    assert prof.GATE_HISTORY_FIELDS[-2:] == ("runner", "run_id")
    old = prof.gate_row(gate_queue_line(1000, 900))
    assert (old["runner"], old["run_id"]) == ("", ""), "a row from before the fields answers '' for each"
    split = prof.gate_row(gate_queue_line(1000, 900, reason="red\tstands") + "\tlocal\t-")
    assert (split["reason"], split["runner"], split["run_id"]) == ("red\tstands", "local", "-"), \
        "a reason that holds a tab is rejoined, so the runner fields keep their place"


def test_each_gate_history_reader_takes_the_runner_fields_from_the_same_end(prof):
    """(#1927) One grammar, three readers: the hub's rule layer, throughput.py and tune_report.py read a row's entry mode
    from its end. A row that carries the runner fields must keep its mode in all three, or a `skipped` gate would count as
    a gate run in the throughput report."""
    row = gate_queue_line(1000, 900) + "\tci\t4242"
    assert prof.gate_row(row)["mode"] == "gated"
    for name in ("throughput.py", "tune_report.py"):
        mod = load(f"{name[:-3]}_under_test", SCRIPTS / name)
        assert mod.gate_mode(row.split("\t")) == "gated", f"{name} loses the entry mode after the runner fields"
        assert mod.gate_mode(gate_queue_line(1000, 900).split("\t")) == "gated", f"{name} loses it on a row with no runner"


# ---------------------------------------------------------------- the trip chart follows the mock of #2222 (#2255)
# The owner chose the mock's layout on 2026-11-18. The tests below pin the layout alone: the order of the blocks, the
# gutter that names each lane, the three short ticks a lane, the band tint and its rules, the mean of each column inside
# its lane, the thin bar under the Machine lane, the labels in the column's colour, the lines' colours and the table.
def trip_consts():
    """The layout constants the page declares, read from the source so a test measures the page's own frame."""
    src = (SCRIPTS / "hub-profiler.js").read_text()

    def num(name):
        return float(re.search(name + r"=([\d.]+)", src).group(1))
    return {"gutter": num("PROF_TRIP_GUTTER"), "lane": num("PROF_TRIP_LANE"), "top": num("PROF_TRIP_TOP"),
            "gap": num("PROF_TRIP_GAP"), "row": num("PROF_TRIP_ROW"), "pad": num("PROF_TRIP_PAD")}


def test_the_blocks_follow_the_mock_from_the_heading_to_the_table():
    """AC1 (#2255): heading, subtitle, Tokens lane, Machine lane, trip bar, labels, table. One subtitle carries every
    sentence the page states above the chart, and the label row stands below the bar's foot, never over it."""
    c = trip_consts()
    out = chart_js()
    order = [out.index("<h3>"), out.index("<p class=tpn>"), out.index("<svg id=proftc"), out.index("<table id=proftt")]
    assert order == sorted(order), "heading, subtitle, the chart, the table"
    assert out.count("<p class=tpn>") == 1, "one subtitle, never four sentences over three places"
    svg = chart_svg(out)
    assert svg.index(">Tokens<") < svg.index(">Machine<") < svg.index('class="ptb-seg') < svg.index('class="ptlb'), \
        "the lanes come first, then the bar, then the labels"
    bar = re.search(r'<rect class=ptbbar x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"', svg)
    bar_bottom = float(bar.group(1)) + float(bar.group(2))
    label_ys = [float(y) for y in re.findall(r'<text class="ptl[bs] pts-\w+"[^>]*y="([\d.]+)"', svg)]
    assert label_ys and min(label_ys) == pytest.approx(bar_bottom + c["row"]), \
        "the first label row opens one row under the bar's foot"


def test_each_lane_names_its_unit_and_legend_in_the_left_gutter():
    """AC2 (#2255): each lane's title, unit and legend stand in the gutter at the left, and its axis values stand at the
    left of the plot, outside it."""
    c = trip_consts()
    svg = chart_svg(chart_js())
    for text in ("Tokens", "k tokens / min", "Machine", "min-max per line", "CPU", "RAM"):
        m = re.search(r'<text class=(?:ptn|ptu)[^>]*x="(\d+)"[^>]*>' + re.escape(text) + "</text>", svg)
        assert m and float(m.group(1)) < c["gutter"], f"{text} stands in the left gutter"
    assert re.search(r'<line class="ptkey ptl-cpu"[^>]*></line>', svg), "the CPU legend keeps the CPU line's colour"
    assert re.search(r'<line class="ptkey ptl-ram"[^>]*>', svg), "the RAM legend keeps the RAM line's colour, dashed"
    ticks = re.findall(r'<text class=ptt x="([\d.]+)" [^>]*text-anchor=end>([^<]+)</text>', svg)
    assert len(ticks) == 3 and all(float(x) < c["gutter"] for x, _ in ticks), \
        "the Tokens lane's three axis values stand in the gutter, left of the plot"
    ends = re.findall(r'<text class="ptv (?:ptvc|ptvr)" x="([\d.]+)"', svg)
    assert len(ends) == 4 and all(float(x) < c["gutter"] for x in ends), \
        "the Machine lane's four end values stand in the gutter, left of the plot"


def test_each_lane_prints_at_most_three_short_ticks_and_a_gap_separates_the_lanes():
    """AC3 (#2255) and AC3 (#2254): the Tokens lane holds three short ticks, the Machine lane states four short end
    values, and a gap stands between the two lanes. The Tokens grid ends at its own lane's bottom."""
    c = trip_consts()
    assert c["gap"] > 0, "a gap separates the Tokens lane from the Machine lane"
    svg = chart_svg(chart_js())
    ticks = re.findall(r"<text class=ptt[^>]*>([^<]+)</text>", svg)
    assert [t for t in ticks if t.endswith("k")] == ["0k", "0.5k", "1k"], "three short Tokens ticks"
    ends = re.findall(r'<text class="ptv (?:ptvc|ptvr)"[^>]*>([^<]+)</text>', svg)
    assert len(ends) == 4 and all(len(t) <= 9 for t in ticks + ends), \
        "no axis value prints a long text: the Tokens tick stays short and an end value names one series"
    ys = [float(y) for y in re.findall(r'<line class=ptg0 x1="[\d.]+" y1="([\d.]+)"', svg)]
    assert len(ys) == 3 and max(ys) <= c["top"] + c["lane"], "the Tokens grid ends at its own lane's bottom"
    assert "dasharray" in re.search(r"#proftrip \.ptg0\{([^}]*)\}", chart_css()).group(1), "the grid is dashed"


def test_a_sub_thousand_tokens_axis_keeps_three_distinct_short_ticks():
    """AC3 (#2255): a Tokens lane whose top tick is below a thousand keeps three ticks that read apart, each a short
    value, never three labels that round to the same text."""
    v = json.loads(json.dumps(CHART))
    v["tokens_ticks"] = [0, 20, 40]
    ticks = re.findall(r"<text class=ptt[^>]*>([^<]+)</text>", chart_svg(chart_js(v)))[:3]
    assert len(ticks) == 3 and len(set(ticks)) == 3, f"three distinct Tokens ticks, got {ticks}"
    assert all(len(t) <= 6 for t in ticks), f"each tick stays short, got {ticks}"


def test_the_subtitle_joins_its_sentences_with_single_spaces():
    """AC1 (#2255): one subtitle joins its sentences with one space each, and it never restates a layout rule the
    caption already carries."""
    v = json.loads(json.dumps(CHART))
    v.update(invalid_loads=2, ram_minutes=0)
    sub = re.search(r"<p class=tpn>(.*?)</p>", chart_js(v), re.S).group(1)
    assert "  " not in sub, "one space joins two sentences"
    assert sub == sub.strip(), "the subtitle opens and ends on a word"
    assert "Width follows" not in sub, "the caption carries no second copy of the layout rule"


def test_no_two_means_share_a_box_in_a_lane():
    """AC5/AC7 (#2255): a run of narrow columns pushes many mean texts into one lane, and every text still keeps its
    own box, so one mean never prints over another."""
    char = chart_metrics()[0]
    v = json.loads(json.dumps(CHART))
    base = CHART["columns"]
    v["columns"] = []
    for i, share in enumerate([0.8] + [0.02] * 5):
        col = json.loads(json.dumps(base[i % len(base)]))
        col.update(column=f"c{i}", label=f"Col{i}", share=share)
        v["columns"].append(col)
    means = [(line, float(x), float(y), text) for line, x, y, text in re.findall(
        r'<text class="ptlm (ptlmt|ptlmc|ptlmr)" x="([\d.]+)" y="([\d.]+)" text-anchor=end font-size=11>([^<]*)</text>',
        chart_svg(chart_js(v)))]
    assert len(means) == 18, "six columns, three lines"
    boxes = [(line, y, x - len(re.sub(r'&#?\w+;', '~', text)) * char * 11 / 13, x, text) for line, x, y, text in means]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not (abs(a[1] - b[1]) < 13 and a[2] < b[3] and b[2] < a[3]), \
                f"{a[4]!r} and {b[4]!r} share a box in the {a[0]} lane"


def test_each_band_is_tinted_and_a_vertical_rule_separates_two_columns():
    """AC4 (#2255): each column band is clearly tinted, and a vertical rule between two columns runs through both lanes."""
    c = trip_consts()
    svg = chart_svg(chart_js())
    tint = re.search(r"#proftrip \.ptc\{[^}]*opacity:\.(\d+)", chart_css())
    assert tint and int(tint.group(1)) >= 25, "the band's tint tells two columns apart"
    rules = [(float(x), float(y0), float(y1)) for x, y0, y1 in
             re.findall(r'<line class=ptr x1="([\d.]+)" y1="([\d.]+)" x2="[\d.]+" y2="([\d.]+)"', svg)]
    ends = band_starts(CHART["columns"], svg, c["gutter"])[1:]
    assert [r[0] for r in rules] == pytest.approx(ends[:len(rules)], abs=0.2), \
        "a rule stands at each seam between two drawn columns, never before the first"
    assert [r[1] for r in rules] == [c["top"]] * len(rules) and \
        [r[2] for r in rules] == [c["top"] + 2 * c["lane"] + c["gap"]] * len(rules), "the rule crosses both lanes"


def test_a_zero_width_column_keeps_the_rule_between_the_columns_around_it():
    """AC4 (#2255): a board column that drew no delivery paints no band, and the rule still stands at the seam
    between the two drawn columns on either side of it."""
    c = trip_consts()
    v = json.loads(json.dumps(CHART))
    v["columns"][1]["share"] = 0.0
    span = 1000 - c["gutter"]
    svg = chart_svg(chart_js(v))
    rules = [float(x) for x in re.findall(r'<line class=ptr x1="([\d.]+)"', svg)]
    starts = [x for x, col in zip(band_starts(v["columns"], svg, c["gutter"]), v["columns"], strict=True) if span * col["share"] > 0]
    assert rules == pytest.approx(starts[1:], abs=0.2), "a rule stands at each seam between two drawn columns"


def test_each_column_mean_prints_in_its_lane_top_right_in_its_lines_colour():
    """AC5 (#2255): each column's mean prints inside its lane, at the top right of the column, in the colour of its
    line: white for Tokens, pink for CPU and lilac for RAM."""
    c = trip_consts()
    mac_top = c["top"] + c["lane"] + c["gap"]
    svg = chart_svg(chart_js())
    css = chart_css()
    assert re.search(r"#proftrip \.ptlmt\{[^}]*fill:var\(--fg\)", css), "the token mean takes the line's white"
    assert re.search(r"#proftrip \.ptlmc\{[^}]*fill:var\(--s7\)", css), "the CPU mean takes the line's pink"
    assert re.search(r"#proftrip \.ptlmr\{[^}]*fill:var\(--none\)", css), "the RAM mean takes the line's lilac"
    means = [(line, float(x), float(y), text) for line, x, y, text in re.findall(
        r'<text class="ptlm (ptlmt|ptlmc|ptlmr)" x="([\d.]+)" y="([\d.]+)" text-anchor=end font-size=11>([^<]*)</text>', svg)]
    assert len(means) == 15, "five columns, three lines"
    tok = [m for m in means if m[0] == "ptlmt"]
    assert all(c["top"] < m[2] < c["top"] + c["lane"] for m in tok), "the token means stand in the Tokens lane"
    assert all(mac_top < m[2] < mac_top + c["lane"] for m in means if m[0] != "ptlmt"), \
        "the CPU and RAM means stand in the Machine lane"
    span = 1000 - c["gutter"]
    first_end = c["gutter"] + drawn_widths(CHART["columns"], svg, span)[0]
    assert tok[0][1] == pytest.approx(first_end - 5, abs=0.2), "the mean sits at the column's right edge"
    assert tok[0][2] - c["top"] < c["lane"] / 3, "the mean sits at the top of the lane"
    assert [m[3] for m in means[:3]] == ["mean 60", "CPU 50%", "RAM 40%"], "the full copy at a wide column"


def test_the_trip_bar_is_thin_and_sits_under_the_machine_lane_across_the_plot():
    """AC6 (#2255): the bar is thin, sits under the Machine lane, spans the plot, and each segment sits under its band."""
    c = trip_consts()
    svg = chart_svg(chart_js())
    span = 1000 - c["gutter"]
    bar = re.search(r'<rect class=ptbbar x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)
    assert bar, "the bar is drawn in the chart"
    x, y, w, h = (float(v) for v in bar.groups())
    assert (x, w) == (pytest.approx(c["gutter"]), pytest.approx(span)), "the bar has the plot's width, never the page's"
    assert h <= 10, "the bar is thin"
    assert y >= c["top"] + 2 * c["lane"] + c["gap"], "the bar sits under the Machine lane"
    segs = [(col, float(sx), float(sw)) for col, sx, sw in re.findall(
        r'<rect class="ptb-seg[^"]*pts-(\w+)" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg)]
    bands = {col: (float(bx), float(bw)) for col, bx, bw in
             re.findall(r'<rect class="ptc pts-(\w+)" x="([\d.]+)" width="([\d.]+)"', svg)}
    assert segs and all((sx, sw) == pytest.approx(bands[col], abs=0.2) for col, sx, sw in segs), \
        "each segment sits under its band, at the band's own width"
    assert len(re.findall(r'<rect class="ptb-seg', svg)) == len(bands), "one segment a column"


def test_each_label_takes_its_columns_colour_with_a_bold_name_and_a_small_median():
    """AC7 (#2255): a label takes the column's colour, prints the name in bold with the median small under it, and no
    two labels overlap; a narrow column takes a leader line."""
    svg = chart_svg(chart_js())
    css = chart_css()
    for col, var in (("todo", "--s5"), ("doing", "--run"), ("review", "--acc"), ("landing", "--ok"),
                     ("gating", "--warn")):
        assert re.search(rf"#proftrip text\.ptlb\.pts-{col}\{{[^}}]*fill:var\({var}\)", css), \
            f"the {col} name takes the column's colour"
        assert f"text.ptls.pts-{col}" not in css, "the time under a name is grey, as the mock draws it (#2282)"
    assert re.search(r"#proftrip \.ptls\{fill:var\(--mute\)\}", css), "the time under a name is grey"
    names = re.findall(r'<text class="ptlb pts-(\w+)" x="[\d.]+" y="[\d.]+" text-anchor=\w+ font-weight=600>([^<]*)</text>',
                       svg)
    assert names[:2] == [("todo", "Todo"), ("doing", "Doing")], "the name stands alone, in bold"
    meds = re.findall(r'<text class="ptls pts-(\w+)"[^>]*font-size=(\d+)>([^<]*)</text>', svg)
    assert meds[0] == ("todo", "10", "20.0 s · ×3.0"), \
        "the effective time and the parallelism stand small under the name (#2256)"
    assert "20.0 s" not in names[0][1], "the time is never part of the name"
    assert re.findall(r'<line class="ptg pts-(\w+)"', svg) == [], \
        "a column as wide as its label takes no leader line, and a column of no width gets none (#2278)"


def test_tokens_is_white_cpu_is_pink_and_solid_and_ram_is_lilac_and_dashed():
    """AC8 (#2255): the three lines keep the palette roles of the page."""
    css = chart_css()
    tok = re.search(r"#proftrip \.ptl-tok\{([^}]*)\}", css).group(1)
    cpu = re.search(r"#proftrip \.ptl-cpu\{([^}]*)\}", css).group(1)
    ram = re.search(r"#proftrip \.ptl-ram\{([^}]*)\}", css).group(1)
    assert "stroke:var(--fg)" in tok and "dasharray" not in tok, "Tokens is white and solid"
    assert "stroke:var(--s7)" in cpu and "dasharray" not in cpu, "CPU is pink and solid"
    assert "stroke:var(--none)" in ram and "dasharray" in ram, "RAM is lilac and dashed"


def test_the_table_keeps_each_header_on_one_line_and_short_counts():
    """AC9 (#2255): the headers stay on one line, each cell holds one value, and a count prints in short units."""
    css = chart_css()
    assert re.search(r"#proftt th\{[^}]*white-space:nowrap", css), "no header breaks over two lines"
    out = chart_js()
    at = out.index("<table id=proftt")
    heads = re.findall(r"<th[^>]*>(.*?)</th>", out[at:out.index("</table>", at)], re.S)
    assert [re.sub(r"<[^>]+>", "", h) for h in heads] == TRIP_HEADS
    assert trip_row(out, "doing")[7] == "2.4k", "a count prints in short units"
    big = json.loads(json.dumps(CHART))
    big["columns"][1]["tokens_per_issue"] = 834000.0
    assert trip_row(chart_js(big), "doing")[7] == "834k", "834000 prints as 834k"
    for col in ("todo", "doing", "review", "landing", "gating"):
        assert len(trip_row(out, col)) == 10, f"one value a cell in the {col} row"


# ---------------------------------------------------------------- the least width of a column (#2278)
def short_trip():
    """A trip like the live one: Doing holds most of the bar, and Review and Landing hold almost none of it."""
    v = json.loads(json.dumps(CHART))
    for col, share in zip(v["columns"], (0.30, 0.62, 0.015, 0.001, 0.064), strict=True):
        col["share"] = share
        col["median_s"] = col["median_s"] or 2.0
        col["effective_s"] = col.get("effective_s") or 2.0
        col["parallelism"] = col.get("parallelism") or 1.0
    return v


def overlaps(boxes):
    return [(a["text"], b["text"]) for i, a in enumerate(boxes) for b in boxes[i + 1:]
            if abs(a["y"] - b["y"]) < 13 and a["x0"] < b["x1"] - 1 and b["x0"] < a["x1"] - 1]


def test_a_short_column_is_never_narrower_than_its_label():
    """A column of a tenth of a percent of the trip still draws a band and a segment as wide as its label, its label
    stands under its own segment, and the widths still add up to the plot."""
    out = chart_js(short_trip())
    svg = chart_svg(out)
    floors = label_floors(svg)
    segs = {c: float(w) for c, w in re.findall(r'<rect class="ptb-seg ptc pts-(\w+)" x="[\d.]+" y="[\d.]+" width="([\d.]+)"', svg)}
    assert set(segs) == {"todo", "doing", "review", "landing", "gating"}
    for col, w in segs.items():
        assert w >= floors[col] - 0.1, f"{col} is narrower than its label: {w} under {floors[col]}"
    assert sum(segs.values()) == pytest.approx(1000 - chart_metrics()[1], abs=0.5), "the segments divide the whole plot"
    assert segs["doing"] > segs["todo"] > segs["gating"], "the columns above the floor keep the order of their shares"
    assert not re.findall(r'<line class="ptg ', svg), "no label leaves its column, so none takes a leader line"
    assert set(re.findall(r'<text class="ptlb pts-\w+"[^>]*text-anchor=(\w+)', svg)) == {"middle"}, \
        "each label stands centred under its own segment"
    assert not overlaps(text_boxes(out)), "no label and no number shares a box with another"


def test_the_labels_on_leader_lines_never_stand_over_each_other():
    """A label is two lines. When the labels pass the plot, each leader row keeps the height of both lines."""
    v = short_trip()
    for col in v["columns"]:
        col["label"] = col["label"] + " " + "x" * 40
    out = chart_js(v)
    assert len(re.findall(r'<line class="ptg ', chart_svg(out))) >= 4, "the labels stand on leader rows"
    labels = [b for b in text_boxes(out) if b["cls"].startswith(("ptlb", "ptls"))]
    ys = sorted({b["y"] for b in labels})
    assert len(ys) == 10 and min(b - a for a, b in zip(ys, ys[1:], strict=False)) >= 13, \
        "ten lines of text, five names and five times, each on a row of its own"


def test_the_phone_keeps_a_short_column_visible():
    """The phone's lanes and strip hold the same floor: a short column keeps a band of at least 40 of 1000 units."""
    parts = json.loads(run_js(f"JSON.stringify(profTripShares({json.dumps(short_trip()['columns'])}).map(p=>p.w))"))
    assert min(parts) >= 40 - 0.01 and sum(parts) == pytest.approx(1000, abs=0.5)


# ---------------------------------------------------------------- the finish of the mock (#2282)
def test_every_colour_the_chart_names_is_a_colour_the_page_defines():
    """A fill that names a variable the page never defines paints black: the lane titles were invisible that way."""
    defined = set(re.findall(r"(--[\w-]+):", (SCRIPTS / "hub.py").read_text() + (SCRIPTS / "web-tokens.css").read_text()))
    used = set(re.findall(r"var\((--[\w-]+)\)", chart_css()))
    assert used - defined == set(), f"the chart names colours the page does not define: {sorted(used - defined)}"


def test_a_dark_gap_parts_the_two_lanes():
    c = trip_consts()
    gap = re.search(r'<rect class=ptgap x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)">', chart_svg(chart_js()))
    assert gap, "one rect covers the bands between the lanes"
    assert [float(v) for v in gap.groups()] == [c["gutter"], c["top"] + c["lane"], 1000 - c["gutter"], c["gap"]]
    assert re.search(r"#proftrip \.ptgap\{fill:var\(--bg\)\}", chart_css()), "the gap takes the page's background"


def test_the_lines_are_two_units_thick_and_a_dark_rule_parts_two_columns():
    css = chart_css()
    for cls in ("ptl-tok", "ptl-cpu", "ptl-ram"):
        assert re.search(rf"#proftrip \.{cls}\{{[^}}]*stroke-width:2;", css), f"{cls} is two units thick"
    assert re.search(r"#proftrip \.ptr\{stroke:var\(--bg\);stroke-width:2;opacity:1;", css), "the rule between two columns is dark"


def test_a_mean_in_a_lane_is_smaller_than_a_label():
    svg = chart_svg(chart_js())
    sizes = set(re.findall(r'<text class="ptlm [^"]*"[^>]*font-size=(\d+)>', svg))
    assert sizes == {"11"} and 11 < chart_metrics()[2], "each mean prints at 11 units, under the 13 of a label"


def test_each_lane_keeps_a_margin_inside_its_band_and_its_values_stand_off_the_plot():
    """#2282: the mock draws each lane's scale inside its band by a margin, its values a gap left of the plot, and the
    lane's title on the row of its top value."""
    c = trip_consts()
    src = (SCRIPTS / "hub-profiler.js").read_text()
    gap = float(re.search(r"PROF_TRIP_AXIS_GAP=(\d+)", src).group(1))
    svg = chart_svg(chart_js())
    lines = [float(y) for y in re.findall(r'<line class=ptg0 x1="[\d.]+" y1="([\d.]+)"', svg)]
    assert min(lines) - c["top"] >= c["pad"] >= 10, "the top value line keeps air above it"
    assert c["top"] + c["lane"] - max(lines[:3]) >= c["pad"], "the zero line keeps air below it"
    xs = {float(x) for x in re.findall(r'<text class=ptt x="([\d.]+)"', svg)}
    assert xs == {c["gutter"] - gap} and gap >= 10, "every axis value stands one gap left of the plot"
    title = re.search(r'<text class=ptn x="0" font-size=(\d+) y="([\d.]+)">Tokens</text>', svg)
    assert title and int(title.group(1)) > trip_consts_font(src), "the lane title is larger than the chart text"
    assert abs(float(title.group(2)) - (min(lines) + 4)) <= 2, "the title stands on the row of the top value"


def trip_consts_font(src):
    return int(re.search(r"PROF_TRIP_FONT=(\d+)", src).group(1))
