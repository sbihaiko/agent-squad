"""The wave's run lifecycle (#375, ADR-0145 rules 2, 8, 9, 10, 12 and 14): the branch a run cuts, the children that enter it, and the one push that lands it.

The behaviour is proved offline by two hermetic shell suites. `contrib/squad-goal/scripts/test-wave.sh` drives the three
verbs of `wave.sh` over a bare local remote whose pre-receive hook logs every ref a push writes: what each verb does.
`contrib/squad-goal/scripts/test-wave-lifecycle.sh` drives the production paths those verbs are reached by -- the launch
(`contrib/squad-goal/scripts/launch.py`) cuts the run's wave branch, the pass watch.sh runs over every live run
(`contrib/squad-goal/scripts/childcc.py`) makes a finished child enter it, and the loop's own cycle
(`contrib/squad-goal/scripts/watch.sh`) lands the wave of a run that ended, after its finished children are in it -- so a
verb no production path calls fails here instead of passing in a suite that calls it by hand.

What this file adds is that both suites really ran the cases they claim -- a suite that printed no failure because it was
never reached is the one way a green suite can lie -- and that `land` has a production caller whose order is right, which
neither verb suite can see: the loop's own cycle is what calls it when a run ends, and the landing suite calls it by hand.
The order is right in two senses, and the lifecycle suite proves both: the run's finished children enter its wave before
that landing, and a run whose entry pass is running elsewhere is left unread, so the next cycle lands a wave its children
are in rather than one they were added to afterwards.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
WAVE = SCRIPTS / "wave.sh"
# The cases the lifecycle suite proves through the production entry points: the launch cuts the run's wave branch, and the
# child-completion pass enters a finished child into it. Each one fails when the wiring it reads is removed (#375 review).
LIFECYCLE_CASES = (
    "L1: the run's launch cuts its wave branch from dev",
    "L1: the cut leaves the marker the run's completion lands",
    "L1: the request the engine got does not tell the child to push its branch",
    "L1: the request says the child's branch stays local",
    "L2: the child's completion enters the wave branch",
    "L2: the work of the child is in the wave branch",
    "L2: the card of the child that entered reads Code Complete",
    "L2: the entry is what moved the wave tip",
    "L3: a child whose gates fail never enters the wave",
    "L3: a child that did not enter moves no card to Code Complete",
    "L3: the issue of a child that did not enter is back in the queue",
    "L6: a child that failed its gates enters on the next pass",
    "L6: its card reads Code Complete once it entered",
    "L7: a finished run's child enters its wave whatever column its card is in",
    "L4: the case runs on the loop's first start, with no watch-seen.txt",
    "L4: the run's finished child enters the wave before the completion lands it",
    "L4: the first start announces no verdict (boot=1), and the landing above is still reached",
    "L4: the completion of a finished run lands its child's work in dev",
    "L4: the work of the landed child is in dev",
    "L4: the wave branch goes once its work is in dev",
    "L4: the issue of the landed child is closed through the API",
    "L4: the card of the landed child reads Done",
    "L5: a run whose entry pass runs elsewhere is not landed",
    "L5: the run stays unread, so its completion is reached again",
    "L5: the wave of a run that waits is still there",
    "L5: the next cycle lands the wave its child is in",
)
# One case per acceptance criterion of #375, in the suite's own words.
LAND_CASES = (
    "A: start records the run's wave for its completion",
    "I: the wave of two children holds both",
    "I: the wave lands in dev with one push",
    "I: the moved dev is rebased onto before the push",
    "I: the landing clears the run's wave marker",
    "I: each issue of the wave is closed through the API",
    "I: each issue of the wave has its one delivery comment queued",
    "I: each card of the wave reads Done",
    "I: no child branch is pushed to the remote, ever",
    "E2: a refused push repeats from the fetch and the wave lands",
    "E2: the base that moved between the rebase and the push is in dev",
    "E3: the refused close is retried once",
    "E3: no delivery comment is queued for an issue that did not close",
    "E4: an issue named in a commit body is closed, once",
    "E4: the card of the issue named in a body reads Done",
    "E5: the issue of a commit the base already held is not closed",
    "E5: the ledger carries no row for it",
)
# The cases the run-completion suite proves: the cycle that reads a run's verdict is what calls the landing pass.
COMPLETION_CASES = (
    "the run that ended lands its wave, once",
    "a run that cut no wave is left alone",
    "a run already read is not landed again",
    "a landing that failed is retried on the next tick",
    "a run whose landing failed stays unread",
)


class Wave:
    """One wave over a bare local remote: the production `wave.sh` run for real, with a stub `gh` and board, so a case reads
    the exit code, the remote's refs and the ledger the landing wrote, and never the script's source."""

    def __init__(self, root: Path):
        self.root = root
        self.state = root / "st"
        self.bin = root / "bin"
        self.scripts = root / "s"
        for d in (self.state / "auto", self.bin, self.scripts):
            d.mkdir(parents=True)
        for name in ("wave.sh", "address.sh", "ghcall.sh"):
            shutil.copy(SCRIPTS / name, self.scripts / name)
        self.origin = root / "origin.git"
        self.co = root / "co"
        self.git("init", "-q", "-b", "dev", str(self.co), cwd=root)
        (self.co / "f").write_text("x\n")
        self.git("add", "f", cwd=self.co)
        self.git("commit", "-q", "-m", "init", cwd=self.co)
        self.git("clone", "-q", "--bare", str(self.co), str(self.origin), cwd=root)
        board = root / "board.sh"
        board.write_text(f'#!/bin/bash\necho "$*" >> "{root}/board.log"\n')
        board.chmod(0o755)
        (self.scripts / "env.sh").write_text(
            f"export SG_STATE={self.state} SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT={self.co}"
            f" SG_LAND_REMOTE={self.origin} BOARD={board}\n. {self.scripts}/address.sh\n"
        )
        gh = self.bin / "gh"
        gh.write_text(f'#!/bin/bash\necho "$*" >> "{root}/gh.log"\necho \'{{"number":1,"state":"closed"}}\'\n')
        gh.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
        self.env["PATH"] = f"{self.bin}:{os.environ['PATH']}"
        self.env["SG_WAVE_GATES"] = "true"

    def git(self, *args, cwd=None, check=True):
        full = ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args]
        return subprocess.run(full, cwd=cwd or self.co, capture_output=True, text=True, check=check, env=self.env if hasattr(self, "env") else None)

    def wave(self, verb, *args, cwd=None):
        return subprocess.run(["bash", str(self.scripts / "wave.sh"), verb, *args], cwd=cwd or self.co, capture_output=True, text=True, env=self.env, timeout=60)

    def rev(self, ref, repo=None):
        r = self.git("rev-parse", "--verify", "-q", ref, cwd=repo or self.origin, check=False)
        return r.stdout.strip()

    def child(self, run, issue, *messages):
        """A child worktree cut from the wave branch with one commit per message (subject, then optional body), reviewed and entered."""
        wt = self.root / f"c{issue}"
        self.git("clone", "-q", str(self.origin), str(wt), cwd=self.root)
        self.git("checkout", "-q", "-b", f"gui/{issue}", f"origin/wave/{run}", cwd=wt)
        for i, msg in enumerate(messages):
            (wt / f"g{issue}-{i}").write_text(f"{issue}\n")
            self.git("add", ".", cwd=wt)
            subject, _, body = msg.partition("\n\n")
            self.git("commit", "-q", "-m", subject, *(["-m", body] if body else []), cwd=wt)
        key = self.state / f"review-gui_{issue}"
        key.with_suffix(".md").write_text("VERDICT: APPROVE\n")
        key.with_suffix(".sha").write_text(self.rev("HEAD", wt) + "\n")
        key.with_suffix(".base").write_text(f"wave/{run}\n")
        entered = self.wave("enter", run, str(issue), cwd=wt)
        assert entered.returncode == 0, entered.stdout + entered.stderr
        return wt

    def base_moves(self, filename, text="other\n", message="other work"):
        other = self.root / "other"
        shutil.rmtree(other, ignore_errors=True)
        self.git("clone", "-q", str(self.origin), str(other), cwd=self.root)
        (other / filename).write_text(text)
        self.git("add", ".", cwd=other)
        self.git("commit", "-q", "-m", message, cwd=other)
        self.git("push", "-q", "origin", "HEAD:dev", cwd=other)

    def ledger(self):
        f = self.state / "landed.tsv"
        return [line.split("\t") for line in f.read_text().splitlines()] if f.exists() else []

    def closed(self):
        log = self.root / "gh.log"
        return sorted(set(re.findall(r"issues/(\d+) -f state=closed", log.read_text()))) if log.exists() else []

    def remote_refs(self, pattern):
        return self.git("ls-remote", str(self.origin), pattern, cwd=self.root).stdout.split()


@pytest.fixture
def wave(tmp_path):
    w = Wave(tmp_path)
    assert w.wave("start", "run-1").returncode == 0
    return w


@pytest.mark.heavy
def test_a_landed_wave_is_in_dev_by_one_push_and_logged(wave):
    wt = wave.child("run-1", 7, "work 7 (Refs #7)")
    before = wave.rev("refs/heads/dev")
    r = wave.wave("land", "run-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert wave.rev("refs/heads/dev") == wave.rev("HEAD", wt) != before
    assert wave.remote_refs("refs/heads/wave/*") == [], "the wave branch survives its landing"
    assert wave.remote_refs("refs/heads/gui/*") == [], "a child branch reached the remote"
    assert not (wave.state / "auto" / "wave-run-1").exists(), "the wave marker outlives the landing"
    assert [row[1:] for row in wave.ledger()] == [["wave/run-1", wave.rev("refs/heads/dev"), "7"]]
    assert wave.closed() == ["7"]


@pytest.mark.heavy
def test_a_landing_that_conflicts_exits_1_and_lands_nothing(wave):
    wave.child("run-1", 7, "work 7 (Refs #7)")
    # the base moves under the wave on the very file the child wrote
    wave.base_moves("g7-0", "someone else\n")
    moved = wave.rev("refs/heads/dev")
    r = wave.wave("land", "run-1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert wave.rev("refs/heads/dev") == moved, "a refused landing moved dev"
    assert wave.remote_refs("refs/heads/wave/run-1") != [], "a refused landing deleted the wave branch"
    assert wave.ledger() == [] and wave.closed() == []
    assert (wave.state / "auto" / "wave-run-1").exists(), "the run's wave marker was cleared by a landing that did not happen"


@pytest.mark.heavy
def test_a_landing_without_its_wave_exits_2_and_moves_nothing(wave):
    before = wave.rev("refs/heads/dev")
    r = wave.wave("land", "run-9")
    assert r.returncode == 2, r.stdout + r.stderr
    assert wave.rev("refs/heads/dev") == before
    assert wave.ledger() == []


@pytest.mark.heavy
def test_an_issue_named_in_a_commit_body_is_closed_and_logged_once(wave):
    wave.child("run-1", 60, "work 60\n\nRefs #60", "more work\n\nRefs #60", "third (Refs #63)\n\nRefs #63")
    r = wave.wave("land", "run-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert wave.closed() == ["60", "63"]
    assert sorted(row[3] for row in wave.ledger()) == ["60", "63"], "an issue two commits name was logged twice, or one was missed"


@pytest.mark.heavy
def test_a_commit_the_base_already_holds_closes_no_issue(wave):
    wt61 = wave.child("run-1", 61, "work 61 (Refs #61)")
    wave.child("run-1", 62, "work 62 (Refs #62)")
    # the base gains the same patch under another sha: the landing's rebase drops it
    other = wave.root / "dup"
    wave.git("clone", "-q", str(wave.origin), str(other), cwd=wave.root)
    wave.git("fetch", "-q", str(wt61), "gui/61", cwd=other)
    cp = subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "cherry-pick", "FETCH_HEAD"], cwd=other,
                        capture_output=True, text=True, env={**wave.env, "GIT_COMMITTER_NAME": "other"})
    assert cp.returncode == 0, cp.stderr
    wave.git("push", "-q", "origin", "HEAD:dev", cwd=other)
    r = wave.wave("land", "run-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert wave.closed() == ["62"], "the landing closed an issue it did not deliver"
    assert [row[3] for row in wave.ledger()] == ["62"]


@pytest.mark.slow(reason="runs the wave's hermetic shell suite over a throwaway remote")
@pytest.mark.heavy
def test_the_wave_suite_proves_the_landing_pass():
    r = subprocess.run(["bash", str(SCRIPTS / "test-wave.sh")], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TDD GREEN" in r.stdout, r.stdout
    for case in LAND_CASES:
        assert f"ok   {case}" in r.stdout, f"{case} did not pass in the wave suite:\n{r.stdout}"


@pytest.mark.slow(reason="runs the run lifecycle over a throwaway checkout, remote and stubbed launch")
@pytest.mark.heavy
def test_the_run_lifecycle_cuts_and_enters_the_wave():
    r = subprocess.run(["bash", str(SCRIPTS / "test-wave-lifecycle.sh")], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TDD GREEN" in r.stdout, r.stdout
    for case in LIFECYCLE_CASES:
        assert f"ok   {case}" in r.stdout, f"{case} did not pass in the lifecycle suite:\n{r.stdout}"


@pytest.mark.slow(reason="runs the run-completion suite's own loop over a temp scripts dir")
@pytest.mark.heavy
def test_the_run_completion_lands_the_wave_and_leaves_a_waveless_run_alone():
    r = subprocess.run(["bash", str(SCRIPTS / "test-watch-land.sh")], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TDD GREEN" in r.stdout, r.stdout
    for case in COMPLETION_CASES:
        assert f"ok   {case}" in r.stdout, f"{case} did not pass in the run-completion suite:\n{r.stdout}"
