"""The scope a gate stage runs at, through the one battery the loop shares: an intermediate stage runs the delta a change
reaches, an uncovered change falls back to the full battery with its reason, and the landing stage runs the full battery
whatever the delta would say. The proof is the selector's own answer and the two record files the gate wrapper reads
(GATE_SELECT_FILE and GATE_SCOPE_FILE), so the stage naming and the fallback are tested together, never one shape alone.
The last case guards the wiring the battery cannot show: the landing gate asks for the `landing` stage, and the
promotion proof and the nightly never ask the selector, so no stage name can narrow them (ADR-0166)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_testselect import tip

ROOT = Path(__file__).resolve().parent.parent
BASE_BATTERY = ROOT / "contrib/squad-goal/scripts/base-battery.sh"


def battery(tmp_path, repo, stage):
    """Run the shared battery at <stage> in <repo> with a stub python: ruff and mypy pass, the selector is the real one, and
    the pytest leg only records its argv. Return the selection line, the scope line and every argv the stub saw."""
    log = tmp_path / f"argv-{stage}.log"
    py = tmp_path / f"py-{stage}"
    py.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        "case \"$*\" in\n"
        "  *squad.testselect*)\n"
        "    here=$PWD; shift 2; cd \"$SG_SCOPE_ROOT\" || exit 1\n"
        f'    exec "{sys.executable}" -m squad.testselect --worktree "$here" "$@" ;;\n'
        "esac\n"
        "exit 0\n"
    )
    py.chmod(0o755)
    sel = tmp_path / f"select-{stage}"
    scope = tmp_path / f"scope-{stage}"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SG_SCOPE_ROOT": str(ROOT),
           "GATE_SELECT_FILE": str(sel), "GATE_SCOPE_FILE": str(scope)}
    env.pop("SG_TEST_SELECT", None)   # a gate that runs this suite may hold it; the case reads its own stage, never the live one
    env.pop("SG_GATE_STAGE", None)
    cmd = f'. "{BASE_BATTERY}"; sg_battery_run "{py}" main 2 yes "{stage}"'
    done = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return sel.read_text().strip(), scope.read_text().strip(), log.read_text()


def pytest_line(argv):
    return next(line for line in argv.splitlines() if line.startswith("-m pytest"))


def test_an_intermediate_stage_runs_the_delta_and_records_it(tmp_path):
    """(a) A covered change at an intermediate stage is a delta: the record says `delta` and names the selected files."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    sel, scope, argv = battery(tmp_path, repo, "fast")
    assert sel == "selected 2 files" and scope == "delta"
    assert "squad.testselect --stage fast --base main" in argv
    assert pytest_line(argv).endswith("tests/test_alpha.py tests/test_beta.py")


def test_a_change_to_one_script_runs_the_tests_the_delta_reaches(tmp_path):
    """(a') The hand gate and the child final gate share this battery at the `fast` stage. A change to one loop script also
    edits the measured line of its suite, and a test that imports that line's reader also loads a script by a computed path:
    the delta still names the reached tests, and the record says `delta` (ADR-0169). A test that names only the scripts
    directory is not reached: a directory selects a test only with the file's name or a listing of it (#1784)."""
    extra = {
        "contrib/squad-goal/scripts/watchd.sh": "old\n",
        "tests/shell_suites.py": "def run(p):\n    return None\n",
        "tests/shell_suites_seconds.txt": "suite 1\n",
        "tests/test_names.py": 'SCRIPTS = "contrib/squad-goal/scripts"\n',
        "tests/test_reader.py": "from tests import shell_suites\n",
        "tests/test_loader.py": (
            "import importlib.util\n"
            "from tests import shell_suites\n"
            "\n"
            "SCRIPTS = 'contrib/squad-goal/scripts'\n"
            "SPEC = importlib.util.spec_from_file_location('m', SCRIPTS + '/watchd.sh')\n"
        ),
    }
    repo = tip(tmp_path, {"contrib/squad-goal/scripts/watchd.sh": "new\n",
                          "tests/shell_suites_seconds.txt": "suite 1\nsuite 2\n"}, extra=extra)
    sel, scope, argv = battery(tmp_path, repo, "fast")
    assert sel == "selected 2 files" and scope == "delta"
    assert pytest_line(argv).endswith("tests/test_loader.py tests/test_reader.py")


def test_a_scratch_log_in_the_scratch_place_keeps_the_delta_scope(tmp_path):
    """(2032) A child's untracked scratch log in its `.scratch` folder is no uncommitted change the delta cannot place: the
    record keeps `delta` and the selection the committed diff reaches."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    (repo / ".scratch").mkdir()
    (repo / ".scratch" / "fast-gate-1983-2.log").write_text("scratch\n")
    sel, scope, argv = battery(tmp_path, repo, "fast")
    assert sel == "selected 2 files" and scope == "delta"
    assert pytest_line(argv).endswith("tests/test_alpha.py tests/test_beta.py")


def test_an_uncovered_path_falls_back_to_the_full_battery_and_the_record_says_why(tmp_path):
    """(b) A delta that cannot cover the change is never a pass: the whole battery runs and the record names the path."""
    repo = tip(tmp_path, {"docs/notes.md": "notes\n"})
    sel, scope, argv = battery(tmp_path, repo, "wave-entry")
    assert sel.startswith("all: ") and "docs/notes.md" in sel and scope == "full"
    assert "tests/test_" not in pytest_line(argv)


def test_an_empty_selection_runs_no_pytest_and_logs_why(tmp_path):
    """(#1249) A delta that selects no test runs no pytest, and the battery's log line names the selector's reason, so the
    fast gate can record the attempt as unproven with that reason, never as a pass."""
    repo = tip(tmp_path, {})
    log = tmp_path / "argv-fast.log"
    py = tmp_path / "py-fast"
    py.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        "case \"$*\" in\n"
        "  *squad.testselect*)\n"
        "    here=$PWD; shift 2; cd \"$SG_SCOPE_ROOT\" || exit 1\n"
        f'    exec "{sys.executable}" -m squad.testselect --worktree "$here" "$@" ;;\n'
        "esac\n"
        "exit 0\n"
    )
    py.chmod(0o755)
    sel = tmp_path / "select-fast"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SG_SCOPE_ROOT": str(ROOT), "GATE_SELECT_FILE": str(sel)}
    env.pop("SG_TEST_SELECT", None)
    env.pop("SG_GATE_STAGE", None)
    cmd = f'. "{BASE_BATTERY}"; sg_battery_run "{py}" main 2 yes fast'
    done = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sel.read_text().strip() == "selected 0 files"
    assert "-m pytest" not in log.read_text()
    assert "no pytest ran: the selection is empty: the tip changes no file against main" in done.stdout.splitlines()


@pytest.mark.parametrize("stage", ["landing", "promotion", "nightly"])
def test_a_full_stage_runs_the_full_battery_whatever_the_delta_says(tmp_path, stage):
    """(c) The landing, the promotion proof and the nightly keep the battery: a covered change narrows nothing."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    sel, scope, argv = battery(tmp_path, repo, stage)
    assert sel == f"all: {stage} runs the full battery" and scope == "full"
    assert "tests/test_" not in pytest_line(argv)


def test_the_delivery_gates_never_take_the_delta_scope():
    """The wiring the battery cannot show: the landing check asks for `landing`, so pytest is the whole tree, and the wave
    tip check asks for `wave-tip`, so it stays a delta. The promotion proof and the nightly do not build the battery or read
    the selector at all, so no stage name narrows them. They run pytest over the whole tree themselves."""
    scripts = ROOT / "contrib/squad-goal/scripts"
    land = (scripts / "branchland.sh").read_text()
    assert 'sg_base_gate "refs/sg-gate/$land" "$py" landing' in land
    wave = (scripts / "wave.sh").read_text()
    assert 'sg_base_gate "origin/$base" "$py" wave-tip' in wave
    for name in ("promote-pass.sh", "nightly.sh"):
        text = (scripts / name).read_text()
        assert "-m pytest" in text, name
        assert "testselect" not in text, name
        assert "sg_base_battery" not in text, name
