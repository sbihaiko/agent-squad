"""One issue is dispatched once across both dispatchers (#428).

One claim namespace, and one read of the live child requests, so the bug pass and the frontier pass cannot send the
same issue into a second run. The shell suite counts the request forms a live child may carry, hashed or not.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


@pytest.mark.heavy
def test_the_two_dispatchers_share_one_claim_and_read_live_requests() -> None:
    r = subprocess.run(
        ["bash", str(SCRIPTS / "test-dispatch-mutex.sh")], capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout + r.stderr
