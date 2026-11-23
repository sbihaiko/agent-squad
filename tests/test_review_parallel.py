"""A dispatch reviews a child when that child passes, so the reviews of several passing children run at once.

Today every review runs inside `Dispatch.land`, after `run_of` returned and in request order: the second review starts
only when the first ends. A dispatch that passes k children pays sum(review). These tests pin the overlap: two passing
write children, a review command that stamps its start and end, and the assertion that the two intervals intersect.
They also pin what the overlap must never cost: the review is a reading only (a fix round it asks for runs in `land`,
after the entry's children settled), it never holds a slot a child is waiting for, its funding is decided against a
settled budget, a pause stops it, and its `{base}` never moves between a run and its resume.
"""
import json
import time

import anyio
import pytest

from squad import git
from squad.control import Control
from squad.record import read
from tests.gitrepo import sibling_repo
from tests.helpers import node
from tests.test_dispatch import ev, make, req, routed
from tests.test_review_gate import gated
from tests.test_waves import writer

A, B = "route.router.1", "route.router.2"
REVIEW_S = 1.0
PAUSE_SLACK_S = 0.5     # the moment a cancel needs to reach a waiting review on a loaded runner, never a second pause


def pause_window(tries, pause):
    """(first, last) for one round's `review_try` events: the start of the first try the engine recorded, and the last
    moment a pause may end the run -- one retry pause plus a fixed slack after that try. The runner's own pace before the
    try is outside both, so a loaded runner gets no room to wait (#1612)."""
    first = tries[0]["ts"] - (tries[0]["review"].get("elapsed") or 0.0)
    window = pause + PAUSE_SLACK_S           # one retry pause, plus the moment a cancel needs to arrive
    return first, first + window


def timed_stub(tmp_path, sleep=REVIEW_S):
    """A review command that appends `<start|end> <child> <seconds>` to review.log, sleeps, and exits 0 (approve)."""
    p = tmp_path / "review.sh"
    p.write_text(
        "#!/bin/sh\n"
        'now() { python3 -c \'import time; print("%.6f" % time.time())\'; }\n'
        f'echo "start $2 $(now)" >> "{tmp_path}/review.log"\n'
        f"sleep {sleep}\n"
        f'echo "end $2 $(now)" >> "{tmp_path}/review.log"\n'
        "exit 0\n"
    )
    p.chmod(0o755)
    return str(p)


def overlap_stub(tmp_path, wait_s=8):
    """A review command that proves overlap by a rendezvous, never by a sleep.

    Each invocation records its start, then waits a bounded time for the sibling's start line. Two live reviews proceed
    at once, so the guard holds on a loaded runner whose tasks start seconds apart; a sequential engine makes each
    invocation wait the whole bound, and the two windows still do not intersect (#1645).
    """
    p = tmp_path / "review.sh"
    ticks = int(wait_s / 0.1)
    p.write_text(
        "#!/bin/sh\n"
        'now() { python3 -c \'import time; print("%.6f" % time.time())\'; }\n'
        f'echo "start $2 $(now)" >> "{tmp_path}/review.log"\n'
        "i=0\n"
        f'while [ "$(grep -c \'^start\' "{tmp_path}/review.log" || true)" -lt 2 ] && [ "$i" -lt {ticks} ]; do\n'
        "  sleep 0.1; i=$((i + 1))\n"
        "done\n"
        f'echo "end $2 $(now)" >> "{tmp_path}/review.log"\n'
        "exit 0\n"
    )
    p.chmod(0o755)
    return str(p)


def code_stub(tmp_path, codes, sleep=0, fix_child=None):
    """A review command that sleeps, prints a two-line review, and exits with `codes[round - 1]` (4 asks for a fix).
    `fix_child` limits the exit codes to one child: every other child's review approves."""
    p = tmp_path / "review.sh"
    guard = f'[ "$2" = "{fix_child}" ] || exit 0\n' if fix_child else ""
    p.write_text(f'#!/bin/sh\nsleep {sleep}\n{guard}echo "VERDICT: CHANGES"\necho "- a.txt lacks a test"\n'
                 f'set -- {" ".join(str(c) for c in codes)}\nshift $((SQUAD_REVIEW_ROUND - 1))\nexit $1\n')
    p.chmod(0o755)
    return str(p)


def base_stub(tmp_path, slow=None, sleep=0.6):
    """A review command that logs `<child> <head> <base>` and approves: the argv carries the base it read. `slow` names one
    child whose reading waits `sleep` seconds, so a sibling's reading ends and its merge lands while this one still runs."""
    guard = f'[ "$2" = "{slow}" ] && sleep {sleep}\n' if slow else ""
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\n{guard}echo "$2 $(git rev-parse HEAD) $5" >> "{tmp_path}/review.log"\nexit 0\n')
    p.chmod(0o755)
    return str(p)


def spans(tmp_path):
    """{"route.router.1": {"start": t, "end": t}, ...} read from the review stub's log."""
    out = {}
    for line in (tmp_path / "review.log").read_text().splitlines():
        kind, child, t = line.split()
        out.setdefault(child, {})[kind] = float(t)
    return out


def two_children():
    """The router asks for two write children that both pass, each landing one file of its own."""
    return {"router": routed(req(), req("diagnosing-bugs")), A: writer("a.txt"), B: writer("b.txt")}


def stamped(step, marks):
    """A scripted step that marks `(node, entry, time)` when its call starts, then returns the step as it stands."""
    def call(r):
        marks.append((r.node, r.entry, time.time()))
        return step
    return call


@pytest.mark.anyio
@pytest.mark.heavy
async def test_two_passing_children_are_reviewed_at_the_same_time(tmp_path):
    """The reviews of two passing children overlap: each starts when its child passes, not when the earlier review ends."""
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, two_children(), tweak=gated(overlap_stub(tmp_path)), target=repo)
    assert (await eng.run()).verdict == "completed"
    log = spans(tmp_path)
    assert set(log) == {A, B}, log
    a, b = log[A], log[B]
    assert a["start"] < a["end"] and b["start"] < b["end"], log
    assert a["start"] < b["end"] and b["start"] < a["end"], (
        "the reviews ran one after the other, not at the same time: "
        f"{A} {a['start']:.3f}-{a['end']:.3f}, {B} {b['start']:.3f}-{b['end']:.3f}")
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    assert [e["node"] for e in ev(tmp_path, "land")] == [A, B]   # merges stay in request order, only the reviews overlap


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_resume_never_reviews_a_head_its_record_already_holds(tmp_path):
    """A run cut between a child's review and its merge replays the recorded round: the head is never reviewed twice.
    The new timing records the review of a head before its land, so a resume must consult `review_round` before it
    starts any review for a replayed pass."""
    repo = sibling_repo(tmp_path / "t")
    base = git.head(repo)
    stub = timed_stub(tmp_path, sleep=0)
    eng = make(tmp_path, {"router": routed(req()), A: writer("a.txt")}, tweak=gated(stub), target=repo)
    eng.dispatch.prune = lambda: None
    assert (await eng.run()).verdict == "completed"
    events = read(tmp_path / "record.jsonl")
    cut = next(i for i, e in enumerate(events) if e["event"] == "land")   # keep the recorded round, drop the merge
    (tmp_path / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events[:cut]))
    git.run(["reset", "--hard", base], repo)
    (tmp_path / "review.log").unlink()

    eng2 = make(tmp_path, {}, tweak=gated(stub), target=repo)
    assert (await eng2.resume()).verdict == "completed"
    assert eng2.adapter.calls == []                                  # the child's pass is replayed, never run again
    assert not (tmp_path / "review.log").exists()                    # the recorded round is replayed, never reviewed again
    assert [(e["node"], e["round"]) for e in ev(tmp_path, "review_round") if e["node"] == A] == [(A, 1)]
    assert (repo / "a.txt").read_text() == "x\n"                     # the replayed round still lands the child's work


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_fix_the_entry_can_fund_once_its_siblings_settle_is_not_recorded_unfunded(tmp_path):
    """The fix round's grant is taken in `land`, after the entry's children settled. Taken earlier, while a sibling's own
    reservation is still out, the cap looks short by that reservation: an identical run then holds a child it can fix."""
    repo = sibling_repo(tmp_path / "t")
    # cap 2.2: router 0.1, two children that reserve 1.0 each. The first child measures 0.2 while the second still holds
    # 1.0, so a grant read at that moment finds 0.9 left; once the second settles, 1.7 is left and the fix is funded.
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), A: [writer("a.txt"), writer("a.txt")],
                          B: {**writer("b.txt"), "delay": 0.4}},
               tweak=gated(code_stub(tmp_path, [4, 0], fix_child=A)), target=repo, options={"spend_cap": 2.2})
    assert (await eng.run()).verdict == "completed"
    assert [(e["entry"], e["outcome"]) for e in ev(tmp_path, "outcome") if e["node"] == A] == [(1, "pass"), (2, "pass")]
    assert [(e["node"], e["round"], e["verdict"]) for e in ev(tmp_path, "review_round") if e["node"] == A] == [
        (A, 1, "fix"), (A, 2, "approve")]
    lands = {e["node"]: e for e in ev(tmp_path, "land")}
    assert set(lands) == {A, B} and all(e["outcome"] == "pass" for e in lands.values())
    assert lands[A]["effect"]["rounds"] == 2


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_review_never_holds_a_slot_a_waiting_child_needs(tmp_path):
    """With one parallel slot, a review in flight must not keep the next child out: the review takes a slot of its own
    bound, never the slot a waiting child needs, so the child's call keeps the whole deadline it was given."""
    repo = sibling_repo(tmp_path / "t")
    marks = []

    def one_slot(d):
        gated(timed_stub(tmp_path, sleep=REVIEW_S))(d)
        node(d, "route")["max_parallel"] = 1

    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs", after=[0])),
                          A: stamped(writer("a.txt"), marks), B: stamped(writer("b.txt"), marks)},
               tweak=one_slot, target=repo)
    assert (await eng.run()).verdict == "completed"
    log = spans(tmp_path)
    started = {n: t for n, _e, t in marks if n != "router"}
    assert set(started) == {A, B}, marks
    assert started[B] < log[A]["end"], (
        "the second child waited for the first child's review: its call started after the review ended, "
        f"{B} {started[B]:.3f} vs review {A} {log[A]['start']:.3f}-{log[A]['end']:.3f}")


def test_the_pause_window_is_one_pause_from_the_first_recorded_try():
    """#1612: a pause is judged from the first try the engine recorded, never from the run's start. A runner that takes
    3 s to reach that try is not handed those 3 s back as room to wait: the window one pause plus a fixed slack wide is
    the same for an idle, a loaded and a heavily loaded runner, so a pause that waited out the retry loop stays red."""
    pause = 2.0
    for reached in (0.1, 1.6, 3.1):                              # the first try, as this runner reached it after the start
        first, last = pause_window([{"ts": 1000.0 + reached, "review": {"elapsed": 0.1}}], pause)
        assert first == pytest.approx(1000.0 + reached - 0.1)    # the try's own start, read from the engine's record
        assert last - first == pytest.approx(pause + PAUSE_SLACK_S)
    assert PAUSE_SLACK_S < pause, "the slack is a moment a cancel needs, never a second retry pause"


@pytest.mark.anyio
@pytest.mark.heavy
@pytest.mark.parametrize("start_delay", [0.0, 1.5], ids=["idle", "loaded"])
async def test_a_pause_cancels_a_review_still_retrying(tmp_path, monkeypatch, start_delay):
    """A pause ends the entry's reviews with it: a review that got no verdict retries no more, so the stop is not held up
    by the gate's deadline and no round is recorded after the pause. The claim is measured from the first try the engine
    recorded, one pause plus a fixed slack after it, so the runner's pace before that try is no part of the budget
    (#1612): a shared runner took 3.06 s to reach its first try and the pause was read as the cause."""
    from squad.runners import dispatch as D
    monkeypatch.setattr(D, "REVIEW_RETRY_PAUSE_S", 2.0)
    p = tmp_path / "review.sh"
    p.write_text(f'#!/bin/sh\necho retry >> "{tmp_path}/review.log"\nsleep 0.05\nexit 5\n')
    p.chmod(0o755)
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, two_children(), tweak=gated(str(p), deadline=12), target=repo)
    Control(tmp_path).append("pause", nodes=[B])
    await anyio.sleep(start_delay)   # the runner's own pace, injected: the window below starts after it
    v = await eng.run()
    end = time.time()
    assert (v.verdict, v.exit_code) == ("stopped", 30)
    tries = [e for e in ev(tmp_path, "review_try") if e["node"] == A]
    assert tries, "the review was never tried: the pause had nothing to cancel"
    first, last = pause_window(tries, D.REVIEW_RETRY_PAUSE_S)
    assert end < last, (
        f"the pause ended the run {end - first:.2f}s after the first try, past one {D.REVIEW_RETRY_PAUSE_S:g}s retry pause")
    assert [e for e in ev(tmp_path, "review_round") if e["node"] == A] == []
    assert ev(tmp_path, "land") == []
    assert (tmp_path / "review.log").read_text().count("retry") <= 2   # a cancelled reading is not tried again


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_resume_reviews_the_same_head_against_the_same_base(tmp_path):
    """The `{base}` of a review is the point the child's branch was cut from, read the same way by the run and its resume.
    Read as the target's head at review time, it moves with the timing of an earlier sibling's merge."""
    repo = sibling_repo(tmp_path / "t")
    stub = gated(base_stub(tmp_path, slow=B))
    eng = make(tmp_path, two_children(), tweak=stub, target=repo)
    eng.dispatch.prune = lambda: None
    assert (await eng.run()).verdict == "completed"
    events = read(tmp_path / "record.jsonl")
    # The cut is at the second child's review and drops it and all after it. The second child's review waits, so the first
    # child's review ends first and its merge lands before that review: the record holds the first landing and no round for
    # the second child. The other order is a race the engine never promises, since the two reviews run beside each other.
    cut = next(i for i, e in enumerate(events) if e["event"] == "gate_result" and e.get("node") == B)
    (tmp_path / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events[:cut]))
    first = [ln.split() for ln in (tmp_path / "review.log").read_text().splitlines() if ln.split()[0] == B]
    lands = [e for e in events[:cut] if e["event"] == "land"]
    git.run(["reset", "--hard", lands[-1]["effect"]["head"] if lands else first[0][2]], repo)   # the target as the cut left it
    (tmp_path / "review.log").unlink()

    eng2 = make(tmp_path, {}, tweak=stub, target=repo)
    assert (await eng2.resume()).verdict == "completed"
    again = [ln.split() for ln in (tmp_path / "review.log").read_text().splitlines()]
    assert len(first) == 1 and len(again) == 1, (first, again)   # the pass is replayed, its round read once in each pass
    assert first[0][1:] == again[0][1:], f"the same round was read against two repositories: {first + again}"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_fix_round_never_rewrites_a_worktree_a_sibling_is_reading(tmp_path):
    """A reader that runs after a writer reads that writer's worktree (ADR-0113 rule 11). The writer's fix round runs in
    `land`, after the entry's children ended, so a reader never sees half-written code from beside its own session."""
    repo = sibling_repo(tmp_path / "t")
    seen = []

    def read_a(r):
        from pathlib import Path
        seen.append((Path(r.workspace) / "a.txt").read_text())

    reader = {"output": {"diff": {"files": []}}, "spend": 0.1, "turns": 1, "delay": 0.6, "effect": read_a}
    eng = make(tmp_path, {"router": routed(req(), req("codebase-design", tools=["Read"], after=[0])),
                          A: [writer("a.txt", "impl\n"), writer("a.txt", "fixed\n")], B: reader},
               tweak=gated(code_stub(tmp_path, [4, 0], sleep=0.2)), target=repo)
    assert (await eng.run()).verdict == "completed"
    assert seen == ["impl\n"], "the reader ran on a worktree the fix round had already rewritten"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_broken_review_round_returns_the_grant_it_held(tmp_path, monkeypatch):
    """A review wrapper that raises leaves no grant behind: the reading is read again in `land`, one round is recorded,
    and the run's budget holds nothing the broken attempt reserved (ADR-0108)."""
    from squad import gates
    calls, real = [], gates.review_command

    def broken(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the review wrapper broke")     # the early reading breaks: `land` reads the head again
        return real(*a, **kw)

    monkeypatch.setattr(gates, "review_command", broken)
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req()), A: writer("a.txt")}, tweak=gated(timed_stub(tmp_path, sleep=0)), target=repo)
    assert (await eng.run()).verdict == "completed"
    assert len(calls) == 2 and [(e["node"], e["round"]) for e in ev(tmp_path, "review_round")] == [(A, 1)]
    assert ev(tmp_path, "diagnostic") and "review wrapper broke" in ev(tmp_path, "diagnostic")[0]["payload"]["error"]
    assert eng.budget.reserved == pytest.approx(0.0) and eng.budget.unmeasured == pytest.approx(0.0)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_review_round_that_raises_in_the_land_returns_its_grant(tmp_path, monkeypatch):
    """A round the land reads itself takes the next entry's grant first. A command that raises leaves neither the grant
    reserved nor the failure unnamed (ADR-0108)."""
    from squad import gates
    calls, real = [], gates.review_command

    def broken(*a, **kw):
        calls.append(1)
        if len(calls) <= 2:
            raise OSError("the review wrapper broke")
        return real(*a, **kw)

    monkeypatch.setattr(gates, "review_command", broken)
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req()), A: writer("a.txt")}, tweak=gated(timed_stub(tmp_path, sleep=0)), target=repo)
    with pytest.raises(OSError):
        await eng.run()
    assert len(calls) == 2 and eng.budget.reserved == pytest.approx(0.0)
    assert any("review wrapper broke" in e["payload"]["error"] for e in ev(tmp_path, "diagnostic"))


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_siblings_first_failure_never_cancels_a_passed_childs_review(tmp_path):
    """`join: first_failure` cancels the children still running; a child that already passed keeps the review it started,
    so the review runs once for its head and the child still lands."""
    repo = sibling_repo(tmp_path / "t")
    bad = {"output": None, "spend": 0.1, "turns": 1, "stop_reason": "spend_cap"}

    def first_failure(d):
        gated(timed_stub(tmp_path, sleep=0.5))(d)
        node(d, "route")["join"] = "first_failure"

    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), A: writer("a.txt"), B: bad},
               tweak=first_failure, target=repo)
    assert (await eng.run()).verdict == "failed"
    assert list(spans(tmp_path)) == [A]                    # the review ran once, for the child that passed
    [land] = ev(tmp_path, "land")
    assert land["node"] == A and land["outcome"] == "pass"
    assert (repo / "a.txt").exists()


def record_order(tmp_path):
    """The (event, node) pairs of the run's record, in the order they were written."""
    return [(e["event"], e.get("node")) for e in read(tmp_path / "record.jsonl")]


def at(order, event, node):
    """The index of the first (event, node) pair in `order`."""
    return order.index((event, node))


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_passed_child_is_reviewed_and_landed_before_a_slow_sibling_ends(tmp_path):
    """#1520: a child that passes is reviewed and merged as soon as its reading ends, not after its slow sibling settles."""
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), A: writer("a.txt"),
                          B: {**writer("b.txt"), "delay": 1.5}},
               tweak=gated(code_stub(tmp_path, [0])), target=repo)
    assert (await eng.run()).verdict == "completed"
    order = record_order(tmp_path)
    assert at(order, "review_round", A) < at(order, "outcome", B)
    assert at(order, "land", A) < at(order, "outcome", B)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_slow_child_passed_last_is_reviewed_and_landed_before_a_fast_sibling_ends_in_reverse_order(tmp_path):
    """#1520, the reverse case: the slow child is A, the fast child is B; the review order follows completion, not request order."""
    repo = sibling_repo(tmp_path / "t")
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), A: {**writer("a.txt"), "delay": 1.5},
                          B: writer("b.txt")},
               tweak=gated(code_stub(tmp_path, [0])), target=repo)
    assert (await eng.run()).verdict == "completed"
    order = record_order(tmp_path)
    assert at(order, "review_round", B) < at(order, "outcome", A)
    assert at(order, "land", B) < at(order, "outcome", A)
