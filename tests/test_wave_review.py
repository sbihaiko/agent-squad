"""Issue #376: the review reads each child's diff against the wave branch, and a child it asked changes of never enters.

The two shell suites beside the scripts are the behavior, run here as the targeted pytest half. `test-branchreview.sh`
drives review.sh and branchreview.sh over a bare local remote and a stub `gh`/`claude`: with SG_REVIEW_BASE set to the wave
branch the three-dot compare and the prompt carry only the child's own commits, the base is kept beside the verdict, and the
verdict still lands in the issue's one status comment. `test-wave-entry.sh` drives wave.sh: a child whose review asked for
changes, a review read against `dev` (the whole wave), a review of an older tip, and a child with no review are all held out
of the wave branch. Both run offline, in throwaway repos, and take a few seconds, so each call is marked heavy and carries a slow
reason: a real script suite's wall time is the machine's, never the test's own work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import shell_suites

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


def _suite(name: str):
    return shell_suites.run(SCRIPTS / name)


@pytest.mark.slow(reason="test-branchreview.sh runs a real review suite over throwaway repositories, and its own wall time is the machine's")
@pytest.mark.heavy
def test_the_review_reads_the_child_diff_against_the_wave_branch() -> None:
    r = _suite("test-branchreview.sh")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow(reason="test-wave-entry.sh runs the entry cases over throwaway repositories, more than 20 s on a loaded machine")
@pytest.mark.heavy
def test_a_changes_requested_child_never_enters_the_wave_branch() -> None:
    r = _suite("test-wave-entry.sh")
    assert r.returncode == 0, r.stdout + r.stderr
