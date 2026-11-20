"""The machine-wide ceiling: `caps.runs` live runs, `caps.children` live dispatch children, `caps.gates` gate worker slots and
`caps.reviews` live reviews, across every repository and run.

This pair is not the per-run pair (`run.max_parallel`, dispatch `max_children`). Filling the ceiling queues; a wait whose bound
expires fails by name. A lease whose holder is dead is reclaimed when the slots are counted.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
import yaml

from squad import caps, defaults, procs, record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.options import OptionError
from squad.record import read
from tests.gitrepo import sibling_repo
from tests.helpers import KIND_CHAINS, router
from tests.test_dispatch import CHILD, req, routed

ROOT = Path(__file__).resolve().parent.parent


def plugin_file(tmp_path, **block):
    cfg = defaults.load()
    if block.get("drop"):
        cfg.pop("caps", None)
    p = tmp_path / "agent-defaults.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def project(tmp_path, **pair):
    d = tmp_path / "proj"
    (d / "docs" / "squad").mkdir(parents=True)
    (d / "docs" / "squad" / "defaults.yaml").write_text(yaml.safe_dump({"caps": pair}))
    return d


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_the_plugin_defaults_hold_the_machine_wide_pair_apart_from_the_per_run_pair():
    cfg = defaults.load()
    assert cfg["caps"] == {"runs": 10, "children": 20, "gates": 12, "reviews": 4}
    assert cfg["run"]["max_parallel"] == 20 and "caps" not in cfg["run"]     # the per-run pair stays where it was


def test_the_resolution_reads_the_plugin_file_and_records_its_hash():
    r = caps.resolve()
    assert (r["runs"], r["children"]) == (10, 20)
    assert r["path"] == str(defaults.PATH.resolve()) and r["sha256"] == defaults.digest(defaults.PATH) and r["project"] is None


def test_a_project_may_lower_the_pair(tmp_path):
    r = caps.resolve(project(tmp_path, runs=1, children=3))
    assert (r["runs"], r["children"]) == (1, 3) and r["project"]["sha256"]


def test_a_project_may_never_raise_the_pair(tmp_path):
    with pytest.raises(OptionError, match=r"caps\.runs.*may only lower"):
        caps.resolve(project(tmp_path, runs=11))


def test_a_missing_caps_block_is_a_named_failure_not_a_default(tmp_path):
    with pytest.raises(caps.CapsError, match=r"no caps block"):
        caps.resolve(defaults_path=plugin_file(tmp_path, drop=True))


def test_no_eleventh_run_starts_while_ten_are_live(tmp_path):
    c = caps.Ceiling({"runs": 10, "children": 20}, tmp_path / "leases")
    held = [c.try_take("runs", f"run-{i}") for i in range(10)]
    assert all(held) and c.try_take("runs", "run-10") is None
    c.release(held[0])
    assert c.try_take("runs", "run-10") is not None


def test_no_twenty_first_child_starts_while_twenty_are_live(tmp_path):
    c = caps.Ceiling({"runs": 10, "children": 20}, tmp_path / "leases")
    assert all(c.try_take("children", f"r:route.router.{i}") for i in range(20))
    assert c.try_take("children", "r:route.router.20") is None
    assert c.occupancy()["children"] == 20


def counting_ps(monkeypatch):
    """Every process `squad/caps.py` itself starts, as the argv it was given. This module's process count is the cost a
    poll pays, so it is what a fork storm is measured in."""
    calls = []
    real = procs.snapshot

    def snapshot(pids=None):
        calls.append(pids)
        return real(pids)

    monkeypatch.setattr(procs, "snapshot", snapshot)
    return calls


def test_counting_the_live_leases_starts_one_process_whatever_the_number_of_leases(tmp_path, monkeypatch):
    """The count runs on every poll of every wait, and a wait polls until a slot frees. A `ps` per lease turns that loop
    into a fork storm: a worker spins in a subprocess loop and holds what the rest of the suite needs, so on a two-core
    runner the wait never converges and the run never reaches a verdict (#130; the runner in #158). One process per
    count, whatever the number of leases."""
    c = caps.Ceiling({"runs": 10, "children": 20}, tmp_path / "leases")
    for i in range(20):
        c.try_take("children", f"other-repo:{i}")     # 20 live leases, each of them a `ps` a count would start
    calls = counting_ps(monkeypatch)
    assert c.occupancy()["children"] == 20
    assert len(calls) == 1, f"one count over 20 live leases started {len(calls)} processes"
    calls.clear()
    assert c.try_take("children", "late") is None    # the call a waiting poll makes while the ceiling is full
    assert len(calls) == 1, f"one poll over 20 live leases started {len(calls)} processes"


def test_a_lease_whose_holder_is_dead_is_reclaimed_and_the_ceiling_recovers(tmp_path):
    c = caps.Ceiling({"runs": 1, "children": 20}, tmp_path / "leases")
    assert c.try_take("runs", "killed-run", pid=dead_pid())
    assert c.occupancy()["runs"] == 0                    # one count reclaims it
    assert c.try_take("runs", "next-run") is not None


def zombie_pid():
    """A child that has exited and that its parent has not reaped: `kill -0` still answers it, and it works nothing, so it
    holds no lease (#1573). The caller waits it in a finally, which is what takes the zombie away."""
    p = subprocess.Popen(["true"])
    for _ in range(200):
        try:
            seen = procs.snapshot([p.pid]).get(p.pid)
        except procs.ReadError:
            seen = None
        if seen is not None and seen.stat == "Z":
            return p
        time.sleep(0.01)
    p.wait()
    pytest.skip("the system reaped the child before it read as a zombie")


def test_a_zombie_holders_lease_holds_no_slot(tmp_path):
    """A holder that exited and that its parent has not reaped answers `kill -0`, so the count kept its slot and the cap
    stayed full (#1573): a zombie works nothing, so its lease is stale."""
    z = zombie_pid()
    try:
        c = caps.Ceiling({"runs": 10, "children": 20, "gates": 1, "reviews": 4}, tmp_path / "leases")
        lease = c.try_take("gates", "gate-zombie", pid=z.pid)
        assert c.occupancy()["gates"] == 0               # one count reclaims it
        assert not lease.exists()
        assert c.try_take("gates", "gate-next") is not None
    finally:
        z.wait()


def test_a_live_holders_lease_still_holds_the_cap(tmp_path, capsys):
    """The other half of the rule: a live holder keeps its slot, and no reap line is printed for it."""
    live = subprocess.Popen(["sleep", "30"])
    try:
        c = caps.Ceiling({"runs": 10, "children": 20, "gates": 1, "reviews": 4}, tmp_path / "leases")
        lease = c.try_take("gates", "gate-live", pid=live.pid)
        capsys.readouterr()
        assert c.occupancy()["gates"] == 1
        assert c.try_take("gates", "gate-next") is None  # the slot still holds the ceiling
        assert lease.exists()
        assert capsys.readouterr().err == ""             # a live holder writes no reap line
    finally:
        live.kill()
        live.wait()


def test_reaping_a_dead_holders_slot_says_so_on_one_line(tmp_path, capsys):
    """A reap is never silent (serial.sh's rule, #1057): one line names the slot, its folder and the dead pid."""
    c = caps.Ceiling({"runs": 10, "children": 20, "gates": 1, "reviews": 4}, tmp_path / "leases")
    dead = dead_pid()
    lease = c.try_take("gates", "gate-dead", pid=dead)
    capsys.readouterr()
    assert c.occupancy()["gates"] == 0
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1, lines
    assert "reaping stale slot" in lines[0] and lease.name in lines[0] and str(dead) in lines[0] and "dead" in lines[0]


def test_a_refusal_names_the_lease_pid_and_never_dead(tmp_path):
    """A gate names its holder `gate-$$` while its lease carries the lock holder's pid, so a reader that follows the name
    alone lands on a pid the kernel answers nothing for, and read the refusal as a holder that had died (#1653). The line
    names the lease's own facts, and the count that refused proves the holder lives, so it never says `dead`."""
    live = subprocess.Popen(["sleep", "30"])
    try:
        e = {**os.environ, "SQUAD_CAPS_DIR": str(tmp_path / "leases"), "PYTHONPATH": str(ROOT)}
        full = {k: caps.resolve()[k] for k in caps.KINDS}
        assert caps.Ceiling(full, tmp_path / "leases").try_take_many("gates", "gate-56345", full["gates"], pid=live.pid)
        r = subprocess.run([sys.executable, "-m", "squad.caps", "take", "gates", "gate-next", "--try",
                            "--target", str(tmp_path)], capture_output=True, text=True, env=e, cwd=tmp_path, timeout=60)
        assert r.returncode == caps.SKIPPED, r.stderr
        assert f"gate-56345 (lease pid {live.pid}, live" in r.stderr, r.stderr
        assert "dead" not in r.stderr, r.stderr
    finally:
        live.kill()
        live.wait()


def test_reaping_a_zombie_holders_slot_names_the_zombie(tmp_path, capsys):
    z = zombie_pid()
    try:
        c = caps.Ceiling({"runs": 10, "children": 20, "gates": 1, "reviews": 4}, tmp_path / "leases")
        c.try_take("gates", "gate-zombie", pid=z.pid)
        capsys.readouterr()
        assert c.occupancy()["gates"] == 0
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1 and "zombie" in lines[0] and str(z.pid) in lines[0], lines
    finally:
        z.wait()


@pytest.mark.process_bound
def test_a_lease_of_a_reused_pid_is_reclaimed_and_the_ceiling_recovers(tmp_path):
    c = caps.Ceiling({"runs": 1, "children": 20}, tmp_path / "leases")
    lease = c.try_take("runs", "dead-run", pid=os.getpid())
    data = json.loads(lease.read_text())
    lease.write_text(json.dumps({**data, "since": 1.0}))
    assert c.occupancy()["runs"] == 0                    # this process started after the dead run's lease
    assert c.try_take("runs", "next-run") is not None


@pytest.mark.process_bound
def test_a_lease_naming_a_run_dashboard_holds_no_slot(tmp_path):
    """A run's dashboard page server idles and never works the run (#1389). When the engine died without a verdict and the
    dashboard outlived it, the lease still answered 'alive': the janitor could not prove the run dead, the drain never
    ended, and the slot stayed taken for hours. A lease that names a dashboard is reclaimed as one whose holder is dead,
    and the run's own record is what the janitor's rule C then reaps."""
    c = caps.Ceiling({"runs": 1, "children": 20}, tmp_path / "leases")
    d = subprocess.Popen(["bash", "-c", 'exec -a "squad.dashboard_server /tmp/run-x" sleep 30'])
    try:
        time.sleep(0.1)
        assert c.try_take("runs", "run-with-a-dead-engine", pid=d.pid)
        assert c.occupancy()["runs"] == 0                # a page server is no run, so its lease holds no slot
        assert c.try_take("runs", "next-run") is not None
    finally:
        d.kill()
        d.wait()


def listed(tmp_path, *runs):
    """An active-runs.txt naming one run folder per (name, events) pair; the entries are absolute, as launch.py writes them.
    `events` is None for a run with no record yet (a launch, with a fresh `request.txt` as launch.py writes), else the
    record's events in order, each of "verdict", "run_start" or "progress", or a `("call_start", pid)` or `("signal", pid)`
    pair naming a process the run's record holds."""
    st = tmp_path / "st"
    st.mkdir(parents=True, exist_ok=True)
    path = st / "active-runs.txt"
    names = []
    for name, events in runs:
        folder = tmp_path / "runs" / name
        folder.mkdir(parents=True, exist_ok=True)
        if events is None:
            (folder / "request.txt").write_text(f"run the ticket for {name}\n")
        else:
            rec = record.Record(folder / "record.jsonl")
            for event in events:
                if event == "verdict":
                    rec.append("verdict", verdict="completed", exit_code=0, reasons=[], remains=[], folded=[])
                elif event == "run_start":
                    rec.append("run_start", run_id=name, graph_name="g", graph_hash="h", engine_options={}, root="root", plan=[])
                elif event == "progress":
                    rec.append("progress", node="root", entry=1, item=0, session="s", messages=1, context_tokens=1, tool="Read")
                elif isinstance(event, tuple) and event[0] == "call_start":
                    rec.append("call_start", node="root", entry=1, session="s", pid=event[1], limit=600, started_at=time.time())
                elif isinstance(event, tuple) and event[0] == "signal":
                    rec.append("signal", signal="SIGTERM", pid=event[1], ppid=1, pgid=1)
                else:
                    raise ValueError(event)
        names.append(str(folder))
    path.write_text("".join(n + "\n" for n in names))
    return path


def test_a_run_with_a_final_verdict_holds_no_run_slot(tmp_path):
    """Dispatch counted a run with a verdict as live until its issue closes, so finished runs starved caps.runs. Two finished
    runs and one live run stand listed; caps.runs is two, so one slot is free: the record is the truth."""
    c = caps.Ceiling({**FOUR, "runs": 2}, tmp_path / "leases")
    p = listed(tmp_path, ("run-done-a", ("verdict",)), ("run-done-b", ("verdict",)), ("run-live", ("progress",)))
    assert c.run_holders(p, root=tmp_path) == ["run-live"]
    assert caps.free_runs(c, p, root=tmp_path) == 1


def test_a_resumed_run_holds_its_slot_though_a_verdict_stands(tmp_path):
    """A verdict is final only as the last one after the last `run_start` or `resume` (ADR-0105), as the dashboard, wave.sh
    and wtclean.sh read it. A run resumed by `squad resume --auto-source autonomy` carries an old verdict and a later start;
    it is live, so it holds its slot and the dispatcher never starts a run the engine's `try_take` refuses."""
    c = caps.Ceiling({**FOUR, "runs": 2}, tmp_path / "leases")
    p = listed(tmp_path, ("run-resumed", ("verdict", "run_start")), ("run-done", ("verdict",)))
    assert c.run_holders(p, root=tmp_path) == ["run-resumed"]
    assert caps.free_runs(c, p, root=tmp_path) == 1


def test_a_run_without_a_verdict_keeps_its_slot(tmp_path):
    c = caps.Ceiling({**FOUR, "runs": 2}, tmp_path / "leases")
    p = listed(tmp_path, ("run-a", ("progress",)), ("run-b", ("progress",)))
    assert caps.free_runs(c, p, root=tmp_path) == 0


def test_a_listed_run_whose_named_process_is_gone_holds_no_slot(tmp_path):
    """A run killed with no verdict leaves its active-runs line and a record whose last line is the engine's SIGTERM. The
    counter counted that line as live until the janitor's takeover bound reached it (50 minutes on the live profile), so
    four dead runs held caps.runs and the frontier dispatched nothing for forty minutes. The counter asks the liveness
    question the janitor asks (ADR-0166): the process the record names is gone, so the slot is free. The surgery stays the
    janitor's: the killed verdict and the comment are still its own."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    gone = dead_pid()
    p = listed(tmp_path, ("run-killed", (("call_start", gone), ("signal", gone))))
    assert caps.free_runs(c, p, root=tmp_path) == 1


def test_a_listed_run_whose_named_process_lives_holds_its_slot(tmp_path):
    """A live run keeps its slot whatever its age: only the takeover bound ends it (ADR-0166). The process its record names
    still runs, so the counter never frees the slot of a run that is working."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    engine = subprocess.Popen(["bash", "-c", 'exec -a "squad start /tmp/run-live" sleep 30'])
    try:
        time.sleep(0.1)
        p = listed(tmp_path, ("run-live", (("call_start", engine.pid),)))
        assert caps.free_runs(c, p, root=tmp_path) == 0
    finally:
        engine.kill()
        engine.wait()


def test_a_live_runs_lease_and_its_listed_line_are_one_holder(tmp_path):
    """A run holds one slot, never two: the lease its engine took and the line the loop lists it by are one holder. With room
    to spare the count stays at one, so a live run never eats a slot it does not use."""
    c = caps.Ceiling({**FOUR, "runs": 3}, tmp_path / "leases")
    p = listed(tmp_path, ("run-a", ("progress",)), ("run-b", ("progress",)))
    assert caps.free_runs(c, p, root=tmp_path) == 1
    assert c.try_take("runs", "run-a")
    assert caps.free_runs(c, p, root=tmp_path) == 1     # two holders, not three


def test_a_listed_run_with_no_record_yet_keeps_its_slot(tmp_path):
    """A run in its launch window has no record and no verdict: it is live, so it holds its slot."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    p = listed(tmp_path, ("run-starting", None))
    assert caps.free_runs(c, p, root=tmp_path) == 0


def test_a_listed_run_with_no_record_past_its_launch_window_holds_no_slot(tmp_path):
    """A launch that never started must not pin a slot for ever: the janitor drops an entry with no record past the same
    bound (rule J, the 300-second `request.txt` window), so the counter stops holding it there too."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    p = listed(tmp_path, ("run-stuck", None))
    old = time.time() - caps.LAUNCH_GRACE - 1
    os.utime(tmp_path / "runs" / "run-stuck" / "request.txt", (old, old))
    assert caps.free_runs(c, p, root=tmp_path) == 1


def test_a_listed_run_whose_folder_is_gone_holds_no_slot(tmp_path):
    """A purged run folder runs nothing, so it holds no slot: the janitor drops the missing entry, and a slot never waits
    for a folder that is not there."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    p = listed(tmp_path, ("run-gone", None))
    gone = tmp_path / "runs" / "run-gone"
    (gone / "request.txt").unlink()
    gone.rmdir()
    assert caps.free_runs(c, p, root=tmp_path) == 1


def test_a_live_lease_of_a_run_the_list_does_not_name_still_counts(tmp_path):
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    p = listed(tmp_path, ("run-done", ("verdict",)))
    assert c.try_take("runs", "another-repo:run-9")    # a run another checkout started
    assert caps.free_runs(c, p, root=tmp_path) == 0


def test_a_lingering_lease_of_a_finished_run_still_counts(tmp_path):
    """The dispatcher's count is never more free than the engine's admission: the engine counts raw leases at `try_take`,
    so a finished run's lease the engine has not released is counted here too. The engine releases its lease before it
    writes the verdict, so this is the rare lingering lease and not the normal case (ADR-0167)."""
    c = caps.Ceiling({**FOUR, "runs": 2}, tmp_path / "leases")
    p = listed(tmp_path, ("run-done", ("verdict",)), ("run-live", ("progress",)))
    assert caps.free_runs(c, p, root=tmp_path) == 1
    assert c.try_take("runs", "run-done")
    assert caps.free_runs(c, p, root=tmp_path) == 0    # the dispatcher frees no slot the engine still holds


def test_the_run_with_a_verdict_stays_listed_for_the_reader_that_closes_its_issues(tmp_path):
    """The counter frees the slot and leaves the ledger alone: the janitor (rules E, O and R) and childcc still find the
    finished run where they look, and its record still reads to its verdict."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    p = listed(tmp_path, ("run-done", ("verdict",)), ("run-live", ("progress",)))
    before = p.read_text()
    assert caps.free_runs(c, p, root=tmp_path) == 0
    assert p.read_text() == before
    assert str(tmp_path / "runs" / "run-done") in p.read_text()
    assert read(tmp_path / "runs" / "run-done" / "record.jsonl")[-1]["event"] == "verdict"


@pytest.mark.anyio
async def test_a_wait_whose_bound_expires_fails_by_name(tmp_path):
    c = caps.Ceiling({"runs": 1, "children": 20}, tmp_path / "leases", poll=0.01)
    c.try_take("runs", "holder")
    waits = []
    with pytest.raises(caps.CeilingWaitExpired, match=r"caps\.runs") as e:
        await c.take("runs", "late", deadline_at=anyio.current_time() + 0.05, on_wait=waits.append)
    assert e.value.ceiling == "caps.runs" and e.value.cap == 1
    assert waits == [{"ceiling": "caps.runs", "cap": 1, "live": 1, "holder": "late", "held_by": ["holder"]}]


def make(tmp_path, ceiling, script, tweak=None, **kw):
    d = router()
    d["run"]["deadline"] = 7200
    if tweak:
        tweak(d)
    kw.setdefault("kind_chains", KIND_CHAINS)
    return Engine(from_doc(d), ScriptedAdapter(script), tmp_path / "run", target=sibling_repo(tmp_path), ceiling=ceiling, **kw)


def events(tmp_path, kind):
    return [e for e in read(tmp_path / "run" / "record.jsonl") if e["event"] == kind]


def free_when_the_ceiling_reports_the_wait(c, lease, seconds=30.0):
    """The other side of the case: release `lease` once the wait is registered. Bounded, because a case that waits on a
    loop that never reports must fail by name rather than spin: that is how one worker parked a whole run (#130, #158)."""
    async def free_one():
        deadline = anyio.current_time() + seconds
        while not c.waiting():
            assert anyio.current_time() < deadline, f"the ceiling never reported a wait within {seconds:g} s"
            await anyio.sleep(0.01)
        c.release(lease)
    return free_one


@pytest.mark.anyio
async def test_a_child_over_the_ceiling_waits_and_starts_when_a_slot_frees(tmp_path):
    c = caps.Ceiling({"runs": 10, "children": 20}, tmp_path / "leases", poll=0.01)
    others = [c.try_take("children", f"other-repo:{i}") for i in range(20)]
    eng = make(tmp_path, c, {"router": routed(req()), "route.router.1": CHILD})
    free_one = free_when_the_ceiling_reports_the_wait(c, others[0])

    async with anyio.create_task_group() as tg:
        tg.start_soon(free_one)
        v = await eng.run()
    assert v.verdict == "completed"
    w = [e for e in events(tmp_path, "ceiling_wait") if e["ceiling"] == "caps.children"]
    assert len(w) == 1 and w[0]["cap"] == 20 and w[0]["live"] == 20 and w[0]["holder"].endswith(":route.router.1")
    assert c.occupancy() == {"runs": 0, "children": 19, "gates": 0, "reviews": 0, "waiting": []}      # the run and its child gave their slots back


@pytest.mark.anyio
async def test_a_child_whose_wait_expires_fails_by_name_with_the_full_ceiling(tmp_path):
    c = caps.Ceiling({"runs": 10, "children": 2}, tmp_path / "leases", poll=0.01)
    c.try_take("children", "other-repo:a")
    c.try_take("children", "other-repo:b")
    eng = make(tmp_path, c, {"router": routed(req(deadline=1)), "route.router.1": CHILD}, time_scale=0.05)
    await eng.run()
    out = [e for e in events(tmp_path, "outcome") if e["node"] == "route.router.1"][0]
    assert out["outcome"] == "fail" and out["reasons"][:2] == ["ceiling_wait_expired:caps.children", "cap:2"]


@pytest.mark.anyio
async def test_a_run_over_the_ceiling_waits_and_records_the_ceiling(tmp_path):
    c = caps.Ceiling({"runs": 10, "children": 20}, tmp_path / "leases", poll=0.01)
    others = [c.try_take("runs", f"other-{i}") for i in range(10)]
    eng = make(tmp_path, c, {"router": routed(req()), "route.router.1": CHILD})
    free_one = free_when_the_ceiling_reports_the_wait(c, others[-1])

    async with anyio.create_task_group() as tg:
        tg.start_soon(free_one)
        v = await eng.run()
    assert v.verdict == "completed"
    start = events(tmp_path, "run_start")[0]
    assert start["caps"]["runs"] == 10 and start["caps"]["children"] == 20
    w = events(tmp_path, "ceiling_wait")
    assert w[0]["ceiling"] == "caps.runs" and w[0]["cap"] == 10 and w[0]["holder"] == eng.run_id


@pytest.mark.anyio
async def test_a_run_whose_wait_expires_fails_by_name(tmp_path):
    c = caps.Ceiling({"runs": 1, "children": 20}, tmp_path / "leases", poll=0.01)
    c.try_take("runs", "other")
    eng = make(tmp_path, c, {"router": routed(req()), "route.router.1": CHILD}, time_scale=0.00001)     # a run deadline of 7200 scaled to 72 ms
    v = await eng.run()
    assert v.verdict != "completed"
    root = [e for e in events(tmp_path, "outcome") if e["node"] == eng.g.root]
    assert root and root[-1]["reasons"][:2] == ["ceiling_wait_expired:caps.runs", "cap:1"]


def test_the_launch_ceiling_is_the_one_resolution_and_lives_in_the_machine_wide_folder(tmp_path):
    from squad import launch
    proj = project(tmp_path, runs=1)
    c = launch.launch_ceiling(proj, defaults.PATH)
    assert c.caps == caps.resolve(proj) and c.caps["runs"] == 1
    assert str(c.folder).startswith(os.environ["SQUAD_CAPS_DIR"])


# --- gates and reviews: the two kinds a gate and a review take from the same budget -------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
FOUR = {"runs": 10, "children": 20, "gates": 12, "reviews": 4}


def cli(tmp_path, *argv, **env):
    """`python3 -m squad.caps ...` as a bash script runs it, in the leases folder of this case."""
    e = {**os.environ, "SQUAD_CAPS_DIR": str(tmp_path / "leases"), "PYTHONPATH": str(ROOT), **env}
    return subprocess.run([sys.executable, "-m", "squad.caps", *argv], capture_output=True, text=True, env=e, cwd=tmp_path, timeout=60)


def test_the_resolution_carries_gates_and_reviews_and_a_project_may_lower_them(tmp_path):
    r = caps.resolve(project(tmp_path, gates=2, reviews=1))
    assert (r["gates"], r["reviews"]) == (2, 1) and (r["runs"], r["children"]) == (10, 20)


@pytest.mark.parametrize("kind,above", [("gates", 13), ("reviews", 5)])
def test_a_project_above_the_plugin_default_of_gates_or_reviews_is_refused(tmp_path, kind, above):
    with pytest.raises(OptionError, match=rf"caps\.{kind}.*may only lower"):
        caps.resolve(project(tmp_path, **{kind: above}))


def test_a_caps_block_without_gates_and_reviews_is_a_named_failure(tmp_path):
    cfg = defaults.load()
    cfg["caps"] = {"runs": 10, "children": 20}
    p = tmp_path / "agent-defaults.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(caps.CapsError, match=r"gates"):
        caps.resolve(defaults_path=p)


def test_gate_slots_are_taken_whole_when_they_fit(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    first = c.try_take_many("gates", "gate-a", 6)
    assert len(first) == 6 and c.occupancy()["gates"] == 6
    assert len(c.try_take_many("gates", "gate-b", 6)) == 6               # it fits: the six are taken whole
    assert c.occupancy()["gates"] == 12
    c.release_many(first)
    assert len(c.try_take_many("gates", "gate-c", 6)) == 6 and c.occupancy()["gates"] == 12


def test_the_slots_of_a_dead_holder_are_reclaimed_whatever_the_kind(tmp_path):
    c = caps.Ceiling({**FOUR, "gates": 6}, tmp_path / "leases")
    assert len(c.try_take_many("gates", "killed-gate", 6, pid=dead_pid())) == 6
    assert c.occupancy()["gates"] == 0
    assert len(c.try_take_many("gates", "next-gate", 6)) == 6
    c2 = caps.Ceiling({**FOUR, "reviews": 1}, tmp_path / "leases")
    assert c2.try_take("reviews", "killed-review", pid=dead_pid())
    assert c2.try_take("reviews", "next-review") is not None


@pytest.mark.anyio
async def test_a_step_waits_while_the_slots_are_full_and_names_the_ceiling_and_its_holder(tmp_path):
    c = caps.Ceiling({**FOUR, "reviews": 1}, tmp_path / "leases", poll=0.01)
    held = c.try_take("reviews", "review-of-x")
    waits = []

    async def free():
        while not c.waiting():
            await anyio.sleep(0.01)
        c.release(held)

    async with anyio.create_task_group() as tg:
        tg.start_soon(free)
        lease = await c.take("reviews", "late-review", deadline_at=anyio.current_time() + 30, on_wait=waits.append)
    assert lease is not None
    assert waits == [{"ceiling": "caps.reviews", "cap": 1, "live": 1, "holder": "late-review", "held_by": ["review-of-x"]}]


@pytest.mark.anyio
async def test_a_long_wait_ticks_so_the_record_of_the_run_keeps_moving(tmp_path):
    """A wait inside a run stays inside the call deadline and writes to the record while it lasts, so the heartbeat reads a run
    that waits for a slot as a run that moves.

    The case waits for ticks, never for a wall second: what it reads is the ticks that arrived, and a slot frees once three
    have. The bound below is the guard against a wait that never ticks, so it is far wider than the work the ticks need: a
    0.3 s window of a loaded runner can hold fewer than three polls, and the case then read the machine's load, not the tick
    (#736)."""
    c = caps.Ceiling({**FOUR, "reviews": 1}, tmp_path / "leases", poll=0.01)
    holder = c.try_take("reviews", "holder")
    ticks = []
    bound = 60.0

    async def free_after_three_ticks():
        deadline = anyio.current_time() + bound
        while len(ticks) < 3:
            assert anyio.current_time() < deadline, f"the wait ticked {len(ticks)} times within {bound:g} s"
            await anyio.sleep(0.01)
        c.release(holder)

    async with anyio.create_task_group() as tg:
        tg.start_soon(free_after_three_ticks)
        lease = await c.take("reviews", "late", deadline_at=anyio.current_time() + bound, on_tick=ticks.append, tick=0.05)
    assert lease is not None and len(ticks) >= 3 and ticks[0]["ceiling"] == "caps.reviews"
    assert c.occupancy()["reviews"] == 1        # the wait took the slot the release freed


def test_try_mode_skips_at_once_with_one_line_naming_the_ceiling_and_its_holder(tmp_path):
    alive = str(os.getpid())
    held = cli(tmp_path, "take", "reviews", "first", "--pid", alive)
    assert held.returncode == 0, held.stderr
    for i in range(3):
        cli(tmp_path, "take", "reviews", f"filler-{i}", "--pid", alive)
    t0 = time.monotonic()
    r = cli(tmp_path, "take", "reviews", "second", "--try")
    assert r.returncode == 73 and time.monotonic() - t0 < 5
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and "caps.reviews" in lines[0] and "first" in lines[0] and "skipped" in lines[0]


@pytest.mark.heavy     # a wait that runs out its 1 s bound, plus the steps around it (#1511)
def test_a_step_that_waits_logs_one_line_and_expires_with_exit_75(tmp_path):
    for i in range(4):
        cli(tmp_path, "take", "reviews", f"filler-{i}", "--pid", str(os.getpid()))
    r = cli(tmp_path, "take", "reviews", "late", "--wait", "1", SQUAD_CAPS_POLL="0.1")
    assert r.returncode == 75
    waiting = [ln for ln in r.stderr.splitlines() if "waiting" in ln]
    assert len(waiting) == 1 and "caps.reviews" in waiting[0] and "filler-0" in waiting[0] and "4" in waiting[0]


def test_the_cli_takes_and_releases_a_count_of_slots(tmp_path):
    r = cli(tmp_path, "take", "gates", "gate-1", "--count", "6", "--pid", str(os.getpid()))
    assert r.returncode == 0 and r.stdout.strip() == "6"
    assert json.loads(cli(tmp_path).stdout)["occupancy"]["gates"] == 6
    assert cli(tmp_path, "release", "gates", "gate-1").returncode == 0
    assert json.loads(cli(tmp_path).stdout)["occupancy"]["gates"] == 0


def test_the_command_with_no_target_reads_the_project_file_of_the_working_directory(tmp_path):
    proj = project(tmp_path, runs=4, gates=3)
    out = json.loads(cli(proj).stdout)
    assert (out["runs"], out["gates"]) == (4, 3)
    assert out["project"]["path"] == str((proj / "docs" / "squad" / "defaults.yaml").resolve())


def test_the_command_with_no_target_and_no_project_file_reads_the_plugin_values(tmp_path):
    out = json.loads(cli(tmp_path).stdout)
    assert (out["runs"], out["gates"]) == (10, 12) and out["project"] is None


def test_a_gate_asking_for_more_than_the_project_ceiling_takes_the_ceiling_and_never_waits(tmp_path):
    proj = project(tmp_path, gates=2)
    t0 = time.monotonic()
    r = cli(tmp_path, "take", "gates", "gate-1", "--count", "6", "--target", str(proj), "--wait", "30", "--pid", str(os.getpid()))
    assert r.returncode == 0 and r.stdout.strip() == "2" and time.monotonic() - t0 < 10
    assert "caps.gates" in r.stderr and "2" in r.stderr           # one line says the count was lowered to the ceiling


def childcc_fixture(tmp_path):
    """A run of one passed child that cut a wave, a child worktree, a stub review and a stub wave: childcc.py reaches its review."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    src = ROOT / "contrib" / "squad-goal" / "scripts"
    for f in ("childcc.py", "issue_state.py", "child_issues.py", "issue_ledger.py", "board_columns.py", "board-definition.json"):
        (scripts / f).write_text((src / f).read_text())
    (scripts / "board.sh").write_text("#!/bin/bash\nexit 0\n")
    (scripts / "childreview.sh").write_text(f"#!/bin/bash\necho reviewed >> {tmp_path}/reviews\n")
    (scripts / "wave.sh").write_text("#!/bin/bash\necho 'WAVE HOLD: no review yet'; exit 1\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text("#!/bin/bash\ncase \"$*\" in *'-q .state'*) echo open;; esac\n")
    for f in (*scripts.glob("*.sh"), bin_ / "gh"):
        f.chmod(0o755)
    st = tmp_path / "st"
    (st / "auto").mkdir(parents=True)
    (st / "auto" / "waved-r1").touch()
    (st / "auto" / "wave-r1").touch()
    run = tmp_path / "co" / "runs" / "r1"
    (st / "active-runs.txt").write_text(f"{run}\n")
    wt = run / "ws" / "target-c"
    wt.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    (run / "record.jsonl").write_text(
        json.dumps({"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #77"}) + "\n"
        + json.dumps({"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}) + "\n")
    env = {**os.environ, "SG_STATE": str(st), "SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_SQUAD": str(ROOT),
           "PATH": f"{bin_}:{os.environ['PATH']}", "SQUAD_CAPS_DIR": str(tmp_path / "leases"), "SQUAD_CAPS_POLL": "0.1"}
    return scripts / "childcc.py", tmp_path / "co", env


@pytest.mark.heavy   # four CLI calls and a childcc subprocess: 2.9 s here, over the 2 s line
def test_a_review_in_childcc_never_waits_for_a_slot_while_it_holds_the_childcc_lock(tmp_path):
    script, co, env = childcc_fixture(tmp_path)
    alive = str(os.getpid())
    for i in range(4):
        assert cli(tmp_path, "take", "reviews", f"other-{i}", "--pid", alive).returncode == 0
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=co, timeout=60)
    assert time.monotonic() - t0 < 20, "childcc waited for a review slot"
    assert not (tmp_path / "reviews").exists()                                      # the review did not run this pass
    # The skip line is the one that names the ceiling and its holder. The per-child cause of #931 also names caps.reviews
    # ("no review slot this pass: caps.reviews is full"), and it is a different statement, so the count reads the skip only.
    skips = [ln for ln in r.stdout.splitlines() if "caps.reviews" in ln and "skipped" in ln]
    assert len(skips) == 1 and "other-0" in skips[0]
    assert r.returncode == 3                                                        # the child is left for a later pass, never landed without
    assert not (tmp_path / "st" / "auto" / "rvd-r1-c").exists()                     # no claim: the next pass reviews it


def test_a_review_in_childcc_takes_a_review_slot_and_gives_it_back(tmp_path):
    script, co, env = childcc_fixture(tmp_path)
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=co, timeout=60)
    assert (tmp_path / "reviews").read_text() == "reviewed\n", r.stdout + r.stderr
    assert json.loads(cli(tmp_path).stdout)["occupancy"]["reviews"] == 0


# ---- #1226: while the loop is stopped, the health pass frees what a dead run holds (squad/reap.py) ----
OLD = 1_000_000.0   # a wall time long before this machine started any process: a pid named with it is a reused pid


def reap_run(tmp_path, name, *calls, request_age=3600, record_age=3600):
    """A run folder listed in active-runs.txt. Each of `calls` is "start", "resume", "verdict", "ceiling_wait", or a
    (pid, started_at) pair: a `call_start` the engine process `pid` wrote. The request and the record are old unless an age
    says otherwise."""
    st = tmp_path / "st"
    st.mkdir(parents=True, exist_ok=True)
    folder = tmp_path / "runs" / name
    r = record.Record(folder / "record.jsonl")
    for c in calls:
        if c == "start":
            r.append("run_start", run_id=name, graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
        elif c == "resume":
            r.append("resume", run_id=name, graph_hash="h", frontier=[], invoker="cli", engine_options={}, preflight={})
        elif c == "verdict":
            r.append("verdict", verdict="pass", exit_code=0, reasons=[], remains=[], folded=[])
        elif c == "ceiling_wait":
            r.append("ceiling_wait", ceiling="caps.runs", cap=3, live=3, holder=name)
        else:
            r.append("call_start", node="main", entry=1, session="s", pid=c[0], limit=None, started_at=c[1])
    (folder / "request.txt").write_text("the request\n")
    now = time.time()
    os.utime(folder / "request.txt", (now - request_age, now - request_age))
    os.utime(folder / "record.jsonl", (now - record_age, now - record_age))
    with open(st / "active-runs.txt", "a") as f:
        f.write(f"{folder}\n")
    return folder


def last_event(folder):
    return read(folder / "record.jsonl")[-1]


def reap_pass(tmp_path, c, procs=(), dry=False):
    from squad import reap
    return reap.reap(tmp_path / "st" / "active-runs.txt", tmp_path, ceiling=c, procs=list(procs), dry=dry)


def test_reap_frees_dead_gate_leases_while_the_loop_is_stopped(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    assert c.try_take_many("gates", "child-dead", 3, pid=dead_pid())
    assert c.try_take("gates", "child-live", pid=os.getpid())
    reap_pass(tmp_path, c)
    assert sorted(p.name for p in (tmp_path / "leases" / "gates").glob("*.json")) == ["child-live.json"]


def test_reap_gives_a_dead_unfinished_run_its_killed_verdict_and_keeps_its_line(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-dead", "start", (dead_pid(), time.time() - 7200))
    act = tmp_path / "st" / "active-runs.txt"
    before = act.read_text()
    assert caps.free_runs(caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases"), act) == 1   # the counter already frees it (ADR-0167)
    assert reap_pass(tmp_path, c) == ["run-dead"]
    ev = last_event(run)
    assert ev["event"] == "verdict" and ev["verdict"] == "failed" and ev["reasons"] == ["killed/no-process"]
    assert act.read_text() == before                       # landing and issue reconciliation still read the line
    assert caps.free_runs(caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases"), act) == 1
    assert reap_pass(tmp_path, c) == []                     # a run that holds its final verdict is not marked twice


def test_reap_never_kills_a_resumed_run_whose_new_process_lives(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-resumed", "start", (dead_pid(), time.time() - 7200), "verdict", "resume", (os.getpid(), time.time()))
    assert reap_pass(tmp_path, c) == []
    assert last_event(run)["event"] == "call_start"


def test_reap_never_kills_a_resumed_run_that_has_named_no_process_yet(tmp_path):
    """The dead pid before the resume is the earlier invocation's: it proves nothing about the resumed one, and a resume that
    moved its record a minute ago is still starting (#1270's five-minute absence bound is what proves a pid-less run dead)."""
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-resuming", "start", (dead_pid(), time.time() - 7200), "verdict", "resume", record_age=200)
    assert reap_pass(tmp_path, c) == []
    assert last_event(run)["event"] == "resume"


def test_reap_leaves_a_live_run_alone(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-live", "start", (os.getpid(), time.time()))
    assert reap_pass(tmp_path, c) == []
    assert last_event(run)["event"] == "call_start"


@pytest.mark.process_bound
def test_reap_takes_a_reused_pid_as_no_proof_of_life(tmp_path):
    """A process that started after the run named its pid is another process: the run is dead, and the unrelated process is
    left running (reap kills nothing)."""
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-reused", "start", (os.getpid(), OLD))
    assert reap_pass(tmp_path, c) == ["run-reused"]
    assert last_event(run)["reasons"] == ["killed/no-process"]
    os.kill(os.getpid(), 0)                                 # this process, the one that reused the pid, still lives


def test_reap_leaves_a_run_inside_its_launch_grace_or_with_a_fresh_record(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    young = reap_run(tmp_path, "run-young", "start", (dead_pid(), time.time() - 60), request_age=10)
    fresh = reap_run(tmp_path, "run-fresh", "start", (dead_pid(), time.time() - 60), record_age=5)
    assert reap_pass(tmp_path, c) == []
    assert last_event(young)["event"] == last_event(fresh)["event"] == "call_start"


def test_reap_leaves_a_run_waiting_for_a_gate_slot(tmp_path):
    """The engine is gone, but a gate process inside the run's folder still waits for its slot, or holds it."""
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-gate", "start", (dead_pid(), time.time() - 7200))
    procs = [(os.getpid(), str(run / "wt"), "bash /x/contrib/squad-goal/scripts/gate.sh full")]
    assert reap_pass(tmp_path, c, procs) == []
    assert last_event(run)["event"] == "call_start"


def test_reap_leaves_a_run_that_holds_a_live_lease(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-leased", "start", (dead_pid(), time.time() - 7200))
    assert c.try_take("children", "run-leased:child-1", pid=os.getpid())
    assert reap_pass(tmp_path, c) == []
    assert last_event(run)["event"] == "call_start"


def test_reap_reads_a_missing_list_or_record_as_absence(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    assert reap_pass(tmp_path, c) == []
    (tmp_path / "st").mkdir()
    gone = tmp_path / "runs" / "run-gone"
    (tmp_path / "st" / "active-runs.txt").write_text(f"{gone}\n")
    assert reap_pass(tmp_path, c) == []
    assert not gone.exists()


def test_reap_dry_names_the_run_and_writes_nothing(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-dead", "start", (dead_pid(), time.time() - 7200))
    assert reap_pass(tmp_path, c, dry=True) == ["run-dead"]
    assert last_event(run)["event"] == "call_start"


# ---- #1270: a run that named no process, and whose engine died while it waited at the cap ----
def test_reap_gives_a_run_that_named_no_process_its_killed_verdict_past_five_minutes(tmp_path):
    """A run that waited at `caps.runs` records only `ceiling_wait`, so it names no pid, and the reap could never prove it dead:
    its slot stayed counted for 35 minutes while nothing dispatched. A record that has not moved for five minutes, with no
    process inside its folder, is proof of death without a named pid."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    run = reap_run(tmp_path, "run-waiting", "start", "ceiling_wait", record_age=400)
    act = tmp_path / "st" / "active-runs.txt"
    assert caps.free_runs(c, act) == 0
    assert reap_pass(tmp_path, c) == ["run-waiting"]
    ev = last_event(run)
    assert ev["event"] == "verdict" and ev["reasons"] == ["killed/no-process"]
    assert caps.free_runs(c, act) == 1                     # the slot is free in the same pass


def test_reap_leaves_a_run_that_named_no_process_while_its_record_is_young(tmp_path):
    """The five-minute bound is the proof: a run that has not named a process yet may still be starting, and a record that moved
    inside the bound is no evidence of a dead run."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    run = reap_run(tmp_path, "run-waiting", "start", "ceiling_wait", record_age=200)
    assert reap_pass(tmp_path, c) == []
    assert last_event(run)["event"] == "ceiling_wait"


def test_reap_leaves_a_run_that_named_no_process_and_has_a_live_session_in_its_folder(tmp_path):
    """A run waiting at the cap is alive when its own session works inside its folder, whatever its pid-less record says."""
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-waiting", "start", "ceiling_wait", record_age=400)
    procs = [(os.getpid(), str(run / "ws"), "python3 -m squad start run-waiting")]
    assert reap_pass(tmp_path, c, procs) == []
    assert last_event(run)["event"] == "ceiling_wait"


def test_reap_leaves_a_run_that_named_no_process_but_holds_a_live_lease(tmp_path):
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-waiting", "start", "ceiling_wait", record_age=400)
    assert c.try_take("children", "run-waiting:child-1", pid=os.getpid())
    assert reap_pass(tmp_path, c) == []
    assert last_event(run)["event"] == "ceiling_wait"


# ---- #1270: a full ceiling names the holder that has had no lease and no process for five minutes ----
def stale_slots(tmp_path, c, procs=(), bound=None):
    from squad import reap
    act = tmp_path / "st" / "active-runs.txt"
    kw = {} if bound is None else {"bound": bound}
    return reap.stale_slot_holders(act, str(tmp_path), ceiling=c, procs=list(procs), now=time.time(), **kw)


def test_a_full_ceiling_names_a_holder_that_has_had_no_lease_and_no_process_for_five_minutes(tmp_path):
    """A pass that reads no free slot must say which run holds it when that run has had no live lease and no live process for
    five minutes: the owner sees the cause, not just the count."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    reap_run(tmp_path, "run-idle", "start", "ceiling_wait", record_age=400)
    out = stale_slots(tmp_path, c)
    assert [name for name, _ in out] == ["run-idle"]
    assert out[0][1] >= 300


def test_a_free_slot_names_no_holder(tmp_path):
    c = caps.Ceiling({**FOUR, "runs": 2}, tmp_path / "leases")
    reap_run(tmp_path, "run-idle", "start", "ceiling_wait", record_age=400)
    assert stale_slots(tmp_path, c) == []


def test_a_holder_inside_the_five_minute_bound_is_not_named(tmp_path):
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    reap_run(tmp_path, "run-young", "start", "ceiling_wait", record_age=200)
    assert stale_slots(tmp_path, c) == []


def test_a_holder_with_a_live_process_in_its_folder_is_not_named(tmp_path):
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    run = reap_run(tmp_path, "run-live", "start", "ceiling_wait", record_age=400)
    procs = [(os.getpid(), str(run / "ws"), "python3 -m squad start run-live")]
    assert stale_slots(tmp_path, c, procs) == []


def test_a_holder_with_a_live_lease_is_not_named(tmp_path):
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    reap_run(tmp_path, "run-leased", "start", "ceiling_wait", record_age=400)
    assert c.try_take("children", "run-leased:child-1", pid=os.getpid())
    assert stale_slots(tmp_path, c) == []


# ---- #1270 (review): the holder the pass names is never a run whose named engine lives ----
def test_a_holder_whose_named_engine_is_alive_is_never_printed(tmp_path):
    """`--slots` read a run whose record names a live engine as "no live process" when the engine's cwd sat outside the run
    folder. The named pid is the run's own process: while it lives (same_process), the run is not named."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    reap_run(tmp_path, "run-named", "start", (os.getpid(), time.time()), record_age=400)
    assert stale_slots(tmp_path, c, procs=[]) == []


def test_a_holder_whose_named_engine_is_dead_holds_no_slot_and_is_never_named(tmp_path):
    """A run whose record names a dead engine holds no slot at all: the counter frees it at the count (ADR-0167), so a full
    ceiling is never the dead run's and the pass names only the holder that is left. The killed verdict and the comment stay
    the janitor's own work."""
    c = caps.Ceiling({**FOUR, "runs": 1}, tmp_path / "leases")
    reap_run(tmp_path, "run-idle", "start", "ceiling_wait", record_age=400)
    reap_run(tmp_path, "run-gone", "start", (dead_pid(), time.time() - 7200), record_age=400)
    assert [name for name, _ in stale_slots(tmp_path, c, procs=[])] == ["run-idle"]


def test_a_dashboard_server_in_the_folder_is_no_live_process_for_the_pass(tmp_path):
    """LIVE_PROC reads the engine, a gate and a session. `python3 -m squad.dashboard_server` is none of them, so a dashboard
    left inside an idle pid-less run does not keep the run alive, and the reap gives it the killed verdict."""
    from squad import reap
    c = caps.Ceiling(FOUR, tmp_path / "leases")
    run = reap_run(tmp_path, "run-dash", "start", "ceiling_wait", record_age=400)
    dash = [(os.getpid(), str(run), "python3 -c import time -m squad.dashboard_server " + str(run) + " --idle 7200")]
    assert reap.LIVE_PROC.search(dash[0][2]) is None
    assert reap_pass(tmp_path, c, dash) == ["run-dash"]


def test_a_squad_start_engine_in_the_folder_is_a_live_process(tmp_path):
    from squad import reap
    cmd = "/Users/x/agent-squad/.venv/bin/python3 /Users/x/agent-squad/.venv/bin/squad start --workflow default"
    assert reap.LIVE_PROC.search(cmd)


def test_the_janitor_zombie_and_fix_run_rules_read_the_same_live_pattern_as_reap():
    """The janitor runs as a bare script (no `squad` package beside it), so it carries the pattern as a copy. A copy that
    drifts from squad/reap.py LIVE_PROC would read a process one way in the health pass and another in the janitor."""
    from squad import reap
    src = (Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/janitor.py").read_text()
    assert f'LIVE = re.compile(r"{reap.LIVE_PROC.pattern}")' in src


def test_a_run_packs_two_children_with_no_live_gate_and_six_with_one(tmp_path, monkeypatch):
    """(#2544) The pack of one run follows the gate load: 2 while no gate lease is live, 6 while one or more is."""
    monkeypatch.delenv("SG_PACK_IDLE", raising=False)
    monkeypatch.delenv("SG_PACK_BUSY", raising=False)
    c = caps.Ceiling({"runs": 10, "children": 20, "gates": 3}, tmp_path / "leases")
    assert caps.pack(c) == (2, 0)
    assert c.try_take("gates", "gate-1") is not None
    assert caps.pack(c) == (6, 1)


def test_the_pack_reads_its_two_numbers_from_the_environment(tmp_path, monkeypatch):
    c = caps.Ceiling({"runs": 10, "children": 20, "gates": 3}, tmp_path / "leases")
    monkeypatch.setenv("SG_PACK_IDLE", "1")
    monkeypatch.setenv("SG_PACK_BUSY", "4")
    assert caps.pack(c) == (1, 0)
    c.try_take("gates", "gate-1")
    assert caps.pack(c) == (4, 1)
    monkeypatch.setenv("SG_PACK_BUSY", "0")
    with pytest.raises(caps.CapsError, match="SG_PACK_BUSY"):
        caps.pack(c)


def test_the_resolution_is_kept_while_its_two_files_are_unchanged(tmp_path, monkeypatch):
    """AC F of #2582: an unchanged pair of files answers from the memo, and a changed file builds the resolution again."""
    proj = project(tmp_path, runs=1)
    calls = []
    real_d, real_p = defaults.load, caps.P.load
    monkeypatch.setattr(defaults, "load", lambda p: (calls.append("defaults"), real_d(p))[1])
    monkeypatch.setattr(caps.P, "load", lambda t: (calls.append("project"), real_p(t))[1])
    monkeypatch.setattr(defaults, "digest", lambda p: (calls.append("digest"), "d")[1])
    assert caps.resolve(proj)["runs"] == 1
    first = sorted(calls)
    assert caps.resolve(proj)["runs"] == 1
    assert sorted(calls) == first, f"an unchanged pair of files read again: {calls}"
    (proj / "docs" / "squad" / "defaults.yaml").write_text(yaml.safe_dump({"caps": {"runs": 2}}))
    assert caps.resolve(proj)["runs"] == 2, "a changed project file answered the resolution before it"
    assert calls.count("project") == 2 and calls.count("digest") == 2
