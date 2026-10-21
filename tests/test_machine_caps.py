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
import types
from pathlib import Path

import anyio
import pytest
import yaml

from squad import caps, defaults
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.options import OptionError
from squad.record import read
from tests.gitrepo import sibling_repo
from tests.helpers import router
from tests.test_dispatch import CHILD, req, routed


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

    def run(argv, *a, **k):
        calls.append(argv)
        return subprocess.run(argv, *a, **k)

    monkeypatch.setattr(caps, "subprocess", types.SimpleNamespace(run=run))
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


def test_a_lease_of_a_reused_pid_is_reclaimed_and_the_ceiling_recovers(tmp_path):
    c = caps.Ceiling({"runs": 1, "children": 20}, tmp_path / "leases")
    lease = c.try_take("runs", "dead-run", pid=os.getpid())
    data = json.loads(lease.read_text())
    lease.write_text(json.dumps({**data, "since": 1.0}))
    assert c.occupancy()["runs"] == 0                    # this process started after the dead run's lease
    assert c.try_take("runs", "next-run") is not None


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
    for f in ("childcc.py", "issue_state.py", "child_issues.py", "issue_ledger.py"):
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


def test_a_review_in_childcc_never_waits_for_a_slot_while_it_holds_the_childcc_lock(tmp_path):
    script, co, env = childcc_fixture(tmp_path)
    alive = str(os.getpid())
    for i in range(4):
        assert cli(tmp_path, "take", "reviews", f"other-{i}", "--pid", alive).returncode == 0
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=co, timeout=60)
    assert time.monotonic() - t0 < 20, "childcc waited for a review slot"
    assert not (tmp_path / "reviews").exists()                                      # the review did not run this pass
    skips = [ln for ln in r.stdout.splitlines() if "caps.reviews" in ln]
    assert len(skips) == 1 and "skipped" in skips[0] and "other-0" in skips[0]
    assert r.returncode == 3                                                        # the child is left for a later pass, never landed without
    assert not (tmp_path / "st" / "auto" / "rvd-r1-c").exists()                     # no claim: the next pass reviews it


def test_a_review_in_childcc_takes_a_review_slot_and_gives_it_back(tmp_path):
    script, co, env = childcc_fixture(tmp_path)
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=co, timeout=60)
    assert (tmp_path / "reviews").read_text() == "reviewed\n", r.stdout + r.stderr
    assert json.loads(cli(tmp_path).stdout)["occupancy"]["reviews"] == 0
