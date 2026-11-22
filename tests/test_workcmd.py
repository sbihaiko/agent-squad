"""One policy for a command run in a workspace (squad.workcmd, ticket #146).

Guards: the gate (`ac-verify`) and the landing check (`Dispatch.ac_survey`) both run a command with the virtualenv
stripped, the cap from `SG_AC_TIMEOUT` (default `TIMEOUT_S`), and the output tail kept as evidence. The actor's own
process gets the same clean environment from the SDK adapter, so the scripts never edit `os.environ`.

The default clears the fork's own check (#1785): `contrib/squad-goal/scripts/shell-suites.sh` took 158.69 s real on an
idle machine, so a 120 s cap killed it, recorded `ac_failed:timeout after 120 s` and failed a run whose code was green.
"""
import asyncio
import pathlib
import subprocess
import time
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


def test_a_timeout_is_unproven_and_the_second_on_the_same_tree_and_command_is_a_red(tmp_path, monkeypatch):
    """#1874: a command the machine could not finish measured nothing, so the first timeout is unproven (ADR-0164).
    The same tree and the same command timing out twice is a hang the change brought, so the second is a measured red."""
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one"],
                   cwd=repo, check=True)
    first = workcmd.run("sleep 5", str(repo), timeout=0.2)
    assert not first.ok and first.unproven and first.evidence == "timeout after 0.2 s"
    second = workcmd.run("sleep 5", str(repo), timeout=0.2)
    assert not second.ok and not second.unproven, f"the second timeout is a red: {second.unproven}"
    assert "measured red" in second.evidence
    other = workcmd.run("sleep 5; true", str(repo), timeout=0.2)
    assert not other.ok and other.unproven, "another command on the same tree has its own first timeout"


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
    e = SimpleNamespace(target=str(tmp_path), fix_run=False, artifacts={"c1": {"ac_commands": cmds}})
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
    d = Dispatch(SimpleNamespace(target=str(repo), scale=1, fix_run=False, artifacts={"c1": {"ac_commands": cmds}}))
    d.ac_before = {"c1": True}
    return asyncio.run(d.landing_check("c1", git.head(repo)))


def test_the_landing_check_carries_the_failing_command_evidence_into_the_record(tmp_path):
    r = landing(tmp_path, ["echo boom; exit 1"])
    assert not r.ok and r.reasons == ["ac_regression:c1"]
    assert r.extra["evidence"] == {"ac_regression:c1": {"ac_failed:echo boom; exit 1": "boom"}}


def test_a_timeout_through_the_landing_path_is_unproven_and_never_a_regression(tmp_path, monkeypatch):
    """#1874: the first timeout on a tree and command measured nothing, so the landing carries it as an obligation and
    never as a regression; the second timeout on the same (tree, command) is the measured red."""
    monkeypatch.delenv("SG_AC_TIMEOUT", raising=False)
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    real = subprocess.run

    def late(*a, **kw):
        if kw.get("shell"):      # only the held-out command times out; the git calls of the landing check run for real
            raise subprocess.TimeoutExpired("cmd", kw["timeout"])
        return real(*a, **kw)
    monkeypatch.setattr(subprocess, "run", late)
    r = landing(tmp_path, ["sleep 999"])
    assert r.ok and r.extra == {"unproven": ["sleep 999"]}, f"the timeout landed as {r.extra}"


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


# A child's test command takes `gates` slots of the machine-wide budget (squad/caps.py, #567) before it runs, and runs with
# the worker count it took (#608): a full budget makes it wait and say so, never fail.
BOUNDED = 'test "$PYTEST_XDIST_AUTO_NUM_WORKERS" = {n} && test "$SQUAD_GATE_WORKERS" = {n}'


def budget_env(monkeypatch):
    for var in ("SG_SERIAL_HELD", "SQUAD_GATE_WORKERS", "SG_CHILD_TEST_WORKERS", "SG_CHILD_SLOT_WAIT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SQUAD_CAPS_POLL", "0.05")


def hold_every_gate_slot():
    from squad import caps
    holder = subprocess.Popen(["sleep", "30"])
    c = caps.ceiling()
    assert c.try_take_many("gates", "other-gate", c.caps["gates"], pid=holder.pid)
    return c, holder


def test_a_child_test_command_waits_for_gate_slots_says_so_and_runs_with_the_count_it_took(tmp_path, monkeypatch, capsys):
    import threading
    import time
    budget_env(monkeypatch)
    c, holder = hold_every_gate_slot()
    try:
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", workcmd.run(BOUNDED.format(n=2), str(tmp_path))))
        t.start()
        time.sleep(0.3)
        assert t.is_alive() and "r" not in out          # the budget is full: the command has not started
        c.release_holder("gates", "other-gate")
        t.join(5)
        assert out["r"].ok, out["r"].evidence
    finally:
        holder.kill()
    assert "waiting" in capsys.readouterr().err and "other-gate" not in [w["holder"] for w in c.waiting()]
    assert c.occupancy()["gates"] == 0                  # the slots go back when the command ends


def test_a_wait_past_its_bound_runs_the_command_with_one_worker_and_never_fails_it(tmp_path, monkeypatch, capsys):
    budget_env(monkeypatch)
    monkeypatch.setenv("SG_CHILD_SLOT_WAIT", "0.2")
    c, holder = hold_every_gate_slot()
    try:
        r = workcmd.run(BOUNDED.format(n=1), str(tmp_path))
    finally:
        holder.kill()
    assert r.ok, r.evidence
    assert "1 worker" in capsys.readouterr().err


def test_a_command_that_gets_fewer_slots_than_it_asked_runs_with_the_slots_it_holds(tmp_path, monkeypatch):
    """#734: 3 free slots answer a child that asks 4 with 3, so the command runs with 3 workers instead of waiting."""
    budget_env(monkeypatch)
    monkeypatch.setenv("SG_CHILD_TEST_WORKERS", "4")
    monkeypatch.setenv("SQUAD_CAPS_DIR", str(tmp_path / "caps"))
    from squad import caps
    c = caps.ceiling()
    holder = subprocess.Popen(["sleep", "30"])
    assert len(c.try_take_many("gates", "other-gate", c.caps["gates"] - 3, pid=holder.pid)) == c.caps["gates"] - 3
    try:
        r = workcmd.run(BOUNDED.format(n=3), str(tmp_path), timeout=10)
        assert r.ok, r.evidence
        assert c.occupancy()["gates"] == c.caps["gates"] - 3   # the command's slots go back when it ends, the holder's stand
    finally:
        holder.kill()
        holder.wait()
    assert c.occupancy()["gates"] == 0                          # and the killed holder's slots go with it (#1573)


def test_inside_a_gate_holder_a_command_takes_no_slot_and_keeps_the_holder_count(tmp_path, monkeypatch):
    budget_env(monkeypatch)
    monkeypatch.setenv("SG_SERIAL_HELD", "gate")
    monkeypatch.setenv("SQUAD_GATE_WORKERS", "5")
    c, holder = hold_every_gate_slot()
    try:
        r = workcmd.run(BOUNDED.format(n=5), str(tmp_path), timeout=5)
    finally:
        holder.kill()
    assert r.ok, r.evidence


def test_the_sdk_adapter_bounds_the_actor_pytest_workers(tmp_path, monkeypatch):
    budget_env(monkeypatch)
    monkeypatch.setenv("SG_CHILD_TEST_WORKERS", "3")
    req = SimpleNamespace(workspace=str(tmp_path), model="m", tools=[], max_stalls=1, max_spend=1, effort=None, instructions=None,
                          session_id=None, node="n")
    opts = SdkAdapter(query=object(), options_cls=lambda **kw: SimpleNamespace(**kw)).options(req)
    assert opts.env["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "3" and opts.env["SQUAD_GATE_WORKERS"] == "3"


CHILD_GATE = str(pathlib.Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/child-gate.sh")
TIMED_SUITE = "sleep 5; bash " + CHILD_GATE + " ./suite.sh"


def committed_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one"],
                   cwd=repo, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    return repo, sha


def suite_row(state, suite, sha, run_s):
    """One row as child-gate.sh writes it: finish, start, wait, load at start, load at finish, exit, suite, tree sha."""
    state.mkdir(parents=True, exist_ok=True)
    finish = int(time.time())
    with open(state / "child-suite-history", "a") as fh:
        fh.write(f"{finish}\t{finish - run_s}\t0\t0.1\t0.1\t0\t{suite}\t{sha}\n")


def test_a_command_measured_longer_than_the_cap_stays_unproven(tmp_path, monkeypatch):
    """#1936: a suite of this tree that ran past the cap shows the cap is too short, not the change, so every timeout stays unproven."""
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    repo, sha = committed_repo(tmp_path)
    suite_row(tmp_path / "state", repo / "suite.sh", sha, run_s=60)
    first = workcmd.run(TIMED_SUITE, str(repo), timeout=0.2)
    second = workcmd.run(TIMED_SUITE, str(repo), timeout=0.2)
    assert first.unproven and second.unproven and not second.ok, second.evidence


def test_a_suite_row_of_another_tree_measures_nothing(tmp_path, monkeypatch):
    """#1936: a row names the tree it ran in; another tree's row says nothing about this tree's hang."""
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    repo, _ = committed_repo(tmp_path)
    suite_row(tmp_path / "state", repo / "suite.sh", "0" * 40, run_s=60)
    first = workcmd.run(TIMED_SUITE, str(repo), timeout=0.2)
    second = workcmd.run(TIMED_SUITE, str(repo), timeout=0.2)
    assert first.unproven
    assert not second.ok and not second.unproven and "measured red" in second.evidence, second.evidence


def test_run_names_its_held_slots_to_the_command(tmp_path, monkeypatch):
    """#1936: a command that holds gate slots is told how many, so child-gate.sh takes none again inside its cap."""
    budget_env(monkeypatch)
    monkeypatch.delenv("SG_GATE_SLOTS_HELD", raising=False)
    assert workcmd.run('test "$SG_GATE_SLOTS_HELD" = 2', str(tmp_path)).ok
    monkeypatch.setenv("SG_CHILD_SLOT_WAIT", "0.2")
    c, holder = hold_every_gate_slot()
    try:
        r = workcmd.run('test -z "${SG_GATE_SLOTS_HELD+x}"', str(tmp_path))
    finally:
        holder.kill()
    assert r.ok, r.evidence


def test_a_command_cannot_extend_its_cap(tmp_path, monkeypatch):
    """#1936: no file, pid or variable the command writes moves its cap; the command and the process it started end at the cap."""
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    repo, _ = committed_repo(tmp_path)
    fake = repo / "child-gate.sh"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    cmd = f'mkdir -p "$SG_STATE" && echo 1 > "$SG_STATE/slots" && sh {fake} ./suite.sh; echo done'
    started = time.monotonic()
    r = workcmd.run(cmd, str(repo), timeout=0.3)
    assert not r.ok and r.unproven and r.evidence == "timeout after 0.3 s", r.evidence
    assert time.monotonic() - started < 5, "the command outlived its cap"


def test_ac_verify_answers_unproven_for_an_explained_timeout_on_two_dispatches(tmp_path, monkeypatch):
    """#1936: a timeout a suite of this tree explains is unproven on every dispatch, so a green change can land."""
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("SG_AC_TIMEOUT", "0.2")
    repo, sha = committed_repo(tmp_path)
    suite_row(tmp_path / "state", repo / "suite.sh", sha, run_s=60)
    for _ in range(2):
        r = gates.ac_verify({"commands_from": "spec"}, gates.GateInput(artifacts={"spec": {"ac_commands": [TIMED_SUITE]}}, facts={}, cwd=str(repo)))
        assert r.ok and r.unproven, (r.findings, r.evidence)


def pytest_cmd(args):
    import sys
    return f"{sys.executable} -m pytest -q -p no:cacheprovider -p no:deepeval -p no:xdist {args}"


def two_tests(tmp_path):
    (tmp_path / "test_two.py").write_text("def test_a():\n    assert True\n\ndef test_b():\n    assert True\n")


def test_a_pytest_k_expression_pytest_refuses_is_malformed_not_a_red(tmp_path):
    """A `-k` expression pytest cannot parse exits 4 before any test runs (#2765): the command text is wrong, nothing was
    measured, so the gate records it malformed and never fails the run for it."""
    two_tests(tmp_path)
    r = workcmd.run(pytest_cmd("test_two.py -k 'test_a or draws the four fields'"), str(tmp_path))
    assert not r.ok and r.malformed, r


def test_a_missing_test_file_stays_a_measured_red(tmp_path):
    """A test file the child never wrote also exits 4, with `no tests ran` (#2765): that is undelivered work, a red."""
    two_tests(tmp_path)
    r = workcmd.run(pytest_cmd("test_missing.py"), str(tmp_path))
    assert not r.ok and not r.malformed and not r.unproven, r


def test_a_bad_k_after_a_red_command_stays_a_measured_red(tmp_path):
    """A compound command whose first suite named a failure keeps that red, even when its last call is a bad `-k`."""
    (tmp_path / "test_red.py").write_text("def test_r():\n    assert False\n")
    two_tests(tmp_path)
    r = workcmd.run(f"{pytest_cmd('test_red.py')}; {pytest_cmd(chr(39).join(['test_two.py -k ', 'a b', '']))}", str(tmp_path))
    assert not r.ok and not r.malformed and not r.unproven, r
