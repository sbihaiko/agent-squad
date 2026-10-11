"""Issue #1822: the fork gate's `full` set runs the pytest suite too, so a red unit test stops a landing.

The hole: gate-suites.sh maps tests/test_gui_test_*.py to `full`, and fork-gate.sh ran its pytest command only for
the `python` class -- so a diff touching a GUI test (and the tests/test_hub_*.py files that ride with it) never
executed the unit tests. #1809 shipped an inconsistent test that way: it rode a green GUI-and-shell run into a landing.
"""
import os
import shutil
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _tree(tmp_path, suites):
    """A scripts folder holding the real gate scripts and the given fake shell suites (name -> shell body)."""
    d = tmp_path / "contrib" / "squad-goal" / "scripts"
    d.mkdir(parents=True)
    for name in ("shell-suites.sh", "fork-gate.sh", "serial.sh"):
        shutil.copy(SCRIPTS / name, d / name)
    for name, body in suites.items():
        (d / name).write_text("#!/bin/bash\n" + body + "\n")
    return d


def _env(cwd, **over):
    # Hermetic (#1515): the live loop's own SG_*/FG_* must not reach the gate. SG_STATE is private so the gui-suite lock
    # the gate takes is this test's own; PATH carries no uv, so every pytest command here is pinned, never inherited.
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    e["SG_STATE"] = str(Path(cwd) / "state")
    e.update(over)
    return e


def _run(*cmd, cwd, env=None, stdin=None, timeout=60):
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=_env(cwd, **(env or {})), input=stdin)


def _class_for(*paths, cwd):
    """The class gate-suites.sh answers for a diff touching these paths (its own seam: paths in, class out)."""
    p = _run("bash", str(SCRIPTS / "gate-suites.sh"), cwd=cwd, stdin="\n".join(paths) + "\n")
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


def test_a_gui_test_path_takes_the_full_class_and_the_full_class_runs_pytest(tmp_path):
    # A diff touching tests/test_gui_test_*.py is mapped to `full`; the gate that runs for it must execute the unit
    # tests as well, or a red one is invisible to the suite that gates the landing (#1809).
    d = _tree(tmp_path, {"test-a.sh": "echo ok a"})
    cls = _class_for("tests/test_gui_test_home.py", cwd=tmp_path)
    assert cls == "full", f"tests/test_gui_test_*.py maps to the full gate, not {cls}"
    marker = tmp_path / "py.ran"
    p = _run("bash", str(d / "fork-gate.sh"), f"touch {tmp_path / 'gui.ran'}", cwd=tmp_path,
             env={"FG_SUITES": cls, "SG_FORK_PY_CMD": f"touch {marker}"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert marker.exists(), "the full class never ran the pytest suite, so a red unit test rides along (#1822)"
    assert "=== fork gate: python tests" in p.stdout, p.stdout


def test_a_red_pytest_suite_fails_the_full_gate_while_the_other_suites_are_green(tmp_path):
    d = _tree(tmp_path, {"test-a.sh": "echo ok a"})
    p = _run("bash", str(d / "fork-gate.sh"), "true", cwd=tmp_path,
             env={"FG_SUITES": "full", "SG_FORK_PY_CMD": "echo '1 failed, 4 passed' >&2; exit 7"})
    assert p.returncode != 0, "a red unit test must stop the landing, not ride a green GUI-and-shell run into it"
    assert "--- fork gate: python tests FAILED" in p.stdout, p.stdout


def test_the_shell_class_keeps_the_python_suite_out(tmp_path):
    # A contrib/squad-goal-only diff stays the shell gate (#1392): its cost stays the shell suites.
    d = _tree(tmp_path, {"test-a.sh": "echo ok a"})
    marker = tmp_path / "py.ran"
    p = _run("bash", str(d / "fork-gate.sh"), "true", cwd=tmp_path,
             env={"FG_SUITES": "shell", "SG_FORK_PY_CMD": f"touch {marker}"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "test-a.sh" in p.stdout, p.stdout
    assert not marker.exists(), "the shell gate runs the shell suites alone"
