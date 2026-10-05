"""A gate command runs in the project's environment, not the orchestrator's (squad.procs.clean_env).

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
