"""SPEC 505: the selection and reporting logic of the live contract test, proved with a stub that checks its argv.

The live test (`tests/test_model_contract_live.py`) calls each registry entry on its route. This file proves, with no
network and no model call, which entries it selects, which argv it sends and what one line per entry says.
"""
import importlib
import subprocess

import pytest

import squad.modelreg as mr
from squad import contract_probe as cp

TERRA_ARGV = (
    "codex", "exec", "-m", "gpt-5.6-terra", "-c", "service_tier=fast", "-c", "model_reasoning_effort=low",
    "-s", "read-only", "--ephemeral", "--skip-git-repo-check", "-",
)
OPEN = lambda entry: mr.Eligibility(True, None, None)  # noqa: E731


class Stub:
    """A runner that answers only the argv it expects, and records every line it was given."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, stdin, env, timeout):
        self.calls.append((tuple(argv), timeout))
        out = self.answers.get(tuple(argv))
        if out is None:
            return cp.Outcome(returncode=64, stdout="", stderr="unexpected argv")
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture(scope="module")
def probes():
    return {p.entry_id: p for p in cp.plan(eligibility=OPEN)}


def test_a_retired_entry_is_never_selected(probes):
    retired = {m for m, e in mr.default_registry().entries.items() if e.retired}
    assert retired and not retired & set(probes)


def test_every_live_entry_with_a_route_is_selected(probes):
    live = {m for m, e in mr.default_registry().entries.items() if not e.retired and e.routes}
    assert set(probes) == live


def test_terra_is_probed_through_its_cli_route_with_the_registered_argv(probes):
    p = probes["gpt-5.6-terra"]
    assert p.route == "cli"
    assert p.argv == TERRA_ARGV


def test_a_gateway_only_entry_goes_through_the_gateway_wire(probes):
    p = probes["deepseek-v4-pro"]
    assert p.route == "gateway"
    assert p.argv[:4] == ("claude", "-p", "--model", "claude-deepseek-v4-pro")
    assert p.env["ANTHROPIC_BASE_URL"]


def test_an_answer_gives_one_ok_line_naming_entry_and_route(probes):
    p = probes["gpt-5.6-terra"]
    stub = Stub({TERRA_ARGV: cp.Outcome(0, "pong\n", "")})
    r = cp.run(p, stub, deadline=5)
    assert stub.calls == [(TERRA_ARGV, 5)]
    assert r.status == "ok"
    assert r.line.startswith("gpt-5.6-terra cli OK ")
    assert "\n" not in r.line


def test_an_unexpected_argv_is_a_refusal_not_a_pass(probes):
    p = cp.Probe("x", "cli", ("codex", "wrong"), {})
    r = cp.run(p, Stub({TERRA_ARGV: cp.Outcome(0, "pong", "")}), deadline=5)
    assert r.status == "refused"


def test_a_timeout_names_the_entry_and_its_route(probes):
    p = probes["gpt-5.6-terra"]
    stub = Stub({TERRA_ARGV: subprocess.TimeoutExpired(TERRA_ARGV, 5)})
    r = cp.run(p, stub, deadline=5)
    assert r.status == "timeout"
    assert r.line.startswith("gpt-5.6-terra cli TIMEOUT ") and "5s" in r.line


def test_a_refusal_names_the_entry_route_and_the_reason(probes):
    p = probes["gpt-5.6-terra"]
    r = cp.run(p, Stub({TERRA_ARGV: cp.Outcome(1, "", "usage limit reached")}), deadline=5)
    assert r.status == "refused"
    assert r.line.startswith("gpt-5.6-terra cli REFUSED ") and "usage limit reached" in r.line


def test_an_empty_answer_is_a_refusal(probes):
    r = cp.run(probes["gpt-5.6-terra"], Stub({TERRA_ARGV: cp.Outcome(0, "  \n", "")}), deadline=5)
    assert r.status == "refused"


def test_a_missing_binary_is_reported_unavailable(probes):
    r = cp.run(probes["gpt-5.6-terra"], Stub({TERRA_ARGV: FileNotFoundError("codex")}), deadline=5)
    assert r.status == "unavailable" and r.line.startswith("gpt-5.6-terra cli UNAVAILABLE ")


def test_a_closed_usage_window_is_reported_closed_and_never_called():
    closed = lambda entry: mr.Eligibility(False, "codex usage limit", 1900000000)  # noqa: E731
    stub = Stub({})
    lines = cp.report(cp.plan(only=["gpt-5.6-terra"], eligibility=closed), stub, deadline=5)
    assert stub.calls == []
    assert len(lines) == 1 and lines[0].status == "closed"
    assert lines[0].line.startswith("gpt-5.6-terra cli CLOSED codex usage limit")


def test_the_report_has_one_line_per_selected_entry():
    plan = cp.plan(only=["gpt-5.6-terra", "deepseek-v4-pro"], eligibility=OPEN)
    results = cp.report(plan, Stub({TERRA_ARGV: cp.Outcome(0, "pong", "")}), deadline=5)
    assert [r.entry_id for r in results] == ["gpt-5.6-terra", "deepseek-v4-pro"]
    assert [r.status for r in results] == ["ok", "refused"]


def test_an_unknown_entry_in_only_is_an_error():
    with pytest.raises(KeyError):
        cp.plan(only=["no-such-model"], eligibility=OPEN)


def test_the_output_never_carries_a_key_value(probes):
    p = cp.Probe("x", "gateway", ("claude",), {"ANTHROPIC_API_KEY": "sk-secret-123"})
    r = cp.run(p, Stub({("claude",): cp.Outcome(1, "", "bad key sk-secret-123")}), deadline=5)
    assert "sk-secret-123" not in r.line


def test_the_live_test_is_heavy_and_opt_in():
    live = importlib.import_module("tests.test_model_contract_live")
    marks = {m.name for m in live.pytestmark}
    assert "heavy" in marks and "slow" in marks
    assert live.OPT_IN == "SQUAD_LIVE_CONTRACT"
