"""Guard: issue #1619. One bounded probe with the real `claude` CLI proves the operator session's tool set and its kernel
bound. A fake CLI never stands in for the permission boundary, so the denial cases run the real sandbox-exec profile.
"""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
PROBE_SCRIPT = VOICE / "operator_probe.py"


def load(name):
    spec = importlib.util.spec_from_file_location(name, VOICE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_operator_tool_set_is_read_monitor_taskstop_bash():
    operators = load("operators")
    assert operators.TOOLS.split(",") == ["Read", "Monitor", "TaskStop", "Bash"]


def test_a_session_starts_with_its_tool_set_restricted(tmp_path, monkeypatch):
    operators = load("operators")
    argvs = []

    def fake_call(argv, cwd=None, timeout=None):
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(operators, "call", fake_call)
    operators.start("dm", str(tmp_path / "state"), str(tmp_path / "repo"))
    argv = argvs[0]
    assert argv[argv.index("--tools") + 1] == operators.TOOLS
    assert argv[argv.index("--allowedTools") + 1] == operators.TOOLS


def test_the_real_cli_probe_is_deselected_when_the_cli_is_absent(tmp_path, monkeypatch):
    from tests import conftest
    monkeypatch.setenv("PATH", str(tmp_path))
    assert conftest.claude_present() is False


def test_every_probe_call_runs_through_the_sandbox_wrapper(tmp_path, monkeypatch):
    probe = load("operator_probe")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, "control\n", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.sandboxed("(version 1)(allow default)", "/bin/sh", ["-c", "echo control"], tmp_path, 5)
    assert seen["argv"][0] == probe.WRAPPER
    assert seen["env"]["SQUAD_SIGNAL_PROFILE"] == "(version 1)(allow default)"
    assert seen["env"]["SQUAD_CLAUDE_CLI"] == "/bin/sh"


def test_the_default_work_root_is_outside_every_writable_root():
    probe = load("operator_probe")
    work = probe.work_root()
    assert work.is_relative_to(Path.home())
    for temp in ("/private/var/folders", "/var/folders", "/private/tmp", "/tmp", tempfile.gettempdir()):
        assert not work.is_relative_to(Path(temp).resolve())


@pytest.mark.parametrize("denial,kwargs", [
    ("write outside the outbox", {"write_landed": True}),
    ("read of another scope's inbox", {"read_leaked": True}),
    ("network beyond the model endpoint", {"network_reached": True}),
])
def test_a_denial_that_does_not_happen_fails_the_probe(denial, kwargs):
    probe = load("operator_probe")
    flags = {"write_landed": False, "read_leaked": False, "network_reached": False, **kwargs}
    with pytest.raises(probe.ProbeFailed, match=denial):
        probe.check_denials(**flags)


def test_a_session_with_a_write_tool_fails_the_probe():
    probe = load("operator_probe")
    init = {"tools": ["Read", "Write", "Monitor", "TaskStop", "Bash"], "permissionMode": "dontAsk"}
    with pytest.raises(probe.ProbeFailed, match="Write"):
        probe.check_session(init, {"subtype": "success", "is_error": False})


def test_the_session_is_bounded_in_time_and_spend():
    probe = load("operator_probe")
    assert probe.DEADLINE <= 300.0
    assert float(probe.SPEND) <= 1.0


def test_one_command_repeats_the_probe():
    done = subprocess.run([sys.executable, str(PROBE_SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert done.returncode == 0


@pytest.mark.claude_bound
@pytest.mark.heavy     # a live `claude -p` call takes seconds: over the 1 s line, so the fast battery deselects it (ADR-0139)
def test_the_real_cli_probe_passes():
    probe = load("operator_probe")
    assert probe.main([]) == 0
