"""One reader of a run's liveness (squad/run_liveness.py), and every caller reads through it.

A run is live while its last invocation (the events after its last `run_start` or `resume`) has no verdict and the engine
process that invocation named in its last `call_start` or `gate_start` still runs: the pid lives as a squad run, and the
process did not start after the call named it (a later start is a reused pid). Before its first call, a run is a launch
while its record moved within the launch window. A read that fails is an explicit unknown (`ReadError`), never "no
process" or "an empty record": a drain that reads unknown holds.

The cases run through two doors, the module and the hub's loop switch, so the two can never disagree again (#1228).
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from squad import caps
from squad import run_liveness as RL

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
NOW = 1_700_000_000.0
SQ = "uv run squad start --target /co x"
LAUNCH = 60.0


def ev(event, **kw):
    return {"ts": kw.pop("ts", NOW - 900), "event": event, **kw}


def call(pid, at, kind="call_start"):
    return ev(kind, ts=at, node="n", entry=1, item=None, session="s", pid=pid, limit=None, started_at=at)


VERDICT = ev("verdict", verdict="failed", reasons=[], exit_code=1)
START, RESUME = ev("run_start"), ev("resume")

# (name, events, record age in seconds, process snapshot {pid: (start, command)}, live)
CASES = [
    ("a run whose engine lives is live", [START, call(100, NOW - 50)], 600, {100: (NOW - 60, SQ)}, True),
    ("a run resumed after a verdict is live again", [START, call(99, NOW - 90), VERDICT, RESUME, call(100, NOW - 50)], 600,
     {100: (NOW - 55, SQ)}, True),
    ("a verdict after the last start is final", [START, RESUME, call(100, NOW - 50), VERDICT], 600, {100: (NOW - 60, SQ)}, False),
    ("a gate_start pid of the last invocation is the run's", [START, call(99, NOW - 90), RESUME, call(100, NOW - 50, "gate_start")], 600,
     {100: (NOW - 60, SQ)}, True),
    ("a pid from before a resume is not the resumed run's", [START, call(100, NOW - 50), RESUME], 600, {100: (NOW - 60, SQ)}, False),
    ("a pid reused by another squad start that began later is not the run's", [START, call(100, NOW - 500)], 600,
     {100: (NOW - 100, SQ)}, False),
    ("a pid whose start the system did not give counts by its pid", [START, call(100, NOW - 500)], 600, {100: (None, SQ)}, True),
    ("a start within a second of the call is the same process (lstart has whole seconds)", [START, call(100, NOW - 50.4)], 600,
     {100: (NOW - 50.0, SQ)}, True),
    ("a live pid that is no squad run is not the run's", [START, call(100, NOW - 50)], 600, {100: (NOW - 60, "python3 -c x")}, False),
    ("a dead pid is no process", [START, call(100, NOW - 50)], 600, {7: (NOW - 60, SQ)}, False),
    ("a run with no call is a launch while its record moved within the window", [START], 10, {}, True),
    ("a run with no call past the launch window is not live", [START], 600, {}, False),
    ("a resumed run before its first call is a launch", [START, call(100, NOW - 500), VERDICT, RESUME], 10, {}, True),
]


def write(folder, events, age):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "record.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in events))
    os.utime(f, (NOW - age, NOW - age))
    return folder


def snapshot(procs):
    return {pid: RL.Proc(start, cmd) for pid, (start, cmd) in procs.items()}


# ---------------------------------------------------------------- the module door
@pytest.mark.parametrize("name,events,age,procs,live", CASES, ids=[c[0] for c in CASES])
def test_the_module_reads_each_case(tmp_path, name, events, age, procs, live):
    run = write(tmp_path / "runs" / "r", events, age)
    assert RL.run_live_at(str(run), str(tmp_path), snapshot(procs), NOW, LAUNCH) is live
    listed = tmp_path / "active-runs.txt"
    listed.write_text("r\n")
    assert RL.live_runs(listed, str(tmp_path), snapshot(procs), NOW, LAUNCH) == (["r"] if live else [])


def test_the_reader_never_names_its_own_process(tmp_path):
    run = write(tmp_path / "runs" / "r", [START, call(100, NOW - 50)], 600)
    assert RL.run_live_at(str(run), str(tmp_path), snapshot({100: (NOW - 60, SQ)}), NOW, LAUNCH, self_pid=100) is False


def test_a_process_read_failure_is_unknown_never_no_process(tmp_path):
    write(tmp_path / "runs" / "r", [START, call(100, NOW - 50)], 600)
    listed = tmp_path / "active-runs.txt"
    listed.write_text("r\n")

    def fails():
        raise RL.ReadError("ps failed")
    with pytest.raises(RL.ReadError):
        RL.live_runs(listed, str(tmp_path), fails, NOW, LAUNCH)


def test_a_process_snapshot_is_read_only_when_a_run_needs_it(tmp_path):
    write(tmp_path / "runs" / "r", [START, call(100, NOW - 50), VERDICT], 600)
    listed = tmp_path / "active-runs.txt"
    listed.write_text("r\n")

    def fails():
        raise RL.ReadError("ps failed")
    assert RL.live_runs(listed, str(tmp_path), fails, NOW, LAUNCH) == []


def test_a_failed_process_read_is_a_read_failure(monkeypatch):
    def boom(pids=None):
        raise RL.ReadError("cannot list the processes")
    monkeypatch.setattr(RL.P, "snapshot", boom)
    with pytest.raises(RL.ReadError):
        RL.read_processes()


def test_an_empty_process_table_is_a_read_failure(monkeypatch):
    monkeypatch.setattr(RL.P, "snapshot", lambda pids=None: {})
    with pytest.raises(RL.ReadError):
        RL.read_processes()


@pytest.mark.process_bound
def test_the_live_process_reading_names_this_process_with_its_command():
    procs = RL.read_processes()
    me = procs[os.getpid()]
    # The command is whatever ps shows: an xdist worker on Linux renames itself ("[pytest-xdist running] ..."), so the
    # reading must name this process with a command, never one that holds a given word.
    assert me.start is not None and me.start <= time.time() and me.cmd.strip()


def test_the_ps_parser_reads_pid_start_and_command():
    procs = RL.parse_ps("  101 Tue Nov  3 10:00:00 2026 uv run squad start x\n"
                        "  102 Tue Nov  3 10:05:07 2026\n"
                        "  103 not a date\n"
                        "garbage\n")
    assert procs[101].cmd == "uv run squad start x"
    assert procs[102].start - procs[101].start == 307 and procs[102].cmd == ""
    assert procs[103].start is None
    assert set(procs) == {101, 102, 103}


def test_a_record_read_failure_is_unknown_never_an_empty_record(tmp_path):
    (tmp_path / "runs" / "r" / "record.jsonl").mkdir(parents=True)   # a read that fails, as root too
    with pytest.raises(RL.ReadError):
        RL.run_live_at(str(tmp_path / "runs" / "r"), str(tmp_path), {}, NOW, LAUNCH)


def test_an_active_list_read_failure_is_unknown(tmp_path):
    (tmp_path / "active-runs.txt").mkdir()
    with pytest.raises(RL.ReadError):
        RL.live_runs(tmp_path / "active-runs.txt", str(tmp_path), {}, NOW, LAUNCH)


def test_a_missing_list_or_record_is_absence(tmp_path):
    assert RL.live_runs(tmp_path / "active-runs.txt", str(tmp_path), {}, NOW, LAUNCH) == []
    (tmp_path / "active-runs.txt").write_text("gone\n")
    assert RL.live_runs(tmp_path / "active-runs.txt", str(tmp_path), {}, NOW, LAUNCH) == []


def test_the_engine_process_is_the_last_valid_pid_of_the_last_invocation():
    events = RL.invocation("".join(json.dumps(e) + "\n" for e in [START, call(11, NOW - 9), RESUME, call(12, NOW - 5),
                                                                    call(0, NOW - 4), call(True, NOW - 3)]) + "not json\n")
    assert events[0]["event"] == "resume"
    assert RL.engine_process(events) == (12, NOW - 5)
    assert RL.engine_process(RL.invocation(json.dumps(RESUME))) is None


# ---------------------------------------------------------------- one identity predicate
@pytest.mark.parametrize("began,since,same", [
    (None, 1000.0, True), (900.0, None, True), (900.0, 1000.0, True), (1000.9, 1000.0, True), (1001.0, 1000.0, True),
    (1001.5, 1000.0, False), (1500.0, 1000.0, False), (1500.0, "2026-11-03", True),
])
def test_one_predicate_says_whether_a_pid_is_still_the_named_process(began, since, same):
    assert RL.same_process(began, since) is same
    assert caps.alive(os.getpid(), since, {os.getpid(): began}) is same   # the lease reclaim reads the same rule


# ---------------------------------------------------------------- the entry point the drain calls
def drain(listed, checkout, *extra):
    return subprocess.run([sys.executable, "-m", "squad.run_liveness", "drain", str(listed), str(checkout), *extra],
                          capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_the_entry_point_answers_one_token_on_exit_0(tmp_path):
    listed = tmp_path / "active-runs.txt"
    listed.write_text("r\n")
    write(tmp_path / "runs" / "r", [START, call(100, NOW - 50), VERDICT], 600)
    p = drain(listed, tmp_path)
    assert (p.returncode, p.stdout) == (0, "idle\n"), p.stderr
    write(tmp_path / "runs" / "r", [START], 0)
    os.utime(tmp_path / "runs" / "r" / "record.jsonl")
    p = drain(listed, tmp_path)
    assert (p.returncode, p.stdout) == (0, "live\n"), p.stderr


def test_the_entry_point_says_unknown_by_its_exit_and_never_on_stdout(tmp_path):
    listed = tmp_path / "active-runs.txt"
    listed.write_text("r\n")
    (tmp_path / "runs" / "r" / "record.jsonl").mkdir(parents=True)
    p = drain(listed, tmp_path)
    assert p.returncode == RL.UNKNOWN_EXIT and p.stdout == "" and "record" in p.stderr


# ---------------------------------------------------------------- the import writes nothing
def test_importing_the_module_performs_no_io(tmp_path):
    """The defect: the drain imported hub.py every 5 s, and that import wrote runs/.hub, SG_SHARED and .hub/.gitignore."""
    probe = (
        "import os, sys\n"
        "seen = []\n"
        "def hook(event, args):\n"
        "    if event == 'open' and args[0] and not str(args[0]).endswith(('.py', '.pyc')) and not os.path.isdir(str(args[0])):\n"
        "        seen.append((event, str(args[0])))\n"
        "    elif event in ('os.mkdir', 'os.rename', 'os.remove', 'os.rmdir', 'os.symlink', 'os.truncate', 'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec'):\n"
        "        seen.append((event, str(args[:1])))\n"
        "sys.addaudithook(hook)\n"
        "import squad.run_liveness\n"
        "print(repr(seen))\n")
    p = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True, text=True, cwd=ROOT, timeout=60,
                       env={**os.environ, "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "[]", p.stdout
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- the hub door: the same cases
@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS", "SG_SQUAD", "SG_RUN_CMD_RE"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_one_reader", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name,events,age,procs,live", CASES, ids=[c[0] for c in CASES])
def test_the_hub_reads_each_case_as_the_module_does(hub, tmp_path, name, events, age, procs, live):
    run = write(tmp_path / "runs" / "r", events, age)
    assert hub.loop_run_live(str(run), snapshot(procs), now=NOW) is live


def test_the_hub_says_a_failed_read_is_unknown(hub, tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "active-runs.txt").write_text(f"{tmp_path / 'runs' / 'r'}\n")
    (tmp_path / "runs" / "r" / "record.jsonl").mkdir(parents=True)
    got = hub.loop_live()
    assert got["runs"] == [] and "record" in got.get("unknown", "")


def test_the_hub_live_answer_reads_an_empty_table_once(hub, tmp_path, monkeypatch):
    """AC D of #2582: an empty shared scan is no second read. `run_table` answers the live runs, and a caller never goes
    back to `read_processes` for a second look at the same machine."""
    reads = []

    class RL:
        Proc = object

        @staticmethod
        def read_processes():
            reads.append("read_processes")
            return {}

        @staticmethod
        def live_runs(_listed, _checkout, procs, *_a, **_k):
            if callable(procs):
                procs()   # the real reader reads a callable source; a second read shows here
            return []

    monkeypatch.setattr(hub, "run_table", lambda now=None: (reads.append("run_table") or {}))
    monkeypatch.setattr(hub, "liveness", lambda: RL)
    got = hub.loop_live()
    assert got["runs"] == [] and got["gates"] == []
    assert reads == ["run_table"], f"one Live answer read the table as {reads}"


def test_the_hub_holds_no_copy_of_the_reader():
    text = (SCRIPTS / "hub.py").read_text()
    for name in ("def loop_invocation", "PS_LSTART =", "re.search(LOOP_RUN_CMD"):
        assert name not in text, name
    assert "def last_invocation" not in (ROOT / "squad" / "reap.py").read_text()
    assert "def engine_process" not in (ROOT / "squad" / "reap.py").read_text()


# ---------------------------------------------------------------- the work a drain waits for besides a run (#1711)
def _lease(folder, name, pid, since=None):
    d = Path(folder) / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({"pid": pid, "holder": name, "since": since or time.time()}))


def test_a_live_child_review_holds_the_drain(tmp_path, monkeypatch):
    """A review in flight is work the stop would take with the group: a drain that waits is safe, one that stops is not."""
    monkeypatch.setenv("SQUAD_CAPS_DIR", str(tmp_path / "caps"))
    listed = tmp_path / "active-runs.txt"
    listed.write_text("")
    assert drain(listed, tmp_path).stdout == "idle\n", "no work at all is idle"
    _lease(tmp_path / "caps", "runreview-r-c", os.getpid())
    p = drain(listed, tmp_path)
    assert (p.returncode, p.stdout) == (0, "live\n"), p.stderr


def test_a_landing_in_flight_holds_the_drain(tmp_path, monkeypatch):
    monkeypatch.setenv("SQUAD_CAPS_DIR", str(tmp_path / "caps"))
    listed = tmp_path / "active-runs.txt"
    listed.write_text("")
    lock = tmp_path / "locks" / "single-wland"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{os.getpid()}\n")
    p = drain(listed, tmp_path)
    assert (p.returncode, p.stdout) == (0, "live\n"), p.stderr


def test_a_work_fact_it_cannot_read_is_unknown_and_holds(tmp_path, monkeypatch):
    """The rule the run read already carries: unknown holds. A landing lock with no readable pid is not "nobody lands"."""
    monkeypatch.setenv("SQUAD_CAPS_DIR", str(tmp_path / "caps"))
    listed = tmp_path / "active-runs.txt"
    listed.write_text("")
    lock = tmp_path / "locks" / "single-wland"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("")
    p = drain(listed, tmp_path)
    assert p.returncode == RL.UNKNOWN_EXIT and p.stdout == "" and "landing" in p.stderr
