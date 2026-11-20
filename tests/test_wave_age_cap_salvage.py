"""The wave's age cap keeps the work it can, and the gate wait it spends is no wave time (#2231, ADR-0145 rule 13, ADR-0166).

Issue #2231: a run whose child committed a RED test and the fix reached the age cap while it waited on gate slots. The
wave landed empty, the child's commits reached no remote branch, and the issue was requeued to build the same work again.
Three rules answer that:

- A child whose commits exist when the cap ends its run keeps them: the cap pushes its head to a work branch
  (`fix/<n>-salvage-<run>`, the one a run with no wave already pushes), so `branchland.sh` reviews and lands it, and the
  frontier skips the issue while that branch is open.
- A child with no commit keeps today's path: the issue goes back to the queue.
- The time a child waits for a gate slot is no run time (ADR-0166), so the wave's age does not count it.
- A second gate call on one tree in one child reuses the first verdict (ADR-0168), so the child runs its gate once.

The fixtures here are throwaway repositories, a bare local remote, a stub `gh` and a stub board: no GitHub call is made.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Each case drives wave.sh through a clone and a push: about 2 s on an idle machine, and more under a gate's load.
pytestmark = pytest.mark.heavy

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
GIT = "/usr/bin/git"
# The scripts wave.sh and its landing path read beside themselves, as the split wave suites copy them into their own directory.
COPY = (
    "wave.sh", "wavepause.py", "issue_ledger.py", "ghread.py", "child_issues.py", "address.sh", "gate.sh", "caps.sh",
    "serial.sh", "ghcall.sh", "drainpolicy.py", "base-battery.sh", "basecheck.sh", "fast-gate.sh", "quality-reading.py",
    "siblings.py", "cluster.py", "autohunt.sh", "revformat.py", "workbranches.sh", "columnlimit.sh", "issue_state.py",
)


def _run(argv, cwd, env, timeout=120):
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)


# The fixture's git carries its own identity (#2247): a runner's config names none, and git there refuses an
# auto-detected address, so a setup commit that read the host's config would fail and the remote would carry no `dev`.
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "a@b", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "a@b"}


def _git(repo, *args, env=None):
    return _run([GIT, "-C", str(repo), *args], ROOT, env or {**os.environ, **IDENTITY})


def _scripts(tmp: Path) -> Path:
    s = tmp / "s"
    s.mkdir()
    for name in COPY:
        shutil.copy2(SCRIPTS / name, s / name)
    return s


def _board(tmp: Path) -> Path:
    b = tmp / "board.sh"
    b.write_text(f'#!/bin/bash\necho "$*" >> "{tmp}/board.log"\nexit 0\n')
    b.chmod(0o755)
    return b


def _gh(tmp: Path) -> Path:
    g = tmp / "gh"
    g.write_text(f'#!/bin/bash\necho "$*" >> "{tmp}/gh.log"\ncase "$*" in *"issues/"*) echo \'{{"state":"open","labels":[]}}\';; *) echo \'{{}}\';; esac\n')
    g.chmod(0o755)
    return g


class Fixture:
    """One loop fixture: a bare remote with dev, a checkout, a run folder and its child worktree."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.s = _scripts(tmp)
        self.st = tmp / "st"
        (self.st / "auto").mkdir(parents=True)
        (self.st / "cq").mkdir()
        self.origin = tmp / "origin.git"
        self.co = tmp / "co"
        self.board = _board(tmp)
        self.gh = _gh(tmp)
        env = {
            **os.environ,
            "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
            **IDENTITY,
        }
        env.update(SG_STATE=str(self.st), SG_REPO="owner/repo", SG_BASE="dev", SG_OWNER="o", SG_PROJECT="3",
                   SG_CHECKOUT=str(self.co), BOARD=str(self.board), SG_LAND_REMOTE=str(self.origin),
                   SG_WAVE_TIP_GATES="true", SG_WAVE_ENTRY_GATE="on", SG_BOARD_VOCAB="shipped",
                   SG_RETIRE_CMD="/usr/bin/true", SG_SQUAD=str(ROOT))
        self.env = env
        _git(tmp, "init", "-q", "-b", "dev", str(self.co))
        (self.co / "f").write_text("x\n")
        _git(self.co, "add", "f")
        _git(self.co, "commit", "-q", "-m", "init")
        _run([GIT, "clone", "-q", "--bare", str(self.co), str(self.origin)], ROOT, env)
        # The checkout tracks the bare remote's dev, as the loop's checkout does: salvage measures `origin/dev..HEAD`.
        _run([GIT, "remote", "add", "origin", str(self.origin)], self.co, env)
        _git(self.co, "fetch", "-q", "origin", env=env)
        _git(self.co, "branch", "-q", "--set-upstream-to=origin/dev", "dev", env=env)
        # env.sh is the profile wave.sh sources: the fixture's own, never the live loop's.
        (self.s / "env.sh").write_text(
            f'export SG_STATE="{self.st}" SG_REPO=owner/repo SG_BASE=dev SG_OWNER=o SG_PROJECT=3\n'
            f'export SG_CHECKOUT="{self.co}" BOARD="{self.board}"\n'
            f'export SG_LAND_REMOTE="{self.origin}" SG_WAVE_TIP_GATES=true SG_WAVE_ENTRY_GATE=on\n'
            f'export SG_BOARD_VOCAB=shipped SG_RETIRE_CMD=/usr/bin/true SG_WAVE_MAX_AGE_SECONDS=3600\n'
            f'. "{self.s}/address.sh"\n'
        )

    def wave(self, *args):
        return _run(["bash", str(self.s / "wave.sh"), *args], self.co, self.env)

    def start_capped_wave(self, run: str) -> None:
        r = self.wave("start", run)
        assert r.returncode == 0, r.stdout + r.stderr
        (self.st / "auto" / f"wave-start-{run}").write_text(f"{int(time.time()) - 7201}\n")

    def run_folder(self, run: str) -> Path:
        rd = self.co / "runs" / run
        (rd / "ws").mkdir(parents=True, exist_ok=True)
        return rd

    def record(self, run: str, child: str, issue: int) -> Path:
        rd = self.run_folder(run)
        line = {"event": "spawn_accepted", "child": child,
                "definition": {"prompt": f"GitHub issue #{issue} work", "workspace": "target"}}
        (rd / "record.jsonl").write_text(json.dumps(line) + "\n")
        return rd

    def child_worktree(self, run: str, name: str, commit: bool) -> Path:
        wt = self.run_folder(run) / "ws" / f"target-{name}"
        _git(self.co, "worktree", "add", "-q", "-b", f"child/{run}-{name}", str(wt), "dev")
        if commit:
            _git(wt, "commit", "-q", "--allow-empty", "-m", "TDD RED: a case that fails", "-m", "Refs #501")
            _git(wt, "commit", "-q", "--allow-empty", "-m", "the fix", "-m", "Refs #501")
        return wt

    def land(self, run: str):
        rd = self.co / "runs" / run
        return _run(["bash", str(self.s / "wave.sh"), "land", run, str(rd)], self.co, self.env)


def test_a_committed_clean_child_keeps_its_commits_on_a_work_branch(tmp_path: Path) -> None:
    """Criterion A: the cap pushes a committed child's head to a remote work branch, and the frontier skips the issue."""
    fx = Fixture(tmp_path)
    run = "run-a"
    fx.start_capped_wave(run)
    (fx.st / "auto" / "disp-501").write_text(f"{run}\n")
    fx.record(run, "c", 501)
    wt = fx.child_worktree(run, "c", commit=True)
    head = _git(wt, "rev-parse", "HEAD").stdout.strip()
    r = fx.land(run)
    assert r.returncode == 0, r.stdout + r.stderr
    branch = f"fix/501-salvage-{run}"
    got = _git(fx.origin, "rev-parse", "-q", "--verify", f"refs/heads/{branch}")
    assert got.returncode == 0, f"the child's commits reached no work branch:\n{r.stdout}{r.stderr}"
    assert got.stdout.strip() == head, "the work branch does not stand on the child's head"
    # The frontier's fact: an open work branch names the issue, so the next pass starts no run for it.
    fact = _run(["bash", "-c", f'. "{fx.s}/workbranches.sh"; sg_work_issues "{fx.origin}"'], ROOT, fx.env)
    assert fact.stdout.split() == ["501"], f"the frontier does not read the work branch: {fact.stdout!r}{fact.stderr}"
    # The issue still goes back to the queue; the branch is what keeps it from being built again.
    assert not (fx.st / "auto" / "disp-501").exists(), "the dispatch marker outlives the capped landing"
    assert "Todo 501" in (tmp_path / "board.log").read_text()


def test_a_child_with_no_commit_goes_back_to_the_queue(tmp_path: Path) -> None:
    """Criterion B: no commit, no branch; the issue goes back to the queue as today."""
    fx = Fixture(tmp_path)
    run = "run-b"
    fx.start_capped_wave(run)
    (fx.st / "auto" / "disp-501").write_text(f"{run}\n")
    fx.record(run, "c", 501)
    fx.child_worktree(run, "c", commit=False)
    r = fx.land(run)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(fx.origin, "rev-parse", "-q", "--verify", f"refs/heads/fix/501-salvage-{run}").returncode != 0, \
        "a child with no commit got a work branch"
    assert not (fx.st / "auto" / "disp-501").exists(), "the issue did not go back to the queue"
    assert "Todo 501" in (tmp_path / "board.log").read_text()


def _expired(fx: Fixture, run: str) -> subprocess.CompletedProcess:
    return fx.wave("expired", run)


def test_gate_wait_does_not_count_against_the_wave_age_cap(tmp_path: Path) -> None:
    """Criterion C: a gate wait longer than the cap leaves the wave under its age, while a wait-free wave expires."""
    fx = Fixture(tmp_path)
    run = "run-c"
    (fx.st / "auto" / f"wave-{run}").write_text(f"wave/{run}\n")
    now = int(time.time())
    (fx.st / "auto" / f"wave-start-{run}").write_text(f"{now - 3700}\n")
    # Without a pause the wave is past its 3600 s cap.
    assert _expired(fx, run).returncode == 0, "a wave past its cap with no pause is not expired"
    # One gate that waited 300 s inside the window: the age it measures is 3700 - 300, under the cap.
    start = now - 100
    fields = ["-"] * 23
    fields[0], fields[4], fields[17] = str(start), str(start), "300"
    (fx.st / "gate-history").write_text("\t".join(fields) + "\n")
    assert _expired(fx, run).returncode == 1, "the wave counted a gate wait toward its age"
    # A child suite's wait pauses it too, from the suite's own history.
    (fx.st / "gate-history").write_text("")
    (fx.st / "child-suite-history").write_text(f"{start}\t{start}\t300\t1\t1\t0\ttest-x.sh\t-\n")
    assert _expired(fx, run).returncode == 1, "the wave counted a child suite wait toward its age"


def test_a_second_entry_gate_on_one_tree_reuses_the_verdict(tmp_path: Path) -> None:
    """Criterion D: two entry gate calls on one tree run the suite once, and the second prints the kept verdict."""
    repo = tmp_path / "r"
    _git(tmp_path, "init", "-q", "-b", "dev", str(repo))
    (repo / "docs").mkdir()
    (repo / "docs" / "x.md").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "gui/501")
    (repo / "docs" / "x.md").write_text("work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "work")
    st = tmp_path / "st"
    st.mkdir()
    ran = tmp_path / "ran.log"
    battery = tmp_path / "battery.sh"
    battery.write_text(f'#!/bin/bash\necho ran >> "{ran}"\necho "1 passed"\n')
    env = {**os.environ, "SG_STATE": str(st), "SG_FAST_BASE": "dev", "SG_FAST_CMD": f"bash {battery}",
           "SG_FAST_RETRY_CMD": "false", "SG_FAST_MEMO": "1", "SG_GATE_CALLER": "entry",
           "SG_SERIAL_HELD": "gate", "SG_FAST_LOG_DIR": str(tmp_path / "logs")}
    env.pop("GATE_REUSE_FILE", None)
    first = _run(["bash", str(SCRIPTS / "fast-gate.sh")], repo, env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert ran.read_text().split() == ["ran"], "the first entry gate did not run the battery once"
    second = _run(["bash", str(SCRIPTS / "fast-gate.sh")], repo, env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert ran.read_text().split() == ["ran"], "the second entry gate on one tree ran the suite again"
    line = [x for x in second.stdout.splitlines() if x.strip()][-1]
    assert line.startswith("fast-gate: pass ") and "memo" in line, f"the second call printed no kept verdict: {line!r}"


def test_the_fixture_stands_on_a_runner_with_no_git_identity(tmp_path: Path, monkeypatch) -> None:
    """Criterion E (#2247): a CI runner carries no user.name and user.email, so the fixture's own identity must stand.

    The fixture's setup commits read the host's git config. On a runner that config names no identity, every setup
    commit fails, the bare remote carries no `dev`, and the wave tests report a defect the code does not have.
    """
    home = tmp_path / "home"
    home.mkdir()
    # This host's config names no identity, and auto-detection is off, as on a runner whose hostname git refuses:
    # a commit that reads the host's config fails, while one that carries its own identity passes.
    (home / "gitconfig").write_text("[user]\n\tuseConfigOnly = true\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "system-gitconfig"))
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    fx = Fixture(tmp_path)
    got = _git(fx.origin, "rev-parse", "-q", "--verify", "refs/heads/dev")
    assert got.returncode == 0, f"the remote carries no dev branch, so the setup commit ran on no identity: {got.stderr}"
    r = fx.wave("start", "run-e")
    assert r.returncode == 0, r.stdout + r.stderr
