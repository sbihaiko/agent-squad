"""One issue is dispatched once across both dispatchers (#428).

One claim namespace, and one read of the live child requests, so the bug pass and the frontier pass cannot send the
same issue into a second run. The shell suite counts the request forms a live child may carry, hashed or not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import shell_suites

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


@pytest.mark.heavy
@pytest.mark.slow(reason="runs the dispatcher suite: a real shell suite over a stubbed gh")
def test_the_two_dispatchers_share_one_claim_and_read_live_requests() -> None:
    r = shell_suites.run(SCRIPTS / "test-dispatch-mutex.sh")
    assert r.returncode == 0, r.stdout + r.stderr
