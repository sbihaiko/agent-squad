"""Issue #81: the child's request names what it runs, and leaves the machine's full gate to the loop.

A child that runs the full shell suites and the full pytest gate in its own worktree runs exactly what the loop's land
pass runs, and the two gates take the machine between them. So the request every builder emits through mkreq.sh says
which suites the child runs -- the one beside the script it touched, and the targeted test cases -- and says that the
machine's full gate belongs to the loop, once per landing.

Host-free: the real mkreq.sh runs in a throwaway tree with a throwaway HOME, so nothing here touches the live loop's
request folder or the network.
"""
import os
import shutil
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
# The line every generated request carries. `bash`/`uv` open their inline spans, which is also what keeps the paths in
# them out of the slice guard's reading of a request (#44): they are commands a child RUNS, not files it edits.
SUITES_LINE = (
    "SUITES: run what you touched -- the shell suite beside the script you changed "
    "(`bash contrib/squad-goal/scripts/test-<script>.sh`) and the targeted pytest cases "
    "(`uv run --extra dev pytest tests/<file>.py -q -p no:deepeval -k <case>`). "
    "The machine's full gate (`bash contrib/squad-goal/scripts/shell-suites.sh` and the pytest gate beside it) "
    "belongs to the loop and to the land pass, once per landing: never run it."
)
PR_BODY = "IMPORTANT: your first and only action is X\nEdit dashboard/free.py\n"
FORK_BODY = "IMPORTANT: your first and only action is X\nFork-only work: no PR.\n"


def _scripts(tmp_path):
    """A scripts folder holding the real request builder and everything it reads."""
    d = tmp_path / "scripts"
    d.mkdir(parents=True)
    for name in ("mkreq.sh", "forkonly.sh", "models.json", "req-checklist.txt", "req-checklist-fork.txt"):
        shutil.copy(SCRIPTS / name, d / name)
    return d


def _mkreq(tmp_path, name, body):
    """The request mkreq.sh generates for this body, as text."""
    d = _scripts(tmp_path)
    (tmp_path / "home").mkdir(exist_ok=True)
    src = tmp_path / f"{name}.body"
    src.write_text(body)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = "/usr/bin:/bin"
    r = subprocess.run(["bash", str(d / "mkreq.sh"), name, str(src)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return (tmp_path / "home" / ".cache" / "squad-goal" / "req" / f"{name}.txt").read_text()


def test_a_child_request_names_the_suites_it_runs_and_the_full_gate_as_the_loops(tmp_path):
    text = _mkreq(tmp_path, "pr", PR_BODY)
    assert SUITES_LINE in text, text
    lines = [l for l in text.splitlines() if l.startswith("SUITES:")]
    assert lines == [SUITES_LINE], f"the request carries one SUITES line: {lines}"


def test_a_fork_only_child_request_carries_the_same_line(tmp_path):
    # The fork-only route appends its own checklist; the line that says what a child runs is not a property of the route.
    assert SUITES_LINE in _mkreq(tmp_path, "fork", FORK_BODY)


def test_the_line_names_the_targeted_commands_and_not_only_the_full_gate(tmp_path):
    # The instruction is actionable: the suite beside the touched script and the targeted cases, both named in a command.
    text = _mkreq(tmp_path, "pr2", PR_BODY)
    line = [l for l in text.splitlines() if l.startswith("SUITES:")][0]
    assert "test-<script>.sh" in line and "pytest tests/<file>.py" in line, line
    assert "once per landing" in line and "belongs to the loop" in line, line


def test_the_request_still_carries_the_done_check_the_gate_reads(tmp_path):
    # The SUITES line is added beside the DONE CHECK, never in place of it: the run's final gate reads that block.
    text = _mkreq(tmp_path, "pr3", PR_BODY)
    assert "DONE CHECK" in text and '"status": "pass"' in text, text
