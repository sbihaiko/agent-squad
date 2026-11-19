"""A suite the pytest runner starts never sees the git location variables of its caller (#2306).

A git hook or a gate exports GIT_DIR. A fixture that runs `git init --bare` in its own folder, or `git config` there, then
writes GIT_DIR instead, and sets `core.bare = true` on the checkout that runs the tests.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import shell_suites

GIT_LOCATION = shell_suites.GIT_LOCATION
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
NAME = re.compile(r"GIT_[A-Z_]+")


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


def test_a_fresh_conftest_import_drops_the_hook_variables(tmp_path):
    """The drop at import is what a fixture inherits: a fresh conftest import under a hook's variables leaves the three the
    criteria name absent, and the two the criteria do not name present (a test drives those through shell_suites._run)."""
    live = str(tmp_path / "live")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT),
           "GIT_DIR": live, "GIT_WORK_TREE": live, "GIT_INDEX_FILE": f"{live}/index",
           "GIT_COMMON_DIR": f"{live}/.git", "GIT_OBJECT_DIRECTORY": f"{live}/.git/objects"}
    code = ("import json, os, tests.conftest\n"
            f"print(json.dumps([k for k in {list(GIT_LOCATION)!r} if k in os.environ]))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"]


def _caps_names():
    line = next(row for row in (SCRIPTS / "caps.sh").read_text().splitlines() if row.startswith("sg_git_env_scrub()"))
    return set(NAME.findall(line))


def _janitor_names():
    text = (SCRIPTS / "janitor.py").read_text()
    block = text[text.index("def checkout_bare():"):]
    return set(re.findall(r'"(GIT_[A-Z_]+)"', block[:block.index("\ncheckout_bare()\n")]))


def test_the_three_git_location_lists_agree():
    """One set of names on every side: the shell scrub, the janitor's own git call, and the pytest runner's clear."""
    assert _caps_names() == set(GIT_LOCATION)
    assert _janitor_names() == set(GIT_LOCATION)


def test_the_hook_variables_are_absent_in_this_session():
    """The probe the pytest run below selects: under any caller, a test reads none of the three hook variables."""
    assert [name for name in shell_suites.GIT_HOOK_LOCATION if name in os.environ] == []


@pytest.mark.heavy
def test_a_pytest_run_under_git_dir_drops_it_before_any_test(tmp_path):
    """A pytest process started the way a git hook starts it, with GIT_DIR and its siblings exported, runs its tests with
    none of the three: conftest drops them at import, before any module or fixture starts git."""
    live = str(tmp_path / "live")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "GIT_DIR": live, "GIT_WORK_TREE": live, "GIT_INDEX_FILE": f"{live}/index"})
    probe = "tests/test_git_env_scrub.py::test_the_hook_variables_are_absent_in_this_session"
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider", probe],
                         cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    assert "1 passed" in out.stdout, out.stdout[-2000:]
