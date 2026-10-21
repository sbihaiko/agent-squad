"""Ticket #716: print the tailscale serve command for the hub, and check the hub is not on Funnel."""
import json
import subprocess
from pathlib import Path

import pytest

from squad import cli, tailnet_check

FIXTURES = Path(__file__).parent / "fixtures" / "tailscale_serve_status"
STATUS_ARGV = ["tailscale", "serve", "status", "--json"]


class Recorder:
    """A stub for the subprocess seam: records each argv and returns canned stdout."""

    def __init__(self, stdout="", returncode=0):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="")


@pytest.fixture
def no_subprocess(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a subprocess was started")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def test_serve_command_names_the_hub_port_for_the_tailnet():
    assert tailnet_check.serve_command(7700) == "tailscale serve --bg http://127.0.0.1:7700"


def test_serve_command_prints_the_command_for_a_given_port_and_starts_nothing(no_subprocess, capsys):
    assert cli.main(["tailnet", "serve-command", "7801"]) == 0
    assert capsys.readouterr().out == "tailscale serve --bg http://127.0.0.1:7801\n"


def test_serve_command_defaults_to_port_7700(no_subprocess, capsys):
    assert cli.main(["tailnet", "serve-command"]) == 0
    assert capsys.readouterr().out == "tailscale serve --bg http://127.0.0.1:7700\n"


def test_a_tailnet_only_hub_has_no_problems():
    status = json.loads((FIXTURES / "tailnet_only.json").read_text())
    assert tailnet_check.diagnose(status, 7700) == []


def test_a_hub_on_funnel_is_a_problem_that_names_the_port_and_funnel():
    status = json.loads((FIXTURES / "allowfunnel_hub.json").read_text())
    problems = tailnet_check.diagnose(status, 7700)
    assert len(problems) == 1
    assert "7700" in problems[0]
    assert "Funnel" in problems[0]


def test_an_absent_hub_port_is_a_problem_that_names_the_port_and_not_served():
    status = json.loads((FIXTURES / "hub_absent.json").read_text())
    problems = tailnet_check.diagnose(status, 7700)
    assert len(problems) == 1
    assert "7700" in problems[0]
    assert "not served" in problems[0]


def test_check_exits_zero_for_the_tailnet_only_fixture(capsys):
    code = cli.main(["tailnet", "check", "--status", str(FIXTURES / "tailnet_only.json")])
    assert code == 0


def test_check_exits_nonzero_and_names_the_cause_for_the_funnel_fixture(capsys):
    code = cli.main(["tailnet", "check", "--status", str(FIXTURES / "allowfunnel_hub.json")])
    assert code != 0
    assert "Funnel" in capsys.readouterr().out


def test_check_exits_nonzero_and_names_the_cause_when_the_hub_port_is_absent(capsys):
    code = cli.main(["tailnet", "check", "--status", str(FIXTURES / "hub_absent.json")])
    assert code != 0
    assert "not served" in capsys.readouterr().out


def test_without_status_the_only_argv_run_is_tailscale_serve_status_json(monkeypatch):
    recorder = Recorder(stdout=(FIXTURES / "tailnet_only.json").read_text())
    monkeypatch.setattr(subprocess, "run", recorder)
    assert cli.main(["tailnet", "check"]) == 0
    assert recorder.calls == [STATUS_ARGV]


def test_without_status_a_failing_tailscale_names_the_cause(monkeypatch, capsys):
    recorder = Recorder(stdout="", returncode=1)
    monkeypatch.setattr(subprocess, "run", recorder)
    assert cli.main(["tailnet", "check"]) != 0
    assert "tailscale serve status failed" in capsys.readouterr().out
