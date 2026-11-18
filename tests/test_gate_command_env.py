"""A gate command runs in the project's environment, not the orchestrator's (squad.workcmd.clean_env).

Guards: `ac-verify` strips `VIRTUAL_ENV` and the virtualenv's bin directory from `PATH`, so `python3 -m pytest`
inside a command reaches the project's python3 instead of ours. Without it, `uv run` makes the command find our
python3, which holds no pytest, and the gate fails a correct patch.
"""
import os
import shlex

from squad import gates


def run(tmp_path, cmd):
    return gates.ac_verify({"commands_from": "spec"}, gates.GateInput(artifacts={"spec": {"ac_commands": [cmd]}}, facts={}, cwd=str(tmp_path)))


def test_an_ac_command_does_not_see_the_orchestrators_virtualenv(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    monkeypatch.setenv("PATH", f"{tmp_path}/venv/bin:/usr/bin:/bin")
    r = run(tmp_path, 'test -z "$VIRTUAL_ENV" && test "$PATH" = "/usr/bin:/bin"')
    assert r.ok and r.findings == []
    assert os.environ["VIRTUAL_ENV"] == str(tmp_path / "venv")     # the parent environment is untouched


def test_an_ac_command_that_fails_still_reads_as_a_failure(tmp_path):
    r = run(tmp_path, "exit 3")
    assert not r.ok and r.findings == ["ac_failed:exit 3"]


def test_an_ac_command_with_a_trailing_parenthetical_is_malformed_not_failed(tmp_path):
    r = run(tmp_path, "true (exit 0, no FAIL lines)")
    assert r.ok                                     # recorded, and never a failed check on its own (ADR-0164, #1803)
    assert r.findings == ["malformed:true (exit 0, no FAIL lines)"]
    assert not any(k.startswith("ac_failed") for k in r.evidence)


MISSING_PROGRAM = "/nonexistent/.venv/bin/python -c 'print(1)'"


def test_a_command_whose_program_cannot_start_is_unproven_not_failed(tmp_path):     # #1803
    r = run(tmp_path, MISSING_PROGRAM)
    assert r.ok                                          # nothing ran, so nothing was proved false (ADR-0164)
    assert r.findings == [f"malformed:{MISSING_PROGRAM}"]
    assert list(r.evidence) == [f"malformed:{MISSING_PROGRAM}"]


def test_a_real_failing_command_beside_an_unstartable_one_still_fails_the_gate(tmp_path):     # #1803
    r = gates.ac_verify({"commands_from": "spec"},
                        gates.GateInput(artifacts={"spec": {"ac_commands": [MISSING_PROGRAM, "exit 3"]}}, facts={}, cwd=str(tmp_path)))
    assert not r.ok
    assert "ac_failed:exit 3" in r.findings and f"malformed:{MISSING_PROGRAM}" in r.findings


def test_a_program_that_started_and_exited_127_is_a_measured_failure(tmp_path):     # #1803
    # Exit 127 alone is not "cannot start": here `sh` started and chose 127. Only the shell's own not-found
    # on the leading program makes a command malformed.
    r = run(tmp_path, "sh -c 'exit 127'")
    assert not r.ok
    assert r.findings == ["ac_failed:sh -c 'exit 127'"]


def test_a_compound_command_that_ran_and_exited_127_is_a_measured_failure(tmp_path):     # #1803
    r = run(tmp_path, "true; exit 127")
    assert not r.ok and r.findings == ["ac_failed:true; exit 127"]


def test_the_gate_fails_on_the_one_measured_finding_beside_two_unproven_ones(tmp_path):     # #1803
    r = gates.ac_verify({"commands_from": "spec"},
                        gates.GateInput(artifacts={"spec": {"ac_commands": [MISSING_PROGRAM, "true (exit 0)", "exit 3"]}}, facts={}, cwd=str(tmp_path)))
    assert not r.ok
    assert r.findings == [f"malformed:{MISSING_PROGRAM}", "malformed:true (exit 0)", "ac_failed:exit 3"]


def test_a_program_that_exists_but_cannot_execute_is_unproven(tmp_path):     # #1803
    # Exit 126 is the shell's "found, not executable": here the kernel refuses the shebang interpreter.
    # The program never ran, so nothing was measured, whatever its file mode says.
    prog = tmp_path / "check"
    prog.write_text("#!/nonexistent/interpreter\n")
    prog.chmod(0o755)
    r = run(tmp_path, "./check")
    assert r.ok
    assert r.findings == ["malformed:./check"]


def test_a_missing_program_after_a_cd_is_unproven(tmp_path):     # #1803
    # The leading word is `cd`, a builtin that always starts; the program that failed is the one after it.
    (tmp_path / "sub").mkdir()
    r = run(tmp_path, "cd sub && ./missing")
    assert r.ok
    assert r.findings == ["malformed:cd sub && ./missing"]


def test_a_program_that_ran_after_a_cd_and_exited_127_is_a_measured_failure(tmp_path):     # #1803
    # `./check` exists under `sub` and starts; it chose 127, so it is a measured failure, never malformed.
    sub = tmp_path / "sub"
    sub.mkdir()
    check = sub / "check"
    check.write_text("#!/bin/sh\nexit 127\n")
    check.chmod(0o755)
    r = run(tmp_path, "cd sub && ./check")
    assert not r.ok
    assert r.findings == ["ac_failed:cd sub && ./check"]


def test_the_gate_filters_a_stubbed_result_by_its_kind_not_by_its_label(tmp_path, monkeypatch):     # #1803
    # A hand-built result isolates the gate's own filter from `run`'s classification: the gate must
    # read the `malformed` flag and keep the measured failure, whatever the command text says.
    from squad import workcmd
    stubs = {
        "malformed: looking command": workcmd.CmdResult(False, "boot", malformed=True),
        "measured red command": workcmd.CmdResult(False, "one failed"),
    }
    monkeypatch.setattr(workcmd, "run", lambda cmd, cwd: stubs[cmd])
    r = gates.ac_verify({"commands_from": "spec"},
                        gates.GateInput(artifacts={"spec": {"ac_commands": list(stubs)}}, facts={}, cwd=str(tmp_path)))
    assert not r.ok
    assert r.findings == ["malformed:malformed: looking command", "ac_failed:measured red command"]


GATE_UNPROVEN_LINE = ("fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; "
                      "unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)")
UNPROVEN_CMD = f"echo {shlex.quote(GATE_UNPROVEN_LINE)}; exit 4"


def test_a_gate_that_could_not_run_is_unproven_not_a_failed_check(tmp_path):     # #1825
    # The fast gate answers exit 4 with its own unproven line when it measured nothing: no battery ran and nothing is
    # red (ADR-0164). The gate result says unproven, never a failing finding that holds the run. The evidence decides,
    # so the line must be on the output: the exit code alone proves nothing.
    r = run(tmp_path, UNPROVEN_CMD)
    assert r.ok and r.outcome == "unproven"
    assert r.findings == [f"unproven:{UNPROVEN_CMD}"]


BOUNDED_ALREADY_LINE = ("fast-gate: bounded already at ab12cd34e: a child spends one bounded attempt per head (#1527); "
                        "the entry gate and the landing own the obligation (ADR-0164), so commit and report done "
                        "(log /tmp/fast-gate.log)")


def folded(tmp_path, cmd):
    """The gate result of one command, folded into the run verdict the way the engine folds a record (#2337)."""
    from squad import verdict as V
    r = run(tmp_path, cmd)
    reasons = [f"ac-verify:{f}" for f in r.findings]
    rows = [{"event": "outcome", "node": "final_gate", "entry": 1, "outcome": r.outcome, "reasons": reasons},
            {"event": "outcome", "node": "main", "entry": 1, "outcome": r.outcome, "reasons": reasons}]
    events = [{"event": "run_start", "run_id": "r1", "graph_name": "g", "graph_hash": "h", "engine_options": {},
               "root": "main", "plan": ["main", "final_gate"]}] + rows
    for i, e in enumerate(events, 1):
        e["pos"] = i
    return r, V.fold(events)


def test_an_entry_gate_at_exit_four_with_no_red_folds_to_completed_unproven(tmp_path):     # #2337
    # The fast gate answers exit 4 on a line that names no red and carries no pytest summary: the bounded-attempt line
    # (#1527) is one of them. The gate measured nothing, so it is unproven (ADR-0164), and a run whose only failing
    # outcome is unproven completes with unproven:<node> instead of failing and losing its commits.
    cmd = f"echo {shlex.quote(BOUNDED_ALREADY_LINE)}; exit 4"
    r, v = folded(tmp_path, cmd)
    assert r.outcome == "unproven", r.findings
    assert (v.verdict, v.exit_code) == ("completed", 0), v.reasons
    assert v.reasons == ["unproven:final_gate"]


def test_a_measured_red_at_exit_four_still_folds_to_failed(tmp_path):     # #2337
    # A measurement that names a red is never released by the exit code: the defect holds the run (ADR-0164).
    cmd = "printf 'FAIL contrib/squad-goal/scripts/test-x.sh::case\\n'; exit 4"
    r, v = folded(tmp_path, cmd)
    assert r.outcome == "fail" and (v.verdict, v.exit_code) == ("failed", 1), v.reasons
    assert any(reason.startswith("ac-verify:ac_failed:") for reason in v.reasons), v.reasons


def test_an_exit_four_with_no_summary_and_no_red_is_unproven(tmp_path):     # #2337
    # The gate's own words are not required: an exit-4 line that names no red carries no such word, and the run must not
    # lose the commits of a measurement that never happened. A summary or a named red still makes it a measured failure.
    r = run(tmp_path, "sh -c 'exit 4'")
    assert r.ok and r.outcome == "unproven"
    assert r.findings == ["unproven:sh -c 'exit 4'"]


def test_a_measured_shell_red_at_exit_four_is_not_unproven(tmp_path):     # #1825
    # A battery that ran and failed a shell suite names the defect. It prints no pytest summary, so the counts are
    # empty; the red is still measured and must never be released as unproven (ADR-0164).
    r = run(tmp_path, "printf 'FAIL contrib/squad-goal/scripts/test-x.sh::case\\n'; exit 4")
    assert r.outcome == "fail" and not r.unproven
    assert r.findings[0].startswith("ac_failed:")


def test_one_measured_red_beside_an_unproven_check_still_fails_the_gate(tmp_path):     # #1825
    # A measured red wins: the unproven command never releases the defect another command measured (ADR-0164).
    r = gates.ac_verify({"commands_from": "spec"},
                        gates.GateInput(artifacts={"spec": {"ac_commands": [UNPROVEN_CMD, "exit 3"]}}, facts={}, cwd=str(tmp_path)))
    assert r.outcome == "fail"
    assert "ac_failed:exit 3" in r.findings and f"unproven:{UNPROVEN_CMD}" in r.findings


def test_a_clean_ac_command_still_passes_on_its_exit_code(tmp_path):
    assert run(tmp_path, "true").ok
    assert run(tmp_path, "exit 3").findings == ["ac_failed:exit 3"]


def test_the_shells_own_words_for_a_missing_program_agree(tmp_path):
    """The suite runs under more than one shell and the CI's is not the laptop's: bash writes `No such file or directory`
    and dash writes `not found` for the same command (#1803, #1818). The reading must not follow the platform."""
    from squad import workcmd
    (tmp_path / "sub").mkdir()
    missing = str(tmp_path / "sub" / "missing")
    cmd = "cd sub && ./missing"
    assert workcmd._cannot_start(cmd, str(tmp_path), {}, f"/bin/bash: {missing}: No such file or directory\n")
    assert workcmd._cannot_start(cmd, str(tmp_path), {}, f"/bin/dash: 1: {missing}: not found\n")


def test_a_shebang_interpreter_that_is_not_there_is_read_from_the_file(tmp_path):
    """dash says only `not found` for a script whose shebang names a missing interpreter, so the shell's words cannot tell
    that case from a missing program. The file itself can (#1803, #1818)."""
    from squad import workcmd
    prog = tmp_path / "check"
    prog.write_text("#!/nonexistent/interpreter\n")
    prog.chmod(0o755)
    assert workcmd._cannot_start("./check", str(tmp_path), {}, "/bin/dash: 1: ./check: not found\n")
    good = tmp_path / "good"
    good.write_text("#!/bin/sh\nexit 126\n")
    good.chmod(0o755)
    assert not workcmd._cannot_start("./good", str(tmp_path), {}, ""), "a started program is measured, not malformed"
