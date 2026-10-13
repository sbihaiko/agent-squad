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
    for name in ("shell-suites.sh", "startability.sh", "serial.sh"):
        if (SCRIPTS / name).exists():
            shutil.copy(SCRIPTS / name, d / name)
    for name, body in suites.items():
        (d / name).write_text("#!/bin/bash\n" + body + "\n")
    return d


def _run(*cmd, cwd, env=None, timeout=60):
    # Hermetic (#1515): the live loop's own SG_*/FG_* must not reach the gate. serial.sh takes the machine-wide
    # gui-suite lock inside $SG_STATE, so an inherited SG_STATE makes this test wait for the running GUI suite --
    # minutes on a busy machine -- and the test times out or blows the suite's 20 s cap on the loop's load, not on
    # the gate's wiring. A private SG_STATE gives the gate its own lock; FG_SUITES is cleared so the gate is `full`.
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    e["SG_STATE"] = str(Path(cwd) / "state")
    e.update(env or {})
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
    # The gate's own inputs are pinned rather than inherited: startability.sh exports FG_SUITES for the branch's diff (gate-suites.sh) and this
    # test runs inside that very gate. Left inherited, a `python` outer gate makes the gate run its pytest branch instead of the full one this
    # test is about, which the fixture's /usr/bin:/bin PATH cannot even start (startability.sh: uv: not found) -- SG_FORK_PY_CMD is pinned with
    # it, since the full gate runs the pytest suite too (#1822), and this test is about the GUI command and the shell suites. SG_STATE is pinned for the same
    # reason: left shared, the GUI suite waits on the machine's gui-suite lock behind every other run's own suite, so a busy machine reddens a
    # test that says nothing about locks (serial.sh waits 1800s, the fixture's timeout is 60s). test-forkclose-serial.sh owns that arbitration.
    gate = lambda gui: _run("bash", str(d / "startability.sh"), gui, cwd=tmp_path,  # noqa: E731
                            env={"FG_SUITES": "full", "SG_STATE": str(tmp_path / "state"), "SG_FORK_PY_CMD": "true"})
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
    src = (SCRIPTS / "startability.sh").read_text()
    assert src.count("startability.sh") >= 2, "both the base test and the merge test must go through the gate"
    assert "sh -c \"$SG_FORK_TEST_CMD\"" not in src
