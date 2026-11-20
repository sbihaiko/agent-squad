"""The hub reads the machine's process table once a tick, and never for a dashboard that is not its own (#1848).

`hub.process_table` and `hub.read_procs` used to read the whole table from scratch on every request, so `/api/runs`
paid a 70 ms read and a 63 ms read each time. Both now read through `procscan`, which keeps the machine's raw snapshot
for one second: every reader format derives its own view of that one read, so N readers in one tick cost one read. The
count is taken from an injected reader and an injected clock, never from the wall clock (ADR-0012): the test observes
the work the cache does, not how long it took.

The kill path is the exception the rule names. `_ps_rows` reads fresh every time, so `stop-now` always sees the run it
was asked to kill; the cache may make a run born inside the tick read as gone on the screen for a second, and only
there. `runs()` asks `lsof` only for a dashboard this profile owns, and keeps the port by `(pid, start)`.

`runs()` also reads an owned dashboard's port, so the same process is asked once, however often the list is rebuilt.
"""
import importlib.util
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
TICK = 1.0   # the shared snapshot's tick: PROCSCAN_SECS


class P:
    """One process, in the shape squad/procs.py's Proc answers."""

    __slots__ = ("pid", "ppid", "start", "command", "cpu")

    def __init__(self, pid, ppid=1, start=100.0, command="x", cpu=0.0):
        self.pid, self.ppid, self.start, self.command, self.cpu = pid, ppid, start, command, cpu


class FakeProcs:
    """The injected machine reader: it counts its reads and answers a copy of the table it is given."""

    def __init__(self, table, reads):
        self.table, self.reads = table, reads

    def snapshot(self):
        self.reads.append(1)
        return dict(self.table)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_procscan_cache", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ps = sys.modules["procscan"]   # the module every profile's hub.py shares: no read a test before this one left behind
    ps._STATE["snap"] = None
    ps._STATE["at"] = None
    getattr(ps, "_CELLS", {}).clear()
    getattr(ps, "_VIEWS", {}).clear()
    getattr(mod, "PORT_MEMO", {}).clear()
    return mod


def clock(hub, monkeypatch, at=1000.0):
    c = {"t": at}
    monkeypatch.setattr(hub, "time", SimpleNamespace(time=lambda: c["t"]))
    return c


# ---------------------------------------------------------------- one read a tick, one view a format
def test_one_tick_costs_one_read_however_many_readers(hub, monkeypatch):
    """The defect: every request read the whole table, `/api/runs` twice. Two readers of one tick now cost one read."""
    c = clock(hub, monkeypatch)
    reads = []
    fake = FakeProcs({1: P(1), 2: P(2)}, reads)
    monkeypatch.setattr(hub, "procs_mod", lambda: fake)
    for _ in range(3):
        hub.process_table()
        hub.read_procs()
    assert len(reads) == 1, f"one tick read the machine's process table {len(reads)} times for two readers"
    c["t"] += TICK + 0.001
    hub.process_table()
    assert len(reads) == 2, "a later tick kept a scan it should have read again"


def test_each_reader_format_reads_the_same_snapshot(hub, monkeypatch):
    """The two formats derive from one read: the table (`Proc`) and the starts the run list judges liveness on."""
    clock(hub, monkeypatch)
    reads = []
    monkeypatch.setattr(hub, "procs_mod", lambda: FakeProcs({7: P(7, start=55.0), 8: P(8, start=66.0)}, reads))
    assert hub.process_table()[7].start == 55.0
    assert hub.read_procs() == {7: 55.0, 8: 66.0}
    assert len(reads) == 1


def test_the_cache_tick_is_one_second(hub):
    assert hub.PROCSCAN_SECS == 1.0


# ---------------------------------------------------------------- the kill path never reads the cache
def test_the_kill_path_reads_fresh_not_the_one_second_cache(hub, monkeypatch):
    """A run born inside the tick may read as gone on the screen for a second, and only there: the kill path reads
    fresh, so `stop-now` always sees the process it was asked to kill."""
    clock(hub, monkeypatch)
    reads = []
    table = {1: P(1)}
    monkeypatch.setattr(hub, "procs_mod", lambda: FakeProcs(table, reads))
    assert [p.pid for p in hub.process_table().values()] == [1]
    table[2] = P(2)   # a run born inside this tick
    assert 2 in [r[0] for r in hub._ps_rows()], "the kill path read the cached table: a run born inside the tick would never be killed"
    assert 2 in [r[0] for r in hub._ps_rows()], "the kill path read the cached table on a later call"
    assert 2 not in [p.pid for p in hub.process_table().values()], "the screen's reading may lag one tick; the kill path never does"
    assert len(reads) == 3, f"the machine was read {len(reads)} times: each kill-path call reads fresh"


def test_the_profiler_and_the_load_sampler_share_one_read(hub, monkeypatch):
    """The Loop tab's sampler and the load strip parse two formats; both derive from the one snapshot of the tick."""
    clock(hub, monkeypatch)
    reads = []
    monkeypatch.setattr(hub, "procs_mod", lambda: FakeProcs({3: P(3)}, reads))
    hub.profiling_table()
    hub.proc_snapshot()
    hub.process_table()
    assert len(reads) == 1, f"one tick read the machine's process table {len(reads)} times for the two samplers"


# ---------------------------------------------------------------- lsof only for this profile's dashboard
def test_the_dashboard_port_is_asked_once_for_one_process(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(hub, "sh", lambda *a: calls.append(a) or "TCP 127.0.0.1:8765")
    assert hub.dashboard_port(100, 5.0) == "8765"
    assert hub.dashboard_port(100, 5.0) == "8765"
    assert len(calls) == 1, "lsof ran again for the same process"
    assert hub.dashboard_port(100, 6.0) == "8765"   # a reused pid has a new start: ask again
    assert len(calls) == 2
    assert all(a[0] == hub.LSOF for a in calls)


def test_runs_asks_lsof_only_for_this_profiles_dashboard(hub, tmp_path, monkeypatch):
    """The defect: `lsof` ran for every dashboard on the machine before the ownership check."""
    base = pathlib.Path(tempfile.mkdtemp(prefix="hub-runs-"))   # outside pytest's own tree: those paths read as test fixtures
    owned = base / "runs" / "run-owned"
    owned.mkdir(parents=True)
    other = base / "other" / "runs" / "run-other"
    other.mkdir(parents=True)
    table = {11: P(11, start=10.0, command=f"/x/python -m squad.dashboard_server {owned}"),
             12: P(12, start=11.0, command=f"/x/python -m squad.dashboard_server {other}")}
    calls = []
    monkeypatch.setattr(hub, "process_table", lambda *a, **k: table)
    monkeypatch.setattr(hub, "read_procs", lambda *a, **k: {})
    monkeypatch.setattr(hub, "own_roots", lambda: {owned.parent})
    monkeypatch.setattr(hub, "sh", lambda *a: calls.append(a) or "TCP 127.0.0.1:5555")
    monkeypatch.setattr(hub, "run_row", lambda d, port, procs: ({"id": pathlib.Path(d).name, "rank": 0, "age": 0}, None))
    monkeypatch.setattr(hub, "_titles", lambda: None)
    monkeypatch.setattr(hub, "active_entries", lambda: [])
    monkeypatch.setattr(hub, "held_runs", lambda: [])
    monkeypatch.setattr(hub, "childcc_row", lambda: None)
    monkeypatch.setattr(hub, "hold_rows", lambda: [])
    monkeypatch.setattr(hub, "attach_load", lambda *a, **k: None)
    monkeypatch.setattr(hub, "STATE", tmp_path)
    try:
        hub.runs()
        assert len(calls) == 1, f"lsof ran {len(calls)} times: it must run only for this profile's own dashboard"
        assert str(other) not in " ".join(calls[0]), "lsof ran for another profile's dashboard"
        hub.runs()   # the port is kept by (pid, start): the same dashboard is asked once
        assert len(calls) == 1, "lsof ran again for a dashboard whose port this profile already knows"
    finally:
        shutil.rmtree(base, ignore_errors=True)


class FakeTable:
    """A process-table reader that is an object, as a test's fake is: it has no `__qualname__` of its own."""

    def __init__(self, name):
        self.name = name

    def __call__(self):
        return {"from": self.name}


def test_two_fake_readers_in_one_tick_each_read_their_own_table():
    """A reader with no name of its own keeps its own cell: a fake installed after another fake is never answered with
    the other's scan inside one tick. A shared cell served one test the process table a test before it had left there,
    so a refused CPU read counted as read (#1957)."""
    spec = importlib.util.spec_from_file_location("procscan_two_fakes", SCRIPTS / "procscan.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    old, new = FakeTable("old"), FakeTable("new")
    assert scan.table(old, TICK, 100.0)[0] == {"from": "old"}
    assert scan.table(new, TICK, 100.0)[0] == {"from": "new"}, "the new reader was answered with the old reader's scan"
    assert scan.view("profiler", dict, TICK, 100.0, new)[0] == {"from": "new"}
    assert scan.table(old, TICK, 100.0)[0] == {"from": "old"}, "one reader of the tick reads once"


# ---------------------------------------------------------------- the run readers share the page's own window
def test_the_run_readers_keep_one_read_for_25_seconds(hub, monkeypatch):
    """AC D and step 3 of #2582: the run readers (liveness, the gate slots and the loop rows) derive from one read of the
    table, kept for 2.5 s, so the page's 3 s live poll costs one read."""
    c = clock(hub, monkeypatch)
    reads = []
    monkeypatch.setattr(hub, "procs_mod", lambda: FakeProcs({1: P(1, start=5.0)}, reads))
    hub.run_table()
    hub.run_table()
    assert len(reads) == 1, f"two run readers read the machine {len(reads)} times"
    c["t"] += 2.4
    hub.run_table()
    assert len(reads) == 1, "a read inside the window read the table again"
    c["t"] += 0.2
    hub.run_table()
    assert len(reads) == 2, "a read past the window serves the kept scan"
    assert hub.RUN_TABLE_SECS == 2.5


def test_the_live_and_stats_readers_share_one_machine_read(hub, monkeypatch):
    """AC D and step 3 of #2582: `/api/live`'s run readers and `/api/stats`'s table derive from one read of the machine in
    one tick, both through `procscan`'s shared scan, never one scan per endpoint."""
    clock(hub, monkeypatch)
    reads = []
    monkeypatch.setattr(hub, "procs_mod", lambda: FakeProcs({1: P(1)}, reads))
    hub.run_table()
    hub.process_table()
    hub.read_procs()
    assert len(reads) == 1, f"one tick read the machine's process table {len(reads)} times across the live and stats readers"


def test_one_live_answer_reads_the_process_table_once(hub, monkeypatch):
    """AC D of #2582: one `/api/live` read reads the process table at most once, whatever its rows need."""
    clock(hub, monkeypatch)
    reads = []
    monkeypatch.setattr(hub, "procs_mod", lambda: FakeProcs({1: P(1), 2: P(2)}, reads))
    hub.live_served(now=1000.0)
    assert len(reads) == 1, f"one Live answer read the machine {len(reads)} times"
