"""The dependency graph's deadlock validator and its read-only audit (#1983).

An edge "A blocked by B" makes A ineligible while B is open, or while B is closed without delivery. The validator is a
pure function: it takes the graph the reads already hold and the proposed edges, and it returns one verdict. A cycle or a
self-edge is invalid; a blocker the reads could not prove is unknown, never valid; an owner hold is held with its actor.

Every case builds its graph by hand. No case reaches GitHub.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "contrib/squad-goal/scripts/deadlock.py"

_spec = importlib.util.spec_from_file_location("deadlock", MODULE)
deadlock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deadlock)

A, B, C, S, L = "o/r#1", "o/r#2", "o/r#3", "o/r#4", "o/r#5"


def node(kind="task", open=True, delivered=False, parent=None, blockers=(), complete=True, read=True, hold=None,
         outside_loop=False):
    return {"kind": kind, "open": open, "delivered": delivered, "parent": parent, "blockers": list(blockers),
            "complete": complete, "read": read, "hold": hold, "outside_loop": outside_loop}


DEADLOCK_CLASSES = [
    pytest.param({A: node(blockers=[B]), B: node()}, [(B, A)], "invalid", id="plain-cycle"),
    pytest.param({A: node()}, [(A, A)], "invalid", id="self-edge"),
    pytest.param({S: node(kind="spec"), A: node(parent=S)}, [(A, S)], "invalid", id="implicit-parent-child-wait"),
    pytest.param({L: node(kind="loop-bug"), A: node()}, [(L, A)], "invalid", id="loop-bug-blocked-by-dispatch-work"),
    pytest.param({L: node(kind="loop-bug"), A: node(outside_loop=True)}, [(L, A)], "valid",
                 id="loop-bug-blocked-by-work-outside-the-loop"),
    pytest.param({A: node()}, [(A, B)], "unknown", id="edge-to-an-issue-never-read"),
    pytest.param({A: node(), B: node(read=False)}, [(A, B)], "unknown", id="edge-to-an-issue-whose-read-failed"),
    pytest.param({A: node(), B: node(complete=False)}, [(A, B)], "unknown", id="blocker-page-missing"),
    pytest.param({A: node(), B: node(hold="owner")}, [(A, B)], "held", id="open-owner-hold"),
    pytest.param({A: node(), B: node(open=False, delivered=False)}, [(A, B)], "held",
                 id="closed-without-delivery"),
    pytest.param({A: node(), B: node(open=False, delivered=True)}, [(A, B)], "valid", id="closed-and-delivered"),
    pytest.param({A: node(), B: node(), C: node()}, [(A, B), (B, C)], "valid", id="plain-chain"),
]


@pytest.mark.parametrize("graph, proposed, expected", DEADLOCK_CLASSES)
def test_each_deadlock_class_has_its_verdict(graph, proposed, expected):
    verdict = deadlock.validate(graph, proposed)
    assert verdict["status"] == expected, verdict
    if expected == "invalid":
        assert verdict["witness"], "an invalid edge names the path that closes the cycle"


def test_a_failed_read_is_never_valid():
    verdict = deadlock.validate({A: node()}, [(A, "o/r#99")])
    assert verdict["status"] == "unknown"


def test_an_owner_hold_names_its_actor():
    verdict = deadlock.validate({A: node(), B: node(hold="owner")}, [(A, B)])
    assert verdict["status"] == "held" and verdict["actor"] == "owner"


def test_two_edges_that_make_a_cycle_together_are_invalid_as_a_batch():
    assert deadlock.validate({A: node(), B: node()}, [(A, B)])["status"] == "valid"
    assert deadlock.validate({A: node(), B: node()}, [(B, A)])["status"] == "valid"
    assert deadlock.validate({A: node(), B: node()}, [(A, B), (B, A)])["status"] == "invalid"


def test_issue_twelve_of_one_repository_is_never_issue_twelve_of_another():
    graph = {"o/a#12": node(), "o/b#12": node(blockers=["o/a#12"])}
    assert deadlock.validate(graph, [("o/a#12", "o/b#12")])["status"] == "invalid"
    assert deadlock.validate({"o/a#12": node()}, [("o/a#12", "o/b#12")])["status"] == "unknown"


def test_a_missing_page_is_unknown_for_its_component_only():
    graph = {A: node(complete=False), B: node(), C: node(), S: node(kind="spec")}
    assert deadlock.validate(graph, [(A, B)])["status"] == "unknown"
    assert deadlock.validate(graph, [(C, S)])["status"] == "valid"


def test_a_drain_audit_reports_a_loop_bug_on_dispatch_work_as_one_alert(tmp_path):
    issues = [
        {"repo": "o/r", "number": 1, "state": "open", "labels": [{"name": "loop-bug"}], "parent": None,
         "blocked_by_edges": [{"repo": "o/r", "number": 2, "state": "open"}], "blocked_by_complete": True},
        {"repo": "o/r", "number": 2, "state": "open", "labels": [{"name": "bug"}], "parent": None,
         "blocked_by_edges": [], "blocked_by_complete": True},
    ]
    path = tmp_path / "issues.json"
    path.write_text(json.dumps(issues))
    out = subprocess.run([sys.executable, str(MODULE), "audit", str(path)], capture_output=True, text=True, check=True).stdout
    alerts = out.splitlines()
    assert len(alerts) == 1 and alerts[0].startswith("ALERT ") and "loop-bug" in alerts[0], out


def test_the_audit_reports_each_cause_once(monkeypatch):
    issues = [
        {"repo": "o/r", "number": 1, "state": "open", "labels": [{"name": "loop-bug"}], "parent": None,
         "blocked_by_edges": [{"repo": "o/r", "number": 2, "state": "open"}], "blocked_by_complete": True},
        {"repo": "o/r", "number": 2, "state": "open", "labels": [{"name": "bug"}], "parent": None,
         "blocked_by_edges": [], "blocked_by_complete": True},
    ]

    def no_github(*_args, **_kwargs):
        raise AssertionError("the audit reads only the issues it is given")

    monkeypatch.setattr("subprocess.run", no_github)
    causes = deadlock.audit(issues)
    assert [c["status"] for c in causes] == ["invalid"]
    assert deadlock.new_causes(causes, seen=set()) == causes
    assert deadlock.new_causes(causes, seen={c["cause"] for c in causes}) == []
