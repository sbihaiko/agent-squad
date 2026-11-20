"""The machine slots read (#2435): runs and gate workers, their cap, the slots in use and the holder of each slot.

A slot is one unit of the machine-wide ceiling (`squad/caps.py`): `caps.runs` live runs, `caps.gates` gate worker slots.
The read names the holder of every slot, the runs' issues where their claim line names them, and the number of loop gates
that fit at once. Children and reviews are file-only settings, named with their values. The read never writes: the hub
observes the ceiling and reaps nothing (ADR-0162), so a dead lease stays for the next count.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from squad import defaults, machine_slots


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("SG_GATE_WORKERS", raising=False)   # the loop exports a count of its own: the suite reads the default
    cfg = tmp_path / "agent-defaults.yaml"
    cfg.write_text(yaml.safe_dump(defaults.load()))
    proj = tmp_path / "proj"
    (proj / "docs" / "squad").mkdir(parents=True)
    (proj / "docs" / "squad" / "defaults.yaml").write_text(yaml.safe_dump({"caps": {"runs": 4, "gates": 3}}))
    caps_dir = tmp_path / "caps"
    return proj, cfg, caps_dir


def _lease(caps_dir, kind, stem, holder, pid=None):
    d = caps_dir / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.json").write_text(json.dumps({"holder": holder, "pid": pid or os.getpid(), "since": time.time()}))


def _dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def _read(proj, cfg, caps_dir, **kw):
    return machine_slots.read(str(proj), folder=caps_dir, defaults_path=cfg, **kw)


def test_the_read_names_each_run_slot_its_cap_and_its_holder(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "runs", "run-a", "run-a")
    got = _read(proj, cfg, caps_dir)
    assert got["runs"]["cap"] == 4
    assert got["runs"]["in_use"] == 1
    assert [h["name"] for h in got["runs"]["holders"]] == ["run-a"]


def test_a_run_holder_carries_the_issues_its_claim_line_names(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "runs", "run-a", "run-a")
    req = proj / "runs" / "run-a" / "request.txt"
    req.parent.mkdir(parents=True)
    req.write_text("CHILD — GitHub issue(s) 31, 32 (both):\nfix them\n")
    got = _read(proj, cfg, caps_dir)
    assert got["runs"]["holders"] == [{"name": "run-a", "issues": [31, 32]}]


def test_the_read_names_every_gate_worker_slot_and_its_holder(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "gates", "gate-11.slot0", "gate-11")
    _lease(caps_dir, "gates", "gate-11.slot1", "gate-11")
    got = _read(proj, cfg, caps_dir)
    assert got["gates"]["cap"] == 3
    assert got["gates"]["in_use"] == 2
    assert got["gates"]["holders"] == ["gate-11", "gate-11"]
    assert got["gates"]["gates_running"] == 1


def test_a_child_gate_or_a_day_suite_is_no_loop_gate_running(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "gates", "gate-11.slot0", "gate-11")
    _lease(caps_dir, "gates", "cg.slot0", "child-gate-999")
    _lease(caps_dir, "gates", "ds.slot0", "daysuites-999")
    got = _read(proj, cfg, caps_dir)
    assert got["gates"]["in_use"] == 3, "every live lease holds a slot"
    assert got["gates"]["gates_running"] == 1, "one holder is one loop gate, and only `gate-` names one"


def test_the_gate_row_divides_the_cap_by_the_workers_of_one_gate(world):
    proj, cfg, caps_dir = world
    got = _read(proj, cfg, caps_dir)
    assert got["gates"]["workers"] == machine_slots.DEFAULT_GATE_WORKERS
    assert got["gates"]["gates_at_once"] == 1, "3 slots of 6 workers per gate fit one gate at a time"
    assert got["gates"]["slots_per_gate"] == 3, "a gate takes min(6 workers, 3 slots) = 3 slots"
    assert _read(proj, cfg, caps_dir, workers=2)["gates"]["gates_at_once"] == 1
    (proj / "docs" / "squad" / "defaults.yaml").write_text(yaml.safe_dump({"caps": {"runs": 4, "gates": 6}}))
    assert _read(proj, cfg, caps_dir, workers=2)["gates"]["gates_at_once"] == 3


def test_children_and_reviews_are_named_with_their_file_values(world):
    proj, cfg, caps_dir = world
    got = _read(proj, cfg, caps_dir)
    assert got["children"] == 20 and got["reviews"] == 4
    assert got["file"].endswith(str(Path("docs") / "squad" / "defaults.yaml"))


def test_the_read_reaps_no_lease(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "gates", "gate-dead", "gate-dead", pid=_dead_pid())
    got = _read(proj, cfg, caps_dir)
    assert (caps_dir / "gates" / "gate-dead.json").exists(), "the read reaped a lease it only observes"
    assert got["gates"]["in_use"] == 0
    assert got["gates"]["holders"] == []


def test_the_read_leaves_a_dead_run_lease_for_the_next_count(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "runs", "run-dead", "run-dead", pid=_dead_pid())
    got = _read(proj, cfg, caps_dir)
    assert (caps_dir / "runs" / "run-dead.json").exists(), "the read reaped a run lease it only observes"
    assert got["runs"]["in_use"] == 0


def test_a_gate_lease_held_by_a_page_server_counts_no_slot(world):
    proj, cfg, caps_dir = world
    page = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)", "squad.dashboard_server"])
    try:
        time.sleep(0.3)   # let the process table name its command line
        _lease(caps_dir, "gates", "gate-page.slot0", "gate-page", pid=page.pid)
        got = _read(proj, cfg, caps_dir)
    finally:
        page.kill()
        page.wait()
    assert got["gates"]["in_use"] == 0, "a page server idles, so its lease holds no gate slot"
    assert got["gates"]["holders"] == []


def test_a_number_the_claim_prose_names_is_no_claimed_issue(world):
    proj, cfg, caps_dir = world
    _lease(caps_dir, "runs", "run-a", "run-a")
    req = proj / "runs" / "run-a" / "request.txt"
    req.parent.mkdir(parents=True)
    req.write_text("CHILD — GitHub issue(s) 31, 32 (both):\n2 cards, fix them\n")
    got = _read(proj, cfg, caps_dir)
    assert got["runs"]["holders"] == [{"name": "run-a", "issues": [31, 32]}]


def test_the_read_names_the_plugin_ceiling_of_each_cap(world):
    proj, cfg, caps_dir = world
    got = _read(proj, cfg, caps_dir)
    assert got["ceiling"] == {"runs": 10, "children": 20, "gates": 12, "reviews": 4}
