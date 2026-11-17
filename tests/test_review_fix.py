"""#570: a child whose review in its run asks for a fix is fixed by its run, in its own worktree, as entry 2, 3, ... of the child.

Each case drives a run end to end with a stub review command whose exit changes by round. The stub records its working
directory, the round and fix flags the engine gave it, the head it reviewed, and its argv; the test compares the recorded
lines. Exit 4 asks for a fix round; 0 approves; 1 holds (the command has requeued the issue).
"""
import json
import os

import pytest

from squad import gates, git
from squad.record import read
from squad.runners import dispatch as D
from tests.gitrepo import sibling_repo
from tests.test_dispatch import ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

CHILD = "route.router.1"


def stub(tmp_path, codes):
    """A review command: it logs `<cwd> <round> <fix> <head> <argv>` to review.log, prints a two-line review, and exits
    with the code of its round (`codes[round - 1]`)."""
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\necho "$PWD $SQUAD_REVIEW_ROUND $SQUAD_REVIEW_FIX $(git rev-parse HEAD) $*" >> "{tmp_path}/review.log"\n'
                 f'echo "VERDICT: CHANGES"\necho "## Blocking"\necho "- a.txt lacks a test (round $SQUAD_REVIEW_ROUND)"\necho "- no docs"\n'
                 f'set -- {" ".join(str(c) for c in codes)}\nshift $((SQUAD_REVIEW_ROUND - 1))\nexit $1\n')
    p.chmod(0o755)
    return str(p)


def review_text(n):
    return f"VERDICT: CHANGES\n## Blocking\n- a.txt lacks a test (round {n})\n- no docs\n"


def run_with(tmp_path, codes, steps, **kw):
    repo = sibling_repo(tmp_path / "t")
    base = git.head(repo)
    eng = make(tmp_path, {"router": routed(req(task="issue #570")), CHILD: steps}, tweak=gated(stub(tmp_path, codes)), target=repo, **kw)
    return eng, repo, base


def logged(tmp_path):
    return (tmp_path / "review.log").read_text().splitlines()


def child_path(eng):
    return eng.runs.workspace(CHILD, eng.dispatch.children[CHILD]["definition"])


def rounds(tmp_path):
    return [(r["node"], r["round"], r["head"], r["verdict"]) for r in ev(tmp_path, "review_round")]


def entries(tmp_path):
    return [(e["entry"], e["outcome"]) for e in ev(tmp_path, "outcome") if e["node"] == CHILD]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_fix_exit_starts_entry_2_in_the_worktree_with_the_full_review_and_reviews_the_new_head(tmp_path):
    assert D.REVIEW_FIX_EXIT == gates.REVIEW_FIX_EXIT == 4
    eng, repo, base = run_with(tmp_path, [4, 0], [writer("a.txt"), writer("b.txt")])
    assert (await eng.run()).verdict == "completed"
    wt = child_path(eng)
    calls = [c for c in eng.adapter.calls if c.node == CHILD]
    assert [c.entry for c in calls] == [1, 2] and calls[1].workspace == calls[0].workspace == str(wt)
    assert review_text(1) in calls[1].prompt and calls[1].prompt.startswith(calls[0].prompt)
    assert (calls[1].model, calls[1].effort, calls[1].tools) == (calls[0].model, calls[0].effort, calls[0].tools)
    h1, h2 = [r[2] for r in rounds(tmp_path)]
    assert h1 != h2
    rid, real = eng.run_id, os.path.realpath(wt)
    assert logged(tmp_path) == [f"{real} 1 1 {h1} {rid} {CHILD} 570 {wt} {base}", f"{real} 2 1 {h2} {rid} {CHILD} 570 {wt} {base}"]
    assert rounds(tmp_path) == [(CHILD, 1, h1, "fix"), (CHILD, 2, h2, "approve")]
    assert ev(tmp_path, "review_round")[0]["review"]["findings"] == review_text(1)
    assert entries(tmp_path) == [(1, "pass"), (2, "pass")]
    assert git.run(["rev-list", "--count", f"{h1}..{h2}"], repo).stdout.strip() == "1"     # the fix commit sits on the child's branch
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "pass" and land["effect"]["rounds"] == 2 and land["effect"]["review"]["verdict"] == "approve"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_hold_after_a_fix_round_holds_the_child_with_every_round_recorded(tmp_path):
    eng, repo, base = run_with(tmp_path, [4, 1], [writer("a.txt"), writer("b.txt")])
    assert (await eng.run()).verdict == "failed"
    assert [r[3] for r in rounds(tmp_path)] == ["fix", "hold"] and entries(tmp_path) == [(1, "pass"), (2, "pass")]
    assert git.head(repo) == base and child_path(eng).exists()
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_held"] and land["effect"]["rounds"] == 2


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_hold_on_the_first_round_starts_no_fix_entry(tmp_path):
    eng, repo, base = run_with(tmp_path, [1], [writer("a.txt"), writer("b.txt")])
    assert (await eng.run()).verdict == "failed"
    assert [c.entry for c in eng.adapter.calls if c.node == CHILD] == [1]
    assert [r[3] for r in rounds(tmp_path)] == ["hold"] and git.head(repo) == base


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_round_the_budget_cannot_fund_is_told_so_and_no_fix_entry_starts(tmp_path):
    """Router 0.1 and the child 0.2 measured, a cap of 1.2: 0.9 is left, and the next entry's grant is 1.0."""
    eng, repo, base = run_with(tmp_path, [4, 0], [writer("a.txt"), writer("b.txt")], options={"spend_cap": 1.2})
    assert (await eng.run()).verdict == "failed"
    assert [c.entry for c in eng.adapter.calls if c.node == CHILD] == [1]
    [line] = logged(tmp_path)
    assert line.split()[1:3] == ["1", "0"]                     # round 1, no fix round on offer
    assert [r[3] for r in rounds(tmp_path)] == ["hold"]
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_held", "review_fix_unfunded"]
    assert eng.budget.reserved == pytest.approx(0.0)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_fix_entry_that_fails_holds_the_child(tmp_path):
    """A fix entry that ends on its own cap holds the child at once. A fix entry that writes no artifact is no failure
    of that kind: it is the review's finding unanswered, so the run answers it on the next coder and one round still
    stands (ADR-0151, #735, `tests/test_review_streak.py`)."""
    eng, repo, base = run_with(tmp_path, [4, 0], [writer("a.txt"), {"output": None, "spend": 0.1, "turns": 1, "stop_reason": "spend_cap"}])
    assert (await eng.run()).verdict == "failed"
    assert entries(tmp_path)[0] == (1, "pass") and entries(tmp_path)[1][1] == "fail"
    assert len(logged(tmp_path)) == 1 and git.head(repo) == base
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"][0] == "review_fix_failed"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_resume_replays_the_recorded_rounds_and_fix_entries(tmp_path):
    eng, repo, base = run_with(tmp_path, [4, 0], [writer("a.txt"), writer("b.txt")])
    eng.dispatch.prune = lambda: None
    assert (await eng.run()).verdict == "completed"
    events = read(tmp_path / "record.jsonl")
    cut = next(e["pos"] for e in events if e["event"] == "outcome" and e["node"] == CHILD and e["entry"] == 2)
    (tmp_path / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events[:cut]))
    git.run(["reset", "--hard", base], repo)
    (tmp_path / "review.log").unlink()
    eng2 = make(tmp_path, {}, tweak=gated(stub(tmp_path, [4, 0])), target=repo)
    v = await eng2.resume()
    assert v.verdict == "completed" and eng2.adapter.calls == []        # entry 2 is replayed, never run again
    [line] = logged(tmp_path)                                           # round 1 is replayed; only the new head is reviewed
    assert line.split()[1:3] == ["2", "1"]
    assert [r[1:] for r in rounds(tmp_path)][-1][2] == "approve" and [r[1] for r in rounds(tmp_path)] == [1, 2]
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    out = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][-1]
    assert out["outcome"] == "pass"


UNPROVEN = ("fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; "
            "unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)")


@pytest.mark.anyio
@pytest.mark.heavy
@pytest.mark.parametrize("status,delivers", [("unproven", True), ("fail", False)])
async def test_a_fix_round_whose_gate_is_unproven_goes_to_the_next_review(tmp_path, status, delivers):
    """#2050: a fix entry whose gate measured nothing (`unproven`, exit 4) holds no red, so the round goes on to the next
    review, as the first entry does since #2037; an approved head lands, and the landing gate measures it (ADR-0164). A fix
    entry that measured a red still ends the round as `review_fix_failed`."""
    fix = writer("b.txt", status=status, reasons=[UNPROVEN if status == "unproven" else "ac_failed"])
    eng, repo, base = run_with(tmp_path, [4, 0], [writer("a.txt"), fix])
    verdict = (await eng.run()).verdict
    assert entries(tmp_path) == [(1, "pass"), (2, status)]
    [land] = ev(tmp_path, "land")
    if delivers:
        assert verdict == "completed"
        assert [r[3] for r in rounds(tmp_path)] == ["fix", "approve"] and len(logged(tmp_path)) == 2
        assert land["outcome"] == "pass" and land["effect"]["rounds"] == 2
        assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    else:
        assert verdict == "failed" and len(logged(tmp_path)) == 1
        assert land["outcome"] == "fail" and land["reasons"][0] == "review_fix_failed"
        assert git.head(repo) == base
