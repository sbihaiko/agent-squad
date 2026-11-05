"""#1378 (ADR-0151): two identical ac-verify failures in a row end the loop as needs-human, with the repeated evidence.

A changed finding, a new commit or a passing gate starts the count again. Findings read the same once line numbers,
shas and whitespace are removed; the criterion each names is kept, and order is not significant.
"""
import pytest

from squad import acrepeat as A
from squad import record
from tests.flows import run_engine, scope_gate

pytestmark = pytest.mark.anyio

FAIL_ONE = 'echo "a.py:12 expected 4 got 5 at 1234abcd"; exit 1'
FAIL_TWO = 'echo "b.py:3 missing"; exit 1'
PASS = "exit 0"
AC_KEY = "ac-verify:ac_failed:" + FAIL_ONE


def ac_graph(max_=3, between=()):
    actor = {"name": "actor", "kind": "agent", "model": "sonnet", "tools": ["Read"], "output_key": "spec",
             "budget": {"stalls": {"min": 1, "max": 10}, "spend": {"min": 0.01, "max": 1.0}}, "deadline": 30, "next": {"fail": "repeat"}}
    ac = {"name": "ac_gate", "kind": "gate", "check": "ac-verify", "params": {"commands_from": "spec"}, "next": {"fail": "repeat"}}
    work = {"name": "work", "kind": "loop", "max": {"value": max_, "min": 1}, "children": [*between, "actor", "ac_gate"]}
    return {"schema_version": 1, "name": "t", "run": {"deadline": 600, "max_parallel": 2, "spend_cap": 10.0}, "facts": [],
            "gates": [{"id": "ac-verify", "params": ["commands_from"]}, {"id": "scope", "params": ["allowed_generated"]}],
            "root": "main", "nodes": [{"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["work"]},
                                      work, actor, ac, *[scope_gate(name) for name in between]]}


def says(*commands):
    return [{"output": {"spec": {"ac_commands": [c]}}, "spend": 0.1} for c in commands]


async def test_two_identical_ac_failures_end_the_run_as_needs_human(tmp_path):
    eng = run_engine(ac_graph(), {"actor": says(FAIL_ONE)}, tmp_path, adapter={"repeat_last": True})
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("needs-human", 10)
    assert "ac_repeated" in v.reasons and AC_KEY in v.reasons
    loop = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "work"][-1]
    assert loop["outcome"] == "needs-human"
    assert "a.py:12 expected 4 got 5 at 1234abcd" in loop["repeated"][AC_KEY]
    assert actor_rounds(tmp_path) == 2


async def test_two_identical_ac_failures_across_a_resume_end_as_needs_human(tmp_path):
    eng = run_engine(ac_graph(), {"actor": says(FAIL_ONE)}, tmp_path, adapter={"repeat_last": True}, options={"stop_after": "ac_gate"})
    first = await eng.run()
    assert first.verdict == "stopped"
    eng2 = run_engine(ac_graph(), {"actor": says(FAIL_ONE)}, tmp_path, adapter={"repeat_last": True})
    v = await eng2.resume()
    assert (v.verdict, v.exit_code) == ("needs-human", 10)
    assert "ac_repeated" in v.reasons and AC_KEY in v.reasons
    assert actor_rounds(tmp_path) == 2


async def test_two_failures_with_different_evidence_do_not_end_across_a_resume(tmp_path):
    cmd = counting_fail(tmp_path)
    first = run_engine(ac_graph(), {"actor": says(cmd)}, tmp_path, adapter={"repeat_last": True}, options={"stop_after": "ac_gate"})
    assert (await first.run()).verdict == "stopped"
    second = run_engine(ac_graph(), {"actor": says(cmd)}, tmp_path, adapter={"repeat_last": True}, options={"stop_after": "ac_gate"})
    assert (await second.resume()).verdict == "stopped"
    third = run_engine(ac_graph(), {"actor": says(cmd)}, tmp_path, adapter={"repeat_last": True})
    v = await third.resume()
    assert v.verdict != "needs-human" and "ac_repeated" not in v.reasons


async def test_different_consecutive_findings_do_not_end_the_run(tmp_path):
    eng = run_engine(ac_graph(), {"actor": says(FAIL_ONE, FAIL_TWO, PASS)}, tmp_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("completed", 0)


async def test_a_passing_gate_between_the_failures_does_not_start_the_count_again(tmp_path):
    eng = run_engine(ac_graph(max_=3, between=("scope_gate",)), {"actor": says(FAIL_ONE)}, tmp_path, adapter={"repeat_last": True})
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("needs-human", 10) and "ac_repeated" in v.reasons


def counting_fail(tmp_path):
    """Fails with the same finding each run, and evidence that names the run count, so each failure reads differently."""
    p = tmp_path / "count"
    return f'n=$(cat "{p}" 2>/dev/null || echo 0); echo $((n+1)) > "{p}"; echo "a.py:12 expected 4 got $n"; exit 1'


def actor_rounds(path):
    return sum(1 for e in record.read(path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "actor")


def test_findings_that_differ_only_in_line_numbers_shas_and_whitespace_read_the_same():
    key = "ac-verify:ac_failed:pytest tests/t.py"
    a = A.fingerprint([key], {key: "t.py:12  boom at 1234abcd\n"})
    b = A.fingerprint([key], {key: "t.py:98 boom at 99ffee0"})
    assert a == b


def test_a_line_number_is_stripped_after_any_source_extension():
    assert A.normalize("a.ts:12 boom") == A.normalize("a.ts:98 boom")


def test_a_decimal_number_is_not_mistaken_for_a_sha():
    assert A.normalize("expected 1234567 got 7654321") != A.normalize("expected 9999999 got 1111111")


def test_a_different_criterion_is_a_different_finding():
    a = A.fingerprint(["ac-verify:ac_failed:pytest tests/t.py"], {})
    b = A.fingerprint(["ac-verify:ac_failed:pytest tests/other.py"], {})
    assert a != b


def test_the_order_of_findings_is_not_significant():
    one, two = "ac-verify:ac_failed:one", "ac-verify:ac_failed:two"
    assert A.fingerprint([one, two], {}) == A.fingerprint([two, one], {})


def test_a_new_commit_starts_the_count_again():
    fp = A.fingerprint(["ac-verify:ac_failed:one"], {})
    streak = A.Streak()
    assert streak.failed(fp, "h1") is False
    assert streak.failed(fp, "h2") is False
    assert streak.failed(fp, "h2") is True
