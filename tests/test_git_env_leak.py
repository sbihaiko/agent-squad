"""#2306: a test or a gate never writes into the repository that runs it.

At 14:20 the shared `.git/config` of the main checkout received `core.bare = true`, and every git command there then failed
with `fatal: this operation must be run in a work tree` until a hand edit repaired it. The probe (`git init --bare` with no
path, under `GIT_DIR`) reproduces it: git re-initializes `GIT_DIR` and writes `core.bare = true` into the caller's config.

So the test harness drops `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE` before any test runs, and a helper that runs
`git init` or `git clone` hands its own clean environment. The janitor reports a checkout whose config says it is bare,
and names that checkout.
"""
import os
import subprocess

import pytest

from tests.conftest import drop_live_paths
from tests.gitrepo import git_env, with_origin
from tests.test_janitor_card_done_base import janitor, sandbox

VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _git(cwd, *args):
    """A read the test itself makes: it names its repository by its working tree, never by an inherited GIT_DIR."""
    return subprocess.run(["git", *args], cwd=cwd, env=git_env(), capture_output=True, text=True)


def test_the_session_drops_the_git_environment_a_gate_inherits(monkeypatch):
    """A suite runs under a gate, and the git hook that started the gate exports GIT_DIR: no test may inherit it."""
    for name in VARS:
        monkeypatch.setenv(name, "/live/" + name.lower())
    drop_live_paths()
    left = [n for n in VARS if n in os.environ]
    assert not left, f"a test that runs git init or git clone would write into the caller's repository: {left}"


def test_the_repo_helper_never_writes_into_an_inherited_git_dir(tmp_path, monkeypatch):
    """With GIT_DIR naming another repository, `with_origin` leaves that repository's config alone and builds its own."""
    caller = tmp_path / "caller"
    caller.mkdir()
    assert _git(tmp_path, "init", "-q", "-b", "main", str(caller)).returncode == 0
    monkeypatch.setenv("GIT_DIR", str(caller / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(caller))
    monkeypatch.setenv("GIT_INDEX_FILE", str(caller / ".git" / "index"))
    co = tmp_path / "co"
    co.mkdir()
    (co / "a.txt").write_text("a\n")
    with_origin(co)
    config = (caller / ".git" / "config").read_text()
    assert "bare = true" not in config, f"the helper re-initialized the caller's repository: {config}"
    status = _git(caller, "status", "--porcelain")
    assert status.returncode == 0, f"the caller's repository is unusable: {status.stderr}"
    assert _git(co, "rev-parse", "--verify", "origin/main").returncode == 0, "the helper's own origin was not built"


@pytest.mark.heavy
def test_the_janitor_reports_a_bare_checkout_and_names_it(tmp_path):
    """The pass names the checkout whose config says it is bare, so the owner knows which tree to repair."""
    sandbox(tmp_path, "echo 0")
    co = tmp_path / "co"
    assert _git(co, "init", "-q", "-b", "main").returncode == 0
    assert _git(co, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base").returncode == 0
    assert _git(co, "config", "core.bare", "true").returncode == 0
    p = janitor(tmp_path, JANITOR_NOTIFY="/usr/bin/true")
    assert p.returncode == 0, p.stderr
    alerts = (tmp_path / "st" / "alerts.log").read_text() if (tmp_path / "st" / "alerts.log").exists() else ""
    assert "BARE-CHECKOUT" in alerts, f"no alert for a bare checkout: {alerts}"
    assert str(co.resolve()) in alerts, f"the alert names no checkout: {alerts}"
