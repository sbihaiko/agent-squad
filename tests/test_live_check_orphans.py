"""Guard: issue #1660. The live-check suite drives the real door, and the door takes its serial slot with a watchdog that
sleeps out the whole hold (the window plus five minutes, 3 900 s at the live default). A case that ends while the watchdog
sleeps leaves a `serial.sh live-check` tree behind, orphaned to pid 1, and every parallel worker of a suite run adds its own:
the machine carried 29 roots and 60 processes. The suite must own every process it starts and leave none behind.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "contrib" / "squad-goal" / "scripts" / "test-live-check.sh"


@pytest.mark.heavy
@pytest.mark.slow(reason="runs the live-check shell suite, a real script whose wall time is the machine's")
def test_the_live_check_suite_leaves_no_holder_behind():
    done = subprocess.run(["bash", str(SUITE)], capture_output=True, text=True, timeout=180, check=False)
    assert done.returncode == 0, f"the suite failed:\n{done.stdout[-2000:]}{done.stderr[-1000:]}"
    ps = subprocess.run(["/bin/ps", "ax", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=False)
    left = [line.strip() for line in ps.stdout.splitlines() if "serial.sh live-check" in line and str(ROOT) in line]
    assert not left, "the suite left a live-check holder behind:\n" + "\n".join(left[:6])
