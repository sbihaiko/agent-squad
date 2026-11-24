#!/usr/bin/env python3
"""A child that finished with outcome pass moves its issue to Batching (called by watch.sh every loop).
A child that finished with outcome unproven is a finished child too (ADR-0164): its work is done and the proof could not
finish, so it enters its wave. Its entry carries a proof obligation only when a gate wrote one (wave.sh names it on the entry
line); the child's own `unproven` word settles nothing. A child with outcome fail, or a child whose own landing failed, never
enters: a measured red holds delivery.
A child of an issue whose body holds an "after" image owes a render (#2833, render_owed.py): while its worktree holds no PNG
under .scratch/renders/, it does not enter. The pass writes the loop's CHANGES review in its review file, so the fix round
below starts, and queues one issue comment per tip that names the missing render; the card keeps its column. The check reads
that evidence, never the child's status word, so a child that answers `pass` with no render is held too.
A child of a run that cut a wave enters it first (#375, ADR-0145 rules 9 and 12): `wave.sh enter <run> <issue>` runs in the
child's own worktree, is the only route into the wave branch, and its entry is what moves the card; a child that did not
enter has no card to move, its issue went back to the queue. A run that cut no wave keeps the direct move, unless it was dispatched
for a child: then its cut failed, and the child is named on a salvage line instead of moved.
The child's own diff is reviewed before that entry, here, once per head (childreview.sh, #448): the entry waits for the review
of the child's own commits against the wave branch (#376), and a child's branch is pushed nowhere (#375), so the review lane
that lists pushed work branches never saw it and every child was held out of its wave for good.
The child's issue is read from its own spawn prompt, first match wins: "GitHub issue #N", "issue #N",
"GitHub issues N", "GitHub issue N", "Refs #N", then a "gui/N" or "wt-as-N" branch/worktree name.
A pass whose prompt names no issue is logged once (CHILDCC UNMAPPED <run> <child>) instead of being skipped silently.
The runs the pass works are its arguments, else every live run: watch.sh's completion of a finished run hands it that one run, so the children that run finished are in its wave before that same step lands the wave (#375 review).
Live runs move at once; a run that already has its verdict (tiny and fork-only runs go pass -> verdict in under 1 s, so they are never seen live) is backfilled once,
but only when the card is still in Todo/In Progress/Blocked, never past Batching; a run with a wave not yet landed is not backfilled by card but by its wave marker, so a child that has not entered still enters. The once-marker is written when the child entered or needs no entry, never after a refused entry: the next pass tries it again. Once per child (marker auto/cc-<run>-<child>); only an open issue moves; queues a comment with the child's first reason.
Closed comes later (the wave lands in dev), so Batching never overrides a later column: an issue already
past it is left alone because the marker is written the first time the child is seen passing.
One pass works at most SG_CHILDCC_BUDGET pending children (1 by default, #590): a review and the gates take 2 to 15 min a
child, and a pass that worked every child held the childcc lock, and every landing behind it, for 14 to 25 min. A child is
work when its entry reaches the review and the gates; a child its requeue holds on an unchanged head, or that needs no entry,
costs nothing. The cursor (auto/childcc-cursor) names the last child worked, and the next pass starts after it, so a child
held on every pass does not take every pass. A review takes one review slot of the machine-wide budget (caps.reviews, squad/caps.py), in try mode: a full budget skips the review for this pass with one log line and leaves the child for a later pass, so this pass never waits for a slot while it holds the childcc lock (SG_LAND_WAIT).
A pass that leaves a child that had no turn since the last complete pass
(auto/childcc-round) exits 3, never 0: the landing waits for that child, and a 0 would land the wave without it. Every child
the pass leaves is named with its cause, one line per child each pass (CHILDCC DEFERRED <run> <child> #<n>: <cause>), so a
deferral is diagnosable from the log without a hand run (#931). A child
its entry held (WAVE HOLD: neither entered nor requeued) is no ruling either, so it stays left until its wave reaches its
age cap; SG_HOLD_TRIES review holds on one head (3 by default) requeue it with that reason, so the wave can land (#620).
A child the pass tried and could not enter waits on a deadline (ADR-0134): SG_CHILDCC_DEFER_MAX passes (6 by default) on an
unchanged (tip, cause) requeue the issue with that cause, and record the head, so the wave lands and the next pass costs
nothing (#931). The counter is auto/dwait-<run>-<child>, a name the janitor never removes; the escalation marker stays
auto/ccdefer-<run>-<child>, which the janitor drops once the run has its verdict. A machine-wide hold -- the gate lock, the
gate slots, an inconclusive gate, a full review budget -- clears by itself, so it never counts and never requeues. A child a
live run never approved waits on that run, at no deadline (ADR-0151 rule 5); once the run has its final verdict no actor can
review that head again, so the child keeps no path to an approval and the deadline owns it like any other. A child the pass
never reached (its budget, its cursor) has no deadline, because the pass did not try it (#590).
A gate-lock or gate-slot hold with no review run in this pass is no work: it spends no budget, the cursor stops before the held child, and the pass ends its entries (exit 3), because the next child meets the same holder (#1407).
A child whose own engine landing failed never enters: its work did not reach the run's target, so its issue goes back to
the queue once with the landing's reason, and no card moves (#592).
With the shipped vocabulary a child whose review starts, or that is deferred while it waits for review, moves its card from
Doing to Reviewing through board.sh, once per child (marker auto/crv-<run>-<child>): a card in any other column, past
Reviewing or sent back to Todo by a requeue, is never moved (#603, ADR-0143, ADR-0144).
A run reviews its own children (#601): the verdict of its review gate is in the child's `land` event. While such a run is live
its passed child is still its own (#696): it reviews it, fixes it or lands it, so the child is pending here only once its `land`
event is in the record or the run has its verdict. Such a run's dispatch node names a review gate (its graph.json), or its
record holds a review event for the child; a review that answered "no review here" is no review, so that child is read here
as before (ADR-0145 rule 9). A run that reviewed a child owns it (#701, ADR-0151 rule 5): childcc never reviews or fixes a
child its run reviewed, and starts no fix run for it. The run's approval enters such a child with no second review here, and
it binds to the head the run's review read (ADR-0151 rule 4): a child at another head, or one whose run approved no head,
is neither reviewed nor fixed here and its delivery holds, for the wave's age cap to free it. That hold is read before the
failed land's requeue: a land that failed on the review itself (review_held, review_deadline) is missing evidence and holds,
and only a standing approval at the reviewed head lets another landing failure requeue the child. A child its run held, with its
`requeue` fact in the issue's ledger, is neither entered, reviewed nor requeued again; a child of a run with no review in its
record is reviewed here as before.
A dispatch child with fix entries (#570) has one passing `outcome` per entry: the pass works it once, by its last entry (#611).
A review here that asks for CHANGES starts one fix run in the child's kept worktree (childfix.sh, #636), and the issue does not
go back to the queue: the child's branch is pushed nowhere, so the pushed-branch fix lane never reaches it. The marker
auto/ccfix-<run>-<child> names the fix run's folder; while that run has no verdict the child is held and its wave waits. Each fix run's folder is also one line of auto/wave-pause-<run>:
the wave's age cap pauses while it runs (#637, `wave.sh expired`). A fix
run is no delivery of its own (marker auto/ccfixrun-<fix run>): the pass never works its children. The new head is reviewed
again, and an APPROVE enters the wave. Findings that do not change after a fix run requeue the issue once, with them in the
reason (ADR-0134, revformat.py's fingerprint); changed findings start the next fix run."""
import json, os, pathlib, re, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
import child_issues   # the issues a child's spawn text names (#555)
import issue_ledger   # the issue's loop facts: the head a run requeued its child on is a fact there, never a marker (ADR-0149)
from issue_state import board_state
from board_columns import claimed_names, REVIEWING, batching_column   # the claimed card names: "Doing" on this board, "In Progress" on the default (#1342); the review column's canonical name (#2155)
st = pathlib.Path(os.environ["SG_STATE"]); A = st / "auto"; A.mkdir(parents=True, exist_ok=True)
repo, owner, proj = os.environ["SG_REPO"], os.environ["SG_OWNER"], os.environ["SG_PROJECT"]
CQ = os.environ["SG_REPO"].replace("/", "__")   # a queued comment is named <owner>__<repo>#<n>-<tag>.md: cqflush.sh posts it to the repository it names
board = str(pathlib.Path(__file__).resolve().parent / "board.sh")
wave = str(pathlib.Path(__file__).resolve().parent / "wave.sh")   # the wave's verbs, beside this pass (#375)
childreview = str(pathlib.Path(__file__).resolve().parent / "childreview.sh")   # the review of the child's own diff, beside this pass (#448)
childfix = str(pathlib.Path(__file__).resolve().parent / "childfix.sh")   # the fix run in the child's kept worktree, beside this pass (#636)
state = board_state()   # the dispatch pass asks the seam what state an issue carries, never the carrier (#236)
BUDGET = int(os.environ.get("SG_CHILDCC_BUDGET") or 1)   # the children one pass works (#590)
SHIPPED = os.environ.get("SG_BOARD_VOCAB") == "shipped"   # the old vocabulary has no Reviewing column (#603)
HOLD_TRIES = int(os.environ.get("SG_HOLD_TRIES") or 3)   # the review holds on one head before the child is requeued (#620)
DEFER_MAX = int(os.environ.get("SG_CHILDCC_DEFER_MAX") or 6)   # the passes one attempted child waits on one unchanged (tip, cause) before its issue goes back to the queue (#931, ADR-0134)
HOLDS = set()   # the children this pass held out of their wave, neither entered nor requeued: the landing waits for them (#620)
CAUSE = {}   # why this pass left each child out of its turn: the one diagnosis a deferral carries (#931)
REVIEWED_NOW = set()   # the children whose review this pass ran: a review is work, so a gate hold after it still spends the budget (#1407)
LOCK_HOLD = []   # set when an entry stopped at the gate lock or the gate slots: the next child meets the same holder, so the pass ends its entries (#1407)
ATTEMPTED = set()   # the children this pass tried to enter and could not: only these have a deferral deadline, never one the budget left (#931)
FIX_WAIT = set()   # the children a live fix run still works: its verdict is their bound, so they never count toward the deadline (#931)
INCOMPLETE = 3   # the exit of a pass that left a child for a later pass: not a failure (1, a traceback), and not done (0)
CURSOR, ROUND = A / "childcc-cursor", A / "childcc-round"
NOW = A / "childcc-now.json"   # what the hub shows of this pass (#598): its pid and start, and each child it holds with its step and since when it waits
T0 = time.time()
PENDING, HELD = [], {"key": None, "step": None, "rk": None}   # HELD: the child this pass works now, and the step it is at (review, enter)
try: SINCE = {f"{c['run']} {c['child']}": c["since"] for c in json.loads(NOW.read_text()).get("children", [])}   # a child keeps its wait from the pass before
except Exception: SINCE = {}
def publish(live=True):
    """Write the hub's reading of the pass, atomically. Every pending child with no marker is held: the one worked now at its
    step, the rest deferred. `pid` is None once the pass ends, so the hub reads a child that waits and no pass that runs."""
    rows = []
    for key, r, child, _, n, *_ in PENDING:
        name = pathlib.Path(r).name
        if (A / f"cc-{name}-{child}").exists(): continue
        now = key == HELD["key"]
        rows.append({"run": name, "child": child, "issue": n, "step": HELD["step"] if now else "deferred", "rk": HELD["rk"] if now else None,
                     "since": SINCE.setdefault(f"{name} {child}", time.time())})
    tmp = NOW.with_name(f"{NOW.name}.{os.getpid()}.tmp"); tmp.write_text(json.dumps({"pid": os.getpid() if live else None, "t0": T0, "children": rows})); os.replace(tmp, NOW)
def loop_defect():
    """The open issue that marks a defect in the loop's own machinery (#596, ADR-0146), or None. One bounded read of the
    open issues through the seam for the whole pass; a refusal or a failure answers None, so a read the pass could not
    prove never stops it and keeps the knowledge the pass already had."""
    try:
        import ghread   # the one GitHub door (ADR-0277), beside this script; a bare `gh api` would read a refusal as a list
        os.environ.setdefault("SG_GH_PASS", "childcc")
        r = ghread.read(f"repos/{repo}/issues?state=open")
    except Exception:
        return None
    if not r.is_answer or not isinstance(r.answer, list): return None
    return ghread.loop_stop(r.answer)   # one list for every reader: dispatch, landing and infrastructure (#641, ADR-0146)
STOP = loop_defect()   # the loop cannot deliver its own fix: no review, fix run or entry starts while one is open (#596, ADR-0146)
def child_worktree(rd, child, d):   # the tree a child worked in, by the engine's own rule (squad/runners/children.py, Children.workspace)
    ws = d.get("workspace") if isinstance(d, dict) else None
    logical = "target" if not ws or ws == "." else str(ws).replace("/", "_")
    return rd / "ws" / f"{logical}-{child}"
def head_of(wt):   # the sha the child's worktree stands on, "" when it has none
    return subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
def commits_ahead(wt):   # the commits the child's HEAD carries past origin/<base>; None when git cannot say (the push then decides)
    try: r = subprocess.run(["git", "-C", str(wt), "rev-list", "--count", f"origin/{os.environ.get('SG_BASE') or 'dev'}..HEAD"], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired: return None
    got = r.stdout.strip()
    return int(got) if r.returncode == 0 and got.isdigit() else None
def salvage_push(wt, branch):   # the child's HEAD pushed to a work branch on its own origin: (True, "") or (False, why); the branch is never forced
    try: r = subprocess.run(["git", "-C", str(wt), "push", "origin", f"HEAD:refs/heads/{branch}"], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired: return False, "the push timed out"
    return r.returncode == 0, (r.stderr or r.stdout).strip()[:200]
def requeue_head(run, n):   # the head this run requeued the issue's child on, "" when it did not (the old auto/requeued- marker, ADR-0149); "held" when the ledger cannot answer, and then the child is held out
    try: return issue_ledger.requeued(f"{repo}#{n}", run)
    except (OSError, ValueError, KeyError): return "held"
def record_requeue(run, n, head):   # the same fact, written through the ledger's one transition function
    try: issue_ledger.requeue(f"{repo}#{n}", run, head)
    except (OSError, ValueError, KeyError): pass
def claimed(claim, head):   # a review claim [head, pid] holds while its pass is alive; a bare head, or a dead pass, holds nothing (#502)
    if len(claim) != 2 or claim[0] != head or not claim[1].isdigit(): return False
    try: os.kill(int(claim[1]), 0)
    except ProcessLookupError: return False
    except PermissionError: pass
    return True
def code_review(name, child, n, why):
    """Move the card of a child its review holds from Doing to Reviewing, once per child (#603). Only a Doing card moves:
    a later column is never moved back, and a Todo card is a requeue's. A carrier that does not answer leaves no marker,
    so the next pass asks again; a refused write is named, never dropped."""
    mk = A / f"crv-{name}-{child}"
    if not SHIPPED or mk.exists(): return
    have = card_status(n)
    if not have: return
    mk.touch()
    if have not in ("Doing", "In Progress"): return
    e = subprocess.run([board, owner, proj, REVIEWING, str(n)], capture_output=True, text=True)
    if e.returncode: print(f"CHILDCC-BOARD #{n} {REVIEWING} refused: {(e.stdout + e.stderr).strip()[:300]}", flush=True)
    else: print(f"CODE REVIEW #{n} ({name} {child}): {why}", flush=True)
def caps_cmd(*argv):   # `python3 -m squad.caps <argv>`: the machine-wide budget's one command (SG_SQUAD names the repository, else the one this script lives in)
    root = os.environ.get("SG_SQUAD") or str(pathlib.Path(__file__).resolve().parents[3])
    return subprocess.run([sys.executable, "-m", "squad.caps", *argv], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")})
def review_slot(holder):   # one review slot, in try mode: (True, "") when taken, (False, the line naming the full ceiling and its holder) when the budget is full
    e = caps_cmd("take", "reviews", holder, "--try", "--pid", str(os.getpid()))
    return e.returncode == 0, e.stderr.strip() or f"caps.reviews take exited {e.returncode}"
def reviewed(rd, child, n, wt, rv):
    """The review of the child's own diff, read in the child's worktree against its run's wave branch, before the entry: the
    branch the review lane lists is a pushed `fix/`/`gui/` one, and a child's branch reaches no remote at all (#375), so no
    pass wrote the review the entry waits for and every child of every run was held for good (#448). One review per head
    (marker auto/rvd-<run>-<child> holds the sha it read): a pass that starts while this one reviews does not repeat it, and a
    head that gets a new commit is read again. A review that produced no verdict is not a review: the claim goes, so a chain
    that was down is tried again on the next pass. The claim also names the pass that holds it (#502): a pass killed
    mid-review (the loop stopped) leaves its claim and no reviewed head, and a claim whose pass is gone is no claim."""
    head = head_of(wt)
    if rv.exists() and claimed(rv.read_text().split(), head): return True
    md, sha = review_paths(wt)
    if md.is_file() and md.stat().st_size and sha.is_file() and sha.read_text().strip() == head:   # a review of this head is already there: the entry reads it, and nothing is spent twice
        rv.write_text(head + "\n"); return True
    holder = f"childcc-{rd.name}-{child}-{os.getpid()}"
    took, why = review_slot(holder)
    if not took: print(f"CHILD REVIEW SKIPPED {rd.name} {child}: {why}", flush=True); return False
    rv.write_text(f"{head} {os.getpid()}\n")
    code_review(rd.name, child, n, "its review starts")
    HELD.update(step="review", rk=review_paths(wt)[0].name[len("review-"):-len(".md")]); publish()   # the key names the file review.sh writes the model it tries to
    try: e = subprocess.run(["bash", childreview, rd.name, str(n)], cwd=str(wt), capture_output=True, text=True)
    finally: caps_cmd("release", "reviews", holder); HELD.update(step="enter", rk=None); publish()
    out = (e.stdout + e.stderr).strip()
    REVIEWED_NOW.add(f"{rd.name} {child}")
    if out: print(f"CHILD REVIEW {rd.name} {child}: {out}", flush=True)
    if e.returncode == 6:   # #1406: no change past its base, so nothing to review: the issue is requeued at once with the child's reason, never three hold passes
        k = review_paths(wt)[0].name[len("review-"):-len(".md")]
        try: why = (pathlib.Path(st) / f"reason-{k}").read_text().strip().splitlines()[0]
        except (OSError, IndexError): why = "the child made no change"
        subprocess.run(["bash", wave, "requeue", str(n), f"the child made no change past its base in run {rd.name}: {why}"], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": rd.name})
        record_requeue(rd.name, n, head)   # the head the child was requeued on, so the pass waits for a new one (ADR-0149)
        return "nochange"
    if e.returncode != 0: rv.unlink(missing_ok=True)
    return True
def review_paths(wt):   # the verdict and the sha the entry reads, named by the child's own branch (review.sh's rule)
    br = subprocess.run(["git", "-C", str(wt), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    k = f"{st}/review-{br.replace('/', '_')}"   # the suffix is appended, never set: a child's branch carries dots of its own
    return pathlib.Path(k + ".md"), pathlib.Path(k + ".sha")
def first_finding(wt):   # the first finding of the review the entry read, for the issue that goes back to the queue
    md, _ = review_paths(wt)
    try: lines = [l.strip() for l in md.read_text().splitlines()[1:] if l.strip()]
    except OSError: return "no finding was written"
    return (lines[0] if lines else "no finding was written")[:300]
def fix_live(fx):   # the fix run the marker names has no verdict yet, and a record that cannot be read is no verdict either (#667): the fix run lived inside the child's worktree, and a landing that cleaned it took the record too, so the child stays held and the wave's age cap frees it
    try: return '"event": "verdict"' not in (pathlib.Path(fx.read_text().strip()) / "record.jsonl").read_text()
    except OSError: return True
def fix_running(fx):   # the fix run the marker names is still at work: its record is readable and holds no verdict yet, so its own end is the bound (#931). An unreadable record is no live run: the deadline still bounds it (#667)
    try: return '"event": "verdict"' not in (pathlib.Path(fx.read_text().strip()) / "record.jsonl").read_text()
    except OSError: return False
def last_turn(fx):   # the child's last turn of the fix run `fx` names: why it stopped, so a reader of the requeue reason sees it (ADR-0151, #735)
    try: lines = (pathlib.Path(fx.read_text().strip()) / "record.jsonl").read_text().splitlines()
    except OSError: return ""
    for line in reversed(lines):
        try: ev = json.loads(line)
        except ValueError: continue
        diag = ev.get("diagnostic") if ev.get("event") == "outcome" else None
        last = diag.get("ended", {}).get("last") if isinstance(diag, dict) and isinstance(diag.get("ended"), dict) else None
        if last: return " ".join(str(last).split())[:300]
    return ""
def fix_round(rd, child, n, wt, fx, out):
    """A review here asked for CHANGES (#636). The first time, or when the findings changed after a fix run, one fix run
    starts in the child's worktree and the child is held: (False, line, True). Findings that a fix run left as they were
    requeue the issue once, with them in the reason (ADR-0134). A fix run that did not start holds the child, and the next
    pass tries again on its review."""
    import revformat   # the fingerprint of a review's findings and the stall count of its fix rounds (ADR-0134), read only here
    md, _ = review_paths(wt); key = f"{rd.name}-{child}"
    try: fp = revformat.findings_fingerprint(md.read_bytes())
    except OSError: fp = ""
    same = revformat.stall_rounds(str(A), "ccfix", key, fp)
    if fx.exists() and same:
        stopped = last_turn(fx)
        why = f"a fix run left the same review findings: {first_finding(wt)}" + (f" (its last turn: {stopped})" if stopped else "")
        subprocess.run(["bash", wave, "requeue", str(n), why], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": rd.name})   # the run is the key of the count (#609)
        record_requeue(rd.name, n, head_of(wt))   # the head the child was requeued on, so the pass waits for a new one (ADR-0149)
        return False, out + f"\nCHILD FIX REQUEUE #{n}: {why}", True
    f = subprocess.run(["bash", childfix, rd.name, str(n)], cwd=str(wt), capture_output=True, text=True)
    lines = f.stdout.strip().splitlines()
    HOLDS.add(f"{rd.name} {child}")
    if f.returncode or not lines:
        return False, out + f"\nCHILD FIX REFUSED #{n} ({rd.name} {child}): {(f.stdout + f.stderr).strip()[-300:] or f'exit {f.returncode}'}", True
    fx.write_text(lines[-1] + "\n"); (A / f"ccfixrun-{pathlib.Path(lines[-1]).name}").touch()
    with (A / f"wave-pause-{rd.name}").open("a") as p: p.write(lines[-1] + "\n")   # the wave's age cap pauses while the fix run runs (#637, wave.sh expired)
    revformat.record_stall(str(A), "ccfix", key, fp, same)
    return False, out + f"\nCHILD FIX RUN #{n} ({rd.name} {child}): {first_finding(wt)} -> {pathlib.Path(lines[-1]).name}", True
def run_hold(rd, child, n, d, ok, rnd):
    """Why a child its run reviewed waits on that run instead of this pass (ADR-0151 rules 4 and 5), "" when its approval
    stands: a run that reviews its own children owns them, so childcc never reviews or fixes one, and missing evidence holds
    delivery. The approval binds to the head the run's review read (rule 4), so a new commit after it makes it stale and
    nothing enters the wave."""
    if not ok: return "its run approved no head for it"
    head = str((rnd or {}).get("head") or "")
    if not head: return "its run recorded no reviewed head, so its approval binds to nothing"
    now = head_of(child_worktree(rd, child, d))
    if now != head: return f"its run approved {head[:9]}, and the child stands at {now[:9] or 'no head'}: a new commit needs a new review"
    return ""
def machine_hold(cause):   # a machine-wide hold clears by itself, so it never counts toward a deferral deadline and never requeues (#931, #567)
    # A child kept on a rebase round waits for its own commits to be rebased, not for the machine: no deadline requeues it,
    # and no failed dispatch is counted against it (#1344).
    return any(s in cause for s in ("the gate lock is held", "the gate slots (caps.gates) are full",
                                    "the local gates were inconclusive", "no review slot this pass",
                                    "for one rebase round, not requeued", "its issue body could not be read"))
def moved_tip(rd, child, d, rnd):   # the tip a review read, when the child stands past it (#808); False when no head was read or the tip is not there
    head = str((rnd or {}).get("head") or "")
    return bool(head) and head_of(child_worktree(rd, child, d)) not in ("", head)
def logged(key, line):   # a line printed once per change of its text: a pass that repeats the same line is silent (#808)
    f = A / "childcc-logged.json"
    try: last = json.loads(f.read_text())
    except (OSError, ValueError): last = {}
    if last.get(key) == line: return
    last[key] = line; f.write_text(json.dumps(last)); print(line, flush=True)
def render_hold(rd, child, n, wt, fx):
    """The render check before the entry (#2833): None when the child owes no render, else enter()'s answer. A child of an
    issue whose body holds an "after" image, with no PNG under .scratch/renders/ in its worktree, gets the loop's CHANGES
    review at its head and one fix round. The review file it replaces is kept beside it and put back once the render is
    there, so a run's own approval survives the round; a render review left with no kept file is removed, so the next
    review reads the head afresh. A body that cannot be read holds the child for this pass, as a machine hold."""
    import render_owed   # read only here: a fixture that copies this script with no sibling never reaches a wave entry
    os.environ.setdefault("SG_GH_PASS", "childcc")
    try:
        import ghread   # the one GitHub door (ADR-0277), beside this script
        r = ghread.read(f"repos/{repo}/issues/{n}")
    except Exception as x: r = None; why = f"{type(x).__name__}: {x}"
    if r is None or not r.is_answer or not isinstance(r.answer, dict):
        why = why if r is None else (r.message or r.detail or "no issue in the answer")
        HOLDS.add(f"{rd.name} {child}")
        return False, f"CHILD RENDER HOLD #{n} ({rd.name} {child}): its issue body could not be read ({str(why).strip()[:200]})", False
    body = r.answer.get("body") or ""
    md, sha = review_paths(wt); kmd, ksha = md.with_name(md.name + ".prerender"), sha.with_name(sha.name + ".prerender")
    try: mine = md.is_file() and render_owed.TAG in md.read_text(errors="replace")
    except OSError: mine = False
    if not render_owed.render_owed(body, wt):
        if mine:
            if kmd.exists(): os.replace(kmd, md); ksha.exists() and os.replace(ksha, sha)
            else: md.unlink(missing_ok=True); sha.unlink(missing_ok=True)
        else: kmd.unlink(missing_ok=True); ksha.unlink(missing_ok=True)
        (A / f"render-{rd.name}-{child}").unlink(missing_ok=True)   # the render is there: the card may move again
        return None
    head = head_of(wt)
    if not mine and md.is_file():
        os.replace(md, kmd)
        if sha.is_file(): os.replace(sha, ksha)
    md.write_text(render_owed.render_review()); sha.write_text(head + "\n")
    tk = A / f"render-{rd.name}-{child}"   # the tip the last render comment named; while it exists the card keeps its column
    try: told = tk.read_text().strip()
    except OSError: told = ""
    if told != head:   # one comment per tip
        (st / "cq").mkdir(parents=True, exist_ok=True)
        (st / "cq" / f"{CQ}#{n}-render-{rd.name}-{child}-{head[:12]}.md").write_text(
            f"Child {child} of squad run {rd.name} stands at {head[:9]} with no render: the issue carries an after image, and the"
            f" worktree holds no PNG under `{render_owed.RENDERS}/`. It does not enter its wave. A fix round renders each changed"
            f" screen through ego-browser against a throwaway hub on its own port, as `contrib/squad-goal/scripts/test-hub-e2e.py`"
            f" does, and saves the renders there.\n")
        tk.write_text(head + "\n")
    return fix_round(rd, child, n, wt, fx, f"CHILD RENDER OWED #{n} ({rd.name} {child}): the issue carries an after image and the worktree holds {render_owed.TAG}")
def enter(rd, child, n, d, approved=False, own=False):
    """The wave's entry of a finished child, in the tree the engine made for it. Return (entered, why, worked): (None, "") when the
    run cut no wave and the direct move stands, (True, the entry line) when the child is now in the wave branch, and
    (False, why) when it is in none -- a child that did not enter has no delivery to claim. `worked` is True when the
    review and the gates ran, the work one pass is budgeted for (#590). A child a refused entry requeued
    is not tried again on the same HEAD (the issue's `requeue` fact in its ledger, ADR-0149): the gates and the requeue wait for a new commit.
    The child's own diff is reviewed here, once per head, before the entry asks for its verdict. A review that asked for
    CHANGES is not a child held where it stands: nothing behind that hold could fix it (the fix lane reads pushed work
    branches, and a child's branch is pushed nowhere), so its issue goes back to the queue -- live run or not, its next head
    is read again. A child its run reviewed is never reviewed or fixed here (`own`, ADR-0151 rule 5): its run's approval is
    the only evidence, and it enters on that alone, bound to the head the run read (rule 4)."""
    if not (A / f"waved-{rd.name}").exists() and not (A / f"wave-{rd.name}").exists():   # `waved-` is the durable record: a landing removes `wave-`, never this
        if child_request(rd): return False, f"WAVE-SALVAGE #{n} ({rd.name} {child}): the run cut no wave, so this child's commit is on its run's branch and in no wave; no card moves", False
        return None, "", False
    wt = child_worktree(rd, child, d)
    if not wt.is_dir(): return False, f"WAVE no entry for #{n}: the child's worktree {wt} is not there", False
    rq = requeue_head(rd.name, n)   # the sha a refused entry left the child on: the same sha would fail the same gates again, so the pass waits for a new commit
    if rq == "held": return False, f"CHILD HELD #{n}: its ledger could not be read", False
    if rq and rq == head_of(wt): return False, "", False
    fx = A / f"ccfix-{rd.name}-{child}"   # the fix run this pass started for the child (#636)
    if fx.exists() and fix_live(fx):   # its fix run still works the child's worktree: held, at no cost, and its wave waits
        HOLDS.add(f"{rd.name} {child}")
        if fix_running(fx): FIX_WAIT.add(f"{rd.name} {child}")   # a live fix run bounds its own child, so the deferral deadline never counts it (#931)
        return False, f"CHILD FIX WAIT #{n} ({rd.name} {child}): its fix run {pathlib.Path(fx.read_text().strip()).name} has no verdict yet", False
    if not approved and own: return False, f"CHILD RUN-OWNED #{n} ({rd.name} {child}): its run approved no head for it; delivery holds", False
    held = render_hold(rd, child, n, wt, fx)   # an after image with no render: no entry, one fix round (#2833); a run-owned child too, since its run never renders
    if held is not None: return held
    rvd = False if approved else reviewed(rd, child, n, wt, A / f"rvd-{rd.name}-{child}")
    if rvd == "nochange": return False, f"CHILD NO CHANGE #{n} ({rd.name} {child}): nothing to review past its base; the issue is requeued", True
    if not approved and not rvd: return False, "", None   # no review slot: the child waits, and the landing waits for it
    e = subprocess.run(["bash", wave, "enter", rd.name, str(n)], cwd=str(wt), capture_output=True, text=True)
    out = (e.stdout + e.stderr).strip()
    if "WAVE REQUEUE" in out or "WAVE STOP" in out: record_requeue(rd.name, n, head_of(wt))   # a hold (no review yet) is no requeue: it is tried again on the same sha; a stop (#594) is one that froze the issue
    if not own and e.returncode != 0 and re.search(r"did not approve it: .*CHANGES", out):
        return fix_round(rd, child, n, wt, fx, out)   # a fix run in its worktree, never a requeue at once (#636)
    elif e.returncode != 0 and "WAVE HOLD" in out:   # held, not requeued: the wave waits for it (#620)
        if re.search(r"kept on fix/\S+ for one rebase round, not requeued", out):
            # Its commits stand on a work branch of their own, which the landing passes deliver, so it leaves this wave
            # rather than holding it for its whole age cap (#1344).
            return False, out, True
        HOLDS.add(f"{rd.name} {child}")
        if machine_hold(out) and re.search(r"the gate lock is held|the gate slots \(caps.gates\) are full|waited for the gate lock", out):   # a machine hold: no work unless this pass ran the child's review (#1407)
            LOCK_HOLD.append(1)
            return False, out, f"{rd.name} {child}" in REVIEWED_NOW
        if re.search(r"WAVE HOLD #\d+: (no review|the review of)", out):   # a review hold, never a gate one: bounded per head, so a dead review chain never holds the wave for its whole age cap
            hk, h = A / f"holds-{rd.name}-{child}", head_of(wt)
            try: was, k = hk.read_text().split()
            except (OSError, ValueError): was, k = "", "0"
            k = int(k) + 1 if was == h else 1
            hk.write_text(f"{h} {k}\n")
            if k >= HOLD_TRIES:
                m = re.search(r"did not approve it: (.*)", out)   # a BLOCKED a model wrote is the issue's own; the chain's "every review model failed" is the loop's (#621)
                why = f"its review held it {k} times on head {h[:9]}: {m.group(1)}" if m else f"its review held it {k} times on head {h[:9]} with no verdict"
                subprocess.run(["bash", wave, "requeue", str(n), why], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": rd.name})
                record_requeue(rd.name, n, h); hk.unlink(); HOLDS.discard(f"{rd.name} {child}"); out += f"\nCHILD HOLD REQUEUE #{n}: {why}"
    return e.returncode == 0, out or f"WAVE no entry for #{n} (exit {e.returncode})", True
def card_status(n):   # the state the card carries, "" when it carries none or the carrier does not answer
    try: return state.read(n)
    except RuntimeError: return ""   # a refusal or a deadline miss is the carrier not answering, not a card that carries nothing
def issue_from(df):
    n = child_issues.first(df)   # the one reader the janitor's sibling passes share (#555)
    return int(n) if n else None
def cluster_from(df):
    """The first issue of the dispatcher's own cluster form (#954), which names several: `child_issues.first` reads the
    single-issue forms only, so a cluster child would otherwise read as unmapped and move nothing."""
    txt = (df if isinstance(df, str) else " ".join(str(df.get(k) or "") for k in ("task", "prompt"))) if df is not None else ""
    got = child_issues.cluster(txt)
    return int(got[0]) if got else None
def every_issue(d, n):
    """Every issue one child works (#954): a cluster dispatch packs several into one child, so its cards are the whole
    cluster, not only the first number the text names. Falls back to the child's one issue."""
    txt = (" ".join(str(d.get(k) or "") for k in ("task", "prompt")) if isinstance(d, dict) else str(d or ""))
    got = [int(x) for x in child_issues.cluster(txt)]
    return got or [n]
def child_request(rd):   # the run was dispatched for a child (launch.py's _issue_child_request), so it had a wave to cut
    try: txt = (rd / "request.txt").read_text(errors="replace")
    except OSError: return False
    return bool(re.search(r"^CHILD[^\n]*GitHub issue", txt, re.M) or re.search(r"^FORK-ONLY", txt, re.M))
def record_finished(rec):
    """True when the record's last of a start, a resume or a verdict is a verdict: a verdict is final only after the last
    start or resume, so a resumed run is live again until its next verdict (ADR-0167, and the same fold as janitor.py).
    A run that reads as finished can never review a child again, which is what the deferral deadline turns on."""
    finished = False
    try: lines = rec.open()
    except OSError: return False
    with lines:
        for line in lines:
            if '"run_start"' not in line and '"resume"' not in line and '"verdict"' not in line: continue
            try: ev = json.loads(line).get("event")
            except ValueError: continue
            if ev in ("run_start", "resume"): finished = False
            elif ev == "verdict": finished = True
    return finished
pending, runs = [], set()   # every finished child with no marker yet, as (key, run, child, why, issue, finished, definition)
for r in (sys.argv[1:] or (st / "active-runs.txt").read_text().split()):
    rec = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not rec.exists(): continue
    finished = record_finished(rec)
    try: run_issues = child_issues.request_issues((rec.parent / "request.txt").read_text())   # the issues the run was dispatched for (#2276)
    except OSError: run_issues = []
    issue, seen, done, unmapped, defn, failed, verdict, word = {}, set(), {}, [], {}, {}, {}, {}   # done: one item per child, its last finished entry (#611); word: the outcome that entry ended on
    owned, landed, rounds = set(), set(), {}   # the children the run reviews itself (#696), the children with a `land` event, and each child's last review round
    try: gated = {g.get("name") for g in json.loads((rec.parent / "graph.json").read_text()).get("nodes", []) if isinstance(g, dict) and g.get("review")}   # the dispatch nodes with a review gate
    except (OSError, ValueError, AttributeError): gated = set()
    for l in rec.open():
        if '"spawn_accepted"' not in l and '"outcome"' not in l and '"land"' not in l and '"review_' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get("event") == "spawn_accepted":
            seen.add(d["child"]); defn[d["child"]] = d.get("definition"); n, cited = child_issues.of_run(json.dumps(d.get("definition")), run_issues); n = int(n) if n else None
            if cited: logged(f"remap {rec.parent.name} {d['child']}", f"CHILDCC REMAP {rec.parent.name} {d['child']}: its prompt names #{cited} first, which is no issue of its run; mapped to #{n}")   # a cited issue never decides the mapping (#2276)
            if d.get("envelope") in gated: owned.add(d["child"])
            if n is not None: issue[d["child"]] = n
        elif d.get("event") == "outcome" and d.get("outcome") in ("pass", "unproven") and d.get("node") in issue:   # unproven: the work is done and the proof could not finish, so the child is finished; its entry carries an obligation only when a gate wrote one (ADR-0164)
            done.pop(d["node"], None); done[d["node"]] = (d.get("reasons") or [""])[0]; word[d["node"]] = d.get("outcome")   # a fix entry (#570) is entry 2, 3, ... of the same child: the last one stands, and its worktree holds its head
        elif d.get("event") == "outcome" and d.get("outcome") in ("pass", "unproven") and d.get("node") in seen:
            if d["node"] not in unmapped: unmapped.append(d["node"])
        elif d.get("event") == "review_round" and d.get("node") in seen:
            if d.get("verdict") == "no_review": owned.discard(d["node"])   # "no review here" is no review: the pass reads that child as before (ADR-0145 rule 9)
            else: owned.add(d["node"]); rounds[d["node"]] = d   # the run reviewed this head, so it owns the child (#701, ADR-0151 rule 5)
        elif str(d.get("event")).startswith("review_") and d.get("node") in seen:
            owned.add(d["node"])   # a review try or a round in flight: only the run reads that head
        elif d.get("event") == "land" and d.get("node") in seen:
            landed.add(d["node"])
            rv = (d.get("effect") or {}).get("review") if isinstance(d.get("effect"), dict) else None
            if isinstance(rv, dict): verdict[d["node"]] = rv.get("verdict")   # the run's own review of the child (#599, #601)
            if d.get("outcome") != "pass": failed[d["node"]] = (d.get("reasons") or ["the landing failed"])[0]   # the child's own engine landing: its work is not in the run's target (#592)
    name = pathlib.Path(r).name; runs.add(name)
    if (A / f"ccfixrun-{name}").exists(): continue   # a fix run this pass started: its child commits in another child's worktree, and delivers nothing itself (#636)
    for child, why in done.items():
        if not finished and child in owned and child not in landed: continue   # its live run still reviews, fixes or lands it (#696)
        # The run's approval is the verdict its landing recorded, or the verdict of the review round it wrote when it ended
        # before landing the child: both bind to the head the review read, and the second is the only evidence a run that
        # stopped at the landing leaves (#2089, ADR-0151 rule 4).
        if not (A / f"cc-{name}-{child}").exists(): pending.append((f"{name} {child}", r, child, why, issue[child], finished, defn.get(child), failed.get(child), verdict.get(child) == "approve" or str((rounds.get(child) or {}).get("verdict") or "") == "approve", child in owned, rounds.get(child)))
    for child in unmapped:
        mk = A / f"unmap-{name}-{child}"
        if mk.exists(): continue
        mk.touch(); print(f"CHILDCC UNMAPPED {name} {child}")
# The pass starts after the child the cursor names, in key order, so the work rotates over every pending child (#590).
pending.sort(key=lambda p: p[0])
PENDING[:] = pending; publish()
if STOP is not None:
    # The stop is on intake only (#641, ADR-0146 decision 3): an entered child is approved and gated, so a wave whose children
    # all entered still lands, and the pass exits 0 for it. A child not yet in its wave is never reviewed, fixed or entered
    # here, and a 0 would land the wave without it, so the pass exits 3 and the wave waits, until its age cap: the capped
    # landing requeues the child that never entered.
    line = f"LOOP STOP: #{STOP} is a loop defect; the fix lands outside the loop"
    print(line, flush=True)
    with (st / "cycle.log").open("a") as f: f.write(line + "\n")   # the loop log, which the hub reads
    publish(live=False)
    wait = sorted({pathlib.Path(p[1]).name for p in pending if (A / f"wave-{pathlib.Path(p[1]).name}").exists()})
    wait = [w for w in wait if subprocess.run(["bash", wave, "expired", w], capture_output=True).returncode]
    if wait:
        print(f"CHILDCC STOP the wave of {', '.join(wait)} waits: a child is not in it, and no child enters while the stop holds", flush=True)
        sys.exit(INCOMPLETE)
    sys.exit(0)
cur = CURSOR.read_text().strip() if CURSOR.exists() else ""
k0 = next((i for i, p in enumerate(pending) if p[0] > cur), 0)
spent, visited, RUN_HELD = 0, set(), set()   # RUN_HELD: the children this pass left to their run's approval, so the landing waits for them (ADR-0151 rule 5)
for key, r, child, why, n, finished, d, lwhy, ok, own, rnd in pending[k0:] + pending[:k0]:
    if spent >= BUDGET: break   # the budget is spent: the children left wait for the next pass
    visited.add(key)
    HELD.update(key=key, step="enter", rk=None); publish()
    name = pathlib.Path(r).name; mk = A / f"cc-{name}-{child}"
    e = subprocess.run(["gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state"], capture_output=True, text=True); s = e.stdout.strip()
    if e.returncode or s not in ("open", "closed"):   # a failed read is no answer: no done marker, so the child is tried again on the next pass (#848)
        CAUSE[key] = f"its issue state could not be read ({(e.stderr or s).strip()[:200] or f'exit {e.returncode}'})"
        logged(f"state {name} {child}", f"CHILDCC SKIP #{n} ({name} {child}): {CAUSE[key]}; no marker, tried on the next pass")
        visited.discard(key); continue
    if s == "closed": logged(f"closed {name} {child}", f"CHILDCC SKIP #{n} ({name} {child}): its issue is closed; no entry, no card move"); mk.touch(); continue
    waved = (A / f"wave-{name}").exists()   # the run's wave is not landed yet: its marker, not the card, says the child still has an entry to make
    if (A / f"waved-{name}").exists() and not waved:   # the wave is landed and this child is not in it: its issue went back to the queue, so no card moves (never the direct Batching move)
        logged(f"landed {name} {child}", f"CHILDCC SKIP #{n} ({name} {child}): its run's wave landed without it; no entry, no card move"); mk.touch(); continue
    if finished and not waved and not (A / f"waved-{name}").exists():   # the run cut no wave: no wave- or waved- marker (#1349)
        card = card_status(n)   # read once: the salvage and the backfill below share it
        if child_request(pathlib.Path(r)) and card not in ("Done", "Closed"):   # a child of a run dispatched to cut a wave: its commits reach no remote branch, so they are salvaged, whatever the card
            wt = child_worktree(pathlib.Path(r), child, d)
            if not wt.is_dir() or commits_ahead(wt) == 0:   # nothing past origin/dev to salvage: requeued with the reason, which loop_caused reads as the loop's
                subprocess.run(["bash", wave, "requeue", str(n), "its run cut no wave, so its finished commits have no entry"], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": name})
                print(f"CHILD NO-WAVE REQUEUE #{n} ({name} {child})", flush=True); mk.touch(); continue
            sha, branch = head_of(wt), f"fix/{n}-salvage-{name}"
            ok_push, why_push = salvage_push(wt, branch)
            if not ok_push:   # a failed push is no marker and no requeue: the next pass pushes again
                print(f"CHILD NO-WAVE SALVAGE FAILED #{n} ({name} {child}): the push of {sha[:9]} to {branch} failed, tried on the next pass: {why_push}", flush=True); continue
            (A / f"salvaged-{branch.replace('/', '_')}").write_text(sha + "\n")   # the provenance marker: branchland.sh gives a marked branch its rebase round (#2571)
            print(f"CHILD NO-WAVE SALVAGE #{n} ({name} {child}): pushed {sha[:9]} to {branch}", flush=True); mk.touch(); continue
        if card not in (*claimed_names().split("|"), "Todo", "Blocked"):   # backfill never moves a card past (or out of) its column; a card the backfill may move falls through to the entry, which moves it when there is no wave
            logged(f"backfill {name} {child}", f"CHILDCC SKIP #{n} ({name} {child}): its run ended and its card is past the backfill; no entry, no card move"); mk.touch(); continue
    if lwhy is not None and requeue_head(name, n):   # its run's own review held it and requeued it already (#601): never twice
        print(f"CHILD HELD #{n} ({name} {child}): its run's review requeued it", flush=True); mk.touch(); continue
    if own:   # the approval is read before a failed land's requeue: a run that reviewed the child owns it, and missing evidence holds delivery (#701, ADR-0151 rules 4 and 5)
        held = run_hold(pathlib.Path(r), child, n, d, ok, rnd)
        if held and finished and lwhy is None and moved_tip(pathlib.Path(r), child, d, rnd):   # #808: the run has ended and its review read an older head, so no run will review the new tip: childcc reviews it, once per head, and enters it on its own review
            own, ok = False, False
            tip = head_of(child_worktree(pathlib.Path(r), child, d))   # once per head (#2538): a tip at its requeue head waits silently
            logged(f"tip {name} {child}", f"CHILD REVIEW TIP #{n} ({name} {child}): the run ended on {(rnd or {}).get('head', '')[:9]}; the child's new tip {(tip or '')[:9]} is reviewed here")
        elif held:   # childcc never reviews or fixes a child its run reviewed: its run's approval is the only evidence, and a land that failed on the review itself (review_held, review_deadline) is missing evidence
            CAUSE[key] = held
            logged(f"held {name} {child}", f"CHILD RUN-OWNED #{n} ({name} {child}): {held}; delivery holds")
            RUN_HELD.add(key)
            if finished and not (requeue_head(name, n) == head_of(child_worktree(pathlib.Path(r), child, d))):
                # The run has its final verdict and will never review that head again, so the hold has no path to an
                # approval: the child counts toward the deferral deadline and its issue goes back to the queue with the
                # cause named (ADR-0151 rule 5). A live run's child keeps its unbounded hold. A head already requeued
                # stays out, so one issue is never sent back twice (ADR-0149).
                ATTEMPTED.add(key)
            if subprocess.run(["bash", wave, "expired", name], capture_output=True).returncode: visited.discard(key)   # a wave past its age cap lands and requeues it (#378)
            continue
    if lwhy is not None:   # the child's own engine landing failed: its work never reached the run's target, so no entry and no card move (#592); for a run-owned child only a standing approval at its reviewed head reaches here, so the failure is not the review's
        subprocess.run(["bash", wave, "requeue", str(n), f"its landing failed: {lwhy}"], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": name})
        print(f"CHILD LAND REQUEUE #{n} ({name} {child}): {lwhy}", flush=True); mk.touch(); continue
    entered, why, worked = enter(pathlib.Path(r), child, n, d, ok, own)
    if worked is None:   # the review budget was full: this child had no turn, so the pass leaves it (exit 3) and the next one tries it first
        CAUSE[key] = "no review slot this pass: caps.reviews is full"
        visited.discard(key); continue
    if worked: spent += 1; CURSOR.write_text(key + "\n")
    elif LOCK_HOLD:   # no work: the cursor stops just before the held child, so the next pass tries it first (#1407)
        i = [p[0] for p in pending].index(key)
        if i: CURSOR.write_text(pending[i - 1][0] + "\n")
        else: CURSOR.unlink(missing_ok=True)
    if why: print(why, flush=True)
    if key in HOLDS:   # a held child is no ruling: it stays left, so the pass exits 3 and the wave waits, until its age cap (#620)
        CAUSE[key] = why or "its entry held it on this head"
        if subprocess.run(["bash", wave, "expired", name], capture_output=True).returncode:
            visited.discard(key)
            if key not in FIX_WAIT: ATTEMPTED.add(key)   # only a child this pass tried and could not enter has a deadline; a budget or cursor child does not, and a live fix run is its own bound (#931)
    if LOCK_HOLD: break   # a lock hold ends the pass's entries: the next child meets the same holder, and the held child is tried first next pass (#1407)
    if entered is False: continue   # the child never entered the wave: no marker, so the next pass tries it again; no card moves, its issue went back to the queue
    mk.touch()
    if entered is None:   # no wave: the pass moves the cards itself, one per issue the child works (#954)
        for sib in every_issue(d, n):
            subprocess.run([board, owner, proj, batching_column(SHIPPED), str(sib)], capture_output=True)   # the vocabulary's own column (#2190)
            print(f"CODE COMPLETE #{sib} ({name} {child})", flush=True)
    ow = word.get(child, "pass")   # the outcome the child ended on: `unproven` is no pass, so the record never claims one
    (st / "cq" / f"{CQ}#{n}-cc-{mk.name}.md").write_text(f"Child {child} of squad run {name} finished: {ow}. "
        + (f"Its entry carries the proof obligation its gate wrote (ADR-0164). " if "with obligation" in why else "")   # only a gate writes an obligation (wave.sh's entry line names it); the child's own `unproven` word is none
        + f"Cards -> {batching_column(SHIPPED)}: {', '.join('#' + str(x) for x in every_issue(d, n))} (Closed follows when the wave lands in dev). {why[:600]}\n")
# The round holds the children worked since the last complete pass. A pass is complete when every child it left had its turn
# in the round: a child that fails on every pass then still lets the landing go on, as one pass over every child did. A held
# child is never in `visited` (#620): it holds the landing until its hold is requeued or its wave reaches its age cap.
rnd = set(ROUND.read_text().split("\n")) - {""} if ROUND.exists() else set()
keys = {p[0] for p in pending}
left = [k for k in keys - visited if k not in rnd or k in RUN_HELD]
# The children that wait for review: every pending child this pass did not work, deferred or out of the budget's reach. The
# dispatch passes read the count (gh_dispatch_hold, #562) and start no new run while too many wait; it is written on every
# pass, 0 when none wait, and atomically, so a reader never sees a half-written count.
WAITING = st / "childcc-waiting"
_tmp = WAITING.with_name(f"{WAITING.name}.{os.getpid()}.tmp"); _tmp.write_text(f"{len(keys - visited)}\n"); os.replace(_tmp, WAITING)
HELD.update(key=None, step=None, rk=None); publish(live=False)
for key, r, child, why, n, finished, d, lwhy, *_ in pending:   # a child that waits for review: its card reads Reviewing (#603)
    name = pathlib.Path(r).name
    if key not in visited and key not in RUN_HELD and (A / f"wave-{name}").exists() and not (A / f"render-{name}-{child}").exists(): code_review(name, child, n, "it waits for review")   # a child held for its render keeps its column (#2833)
info = {p[0]: p for p in pending}
for k in sorted(keys - visited):   # every child the pass leaves is named with its cause, every pass, so a deferral is diagnosable without a hand run (#931)
    p = info.get(k)
    if p is None: continue
    cause = CAUSE.get(k) or (f"the pass budget (SG_CHILDCC_BUDGET={BUDGET}) was spent before it" if spent >= BUDGET else "it waits for a later pass in this round")
    print(f"CHILDCC DEFERRED {pathlib.Path(p[1]).name} {p[2]} #{p[4]}: {cause}", flush=True)
if left:
    ROUND.write_text("".join(k + "\n" for k in sorted((rnd & keys) | visited | {k for k in rnd if k.split(" ")[0] not in runs})))
    ended = set()
    for k in sorted(left):
        p = info.get(k)
        if p is None or not p[5]: continue   # a live run's deferral is its own session's to resolve
        if k not in ATTEMPTED: continue      # a child the pass never tried has no deadline (#590, #931)
        _, r, child, _, n, _, d, *_ = p
        cause = CAUSE.get(k) or "it had no turn in this pass"
        if machine_hold(cause): continue     # a machine-wide hold clears by itself: never counted, never requeued (#931)
        rp, name = pathlib.Path(r), pathlib.Path(r).name
        tip = head_of(child_worktree(rp, child, d))
        if not tip: continue
        # The counter has its own name: the janitor drops a finished run's auto/ccdefer-* markers every cycle, so a count kept
        # there restarts at one before it reaches its bound (#945, #931).
        dk = A / f"dwait-{name}-{child}"
        try: was = json.loads(dk.read_text())
        except (OSError, ValueError): was = {}
        same = was.get("tip") == tip and was.get("cause") == cause
        cnt = int(was.get("cnt") or 0) + 1 if same else 1
        esc = str(was.get("esc") or "0") if same else "0"
        dk.write_text(json.dumps({"tip": tip, "cause": cause, "cnt": cnt, "esc": esc}))
        if cnt < DEFER_MAX: continue
        e = subprocess.run(["bash", wave, "requeue", str(n), cause], capture_output=True, text=True, env={**os.environ, "SG_REQUEUE_RUN": name})
        answer = (e.stdout + e.stderr).strip()
        # wave.sh answers a terminal stop with exit 0: "WAVE STOP" when the issue is frozen, and "WAVE NEEDS-HUMAN" when its
        # loop-caused requeues ran out (wave.sh loop_stop, and the `already stopped` answer). Either way the issue is out of
        # the queue, so the child is done here and the wave lands. Only a command that answered neither is a refusal (#931).
        stopped = not e.returncode and ("WAVE STOP" in answer or "WAVE NEEDS-HUMAN" in answer)
        if e.returncode or not (stopped or "WAVE REQUEUE" in answer):   # a refused requeue is no delivery: name it, keep the child and its count, retry next pass (#931)
            print(f"CHILD DEFER REQUEUE #{n} ({name} {child}) refused: {answer[:300] or f'exit {e.returncode}'}", flush=True)
            continue
        if not stopped: record_requeue(name, n, tip)   # the head the issue went back on, so no later pass requeues it again (ADR-0149); a terminal stop is no requeue
        if not stopped and esc != "1":   # the escalation is reported once, and only after the requeue stands (#808); a terminal stop is no requeue, so no comment claims the issue went back to the queue (#931)
            dk.write_text(json.dumps({"tip": tip, "cause": cause, "cnt": cnt, "esc": "1"}))
            (A / f"ccdefer-{name}-{child}").write_text(f"{tip} {cnt} 1\n")   # the escalation marker the janitor drops once the run is over
            br = subprocess.run(["git", "-C", str(child_worktree(rp, child, d)), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or "(detached)"
            (st / "cq").mkdir(parents=True, exist_ok=True)
            (st / "cq" / f"{CQ}#{n}-ccdefer-{name}-{child}.md").write_text(f"Squad run {name}, child {child} (issue #{n}): deferred {cnt} childcc passes at branch {br}, sha {tip}; it cannot have its turn here because {cause}. Its issue went back to the queue.\n")
            with (st / "alerts.log").open("a") as f: f.write(f"{time.strftime('%F %T')} CHILDCC DEFERRED {name} {child} #{n} branch {br} sha {tip[:9]}: waited {cnt} passes, requeued: {cause}\n")
        record_requeue(name, n, tip)   # the head it was requeued on, so the next pass costs nothing (ADR-0149)
        dk.unlink(missing_ok=True)
        ended.add(k)
        print(f"CHILD DEFER {'STOP' if stopped else 'REQUEUE'} #{n} ({name} {child}): {answer[:300] if stopped else cause}", flush=True)
    left = [k for k in left if k not in ended]
    if left: sys.exit(INCOMPLETE)
ROUND.write_text("".join(k + "\n" for k in sorted(k for k in rnd if k.split(" ")[0] not in runs)))   # this pass closed the round of its runs
