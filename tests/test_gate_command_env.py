"""A gate command runs in the project's environment, not the orchestrator's (squad.workcmd.clean_env).

Guards: `ac-verify` strips `VIRTUAL_ENV` and the virtualenv's bin directory from `PATH`, so `python3 -m pytest`
inside a command reaches the project's python3 instead of ours. Without it, `uv run` makes the command find our
python3, which holds no pytest, and the gate fails a correct patch.
"""
import os

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


def test_a_clean_ac_command_still_passes_on_its_exit_code(tmp_path):
    assert run(tmp_path, "true").ok
    assert run(tmp_path, "exit 3").findings == ["ac_failed:exit 3"]
