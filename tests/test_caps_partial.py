"""Issue #734: a gate takes the slots that are free, not six or nothing.

`caps.gates` is 12 and a gate asks for 6. Two gates fill the budget, and a third waited within its bound and exited 75, so a hand
gate needed three attempts to run its suite. A take is still one locked step: a holder never holds part of its slots while it waits
for the rest. With fewer than two slots free there is no take, so the caller waits exactly as before.

Guard: this file and `contrib/squad-goal/scripts/test-gate-lock.sh`.
"""
import os
import subprocess
import sys
from pathlib import Path

from squad import caps

ROOT = Path(__file__).resolve().parent.parent
FOUR = {"runs": 10, "children": 20, "gates": 12, "reviews": 4}


def live_pid():
    """A pid that lives while the test runs, so its leases are not reclaimed."""
    p = subprocess.Popen(["sleep", "30"])
    return p, p.pid


def ceiling(tmp_path):
    return caps.Ceiling(FOUR, tmp_path / "leases")


def test_four_free_slots_answer_a_take_of_six_with_four(tmp_path):
    """A third gate runs with 4 workers instead of giving up."""
    holder, pid = live_pid()
    try:
        c = ceiling(tmp_path)
        assert len(c.try_take_many("gates", "gate-a", 6, pid=pid)) == 6
        assert len(c.try_take_many("gates", "gate-b", 2, pid=pid)) == 2     # 8 held, 4 free
        assert len(c.try_take_many("gates", "gate-c", 6, pid=pid)) == 4     # takes the 4 that are free
        assert c.occupancy()["gates"] == 12
    finally:
        holder.kill()


def test_one_free_slot_is_no_take_and_holds_nothing(tmp_path):
    """Below two free the rule is unchanged: the gate waits, and it holds no slot while it waits."""
    holder, pid = live_pid()
    try:
        c = ceiling(tmp_path)
        assert len(c.try_take_many("gates", "gate-a", 11, pid=pid)) == 11
        assert c.try_take_many("gates", "gate-b", 6) is None
        assert c.occupancy()["gates"] == 11
    finally:
        holder.kill()


def test_a_take_that_fits_is_whole_and_a_short_take_takes_the_free_slots(tmp_path):
    holder, pid = live_pid()
    try:
        c = ceiling(tmp_path)
        assert len(c.try_take_many("gates", "gate-a", 6, pid=pid)) == 6
        assert len(c.try_take_many("gates", "gate-b", 4, pid=pid)) == 4     # it fits: taken whole
        assert len(c.try_take_many("gates", "gate-c", 7, pid=pid)) == 2     # 2 free is short of 7: taken
        assert c.occupancy()["gates"] == 12
    finally:
        holder.kill()


def test_the_take_command_prints_the_count_it_took(tmp_path):
    """gate.sh reads that count into SQUAD_GATE_WORKERS, so the battery runs with the slots the gate holds."""
    holder, pid = live_pid()
    try:
        c = ceiling(tmp_path)
        assert len(c.try_take_many("gates", "other", 8, pid=pid)) == 8
        e = {**os.environ, "SQUAD_CAPS_DIR": str(tmp_path / "leases"), "PYTHONPATH": str(ROOT)}
        r = subprocess.run([sys.executable, "-m", "squad.caps", "take", "gates", "gate-734", "--count", "6", "--pid", str(pid),
                            "--target", str(tmp_path)], capture_output=True, text=True, env=e, cwd=tmp_path, timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "4", r.stdout + r.stderr
    finally:
        holder.kill()


# Issue #2049, ADR-0174: a waiting tip check or landing check goes first in the queue. While it waits it writes a priority
# waiting marker, and a take by any other holder takes no slot while that marker's holder lives.
THREE = {"runs": 10, "children": 20, "gates": 3, "reviews": 4}


def test_a_waiting_tip_check_takes_the_freed_slots_before_a_fourth_child_gate(tmp_path):
    holder, pid = live_pid()
    try:
        c = caps.Ceiling(THREE, tmp_path / "leases")
        kids = [c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3)]
        assert all(kids) and c.occupancy()["gates"] == 3
        assert c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip") is None
        c.release_many(kids[0])                                                # one slot frees
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None    # the fourth child gate waits
        assert c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip") is None   # one free slot is no take
        c.release_many(kids[1])                                                # a second slot frees
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None
        assert c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip") is None   # two free is no take (#2060)
        c.release_many(kids[2])
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None    # the third slot stays for the tip check
        assert len(c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip")) == 3   # the whole count
        c.release_holder("gates", "gate-tip")
        assert c.waiting() == []                                               # its release takes the marker with it
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is not None   # no waiter: the child gate takes a slot
    finally:
        holder.kill()


def test_the_fourth_child_gate_waits_until_the_tip_check_releases_its_slots(tmp_path):
    holder, pid = live_pid()
    try:
        c = caps.Ceiling(THREE, tmp_path / "leases")
        kids = [c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3)]
        assert c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip") is None
        for k in kids:
            c.release_many(k)
            assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None
        tip = c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip")
        assert len(tip) == 3
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None    # the budget is full
        c.release_holder("gates", "gate-tip")
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is not None
    finally:
        holder.kill()


def test_with_no_waiting_tip_check_child_gates_take_slots_as_today(tmp_path):
    holder, pid = live_pid()
    try:
        c = caps.Ceiling(THREE, tmp_path / "leases")
        assert all(c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3))
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None    # full: it waits, as today
        c.release_holder("gates", "child-gate-1")
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is not None
    finally:
        holder.kill()


def test_the_waiting_marker_names_the_tip_check_as_the_next_holder(tmp_path):
    holder, pid = live_pid()
    try:
        c = caps.Ceiling(THREE, tmp_path / "leases")
        assert all(c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3))
        assert c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip") is None
        assert c.waiting() == [{"ceiling": "caps.gates", "holder": "gate-tip", "next": "tip"}]
        c.release_holder("gates", "gate-tip")                                  # a waiter that gives up takes its marker away
        assert c.waiting() == []
    finally:
        holder.kill()


def test_a_dead_waiter_never_blocks_a_child_gate(tmp_path):
    dead = subprocess.Popen(["true"])
    dead.wait()
    c = caps.Ceiling(THREE, tmp_path / "leases")
    assert c.try_take_many("gates", "gate-tip", 3, pid=dead.pid, priority="tip") is not None   # free: taken, no marker
    c.release_holder("gates", "gate-tip")
    holder, pid = live_pid()
    try:
        assert all(c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3))
        assert c.try_take_many("gates", "gate-tip", 3, pid=dead.pid, priority="tip") is None    # the marker names a dead pid
        c.release_holder("gates", "child-gate-1")
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is not None
    finally:
        holder.kill()


def test_the_take_command_writes_the_priority_marker_and_a_child_take_names_it(tmp_path):
    holder, pid = live_pid()
    try:
        c = caps.Ceiling(THREE, tmp_path / "leases")
        assert all(c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3))
        e = {**os.environ, "SQUAD_CAPS_DIR": str(tmp_path / "leases"), "PYTHONPATH": str(ROOT)}
        proj = tmp_path / "proj" / "docs" / "squad"
        proj.mkdir(parents=True)
        (proj / "defaults.yaml").write_text("caps:\n  gates: 3\n")

        def take(*a):
            return subprocess.run([sys.executable, "-m", "squad.caps", "take", "gates", *a, "--pid", str(pid), "--try",
                                   "--target", str(tmp_path / "proj")], capture_output=True, text=True, env=e, timeout=60)
        r = take("gate-tip", "--count", "3", "--priority", "tip")
        assert r.returncode == 73, r.stderr
        assert c.waiting() == [{"ceiling": "caps.gates", "holder": "gate-tip", "next": "tip"}]
        c.release_holder("gates", "child-gate-1")
        r = take("child-gate-4", "--count", "1")
        assert r.returncode == 73 and "gate-tip" in r.stderr, r.stderr
    finally:
        holder.kill()


def test_a_priority_check_takes_the_whole_count_or_none(tmp_path):
    """(#2060) A tip or landing check never starts on part of its slots: two free of three is no take, and it keeps its
    marker, so no child takes the third slot; the take succeeds when all three are free."""
    holder, pid = live_pid()
    try:
        c = caps.Ceiling(THREE, tmp_path / "leases")
        kids = [c.try_take_many("gates", f"child-gate-{i}", 1, pid=pid) for i in (1, 2, 3)]
        c.release_many(kids[0])
        c.release_many(kids[1])
        assert c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip") is None      # two free: no part take
        assert c.try_take_many("gates", "child-gate-4", 1, pid=pid) is None                  # the marker keeps them
        c.release_many(kids[2])
        tip = c.try_take_many("gates", "gate-tip", 3, pid=pid, priority="tip")
        assert tip is not None and len(tip) == 3 and c.waiting() == []
    finally:
        holder.kill()
