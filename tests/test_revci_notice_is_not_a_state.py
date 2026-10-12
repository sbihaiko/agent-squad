"""revci-<pr>.out keeps its progress and terminal notices out of the `#<pr> <sha9> ` summary namespace (#1767).

The namespace carries the run's summary line only -- the line with `CI=` and the review verdict. A notice printed inside
it (CI_DISPATCH, SUPERSEDED, CI_TIMEOUT) has no `CI=` and no verdict, so `hub.py`'s `pr_state` took it for the run's
result and the card showed a state with no verdict behind it, while the loop stopped re-queueing reviews for that head
(the PR #1706 stall, 2026-10-11).
"""
import importlib.util
import os
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

SHA = "1e38e5a7b41c0d2e"          # current head of the PR under test
SHA9 = SHA[:9]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_revci_notice", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """A squad state dir with the files the loop leaves behind: the run's output and its markers in auto/."""
    st = tmp_path / "state"
    (st / "auto").mkdir(parents=True)
    monkeypatch.setattr(hub, "STATE", st)
    monkeypatch.delenv("SG_REREVIEW_MINUTES", raising=False)
    return st


def _out(st, lines):
    (st / "revci-1491.out").write_text("\n".join(lines) + "\n")


def _launched(st, pr=1491, sha9=SHA9):
    """`auto/launched-<pr>-<sha9>`: the loop queued a review for this head (autopilot writes it before revci.sh starts)."""
    (st / "auto" / f"launched-{pr}-{sha9}").write_text("")


def _gate(st, pr=1491, sha9=SHA9, reason="merging"):
    """The loop's own two files (#1771): auto/.pass stamps the pass, auto/gate-<pr> carries its word for this head.
    pr_state reads the card's readiness off them, so a test that expects a verdict must leave them behind too."""
    at = int(time.time())
    (st / "auto" / ".pass").write_text(f"{at}\n")
    (st / "auto" / f"gate-{pr}").write_text(f"{sha9}\t{at}\t{reason}\tthe merge is going ahead\n")


def _pr(n=1491, head=SHA):
    return {"n": n, "t": "Hub: the OPEN PRS card says why a review is late", "u": "2026-10-11T09:00:00Z", "h": head,
            "d": False, "ci": [], "ms": "clean", "o": "2026-10-11T08:00:00Z"}


def test_a_ci_dispatch_notice_is_not_the_run_result(hub, state):
    """A head whose push created no workflow run gets a notice ~90 s in; it is not a result, so the head is still being worked on."""
    _launched(state)
    _out(state, [f"#1491 {SHA9} CI_DISPATCH: the head has no check run at all, so the gate would wait forever; checks.yml dispatched on gui/1491"])
    assert hub.pr_state(_pr()) == ["running", "reviewing"]


def test_a_superseded_notice_is_not_the_run_result(hub, state):
    """A run that ends because the head moved leaves a notice, not a verdict: the card must not read a state out of it."""
    _launched(state)
    _out(state, [f"#1491 {SHA9} SUPERSEDED by 9c0ffee12"])
    assert hub.pr_state(_pr()) == ["running", "reviewing"]


def test_a_notice_outside_the_namespace_leaves_the_state_alone(hub, state):
    """The shape after the fix: the notice lives under `NOTICE `, out of the `#<pr> <sha9> ` namespace (the positive control)."""
    _launched(state)
    _out(state, [f"NOTICE #1491 {SHA9} CI_DISPATCH: the head has no check run at all, so the gate would wait forever; checks.yml dispatched on gui/1491"])
    assert hub.pr_state(_pr()) == ["running", "reviewing"]


def test_the_summary_line_still_decides_the_state(hub, state):
    """The summary line keeps its authority: `CI=` plus a verdict is what the card reads (guards against over-filtering).
    A green summary line is not the verdict by itself since #1771: the loop's gate line is, and it says the merge goes ahead."""
    _launched(state)
    _gate(state)
    _out(state, [f"NOTICE #1491 {SHA9} CI_DISPATCH: dispatched", f"#1491 {SHA9} clean CI=success:9 VERDICT: APPROVE"])
    ps = hub.pr_state(_pr())   # the gate line's own tip rides third, as on every card the loop judged (#1771)
    assert ps[0] == "ready" and ps[1] == "ready to merge"


def test_a_notice_after_the_summary_does_not_erase_the_verdict(hub, state):
    """A notice written after the summary (a later run on the same head) must not become the selected line."""
    _launched(state)
    _out(state, [f"#1491 {SHA9} clean CI=success:9 VERDICT: CHANGES", f"#1491 {SHA9} CI_TIMEOUT after 45m"])
    assert hub.pr_state(_pr()) == ["changes", "changes requested"]
