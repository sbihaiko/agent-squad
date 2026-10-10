"""Issue #1392: the fork gate runs the squad's own shell suites (contrib/squad-goal/scripts/test-*.sh), offline."""
import os
import shutil
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _tree(tmp_path, suites):
    """A scripts folder holding the real gate scripts and the given fake suites (name -> shell body)."""
    d = tmp_path / "contrib" / "squad-goal" / "scripts"
    d.mkdir(parents=True)
    for name in ("shell-suites.sh", "fork-gate.sh"):
        if (SCRIPTS / name).exists():
            shutil.copy(SCRIPTS / name, d / name)
    for name, body in suites.items():
        (d / name).write_text("#!/bin/bash\n" + body + "\n")
    return d


def _run(*cmd, cwd, env=None, timeout=60):
    e = {**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **(env or {})}
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, env=e, timeout=timeout)


def test_a_clean_tree_exits_zero(tmp_path):
    d = _tree(tmp_path, {"test-a.sh": "echo ok a", "test-b.sh": "echo ok b"})
    p = _run("bash", str(d / "shell-suites.sh"), cwd=tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "test-a.sh" in p.stdout and "test-b.sh" in p.stdout


def test_a_broken_suite_exits_non_zero_and_every_failure_is_named(tmp_path):
    d = _tree(tmp_path, {"test-a.sh": "exit 1", "test-b.sh": "echo ok b", "test-c.sh": "echo FAIL c; exit 3"})
    p = _run("bash", str(d / "shell-suites.sh"), cwd=tmp_path)
    assert p.returncode != 0
    assert "FAILED: test-a.sh" in p.stdout and "FAILED: test-c.sh" in p.stdout, p.stdout
    assert "test-b.sh" in p.stdout and "FAILED: test-b.sh" not in p.stdout


def test_a_suite_that_prints_fail_but_exits_zero_is_not_hidden(tmp_path):
    # The squad's suites set a flag and `exit $fail`; a stray FAIL line with rc 0 is still reported, not swallowed.
    d = _tree(tmp_path, {"test-a.sh": "echo 'FAIL something'; exit 0"})
    p = _run("bash", str(d / "shell-suites.sh"), cwd=tmp_path)
    assert p.returncode == 0 and "FAIL something" in p.stdout


def test_the_fork_gate_runs_the_gui_command_and_the_shell_suites_and_fails_on_either(tmp_path):
    d = _tree(tmp_path, {"test-a.sh": "echo ok a"})
    marker = tmp_path / "gui-ran"
    gate = lambda gui: _run("bash", str(d / "fork-gate.sh"), gui, cwd=tmp_path)  # noqa: E731
    p = gate(f"touch {marker}")
    assert p.returncode == 0, p.stdout + p.stderr
    assert marker.exists(), "the GUI suite the gate already ran must keep running"
    assert "test-a.sh" in p.stdout, "the shell suites must run too"

    (d / "test-a.sh").write_text("#!/bin/bash\nexit 1\n")
    marker.unlink()
    p = gate(f"touch {marker}")
    assert p.returncode != 0, "a failing shell suite must block the merge"
    assert marker.exists(), "a red shell suite must not skip the GUI suite"

    (d / "test-a.sh").write_text("#!/bin/bash\nexit 0\n")
    assert gate("exit 4").returncode != 0, "a red GUI suite still blocks, and the shell suites do not mask it"


def test_forkclose_runs_the_gate_instead_of_the_bare_gui_command():
    src = (SCRIPTS / "forkclose.sh").read_text()
    assert src.count("fork-gate.sh") >= 2, "both the base test and the merge test must go through the gate"
    assert "sh -c \"$SG_FORK_TEST_CMD\"" not in src
