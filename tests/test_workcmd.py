"""One policy for a command run in a workspace (squad.workcmd, ticket #146).

Guards: the gate (`ac-verify`) and the landing check (`Dispatch.ac_survey`) both run a command with the virtualenv
stripped, the cap from `SG_AC_TIMEOUT` (default `TIMEOUT_S`), and the output tail kept as evidence. The actor's own
process gets the same clean environment from the SDK adapter, so the scripts never edit `os.environ`.

The default clears the fork's own check (#1785): `contrib/squad-goal/scripts/shell-suites.sh` took 158.69 s real on an
idle machine, so a 120 s cap killed it, recorded `ac_failed:timeout after 120 s` and failed a run whose code was green.
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
    assert workcmd.TIMEOUT_S >= 300      # the project's own suite (159 s real, #1785) has to fit under the default


def test_the_default_cap_covers_the_project_suite_and_SG_AC_TIMEOUT_moves_it(monkeypatch):
    """#1785: the fork's canonical check `contrib/squad-goal/scripts/shell-suites.sh` runs the 41 test-*.sh suites
    next to it and took 158.69 s real on an idle machine. The instructions name that command, so the default cap must
    clear it, and `SG_AC_TIMEOUT` must move the cap for a run that needs more room."""
    monkeypatch.delenv("SG_AC_TIMEOUT", raising=False)
    assert workcmd.ac_timeout() == workcmd.TIMEOUT_S >= 300
    monkeypatch.setenv("SG_AC_TIMEOUT", "600")
    assert workcmd.ac_timeout() == 600
    for junk in ("0", "-1", "soon", ""):
        monkeypatch.setenv("SG_AC_TIMEOUT", junk)
        assert workcmd.ac_timeout() == workcmd.TIMEOUT_S   # not a cap: the default, never a value that kills every command


def test_a_run_uses_the_configured_cap_and_reports_a_timeout_not_a_syntax_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_AC_TIMEOUT", "0.2")
    late = workcmd.run("sleep 5", str(tmp_path))
    assert not late.ok and not late.malformed        # the shell parsed it and it ran; the cap is what stopped it
    assert late.evidence == "timeout after 0.2 s"    # the value it actually used, so the record names the real cap


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
    monkeypatch.delenv("SG_AC_TIMEOUT", raising=False)
    real = subprocess.run

    def late(*a, **kw):
        if kw.get("shell"):      # only the held-out command times out; the git calls of the landing check run for real
            raise subprocess.TimeoutExpired("cmd", kw["timeout"])
        return real(*a, **kw)
    monkeypatch.setattr(subprocess, "run", late)
    r = landing(tmp_path, ["sleep 999"])
    assert r.extra["evidence"] == {"ac_regression:c1": {"ac_failed:sleep 999": f"timeout after {workcmd.TIMEOUT_S:g} s"}}


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
