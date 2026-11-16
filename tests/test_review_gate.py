"""#599: a dispatch node's graph-declared review gate runs on each passed child, after its checks and before `merge_land`.

Each case drives a run end to end with a stub review command that records its working directory and its argv, and the
test compares the recorded line. Exit 0 approves and the child merges; another exit holds it and records the findings;
exit 3 is "no review here" and the child merges as today. A graph with no review gate lands as today.
"""
import os
import time

import anyio
import pytest

from squad import git, heartbeat
from squad.graph import validate
from squad.runners import dispatch as D
from tests.gitrepo import sibling_repo
from tests.helpers import node, router
from tests.test_dispatch import ev, make, req, routed
from tests.test_waves import writer

ARGV = ["{run_id}", "{child}", "{issue}", "{worktree}", "{base}"]


def stub(tmp_path, code, sleep=0, lead=0):
    """A review command: it waits `lead` seconds, logs `<cwd> <argv>` to review.log, prints two findings, sleeps, and exits with `code`."""
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\nsleep {lead}\necho "$PWD $*" >> "{tmp_path}/review.log"\necho "blocking: a.txt lacks a test"\n'
                 f'echo "blocking: no docs"\nsleep {sleep}\nexit {code}\n')
    p.chmod(0o755)
    return str(p)


def killed(tmp_path):
    """A review command that a signal ends: it prints a finding and kills itself, so its exit status is -15."""
    p = tmp_path / "review-killed.sh"
    p.write_text('#!/bin/sh\necho "blocking: a.txt lacks a test"\nkill -TERM $$\n')
    p.chmod(0o755)
    return str(p)


def bare(d):
    """The router graph without the review gate it ships for the loop (#601): the graph with no review step."""
    name = node(d, "route").pop("review", None)
    d["nodes"] = [n for n in d["nodes"] if n["name"] != name]
    d["gates"] = [g for g in d["gates"] if g["id"] != "review-command"]


def gated(cmd, deadline=30):
    def tweak(d):
        bare(d)
        d["gates"].append({"id": "review-command", "params": ["argv", "deadline"]})
        d["nodes"].append({"name": "child_review", "kind": "gate", "check": "review-command",
                           "params": {"argv": [cmd, *ARGV], "deadline": deadline}})
        node(d, "route")["review"] = "child_review"
    return tweak


def run_one(tmp_path, code, sleep=0, deadline=30, lead=0, **request):
    repo = sibling_repo(tmp_path / "t")
    base = git.head(repo)
    eng = make(tmp_path, {"router": routed(req(**(request or {"task": "issue #599"}))), "route.router.1": writer("a.txt")},
               tweak=gated(stub(tmp_path, code, sleep, lead), deadline), target=repo)
    return eng, repo, base


def child_path(eng):
    return eng.runs.workspace("route.router.1", eng.dispatch.children["route.router.1"]["definition"])


def logged(tmp_path):
    return (tmp_path / "review.log").read_text().splitlines()


@pytest.mark.anyio
@pytest.mark.heavy
async def test_an_approved_child_is_reviewed_once_in_its_worktree_then_merged(tmp_path):
    eng, repo, base = run_one(tmp_path, 0)
    assert (await eng.run()).verdict == "completed"
    wt = child_path(eng)
    assert logged(tmp_path) == [f"{os.path.realpath(wt)} {eng.run_id} route.router.1 599 {wt} {base}"]
    assert (repo / "a.txt").read_text() == "x\n"
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "pass" and land["effect"]["name"] == "merge_land"
    rv = land["effect"]["review"]
    assert rv["command"] == [str(tmp_path / "review.sh"), eng.run_id, "route.router.1", "599", str(wt), base]
    assert rv["exit_code"] == 0 and rv["verdict"] == "approve" and rv["elapsed"] >= 0
    assert rv["findings"] == "blocking: a.txt lacks a test\nblocking: no docs\n"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_the_review_names_the_childs_refs_issue_not_a_blocker_its_prompt_names_first(tmp_path):
    prompt = "Check how #599 added the review gate, then build the view.\nThe commit message carries `Refs #602` (never Closes)."
    eng, repo, base = run_one(tmp_path, 0, prompt=prompt)
    assert (await eng.run()).verdict == "completed"
    [land] = ev(tmp_path, "land")
    assert land["effect"]["review"]["command"][3] == "602"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_non_zero_review_holds_the_child_and_records_its_findings(tmp_path):
    eng, repo, base = run_one(tmp_path, 1)
    v = await eng.run()
    assert v.verdict == "failed"                  # a held child did not land, so the run did not complete
    assert not (repo / "a.txt").exists() and git.head(repo) == base
    assert child_path(eng).exists()               # the held child's worktree stays for a fix round
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_held"]
    assert land["effect"]["name"] == "review" and land["effect"]["head"] == base
    rv = land["effect"]["review"]
    assert rv["exit_code"] == 1 and rv["verdict"] == "hold"
    assert rv["findings"] == "blocking: a.txt lacks a test\nblocking: no docs\n"
    disp = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert disp["outcome"] == "fail" and "review_held" in disp["reasons"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_review_killed_by_a_signal_is_an_infrastructure_stop_not_a_hold(tmp_path):
    """#1575: a review a signal ended never answered, so it is `review_killed`, never `review_held`."""
    repo = sibling_repo(tmp_path / "t")
    base = git.head(repo)
    eng = make(tmp_path, {"router": routed(req(task="issue #1575")), "route.router.1": writer("a.txt")},
               tweak=gated(killed(tmp_path)), target=repo)
    assert (await eng.run()).verdict == "failed"
    assert not (repo / "a.txt").exists() and git.head(repo) == base
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_killed"]
    rv = land["effect"]["review"]
    assert rv["verdict"] == "killed" and rv["exit_code"] == -15 and rv["signal"] == 15
    [round_] = ev(tmp_path, "review_round")
    assert round_["node"] == "route.router.1" and round_["verdict"] == "killed"
    assert round_["exit_code"] == -15 and round_["signal"] == 15


@pytest.mark.anyio
@pytest.mark.heavy
async def test_the_no_review_exit_merges_the_child_as_today(tmp_path):
    eng, repo, _ = run_one(tmp_path, D.REVIEW_NONE_EXIT)
    assert D.REVIEW_NONE_EXIT == 3
    assert (await eng.run()).verdict == "completed"
    assert (repo / "a.txt").exists() and len(logged(tmp_path)) == 1
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "pass" and land["effect"]["review"]["verdict"] == "no_review"


@pytest.mark.anyio
async def test_a_graph_with_no_review_gate_lands_as_today(tmp_path):
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")}, tweak=bare, target=repo)
    assert (await eng.run()).verdict == "completed"
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "pass" and "review" not in land["effect"]
    assert not ev(tmp_path, "review_progress")


async def drive_with_heartbeat(eng, tmp_path, monkeypatch):
    """Run the engine while a heartbeat pass reads it every 0.1 s with a flat CPU and a 0.6 s quiet window. Return the kills."""
    monkeypatch.setenv("HB_RECORD_MIN", str(0.6 / 60))
    monkeypatch.setenv("HB_FLAT_MIN", str(0.01 / 60))
    runs, stamps = tmp_path / "active.txt", tmp_path / "hb"
    runs.write_text(f"{tmp_path}\n")
    stamps.mkdir()
    (stamps / tmp_path.name).write_text(f"10.0 {time.time() - 1000}\n")
    kills, done = [], anyio.Event()

    async def watch():
        while not done.is_set():
            if (tmp_path / "review.log").exists():      # read only while the review runs: a heartbeat pass on its own record
                heartbeat.scan(runs, tmp_path, stamps, now=time.time(), rows=[(os.getpid(), 1, 10.0)],
                               kill=lambda pid, sig: kills.append(sig), mark=lambda f, r: kills.append(r) or True)
            await anyio.sleep(0.1)

    async def go():
        await eng.run()
        done.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(watch)
        tg.start_soon(go)
    return kills


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_slow_review_writes_progress_so_the_heartbeat_never_kills_it(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "REVIEW_TICK_S", 0.1)   # six ticks per quiet window: a loaded runner stalls one or two
    eng, repo, _ = run_one(tmp_path, 0, sleep=1.5)
    kills = await drive_with_heartbeat(eng, tmp_path, monkeypatch)
    assert kills == []
    ticks = ev(tmp_path, "review_progress")
    assert len(ticks) >= 3 and ticks[0]["node"] == "route.router.1" and ticks[0]["gate"] == "child_review"
    assert (repo / "a.txt").exists()


@pytest.mark.anyio
@pytest.mark.heavy
async def test_with_no_progress_the_same_heartbeat_would_kill_the_review(tmp_path, monkeypatch):
    """The control of the case above: the heartbeat pass it runs does kill a review that writes nothing."""
    monkeypatch.setattr(D, "REVIEW_TICK_S", 999)
    eng, _, _ = run_one(tmp_path, 0, sleep=1.5)
    assert await drive_with_heartbeat(eng, tmp_path, monkeypatch)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_the_review_deadline_ends_a_review_and_holds_the_child(tmp_path):
    eng, repo, _ = run_one(tmp_path, 0, sleep=30, deadline=1)
    t0 = time.monotonic()
    assert (await eng.run()).verdict == "failed"
    assert time.monotonic() - t0 < 10
    assert not (repo / "a.txt").exists()
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_deadline"]
    rv = land["effect"]["review"]
    assert rv["verdict"] == "deadline" and rv["exit_code"] is None


@pytest.mark.anyio
@pytest.mark.heavy
async def test_the_review_deadline_holds_a_child_whose_review_prints_nothing_before_it(tmp_path):
    """#612: a review that starts late (a loaded machine) prints nothing before the deadline; the hold does not need its output."""
    eng, repo, _ = run_one(tmp_path, 0, sleep=30, deadline=1, lead=3)
    assert (await eng.run()).verdict == "failed"
    assert not (repo / "a.txt").exists()
    [land] = ev(tmp_path, "land")
    assert land["outcome"] == "fail" and land["reasons"] == ["review_deadline"]
    rv = land["effect"]["review"]
    assert rv["verdict"] == "deadline" and rv["exit_code"] is None and rv["findings"] == ""


def test_the_graph_validates_a_review_gate_and_refuses_a_misplaced_one(tmp_path):
    d = router()
    gated("/bin/true")(d)
    assert validate(d) == []
    bad = router()
    gated("/bin/true")(bad)
    node(bad, "route")["review"] = "scope_gate"
    assert any("review" in e.message for e in validate(bad))
    bad = router()
    gated("/bin/true")(bad)
    del node(bad, "route")["review"]
    node(bad, "route")["checks"].append("child_review")
    assert any("review-command" in e.message for e in validate(bad))


UNPROVEN = ("fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; "
            "unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)")


@pytest.mark.anyio
@pytest.mark.heavy
@pytest.mark.parametrize("status,delivers", [("unproven", True), ("fail", False)])
async def test_an_unproven_child_is_reviewed_and_lands_and_a_failed_one_is_not(tmp_path, status, delivers):
    """#2037: a child whose gate measured nothing (`unproven`, exit 4) holds no red, so its work is delivered: the review
    reads it and an approved head lands, and the landing gate measures it (ADR-0164). Before, only `pass` reached the
    review, so the review gate said `no_child_reviewed` and the loop requeued the child with its commits on no remote."""
    repo = sibling_repo(tmp_path / "t")
    child = writer("a.txt", status=status, reasons=[UNPROVEN if status == "unproven" else "ac_failed"])
    eng = make(tmp_path, {"router": routed(req(task="issue #2037")), "route.router.1": child},
               tweak=gated(stub(tmp_path, 0)), target=repo)
    await eng.run()
    gate = [e for e in ev(tmp_path, "outcome") if e["node"] == "review_gate" or e["node"] == "child_review"]
    if delivers:
        assert len(logged(tmp_path)) == 1, gate
        assert (repo / "a.txt").read_text() == "x\n"
        [land] = ev(tmp_path, "land")
        assert land["outcome"] == "pass" and land["effect"]["review"]["verdict"] == "approve"
    else:
        assert not (tmp_path / "review.log").exists()
        assert not (repo / "a.txt").exists() and not ev(tmp_path, "land")
