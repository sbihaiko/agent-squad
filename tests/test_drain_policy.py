"""The one drain policy: each loop-bug label set blocks the four restrictions it names (#1980)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts" / "drainpolicy.py"
_spec = importlib.util.spec_from_file_location("drainpolicy", _PATH)
drainpolicy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drainpolicy)

DISPATCH, CHILD, LANDING, PUBLISH = "dispatch", "child", "landing", "publish"
ALL = {DISPATCH, CHILD, LANDING, PUBLISH}
SHA = "a" * 40
OTHER = "b" * 40


def issue(number, *labels, body="fix"):
    return {"number": number, "title": "t", "body": body, "labels": [{"name": name} for name in labels]}


LABEL_SETS = [
    pytest.param((), set(), id="no loop-bug"),
    pytest.param(("bug", "wave:01"), set(), id="ordinary labels"),
    pytest.param(("loop-buggy",), set(), id="a lookalike label is no loop-bug"),
    pytest.param(("loop-bug",), ALL, id="bare loop-bug drains all four"),
    pytest.param(("loop-bug:landing",), {DISPATCH, CHILD, LANDING}, id="landing blocks dispatch, child and landing"),
    pytest.param(("loop-bug:publish",), {PUBLISH}, id="publish blocks publish only"),
    pytest.param(("loop-bug:dispatch",), ALL, id="dispatch class is outside the four, full drain"),
    pytest.param(("loop-bug:infra",), ALL, id="infra class is outside the four, full drain"),
    pytest.param(("loop-bug:",), ALL, id="a missing class is a full drain"),
    pytest.param(("loop-bug:typo",), ALL, id="an unknown class is a full drain"),
    pytest.param(("loop-bug:publish", "loop-bug:landing"), ALL, id="union of publish and landing"),
    pytest.param(("loop-bug:publish", "loop-bug:typo"), ALL, id="union with an unknown class is full"),
]


@pytest.mark.parametrize("labels,blocked", LABEL_SETS)
@pytest.mark.parametrize("restriction", [DISPATCH, CHILD, LANDING, PUBLISH])
def test_label_set_blocks_each_restriction_it_names(labels, blocked, restriction):
    issues = [issue(596, *labels)] if labels else []
    expected = 596 if restriction in blocked else None
    assert drainpolicy.held(restriction, issues) == expected


def test_two_open_loop_bugs_combine_as_union():
    issues = [issue(596, "loop-bug:publish"), issue(597, "loop-bug:landing")]
    assert drainpolicy.restrictions(issues) == frozenset(ALL)
    assert drainpolicy.held(LANDING, issues) == 597
    assert drainpolicy.held(PUBLISH, issues) == 596
    assert drainpolicy.held(DISPATCH, issues) == 597


def test_publish_only_loop_bug_lets_dispatch_run():
    assert drainpolicy.held(DISPATCH, [issue(596, "loop-bug:publish")]) is None


def test_pull_requests_are_no_loop_bug():
    pull = {"number": 9, "labels": [{"name": "loop-bug"}], "pull_request": {"url": "x"}}
    assert drainpolicy.restrictions([pull]) == frozenset()


def test_publish_hold_accepts_the_reviewed_sha_of_its_fix():
    held = [issue(596, "loop-bug:publish", body=f"reviewed-sha: {SHA}")]
    assert drainpolicy.publish_refusal(held, SHA) is None


def test_publish_hold_refuses_any_other_sha():
    held = [issue(596, "loop-bug:publish", body=f"reviewed-sha: {SHA}")]
    assert drainpolicy.publish_refusal(held, OTHER) == 596


def test_publish_hold_without_a_reviewed_sha_refuses_every_sha():
    assert drainpolicy.publish_refusal([issue(596, "loop-bug:publish")], SHA) == 596


def test_publish_only_refuses_when_a_loop_bug_holds_publish():
    assert drainpolicy.publish_refusal([issue(596, "loop-bug:landing")], OTHER) is None
    assert drainpolicy.publish_refusal([], OTHER) is None


@pytest.mark.parametrize("payload", ["not json", '{"number": 596}', '"loop-bug"'])
def test_unreadable_issue_list_is_unknown(payload):
    proc = subprocess.run([sys.executable, str(_PATH), DISPATCH], input=payload, capture_output=True, text=True)
    assert proc.returncode == 2
