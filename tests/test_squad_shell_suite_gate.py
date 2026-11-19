"""Issue #1392: the squad's own shell suites (contrib/squad-goal/scripts/test-*.sh) run offline, and every red is named.

The gate script that ran the GUI command beside them retired with the local land path (#115), so what this file guards is
shell-suites.sh itself: the shell half of the machine's full gate, run by hand (`bash contrib/squad-goal/scripts/
shell-suites.sh`) and handed to a child by the request builder's line. The pytest half of that gate runs in CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
ROOT = Path(__file__).resolve().parent.parent


def _tree(tmp_path, suites):
    """A scripts folder holding the real gate scripts and the given fake suites (name -> shell body)."""
    d = tmp_path / "contrib" / "squad-goal" / "scripts"
    d.mkdir(parents=True)
    for name in ("shell-suites.sh", "startability.sh", "serial.sh", "caps.sh"):   # caps.sh: the git scrub (#2306)
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


def test_a_script_the_loop_edits_is_still_read_by_the_suites_that_gate_it():
    """#1392, after #115 retired the local gate: a diff touching contrib/squad-goal/scripts is otherwise gated by a suite
    that never looks at them. Its two halves live where they run now: the shell suites beside the script (handed to a child
    by the request builder, run by hand) and the pytest suite, which CI runs on every push and every pull request."""
    assert "shell-suites.sh" in (SCRIPTS / "mkreq.sh").read_text(), (
        "the request builder must hand a child the suite beside the script it changed")
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    runs = [step["run"] for job in wf["jobs"].values() for step in job["steps"] if "run" in step]
    assert any("pytest" in r for r in runs), runs


def test_the_emulator_acceptance_goes_through_the_one_serial_lock_and_is_never_bare():
    """#1410/#1392 where they live now, with the gate retired: an acceptance command that launches the emulator runs
    through serial.sh's `gui-suite` lock -- the prompt checklist a branch is handed names it -- and no script of the loop
    runs a profile's GUI command bare, since Mesen's SingleInstance mutex makes the second emulator exit 0."""
    bare = [p.name for p in SCRIPTS.glob("*.sh")
            if not p.name.startswith("test-")
            and any("$SG_FORK_TEST_CMD" in line for line in p.read_text().splitlines()
                    if not line.lstrip().startswith("#"))]
    assert bare == [], f"a script runs the profile's GUI command bare: {bare}"
    assert "serial.sh gui-suite" in (SCRIPTS / "req-checklist.txt").read_text(), (
        "the checklist must send an emulator acceptance through the gui-suite lock")


def test_the_gate_knobs_never_reach_a_suite(tmp_path):
    # #755: nightly.sh runs the suites with SERIAL_HOLD_SECONDS=10800, and a suite that reads the bound in force saw a
    # reserve it wrote as dead still alive. The gate's SERIAL_* knobs are cleared like SG_*, so a suite reads its own.
    d = _tree(tmp_path, {"test-a.sh": 'echo "hold=${SERIAL_HOLD_SECONDS:-unset} try=${SERIAL_TRY:-unset}"'})
    p = _run("bash", str(d / "shell-suites.sh"), cwd=tmp_path, env={"SERIAL_HOLD_SECONDS": "10800", "SERIAL_TRY": "1"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "hold=unset try=unset" in p.stdout, p.stdout
