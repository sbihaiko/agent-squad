"""Issue #81: the child's request names what it runs, and leaves the machine's full gate to the loop.

A child that runs the full shell suites and the full pytest gate in its own worktree runs exactly what the loop's land
pass runs, and the two gates take the machine between them. So the request every builder emits through mkreq.sh says
which suites the child runs -- the one beside the script it touched, and the targeted test cases -- and says that the
machine's full gate belongs to the loop, once per landing.

Host-free: the real mkreq.sh runs in a throwaway tree with a throwaway HOME and a throwaway SG_STATE, so nothing here
touches the live loop's request folder or the network. The state directory is handed in, never spelled: the seam owns it
(#25), and mkreq.sh names the absence instead of building a path under the filesystem root.
"""
import os
import shutil
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
# The line every generated request carries. `bash`/`uv` open their inline spans, which is also what keeps the paths in
# them out of the slice guard's reading of a request (#44): they are commands a child RUNS, not files it edits.
SUITES_LINE = (
    "SUITES: run what you touched -- the targeted pytest cases "
    "(`uv run --extra dev pytest tests/<file>.py -q -p no:deepeval -k <case>`) and the shell suite beside the script you changed "
    "(`bash contrib/squad-goal/scripts/test-<script>.sh`), unless the SUITE COST line names that suite: the fast gate runs it once "
    "per head, so a suite the SUITE COST line names is never run by you. "
    "Then run `bash contrib/squad-goal/scripts/fast-gate.sh` in your worktree before you report done: "
    "it is the gate your commit must pass to enter the wave, its pytest takes the bounded worker count it carries. "
    "Run it once per commit: exit 0 is a pass; exit 4 is unproven whatever its line names (a bounded battery, an "
    "inconclusive battery or a full gate budget), not a defect in your work, and it ends your gate step: the entry gate "
    "and the landing measure your commit again under their own clock, so never run it a second time on the same commit, "
    "never wait for the machine, the nightly or another gate, and report done with the gate's last line in your result. "
    "When that line says unproven, end your result with {\"status\": \"unproven\", \"reasons\": [that line]}: a gate that "
    "could not run is no completed proof and no failing child, and the run carries the obligation (ADR-0164). Never list "
    "the fast gate in ac_commands: the run's gate runs that command again at its final head, and the proof there is the "
    "run's, not yours. Exit 1 names a defect: fix it and run the gate once on the new commit. "
    "The machine's full gate (`bash contrib/squad-goal/scripts/shell-suites.sh` and the full pytest suite beside it) "
    "belongs to the loop and to the land pass, once per landing: never run it."
)
PR_BODY = "IMPORTANT: your first and only action is X\nEdit dashboard/free.py\n"
FORK_BODY = "IMPORTANT: your first and only action is X\nFork-only work: no PR.\n"


def _scripts(tmp_path):
    """A scripts folder holding the real request builder and everything it reads."""
    d = tmp_path / "scripts"
    d.mkdir(parents=True)
    for name in ("mkreq.sh", "startability.sh", "skiplabels.sh", "models.json", "req-checklist.txt"):   # the fork checklist retired with the fork-only land (#115)
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
    env["SG_STATE"] = str(tmp_path / "st")   # the repository's state directory, the seam's one answer (#25)
    env["PATH"] = "/usr/bin:/bin"
    r = subprocess.run(["bash", str(d / "mkreq.sh"), name, str(src)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return (tmp_path / "st" / "req" / f"{name}.txt").read_text()


def test_a_child_request_names_the_suites_it_runs_and_the_full_gate_as_the_loops(tmp_path):
    text = _mkreq(tmp_path, "pr", PR_BODY)
    assert SUITES_LINE in text, text
    lines = [ln for ln in text.splitlines() if ln.startswith("SUITES:")]
    assert lines == [SUITES_LINE], f"the request carries one SUITES line: {lines}"


def _mkreq_real(tmp_path, name, body):
    """The request the repository's own mkreq.sh writes, so the measured suite costs of this checkout are read."""
    src = tmp_path / f"{name}.body"
    src.write_text(body)
    (tmp_path / "home").mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    env["HOME"] = str(tmp_path / "home")
    env["SG_STATE"] = str(tmp_path / "st")
    env["PATH"] = "/usr/bin:/bin"
    r = subprocess.run(["bash", str(SCRIPTS / "mkreq.sh"), name, str(src)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return (tmp_path / "st" / "req" / f"{name}.txt").read_text()


def _header_seconds(name):
    """The cost a suite's own `# seconds:` header names: a re-measure changes the header, never this test."""
    for line in (SCRIPTS / name).read_text(errors="ignore").splitlines()[:6]:
        if line.startswith("# seconds:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{name} carries no '# seconds:' header")


def test_the_request_names_the_measured_cost_of_each_long_suite_and_no_short_one(tmp_path):
    """#1761: a child that re-runs a long suite spends its deadline in its own proofs, so the request names what each costs."""
    text = _mkreq_real(tmp_path, "cost", PR_BODY)
    cost = [ln for ln in text.splitlines() if ln.startswith("SUITE COST:")]
    assert len(cost) == 1, text
    for name in ("test-branchland.sh", "test-autobug.sh"):
        assert f"{name} {_header_seconds(name)} s" in cost[0], cost[0]
    assert "test-board-fast.sh" not in cost[0], cost[0]
    assert "a proof that cannot finish before your deadline is not a failure" in text.lower(), text


def test_the_checklist_names_a_resolved_interpreter_and_never_a_shell_variable():
    """#1243: the ac-verify shell has no SG_CHECKOUT, so `$SG_CHECKOUT/.venv/bin/python` runs as `/.venv/bin/python` and
    exits 127. The checklist names the path sg_gate_python prints, written out in full."""
    item = (SCRIPTS / "req-checklist.txt").read_text().split("(14)", 1)[1]
    assert "sg_gate_python" in item, "the checklist does not name the one resolver"
    assert "$SG_CHECKOUT/" not in item, "the checklist still names a shell variable as the interpreter"


def test_a_fork_only_child_request_carries_the_same_line(tmp_path):
    # The fork-only route appends its own checklist; the line that says what a child runs is not a property of the route.
    assert SUITES_LINE in _mkreq(tmp_path, "fork", FORK_BODY)


def test_the_line_names_the_targeted_commands_and_not_only_the_full_gate(tmp_path):
    # The instruction is actionable: the suite beside the touched script and the targeted cases, both named in a command.
    text = _mkreq(tmp_path, "pr2", PR_BODY)
    line = [ln for ln in text.splitlines() if ln.startswith("SUITES:")][0]
    assert "test-<script>.sh" in line and "pytest tests/<file>.py" in line, line
    assert "once per landing" in line and "belongs to the loop" in line, line


def test_the_line_makes_the_entry_gate_the_childs_own_check(tmp_path):
    # #670: an approved child kept dying at the entry gate on a check it was never told to run. The line now names that gate.
    line = [ln for ln in _mkreq(tmp_path, "pr4", PR_BODY).splitlines() if ln.startswith("SUITES:")][0]
    assert "fast-gate.sh" in line and "exit 0 is a pass" in line, line
    assert "never run it a second time on the same commit" in line, line   # #1527: a bounded gate ends the child's gate step
    assert "shell-suites.sh" in line and "never run it" in line, line   # the machine-wide gate stays the loop's


def test_the_request_still_carries_the_done_check_the_gate_reads(tmp_path):
    # The SUITES line is added beside the DONE CHECK, never in place of it: the run's final gate reads that block.
    text = _mkreq(tmp_path, "pr3", PR_BODY)
    assert "DONE CHECK" in text and '"status": "pass"' in text, text
