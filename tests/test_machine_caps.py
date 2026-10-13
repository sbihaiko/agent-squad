"""The machine-wide ceiling: `caps.runs` live runs and `caps.children` live dispatch children, across every repository and run.

This pair is not the per-run pair (`run.max_parallel`, dispatch `max_children`). Filling the ceiling queues; a wait whose bound
expires fails by name. A lease whose holder is dead is reclaimed when the slots are counted.
"""
import json
import os
import subprocess

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
    assert cfg["caps"] == {"runs": 10, "children": 20}
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
    assert waits == [{"ceiling": "caps.runs", "cap": 1, "live": 1, "holder": "late"}]


def make(tmp_path, ceiling, script, tweak=None, **kw):
    d = router()
    d["run"]["deadline"] = 7200
    if tweak:
        tweak(d)
    return Engine(from_doc(d), ScriptedAdapter(script), tmp_path / "run", target=sibling_repo(tmp_path), ceiling=ceiling, **kw)


def events(tmp_path, kind):
    return [e for e in read(tmp_path / "run" / "record.jsonl") if e["event"] == kind]


@pytest.mark.anyio
async def test_a_child_over_the_ceiling_waits_and_starts_when_a_slot_frees(tmp_path):
    c = caps.Ceiling({"runs": 10, "children": 20}, tmp_path / "leases", poll=0.01)
    others = [c.try_take("children", f"other-repo:{i}") for i in range(20)]
    eng = make(tmp_path, c, {"router": routed(req()), "route.router.1": CHILD})

    async def free_one():
        while not c.waiting():
            await anyio.sleep(0.01)
        c.release(others[0])

    async with anyio.create_task_group() as tg:
        tg.start_soon(free_one)
        v = await eng.run()
    assert v.verdict == "completed"
    w = [e for e in events(tmp_path, "ceiling_wait") if e["ceiling"] == "caps.children"]
    assert len(w) == 1 and w[0]["cap"] == 20 and w[0]["live"] == 20 and w[0]["holder"].endswith(":route.router.1")
    assert c.occupancy() == {"runs": 0, "children": 19, "waiting": []}      # the run and its child gave their slots back


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

    async def free_one():
        while not c.waiting():
            await anyio.sleep(0.01)
        c.release(others[-1])

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
