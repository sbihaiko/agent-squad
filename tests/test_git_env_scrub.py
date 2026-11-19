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
    line = next(l for l in (SCRIPTS / "caps.sh").read_text().splitlines() if l.startswith("sg_git_env_scrub()"))
    return set(NAME.findall(line))


def _janitor_names():
    text = (SCRIPTS / "janitor.py").read_text()
    block = text[text.index("def checkout_bare():"):]
    return set(re.findall(r'"(GIT_[A-Z_]+)"', block[:block.index("def live_noise(")]))


def test_the_three_git_location_lists_agree():
    """One set of names on every side: the shell scrub, the janitor's own git call, and the pytest runner's clear."""
    assert _caps_names() == set(GIT_LOCATION)
    assert _janitor_names() == set(GIT_LOCATION)
