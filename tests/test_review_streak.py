"""#735 (ADR-0151): a fix round that writes no artifact is the same finding unanswered, not a new one.

A review that asks for changes opens a finding; the fix round answers it, or it does not. A fix round that ends with no
artifact wrote nothing, so the finding stands as it was: it is the finding's first repetition, and it changes
the coder on the next round. Two CHANGES verdicts whose findings differ are two findings, so the coder stays.
The record keeps the child's last turn of that round, so a reader sees why it stopped. The ledger holds the streak of the
issue across runs, and the dispatch reads it, so a requeue or a new run starts where the earlier one stopped (#702).
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from squad import review_streak as R
from squad.adapter import CallResult
from squad.runners import dispatch as D
from squad.runners.children import Children
from tests.gitrepo import sh, sibling_repo
from tests.helpers import node
from tests.test_dispatch import CHAINS, MODEL, child_outs, ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

CHILD = "route.router.1"
OTHER = CHAINS["coding"][1]   # the coder after the child's own model in the coding chain (SPEC 750)
SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


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
    assert R.unanswered("fail", ["review_fix_failed", "child_ended_early"])
    assert R.unanswered("fail", ["session_error:error_during_execution"])
    assert R.unanswered("fail", ["artifact_missing"])
    assert not R.unanswered("fail", ["review_fix_failed", "deadline"])
    assert not R.unanswered("fail", ["node_stall_cap"])
    assert not R.unanswered("pass", [])
    assert R.with_unanswered([fp("a.txt — no test — it fails — add one")], [True]) == [fp("a.txt — no test — it fails — add one")] * 2


def test_a_moved_line_is_not_a_changed_finding():
    assert fp("- a.txt:12 — no test — it fails — add one") == fp("- a.txt:41 — no test — it fails — add one")


PREMISE = "Name the premise the last fix rested on, and test that premise before you attempt a third fix."
FINDING = "- a.txt — the file has no test — it fails — add one"


def fix_prompt(repeated):
    """The prompt one fix entry of the child receives, as `Children.request` builds it, for the review of one finding.
    The engine it reads carries only the two fields that call needs, so no session starts."""
    d = {"prompt": "Fix the finding on this branch.", "skill": {"name": "tdd"}, "model": MODEL, "effort": "medium",
         "tools": ["Read", "Edit"], "stalls": 4}
    eng = SimpleNamespace(run_id="run-1", limits={})
    return Children(eng).request(CHILD, d, SimpleNamespace(amount=1.0), "/tmp/wt", 100.0, MODEL, 2,
                                 {"findings": FINDING}, repeated).prompt


def test_a_first_finding_gets_the_fix_prompt_of_today():
    """#882: the fix round that answers a finding first reads what it reads today. The premise sentence is the second
    round's alone."""
    prompt = fix_prompt(False)
    assert PREMISE not in prompt
    assert FINDING in prompt, "the fix entry still reads the full review"


def test_a_repeated_finding_tells_the_fix_round_to_test_the_premise():
    """#882 (ADR-0151): a finding that repeats the one before it adds exactly one sentence to the fix prompt. The coder
    names the premise its last fix rested on and tests that premise before it attempts a third fix."""
    first = fix_prompt(False)
    assert fix_prompt(True) == first + "\n\n" + PREMISE


class Recorded:
    """The fields of an engine's record the repeat reading reads: its events, and no outcome for a fix entry."""

    def __init__(self):
        self.events = []

    def append(self, event, **kw):
        self.events.append({"event": event, "pos": len(self.events), **kw})


def with_rounds(findings):
    """A dispatch over a record that holds one review round per finding, in order: the child's reviews, nothing run."""
    e = SimpleNamespace(record=Recorded(), outcomes=SimpleNamespace(event=lambda name, entry: None))
    disp = D.Dispatch(e)
    for n, text in enumerate(findings, 1):
        e.record.append("review_round", node=CHILD, round=n, head=f"h{n}", verdict="fix", offered=True,
                        review={"findings": text})
    return disp


def test_the_repeat_a_fix_round_answers_is_read_from_the_childs_reviews():
    """#882 (ADR-0151): "repeat" is the one reading `review_streak.repeated` already makes over the child's recorded
    reviews. A first finding is no repeat, the same finding twice is one, and two different findings are none."""
    assert with_rounds([FINDING]).repeated_round(CHILD, 1) is False
    assert with_rounds([FINDING, FINDING]).repeated_round(CHILD, 2) is True
    assert with_rounds([FINDING, "- b.txt — no docs — it is undocumented — document it"]).repeated_round(CHILD, 2) is False


def test_a_requeued_issues_first_fix_round_is_not_told_of_the_last_fix():
    """#882 (ADR-0151): a requeue cuts a fresh worktree, where the last fix's commit is not, so its first fix round
    cannot name that fix's premise. The issue ledger still moves the coder one place down the chain, and the record is
    the one source of the premise sentence: no premise sentence here."""
    disp = with_rounds([FINDING])
    disp.ledger_moves = lambda d: 1        # the ledger moved this issue's coder across a run
    assert disp.repeated_round(CHILD, 1) is False


def test_the_coder_moves_one_place_per_repeated_finding_until_the_chain_ends():
    """#702: the ledger holds how many times identical findings repeated; the chain moves that many places, and the last
    entry stays when the chain has no further one."""
    chain = ["claude-sonnet-5-5", OTHER, "claude-opus-5-5"]
    assert R.at(chain, 0, "claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert R.at(chain, 1, "claude-sonnet-5-5") == OTHER
    assert R.at(chain, 9, "claude-sonnet-5-5") == "claude-opus-5-5"
    assert R.at([], 0) is None


def no_artifact(last):
    return CallResult(None, 0.1, turns=1, ended={"subtype": "success", "is_error": False, "last": last})


def stub(tmp_path, findings, codes=(4, 4)):
    """A review command: it logs its round and head, prints `findings` for that round, and exits with that round's code;
    the last code repeats."""
    tail = " ".join(str(c) for c in codes)
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\necho "$SQUAD_REVIEW_ROUND $SQUAD_REVIEW_FIX $(git rev-parse HEAD)" >> "{tmp_path}/review.log"\n'
                 f'echo "VERDICT: CHANGES"\necho "## Blocking"\n{findings}\n'
                 f'set -- {tail}\nshift $((SQUAD_REVIEW_ROUND - 1)); exit ${{1:-0}}\n')
    p.chmod(0o755)
    return str(p)


def run(tmp_path, steps, findings, codes=(4, 4)):
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req(task="issue #735")), **steps},
               tweak=gated(stub(tmp_path, findings, codes)), target=repo, options={"spend_cap": 10})
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
    prompts = [c.prompt for c in eng.adapter.calls if c.node == CHILD]
    assert PREMISE not in prompts[1] and PREMISE in prompts[2]     # #882: only the fix round that answers the repeat is told
    assert [e["entry"] for e in outcomes(tmp_path)] == [1, 2, 3]
    last = outcomes(tmp_path)[1]
    assert last["outcome"] == "fail" and last["reasons"] == ["child_ended_early"]
    assert last["diagnostic"]["ended"]["last"] == "I read the finding, then ran out of context before I wrote the file"
    rounds = ev(tmp_path, "review_round")
    assert [(r["round"], r.get("repeated", False)) for r in rounds] == [(1, False), (2, True)]   # the unchanged head is not read again
    assert rounds[1]["review"]["findings"] == rounds[0]["review"]["findings"] and rounds[1]["review"]["elapsed"] == 0.0
    assert len((tmp_path / "review.log").read_text().splitlines()) == 1
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_fix_failed", "child_ended_early"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_two_changes_verdicts_with_different_findings_keep_the_coder(tmp_path):
    findings = 'echo "- a.txt — defect $SQUAD_REVIEW_ROUND — it fails — fix it"'
    eng = run(tmp_path, {CHILD: [writer("a.txt"), writer("b.txt"), writer("c.txt")]}, findings, codes=(4, 4, 0))
    assert (await eng.run()).verdict == "completed"
    assert calls(eng) == [(1, MODEL), (2, MODEL), (3, MODEL)]     # CHANGES twice with different findings: no coder change


def github_target(tmp_path):
    """A target whose origin names a GitHub repository, as a loop run's target does: the ledger key is that address."""
    repo = sibling_repo(tmp_path / "t")
    sh(repo, "git", "remote", "add", "origin", "https://github.com/x/y")
    return repo


def seed_streak(tmp_path, monkeypatch, issue):
    """Two reviews of the issue with the same findings, written by the ledger's own command: one repeated finding, so the
    ledger holds one move down the coder chain. The engine reads it from the state directory the loop would use."""
    st = tmp_path / "state"
    monkeypatch.setenv("SG_STATE", str(st))
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_STATE=str(st), SG_REPO="x/y")
    for rnd in (1, 2):
        p = subprocess.run([sys.executable, str(SCRIPTS / "issue_ledger.py"), "review", f"x/y#{issue}", f"r{rnd}",
                            "changes", "the same finding"], env=env, capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, p.stderr
    assert subprocess.run([sys.executable, str(SCRIPTS / "issue_ledger.py"), "coder", f"x/y#{issue}"],
                          env=env, capture_output=True, text=True, timeout=15).stdout.strip() == "1"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_ledger_repeat_moves_the_next_fix_round_down_the_chain(tmp_path, monkeypatch):
    """#702 (ADR-0151 rule 2): the moves are the issue's fact, not one run's. The review of the first entry reports the two
    earlier runs' identical finding to the ledger, and the fix round the run starts after it answers on the next coder,
    though this run's own record holds one review only."""
    repo = github_target(tmp_path)
    st = tmp_path / "state"
    monkeypatch.setenv("SG_STATE", str(st))
    earlier = " ".join(f'{sys.executable} "{SCRIPTS / "issue_ledger.py"}" review "x/y#735" seed-{i} changes "the same finding";'
                       for i in (1, 2))
    findings = 'echo "- a.txt — the same finding — it fails — fix it"'
    cmd = tmp_path / "review.sh"
    cmd.write_text(f'#!/bin/sh\n[ "$SQUAD_REVIEW_ROUND" = 1 ] && {{ {earlier} }}\n'
                   f'echo "VERDICT: CHANGES"\necho "## Blocking"\n{findings}\n'
                   f'set -- 4 0\nshift $((SQUAD_REVIEW_ROUND - 1)); exit ${{1:-0}}\n')
    cmd.chmod(0o755)
    eng = make(tmp_path, {"router": routed(req(task="issue #735")), CHILD: [writer("a.txt"), writer("b.txt")]},
               tweak=gated(str(cmd)), target=repo, options={"spend_cap": 10})
    assert (await eng.run()).verdict == "completed"
    assert calls(eng) == [(1, MODEL), (2, OTHER)], "the ledger's one move places the fix round on the second chain entry"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_ledger_repeat_moves_a_redispatched_issue_to_the_next_coder(tmp_path, monkeypatch):
    """#702 (ADR-0151 rule 2): a requeue starts a new run that dispatches the issue again with the top of the chain named;
    the ledger's moves lead its first attempt, so the next attempt uses the stronger coder."""
    repo = github_target(tmp_path)
    seed_streak(tmp_path, monkeypatch, "735")
    eng = make(tmp_path, {"router": routed(req(task="issue #735")), CHILD: writer("a.txt")}, target=repo)
    assert (await eng.run()).verdict == "completed"
    assert calls(eng) == [(1, OTHER)], "a new run's first attempt starts one place down the chain"


def held_ledger(tmp_path, monkeypatch, issue, corrupt):
    """An issue's ledger that cannot be read as one: a corrupt file, or no file while the issue's old counters are there.
    ADR-0149 rule 4 makes that a held issue, never an issue with no loop fact."""
    st = tmp_path / "state"
    monkeypatch.setenv("SG_STATE", str(st))
    if corrupt:
        p = st / "state" / f"x__y__{issue}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not a ledger\n")
    else:
        monkeypatch.setenv("SG_REPO", "x/y")   # the old counters were the primary repository's
        (st / "auto").mkdir(parents=True, exist_ok=True)
        (st / "auto" / f"requeues-{issue}").write_text("2\n")


@pytest.mark.anyio
@pytest.mark.heavy
@pytest.mark.parametrize("corrupt", [True, False], ids=["corrupt", "missing-beside-old-counters"])
async def test_a_held_ledger_starts_no_child_session(tmp_path, monkeypatch, corrupt):
    """#702 (ADR-0149 rule 4): a ledger that is corrupt, or missing beside the issue's old counters, holds the issue. The
    run starts no child session and asks the owner with the ledger's own cause; it never starts the child on the top coder
    as if the issue had no loop fact."""
    repo = github_target(tmp_path)
    held_ledger(tmp_path, monkeypatch, "735", corrupt)
    eng = make(tmp_path, {"router": routed(req(task="issue #735")), CHILD: writer("a.txt")}, target=repo)
    v = await eng.run()
    assert calls(eng) == [], "a held ledger starts no child session"
    assert v.verdict == "needs-human", v.reasons
    assert "ledger_held" in v.reasons and any("holds the issue" in r for r in v.reasons), v.reasons
    [child] = [e for e in outcomes(tmp_path)]
    assert (child["outcome"], child["reasons"][0]) == ("needs-human", "ledger_held")


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_dependent_of_a_held_issue_is_held_with_the_ledger_cause(tmp_path, monkeypatch):
    """#702 (ADR-0149 rule 4, ADR-0029): a child that runs after a ledger-held issue is held with it, never failed
    `after_failed`. The dependent's failure would fold the dispatch to `failed` and hide the ledger's cause, though the
    owner is the one who must answer the frozen issue."""
    repo = github_target(tmp_path)
    held_ledger(tmp_path, monkeypatch, "735", corrupt=True)
    eng = make(tmp_path, {"router": routed(req(task="issue #735"), req("codebase-design", tools=["Read"], after=[0]),
                                          req("codebase-design", tools=["Read"], after=[1])),
                          CHILD: writer("a.txt")}, target=repo)
    v = await eng.run()
    assert calls(eng) == [], "neither the held issue nor its dependents start a session"
    outs = child_outs(tmp_path)
    assert (outs[CHILD]["outcome"], outs[CHILD]["reasons"][0]) == ("needs-human", "ledger_held")
    for dep_name in ("route.router.2", "route.router.3"):   # the hold follows the chain of dependents, it never fails one
        dep = outs[dep_name]
        assert dep["outcome"] == "needs-human" and dep["reasons"][0] == "after_held", dep
    assert v.verdict == "needs-human", v.reasons
    assert any("holds the issue" in r for r in v.reasons), v.reasons


@pytest.mark.anyio
@pytest.mark.heavy
async def test_first_failure_asks_the_owner_for_a_held_issue_not_a_cancelled_sibling(tmp_path, monkeypatch):
    """#702 (ADR-0029): a ledger hold is not a failed attempt, so `first_failure` never cancels the held issue's siblings.
    A cancelled sibling folds the dispatch to `failed` and hides the ledger's cause; the run asks the owner instead."""
    def first_failure(d):
        node(d, "route")["join"] = "first_failure"
    repo = github_target(tmp_path)
    held_ledger(tmp_path, monkeypatch, "735", corrupt=True)
    eng = make(tmp_path, {"router": routed(req("diagnosing-bugs"), req(task="issue #735")),
                          "route.router.1": writer("a.txt")}, tweak=first_failure, target=repo)
    v = await eng.run()
    outs = child_outs(tmp_path)
    assert outs["route.router.1"]["outcome"] == "pass", "a held issue never cancels its siblings"
    assert (outs["route.router.2"]["outcome"], outs["route.router.2"]["reasons"][0]) == ("needs-human", "ledger_held")
    assert v.verdict == "needs-human", v.reasons
    assert any("holds the issue" in r for r in v.reasons), v.reasons
