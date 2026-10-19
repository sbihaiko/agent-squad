#!/usr/bin/env python3
"""A child that finished with outcome pass moves its issue to Code Complete (called by watch.sh every loop).
A child of a run that cut a wave enters it first (#375, ADR-0145 rules 9 and 12): `wave.sh enter <run> <issue>` runs in the
child's own worktree, is the only route into the wave branch, and its entry is what moves the card; a child that did not
enter has no card to move, its issue went back to the queue. A run that cut no wave keeps the direct move.
The child's own diff is reviewed before that entry, here, once per head (childreview.sh, #448): the entry waits for the review
of the child's own commits against the wave branch (#376), and a child's branch is pushed nowhere (#375), so the review lane
that lists pushed work branches never saw it and every child was held out of its wave for good.
The child's issue is read from its own spawn prompt, first match wins: "GitHub issue #N", "issue #N",
"GitHub issues N", "GitHub issue N", "Refs #N", then a "gui/N" or "wt-as-N" branch/worktree name.
A pass whose prompt names no issue is logged once (CHILDCC UNMAPPED <run> <child>) instead of being skipped silently.
The runs the pass works are its arguments, else every live run: watch.sh's completion of a finished run hands it that one run, so the children that run finished are in its wave before that same step lands the wave (#375 review).
Live runs move at once; a run that already has its verdict (tiny and fork-only runs go pass -> verdict in under 1 s, so they are never seen live) is backfilled once,
but only when the card is still in Todo/In Progress/Blocked, never past Code Complete; a run with a wave not yet landed is not backfilled by card but by its wave marker, so a child that has not entered still enters. The once-marker is written when the child entered or needs no entry, never after a refused entry: the next pass tries it again. A run with more than one child also gets one parent issue naming the run, each child linked as a sub-issue through the seam (marker auto/parent-<run>). Once per child (marker auto/cc-<run>-<child>); only an open issue moves; queues a comment with the child's first reason.
Testing starts later (merge + e2e, or a test.sh pass), so Code Complete never overrides a later column: an issue already
past it is left alone because the marker is written the first time the child is seen passing.
One pass works at most SG_CHILDCC_BUDGET pending children (1 by default, #590): a review and the gates take 2 to 15 min a
child, and a pass that worked every child held the childcc lock, and every landing behind it, for 14 to 25 min. A child is
work when its entry reaches the review and the gates; a child its requeue holds on an unchanged head, or that needs no entry,
costs nothing. The cursor (auto/childcc-cursor) names the last child worked, and the next pass starts after it, so a child
held on every pass does not take every pass. A review takes one review slot of the machine-wide budget (caps.reviews, squad/caps.py), in try mode: a full budget skips the review for this pass with one log line and leaves the child for a later pass, so this pass never waits for a slot while it holds the childcc lock (SG_LAND_WAIT).
A pass that leaves a child that had no turn since the last complete pass
(auto/childcc-round) exits 3, never 0: the landing waits for that child, and a 0 would land the wave without it. A child
its entry held (WAVE HOLD: neither entered nor requeued) is no ruling either, so it stays left until its wave reaches its
age cap; SG_HOLD_TRIES review holds on one head (3 by default) requeue it with that reason, so the wave can land (#620).
A child whose own engine landing failed never enters: its work did not reach the run's target, so its issue goes back to
the queue once with the landing's reason, and no card moves (#592).
With the shipped vocabulary a child whose review starts, or that is deferred while it waits for review, moves its card from
Doing to Code Review through board.sh, once per child (marker auto/crv-<run>-<child>): a card in any other column, past
Code Review or sent back to Todo by a requeue, is never moved (#603, ADR-0143, ADR-0144).
A run reviews its own children (#601): the verdict of its review gate is in the child's `land` event. A child its run approved
enters with no second review here; a child its run held, with its requeued marker, is neither entered, reviewed nor requeued
again; a child of a run with no review in its record is reviewed here as before. An approved child that its entry still
holds (its review file is stale or gone) is reviewed here from the next pass (marker auto/apphold-<run>-<child>), so it is
never held for good (#600).
A dispatch child with fix entries (#570) has one passing `outcome` per entry: the pass works it once, by its last entry (#611)."""
import json, os, pathlib, re, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
import child_issues   # the issues a child's spawn text names (#555)
from issue_state import board_state
st = pathlib.Path(os.environ["SG_STATE"]); A = st / "auto"; A.mkdir(parents=True, exist_ok=True)
repo, owner, proj = os.environ["SG_REPO"], os.environ["SG_OWNER"], os.environ["SG_PROJECT"]
CQ = os.environ["SG_REPO"].replace("/", "__")   # a queued comment is named <owner>__<repo>#<n>-<tag>.md: cqflush.sh posts it to the repository it names
board = str(pathlib.Path(__file__).resolve().parent / "board.sh")
wave = str(pathlib.Path(__file__).resolve().parent / "wave.sh")   # the wave's verbs, beside this pass (#375)
childreview = str(pathlib.Path(__file__).resolve().parent / "childreview.sh")   # the review of the child's own diff, beside this pass (#448)
state = board_state()   # the dispatch pass asks the seam what state an issue carries, never the carrier (#236)
BUDGET = int(os.environ.get("SG_CHILDCC_BUDGET") or 1)   # the children one pass works (#590)
SHIPPED = os.environ.get("SG_BOARD_VOCAB") == "shipped"   # the old vocabulary has no Code Review column (#603)
HOLD_TRIES = int(os.environ.get("SG_HOLD_TRIES") or 3)   # the review holds on one head before the child is requeued (#620)
HOLDS = set()   # the children this pass held out of their wave, neither entered nor requeued: the landing waits for them (#620)
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
    for i in r.answer:
        names = [l.get("name") if isinstance(l, dict) else l for l in (i.get("labels") or [])]
        if "loop-bug" in names:
            try: return int(i["number"])
            except (KeyError, TypeError, ValueError): return None
    return None
n = loop_defect()
if n is not None:   # the loop cannot deliver its own fix: no child enters while one is open (#596, ADR-0146)
    line = f"LOOP STOP: #{n} is a loop defect; the fix lands outside the loop"
    print(line, flush=True)
    with (st / "cycle.log").open("a") as f: f.write(line + "\n")   # the loop log, which the hub reads
    sys.exit(0)
def child_worktree(rd, child, d):   # the tree a child worked in, by the engine's own rule (squad/runners/children.py, Children.workspace)
    ws = d.get("workspace") if isinstance(d, dict) else None
    logical = "target" if not ws or ws == "." else str(ws).replace("/", "_")
    return rd / "ws" / f"{logical}-{child}"
def head_of(wt):   # the sha the child's worktree stands on, "" when it has none
    return subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
def claimed(claim, head):   # a review claim [head, pid] holds while its pass is alive; a bare head, or a dead pass, holds nothing (#502)
    if len(claim) != 2 or claim[0] != head or not claim[1].isdigit(): return False
    try: os.kill(int(claim[1]), 0)
    except ProcessLookupError: return False
    except PermissionError: pass
    return True
def code_review(name, child, n, why):
    """Move the card of a child its review holds from Doing to Code Review, once per child (#603). Only a Doing card moves:
    a later column is never moved back, and a Todo card is a requeue's. A carrier that does not answer leaves no marker,
    so the next pass asks again; a refused write is named, never dropped."""
    mk = A / f"crv-{name}-{child}"
    if not SHIPPED or mk.exists(): return
    have = card_status(n)
    if not have: return
    mk.touch()
    if have not in ("Doing", "In Progress"): return
    e = subprocess.run([board, owner, proj, "Code Review", str(n)], capture_output=True, text=True)
    if e.returncode: print(f"CHILDCC-BOARD #{n} Code Review refused: {(e.stdout + e.stderr).strip()[:300]}", flush=True)
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
    if out: print(f"CHILD REVIEW {rd.name} {child}: {out}", flush=True)
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
def enter(rd, child, n, d, approved=False):
    """The wave's entry of a finished child, in the tree the engine made for it. Return (entered, why, worked): (None, "") when the
    run cut no wave and the direct move stands, (True, the entry line) when the child is now in the wave branch, and
    (False, why) when it is in none -- a child that did not enter has no delivery to claim. `worked` is True when the
    review and the gates ran, the work one pass is budgeted for (#590). A child a refused entry requeued
    is not tried again on the same HEAD (marker auto/requeued-<run>-<child>): the gates and the requeue wait for a new commit.
    The child's own diff is reviewed here, once per head, before the entry asks for its verdict. A review that asked for
    CHANGES is not a child held where it stands: nothing behind that hold could fix it (the fix lane reads pushed work
    branches, and a child's branch is pushed nowhere), so its issue goes back to the queue -- live run or not, its next head
    is read again."""
    if not (A / f"waved-{rd.name}").exists() and not (A / f"wave-{rd.name}").exists(): return None, "", False   # `waved-` is the durable record: a landing removes `wave-`, never this
    wt = child_worktree(rd, child, d)
    if not wt.is_dir(): return False, f"WAVE no entry for #{n}: the child's worktree {wt} is not there", False
    rq = A / f"requeued-{rd.name}-{child}"   # the sha a refused entry left the child on: the same sha would fail the same gates again, so the pass waits for a new commit
    if rq.exists() and rq.read_text().strip() == head_of(wt): return False, "", False
    hold = A / f"apphold-{rd.name}-{child}"   # its run approved it, but the entry held it once: the review here takes over (#600)
    approved = approved and not hold.exists()
    if not approved and not reviewed(rd, child, n, wt, A / f"rvd-{rd.name}-{child}"): return False, "", None   # no review slot: the child waits, and the landing waits for it
    e = subprocess.run(["bash", wave, "enter", rd.name, str(n)], cwd=str(wt), capture_output=True, text=True)
    out = (e.stdout + e.stderr).strip()
    if approved and "WAVE HOLD" in out: hold.touch()
    if "WAVE REQUEUE" in out or "WAVE STOP" in out: rq.write_text(head_of(wt) + "\n")   # a hold (no review yet) is no requeue: it is tried again on the same sha; a stop (#594) is one that froze the issue
    if e.returncode != 0 and re.search(r"did not approve it: .*CHANGES", out):
        why = f"the review of its own diff asked for changes: {first_finding(wt)}"
        subprocess.run(["bash", wave, "requeue", str(n), why], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": rd.name})   # the run is the key of the count (#609)
        rq.write_text(head_of(wt) + "\n"); out += f"\nCHILD REVIEW REQUEUE #{n}: {why}"
    elif e.returncode != 0 and "WAVE HOLD" in out:   # held, not requeued: the wave waits for it (#620)
        HOLDS.add(f"{rd.name} {child}")
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
                rq.write_text(h + "\n"); hk.unlink(); HOLDS.discard(f"{rd.name} {child}"); out += f"\nCHILD HOLD REQUEUE #{n}: {why}"
    return e.returncode == 0, out or f"WAVE no entry for #{n} (exit {e.returncode})", True
def card_status(n):   # the state the card carries, "" when it carries none or the carrier does not answer
    try: return state.read(n)
    except RuntimeError: return ""   # a refusal or a deadline miss is the carrier not answering, not a card that carries nothing
def issue_from(df):
    n = child_issues.first(df)   # the one reader the janitor's sibling passes share (#555)
    return int(n) if n else None
pending, runs = [], set()   # every finished child with no marker yet, as (key, run, child, why, issue, finished, definition)
for r in (sys.argv[1:] or (st / "active-runs.txt").read_text().split()):
    rec = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not rec.exists(): continue
    finished = '"event": "verdict"' in rec.read_text()
    issue, seen, done, unmapped, defn, failed, verdict = {}, set(), {}, [], {}, {}, {}   # done: one item per child, its last passing entry (#611)
    for l in rec.open():
        if '"spawn_accepted"' not in l and '"outcome"' not in l and '"land"' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get("event") == "spawn_accepted":
            seen.add(d["child"]); defn[d["child"]] = d.get("definition"); n = issue_from(json.dumps(d.get("definition")))
            if n is not None: issue[d["child"]] = n
        elif d.get("event") == "outcome" and d.get("outcome") == "pass" and d.get("node") in issue:
            done.pop(d["node"], None); done[d["node"]] = (d.get("reasons") or [""])[0]   # a fix entry (#570) is entry 2, 3, ... of the same child: the last one stands, and its worktree holds its head
        elif d.get("event") == "outcome" and d.get("outcome") == "pass" and d.get("node") in seen:
            if d["node"] not in unmapped: unmapped.append(d["node"])
        elif d.get("event") == "land" and d.get("node") in seen:
            rv = (d.get("effect") or {}).get("review") if isinstance(d.get("effect"), dict) else None
            if isinstance(rv, dict): verdict[d["node"]] = rv.get("verdict")   # the run's own review of the child (#599, #601)
            if d.get("outcome") != "pass": failed[d["node"]] = (d.get("reasons") or ["the landing failed"])[0]   # the child's own engine landing: its work is not in the run's target (#592)
    name = pathlib.Path(r).name; runs.add(name)
    for child, why in done.items():
        if not (A / f"cc-{name}-{child}").exists(): pending.append((f"{name} {child}", r, child, why, issue[child], finished, defn.get(child), failed.get(child), verdict.get(child) == "approve"))
    for child in unmapped:
        mk = A / f"unmap-{name}-{child}"
        if mk.exists(): continue
        mk.touch(); print(f"CHILDCC UNMAPPED {name} {child}")
# The pass starts after the child the cursor names, in key order, so the work rotates over every pending child (#590).
pending.sort(key=lambda p: p[0])
PENDING[:] = pending; publish()
cur = CURSOR.read_text().strip() if CURSOR.exists() else ""
k0 = next((i for i, p in enumerate(pending) if p[0] > cur), 0)
spent, visited = 0, set()
for key, r, child, why, n, finished, d, lwhy, ok in pending[k0:] + pending[:k0]:
    if spent >= BUDGET: break   # the budget is spent: the children left wait for the next pass
    visited.add(key)
    HELD.update(key=key, step="enter", rk=None); publish()
    name = pathlib.Path(r).name; mk = A / f"cc-{name}-{child}"
    s = subprocess.run(["gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state"], capture_output=True, text=True).stdout.strip()
    if s != "open": mk.touch(); continue
    waved = (A / f"wave-{name}").exists()   # the run's wave is not landed yet: its marker, not the card, says the child still has an entry to make
    if (A / f"waved-{name}").exists() and not waved: mk.touch(); continue   # the wave is landed and this child is not in it: its issue went back to the queue, so no card moves (never the direct Code Complete move)
    if lwhy is not None and (A / f"requeued-{name}-{child}").exists():   # its run's own review held it and requeued it already (#601): never twice
        print(f"CHILD HELD #{n} ({name} {child}): its run's review requeued it", flush=True); mk.touch(); continue
    if lwhy is not None:   # the child's own engine landing failed: its work never reached the run's target, so no entry and no card move (#592)
        subprocess.run(["bash", wave, "requeue", str(n), f"its landing failed: {lwhy}"], capture_output=True, env={**os.environ, "SG_REQUEUE_RUN": name})
        print(f"CHILD LAND REQUEUE #{n} ({name} {child}): {lwhy}", flush=True); mk.touch(); continue
    if finished and not waved and card_status(n) not in ("Todo", "In Progress", "Blocked"): mk.touch(); continue   # backfill never moves a card past (or out of) its column
    entered, why, worked = enter(pathlib.Path(r), child, n, d, ok)
    if worked is None: visited.discard(key); continue   # the review budget was full: this child had no turn, so the pass leaves it (exit 3) and the next one tries it first
    if worked: spent += 1; CURSOR.write_text(key + "\n")
    if why: print(why, flush=True)
    if key in HOLDS and subprocess.run(["bash", wave, "expired", name], capture_output=True).returncode:   # a held child is no ruling: it stays left, so the pass exits 3 and the wave waits, until its age cap (#620)
        visited.discard(key)
    if entered is False: continue   # the child never entered the wave: no marker, so the next pass tries it again; no card moves, its issue went back to the queue
    mk.touch()
    if entered is None: subprocess.run([board, owner, proj, "Code Complete", str(n)], capture_output=True)   # no wave: the pass moves the card itself
    (st / "cq" / f"{CQ}#{n}-cc-{mk.name}.md").write_text(f"Child {child} of squad run {name} finished: pass. Card -> Code Complete (Testing starts with the merge/e2e or the fork suite). {why[:600]}\n")
    print(f"CODE COMPLETE #{n} ({name} {child})")
# The round holds the children worked since the last complete pass. A pass is complete when every child it left had its turn
# in the round: a child that fails on every pass then still lets the landing go on, as one pass over every child did. A held
# child is never in `visited` (#620): it holds the landing until its hold is requeued or its wave reaches its age cap.
rnd = set(ROUND.read_text().split("\n")) - {""} if ROUND.exists() else set()
keys = {p[0] for p in pending}
left = [k for k in keys - visited if k not in rnd]
# The children that wait for review: every pending child this pass did not work, deferred or out of the budget's reach. The
# dispatch passes read the count (gh_dispatch_hold, #562) and start no new run while too many wait; it is written on every
# pass, 0 when none wait, and atomically, so a reader never sees a half-written count.
WAITING = st / "childcc-waiting"
_tmp = WAITING.with_name(f"{WAITING.name}.{os.getpid()}.tmp"); _tmp.write_text(f"{len(keys - visited)}\n"); os.replace(_tmp, WAITING)
HELD.update(key=None, step=None, rk=None); publish(live=False)
for key, r, child, why, n, finished, d, lwhy, _ in pending:   # a child that waits for review: its card reads Code Review (#603)
    name = pathlib.Path(r).name
    if key not in visited and (A / f"wave-{name}").exists(): code_review(name, child, n, "it waits for review")
if left:
    ROUND.write_text("".join(k + "\n" for k in sorted((rnd & keys) | visited | {k for k in rnd if k.split(" ")[0] not in runs})))
    print(f"CHILDCC DEFERRED {len(left)} child(ren) wait for a later pass: {', '.join(sorted(left))}", flush=True)
    sys.exit(INCOMPLETE)
ROUND.write_text("".join(k + "\n" for k in sorted(k for k in rnd if k.split(" ")[0] not in runs)))   # this pass closed the round of its runs
