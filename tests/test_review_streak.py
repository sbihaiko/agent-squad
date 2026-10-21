"""#735 (ADR-0151): a fix round that writes no artifact is the same finding unanswered, not a new one.

A review that asks for changes opens a finding; the fix round answers it, or it does not. A fix round that ends
`artifact_missing` wrote nothing, so the finding stands as it was: it is the finding's first repetition, and it changes
the coder on the next round. Two CHANGES verdicts whose findings differ are two findings, so the coder stays.
The record keeps the child's last turn of that round, so a reader sees why it stopped.
"""
import pytest

from squad import review_streak as R
from squad.adapter import CallResult
from tests.gitrepo import sibling_repo
from tests.test_dispatch import MODEL, ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

CHILD = "route.router.1"
OTHER = "claude-gpt-5.6-terra"       # the coder after the child's own model in the route node's allow list


def fp(text):
    return R.fingerprint(text)


def test_identical_findings_twice_change_the_coder():
    assert not R.repeated([fp("a.txt — no test — it fails — add one")])
    assert R.repeated([fp("a.txt — no test — it fails — add one")] * 2)
    assert R.coder(["claude-sonnet-5-5", OTHER], [fp("same")] * 2) == OTHER


def test_two_different_findings_need_two_identical_ones_to_change_the_coder():
    seq = [fp("a.txt — no test — it fails — add one"), fp("b.txt — no docs — it is undocumented — document it")]
    assert not R.repeated(seq)
    assert R.coder(["claude-sonnet-5-5", OTHER], seq) == "claude-sonnet-5-5"


def test_a_fix_round_that_wrote_no_artifact_repeats_the_finding():
    assert R.unanswered("fail", ["review_fix_failed", "artifact_missing"])
    assert not R.unanswered("fail", ["review_fix_failed", "deadline"])
    assert not R.unanswered("pass", [])
    assert R.with_unanswered([fp("a.txt — no test — it fails — add one")], [True]) == [fp("a.txt — no test — it fails — add one")] * 2


def test_a_moved_line_is_not_a_changed_finding():
    assert fp("- a.txt:12 — no test — it fails — add one") == fp("- a.txt:41 — no test — it fails — add one")


def no_artifact(last):
    return CallResult(None, 0.1, turns=1, ended={"subtype": "success", "is_error": False, "last": last})


def stub(tmp_path, findings):
    """A review command: it logs its round and head, prints `findings` for that round, and always asks for a fix (exit 4)."""
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\necho "$SQUAD_REVIEW_ROUND $SQUAD_REVIEW_FIX $(git rev-parse HEAD)" >> "{tmp_path}/review.log"\n'
                 f'echo "VERDICT: CHANGES"\necho "## Blocking"\n{findings}\nexit 4\n')
    p.chmod(0o755)
    return str(p)


def run(tmp_path, steps, findings):
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req(task="issue #735")), **steps},
               tweak=gated(stub(tmp_path, findings)), target=repo, options={"spend_cap": 10})
    return eng


def calls(eng):
    return [(c.entry, c.model) for c in eng.adapter.calls if c.node == CHILD]


def outcomes(tmp_path):
    return [e for e in ev(tmp_path, "outcome") if e["node"] == CHILD]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_fix_round_with_no_artifact_changes_the_coder_on_the_first_repetition(tmp_path):
    same = 'echo "- a.txt — the file has no test — it fails — add one"'
    eng = run(tmp_path, {CHILD: [writer("a.txt")],
                         f"{CHILD}@2": [no_artifact("I read the finding, then ran out of context before I wrote the file")],
                         f"{CHILD}@3": [no_artifact("still nothing written: the same finding stands")]}, same)
    assert (await eng.run()).verdict == "failed"
    assert calls(eng) == [(1, MODEL), (2, MODEL), (3, OTHER)]      # the first repetition moves the coder, not the second
    assert [e["entry"] for e in outcomes(tmp_path)] == [1, 2, 3]
    last = outcomes(tmp_path)[1]
    assert last["outcome"] == "fail" and last["reasons"] == ["artifact_missing"]
    assert last["diagnostic"]["ended"]["last"] == "I read the finding, then ran out of context before I wrote the file"
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_fix_failed", "artifact_missing"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_two_changes_verdicts_with_different_findings_keep_the_coder(tmp_path):
    findings = 'echo "- a.txt — defect $SQUAD_REVIEW_ROUND — it fails — fix it"'
    eng = run(tmp_path, {CHILD: [writer("a.txt"), writer("b.txt"), writer("c.txt")]}, findings)
    assert (await eng.run()).verdict is not None
    assert calls(eng) == [(1, MODEL), (2, MODEL), (3, MODEL)]     # CHANGES twice with different findings: no coder change
