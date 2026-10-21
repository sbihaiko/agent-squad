"""#735 (ADR-0151): a fix round that writes no artifact is the same finding unanswered, not a new one.

A review that asks for changes opens a finding; the fix round answers it, or it does not. A fix round that ends
`artifact_missing` wrote nothing, so the finding stands as it was: it is the finding's first repetition, and it changes
the coder on the next round. Two CHANGES verdicts whose findings differ are two findings, so the coder stays.
The record keeps the child's last turn of that round, so a reader sees why it stopped. The ledger holds the streak of the
issue across runs, and the dispatch reads it, so a requeue or a new run starts where the earlier one stopped (#702).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from squad import review_streak as R
from squad.adapter import CallResult
from tests.gitrepo import sh, sibling_repo
from tests.helpers import node, router
from tests.test_dispatch import MODEL, ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

CHILD = "route.router.1"
OTHER = node(router(), "route")["allow"]["models"][1]   # the coder after the child's own model in the route node's allow list
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
    assert R.unanswered("fail", ["review_fix_failed", "artifact_missing"])
    assert not R.unanswered("fail", ["review_fix_failed", "deadline"])
    assert not R.unanswered("pass", [])
    assert R.with_unanswered([fp("a.txt — no test — it fails — add one")], [True]) == [fp("a.txt — no test — it fails — add one")] * 2


def test_a_moved_line_is_not_a_changed_finding():
    assert fp("- a.txt:12 — no test — it fails — add one") == fp("- a.txt:41 — no test — it fails — add one")


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
    assert [e["entry"] for e in outcomes(tmp_path)] == [1, 2, 3]
    last = outcomes(tmp_path)[1]
    assert last["outcome"] == "fail" and last["reasons"] == ["artifact_missing"]
    assert last["diagnostic"]["ended"]["last"] == "I read the finding, then ran out of context before I wrote the file"
    rounds = ev(tmp_path, "review_round")
    assert [(r["round"], r.get("repeated", False)) for r in rounds] == [(1, False), (2, True)]   # the unchanged head is not read again
    assert rounds[1]["review"]["findings"] == rounds[0]["review"]["findings"] and rounds[1]["review"]["elapsed"] == 0.0
    assert len((tmp_path / "review.log").read_text().splitlines()) == 1
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_fix_failed", "artifact_missing"]


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
