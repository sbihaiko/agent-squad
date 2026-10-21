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
