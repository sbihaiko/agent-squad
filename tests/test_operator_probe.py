"""Guard: issue #1619. One bounded probe with the real `claude` CLI proves the operator session's tool set and its kernel
bound. A fake CLI never stands in for the permission boundary, so the denial cases run the real sandbox-exec profile.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"


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


def test_a_denial_that_does_not_happen_fails_the_probe(tmp_path):
    probe = load("operator_probe")
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_text("")
    with pytest.raises(probe.ProbeFailed, match="write outside the outbox"):
        probe.check_denials(write_landed=True, read_leaked=False, network_reached=False)


@pytest.mark.claude_bound
@pytest.mark.heavy     # a live `claude -p` call takes seconds: over the 1 s line, so the fast battery deselects it (ADR-0139)
def test_the_real_cli_probe_passes(tmp_path):
    probe = load("operator_probe")
    assert probe.main(["--work", str(tmp_path)]) == 0
