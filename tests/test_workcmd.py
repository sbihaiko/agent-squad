"""One policy for a command run in a workspace (squad.workcmd, ticket #146).

Guards: the gate (`ac-verify`) and the landing check (`Dispatch.ac_survey`) both run a command with the virtualenv
stripped, the 120 s timeout, and the output tail kept as evidence. The actor's own process gets the same clean
environment from the SDK adapter, so the scripts never edit `os.environ`.
"""
import asyncio
import subprocess
from types import SimpleNamespace

from squad import gates, git, workcmd
from squad.runners.dispatch import Dispatch
from squad.sdk_adapter import SdkAdapter


def venv_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    monkeypatch.setenv("PATH", f"{tmp_path}/venv/bin:/usr/bin:/bin")


SEES_CLEAN = 'test -z "$VIRTUAL_ENV" && test "$PATH" = "/usr/bin:/bin"'


def test_clean_env_drops_a_virtualenv_so_python3_is_the_system_python(tmp_path):
    venv = str(tmp_path / "venv")
    out = workcmd.clean_env({"VIRTUAL_ENV": venv, "PATH": f"{venv}/bin:/usr/bin:/bin", "HOME": "/h"})
    assert "VIRTUAL_ENV" not in out and out["PATH"] == "/usr/bin:/bin" and out["HOME"] == "/h"
    assert workcmd.clean_env({"PATH": "/a"}) == {"PATH": "/a"}


def test_run_maps_exit_code_output_tail_and_timeout(tmp_path):
    assert workcmd.run("true", str(tmp_path)).ok
    bad = workcmd.run("echo start; python3 -c 'print(\"x\" * 3000)'; echo end; exit 3", str(tmp_path))
    assert not bad.ok and bad.evidence.endswith("end") and len(bad.evidence) <= workcmd.EVIDENCE_TAIL
    late = workcmd.run("sleep 5", str(tmp_path), timeout=0.2)
    assert not late.ok and late.evidence == "timeout after 0.2 s"
    assert workcmd.TIMEOUT_S == 120


def test_the_gate_command_sees_the_project_python(tmp_path, monkeypatch):
    venv_env(monkeypatch, tmp_path)
    r = gates.ac_verify({"commands_from": "spec"}, gates.GateInput(artifacts={"spec": {"ac_commands": [SEES_CLEAN]}}, facts={}, cwd=str(tmp_path)))
    assert r.ok


def survey_all(tmp_path, cmds):
    e = SimpleNamespace(target=str(tmp_path), artifacts={"c1": {"ac_commands": cmds}})
    d = Dispatch(e)
    d.landed = {"c1": {}}
    return asyncio.run(d.ac_survey())


def survey(tmp_path, cmd):
    return survey_all(tmp_path, [cmd])


def test_a_landing_check_keeps_the_evidence_of_every_failing_command(tmp_path):
    got, evidence = survey_all(tmp_path, ["echo one; exit 1", "true", "echo two; exit 2"])
    assert got == {"c1": False}
    assert evidence == {"c1": {"ac_failed:echo one; exit 1": "one", "ac_failed:echo two; exit 2": "two"}}


def landing(tmp_path, cmds):
    """A landing check over a real repo. The child's commands passed before the landing, so a failure now is a regression."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git.run(["init", "-q"], repo, check=True)
    git.run(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], repo, check=True)
    d = Dispatch(SimpleNamespace(target=str(repo), scale=1, artifacts={"c1": {"ac_commands": cmds}}))
    d.ac_before = {"c1": True}
    return asyncio.run(d.landing_check("c1", git.head(repo)))


def test_the_landing_check_carries_the_failing_command_evidence_into_the_record(tmp_path):
    r = landing(tmp_path, ["echo boom; exit 1"])
    assert not r.ok and r.reasons == ["ac_regression:c1"]
    assert r.extra["evidence"] == {"ac_regression:c1": {"ac_failed:echo boom; exit 1": "boom"}}


def test_a_timeout_through_the_landing_path_carries_the_timeout_note(tmp_path, monkeypatch):
    real = subprocess.run

    def late(*a, **kw):
        if kw.get("shell"):      # only the held-out command times out; the git calls of the landing check run for real
            raise subprocess.TimeoutExpired("cmd", kw["timeout"])
        return real(*a, **kw)
    monkeypatch.setattr(subprocess, "run", late)
    r = landing(tmp_path, ["sleep 999"])
    assert r.extra["evidence"] == {"ac_regression:c1": {"ac_failed:sleep 999": "timeout after 120 s"}}


def test_the_landing_check_sees_the_project_python(tmp_path, monkeypatch):
    venv_env(monkeypatch, tmp_path)
    got, evidence = survey(tmp_path, SEES_CLEAN)
    assert got == {"c1": True} and evidence == {}


def test_a_landing_check_failure_carries_evidence(tmp_path):
    got, evidence = survey(tmp_path, "echo boom; exit 1")
    assert got == {"c1": False} and evidence == {"c1": {"ac_failed:echo boom; exit 1": "boom"}}


def test_the_sdk_adapter_hands_the_actor_a_clean_environment(tmp_path, monkeypatch):
    venv_env(monkeypatch, tmp_path)
    req = SimpleNamespace(workspace=str(tmp_path), model="m", tools=[], max_stalls=1, max_spend=1, effort=None, instructions=None,
                          session_id=None, node="n")
    opts = SdkAdapter(query=object(), options_cls=lambda **kw: SimpleNamespace(**kw)).options(req)
    assert opts.env["VIRTUAL_ENV"] == "" and opts.env["PATH"] == "/usr/bin:/bin"
