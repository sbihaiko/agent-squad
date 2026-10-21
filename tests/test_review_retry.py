"""#635: an in-run review that gets no verdict (exit 5, "try again") is tried again inside the run, within the gate's deadline.

Each case drives a run end to end with a stub review command whose exit changes by try. The stub logs one line per try; the
test compares the recorded tries. When the deadline runs out, the child is held (`review_deadline`), never merged unreviewed.
"""
import os
import statistics
import subprocess
import time

import pytest

from squad import gates, git
from squad.runners import dispatch as D
from tests.gitrepo import sibling_repo
from tests.test_dispatch import ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

CHILD = "route.router.1"


def stub(tmp_path, codes, sleep=0):
    """A review command: it logs its round to review.log, sleeps, and exits with the code of its try (`codes[try - 1]`;
    the last code repeats)."""
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\necho "$SQUAD_REVIEW_ROUND" >> "{tmp_path}/review.log"\nn=$(wc -l < "{tmp_path}/review.log")\n'
                 f'echo "VERDICT: none"\nsleep {sleep}\nset -- {" ".join(str(c) for c in codes)}\n'
                 f'while [ "$n" -gt 1 ] && [ $# -gt 1 ]; do shift; n=$((n - 1)); done\nexit $1\n')
    p.chmod(0o755)
    return str(p)


def run_with(tmp_path, codes, deadline=30, sleep=0):
    repo = sibling_repo(tmp_path / "t")
    base = git.head(repo)
    eng = make(tmp_path, {"router": routed(req(task="issue #635")), CHILD: writer("a.txt")},
               tweak=gated(stub(tmp_path, codes, sleep), deadline), target=repo)
    return eng, repo, base


def one_try_seconds(tmp_path, sleep):
    """One try of this stub, timed on this machine right now: the median of three, so one hiccup neither shortens nor lengthens
    the gate's deadline below. The deadline is a count of the tries this machine can run, never a wall second guessed on a
    quiet one: the case reads the tries that fit, so the runner's load decides how long it runs, never whether it passes
    (#736)."""
    p = stub(tmp_path, [5], sleep)
    times = []
    for _ in range(3):
        t0 = time.monotonic()
        done = subprocess.run([p], cwd=tmp_path, env={**os.environ, "SQUAD_REVIEW_ROUND": "1"})
        assert done.returncode == gates.REVIEW_RETRY_EXIT
        times.append(time.monotonic() - t0)
    return statistics.median(times)


TRIES_AT_THE_DEADLINE = 10.0   # the gate's deadline is this many tries of the measured machine


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(D, "REVIEW_RETRY_PAUSE_S", 0)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_no_verdict_try_is_tried_again_and_each_try_is_recorded(tmp_path):
    assert D.REVIEW_RETRY_EXIT == gates.REVIEW_RETRY_EXIT == 5
    eng, repo, base = run_with(tmp_path, [5, 5, 0])
    assert (await eng.run()).verdict == "completed"
    assert (tmp_path / "review.log").read_text().splitlines() == ["1", "1", "1"]
    tries = ev(tmp_path, "review_try")
    assert [(t["node"], t["round"], t["try"], t["review"]["verdict"]) for t in tries] == [(CHILD, 1, 1, "retry"), (CHILD, 1, 2, "retry")]
    assert [(r["round"], r["verdict"]) for r in ev(tmp_path, "review_round")] == [(1, "approve")]
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "pass" and (repo / "a.txt").exists()


@pytest.mark.anyio
@pytest.mark.heavy
async def test_tries_stop_at_the_gate_deadline_and_the_child_is_held_unmerged(tmp_path):
    deadline = one_try_seconds(tmp_path, 0.3) * TRIES_AT_THE_DEADLINE
    eng, repo, base = run_with(tmp_path, [5], deadline=deadline, sleep=0.3)
    assert (await eng.run()).verdict == "failed"
    tries = ev(tmp_path, "review_try")
    assert len(tries) >= 2, f"only {len(tries)} try fit the gate's deadline of {deadline:.1f} s"
    # every try but the last started while its round had time left; only the last may run past what remained
    assert sum(t["review"]["elapsed"] for t in tries[:-1]) < deadline
    assert [r["verdict"] for r in ev(tmp_path, "review_round")] == ["deadline"]
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_deadline"]
    assert git.head(repo) == base and not (repo / "a.txt").exists()
