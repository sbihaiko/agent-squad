"""The scope a gate stage runs at, through the one battery the loop shares: an intermediate stage runs the delta a change
reaches, an uncovered change falls back to the full battery with its reason, and the landing stage runs the full battery
whatever the delta would say. The proof is the selector's own answer and the two record files the gate wrapper reads
(GATE_SELECT_FILE and GATE_SCOPE_FILE), so the stage naming and the fallback are tested together, never one shape alone."""
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


def test_an_uncovered_path_falls_back_to_the_full_battery_and_the_record_says_why(tmp_path):
    """(b) A delta that cannot cover the change is never a pass: the whole battery runs and the record names the path."""
    repo = tip(tmp_path, {"docs/notes.md": "notes\n"})
    sel, scope, argv = battery(tmp_path, repo, "wave-entry")
    assert sel.startswith("all: ") and "docs/notes.md" in sel and scope == "full"
    assert "tests/test_" not in pytest_line(argv)


@pytest.mark.parametrize("stage", ["landing", "promotion", "nightly"])
def test_a_full_stage_runs_the_full_battery_whatever_the_delta_says(tmp_path, stage):
    """(c) The landing, the promotion proof and the nightly keep the battery: a covered change narrows nothing."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    sel, scope, argv = battery(tmp_path, repo, stage)
    assert sel == f"all: {stage} runs the full battery" and scope == "full"
    assert "tests/test_" not in pytest_line(argv)
