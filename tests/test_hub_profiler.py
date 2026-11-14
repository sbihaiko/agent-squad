"""The hub's Loop tab (#1743): the loop of this profile as a hot-spot tree, with its own switch, `CPU sampling`.

The fold from process-table readings to the tree is a pure module beside the hub (ADR-0137), so its tests feed readings in
and read no real process table. The route is tested over HTTP: off is the default and reads nothing, a switch write is
audited, and a tailnet write is refused without its own grant, `profiler_write`. The page's renderer runs in node.
"""
import http.client
import importlib.util
import json
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
    assert set(tree) == {"name", "kind", "self_s", "total_s", "share", "events", "total_events", "children"}


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


def ask(port, method, body=None, *, origin=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json",
               "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    c.request(method, "/api/profiler", body=None if body is None else json.dumps(body), headers=headers)
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
    monkeypatch.setattr(mod, "process_table", lambda: reads.append(1) or {})   # what the hub's reader returns when the read fails
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
    """Stub every read `boot` starts but the sampler: the names are read from `boot` itself, so a new one is stubbed too."""
    src = HUB_PY.read_text()
    body = src[src.index("\ndef boot():"):src.index('\nif __name__ == "__main__":')]
    reads = re.findall(r"target=(\w+)", body)
    assert "refresh_board" in reads
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
    main = HUB_PY.read_text().split('\nif __name__ == "__main__":\n', 1)[1]
    assert re.search(r"^    boot\(\)", main, re.M) and "profiler_ensure" not in main, "the hub's start does not go through boot"


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
    assert re.search(r'fetch\("/api/profiler"\)', src)
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
    assert [h for h in re.findall(r"<th[^>]*>([^<]+)</th>", out)] == ["Hot spot", "Self time", "Total time", "Starts"]
    rows = re.findall(r"<tr data-p=\"([^\"]*)\"", out)
    assert rows == ["loop", "loop/watchd.sh", "loop/orphan watch.sh (temp folder)"], "a closed node shows no child"
    assert re.search(r'data-p="loop/watchd\.sh".*?<i class=pbar style="width:75\.0%">', out, re.S)
    row = re.search(r'<tr data-p="loop/watchd\.sh".*?</tr>', out, re.S).group(0)
    assert re.findall(r"<td>([^<]*)</td>", row) == ["0 ms", "6.00 s", "0"], "self time, total time and starts"


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
    assert re.search(r'aria-checked="false"', off) and "<table" not in off and "CPU sampling is off" in off
    ro = run_js(f"profHtml({json.dumps({**VIEW, 'write': False, 'reason': 'read-only from the tailnet'})},new Set())")
    assert re.search(r'data-prof disabled[^>]*title="read-only from the tailnet"', ro)


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
    yield mod, srv.server_address[1], table
    mod.profiler_set(False, "test", "loopback")
    wait(lambda: not mod.profiler_running())
    srv.shutdown()
    srv.server_close()


def test_a_profile_behind_the_many_repository_hub_takes_the_switch(mux):
    """The many-repository hub forwards a write by the name of its handler: a route it does not forward answers nothing."""
    mod, port, table = mux
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


def test_the_many_repository_hub_starts_the_sampler_of_a_profile_whose_switch_is_on(mux):
    """`start_profile` is what the many-repository hub's `__main__` runs for each profile, at its start and for a profile
    added later. The profile's other background reads are stubbed."""
    mod, _, table = mux
    for name in ("refresh_prs", "refresh_quota", "refresh_board", "refresh_history", "load_sampler"):
        setattr(mod, name, lambda: None)
    mod.PROFILER_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    mod.PROFILER_SWITCH.write_text(json.dumps({"on": True}))
    assert not mod.profiler_running()
    mux_mod = load("hubmux_profiler_start", SCRIPTS / "hubmux.py")
    mux_mod.start_profile({"mod": mod})
    wait(lambda: table.reads >= 2)
    assert mod.profiler_running()
    main = (SCRIPTS / "hubmux.py").read_text().split('\nif __name__ == "__main__":\n', 1)[1]
    assert "start_profile(a)" in main and "profiler_ensure" not in main, "the hub's start does not go through start_profile"


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
