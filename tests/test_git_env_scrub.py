"""A suite the pytest runner starts never sees the git location variables of its caller (#2306).

A git hook or a gate exports GIT_DIR. A fixture that runs `git init --bare` in its own folder, or `git config` there, then
writes GIT_DIR instead, and sets `core.bare = true` on the checkout that runs the tests.
"""
import os
import subprocess

from tests import conftest, shell_suites

GIT_LOCATION = shell_suites.GIT_LOCATION


def _checkout(tmp_path):
    repo = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env={"PATH": "/usr/bin:/bin"})
    return repo


def _bare(repo):
    return subprocess.run(["git", "config", "--file", str(repo / ".git" / "config"), "--get", "core.bare"],
                          capture_output=True, text=True).stdout.strip()


def test_a_suite_under_git_dir_keeps_the_checkout_config(tmp_path, monkeypatch):
    repo = _checkout(tmp_path)
    own = tmp_path / "own"
    suite = tmp_path / "test-probe.sh"
    suite.write_text(f'#!/bin/bash\nmkdir -p "{own}" && cd "{own}" && git init -q --bare && git config core.bare true\n')
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    result = shell_suites.run(suite, timeout=30)
    assert result.returncode == 0, result.stderr
    assert _bare(repo) == "false"
    assert (own / "HEAD").is_file()


def test_the_session_drops_every_git_location_variable(monkeypatch):
    for name in GIT_LOCATION:
        monkeypatch.setenv(name, "/live/checkout/.git")
    conftest.drop_git_location()
    assert [name for name in GIT_LOCATION if name in os.environ] == []
