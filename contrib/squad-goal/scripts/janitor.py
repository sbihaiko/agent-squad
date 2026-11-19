#!/usr/bin/env python3
"""Janitor: one conservative cleanup pass per watch cycle (JANITOR_DRY=1 prints and acts on nothing). Every action goes to stdout and $SG_STATE/cycle.log.
 A  finished run (has a verdict, 3 min old): kill every process whose cwd is inside its run folder (leftover claude/dotnet/test hosts), as a tree;
    a process a pass of this loop started (childcc.py: the child review, the wave landing) is never a leftover (#497).
 B  finished run (1 h old): kill its idle dashboard_server.
 S  a counted ceiling on this loop's dashboards (JANITOR_DASHBOARD_MAX, default 8; 0 turns it off). B, J and C reap the dashboard of the one
    run the pass knows about, so a run that left active-runs.txt, or a record the pass cannot read, leaves its dashboard behind and the count
    grows with nothing bounding it (41 were alive after the freeze of 2026-10-13). S counts every dashboard this loop started, whatever the
    dashboard serves, and kills the oldest one the pass cannot tie to a live run, down to the ceiling. A live run's own dashboard is never a
    victim, a dashboard another project started is not this pass's to count, and JANITOR_DRY=1 reports without killing.
 C  zombie run (no verdict, record.jsonl idle 20 min, no run process inside its folder -- the engine, a gate or a session, never a dashboard server -- last started call dead): write the killed verdict into its record (squad.killed), and free its dispatch markers and leases.
    It leaves active-runs.txt on the same pass, and the line says so, so the listing and the leases agree in one sample (#1854): a run no lease counts is no live work. Its line stays only while one of its waves is open, which still lands it (wave-<run>, as wtclean.sh rule A holds a finished run's line).
 T  taken-over run (no verdict, its record has not moved for SG_TAKEOVER_SECONDS, default 1800, no gate process is alive in its folder, and no open call is still inside the deadline that call recorded): end it and keep its issues claimed (ADR-0166). A run waiting its turn in the machine-wide gate queue is not stuck: that wait is no run time, so it never reaches the bound (#1191). A call writes no event until it ends, so a run inside one long call is inside that call's own deadline: the deadline ends the call, and a takeover before it would kill the call short of its cap (#1842). The pass writes the killed
    verdict, kills every process inside the run folder and its dashboard, drops it from active-runs and writes auto/taken-<run>, which names the run and its issues.
    It never frees auto/disp-<n> or auto/bug-<n>, so no fresh child starts the same work with none of the context: a person or an agent takes it over.
    While the marker stands its issues are held, so rule Q never puts their cards back in Todo. The owner or the agent drops the marker after the hand fix.
 D  two live runs that accepted the same issue: report once (never kills).
 E  auto/disp-<n> older than 30 min whose dispatching request log says `verdict failed`: drop it so the frontier can dispatch the issue again.
 F  an open issue named "(#N)" in the subject of a fork commit older than 30 min, with no open work branch naming it: report FIXED-NOT-CLOSED once (never closes: closing needs evidence).
 I  a live run whose accepted issues are ALL closed: when every one was closed as not_planned or is titled "[duplicate…", stop it (kill its process tree and its last call);
    also stop it when every one closed after the run started and before the run's last progress and the run holds no landing for it (no land in its record or the ledger, no branch of its children naming the issue), and write the verdict that says why (#1571);
    otherwise report RUN-ON-CLOSED-ISSUES once and leave it (a closed issue can still have a finishing fix/land run). Once per run.
 J  a run with no record.jsonl 5 min after its request (the squad refused to start): kill its dashboard, drop it from active-runs, and free every issue named in its request (auto/bug-<n> and auto/disp-<n>) so autobug/autofrontier can dispatch again, and the auto/branchfix-<key>-<sha9> marker of the fix round it never started, so the fix pass dispatches that tip again (#2309). Comment once ("run X never started, dispatching again", attempt n of 2) quoting the last lines of the run's launch.log. After 2 never-started runs for the same issue, keep the marker, comment the evidence, raise HEALTH, and do not retry a third time.
 K  a disp-<n> marker whose request log says `Nothing ran` or `refusing to start` (10 min old, no live run on the issue) is dropped like rule E does for `verdict failed`.
 O  a run that recorded a verdict and landed nothing (no `land` pass in its record, that event being the landing stage's own -- a router that passed by asking for children is not a landing): the issue's marker is dropped, so autofrontier/autobug dispatch it again. `failed` is not the only way a run ends without landing (#1847): a run that ended any other way (`needs-human`, `pass`) left its auto/disp-<n> behind, autofrontier.sh skips every issue holding that marker, and the frontier starved on a run that is over -- the cause health.sh reports as frontier-idle.
    A run that landed a change, an issue a live run works or a pending wave carries, and an issue with an open work branch keeps its marker; a run drops only a marker its own request wrote (#539). The loop-board card is rule R's: O writes none, and its comment names the column R wrote
    (a failed run -> Todo, a needs-human run -> Blocked). On the bug board, auto/bug-<n> -> To triage, but only from the column the dispatch set ($SG_BUG_DOING), so a card another step moved past it is never downgraded. Once per run; the comment carries the run id and the verdict's reasons.
    Each such run, marker or not (needs-human aside), is one failed dispatch of the issue, counted once by `wave.sh fail` (#609): at SG_REQUEUE_CAP with no new commit in the base the issue is frozen for the owner. A loop-caused one (the run's own review verdict decides, #621) counts only toward SG_LOOP_REQUEUE_CAP.
 U  an auto/disp-<n> marker whose named run or request is no longer live (no active-runs.txt entry and no run folder under the run root names it) and whose issue's ledger records the owner's rearm newer than the marker's mtime, with no pending card write: the marker is dropped (#1586), so the frontier answers the issue startable again. The card is rule R's and the dispatch count is rule O's, so U writes one line and nothing else; a live run, an older rearm and a pending card write (ADR-0165) each hold the marker. `auto/taken-*` is rule T's own case (ADR-0166) and is never read.
 V  a wave marker of a run that ended: `auto/wave-<run>`, `wave-start-<run>` and `wave-pause-<run>` stand for the run's landing pass, and the loop lands a wave only for a line of active-runs.txt. A run that left the listing can never land it again, and the marker stayed for ever (1325 of them, #2140). The pass drops the markers of a run no line names and no live process works, and names each one; a listed run's wave, and a folder a live process works, stay.
 W  a run whose child started on a blocker's approved tip (`STACK TIP:` in its request, #2260) and whose blocker left Batching without a landing: the blocker's card reads a column before Batching and no work branch of its stands in the landing branch, so the child's commits sit on a branch the base may never hold.
    The one column set is the board definition's, read through `board_columns.from_batching`, so this rule and stack.sh never disagree. The landing is proven by remote ancestry (`git merge-base --is-ancestor`), and an unread compare holds the run: no run is stopped on a fact nobody read.
    The pass stops the run, writes the killed verdict `stack_base_left_landing:#<blocker>`, drops the dispatch markers of its issues and writes the reoffer marker of each, so the frontier builds them again once the blocker is startable. A blocker still in Batching or Testing, a blocker whose work reached the landing branch, and a run cut from the base keep the run.
 X  an auto/disp-<n> or auto/bug-<n> marker whose issue reads closed is dropped (#2142): the loop dispatched the issue,
    the issue closed, and the marker stayed, so sg_dispatch_claimed read a reopened issue as already claimed and the
    frontier skipped it with no line. A marker of an open issue stays. A live run that works the issue, a taken-over
    run's marker (ADR-0166) and a pending card write (ADR-0165) each hold it, and a state read that did not answer
    holds it too. The read costs one call per marker, so a bounded number of markers per pass
    (JANITOR_CLOSED_MARKER_MAX, default 50) drains a backlog over a few passes instead of spending the shared GitHub budget in one burst.
 Q  a card still In Progress behind its auto/disp-<n> (or auto/bug-<n>) marker, 10 min old, that no run works: not a live run, not a run in its launch window, not a run that landed, no open work branch naming the issue, no fork-<n>-<sha9> marker.
    Rules E and O read a run's verdict, so a run that ended without one or that left active-runs by any other road freed nothing: the card sat In Progress, autofrontier.sh skipped the issue,
    and health.sh reported runs-board-drift for hours. Rule Q reads the state, never the run's history: the card goes back to Todo (the bug card to To triage), the marker is dropped, one comment is queued. A refused read touches nothing.
    A marker behind a card already back in Todo (the bug card in To triage), with the same "no run, no work branch" answer, is dropped with no board write (#771): a failed run that left active-runs before rule O released it kept its issue claimed for good.
 P  a `fork-<n>-<sha9>` marker whose commit left the fork base while the base no longer carries its change and the branch has nothing ahead to merge: an unwound landing (a hand revert, a branch reset, a base rebuilt under it).
    Its issue goes back to the column the dispatch reads (Todo; the bug board's Doing -> To triage) and the fork/disp/bug markers are dropped, so the frontier builds it again (#1830). A marker whose commit is in the base, or whose change the base holds anyway, and a branch still ahead of the base (the close retries on its head) are left alone.
 H  board state against the issue's own history (its loop comments; the REST timeline carries no project-status events), at most once per 10 min:
    RED-LOOP #N     3+ "fork suite failed after merging" comments with no green fork close since: the loop is not converging, a person must look.
    STATE-REGRESSION #N  the card is Todo/Blocked although the history says it reached Code Complete, or the card is Done while the issue is open; R repairs a Done card to the supported column.
 R  every board card the passes above know of: its expected status is derived from the run's own truth and the evidence beside it, and only a mismatch is written (one card, one PATCH), with the reason named.
    A live run holds the issue (dispatch marker or accepted child) -> In Progress; an issue that is closed or whose work branch is in $SG_BASE -> Done, so work that landed on a parent branch (#127) and never reached the base is not Done;
    a landing is credited to the child that made it, never to every issue the run held (#271): an issue a child's own accept line names follows that child's own `land` event and its own outcome, so a child that
    committed nothing keeps the loop's queue however many of its siblings landed, and only an issue no child of the run names takes the run's landing as its own;
    a landing of a run that cut a wave, or of a branchland pass, is the `landed` fact the landing pass wrote in the issue's ledger (ADR-0149, #695), never a head the run's record names: a landing that reached no base wrote no fact, so it delivers nothing (#691);
    a completed run, whether or not it landed work, whose branch has not landed -> Code Complete; a failed run with nothing landed -> $SG_TODO (Todo); a needs-human run with nothing landed -> Blocked (ADR-0029: the owner is the next actor,
    and Todo would tell him the loop has it), unless the owner's rearm is newer than that run's verdict: then the freeze is not written and cycle.log names it once per run verdict (BOARD-UNFROZEN, #1562); an issue labelled needs-owner or needs-ratification -> Blocked. R is the one owner of a loop-board card a kept run names: no other rule writes one, so no rule races it.
    An issue labelled frozen:owner keeps the column it carries, and cycle.log names the write R held back (BOARD-FROZEN). A run that stopped needs-human or failed never moves its issue's
    card forward: its Todo or Blocked stands, and a work branch it left is stale evidence. A landing is delivered work once it reached the landing branch (ADR-0143): a failed run's
    landing whose head origin/<land> holds reaches Code Complete, the child's own or the run's, and one that stayed its local land commit delivered nothing,
    so the card goes back to Todo and rule O drops the marker (#691); a needs-human run's landing keeps Blocked, since the owner is the next actor (ADR-0029).
    Text fields: Run and Run started carry the run id and its start time while the card is In Progress, and are cleared when it is anything else. A card already in its expected status is not written again, and a write GitHub refuses is named in cycle.log (BOARD-RECONCILE / BOARD-WRITE-FAILED), never swallowed.
    The board is read once per pass (one fields read, one items read per project), and the card cache follows every status the pass writes, so a later rule (O, Q, P) reads the column the pass set, never the stale read. An issue the board answers no card for is added with its expected status in one write (board.sh --add);
    a board that does not answer at all (a refusal, a deadline miss) is told apart from that: nothing is moved on a guess, nothing is reported as a defect.
    An issue the items read omits is not called absent on the omission alone (#1463): the REST route is eventually consistent and sometimes never carries a card GraphQL names, so the
    pass asks once per omitted issue of the GraphQL projectItems read and the card it finds rides the pass's map; the write failure is named only when both reads fail to carry the card.
    With SG_BOARD_VOCAB=shipped every terminal entry carries its condition in one queued comment (#245): Closed names the branch in the landing line (or who closed it, when no branch names it); Abandoned names who decided and why (wontfix, closed as not planned or as a duplicate).
    A terminal write whose condition no read answers is not made (TERMINAL-NO-CONDITION), and a card settled in a terminal column with no tagged comment
    gets one, once (TERMINAL-BACKFILL, JANITOR_TERMINAL_BACKFILL per pass, default 5).
    With SG_BOARD_VOCAB=shipped R is also the one producer of the board facts (board_facts.py, ADR-0145): a run's wave branch on the remote, the review of its tip, and the check runs of the dev tip give wave_branch, landing and dev_ci (reported facts, no column), and a work branch whose review waits gives review_wait; board_columns.column_for names the column (Batching, Reviewing, Doing).
    A child of an ended run whose code is done and that has not entered the run's open wave is in Reviewing, and no live run or
    review moves a card back from a later column (#603, ADR-0143). No route is added: the branch read is the one work_branches makes, and the dev tip's CI is one door read a pass.
 M  the live skill copy ($SG_LIVE_DIR, else ~/.claude/skills/squad-goal): a file whose mtime changed, or that came or went, since the last pass while no
    publish ran raises LIVE-MTIME (#659). live-guard.sh stamps $SG_STATE/live-publish.stamp when it publishes, and every publish writes the live PUBLISHED file
    (#727); either one newer than the last snapshot excuses the pass's changes. A Python bytecode cache (__pycache__/, *.pyc, *.pyo) and an editor's .DS_Store are no change to the
    loop's code and raise nothing. The snapshot is $SG_STATE/live-mtime.json; the first pass only takes it, and a dry pass never keeps it.
 N  the frontier's idle streak ($SG_STATE/frontier-idle: the epoch the streak began and the frontier's own last idle line, written by
    autofrontier.sh): a streak past SG_FRONTIER_IDLE_SECONDS (default 3600) raises one FRONTIER-IDLE alert naming the Todo count and
    the reasons of that line (#744), because a loop can hold Todo work and start nothing for hours with only an attentive owner to
    notice. It repeats at most once per bound, it never dispatches, and a pass that dispatches drops the file, so the next idle
    starts the clock again. A dry pass raises no alert and writes no bound.
Every open issue of the repository carries a card, whether or not a run names it (#92): the pass reads the repository's open-issue list once through the door (gh_aggregate, the throttle) and adds the ones the board does not carry, in the dispatch column, or in Blocked when the issue carries needs-owner or needs-ratification, so an issue created while the loop runs is never left with no status."""
import calendar, contextlib, importlib.util, io, json, os, pathlib, re, signal, subprocess, sys, tempfile, time, urllib.parse
st = pathlib.Path(os.environ["SG_STATE"]); co = pathlib.Path(os.environ["SG_CHECKOUT"]).resolve(); dry = bool(os.environ.get("JANITOR_DRY")); now = time.time()
CQ = os.environ["SG_REPO"].replace("/", "__")   # a queued comment is named <owner>__<repo>#<n>-<tag>.md: cqflush.sh posts it to the repository it names
acts = []
def say(m):   # details go to cycle.log (or stdout in a dry run); watch.log gets one summary line per pass
    acts.append(m)
    if dry: print("JANITOR " + m, flush=True)
    else: open(st / "cycle.log", "a").write(time.strftime("%T ") + "janitor " + m + "\n")
DEADLINE = float(os.environ.get("JANITOR_CALL_TIMEOUT", "60"))   # a call has a deadline (ADR-0051); 0 waits, and is only for a hand run
# A tool's absolute path differs by platform: /usr/sbin/lsof holds on macOS and is absent on the CI runner, where a
# hardcoded path turns a missing tool into a failed pass. The shell resolves it into SG_LSOF (env.sh); this is the same
# one resolution for a run with no shell around it, and the last entry keeps a tool outside both paths reachable.
LSOF = next((p for p in (os.environ.get("SG_LSOF"), "/usr/sbin/lsof", "/usr/bin/lsof") if p and os.path.exists(p)), "lsof")
def _num(name, default):   # a bad value falls back to the default, never stops the pass
    try: return float(os.environ.get(name) or default)
    except ValueError: return float(default)
PASS_DEADLINE = _num("SG_JANITOR_DEADLINE", 900)   # seconds a pass may run; 0 keeps no bound (#1774)
SLOW_CALL = _num("SG_JANITOR_SLOW_CALL", 5)        # a call of this many seconds or more writes its time and its command
T0, CALLS = time.monotonic(), [0]
def note(m):   # a timing line for cycle.log: it is no action, so it never enters the pass summary that `acts` builds
    if dry: print("JANITOR " + m, flush=True)
    else: open(st / "cycle.log", "a").write(time.strftime("%T ") + "janitor " + m + "\n")
def _over():   # past the pass deadline: one line in cycle.log and in alerts.log, then the pass ends here (#1774)
    msg = f"pass deadline {PASS_DEADLINE:g}s: {CALLS[0]} calls, the rest of the pass is skipped"
    note(msg)
    if not dry: open(st / "alerts.log", "a").write(time.strftime("%F %T ") + "janitor " + msg + "\n")
    raise SystemExit(0)   # a BaseException: no `except Exception` of a rule can keep the pass going
def _run(a, cwd=None, env=None, timeout=None):   # the only runner of this module: it writes into a file, never a pipe, so a grandchild
    """Run one command and return (exit status, stdout), at most DEADLINE seconds. A pipe is never used: a command's
    grandchild that inherits stdout would hold the pipe past the child's exit, and the pass would wait for it for ever
    (the loop stopped for 28 min on 2026-10-13). A deadline miss is one status of 1 and one line in the pass log.
    The pass has a deadline too (#1774): past SG_JANITOR_DEADLINE no call starts and the pass ends."""
    if PASS_DEADLINE and time.monotonic() - T0 > PASS_DEADLINE: _over()
    CALLS[0] += 1
    t = time.monotonic()
    try:
        with tempfile.TemporaryFile("w+") as f:
            try:
                rc = subprocess.run(a, stdout=f, stderr=subprocess.DEVNULL, text=True, cwd=cwd, env=env, timeout=(DEADLINE if timeout is None else timeout) or None).returncode
            except subprocess.TimeoutExpired:
                say(f"call timed out after {(DEADLINE if timeout is None else timeout):g}s: {' '.join(a[:3])}")
                return 1, ""
            except OSError as e:
                say(f"call failed: {' '.join(a[:3])}: {e}")
                return 1, ""
            f.seek(0)
            return rc, f.read()
    finally:
        e = time.monotonic() - t
        if e >= SLOW_CALL: note(f"slow call {e:.1f}s: {' '.join(str(x) for x in a[:4])}")
def sh(*a): return _run(a)[1]
def rc(*a): return _run(a)[0]
alerts = st / "alerts.log"
def alert(msg):   # alerts.log is what health.sh and the session read; the notification reaches the human away from the terminal
    open(alerts, "a").write(time.strftime("%F %T ") + msg + "\n")
    if not dry: _run([os.environ.get("JANITOR_NOTIFY", "/usr/bin/osascript"), "-e", 'display notification "' + msg.replace('"', "'")[:180] + '" with title "squad-goal"'])
def issues_named(text):   # the request's CHILD header is the authoritative list; the body of a request also embeds the raw
    found = set()          # issue text, which names other issues in prose, so a bare `issue #N` scan would free the wrong markers
    for l in text.splitlines():
        if not l.startswith("CHILD"): continue
        for m in re.finditer(r"GitHub issue(?:\(s\))? #?([0-9][0-9, ]*)", l):
            found.update(x.strip() for x in m.group(1).split(",") if x.strip().isdigit())
    for m in re.finditer(r"GitHub issue #(\d+)", text):
        found.add(m.group(1))
    return found
def launch_tail(f, n=15):
    p = f / "launch.log"
    if not p.exists(): return ""
    try: return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
    except OSError: return ""
def queue_neverstart(n, body):
    (st / "cq").mkdir(exist_ok=True)
    (st / "cq" / f"{CQ}#{n}-neverstart-{os.getpid()}.md").write_text(body)
def free_never_started(f):   # drop auto/bug-<n> and auto/disp-<n> for the issues the run was dispatched for, at most twice
    # (#2309) A fix round keeps auto/branchfix-<key>-<sha9> with the run id it dispatched, and the fix pass skips a tip
    # whose marker stands. A run that never started dispatched no round, so its marker names work that never began and
    # would hold that tip for ever: the marker drops here, whatever the retry count of its issues. Only a marker this
    # run's own name stands in is released.
    fx = [p for p in (st / "auto").glob("branchfix-*") if marker_text(p) == f.name] if (st / "auto").is_dir() else []
    if fx:
        say(f"NEVER-STARTED run {f.name}: {len(fx)} fix marker(s) of the round it never started released ({', '.join(sorted(p.name for p in fx))})")
        if not dry:
            for p in fx: p.unlink(missing_ok=True)
    req = f / "request.txt"
    nums = issues_named(req.read_text()) if req.exists() else set()
    tail = launch_tail(f)
    quoted = f"\n```\n{tail}\n```\n" if tail else "\n"
    for n in sorted(nums, key=int):
        marks = [m for m in (st / "auto" / f"{kind}-{n}" for kind in ("bug", "disp")) if owns(f, None, m)]
        if not marks: continue   # nothing this run's request dispatched: not ours to free, and no comment
        if issue_read(n)[0] == "closed":   # a closed issue is never dispatched again: its stale marker goes with no retry and no comment, so nothing re-arms it (#868)
            say(f"NEVER-STARTED #{n}: its issue is closed; {'+'.join(m.name for m in marks)} released, no retry")
            if not dry:
                for mk in marks:
                    if mk.exists(): mk.unlink()
            continue
        ctr = st / "auto" / f"neverstart-{n}"
        try: k = int(ctr.read_text().strip() or "0")
        except (ValueError, OSError): k = 0
        if k >= 2:
            say(f"NEVER-STARTED #{n}: 2 retries already spent, marker kept")
            continue
        k += 1
        if not dry: ctr.write_text(str(k) + "\n")
        if k < 2:
            say(f"NEVER-STARTED #{n}: run {f.name} never started, dispatching again (attempt {k} of 2)")
            if not dry:
                for mk in marks:
                    if mk.exists(): mk.unlink()
                queue_neverstart(n, f"run {f.name} never started, dispatching again (attempt {k} of 2).{quoted}")
        else:
            say(f"NEVER-STARTED #{n}: run {f.name} never started (attempt {k} of 2); not retrying, marker kept")
            if not dry:
                queue_neverstart(n, f"run {f.name} never started (attempt {k} of 2); not retrying.{quoted}")
                alert(f"HEALTH never-started-retry: issue #{n} never started twice (run {f.name}); not retrying")
def _procs_module():
    """squad/procs.py, the one process reader, loaded by path: the repository this script sits in first, as caps.sh reads it,
    then SG_SQUAD, SG_CHECKOUT, else the tree it runs in. That file imports nothing from squad, so it loads alone under any python3 (#1508)."""
    name = "squad_procs"
    if hasattr(sys.modules.get(name), "snapshot"): return sys.modules[name]   # never a foreign stub another loader left
    roots = [r for r in (str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]   # a copy outside the repo: the tree it runs in
    root = next((r for r in roots if (pathlib.Path(r) / "squad" / "procs.py").is_file()), roots[-1])
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(root) / "squad" / "procs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod   # a dataclass in the module reads its own module by name while it loads
    spec.loader.exec_module(mod)
    return mod
PR = _procs_module()
def _active_runs_module():
    """squad/active_runs.py, the one writer protocol of the active-runs list (#1862), loaded by the same roots as procs.py:
    the repository this script sits in first, then SG_SQUAD, SG_CHECKOUT, else the tree it runs in. It imports no squad
    module, so it loads alone under any python3. It is loaded at the write, so a pass that rewrites nothing needs it not."""
    name = "squad_active_runs"
    if hasattr(sys.modules.get(name), "rewrite"): return sys.modules[name]
    roots = [r for r in (str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]
    root = next((r for r in roots if (pathlib.Path(r) / "squad" / "active_runs.py").is_file()), roots[-1])
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(root) / "squad" / "active_runs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
def _handhold_module():
    """squad/handhold.py, the one reader and writer of a hand hold (loop-bug #1664), imported from the repository this script sits in
    first, then SG_SQUAD, SG_CHECKOUT, else the tree it runs in. The janitor carries no second copy of the hold's format."""
    roots = [r for r in (str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]
    root = next((r for r in roots if (pathlib.Path(r) / "squad" / "handhold.py").is_file()), roots[-1])
    if root not in sys.path: sys.path.insert(0, root)
    from squad import handhold
    return handhold
def checkout_bare():   # bare checkout (#2306): git reads the profile's checkout as bare, so every git command in its work tree fails; one alert per episode
    # It runs first in the pass, so the pass deadline never skips it. rev-parse answers for the work tree, so a linked worktree of a
    # bare repository reads false; only a read of `false` ends the episode, and a call that fails or times out keeps the marker.
    mk = st / "checkout-bare-alert"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")}
    rc, out = _run(["git", "-C", str(co), "rev-parse", "--is-bare-repository"], env=env)
    out = out.strip()
    if rc == 0 and out == "false":
        if not dry: mk.unlink(missing_ok=True)
        return
    if rc != 0 or out != "true" or dry or mk.exists(): return
    alert(f"CHECKOUT-BARE {co}: git reads the checkout as a bare repository (core.bare = true), so every git command in the work tree fails; run `git -C {co} config core.bare false` (NEEDS OWNER)")
    mk.write_text(f"{now}\n")
checkout_bare()
try: _table = PR.snapshot()
except PR.ReadError: _table = {}   # no read: no process is judged gone or alive on it
procs = {pid: (p.ppid, p.command) for pid, p in _table.items()}
starts = {pid: p.start for pid, p in _table.items() if p.start is not None}
cwd = {}; pid = None
for l in sh(LSOF, "-d", "cwd", "-Fpn").splitlines():
    if l.startswith("p"): pid = int(l[1:])
    elif l.startswith("n") and pid: cwd[pid] = l[1:]
me = set(); p = os.getpid()
while p in procs: me.add(p); p = procs[p][0]
def tree(root):
    out = [root]
    for q, (pp, _) in procs.items():
        if pp == root: out += tree(q)
    return out
def wip_snapshot(folder, run_id, dry=False):   # #1662: the one writer of a run's WIP refs, loaded by path (a bare file under -I, as squad_procs is)
    name = "squad_wip_snapshot"
    if not hasattr(sys.modules.get(name), "snapshot_run"):
        spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).resolve().parent / "wip_snapshot.py")
        mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return sys.modules[name].snapshot_run(folder, run_id, dry=dry)
killed = set()   # every pid this pass kills: the process table is the pass-start snapshot, so a run whose leftover this pass
# killed is read as live no more (#2419). A rule that decides a run is over reads it, never the stale table alone.
def kill(pidv, why):
    if pidv in me: return
    for q in reversed(tree(pidv)):
        if q in me: continue
        say(f"kill pid={q} ({procs.get(q, (0, '?'))[1][:70]}) {why}")
        killed.add(q)
        if not dry:
            try: os.kill(q, signal.SIGTERM)
            except OSError: pass
OWN_PASS = ("childcc.py",)   # the loop's own passes that work inside a finished run's folder by design (#497)
SESSION = re.compile(r"_bundled/claude|/claude\b|\bclaude-code\b|squad\.cli|-m squad\b")   # a run's own session, never a helper (#745)
ENGINE = re.compile(r"[s]quad (start|resume)( |$)")   # the engine of a run: the .venv/bin/squad entry point, `squad start` or `squad resume` (#1270)
# The liveness of a run, the same predicate as squad/reap.py LIVE_PROC (a copy: this script runs as a bare file). A run's process
# is its engine, a gate or a session; a dashboard server (`-m squad.dashboard_server`) is none of them, so it never keeps a run alive (#1270).
LIVE = re.compile(r"gate\.sh\b|_bundled/claude|/claude\b|\bclaude-code\b|squad\.cli|-m squad(?!\.dashboard_server)\b|[s]quad (start|resume)( |$)")
def own_pass(q):
    """True when the process or one of its ancestors is a pass of this loop: the child review (childcc.py -> childreview.sh ->
    review.sh -> the model) and `wave.sh land` run after the run's verdict, in the child's worktree inside the run folder,
    so rule A read them as leftovers and killed every child review (#497). A process a pass of the loop started is its own
    to end, never a leftover; a stray process in the same folder still is."""
    seen = set()
    while q in procs and q not in seen:
        seen.add(q); pp, c = procs[q]
        if any(s in c for s in OWN_PASS): return True
        q = pp
    return False
_in_land = {}
def in_land(sha):   # the commit is an ancestor of origin/<land>, read in the checkout as named_in_land reads it: the work reached the landing branch. A checkout with no such ref holds none
    if sha not in _in_land:
        _in_land[sha] = _run(["git", "-C", str(co), "merge-base", "--is-ancestor", sha, f"origin/{os.environ.get('SG_LAND_BRANCH') or 'dev'}"])[0] == 0
    return _in_land[sha]
_on_remote = {}; _remote_held = None
def known(sha):   # the checkout can read this commit at all: a head it cannot see names no reach, so the old reading stands (#1958)
    return _run(["git", "-C", str(co), "cat-file", "-e", str(sha) + "^{commit}"])[0] == 0
def remote():   # the tracking remote a salvage or landing pass pushes to: the landing branch's own, or origin
    return os.environ.get("SG_REMOTE") or _run(["git", "-C", str(co), "config", "--get", f"branch.{os.environ.get('SG_LAND_BRANCH') or 'dev'}.remote"])[1].strip() or "origin"
def on_remote(sha):   # the commit stands on a branch of the tracking remote: a landing pass or a salvage can still reach it. One bounded probe over the remote's own namespace, never every remote-tracking ref (#1958)
    if sha not in _on_remote:
        _on_remote[sha] = bool(_run(["git", "-C", str(co), "for-each-ref", "--contains", str(sha), "--count=1", "--format=%(refname)", f"refs/remotes/{remote()}"])[1].strip())
    return _on_remote[sha]
def remote_held():   # the checkout knows the tracking remote's branches at all: without one the pass cannot say a head reached no remote, so it keeps the old reading
    global _remote_held
    if _remote_held is None:
        _remote_held = bool(_run(["git", "-C", str(co), "for-each-ref", "--count=1", "--format=%(refname)", f"refs/remotes/{remote()}"])[1].strip())
        if not _remote_held: say(f"REMOTE-UNSEEN: the checkout holds no refs/remotes/{remote()}; the local-only rule is inert this pass")
    return _remote_held
def folder(r): return pathlib.Path(r) if r.startswith("/") else co / "runs" / r
def unpushed(f):   # #771: "<branch> <sha9> in <tree>" for each child tree of the run whose HEAD is in no origin/<land> and on no remote branch
    out = []
    for w in sorted((f / "ws").glob("*")) if (f / "ws").is_dir() else []:
        r = _run(["git", "-C", str(w), "rev-parse", "HEAD"])
        sha = r[1].strip() if r[0] == 0 else ""
        if not sha or in_land(sha): continue
        if _run(["git", "-C", str(w), "branch", "-r", "--contains", sha])[1].strip(): continue
        b = _run(["git", "-C", str(w), "branch", "--show-current"])[1].strip() or "detached"
        out.append(f"{b} {sha[:9]} in {w}")
    return out
def read(f):
    v = False; pidc = None; started_at = None; acc = set(); t = f / "record.jsonl"; ver = None; rs = []; landed = False; rid = f.name; rts = ""
    lts = ""                        # the stamp of the record's last event: rule T reads the last progress, never the start (#1191)
    vts = None; vkey = ""; nv = 0; ec = None   # the last verdict's time and its key (#692, #693): the rearm floor and rule O's read-once marker read them
    # The per-child evidence (#271): the children the run accepted, the issues each child's own accept line named, the
    # landing nodes that stand, and every node's own outcome. A run that asked for children landed through the child
    # that made the landing, so rule R reads each child's own fact instead of crediting the run's landing to every issue.
    kids = set(); kid_issue = {}; land_nodes = set(); outs = {}; kid_review = {}; kid_nc = {}; heads = []
    last_land = {}   # node -> the outcome and reasons of the LAST landing it made: its final landing state (#2015)
    # The spawn batch (#480): the engine answers a dispatch's requests in one batch, then starts the children. Any event after
    # the last spawn event is past that batch, so the run's choice of children is decided; spawn_ts is when it was made.
    spawned = decided = False; spawn_ts = 0.0
    # The calls open now (#1842): one `call_start` with no later outcome for its node, entry and item. A call writes no event
    # until it ends, so the last motion of a run inside one long call is that `call_start`, and the call's own deadline is the
    # bound it recorded. An invocation starts with no call open: a `run_start` or a `resume` clears what the last one left.
    open_calls = {}
    if not t.exists(): return None
    for l in t.read_text().splitlines():
        try: d = json.loads(l)
        except ValueError: d = {}
        ev = d.get("event")
        if d.get("ts") is not None: lts = d["ts"]   # every event is progress: the record moved when this stamp was written
        if ev in ("spawn_requested", "spawn_accepted", "spawn_rejected"):
            spawned = True; decided = False
            if isinstance(d.get("ts"), (int, float)): spawn_ts = max(spawn_ts, float(d["ts"]))
        elif spawned and ev:
            decided = True
        if ev in ("run_start", "resume"):
            rid = str(d.get("run_id") or rid)
            if ev == "run_start": rts = str(d.get("ts") or d.get("started_at") or rts)
            # A verdict is final only after the last start or resume (ADR-0105): a resumed run is live again until its
            # next verdict, so an earlier verdict no longer reads it as finished (ADR-0167).
            v = False; ver = None; rs = []; ec = None; vts = None; vkey = ""; open_calls = {}
        if ev == "verdict":   # the fold takes the last verdict event after the last start or resume (ADR-0105)
            v = True; ver = d.get("verdict"); rs = [str(x) for x in (d.get("reasons") or [])]; ec = d.get("end_class")
            vts = d.get("ts") if isinstance(d.get("ts"), (int, float)) else None; vkey = f"{vts if vts is not None else ''}:{nv}"; nv += 1
        if ev == "land":   # only the landing stage's own event counts; a router's `routed: continue` is not a landing
            eff = d.get("effect") or {}
            if isinstance(eff, dict) and isinstance(eff.get("review"), dict) and d.get("node") is not None:
                kid_review[str(d["node"])] = eff["review"]   # the run's own review of the child (#599): rule O reads its verdict (#621)
            # A landing that passed with review_nochange changed no file (#2245): it is the no-change end of this child, and
            # its own issue is marked for the owner instead of reaching a second empty wave. The cause needs the passing
            # landing and the no-change verdict together; the verdict compares case-blind, as rule O's review read does.
            if d.get("node") is not None:
                rv = eff.get("review") if isinstance(eff, dict) else None
                kid_nc[str(d["node"])] = (d.get("outcome") == "pass"
                                          and "review_nochange" in [str(x) for x in (d.get("reasons") or [])]
                                          and isinstance(rv, dict) and str(rv.get("verdict") or "").lower() == "no_change")
            # A landing that moved the target's tip by nothing carries no work: it is no delivery, so no card is claimed and
            # no marker stands on it (ADR-0145 rule 12, #464). A record written before the effect carried `before` keeps the
            # old reading, so an old run is judged as it was.
            moved = eff.get("before") is None or eff.get("before") != eff.get("head")
            if d.get("outcome") == "pass" and moved: heads.append((d.get("node"), eff.get("head"), eff.get("held_out") != "fail"))
            # A landing that failed is the reason its child's work never reached the run's target (#2015). The child's own
            # `outcome` may read pass: rule R names this failure, never that outcome word, for an issue that did not land.
            # Only the node's LAST landing counts: a failure a retry stood is no failure, and an aborted landing is no
            # failure either, so only `fail` is kept and any later landing replaces it.
            if d.get("node") is not None:
                last_land[str(d["node"])] = (d.get("outcome"), (d.get("reasons") or ["the landing failed"])[0])
        if ev == "outcome":   # the outcome the node itself recorded, child or run stage: the fact rule R reads per child (#271)
            open_calls.pop((d.get("node"), d.get("entry"), d.get("item")), None)   # the call closed (#1842)
            if d.get("node") is not None:
                outs[str(d["node"])] = (str(d.get("outcome") or ""), [str(x) for x in (d.get("reasons") or [])])
        if ev == "call_start":
            pidc = d.get("pid") or pidc; started_at = d.get("started_at") or started_at
            open_calls[(d.get("node"), d.get("entry"), d.get("item"))] = (d.get("started_at"), d.get("limit"))
        if ev == "spawn_accepted" or '"spawn_accepted"' in l:
            # The router writes the issue down without the hash ("Fix GitHub issue 11 in the repository"), and a child that
            # took several writes them as a list. A hashed-only read lost the run's own issues: rule I and free_reaped read
            # this set (#66).
            found = set()
            # Both dispatchers make every child carry `Refs #N` (autofrontier.sh, autobug.sh), whatever words the router picks around it:
            # a router that wrote "issue 548" matched neither form above, so a live child read as unheld and lost its marker (#551).
            # The line is JSON: an escaped newline ("\\nRefs #715") reads as the newline it encodes, so `\b` finds the
            # boundary and a bundled child's Refs lines map (#725), as child_issues.plain reads them.
            for m in re.finditer(r"GitHub issues? #?([0-9][0-9, ]*)|\bissue #(\d+)|\bRefs #(\d+)", l.replace("\\n", "\n").replace("\\t", "\t")):
                found.update(x for x in re.split(r"[^0-9]+", m.group(1) or m.group(2) or m.group(3) or "") if x)
            acc |= found
            if d.get("child") is not None:   # the issues one accept line names belong to the child that line accepted, never to the whole run (#271)
                kids.add(str(d["child"]))
                kid_issue.setdefault(str(d["child"]), set()).update(found)
    # A failed run's landing is delivered work only once it reached the landing branch (#691, ADR-0143): its own land commit on
    # its work branch, a head origin/<land> does not hold, delivered nothing, so the run reads as one that landed nothing. A
    # landing that records no head keeps the old reading. Any other verdict keeps its landing: a pass goes on through its wave.
    # A head on no remote branch delivers nothing whatever the verdict (#1958): no pass can land work only a local branch holds,
    # so the card returns to Todo and the dispatch marker goes, where before the card sat past Todo and the marker held it for good.
    for node, head, stands in heads:
        if ver == "failed" and head and not in_land(str(head)): continue
        if head and remote_held() and known(head) and not in_land(str(head)) and not on_remote(str(head)): continue
        landed = True
        # The node that landed: a dispatch child's own name (`dispatch.py` writes node=child), or the run's own stage.
        # A landing a child's own held-out command turned red was undone, so it is not a landing (subject.py).
        if stands and node is not None: land_nodes.add(str(node))
    kid_landed = {n for n in land_nodes if n in kids}   # the children whose own landing stands: the run's landing is theirs
    # node -> the reason of the child's final landing, only when that landing failed and the node is a child (#2015): the
    # run's own landing stage is no child's failure, and a failure a retry stood is no failure.
    kid_land_fail_reason = {n: r for n, (o, r) in last_land.items() if o == "fail" and n in kids}
    return {"verdict": v, "name": ver or "no verdict", "pid": pidc, "started_at": started_at, "acc": acc, "idle": now - t.stat().st_mtime,
            "failed": ver == "failed", "reasons": rs, "end_class": ec, "landed": landed, "run_id": rid, "run_ts": rts, "last_ts": lts, "vts": vts, "vkey": vkey,
            "kid_issue": kid_issue, "kid_review": kid_review, "kid_nc": kid_nc, "kid_landed": kid_landed, "kid_out": outs, "kid_land_fail_reason": kid_land_fail_reason, "decided": decided, "spawn_ts": spawn_ts,
            "open_calls": list(open_calls.values())}
def same_process(pidc, started_at):
    if pidc not in procs or not isinstance(started_at, (int, float)):
        return pidc in procs
    started = starts.get(pidc)
    return started is None or started <= started_at
def mark_killed(f, reason=None):
    """C: the zombie gets the killed verdict in its own record (squad.killed, the one writer), so the hub shows it ended. A record it cannot write stays as it is. `reason` names why the pass ended the run, and the verdict carries it (#1571)."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(x for x in (os.environ.get("SG_SQUAD"), os.environ.get("PYTHONPATH")) if x)}
    _run(["python3", "-m", "squad.killed", str(f)] + ([str(reason)] if reason else []), env=env)
TAKEOVER_SECONDS = float(os.environ.get("SG_TAKEOVER_SECONDS", "1800") or 1800)
GATE = re.compile(r"gate\.sh\b")   # a gate process (`gate.sh`, `fast-gate.sh`): a run inside one is doing work or waiting its turn, never stuck (#1191)
# A gate process older than this is itself overdue: every bound the loop gives a gate is shorter (ADR-0163), so
# it is no evidence that the run is working, and the takeover bound applies again (#1390).
GATE_STUCK_SECONDS = float(os.environ.get("SG_GATE_STUCK_SECONDS", "") or TAKEOVER_SECONDS)
def age_of(t):   # T: seconds since a record stamp, or None when the pass cannot read it (an epoch, or an ISO UTC stamp)
    try: return now - float(t)
    except (TypeError, ValueError): pass
    try: return now - calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError): return None
def progress_age(i):   # T: seconds since the run's own record last moved; a gate-queue wait records no progress, and rule T reads the gate (#1191)
    return age_of(i["last_ts"])
def run_age(i):   # T: the fallback when the record carries no readable event stamp: the age of its start
    return age_of(i["run_ts"])
def open_call_in_time(i):   # T: an open call still inside the deadline it recorded (#1842). The call ends itself when the
    # deadline fires, so the run is not stuck, however long its record has been still. A call that records no readable
    # deadline (a null limit, a stamp that is not a number) has no end the pass can prove, so it never moves the bound alone.
    for st, lim in i["open_calls"]:
        if not isinstance(st, (int, float)) or not isinstance(lim, (int, float)) or now < st + lim:
            return True
    return False
def _now(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
def inside(f): return [q for q, c in cwd.items() if c == str(f) or c.startswith(str(f) + "/")]
act = st / "active-runs.txt"; names = act.read_text().split() if act.exists() else []
repo0 = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
GONE = re.compile(r"\bHTTP(?:/[\d.]+)? 410\b|could not resolve to an issue|this issue was deleted", re.I)   # the 410 status or GitHub's stable message, never a bare 410: an issue number, a header or a delay may carry one (#466)
def gone_msg(s): return bool(GONE.search(s or ""))
_is = {}
# The issue facts file (#1744): $SG_STATE/issue-facts.json holds the five facts of every issue a pass read, and the time of the
# last sync. A pass asks GitHub once for the issues that changed since that sync (the door's paged list read, `since` is the
# issue's update time), so an issue the file holds and the list did not name is unchanged and is not read again. The pass
# read each issue of each run folder by itself on every pass: 155 reads and 96 s of a 181 s pass. A list that does not
# answer trusts nothing from the file, and the pass reads each issue itself, as it did. An entry older than FACTS_MAX_AGE
# is read again: a deleted issue never shows in a list of changes. A dry pass writes nothing.
FACTS = st / "issue-facts.json"; FACTS_MAX_AGE = 86400; FACTS_OVERLAP = 300   # the overlap covers a clock that differs from GitHub's
_facts = None   # None: not asked yet; False: the list did not answer this pass; else the file's content, synced
_facts_dirty = False
def _facts_iso(t): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
def _facts_sync():
    global _facts, _facts_dirty
    if _facts is not None: return
    _facts = False
    try: c = json.loads(FACTS.read_text())
    except (OSError, ValueError): c = None
    if not (isinstance(c, dict) and c.get("repo") == repo0 and isinstance(c.get("issues"), dict) and isinstance(c.get("synced"), str)):
        c = {"repo": repo0, "synced": "", "issues": {}}
    start = time.time()
    since = c["synced"] or _facts_iso(start)   # no sync yet: the file holds nothing, so nothing older can be stale
    r, out = _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_pages", f"repos/{repo0}/issues?state=all&since={since}&per_page=100"])
    try: items = json.loads(out) if r == 0 else None
    except ValueError: items = None
    if not isinstance(items, list): return
    for i in items:
        if not (isinstance(i, dict) and isinstance(i.get("number"), int) and i.get("state") in ("open", "closed")): continue
        labels = ",".join(l.get("name", "") for l in i.get("labels") or [] if isinstance(l, dict))
        c["issues"][str(i["number"])] = {"f": [i["state"], i.get("state_reason") or "", i.get("title") or "", labels, i.get("closed_at") or ""], "at": start}
    c["synced"] = _facts_iso(start - FACTS_OVERLAP); _facts = c; _facts_dirty = True
def _facts_save():   # atomic (ADR-0030): a pass that dies in the write leaves the last whole file
    if dry or not _facts or not _facts_dirty: return
    tmp = FACTS.with_name(FACTS.name + f".{os.getpid()}")
    try: tmp.write_text(json.dumps(_facts)); os.replace(tmp, FACTS)
    except OSError as e: say(f"issue facts not written: {e}")
import atexit; atexit.register(_facts_save)
def issue_read(x, fresh=False):   # one read answers every fact the passes ask of an issue: its state, its reason, its title, its labels and its close time (#1571). fresh: a read that decides a write asks GitHub itself, and never the pass's cache nor the facts file, which can hold a state GitHub already changed (#1999)
    global _facts_dirty
    if fresh or x not in _is:
        _facts_sync()
        e = None if fresh else (_facts["issues"].get(str(x)) if _facts else None)
        if isinstance(e, dict) and isinstance(e.get("f"), list) and len(e["f"]) == 5 and time.time() - float(e.get("at") or 0) < FACTS_MAX_AGE:
            _is[x] = list(e["f"])
        else:
            o = sh("gh", "api", f"repos/{repo0}/issues/{x}", "-q", '[.state, (.state_reason // ""), .title, ([.labels[].name] | join(",")), (.closed_at // "")] | @tsv').strip("\n").split("\t")
            _is[x] = (o + ["", "", "", "", ""])[:5]
            if _facts:
                if _is[x][0] in ("open", "closed"): _facts["issues"][str(x)] = {"f": _is[x], "at": time.time()}   # an answer about the issue; a refusal or GitHub's deleted message is never kept
                else: _facts["issues"].pop(str(x), None)
                _facts_dirty = True
    return _is[x]
def issue_is_gone(x):   # a missing issue is not a transient read failure. The pass's one cached read of the issue says it: `gh api`
    # writes GitHub's error body to stdout and ignores -q on a refusal, so a deleted issue reads as GitHub's stable message, no extra call
    return gone_msg("\t".join(issue_read(x)))
live = {}; livef = {}; keep = []; dropped = set(); held = set(); landed_n = set(); freed_markers = {}; missing = []   # held: issues a run still in active-runs may be working (a run in its launch window has no record yet); landed_n: issues a finished run landed; dropped: every run whose line this pass leaves out of the list (#2144)
def marker_mtime(p):   # #794: a marker can vanish between the glob, its test and its stat; a gone marker is no live marker: None
    try: return p.stat().st_mtime
    except FileNotFoundError: return None
def marker_text(p):   # #794: the text of a marker, or None when it vanished before the read
    try: return p.read_text().strip()
    except FileNotFoundError: return None
def req_issues(f): return issues_named((f / "request.txt").read_text()) if (f / "request.txt").exists() else set()
def keep_run(r, f, i):   # one site marks a run readable this pass: `keep` for the card pass and rule Q, `held` for the issues it
    # may be working, and `landed_n` for the issues it landed, so no two sites read the request and settle the landing twice.
    global held, landed_n
    ri = req_issues(f)
    if i["landed"]: landed_n |= ri
    held |= ri | i["acc"]; keep.append(r); return ri
def drop_line(r):   # #2144: every run loop rule that leaves a run's line out of active-runs names it here, so `dropped` is the
    # whole set of lines this pass drops and the rewrite can never keep one of them (#2138, ADR-0030). This is a pure leave-out
    # primitive: it edits no other list and reads no file, so a caller that must also read the run for its card calls keep_run
    # beside it (FINISHED-RUN does). The second keep source is `reaped`: a run whose open wave still lands it keeps its line
    # there, and the rewrite keeps a reaped line whatever `dropped` says (see the zombie rule below).
    # The record that feeds the card pass belongs to the caller's keep_run, never to this primitive. FINISHED-RUN and CLOSED-RUN
    # drop a line and still read the card, so they call keep_run beside the drop. The other callers drop with no record, and
    # that is correct: GONE-RUN's issues are deleted, the killed and never-started runs write reoffer markers for the card's
    # way back, and a missing folder has no record.
    dropped.add(r)
def owns(f, i, p):   # a dispatch marker names the request that wrote it (or its run): a run releases only a marker its own request wrote, never a newer run's (#539)
    try: c = p.read_text().strip()
    except OSError: return False
    if c and c in (f.name, (i or {}).get("run_id")): return True
    try: return (st / "req" / f"{c}.txt").read_bytes() == (f / "request.txt").read_bytes()
    except OSError: return False
def marker_live(m):   # #1586: a dispatch marker's named run or request is still live: an active-runs.txt entry whose folder
    # name answers to it, or whose request.txt is the one the marker names; or a run folder under the run root by that name.
    # A read that fails holds the marker (fail closed).
    for r in names:
        f = folder(r)
        if f.name == m: return True
        try: req = (st / "req" / f"{m}.txt").read_bytes()
        except FileNotFoundError: continue
        except OSError: return True
        try: mine = (f / "request.txt").read_bytes()
        except FileNotFoundError: continue
        except OSError: return True
        if mine == req: return True
    return (co / "runs" / m).exists()
pending = []   # markers of a reaped run, dropped after rule Q has read them
reaped = set()   # C: the active-runs lines of the zombies this pass reaped whose wave is still open; they stay in the file (#1854)
def free_reaped(f, i):
    """C: a reaped run frees its leases now, and its dispatch markers after rule Q. Rule Q reads a marker to find the card a
    dead run left In Progress and puts that card back (#62): a marker dropped here, before the read, left the card In
    Progress for ever with nothing to report, which is the drift rule Q exists to stop. Returns the marker paths."""
    marks = [p for p in (st / "auto" / f"disp-{n}" for n in req_issues(f) | i["acc"]) if owns(f, i, p)]
    holder = f.name; leases = []
    caps = pathlib.Path(os.environ.get("SQUAD_CAPS_DIR") or pathlib.Path.home() / ".cache" / "agent-squad" / "caps")
    for kind in ("runs", "children"):
        for p in (caps / kind).glob("*.json"):
            try: lease = json.loads(p.read_text())
            except (OSError, ValueError): continue
            if lease.get("holder") == holder or str(lease.get("holder", "")).startswith(holder + ":"): leases.append(p)
    if marks or leases: say(f"zombie run {f.name} releases {len(leases)} leases; its {sum(p.exists() for p in marks)} dispatch markers go after the card pass")
    if not dry:
        for p in marks:
            if p.exists() and p.name.startswith("disp-"):
                freed_markers.setdefault(p.name[5:], {})["disp"] = None
            p.unlink(missing_ok=True)
        for p in leases: p.unlink(missing_ok=True)
    return marks
def ccdefer_owner(fname):   # the run a `ccdefer-<run>-<child>` marker belongs to: the longest known run name that prefixes it. A child's
    # own name may carry dashes, and one run's name may only start with another's, so the marker is never read as another run's (#945).
    # active-runs.txt holds the run's folder, which launch.py writes absolute, and the marker carries the folder's name: compare that name.
    return next((m for m in sorted({pathlib.Path(x).name for x in names}, key=len, reverse=True) if fname.startswith(f"ccdefer-{m}-")), None)
def open_wave(*ids):   # the run's wave marker while it stands: its children still land through it, so its line is still read (#1854)
    return next((x for x in ids if x and (st / "auto" / f"wave-{x}").exists()), None)
def holds_lease(name):
    """True while a lease of the caps folders names the run or one of its children and its holder pid lives: such a run is no
    run gone from the leases, so its line is not dropped on that evidence (#1854). A lease whose pid is dead holds nothing:
    the next count reclaims it (`caps.Ceiling._live`)."""
    for kind in ("runs", "children"):
        for p in (pathlib.Path(os.environ.get("SQUAD_CAPS_DIR") or pathlib.Path.home() / ".cache" / "agent-squad" / "caps") / kind).glob("*.json"):
            try: lease = json.loads(p.read_text())
            except (OSError, ValueError): continue
            h = str(lease.get("holder", ""))
            if (h == name or h.startswith(name + ":")) and lease.get("pid") in procs: return True
    return False


for p in (st / "auto").glob("taken-*") if (st / "auto").is_dir() else []:   # T: a taken-over run's issues stay held until the hand fix drops the marker
    for l in (marker_text(p) or "").splitlines():
        if l.startswith("issues "): held |= set(l.split()[1:])
for r in names:
    f = folder(r)
    if not f.exists():   # a child worktree can take its run folder with it; no missing folder owns an issue
        missing.append((r, f)); drop_line(r)
        continue
    i = read(f)
    if i is None:   # J: no record.jsonl = the start was refused (e.g. vendor/skills drift); after 5 min stop its dashboard and drop it
        age = now - (f / "request.txt").stat().st_mtime if (f / "request.txt").exists() else 0
        if age > 300 and not [q for q in inside(f) if "dashboard_server" not in procs.get(q, (0, ""))[1]]:
            say(f"NEVER-STARTED run {f.name} (no record.jsonl after {int(age // 60)} min) dropped from active-runs")
            drop_line(r)
            free_never_started(f)   # the run frees its own issues; say() reports it in a dry run too
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of never-started run {f.name}")
            continue
        held |= req_issues(f); keep.append(r); continue
    n = f.name
    if i["verdict"]:
        # A (#2406): a run with its verdict is over, so a process left in its folder is a leftover. The kill comes before
        # every rule below that drops the line: no later pass reads a dropped run, so its leftovers would live for ever.
        if i["idle"] > 180:
            for q in inside(f):
                c = procs.get(q, (0, ""))[1]
                # Another run's own session is never a leftover of this one: childfix.sh starts a fix run with its target
                # inside the folder of the run it fixes, and rule A killed that engine while it still waited at the run cap
                # -- a state that names no pid, so no reap could prove it dead and its slot stayed counted (#1270). The engine of
                # that run is `squad start` (the .venv entry point), not a session, so ENGINE spares it too. This run's own
                # session is still a leftover, so the pid its record names stays killable, as rule 433 has it.
                if (SESSION.search(c) or ENGINE.search(c)) and q != i["pid"]: continue
                if "dashboard_server" not in c and not own_pass(q): kill(q, f"leftover of finished run {n}")
        # A run with its verdict is over, so a deferral marker it left names a wait no later pass can end: childcc writes
        # auto/ccdefer-<run>-<child> while a finished run's child still waits, and the wave reads it as a wait still owed
        # (run-20261030-184206-f63e6b waited 2h30, #945, #931). The marker of a run that ended is dropped here, and named,
        # before any rule below drops the line: no later pass reads a dropped run, so its markers would live for ever (#2419).
        mks = [p for p in (st / "auto").iterdir() if ccdefer_owner(p.name) == n] if (st / "auto").is_dir() else []
        if mks:
            say(f"CCDEFER-DROPPED {n}: {len(mks)} deferral marker(s) of a finished run dropped ({', '.join(sorted(p.name for p in mks))}); the run is over, so no wave waits on it")
            if not dry:
                for p in mks: p.unlink(missing_ok=True)
        nums = req_issues(f) | i["acc"]
        if nums and all(issue_is_gone(x) for x in nums):
            say(f"GONE-RUN {n}: every issue ({', '.join('#' + x for x in sorted(nums, key=int))}) is gone; dropped from active-runs")
            drop_line(r)
            continue
        # #2138: the run has its verdict and every issue it names is closed, so its line leaves active-runs. A line the loop
        # still counts as live work holds the child room the frontier needs, and six such lines left it reporting "no slot"
        # with nothing running. A run with an open issue keeps its line, as before: its wave or its card still read it.
        if nums and all(issue_read(x)[0] == "closed" for x in nums):
            say(f"CLOSED-RUN {n}: every issue ({', '.join('#' + x for x in sorted(nums, key=int))}) is closed; dropped from active-runs")
            drop_line(r); keep_run(r, f, i)   # a closed issue is not in the open list, so only the run's record moves its card
            continue
        # #2138: a run whose verdict is final is over. Its line stays only while a wave stands for it (that wave still lands
        # its children) or a live process or lease names it. A line the loop counts as live work holds the child room the
        # frontier needs. The five-minute floor lets the pass that wrote the verdict settle it.
        # #2144: the line leaves now, but this pass still reads the run for its card and its markers. The card pass and rule
        # Q read `keep`, so a run dropped before that read left its issue at no column, unfrozen and never requeued: the
        # pass adds it to `keep` and to `dropped`, and the rewritten list leaves it out.
        reap_ended = i["failed"] and any(str(x).startswith("killed/") for x in i["reasons"])   # the reap-ended block below names these runs (#1854)
        if i["idle"] > 300 and not reap_ended and not open_wave(n, i["run_id"]) \
                and not any(LIVE.search(procs.get(q, (0, ""))[1]) for q in inside(f) if q not in killed) and not holds_lease(n):
            say(f"FINISHED-RUN {n}: its verdict is final, no wave stands for it, no process or lease names it; dropped from active-runs")
            drop_line(r); keep_run(r, f, i)   # the line leaves this pass (#2144), and the card pass and rule Q still read the run's record (#2138)
            continue
        if i["idle"] > 3600:
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of finished run {n}")
        # #1854: the health pass's reap ends a run whose engine died and cannot drop its line, since rules O and R read the
        # run from the listing on the pass after the reap. This pass has read it, from `names`, and the reoffer marker
        # carries its issues to the card pass, as a rule-C zombie's do. So a run the reap ended (a killed verdict), with no
        # process and no lease naming it any more, is gone from the leases: its line leaves here, and the line says so. An
        # open wave holds it (its children still land through it) and so does a live lease (the run is not gone from them).
        if reap_ended and not any(LIVE.search(procs.get(q, (0, ""))[1]) for q in inside(f)) and not open_wave(n, i["run_id"]) \
                and not holds_lease(n):
            say(f"killed run {n} (its process and its leases are gone) dropped from active-runs")
            drop_line(r)
            if not dry:
                for x in req_issues(f) | i["acc"]: (st / "auto" / f"reoffer-{x}").write_text(n + "\n")
            continue
        keep_run(r, f, i); continue
    # A live run is one whose own process is inside its folder: its engine (`squad start`), a gate, or a session it started. A
    # helper the session left behind -- uv, caffeinate, a stray shell -- has a cwd there too, and reading any process as the run
    # kept a dead run "alive" for ever, its card in Doing and its active-runs line held (#745). The dashboard server the loop
    # starts beside a run is no run process: counting it kept an idle pid-less run alive, so rule C never fired (#1270).
    hips = inside(f)
    alive = any(LIVE.search(procs.get(q, (0, ""))[1]) for q in hips)
    # A gate process alive inside the run folder is the child's own entry gate waiting on the machine-wide gate lock, or
    # holding it: either way the run is working or waiting its turn, so the bound does not apply (#1191, ADR-0166).
    # A gate that has outlived every bound the loop gives a gate is itself overdue, and it exempts nothing: three dead runs
    # sat behind such a process for hours and no rule named them (#1390).
    gate_wait = any(GATE.search(procs.get(q, (0, ""))[1]) and now - starts.get(q, now) < GATE_STUCK_SECONDS for q in hips)
    age = progress_age(i)
    if age is None: age = run_age(i)
    # T: past the bound with no progress the run is stuck; it is ended and handed over, never requeued (ADR-0166). A run
    # inside one long call shows no progress either, but its call's own deadline is the bound that ends it: the pass leaves
    # it while that deadline has time, so the call reaches its cap (#1842).
    if age is not None and age > TAKEOVER_SECONDS and not gate_wait and not open_call_in_time(i):
        nums = sorted(req_issues(f) | i["acc"], key=int)
        # #1662: the work the run left uncommitted is saved before any teardown, one WIP line per tree. A tree that could not be
        # saved holds the run for this pass: nothing is killed, and the next pass tries the snapshot again.
        saved, saved_ok = wip_snapshot(f, i["run_id"], dry=dry)
        for line in saved: say(line)
        if not saved_ok:
            say(f"TAKEN-OVER HELD run {n}: a tree could not be saved (WIP failed), so the run is not ended this pass")
            held |= set(nums); continue
        msg = f"TAKEN-OVER run {n} ({int(age // 60)} min without progress) ended by the takeover bound; its issues stay claimed for a hand fix"
        say(msg)
        if not dry:
            open(st / "alerts.log", "a").write(f"{_now()} {msg}\n")
            mark_killed(f, "killed/taken-over")
            (st / "auto").mkdir(exist_ok=True)
            (st / "auto" / f"taken-{n}").write_text(f"run {i['run_id']}\nissues {' '.join(nums)}\n")
        held |= set(nums)
        for q in hips: kill(q, f"taken-over run {n}")
        for q, (_, c) in procs.items():
            if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of taken-over run {n}")
        continue
    if not alive and i["idle"] > 1200 and not (i["pid"] and same_process(i["pid"], i["started_at"])):
        # A reaped run leaves active-runs.txt on this same pass, so the listing and the leases agree in one sample (#1854):
        # this pass frees the dead run's leases, and a run no lease counts is no live work for the janitor or the hub that
        # read the list. Its line stays only while one of its waves is open: childcc enters a run's children from the
        # listing until the wave lands, the reason wtclean.sh rule A holds a finished run's line while wave-<run> stands.
        # wtclean.sh rule A drops the line of a run that ended by its own verdict; this pass drops the one whose process is
        # gone, so the line never outlives its run by the hour rule A waits.
        wv = open_wave(n, i["run_id"])
        say(f"zombie run {n} (no verdict, idle {int(i['idle'] // 60)} min, no process) gets the killed verdict; "
            + (f"its wave {wv} is open, so its active-runs line stays until the wave lands" if wv else "dropped from active-runs"))
        if not dry:
            mark_killed(f)
            for x in req_issues(f) | i["acc"]: (st / "auto" / f"reoffer-{x}").write_text(n + "\n")   # the card's way back, read by the board pass (#1034)
        pending += free_reaped(f, i)
        for q in hips:   # the helpers the dead session left behind are reaped with it, never kept by the next pass
            if own_pass(q) or SESSION.search(procs.get(q, (0, ""))[1]): continue
            kill(q, f"leftover of zombie run {n}")
        for q, (_, c) in procs.items():
            if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of zombie run {n}")
        if wv: reaped.add(r)   # an open wave still lands this run: the line stays until the wave marker goes. `reaped` is the
        #                        second keep source beside `keep`: the rewrite keeps a reaped line without asking `dropped`,
        #                        so this branch never calls drop_line -- its run is not dropped this pass.
        else: drop_line(r)     # with no open wave the line leaves the list here
        continue   # this pass's card rules still read the run's markers
    held |= req_issues(f) | i["acc"]; keep.append(r); live[n] = i; livef[n] = f
lines = [r for r in names if r in reaped or (r in keep and r not in dropped)]
# #1862: the list is rewritten through its one writer protocol, under the lock the launcher's append takes. It re-reads the
# list, keeps every line this pass's read did not name (a run a launch started while the pass ran), writes a temporary file
# beside the list and renames it into place (ADR-0030). A rewrite with nothing to change writes nothing.
if not dry: _active_runs_module().rewrite(act, names, lines)
# V: a wave marker `auto/wave-<run>` stands for a run's landing pass, and only a line of active-runs.txt makes the loop land
# that wave (watch.sh reads the run from the listing): a run that left the listing can never land it again, so its marker stays
# for ever (1325 of them on 2026-11-17, #2140). The pass drops the wave markers of a run no line names and no live process
# works, and names each one; the wave of a live run, or of a folder a live process works, stays.
if (st / "auto").is_dir():
    live_names = set()
    for r in lines:
        lf = folder(r); live_names.add(lf.name)
        li = live.get(lf.name) or read(lf)
        if li: live_names.add(li["run_id"])
    for wp in sorted((st / "auto").glob("wave-*")):
        wrun = next((wp.name[len(k):] for k in ("wave-start-", "wave-pause-", "wave-") if wp.name.startswith(k)), "")
        if not wrun or wrun in live_names: continue
        if any(LIVE.search(procs.get(q, (0, ""))[1]) for q in inside(co / "runs" / wrun)): continue
        say(f"WAVE-DROPPED {wp.name}: its run is not in active-runs and no process works its folder, so the wave can no longer land")
        if not dry: wp.unlink(missing_ok=True)
    # V2 (#2185): a landing marker `auto/wland-<run>` answers whether a run already landed, and `wave.sh` reads it only for a
    # run that still waits or lands. A run out of the listing lands no more, so the file stays for ever: one per landed run
    # (803 of them on 2026-11-17). The pass drops the landing markers of a run no line names, no process works and no queue
    # waits for, once they pass the grace window; a live run's marker, and a young one, stay.
    cutoff = now - float(os.environ.get("SG_MARKER_GRACE_SECONDS", 6 * 3600))
    for wp in sorted((st / "auto").glob("wland-*")):
        wrun = wp.name[len("wland-"):].split(".")[0]
        if wrun.startswith("tries-"): wrun = wrun[len("tries-"):]
        if not wrun.startswith("run-") or wrun in live_names: continue
        if (st / "auto" / f"wland-{wrun}.queued").exists(): continue
        if any(LIVE.search(procs.get(q, (0, ""))[1]) for q in inside(co / "runs" / wrun)): continue
        mt = marker_mtime(wp)
        if mt is None or mt > cutoff: continue
        say(f"WLAND-DROPPED {wp.name}: its run is not in active-runs, no process works its folder and no queue waits for it")
        if not dry: wp.unlink(missing_ok=True)
# S: the counted ceiling beside the per-run reap. B, J and C each kill the dashboard of one run the pass knows about, so when any of
# them stops working for any other reason -- a run that left active-runs.txt, a record the pass cannot read -- nothing bounds the
# count and the dashboards of finished runs accumulate (the freeze of 2026-10-13 left 41 alive). This reads the process table the
# pass already holds (one ps and one lsof through _run, ADR-0051), counts this loop's own dashboards whatever run they serve, and
# kills the oldest no live run owns, down to the ceiling. It does not read active-runs.txt to find them: that is what makes it
# independent of the per-run reap.
DASH_MAX = int(os.environ.get("JANITOR_DASHBOARD_MAX", "8") or 0)   # 0 turns the ceiling off, as JANITOR_CALL_TIMEOUT=0 does for a hand run
def ours(p):   # a dashboard of this loop serves a run folder under this checkout's runs, or a run or child worktree's runs (#580, #585); one another project started is not ours
    try: q = pathlib.Path(p).resolve()
    except OSError: return False
    return q.parent.name == "runs" and (q.parent.parent == co or q.parent.parent.parent in (co / "worktrees" / "run", co / "worktrees" / "child"))
def live_run(f):   # a run still going owns its dashboard; a run with no record yet is in its launch window and owns it too
    i = read(f)
    if i is not None: return not i["verdict"]
    req = f / "request.txt"
    return req.exists() and now - req.stat().st_mtime <= 300
if DASH_MAX:
    dash = {}
    for q, (_, c) in procs.items():
        dm = re.search(r"squad\.dashboard_server\s+(\S+)", c)
        if dm and ours(dm.group(1)): dash[q] = dm.group(1)
    orphans = sorted((q for q, d in dash.items() if not live_run(folder(d))), key=lambda q: (starts.get(q) or now, q))
    victims = orphans[:max(0, len(dash) - DASH_MAX)]
    if os.environ.get("JANITOR_DIAG"):
        cand = [c for _, c in procs.values() if "dashboard_server" in c]
        say(f"DASH-DIAG: ps read {len(procs)} processes, {len(dash)} dashboards of this loop, {len(orphans)} without a live run, ceiling {DASH_MAX}; {len(cand)} lines name a dashboard manager: {cand[:1]}")
    if victims:
        say(f"DASHBOARD-CEILING: {len(dash)} dashboards of this loop over the ceiling of {DASH_MAX}; killing {len(victims)} no live run owns (oldest first)")
        for q in victims: kill(q, f"dashboard over the ceiling ({len(dash)} > {DASH_MAX})")
byissue = {}
for n, i in live.items():
    for x in i["acc"]: byissue.setdefault(x, []).append(n)
for x, rs in byissue.items():
    mk = st / "auto" / f"janitor-dup-{x}-{'-'.join(sorted(r[-6:] for r in rs))}"
    if len(rs) > 1 and not mk.exists():
        # #865: one issue, one live run (ADR-0150). The older run keeps it (a run id starts with its start time, so the sort is the
        # start order); every newer run that accepted it is ended with its process tree, and the pass says which. A dry run names the
        # same kills and ends none.
        keep_r, *dups = sorted(rs)
        say(f"DUP issue #{x} accepted by live runs {', '.join(rs)}; keeping {keep_r}, killing {', '.join(dups)}")
        for r in dups:
            why = f"duplicate of run {keep_r} for issue #{x}"
            if live[r]["pid"] and live[r]["pid"] in procs:
                kill(live[r]["pid"], why)
            for q in inside(livef[r]):
                if not own_pass(q):
                    kill(q, why)
        if not dry: mk.touch()
# An issue is held from its dispatch until its delivery ends (#539): a run with a verdict whose wave marker auto/wave-<run>
# stands (wave.sh clears it when the wave lands) still delivers its issues, and so does a child that entered such a wave (the
# entered fact of its ledger, ADR-0149). startability.sh's sg_live_issues reads the same facts for both dispatchers. No rule
# releases an issue it holds.
def ledger():   # the issue ledger module beside this script (ADR-0149); -I keeps the script's directory off sys.path
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import issue_ledger
    return issue_ledger
def rearmed_after(n, i, rec=None):   # the owner's rearm is newer than the run's verdict (#1562, ADR-0029): the one fact that
    # lifts a needs-human fold from the owner freeze. The verdict time alone decides it: `rearmed.runs` names every run the ledger
    # listed, including a verdict runreview.sh wrote while the run was still live, so a name there never proves the run ended
    # before the rearm. A ledger that cannot be read, or one that is held, is no floor, so every caller keeps its own hold
    # (ADR-0149 rule 4, fail closed). `rec` is a record the caller already read, so the pass reads the ledger once.
    try:
        if rec is None: rec = ledger().read(f"{repo0}#{n}")
        if rec.get("state") == "held": return False
        rm = rec.get("rearmed") or {}
        return i["vts"] is not None and isinstance(rm.get("ts"), (int, float)) and i["vts"] < rm["ts"]
    except (OSError, ImportError, ValueError, KeyError): return False
def not_planned(reason):   # the close-reason vocabulary board_columns owns (#921): one copy, so the passes never drift
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from board_columns import not_planned as verdict
    return verdict(reason)
_entered = None
def entered():   # {(run, issue)} of each child of repo0 that entered a wave, read from the ledgers once a pass
    global _entered
    if _entered is None:
        try: _entered = set(ledger().entered(repo0))
        except (OSError, ImportError, ValueError) as e: say(f"LEDGER-UNREAD: the entered facts could not be read ({e})"); _entered = set()
    return _entered
def dispatched(n):   # the issue's ledger records a run that dispatched it (the entered fact, ADR-0149): the proof the
    # loop owns the card, never a wave label (#1987). A ledger that cannot be read records nothing, so the card stays.
    return any(x == str(n) for _, x in entered())
_landed = None
def landings():   # {issue: {(ref, kind)}} of the landing passes' facts (ADR-0149), read from the ledgers once a pass. A
    # landing is delivered work only when a landing pass wrote it (#691, #695): rule R reads this, never a run's land heads.
    global _landed
    if _landed is None:
        try: _landed = ledger().landings(repo0)
        except (OSError, ImportError, ValueError) as e: say(f"LEDGER-UNREAD: the landed facts could not be read ({e})"); _landed = {}
    return _landed
def base_landed(n, names):   # the issue's work is in the base: a landing of this run, or a branch a branchland pass landed
    return any(ref in names or kind == "branch" for ref, kind in landings().get(str(n), ()))
def run_landed(n, names):    # this run's own landing named the issue
    return any(ref in names for ref, kind in landings().get(str(n), ()) if kind == "run")
delivering = set()
for run_, n_ in entered():
    if (st / "auto" / f"wave-{run_}").exists(): delivering.add(n_)
for r in names:
    f = folder(r); i = read(f) if f.exists() else None
    if i and i["verdict"] and any((st / "auto" / f"wave-{x}").exists() for x in {f.name, i["run_id"]} if x): delivering |= req_issues(f) | i["acc"]
def holding(n): return n in byissue or n in delivering   # a live run works it, or a run still lands its wave
base = os.environ.get("SG_BASE", "main")   # the branch a merge must land on to count as delivery (#127)
def issue_info(x, fresh=False):
    return issue_read(x, fresh)[:3]
def issue_labels(x): return set(l for l in issue_read(x)[3].split(",") if l)
def frontier_skip():   # the labels the frontier refuses an issue for: the list autofrontier.sh reads (sg_frontier_skip, #2352)
    r = subprocess.run(["bash", "-c", '. "$0"; sg_frontier_skip', str(pathlib.Path(__file__).parent / "skiplabels.sh")], capture_output=True, text=True)
    return {x for x in r.stdout.strip().split(",") if x}
_open = None
def close_spec(n, why):   # the comment, then the close as completed, both through the door's write (gh_send)
    door = ["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_send"]
    if _run(door + [f"repos/{repo0}/issues/{n}/comments", "-f", f"body={why}"])[0] == 0:
        _run(door + ["-X", "PATCH", f"repos/{repo0}/issues/{n}", "-f", "state=closed", "-f", "state_reason=completed"])
_outside = None
def wave_outside():   # issue -> run: each `Refs #N` a landing named outside its run's dispatch set (wave.sh writes wave-outside.tsv, #775)
    global _outside
    if _outside is None:
        _outside = {}
        try:
            for l in (st / "wave-outside.tsv").read_text().splitlines():
                f = l.split("\t")
                if len(f) >= 3 and f[2].isdigit(): _outside[f[2]] = f[1]
        except OSError: pass
    return _outside
_subs = {}   # issue -> REST's sub_issues_summary, from the same one read as the labels
_kids = {}   # spec -> its sub-issues' numbers (the native relation), from the same one read as the labels
_items = []  # the open issues' list entries, from the same one read (the spec rule reads their parent and creation)
def open_issues():   # every open issue of the repository, ONE read per pass through the door's aggregate route (#92): (answered, {number: labels}). answered is False when the list did not answer (a refusal, a deadline miss, no door beside this module), and then no card is added on a guess. No call per issue: the one list carries every issue's number and labels.
    global _open, _items
    if _open is None:
        r, out = _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_aggregate", f"repos/{repo0}/issues?state=open"])   # the read goes through ghcall.sh (gh_aggregate, the throttle), never a call site of this module's (ADR-0277)
        try: items = json.loads(out) if r == 0 else None
        except ValueError: items = None
        m = {}
        for i in items if isinstance(items, list) else []:
            if isinstance(i, dict) and isinstance(i.get("number"), int):
                m[str(i["number"])] = set(l.get("name") for l in i.get("labels") or [] if isinstance(l, dict))
                if isinstance(i.get("sub_issues_summary"), dict): _subs[str(i["number"])] = i["sub_issues_summary"]
                if isinstance(i.get("sub_issues"), list): _kids[str(i["number"])] = [str(k) for k in i["sub_issues"] if isinstance(k, int)]
        _items = items if isinstance(items, list) else []
        _open = (isinstance(items, list), m)
    return _open
def issue_closed(n):   # the pass's one open-issue list answers for the whole repository (#92, #1566): an issue absent from
    # it is closed. A list that did not answer is no answer, so the caller keeps its line and its rule.
    answered, oans = open_issues()
    return answered and str(n) not in oans
def stamp_epoch(v):   # an absolute epoch from a record stamp (a number) or an ISO UTC stamp; None when the pass cannot read it
    try: return float(v)
    except (TypeError, ValueError): pass
    try: return float(calendar.timegm(time.strptime(v, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError): return None
def run_branch_names(f, x):   # #1571: a branch of this run names the issue: a child tree's branch carries the issue number
    # (`fix/<n>-...`, `gui/<n>-...`), or its commits carry the `Refs #<n>` line the engine writes. That branch is work still
    # to land, so its issue is not a stop signal.
    for w in sorted((f / "ws").glob("*")) if f is not None and (f / "ws").is_dir() else []:
        b = _run(["git", "-C", str(w), "branch", "--show-current"])
        name = b[1].strip() if b[0] == 0 else ""
        m = re.match(r"(?:fix|gui)/(\d+)(?:\D|$)", name)
        if m and m.group(1) == str(x): return True
        log = _run(["git", "-C", str(w), "log", "--format=%B", "-n", "200"])
        if log[0] == 0 and re.search(rf"\bRefs #{x}\b", log[1]): return True
    return False
def closed_undelivered(run_name, i, infos, f):   # #1571: a run whose work an issue's close already answered keeps
    # burning nodes and the machine-wide gate lock, so rule I ends it. Every accepted issue closed after the run started and
    # before the run's last progress, with no landing of this run for it and no branch of this run naming it, is delivered
    # work: nothing of the run is left to land. An unreadable stamp or close time, a close after the last progress (the run
    # may be finishing a land) or a landing decides nothing and keeps the run.
    start, prog = stamp_epoch(i["run_ts"]), stamp_epoch(i["last_ts"])
    if start is None or prog is None: return False
    for x, v in infos.items():
        closed = stamp_epoch(v[4])
        if closed is None or closed < start or closed > prog: return False
        if any(x in i["kid_issue"].get(c, set()) for c in i["kid_landed"]) or run_landed(x, {run_name, i["run_id"]}): return False
        if run_branch_names(f, x): return False
    return True
for n, i in live.items():
    mk = st / "auto" / f"janitor-closedrun-{n[-6:]}"
    if mk.exists() or not i["acc"] or i["idle"] > 3600: continue
    infos = {x: issue_read(x) for x in sorted(i["acc"])}   # the close time rides the pass's one read (#1571)
    if any(v[0] != "closed" for v in infos.values()): continue
    dup = all(not_planned(v[1]) or v[2].startswith("[duplicate") for v in infos.values())
    nums = ", ".join("#" + x for x in infos)
    undelivered = False if dup else closed_undelivered(n, i, infos, livef.get(n))   # a duplicate already decides the stop
    if dup or undelivered:
        say(f"RUN-ON-CLOSED-ISSUES {n}: every issue ({nums}) is closed and this run holds no landing for it; stopping the run"
            if undelivered else f"RUN-ON-CLOSED-ISSUES {n}: every issue ({nums}) is closed as a duplicate / not planned; stopping the run")
        if not dry:
            mk.touch(); f = livef[n]   # the folder active-runs.txt named, a run worktree's too (#585)
            if undelivered: mark_killed(f, "closed_without_landing:" + ",".join("#" + x for x in infos))   # the run ends with a verdict that says why
            for q in inside(f):
                if "dashboard_server" not in procs.get(q, (0, ""))[1]:
                    kill(q, f"run {n} works only closed issues with no landing of its own" if undelivered else f"run {n} works only closed duplicate issues")
            if i["pid"] and same_process(i["pid"], i["started_at"]): kill(i["pid"], f"last call of run {n}")
    else:
        say(f"RUN-ON-CLOSED-ISSUES {n}: every issue ({nums}) is closed but not as a duplicate; not stopped (a land or fix run may be finishing)")
        if not dry: mk.touch()
owner = os.environ.get("SG_OWNER"); proj = os.environ.get("SG_PROJECT"); bproj = os.environ.get("SG_BUG_PROJECT")
_boards = {}
class _Done:   # the shape the seam's runner answers: the binding reads returncode and stdout, and stderr is never kept (no pipe, see _run)
    def __init__(self, rc, out): self.returncode, self.stdout, self.stderr = rc, out, ""
def _seam_run(a, input=None, capture_output=True, text=True, timeout=None):   # the seam's calls go through this module's one runner and its deadline; the binding never writes here, so no input is sent
    return _Done(*_run(list(a), timeout=timeout))
_seams = {}
_filled = set()   # (project, issue) the fallback read named this pass: a missing issue is asked once, never once per rule (#1463)
def board_seam(p):   # the one seam instance a pass asks for project p, so its reads and caches serve every rule
    if p not in _seams:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam sits beside this script; -I keeps the script's directory off sys.path
        from issue_state import board_state
        _seams[p] = board_state(owner=owner, project=p, repo=repo0, state_dir=st, run=_seam_run)
    return _seams[p]
def board_read(p):   # one items read per pass for every card's Status and holder (#236, #327): (answered, {issue: Status}, {issue: holder} or None); answered is False when GitHub did not answer, and nothing is moved then
    if p not in _boards:
        seam = board_seam(p)
        try: _boards[p] = (bool(seam.binding.status_field()["id"]), *seam.board(fresh=True))   # a card this pass may move is never answered from the window's snapshot (#1796)
        except Exception as e:   # any failure of the read is an unanswered board, never a dead pass: nothing is moved on it
            _boards[p] = (False, {}, None)
            say(f"BOARD-READ-FAILED project {p}: {type(e).__name__}: {str(e)[:160]}")
    return _boards[p]
def cards(p): return board_read(p)[:2]
def fill_missing(p, known):   # an issue the pass's REST read omitted is asked once of the GraphQL projectItems read (#1463), and the card it finds rides the pass's map: a card the route never carried stops being named absent. One call per omitted issue per pass, never one per item; the id it finds also seeds the item map the writer reads, so the write path spends no second read on the omission
    if not cards(p)[0]: return
    m = cards(p)[1]
    for n in sorted({str(x) for x in known} - set(m), key=int):
        if (str(p), n) in _filled: continue
        _filled.add((str(p), n))
        fb = board_seam(p).read_fallback(n)
        if not fb: continue   # both reads name no card: the omission stands, and the card is absent
        m[n] = fb["state"]
        if fb.get("id"):
            b = board_seam(p).binding
            cached = b.items_map
            if cached is None:
                try: cached = json.loads((st / b._items_file).read_text())
                except (OSError, ValueError): cached = {}
            if not cached.get(n):
                b.items_map = {**cached, n: fb["id"]}; b._save_items()   # the writer's map carries the found id, never a second read for it
def holders(p):   # the pass's holder map, from board_read; None when the read did not answer. The writer updates it in place
    return board_read(p)[2]
def card_status(p, n):   # the card's Status on project p, "" when unreadable or absent (childcc.py reads it the same way)
    if not p: return ""
    return cards(p)[1].get(str(n), "")
_wb = None; _wv = {}; _tips = {}
def work_branches():   # {issue: [(branch, standing)]} for every remote work branch `fix|gui/<n>-...`, read once a pass (a branch read, never a pull request). standing is the compare of the branch tip with the landing branch: `ahead` or `diverged` (commits dev lacks), `behind` or `identical` (the tip is in dev). The same read keeps each wave branch `wave/<run>` in _wv (run -> (tip, standing)) and each work branch's tip in _tips: the board facts (board_facts.py) read them, and no new route is called
    global _wb
    if _wb is None:
        _wb = {}
        land = os.environ.get("SG_LAND_BRANCH") or "dev"
        lines = sh("gh", "api", f"repos/{repo0}/branches?per_page=100", "--paginate", "--jq", r'.[]|"\(.name) \(.commit.sha)"').splitlines()
        # A standing is a function of the landing tip and the branch tip: kept by both in the state dir, a compare is read
        # only when one moved. One compare per branch on every pass spent the REST quota (#525).
        ltip = next((l.split()[1] for l in lines if len(l.split()) == 2 and l.split()[0] == land), "")
        kept = st / "compare-standing.json"
        try: memo = json.loads(kept.read_text()) if ltip else {}
        except (OSError, ValueError): memo = {}
        memo = memo.get(ltip, {}) if isinstance(memo, dict) else {}
        fresh = {}
        for l in lines:
            m = re.match(r"(?:fix|gui)/(\d+)(?:\D\S*)? (\S+)$", l); w = re.match(r"wave/(\S+) (\S+)$", l)
            if not (m or w): continue
            sha = (m or w).group(2); standing = memo.get(sha) or sh("gh", "api", f"repos/{repo0}/compare/{land}...{sha}", "-q", ".status").strip()
            if standing: fresh[sha] = standing
            if m: _wb.setdefault(m.group(1), []).append((l.split()[0], standing)); _tips[l.split()[0]] = sha
            else: _wv[w.group(1)] = (sha, standing)
        if ltip and not dry:
            tmp = kept.with_suffix(f".{os.getpid()}.tmp"); tmp.write_text(json.dumps({ltip: fresh})); os.replace(tmp, kept)
    return _wb
def review_of(branch, tip):   # the state of the review of a branch tip, from the verdict branchreview.sh keeps in the state dir: none, approved or waiting
    import board_facts
    key = st / ("review-" + branch.replace("/", "_"))
    def text(ext):
        try: return key.with_suffix(key.suffix + ext).read_text()
        except OSError: return None
    return board_facts.review_state(text(".md"), text(".sha"), tip)
def review_waits(n):   # a work branch of the issue is ahead of dev and the review of its tip is read and does not approve it
    work_branches()
    return any(g in ("ahead", "diverged") and review_of(b, _tips.get(b, "")) == "waiting" for b, g in work_branches().get(str(n), []))
_devci = None
def dev_ci_running():   # a check on the landing branch tip has not ended: one read through the door, once a pass
    global _devci
    if _devci is None:
        ok, out = door_read(f"repos/{repo0}/commits/{os.environ.get('SG_LAND_BRANCH') or 'dev'}/check-runs", '[.check_runs[]|select(.status!="completed")]|length')
        _devci = ok and out not in ("", "0")
    return _devci
_TIP_LINE = re.compile(r"^STACK TIP: ([0-9a-fA-F]{7,64}) \(#(\d+), a blocker in (?:Batching|Landing)\)[ \t]*$", re.M)   # Landing: a request written before #2315
import board_columns
def work_landed(blk):   # True, False, or None when no fact answers: one work branch of the blocker stands in the landing branch
    """The blocker's work is in the landing branch, from the one fact that proves it: the remote ancestry of the branch tip.
    A compare that is unread, and a branch that is not listed, hold the run: no evidence is never a stop (#2260)."""
    wb = work_branches().get(str(blk), [])
    if not wb: return None
    co = os.environ.get("SG_CHECKOUT") or ""; base = os.environ.get("SG_BASE") or ""
    seen = []
    for b, g in wb:
        if g in ("ahead", "diverged"): seen.append(False); continue
        tip = _tips.get(b, "")
        if not g or not tip or not co or not base: seen.append(None); continue
        rc = subprocess.run(["/usr/bin/git", "-c", f"safe.directory={co}", "-C", co, "merge-base", "--is-ancestor", tip,
                             f"refs/remotes/origin/{base}"], capture_output=True).returncode
        seen.append(rc == 0 if rc in (0, 1) else None)
    if any(v is True for v in seen): return True
    if seen and all(v is False for v in seen): return False
    return None
# W (#2260) STACK-BASE-LEFT-LANDING: a run whose child started on a blocker's approved tip. A blocker that leaves Batching
# without landing (its review asked for changes, an owner rearm, a failed gate) is no base: the child's commits sit on a
# branch the base may never hold, and the landing would carry them into $SG_BASE with none of the blocker's approvals. The
# run is stopped, one killed verdict says why, its dispatch markers are dropped and its issues get the card's way back, so
# the frontier builds them again once the blocker is startable. A blocker whose card still reads Batching or Testing, and one
# whose branch tip already stands in the landing branch, keep the run: its work landed, and only the card lags.
for n, i in live.items():
    f = livef.get(n)
    if f is None: continue
    mk = st / "auto" / f"janitor-stackleft-{n[-6:]}"
    if mk.exists(): continue
    try: req = (f / "request.txt").read_text()
    except OSError: continue
    m = _TIP_LINE.search(req)
    if not m: continue   # a child cut from the base, or one stacked by a file conflict: rule W owns only the Batching stack
    blk = m.group(2)
    ok, cols, _h = board_read(proj)
    if not ok: continue   # a board that did not answer moves nothing and stops nothing
    col = cols.get(str(blk), "")
    if not col or board_columns.from_batching(col): continue   # the one column set, shared with stack.sh (#2260)
    if work_landed(blk) is not False: continue   # landed, or no readable fact: a run is never stopped on what nobody read
    say(f"STACK-BASE-LEFT-LANDING {n}: blocker #{blk} sits in {col} without a landing, so the branch of {n} is stacked on "
        + f"commits the base may never hold; stopping the run and freeing its issues")
    if dry: continue
    mk.touch(); mark_killed(f, f"stack_base_left_landing:#{blk}")
    freed = req_issues(f) | i["acc"]
    for x in sorted(freed):
        (st / "auto" / f"reoffer-{x}").write_text(n + "\n")   # the card's way back, read by the board pass (#1034)
        for p in (st / "auto").glob(f"disp-{x}"):
            if owns(f, i, p): p.unlink(missing_ok=True)
    for q in inside(f):
        if "dashboard_server" not in procs.get(q, (0, ""))[1]: kill(q, f"run {n} is stacked on blocker #{blk}, which left Batching")
    if i["pid"] and same_process(i["pid"], i["started_at"]): kill(i["pid"], f"last call of run {n}")
def wave_facts(n, runs):   # the board facts of issue n whose child entered the wave of one of `runs` (the folder name or the record's run id)
    import board_facts
    work_branches()
    run = next((r for r in runs if r in _wv), None)
    tip, standing = _wv.get(run, ("", ""))
    approved = bool(run) and review_of("wave/" + run, tip) == "approved"
    return board_facts.issue_facts(entered=bool(run), standing=standing, approved=approved, dev_running=bool(run) and standing in board_facts.IN_DEV and dev_ci_running(), review_waits=False,
                                   gating=bool(run) and standing in board_facts.AHEAD and _gate_gating(n, run))
def _gate_attempts():   # the attempt journal module beside this script (#1255); -I keeps the script's directory off sys.path, so the import names it itself rather than lean on an earlier one
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import gate_attempts
    return gate_attempts
_ga_journal = None
def _gate_journal():   # the attempt journal one pass reads (#1255), None when the board cache is unnamed: the gating fact stays False, never an error
    global _ga_journal
    if _ga_journal is None:
        ga = _gate_attempts()
        cache = os.environ.get("SG_BOARD_CACHE")
        _ga_journal = ga.journal_path(cache, repo0) if cache else False
    return _ga_journal or None
def _gate_gating(n, run):   # the gating fact (#1257): a gate live for the wave right now, or the wave's last attempt red or unproven with none since; read from the journal, so a restart answers the same
    ga = _gate_attempts()
    live = ga.gate_live(st, run)
    journal = _gate_journal()
    return ga.gating(journal, n, run, live=live) if journal else live
def wave_entry(n, f, i):   # the wave's say over issue n of a run (#467): None when the run cut no wave, "entered" when its child entered it (the entered fact of its ledger, ADR-0149), "open" when the wave can still take it, "out" when the wave landed without it. A child's own land is in its tree, never in the wave, so for a run that cut a wave only the entry is Code Complete (ADR-0145 rule 12)
    names = {f.name, i["run_id"]}; a = st / "auto"
    if not any((a / f"waved-{r}").exists() or (a / f"wave-{r}").exists() for r in names): return None
    if any((r, str(n)) in entered() for r in names): return "entered"
    return "open" if any((a / f"wave-{r}").exists() for r in names) else "out"
def pr_count(n):   # how many remote work branches name the issue: one exists once the issue is past dispatch. The name is kept for its callers; no pull request is read
    return str(len(work_branches().get(str(n), [])))
def branch_ahead_count(n):   # how many of its work branches hold commits the landing branch lacks
    return str(sum(1 for _, g in work_branches().get(str(n), []) if g in ("ahead", "diverged")))
def branch_in_dev_count(n):   # how many of its work branches are in the landing branch: the tip is an ancestor of dev
    return str(sum(1 for _, g in work_branches().get(str(n), []) if g in ("behind", "identical")))
_named_in_land = None
def named_in_land():   # {issue: commit time of its newest naming commit} whose fix is a commit in origin/<land>: its subject ends `(#N)` and is no `TDD RED` (a red test alone fixes nothing). A fix pushed straight to dev reached it (#519). The subject only: a body names issues in passing. One local log a pass, no API call; a checkout with no such ref names none
    global _named_in_land
    if _named_in_land is None:
        land = f"origin/{os.environ.get('SG_LAND_BRANCH') or 'dev'}"
        base = os.environ.get("SG_LAND_SCAN_FROM", "").strip()   # a fork's last upstream commit: the upstream's `(#N)` subjects name its PRs, never this repository's issues (#759)
        r = _run(["git", "-C", str(co), "log", "-n", "2000", "--format=%ct %s", f"{base}..{land}" if base else land])
        _named_in_land = {}
        for line in r[1].splitlines() if r[0] == 0 else []:
            ct, _, subj = line.partition(" ")
            m = re.search(r"\(#(\d+)\)\s*$", subj)
            if not m or subj.startswith("TDD RED"): continue
            t = float(ct) if ct.isdigit() else 0.0
            _named_in_land[m.group(1)] = max(_named_in_land.get(m.group(1), 0.0), t)
    return _named_in_land
def kept_marker(n):   # a kept branch holds the issue's commits for a round (wave.sh keep_conflict, branchland.sh): auto/kept-fix_<n>-..., or a marker whose `issue <n>` line names it (#1958)
    for m in (st / "auto").glob("kept-*"):
        if m.name.startswith(f"kept-fix_{n}-"): return True
        t = marker_text(m) or ""
        if any(l.strip() == f"issue {n}" for l in t.splitlines()): return True
    return False
ended_empty = {}   # issue -> verdict time of the newest run that ended with nothing landed for it (rule R fills it, #1958)
def requeued_at(n):   # the newest no-landing requeue of the issue: a requeue fact or a refused wave entry of its ledger, or a run end rule R read (#1958). 0 when none; a ledger that cannot be read is none, so the old reading stands
    t = ended_empty.get(str(n), 0.0)
    try:
        rec = ledger().read(f"{repo0}#{n}")
        t = max([t] + [float(x.get("ts") or 0) for x in (rec.get("requeued") or {}).values() if isinstance(x, dict)]
                + [float(x.get("ts") or 0) for x in (rec.get("wave") or {}).values() if isinstance(x, dict) and x.get("state") == "refused"])
    except (OSError, ImportError, ValueError, KeyError, TypeError): pass
    return t
def named_landing(n):   # a `(#N)` commit in the landing branch is the issue's landing unless a no-landing requeue or a run end is newer than it (#1958): the requeue's Todo is the later fact, so an older partial landing never writes Code Complete over it
    ct = named_in_land().get(str(n))
    return ct is not None and requeued_at(n) <= ct
fd = os.environ.get("SG_FORK_DIR"); fb = os.environ.get("SG_FORK_BASE")
def fork_verdict(n, sha):   # what the fork base says about a `fork-<n>-<sha9>` marker, the close pass's claim that it merged that commit: "landed", "ahead" (a fix commit is still to come), "unwound" (the base no longer holds it and nothing is left to merge), or "unknown" when the fork cannot answer at all -- the marker then stands as the evidence it is
    if not (fd and fb): return "unknown"
    if subprocess.run(["git", "-C", fd, "cat-file", "-e", sha], capture_output=True).returncode: return "unknown"   # the fork does not hold the commit: nothing to test the claim against
    if subprocess.run(["git", "-C", fd, "merge-base", "--is-ancestor", sha, fb], capture_output=True).returncode == 0: return "landed"
    br = (os.environ.get("SG_FORK_BRANCH") or "issue/%n").replace("%n", n)
    if subprocess.run(["git", "-C", fd, "rev-parse", "-q", "--verify", "refs/heads/" + br], capture_output=True).returncode == 0 and sh("git", "-C", fd, "rev-list", "--count", f"{fb}..{br}").strip() not in ("", "0"): return "ahead"
    files = sh("git", "-C", fd, "show", "--pretty=format:", "--name-only", sha).split()
    if not files or subprocess.run(["git", "-C", fd, "diff", "--quiet", sha, fb, "--", *files], capture_output=True).returncode == 0: return "landed"   # the base holds the change itself: a rebased head, nothing was unwound
    return "unwound"
def refuse_comment(n, refusal, err, why):   # a refused write queues one comment on the issue it was about (#141): the card says why it did not move, through the queue cqflush.sh posts from, so the comment lands whatever happens to this pass. The bug the refusal files is the task; this comment is the place. It goes through the helper, so the file name and the body are built in one place.
    rc, _ = _run(["bash", str(pathlib.Path(__file__).parent / "refusal.sh"), str(n), refusal, err, why])
    if rc: say(f"REFUSAL-COMMENT-FAILED #{n}: {refusal} was not queued for this issue (exit {rc})")
    return rc == 0
def status_column(args):   # the Status column a board write sets, or None for a write that sets another fact of the card
    a = list(args)
    if a and a[0] == "--add": a = a[1:]
    return a[2] if len(a) == 4 and not a[0].startswith("--") else None
def predict_write(n, col, why):   # the write is a ledger event first (#1841): the board's next read is predicted before it lands
    try: return ledger().board(f"{repo0}#{n}", "janitor", col, why, by="janitor") in ("recorded", "duplicate")
    except (ImportError, RuntimeError, OSError, ValueError, KeyError) as e:
        say(f"BOARD-PREDICT-FAILED #{n}: the ledger holds no prediction for {col}: {e}")   # the write still runs, and the defect is named
        return False
def drop_write(n, col):   # a move a rule refuses can never land: its prediction ends unwritten, so no dispatch waits on it (#2122)
    try: ledger().boardx(f"{repo0}#{n}", "janitor", col)
    except (ImportError, RuntimeError, OSError, ValueError, KeyError): pass
def prove_write(n, col):   # the writer's own proof ends the prediction: only this mark, or the event's drop, does (#1841)
    try: ledger().boardw(f"{repo0}#{n}", "janitor", col, "janitor")
    except (ImportError, RuntimeError, OSError, ValueError, KeyError): pass
def board_write(args, n, why, reason=None, event=None):   # the one writer of a card: a status write that succeeds updates the card cache, so every later read in this pass sees the column the pass set (#91)
    gone = st / "auto" / f"janitor-gone-{n}"
    if gone.exists(): return False
    col, pending = status_column(args), False
    if col and not dry: pending = predict_write(n, col, why)   # a dry run writes nothing, and predicts nothing
    head = subprocess.run(["git", "-C", os.environ.get("SG_CHECKOUT") or ".", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() if reason else ""
    env = {**os.environ, "SG_MOVE_REASON": reason or "", "SG_MOVE_EVENT": event or "", "SG_MOVE_SHA": head}   # names why a card returns to Todo (#1059) and the event and sha a reset names on its issue (#1904)
    r = subprocess.run(["bash", str(pathlib.Path(__file__).parent / "board.sh"), *args], capture_output=True, text=True, env=env)
    if r.returncode:
        msg = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
        named = next((l for l in msg if l.startswith("#")), None)   # board.py prints a read note beside the refusal (#96): the refusal is the line that names the card
        if r.returncode == 3:   # the seam refused a move back along the axis (ADR-0143): logged there, the card keeps its column, no comment
            if pending: drop_write(n, col)   # the refusal is settled: a pending event would hold every dispatch (ADR-0165, #2122)
            say(f"BOARD-REJECTED #{n}: {why}: {named or msg[0]}")
            return False
        err = named or (msg[0] if msg else "no output")
        if gone_msg(err):
            say(f"GONE-ISSUE #{n}: board write refused because the issue is gone: {err}")
            if pending: drop_write(n, col)   # a deleted issue has no card to write: its prediction ends unwritten (#2074)
            if not dry: gone.touch()
            return False
        say(f"BOARD-WRITE-FAILED #{n}: {why}: {err}")
        if not dry: refuse_comment(n, "BOARD-WRITE-FAILED", err, why)   # a dry run acts on nothing, so it queues nothing
        return False
    if pending: prove_write(n, col)   # the card write landed: the prediction is written, and no reader draws it pending
    a = args[1:] if args and args[0] == "--add" else args
    if len(a) == 4 and not a[0].startswith("--") and a[1] in _boards: _boards[a[1]][1][str(a[3])] = a[2]
    return True
def door_read(route, q):   # one read through the door (gh_api, ADR-0277): (answered, the projection as text); a refusal or a deadline miss is not answered
    r, out = _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_api", route, "-q", q])
    return r == 0, out.strip()
CLOSER = '[.[] | select(.event == "closed") | .actor.login] | last // ""'   # who closed the issue last, from its events (a single-issue read through the door carries no closed_by)
_wave_in_dev = {}   # issue -> (wave branch, its tip) of the wave whose landing in dev put its card in Closed, read by terminal_facts in the same pass
def terminal_facts(n, col):   # the facts a terminal comment states (#245), or None when a read did not answer: nothing is claimed on a guess
    from urllib.parse import quote
    if col == "Closed":
        land = os.environ.get("SG_LAND_BRANCH") or "dev"
        landed = [b for b, g in work_branches().get(str(n), []) if g in ("behind", "identical")]   # the branch is the delivery (#358): no pull request is read
        if landed:
            branch = landed[0]
            ok, sha = door_read(f"repos/{repo0}/branches/{quote(branch, safe='')}", '.commit.sha // ""')
            if not ok: return None
        elif str(n) in _wave_in_dev:
            branch, sha = _wave_in_dev[str(n)]
        else:
            ok, who = door_read(f"repos/{repo0}/issues/{n}/events?per_page=100", CLOSER)
            return {"base": land, "closed_by": who, "reason": issue_info(n)[1]} if ok else None   # the close reason: a not-planned close is never called completed (#517)
        test = ""
        if sha:
            ok, test = door_read(f"repos/{repo0}/commits/{sha}/check-runs", '[.check_runs[] | .name + ": " + (.conclusion // .status)] | join(", ")')
            if not ok: return None
        return {"base": land, "branch": branch, "sha": sha, "test": test}
    state, reason, _ = issue_info(n)   # the pass's one issue read: state, reason and labels
    if "wontfix" in issue_labels(n):
        ok, who = door_read(f"repos/{repo0}/issues/{n}/events?per_page=100", '[.[] | select(.event == "labeled" and .label.name == "wontfix") | .actor.login] | last // ""')
        return {"who": who, "why": "labelled wontfix"} if ok else None
    if state == "closed" and not_planned(reason):
        ok, who = door_read(f"repos/{repo0}/issues/{n}/events?per_page=100", CLOSER)
        return {"who": who, "why": "closed as a duplicate (superseded by another issue)" if str(reason).strip().lower() == "duplicate" else "closed as not planned"} if ok else None
    return None
def run_summary(n):   # the run that worked issue n as a child, as text for its terminal comment (#559): the run id, the other children, the verdict and the dashboard command; "" when no run held it
    best = None
    for r in keep:
        i = read(folder(r))
        if not i or not any(str(n) in v for v in i["kid_issue"].values()): continue
        if best is None or str(i["run_ts"]) >= str(best["run_ts"]): best = i
    if best is None: return ""
    others = sorted({int(x) for v in best["kid_issue"].values() for x in v} - {int(n)})
    return (f"Run {best['run_id']}: other children {', '.join(f'#{k}' for k in others) or 'none'}; verdict {best['name']}. "
            f"Dashboard: `squad dashboard {best['run_id']}`.\n")
def queue_terminal(n, col, note):   # one comment per terminal entry, through the queue cqflush.sh posts from; the marker keeps a repeat pass from queuing it twice
    mk = st / "auto" / f"janitor-terminal-{n}"
    if mk.exists() and mk.read_text().strip() == col: return
    if col == "Closed":   # the state is read again (#775): a close a reopen undid is never reported
        _is.pop(n, None); _is.pop(str(n), None)
        if issue_info(n)[0] != "closed":
            say(f"TERMINAL-REOPENED #{n}: the card sits in Closed but the issue is open again; no Closed comment is queued")
            return
    say(f"TERMINAL-COMMENT #{n}: {col} -> {note.splitlines()[-1]}")
    note += run_summary(n)   # the run's summary rides the comment this pass already posts: no other write path
    if dry: return
    (st / "cq").mkdir(exist_ok=True)
    tag = "terminal-closed" if col == "Closed" else "terminal"   # cqflush.sh reads the state again before it posts a Closed one (#777)
    (st / "cq" / f"{CQ}#{n}-{tag}-{os.getpid()}.md").write_text(note)
    mk.write_text(col + "\n")
expected = {}   # issue -> (rank, status, why, run_id, run_ts): the column R derived for every card it owns; rule O reads it, never the board
no_landing = set()   # the issues whose card returns to Todo because a run landed nothing: their move names that reason (#1059)
def door(fn, *a):   # one call through ghcall.sh: (exit code, output). A read is gh_api, a write gh_send (ADR-0277), so this module's own call sites stay as they were
    return _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), fn, *a])
RUN_PARENT = "run-parent"   # the label of a run's roll-up issue: never work, so no dispatcher takes it (startable) and the board holds no card for it (#500)
def reconcile_board():
    if not (owner and proj and repo0): return
    shipped = os.environ.get("SG_BOARD_VOCAB") == "shipped"   # the loop writes the shipped vocabulary once the board carries it (expand done); until then it writes today's options
    SHIPPED_NAME = FREEZE_LABEL = {}
    TERMINAL = ()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # board_columns sits beside this script; -I keeps the script's directory off sys.path
    # The terminal column is one function for every vocabulary: the reconciler reads the same one, so the two passes cannot
    # disagree about where a closed issue's card belongs (spec #132, #1932).
    from board_columns import terminal_column, ROLE, canonical, REVIEWING, TESTING, batching_column   # a column's spellings resolve before a compare (#2151)
    if shipped:
        from board_columns import SHIPPED_NAME, FREEZE_LABEL, TERMINAL, TERMINAL_TAG, terminal_comment, column_for, past
    doing = "Doing" if shipped else "In Progress"
    landing = batching_column(shipped)   # Batching on the shipped board, the legacy board's own column otherwise (#2190, #2315)
    GATING = TESTING   # the shipped column between Batching and Closed; board_columns.ROLE names the same word (ADR-0170)
    frozen = set()   # issues that carry the owner freeze this pass: shipped vocabulary only
    stopped = set()   # issues whose newest run stopped needs-human or failed: no stale branch or local landing moves them forward
    def unfrozen(n, f, i):   # the owner's rearm lifted the freeze (#1562): the line is written once per (issue, run verdict), so an
        # issue no pass can dispatch does not write one line every cycle. The marker names the run folder and its verdict key, and
        # a resume's new verdict gets a new marker.
        mk = st / "auto" / f"janitor-unfrozen-{n}-{f.name}-{str(i['vkey']).replace(':', '-')}"
        if mk.exists(): return
        say(f"BOARD-UNFROZEN #{n}: the owner rearm is newer than run {i['run_id']}'s verdict")
        if not dry: mk.touch()
    specs = set()   # a spec is never work, and its card is a fold over its sub-issues (ADR-0142): a commit subject, a branch or a landing that names it is its children's, so no work rule writes its card and the fold alone sets it (#665)
    held_owner = {}   # issue -> (rank, status, why): the write frozen:owner held back this pass, named in cycle.log when it differs
    parked = {}   # issue -> (status, why): the Todo back to which R would have moved a card the owner parked in Backlog, named in cycle.log
    _owner_frozen = {}
    def owner_frozen(n):   # the issue carries frozen:owner: the owner holds its card, so R never moves it (the open-issue read answers for the whole pass)
        n = str(n)
        if n not in _owner_frozen:
            _, ol = open_issues()
            labs = ol.get(n)
            _owner_frozen[n] = "frozen:owner" in (labs if labs is not None else issue_labels(n))
        return _owner_frozen[n]
    def set_expected(n, status, why, run_id="", run_ts="", rank=0):
        if shipped:   # the shipped vocabulary (ADR-0141): Blocked is no column, it is the owner freeze over the column the work reached
            status = SHIPPED_NAME.get(status, status)
            if status is None: frozen.add(n); return
        if owner_frozen(n) and issue_info(n)[0] != "closed":   # the owner's freeze keeps the column stuck() wrote: record the write R held back
            old = held_owner.get(n)
            if not old or old[0] < rank: held_owner[n] = (rank, status, why)
            return
        old = expected.get(n)
        if old and old[0] >= rank: return
        if status == os.environ.get("SG_TODO", "Todo") and card_status(proj, n) == "Backlog":
            # The loop's one backward move undoes the dispatch that took the card out of Todo (ADR-0143). A dispatch reads
            # Todo only, so a card in Backlog was never claimed by the loop: the owner parked it, and only the owner starts it
            # (ADR-0142). A run that ends without landing derives Todo here, and that Todo must never reach the parked card (#2007).
            parked.setdefault(n, (status, why)); return
        expected[n] = (rank, status, why, run_id, run_ts)
        if status == "Todo" and "nothing landed" in why: no_landing.add(n)
    def wave_column(n, f, i):   # a card whose wave is in dev is Closed; a gate that holds the wave is Testing; the push, the approval and the dev CI are reported facts, never a column (ADR-0145, ADR-0170)
        if not shipped: return
        col, _ = column_for(wave_facts(n, {f.name, i["run_id"]}))
        if col == "Closed":
            run = next(r for r in {f.name, i["run_id"]} if r in _wv)
            _wave_in_dev[str(n)] = ("wave/" + run, _wv[run][0])
            set_expected(n, col, f"the wave of run {i['run_id']} is in dev", i["run_id"], i["run_ts"], 80)
        elif col == GATING:   # the gate fact reached column_for but no writer set the column (#1430): a live gate, or a red or unproven attempt with none since, holds the card before dev
            set_expected(n, col, f"a gate holds the wave of run {i['run_id']}", i["run_id"], i["run_ts"], 70)
    def unheld(n, f, i):   # a live run holds the issue, but its spawn batch gave it no child (#480): nobody works it, so it is back in the queue and its dispatch marker goes. A marker written after the batch belongs to a newer dispatch and stays
        set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} gave the issue no child (the router did not choose it, or the engine rejected its spawn)", i["run_id"], i["run_ts"], 20)
        for k in ("disp", "bug"):
            m = st / "auto" / f"{k}-{n}"
            mt = marker_mtime(m)   # #794: a marker gone since the test is no marker: no stat on a vanished file
            if mt is not None and mt <= i["spawn_ts"]:
                say(f"UNHELD #{n}: run {i['run_id']} gave it no child; {m.name} dropped")
                if not dry: m.unlink(missing_ok=True)
    def code_complete(n, f, i, why):   # a landing is Batching only once the wave, when the run cut one, holds the issue (#467)
        e = wave_entry(n, f, i)
        if e == "open" and shipped: set_expected(n, REVIEWING, f"{why}; its child's code is done and its review holds it out of the open wave of run {i['run_id']}", i["run_id"], i["run_ts"], 35)   # #603
        elif e == "open": set_expected(n, "In Progress", f"{why}; its child has not entered the open wave of run {i['run_id']} yet", i["run_id"], i["run_ts"], 30)
        elif e == "out": set_expected(n, os.environ.get("SG_TODO", "Todo"), f"{why}, but the wave of run {i['run_id']} landed without its child", i["run_id"], i["run_ts"], 20)
        else:
            set_expected(n, landing, why, i["run_id"], i["run_ts"], 60)
            wave_column(n, f, i)
    def stopped_run(n, f, i, evidence):   # a run that stopped needs-human never moves its issue's card forward: its Blocked stands, whatever local landing it left (ADR-0029, #660)
        stopped.add(n)   # the "nothing landed" stop stands even when the owner's rearm lifts the freeze (#1562): a stale branch never reads as delivered work
        why = "; ".join(i["reasons"]) or "no reason recorded"
        if rearmed_after(n, i): unfrozen(n, f, i)
        else: set_expected(n, "Blocked", f"run {i['run_id']} {evidence}, but the run needs a human ({why})", i["run_id"], i["run_ts"], 40)
    # Rank 20, a run that ended with nothing delivered: Doing back to Todo. It is the one backward move the loop makes, and it
    # undoes the loop's own claim, the dispatch that moved the card out of Todo (ADR-0143, #550). No other rule moves a card
    # back into Todo or Backlog: only the owner does.
    runs = []; newest = {}   # a card follows the newest run that holds its issue: an older run's column never overrides it, whatever its rank (#619)
    for r in keep:   # a run whose line this pass dropped from active-runs is in `keep` too, so it still settles its card: none of its issues is closed, so the closed-issue Done rule never answered for it (#2138, #2144)
        f = folder(r); i = read(f); nums = req_issues(f)
        if r in dropped: say(f"FINISHED-RUN-CARD {r}: the card pass read the run this pass left out of active-runs (#2144)")
        if i: nums |= i["acc"]
        age = (1, "") if i is None else (0, str(i["run_ts"]))   # a run with no record yet (a dispatch marker) is the newest
        runs.append((f, i, nums, age))
        for n in nums: newest[n] = max(newest.get(n, age), age)
    for f, i, nums, age in runs:
        nums = {n for n in nums if age >= newest[n]}   # rank orders the signals of one run only (#619)
        by_issue = {}   # issue -> the children whose own accept line named it: that child's outcome decides its card (#271)
        names = {f.name, i["run_id"]} if i else set()
        cut_wave = bool(i) and any((st / "auto" / m).exists() for r in names for m in (f"waved-{r}", f"wave-{r}"))   # a run that cut a wave delivers through it (ADR-0145 rule 12)
        if i:
            for ch in sorted(i["kid_issue"]):
                for x in i["kid_issue"][ch]: by_issue.setdefault(x, []).append(ch)
        for n in nums:
            mine = by_issue.get(n, [])
            # The delivery evidence: the landing pass's `landed` fact for the issue (#691, #695), a landing of this run or a
            # branch a branchland pass landed. A run that cut no wave delivers by its own engine land event, with the child
            # attribution of #271; a landing of this run that reached no base names no issue, so it credits none.
            kid_ok = bool(mine) and any(c in i["kid_landed"] for c in mine) if i else False   # a child of this issue landed its own work
            sib_ok = bool(mine) and bool(i["kid_landed"]) and not kid_ok if i else False   # the run landed through a sibling
            fact = bool(i) and base_landed(n, names)
            own_landed = fact or (bool(i) and not cut_wave and (kid_ok or (i["landed"] and not sib_ok)))
            sib_landed = bool(i) and ((sib_ok and not cut_wave) or (bool(mine) and any(run_landed(x, names) for x in nums if x != n)))
            run_any = bool(i) and ((not cut_wave and i["landed"]) or any(run_landed(x, names) for x in nums))
            if i is None:
                set_expected(n, "In Progress", f"run {f.name} has dispatch marker", f.name, "", 30)
            elif not i["verdict"] and i["decided"] and not any(n in v for v in i["kid_issue"].values()) and all(i["kid_issue"].values()):
                unheld(n, f, i)   # only when every child maps to its issues: an unmapped child may be working this one (#725)
            elif not i["verdict"]:
                set_expected(n, "In Progress", f"run {i['run_id']} is live", i["run_id"], i["run_ts"], 30)
            elif own_landed:   # a landing is the child's own (#271), and a failed verdict does not unland work that reached the base (#666, ADR-0143, #691); needs-human makes the owner the next actor (ADR-0029, #660)
                if i["name"] == "needs-human": stopped_run(n, f, i, "landed work in the base" if fact else "landed work locally")
                else: code_complete(n, f, i, f"run {i['run_id']} landed work " + ("in the base" if fact else "locally"))
            elif holding(n) and (sib_landed or not run_any): continue   # nothing landed here, but a live run works the issue or a pending wave carries it: its card is that run's, never this run's Todo or Blocked (#540)
            elif sib_landed:   # the run landed, but through a sibling: this child committed nothing, so its own outcome decides and the loop's column holds it (#271)
                c = mine[0]; oc, ors = i["kid_out"].get(c, ("", []))
                # The child's own landing failure names the note, and its `pass` outcome word never does (#2015): a child
                # whose work did not reach the target is no pass, whatever its entries answered.
                lf = i["kid_land_fail_reason"].get(c)
                why = f"its landing failed: {lf}" if lf else ("; ".join(ors) or "no reason recorded")
                if oc == "needs-human" or i["name"] == "needs-human":   # not success (ADR-0029): the owner is the next actor
                    stopped.add(n)
                    if rearmed_after(n, i): unfrozen(n, f, i)
                    # The landing failure and the human need are two facts: the note names the failure first when it holds (#2015).
                    else: set_expected(n, "Blocked", f"run {i['run_id']} child {c} " + (f"its landing failed: {lf} and needs a human" if lf else f"needs a human with nothing landed ({why})"), i["run_id"], i["run_ts"], 40)
                else:
                    if oc == "fail": stopped.add(n)
                    set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} child {c} " + ("" if lf else f"{oc or 'stopped'} ") + f"with nothing landed ({why})", i["run_id"], i["run_ts"], 20)
            elif i["name"] == "no-repro":   # the bug did not reproduce, so the owner decides it: the card keeps its column under the owner freeze (ADR-0029)
                stopped.add(n)
                why = "; ".join(i["reasons"]) or "no reason recorded"
                if rearmed_after(n, i): unfrozen(n, f, i)
                else: set_expected(n, "Blocked", f"run {i['run_id']} found the bug does not reproduce with nothing landed ({why})", i["run_id"], i["run_ts"], 40)
            elif i["failed"]:
                stopped.add(n)
                why = "; ".join(i["reasons"]) or "nothing landed"
                set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} failed with nothing landed ({why})", i["run_id"], i["run_ts"], 20)
            elif i["name"] == "needs-human":   # not success (ADR-0029): the owner is the next actor, and the board says so
                stopped.add(n)
                why = "; ".join(i["reasons"]) or "no reason recorded"
                if rearmed_after(n, i): unfrozen(n, f, i)
                else: set_expected(n, "Blocked", f"run {i['run_id']} needs a human with nothing landed ({why})", i["run_id"], i["run_ts"], 40)
            else:   # a run that ended with nothing delivered delivered nothing: the card goes back to the queue, never to Code Complete (ADR-0145 rule 12, #464)
                set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} completed with nothing landed", i["run_id"], i["run_ts"], 20)
            if i and i["verdict"] and not own_landed and isinstance(i["vts"], (int, float)):   # a run end with nothing landed: an older `(#N)` commit yields to it (#1958)
                ended_empty[str(n)] = max(ended_empty.get(str(n), 0.0), float(i["vts"]))
    # A run that ended without a verdict has no card to rank: the reoffer marker a run-end path wrote names it, and the card
    # goes back to the queue unless a live run holds the issue, the owner froze it, or its work landed. The marker goes once
    # the card has left Doing; an unreadable card keeps it for the next pass (ADR-0143, #1034).
    for m in sorted((st / "auto").glob("reoffer-*")):
        x = m.name[len("reoffer-"):]; run = marker_text(m)
        if not x.isdigit() or run is None: continue
        run = run.strip(); cs = card_status(proj, x)
        if cs and cs != doing:
            if not dry: m.unlink(missing_ok=True)
            continue
        if not cs or x in expected or holding(x) or base_landed(x, {run}) or run_landed(x, {run}): continue
        say(f"STALE-CARD #{x}: card {doing} behind run {run}, which ended without a verdict and landed nothing; card -> {os.environ.get('SG_TODO', 'Todo')}, so the next cycle dispatches the issue again")
        if not dry:
            (st / "cq").mkdir(exist_ok=True)
            (st / "cq" / f"{CQ}#{x}-stalecard-{os.getpid()}.md").write_text(
                f"Its card was {doing} behind run {run}, which ended without a verdict and landed nothing. The card is back in {os.environ.get('SG_TODO', 'Todo')}, so the next cycle dispatches the issue again.\n")
        set_expected(x, os.environ.get("SG_TODO", "Todo"), f"run {run} ended without a verdict (no process, or its folder is gone) with nothing landed", run, "", 20)
    for m in sorted((st / "auto").glob("disp-*")):
        holder = marker_text(m)
        if holder is None: continue   # #794: a marker gone since the glob (a dangling link) holds nothing
        n = m.name[5:]
        if n.isdigit() and holder in keep and not (read(folder(holder)) or {}).get("verdict"):   # a holder with a verdict is over: its own record ranks the card (Todo, Blocked, Code Complete), not the marker it left
            set_expected(n, "In Progress", f"{m.name} holds live run {holder}", holder, "", 30)
    # The loop floor gives a column only to an open issue with no card, once: Todo for a bug, Backlog for anything else
    # (#550). A card that has a column keeps it: the floor never moves a card, so it never sends one back to Backlog or Todo
    # (ADR-0143). Only the owner moves a card there. This rank is below every rule above, so current run, marker, branch,
    # landing, closure, or owner-label evidence wins.
    # A label that holds the issue out of every dispatch lane is the owner's hold: such a card is placed, never stranded (#1958).
    # The lanes' skip lists (skiplabels.sh) and the owner labels; Todo would dispatch none of them.
    STRAND_HOLDS = {x for v in (os.environ.get("SG_BUG_SKIP_LABELS") or "needs-owner,no-auto,wontfix,question",
                                os.environ.get("SG_FRONTIER_SKIP_LABELS") or "") for x in v.split(",") if x} | {
        "frozen:owner", "frozen:dependency", "ready-for-human", "ready-for-HAL9K", "needs-triage", "needs-info",
        "needs-ratification", "debt", RUN_PARENT}
    FRONTIER_SKIP = frontier_skip()
    oans, olabels = open_issues()
    if oans: fill_missing(proj, set(olabels) | set(expected))   # a card the REST read omitted is asked once of the GraphQL read and rides the pass's map (#1463)
    specs = {k for k, ls in olabels.items() if "spec" in ls} if oans else set()
    if oans:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the fold sits beside this script; -I keeps the script's directory off sys.path
        import spec_fold
        have_cards = cards(proj)[1]   # every card's Status, read once per pass: the fold, rule 12 and the Doing note share it
        for n in sorted(olabels, key=int):
            if holding(n) and n not in expected and n not in specs: continue   # a run still delivers it and no rule named its column: the floor never sends it back to Todo (#540); a spec's children never hold its card
            fresh = (lambda a, m: a and not m.get(str(n)))(*cards(proj))   # an answered read that holds no card for it, or a card with no Status: no column yet
            bug = bool(olabels[n] & {os.environ.get("SG_BUG_LABEL") or "bug"})
            if fresh: set_expected(n, os.environ.get("SG_TODO", "Todo") if bug else "Backlog", "open issue with no card", rank=10)
            # A bug card left in Backlog is dispatched by no pass: autobug reads Todo only, and autofrontier skips the bug label.
            # The floor never moves it (ADR-0143), so this is a report, never a write: one line per issue, a marker keeps it once.
            if bug and cards(proj)[1].get(str(n)) == "Backlog":
                mk = st / "auto" / f"janitor-bugback-{n}"
                if not mk.exists():
                    say(f"BUG-IN-BACKLOG #{n}: a bug card sits in Backlog; no pass dispatches it until the owner moves it to Todo")
                    if not dry: mk.touch()
            if shipped and "wontfix" in olabels[n]: set_expected(n, "Abandoned", "issue carries wontfix", rank=95)
            # A spec is written and not scheduled, so the floor opens it in Backlog, and the owner starts it (ADR-0142). The fold
            # moves it forward only. Its rank sits above the floor and below every rule a run, a marker or a branch raises.
            is_spec = "spec" in olabels[n]
            if is_spec or (_kids.get(n) and RUN_PARENT not in olabels[n]):
                # A parent's column is a fold over its sub-issues' cards (spec_fold.py): the relation came with the one read above and
                # the cards are read once per pass, so the fold adds no call. A parent that is not a spec takes the Doing lift only
                # (#942): the spec vocabulary's Closed and Abandoned are never written to it.
                kid_cols = [have_cards.get(k, "") for k in _kids.get(n, [])]
                todo = os.environ.get("SG_TODO", "Todo")
                # A spec reads Todo while a ticket is in flight, and never a work column (#1131). No child gives it a terminal
                # column either (#1906): this walk holds open issues only, so the fold's own-state answer is None here, and a
                # closed spec's card follows its issue through the terminal rule below.
                col, reverted = spec_fold.fold(kid_cols, have_cards.get(n, ""), todo, spec=is_spec, todo=todo)
                if is_spec and col == todo and have_cards.get(n, "") in ("", "Backlog"):   # only the owner starts a spec from Backlog or with no card (ADR-0142, #1909)
                    col = None
                    mk = st / "auto" / f"janitor-specheld-{n}"
                    if not mk.exists():
                        say(f"SPEC-HELD-IN-BACKLOG #{n}: a ticket is in flight but the spec is not in Todo; only the owner moves it there")
                        if not dry: mk.touch()
                if is_spec and reverted and shipped:   # an open spec never keeps a terminal column: its own work is never a branch, so the reopen rule below (ADR-0143 rule 2) returns it to Todo with the reason (#1431)
                    set_expected(n, col, f"spec folds over its sub-issues: a child reopened, the card keeps {col}", rank=15)   # the fold's own read still lands, so a pass whose reopen declines leaves the card where the fold found it
                    reverted = False   # the reopen loop lifts the card; it carries no reverted tag and no negative reason
                elif col and (is_spec or col == spec_fold.DOING): set_expected(n, col, ("spec" if is_spec else "parent") + " folds over its sub-issues" + (": a child reopened, the card keeps its column" if reverted else ""), rank=15)   # forward only: below Todo the fold writes nothing, and the spec keeps the owner's column (#550)
            if is_spec:
                # A spec in Todo lifts none of its tickets: the owner's door moves a spec with its Backlog tickets in one write
                # (hub.py promote_write, #1909). The pass names the held tickets once per spec.
                todo = os.environ.get("SG_TODO", "Todo")
                if have_cards.get(n, "") == todo and any(k in olabels and have_cards.get(k, "") in ("", "Backlog") for k in _kids.get(n, [])):
                    mk = st / "auto" / f"janitor-specheld-todo-{n}"
                    if not mk.exists():
                        say(f"SPEC-HELD-IN-TODO #{n}: its Backlog tickets stay in Backlog; only the owner's move of the spec lifts them")
                        if not dry: mk.touch()
                # A spec with at least one sub-issue, every one closed, is done (the owner's rule): the loop closes it with one comment.
                # A spec with no sub-issue proves nothing and stays open.
                sub = _subs.get(n) or {}
                # A sub-issue a landing named outside its run's dispatch set (wave.sh, wave-outside.tsv, #775) has a close with
                # no evidence: the spec is held open and the hold is named, so the owner decides.
                outside = [(k, r) for k in _kids.get(n, []) for r in [wave_outside().get(k)] if r]
                if sub.get("total", 0) >= 1 and sub.get("completed") == sub["total"] and outside:
                    k, r = outside[0]
                    say(f"SPEC-HELD #{n}: sub-issue #{k} was named outside the dispatch set of run {r}; its close has no evidence, so the spec stays open")
                elif sub.get("total", 0) >= 1 and sub.get("completed") == sub["total"]:
                    say(f"SPEC-CLOSED #{n}: every sub-issue ({sub['total']}) is closed")
                    if not dry: close_spec(n, f"Closed by the loop: every sub-issue ({sub['total']}) is closed, so the spec is done.")
                if reverted:
                    say(f"SPEC-REVERTED #{n}: a child reopened, the card keeps {col} and carries the tag {spec_fold.TAG}")
                    if not dry and spec_fold.TAG not in olabels[n]: _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_send", "-X", "POST", f"repos/{repo0}/issues/{n}/labels", "-f", f"labels[]={spec_fold.TAG}"])
            # A Doing card that no run holds and no marker claims is left alone (#1034): a card a person placed and a stuck one
            # look the same, so only the owner moves it (ADR-0143 rule 1). The recorded dispatch of the issue's ledger tells
            # the two apart: a card the loop dispatched goes back to Todo, and the pass names it once before any other case.
            if not is_spec and have_cards.get(n, "") == doing and not holding(n) and not (st / "auto" / f"disp-{n}").exists() and not (st / "auto" / f"bug-{n}").exists():
                if dispatched(n) and not base_landed(n, set()) and branch_ahead_count(n) == "0" and not owner_frozen(n):   # the one-time recovery (#1034): a card a run left in Doing has no other way back, and the recorded dispatch proves the loop owns it (#1987)
                    held = sorted(issue_labels(n) & FRONTIER_SKIP)   # the frontier refuses such an issue, so Todo would never dispatch it (#2352)
                    if held:
                        say(f"SKIP-LABEL #{n}: card {doing} with a recorded dispatch stays; the frontier skips the issue for label {', '.join(held)}")
                    else:
                        say(f"STALE-CARD #{n}: card {doing} with a recorded dispatch, no live run, no marker and nothing landed; card -> {os.environ.get('SG_TODO', 'Todo')}, so the next cycle dispatches the issue again")
                        set_expected(n, os.environ.get("SG_TODO", "Todo"), f"card in {doing} with a recorded dispatch, no live run, no marker and nothing landed", rank=20)
                else:
                    mk = st / "auto" / f"janitor-doing-norun-{n}"
                    if not mk.exists():
                        say(f"DOING-NO-RUN #{n}: a card sits in Doing with no live run and no marker; the janitor leaves it and the owner moves it")
                        if not dry: mk.touch()
            # The #1034 recovery, widened (#1958): a card in Reviewing or Batching that nothing holds and nothing delivered
            # is stranded, since the frontier reads Todo only. No live run or open wave, no dispatch or kept marker, no remote
            # branch ahead of the landing branch or in it, no freeze, no landing fact and no `(#N)` landing: the card goes back
            # to Todo by the one backward move (ADR-0143), named no-landing, with one STRANDED line. A rule that ranked the card
            # this pass decides it instead.
            have_n = have_cards.get(n, "")
            if (not is_spec and canonical(have_n) in ({canonical(landing)} | ({canonical(REVIEWING)} if shipped else set())) and n not in expected
                    and not holding(n) and not any((st / "auto" / f"{k}-{n}").exists() for k in ("disp", "bug"))
                    and not kept_marker(n) and branch_ahead_count(n) == "0" and branch_in_dev_count(n) == "0"
                    and not owner_frozen(n) and n not in held_owner and not (olabels[n] & STRAND_HOLDS)
                    and not list((st / "auto").glob(f"fork-{n}-*")) and not landings().get(str(n)) and not named_landing(n)):
                say(f"STRANDED #{n}: card {have_n} with no live run, no marker, no branch, no freeze and no landing; card -> {os.environ.get('SG_TODO', 'Todo')} (no-landing), so the next cycle dispatches the issue again")
                set_expected(n, os.environ.get("SG_TODO", "Todo"), f"card in {have_n} with no live run, no marker, no branch and nothing landed", rank=20)
            if olabels[n] & {"needs-owner", "needs-ratification"}:
                set_expected(n, "Blocked", "issue is blocked by owner label", rank=100)
    for n in sorted(set(expected) | (set(olabels) if oans else set()), key=int):   # every open issue, as when the floor named each one: a rule below can still move a card forward
        state, reason, _ = issue_info(n)
        term = terminal_column(state, reason)   # the one function the reconciler reads too: Abandoned when the issue was not delivered, Closed when it was (spec #132, #1932)
        if term: set_expected(n, term if shipped else "Done", "issue is closed" if term == ROLE["abandoned"] else f"issue is closed as {reason}", "", "", 95 if term == ROLE["abandoned"] else 90)
        labels = issue_labels(n)
        if labels & {"needs-owner", "needs-ratification"}: set_expected(n, "Blocked", "issue is blocked by owner label", "", "", 100)
        if n not in specs and named_landing(n): set_expected(n, landing, "a commit in the landing branch names the issue", "", "", 50)   # a hand fix in dev: the floor never moves it back (ADR-0143, #519). Never a spec: a commit that names one is its child's (#665). A newer no-landing requeue or run end wins over it (#1958)
        if n not in specs and n not in stopped and branch_ahead_count(n) != "0":   # the branch's own state sets the column (#358): no pull request is read. A stopped run's branch is stale: it never lifts the Todo or Blocked its verdict set, and a branch that names a spec is its children's (#665)
            if shipped and review_waits(n): set_expected(n, column_for(wave_facts(n, ()) | {"review_wait": True})[0], "a work branch names the issue and the review of its tip waits", "", "", 50)   # ADR-0145: a child enters the wave only after its review approves, so a review that waits marks Doing
            else: set_expected(n, landing, "a work branch names the issue and is not in dev yet", "", "", 50)
        if n not in specs and branch_in_dev_count(n) != "0" and canonical(cards(proj)[1].get(str(n), "")) == canonical(landing):   # a fresh branch is in dev too, so only a card that reached the wave is delivered by it, and a spec's branch is its children's (#665)
            set_expected(n, "Closed" if shipped else "Done", "its work branch is in dev", "", "", 80)
    answered, have_all = cards(proj)
    if not answered: return   # no answer from the board: nothing is moved on a guess (a card that does not exist is an answer, and is created below)
    # The hand hold (#1664): an issue a takeover marker or a hand hold names is somebody's, so its Todo card goes to Doing once and
    # no lower rule moves it back while the marker or hold lives. A release record names the column the card moves to, once: the
    # card moves through the same loop below, and the record goes when the card carries the column. No marker, no hold and no
    # release: this block writes nothing. The format and the reader are squad/handhold.py's.
    HH = _handhold_module()
    hand_columns = {canonical(doing), canonical(landing)} | ({canonical(REVIEWING), canonical(GATING)} if shipped else set())
    released = {}
    for n, col in HH.releases(state=st).items():
        if issue_info(n)[0] == "closed":   # the closed-issue rule already names the card column (ADR-0143 rule 2), so a release has no move to make: the record goes, never a write the board refuses every pass (#1869)
            if not dry: HH.clear_release(n, state=st)
            say(f"HAND-RELEASE-DROPPED #{n}: the issue is closed, so the release to {col} writes nothing and its record is removed")
            continue
        want = canonical(col) if shipped else {"Closed": "Done", "Batching": landing}.get(canonical(col), col)   # a release names the shipped column; the legacy board reads its own (#2190)
        set_expected(n, want, f"a hand hold was released to {col}", rank=120)
        released[n] = want
    for r in HH.holds(state=st, closed=[n for n in HH.holders(state=st) if issue_info(n)[0] == "closed"]):   # a stale hold on a closed issue is released here, once (#2220)
        n = r["issue"]
        if not r["stale"] or issue_info(n)[0] != "closed" or dry: continue
        with contextlib.redirect_stdout(io.StringIO()):
            rc = HH.release([n], by=r["carrier"], to="Closed", state=st)
        if rc != 0: continue
        set_expected(n, "Closed", "a stale hand hold sits on a closed issue", rank=120)
        HH.clear_release(n, state=st)
        say(f"HOLD-RELEASED #{n}: the hold by {r['carrier']} is stale and the issue is closed, so it is released to Closed")
    for n, c in sorted(HH.holders(state=st).items(), key=lambda kv: int(kv[0])):
        if n in released: continue
        have = have_all.get(n)
        if not have or issue_info(n)[0] == "closed": continue
        who = f"taken over from run {c['who']}" if c["kind"] == "taken" else f"held by {c['who']}"
        if have == os.environ.get("SG_TODO", "Todo"): set_expected(n, doing, f"the issue is {who}: somebody works it", rank=45)
        elif canonical(have) in hand_columns: set_expected(n, have, f"the issue is {who}: its card is not moved back", rank=45)
    for n, (_, want, why) in sorted(held_owner.items(), key=lambda kv: int(kv[0])):   # frozen:owner holds the card: R names the write it held back, and writes nothing
        have = have_all.get(str(n))
        if have is not None and have != want:
            say(f"BOARD-FROZEN #{n}: frozen:owner holds the card in {have}; R left it alone (would have written {want}: {why})")
    for n, (want, why) in sorted(parked.items(), key=lambda kv: int(kv[0])):   # the owner parked the card in Backlog: R names the move it held back and writes nothing (#2007)
        say(f"PARKED #{n}: the card sits in Backlog, which only the owner leaves; R left it alone (would have written {want}: {why})")
    for n in sorted((k for k in set(expected) | set(olabels) if RUN_PARENT in (olabels.get(k) or issue_labels(k))), key=int):   # a run parent is a roll-up in GitHub, never work: it gets no card, and a card it has is removed, only when the board carries one (#500)
        expected.pop(n, None)
        if str(n) in have_all and board_write(["--remove", owner, str(proj), str(n)], n, "the issue is a run parent"):
            del have_all[str(n)]
            say(f"BOARD-RECONCILE #{n}: card removed (run-parent: a roll-up, never work)")
    if shipped:   # an open issue whose card reads Closed goes back to Todo, once: the reopen is the door (ADR-0143 rule 2, #1420)
        for n in sorted(k for k in olabels if str(k) in have_all and have_all[str(k)] == "Closed" and not (RUN_PARENT in olabels[k])):
            if n in held_owner or owner_frozen(n): continue
            if n not in specs and (branch_in_dev_count(n) != "0" or str(n) in named_in_land()): continue   # its work is in dev: Closed is the landing's own write (rule above), so Todo would open a dispatch window and the later rules would cycle the card. A spec's own work is never a branch, so a spec never takes this skip (#1431)
            _is.pop(n, None); _is.pop(str(n), None)   # the state is read again before the write (#775), and that read is live (#1999): the open list is a snapshot and the facts file a cache, so a close GitHub already reported never sends a landed card back
            if issue_info(n, fresh=True)[0] != "open": continue   # only a fresh open answer reopens the card: a close, a refusal or any other answer leaves it in Closed
            say(f"TERMINAL-REOPENED #{n}: the card sits in Closed but the issue is open again; no Closed comment is queued; card -> Todo (reopened)")
            if board_write([owner, str(proj), "Todo", str(n)], n, "the issue is open again", "reopened", f"reopen-{n}"):
                have_all[str(n)] = "Todo"; expected.pop(n, None)
                say(f"BOARD-RECONCILE #{n}: card -> Todo (the issue is open again)")
    for n, (_, want, why, run_id, run_ts) in sorted(expected.items(), key=lambda kv: int(kv[0])):
        have = have_all.get(str(n))
        note = None
        if shipped and want in TERMINAL and have != want:   # the guard (#245): no terminal write without the comment that states its condition
            note = terminal_comment(want, terminal_facts(n, want))
            if note is None:
                say(f"TERMINAL-NO-CONDITION #{n}: the condition of {want} could not be read; card not moved ({why})")
                continue
        if shipped and canonical(want) in {canonical(x) for x in (doing, REVIEWING, landing, GATING)} and past(have, want) and n not in specs: want = have   # a card only moves forward (ADR-0143, #603, #1430): a live run, a review or a gate never moves it back, and a card in Testing leaves it only for Closed (ADR-0170). A spec card carries no delivery of its own, so the fold corrects it (#665)
        if have is None:   # an issue created while the loop runs has no card until this pass adds one, with its status, in one write (#86)
            if board_write(["--add", owner, str(proj), want, str(n)], n, why): say(f"BOARD-RECONCILE #{n}: card added -> {want} ({why})")
            else: continue
            if note: queue_terminal(n, want, note)
        elif canonical(have) != canonical(want) and board_write([owner, str(proj), want, str(n)], n, why, "no-landing" if n in no_landing else None, run_id):   # either spelling of a column is that column (ADR-0170): a card the legacy board spells Code Complete never reads as a move to Batching
            say(f"BOARD-RECONCILE #{n}: card -> {want} ({why})")
            if note: queue_terminal(n, want, note)
        if want == doing:
            held = (holders(proj) or {}).get(str(n))
            if run_id and held != {"run": run_id, "started": run_ts or ""}:   # only a holder or a start the card does not carry is written (#327)
                if board_write(["--run-fields", owner, str(proj), str(n), run_id, run_ts], n, f"write Run fields for {why}") and holders(proj) is not None: holders(proj)[str(n)] = {"run": run_id, "started": run_ts or ""}   # every pass: the field names the run that holds the card now; a card no run holds (a spec folded to Doing) gets no Run value
        elif canonical(have) in {canonical(x) for x in ("In Progress", "Doing", REVIEWING)} and canonical(have) != canonical(want):
            board_write(["--clear-run", owner, str(proj), str(n)], n, f"clear Run fields, the card left {have} for {want}")   # only on the way out, so a card already past In Progress is not written again every cycle
    for n, want in sorted(released.items(), key=lambda kv: int(kv[0])):   # a release applies once: its record goes when the card carries the column
        if cards(proj)[1].get(str(n)) == want and not dry:
            HH.clear_release(n, state=st)
            say(f"HAND-RELEASE #{n}: the card carries {want}; the release record is applied and removed")
    for n in sorted(frozen, key=int):   # the owner freeze is a label on the issue, written once; the owner removes it, the pass never does
        if expected.get(n, (0, ""))[1] in ("Closed", "Abandoned") or FREEZE_LABEL["owner"] in issue_labels(n): continue
        say(f"BOARD-FREEZE #{n}: {FREEZE_LABEL['owner']} (an owner label or a run needing a human holds it)")
        if not dry: _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_send", "--method", "POST", f"repos/{repo0}/issues/{n}/labels", "-f", f"labels[]={FREEZE_LABEL['owner']}"])   # through the door's write (ADR-0277)
    if not shipped: return
    # A card that settled in a terminal column before the rule (#245) gets its comment once: a bounded number per pass, one
    # comments read each, and the marker keeps a card from being read again.
    budget = int(os.environ.get("JANITOR_TERMINAL_BACKFILL", "5") or 0)
    for n, have in sorted(have_all.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if budget <= 0: break
        if have not in TERMINAL or not str(n).isdigit() or (st / "auto" / f"janitor-terminal-{n}").exists(): continue
        # A condition the read does not state was read again every pass, and every pass logged the same line (#561): one
        # report and one marker under $SG_STATE remember it. The marker names the column, so a card the owner moves to
        # another terminal column is read once more, where its condition may be stated.
        unread = st / "auto" / f"janitor-terminal-unreadable-{n}"
        try: memo = unread.read_text().strip()
        except OSError: memo = ""
        if memo == have: continue
        if memo and not dry: unread.unlink(missing_ok=True)
        budget -= 1
        ok, seen = door_read(f"repos/{repo0}/issues/{n}/comments?per_page=100", f'[.[] | select(.body | contains("{TERMINAL_TAG}"))] | length')
        if not ok: continue
        if seen not in ("", "0"):
            if not dry: (st / "auto" / f"janitor-terminal-{n}").write_text(have + "\n")
            continue
        facts = terminal_facts(n, have)
        note = terminal_comment(have, facts)
        if note is None:
            reason = (facts or {}).get("reason")   # the read's own answer: a close by decision belongs to Abandoned, and a terminal card is not moved (ADR-0143, #517)
            if not_planned(reason):
                say(f"TERMINAL-NO-CONDITION #{n}: settled in {have}, but the issue is closed as {reason}; its condition is Abandoned, and a terminal card is not moved")
            else:
                say(f"TERMINAL-NO-CONDITION #{n}: settled in {have} with no comment, and its condition could not be read")
            if not dry: unread.write_text(have + "\n")
            continue
        say(f"TERMINAL-BACKFILL #{n}: settled in {have} with no comment; one queued")
        queue_terminal(n, have, note)
reconcile_board()
# an autobug marker of a launch.py refusal too: that refusal makes no run folder, so rule J never sees it (#523)
for d in [*(st / "auto").glob("disp-*"), *(st / "auto").glob("bug-*")]:
    req = marker_text(d)
    if req is None: continue   # #794: a marker gone since the glob holds nothing
    lg = st / f"{req}.log"
    n = d.name.split("-", 1)[1]; lt = lg.read_text() if lg.exists() else ""
    if d.name.startswith("bug-") and "refusing to start" not in lt: continue   # a bug marker of a run that started is rule O's
    refused = "Nothing ran" in lt or "refusing to start" in lt   # K: squad's own refusal, or launch.py's (the model gate, #523)
    mt = marker_mtime(d)
    if mt is None or now - mt < (600 if refused else 1800): continue   # K: a refused start frees the issue after 10 min
    if not (("verdict failed" in lt or refused) and not holding(n)): continue
    repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
    state = sh("gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state").strip()
    if state == "closed":   # a closed issue is never dispatched: its marker only made every pass read its state again (#525)
        say(f"stale {d.name} (issue closed) removed")
        if not dry: d.unlink(missing_ok=True)
        continue
    if state != "open": continue
    if pr_count(n) != "0":   # a work branch exists: the issue is past dispatch, so the marker stands -- and the pass names the issue, so this decision reads apart from a release (#217)
        say(f"DISPATCH-FAILED #{n}: request {req} ended failed, but a work branch names the issue; {d.name} kept so the pass never dispatches it again")
        continue
    say(f"stale {d.name} (request {req} ended failed, issue open, no work branch) removed")
    if not dry: d.unlink(missing_ok=True)
def proof_unfinished(rs):   # #1892: a run whose only reasons are unfinished proofs, and no measured red (ADR-0164)
    return bool(rs) and all(r.startswith(("unproven:", "kept:")) or r == "deadline" for r in rs) and any(r == "deadline" or r.startswith("unproven:") for r in rs)

def count_failed(n, f, i):   # O (#609): a run that ended with nothing delivered for issue n is one failed dispatch, counted
    # by wave.sh fail (one count per issue, keyed by the run, frozen at SG_REQUEUE_CAP), whether or not it still holds a marker.
    # A run the issue's ledger file lists (#673, read through issue_ledger.py, its one module) is not sent again, so a pass
    # costs no call for a run already counted. A corrupt ledger holds the issue (ADR-0149 rule 4): nothing is counted and the
    # hold is named; the pass decides it again once the owner's rearm wrote a fresh file. A ledger module that cannot be
    # loaded sends the call: wave.sh counts a run once anyway.
    # The no-change cause (#2245): a child's land that passed with review_nochange changed no file, so its reasons are
    # unfinished proofs and rule O would count nothing. The trigger is the passing land plus the no-change verdict, read
    # per (child, issue) in kid_nc, so a sibling child's review never marks an issue it does not name. That run counts
    # once, and wave.sh fail stops the issue for the owner at that one run, so a second empty wave starts no new run.
    if i["name"] == "no-repro":   # no work is wrong, the premise is: the issue is the owner's, never a failed dispatch (ADR-0029)
        say(f"NO-REPRO #{n}: run {f.name} found the bug does not reproduce, so the issue is the owner's, not a failed dispatch")
        return True
    nochange = any(n in iss and i["kid_nc"].get(k) for k, iss in i["kid_issue"].items())
    if proof_unfinished(i["reasons"]) and not nochange:
        say(f"UNPROVEN #{n}: run {f.name} ended with its proof unfinished, so it is no failed dispatch ({'; '.join(i['reasons'])})")
        return True
    rec = {}
    try:
        issue_ledger = ledger()
        rec = issue_ledger.read(f"{repo0}#{n}")
        if rec["state"] == "held": say(f"LEDGER-HELD #{n}: {rec['held']}; run {f.name} is not counted"); return False
        if any(x["run"] == f.name for k in issue_ledger.LEDGERS for x in rec[k]["runs"]): return True
        # A rearm's floor (#692): a run the floor names, or one whose verdict is older than the rearm, is not counted again. The
        # name clause stays here alone: the floor names a run the ledger listed at the rearm, and a record may carry no time.
        rm = rec.get("rearmed") or {}
        if f.name in rm.get("runs", []) or rearmed_after(n, i, rec): return True
    except (OSError, ImportError, ValueError, KeyError): pass
    if holding(n): return False   # a live run or a wave still has it: a later pass decides
    st_, pc = issue_info(n)[0], pr_count(n)
    if st_ != "open" or pc != "0": return st_ in ("open", "closed") and pc.isdigit()   # a read that did not answer decides nothing
    # A wave that landed without the child names the cause wave.sh recorded for it (#682: a failed gate, a hold, a conflict, a
    # drop at the tip, the age cap), or "unknown"; a generic text hid the cause and was read as the loop's. The cause is the
    # run's wave fact in the issue's ledger (ADR-0149), keyed by the folder name or the record's run id.
    wv = rec.get("wave") or {}
    ex = next((x.get("cause") for r in (f.name, i["run_id"]) if r for x in [wv.get(r) or {}]
               if x.get("state") not in (None, "entered", "delivered") and x.get("cause")), "unknown")
    why = f"the child of run {i['run_id']} never entered its wave: {ex}" if i["landed"] else f"ended {i['name']}: " + ("; ".join(i["reasons"]) or "nothing landed")
    # The run's own review of the child decides over the reason text (#621): CHANGES or BLOCKED a model wrote on the diff is
    # the issue's own failure (`own`); a BLOCKED the chain wrote because every model failed, a review past its deadline or one that
    # never started, is the loop's (`loop`). No review leaves the text to wave.sh. The child whose land passed with
    # review_nochange decides its own issue (#2245): only that (child, issue) pair takes the no-change kind.
    kind = ""; nc = False
    for k, iss in i["kid_issue"].items():
        if n not in iss: continue
        if i["kid_nc"].get(k): nc = True; continue
        rv = i["kid_review"].get(k)
        if not rv or str(rv.get("verdict") or "").lower() in ("approve", "no_review", ""): continue
        lost = str(rv.get("verdict")) == "deadline" or "every review model failed" in str(rv.get("findings") or "") or str(rv.get("stderr") or "").startswith("review_not_started")
        kind = "own" if not lost or kind == "own" else "loop"
    if nc: kind = "nochange"
    if dry: say(f"FAILED-DISPATCH #{n}: run {f.name} would count ({why}{', ' + kind if kind else ''})"); return False
    tail = [kind, i["end_class"]] if i.get("end_class") else ([kind] if kind else [])
    out = _run(["bash", str(pathlib.Path(__file__).parent / "wave.sh"), "fail", n, f.name, why, *tail])[1].strip()
    if out: say(f"FAILED-DISPATCH #{n}: {out}")
    # A probe's refutation that gh or I/O failed (#888): the run stays uncounted, so the next pass calls `fail` and sends it again.
    return "WAVE PROBE-RETRY" not in out
if not dry:   # a run that left active-runs.txt is read no more, so its read-once marker goes (#693)
    _ck = {f"counted-{folder(r).name}" for r in names}
    for m in (st / "auto").glob("counted-*"):
        if m.name not in _ck: m.unlink(missing_ok=True)
# U (#1586): a dispatch marker whose named run or request is no longer live, and whose issue the owner rearmed after the
# marker was written. A rearm clears the ledger and leaves auto/disp-<n> behind: the run may have left active-runs.txt, so
# rule O never reads it, and its folder may be gone, so rule Q never finds it -- the issue then reads `claimed` on every
# pass and never dispatches again. The rearm is the owner's one act that says the issue is his to settle now, so the marker
# goes and the issue is startable at once. A live run or request, a pending card write (ADR-0165) and an older rearm each
# hold it; auto/taken-* is rule T's own case (ADR-0166) and is never read here.
if repo0:
    for m in sorted((st / "auto").glob("disp-*")):
        n = m.name[5:]; held_by = marker_text(m); mt = marker_mtime(m)
        if not n.isdigit() or held_by is None or mt is None: continue
        if marker_live(held_by): continue
        try: rec = ledger().read(f"{repo0}#{n}")
        except (OSError, ImportError, ValueError, KeyError): continue
        if rec.get("state") == "held": continue
        rm = rec.get("rearmed") or {}
        if not (isinstance(rm.get("ts"), (int, float)) and rm["ts"] > mt): continue
        if (rec.get("board") or {}).get("state") == "pending": continue
        say(f"DISP MARKER DROPPED #{n}: the owner's rearm is newer than run {held_by}")
        if not dry: m.unlink(missing_ok=True)
# X (#2142): a constant stream of the loop's own history was not read by the loop: the auto/ directory held markers of
# issues the loop dispatched and closed, and a marker is a claim no pass released. sg_dispatch_claimed reads only the
# marker's existence (autofrontier.sh), so a reopened issue read as already claimed and the frontier skipped it with no
# line that explains why. The janitor drops the marker of an issue that reads closed; a marker of an open issue stays.
# A live run that works the issue, a taken-over run's marker (ADR-0166) and a pending card write (ADR-0165) each hold it,
# and a state read that did not answer holds it (fail closed). The read is one call per marker, so the drop is bounded
# per pass (JANITOR_CLOSED_MARKER_MAX, default 50): a long backlog drains over a few passes and never spends the shared
# GitHub budget in one burst.
if repo0:
    budget = int(os.environ.get("JANITOR_CLOSED_MARKER_MAX", "50") or 0)
    for m in sorted([*(st / "auto").glob("disp-*"), *(st / "auto").glob("bug-*")], key=lambda p: p.name):
        if budget <= 0: break
        n = m.name.split("-", 1)[1]
        if not n.isdigit() or holding(n) or n in held: continue   # a live run works it, or a taken-over run holds it (ADR-0166)
        try: rec = ledger().read(f"{repo0}#{n}")
        except (OSError, ImportError, ValueError, KeyError): continue   # a ledger that cannot be read holds the marker
        if rec.get("state") == "held" or (rec.get("board") or {}).get("state") == "pending": continue   # ADR-0149 rule 4, ADR-0165
        budget -= 1
        if issue_read(n, fresh=True)[0] != "closed": continue   # open, gone, or no answer: the marker stands. A read that decides a write asks GitHub itself (#1999)
        say(f"DISP MARKER DROPPED #{n}: its issue is closed, so auto/{m.name} released (a reopen dispatches again)")
        if not dry: m.unlink(missing_ok=True)
def stop_of(n):   # why the issue is stopped, or "": its ledger stopped it (WAVE STOP, needs-human, a held file) or the owner froze it (#1958). wave.sh held_out reads the same facts
    try:
        rec = ledger().read(f"{repo0}#{n}")
        if rec.get("state") in ("frozen", "needs-human"): return f"its ledger is stopped: {rec['state']}"
        if rec.get("state") == "held": return f"its ledger is held: {rec.get('held') or 'unreadable'}"
    except (OSError, ImportError, ValueError, KeyError): pass
    labs = issue_labels(n) & {"frozen:owner", "needs-owner"}
    return f"it carries {' '.join(sorted(labs))}" if labs else ""
if owner and proj and repo0:
    o_runs = list(names)   # the listed runs, then every run whose request names a dispatch marker's issue: the janitor prunes a finished run's line, and its marker still claims the issue (#2127)
    o_seen = {folder(r) for r in names}
    by_issue = {}   # a frontier marker holds its request name, never its run folder, so the run is found by the issue it names
    for rf in (co / "runs").glob("*") if (co / "runs").is_dir() else []:
        for n in req_issues(rf): by_issue.setdefault(n, []).append(rf)
    for dm in sorted((st / "auto").glob("disp-*")):
        for rf in by_issue.get(dm.name[5:], []):
            if rf not in o_seen: o_seen.add(rf); o_runs.append(str(rf))
    for r in o_runs:   # O:a run that ended without landing releases its issues: marker dropped (whatever the verdict was: #1847), card back where the dispatch found it
        f = folder(r); i = read(f)
        if (st / "auto" / f"taken-{f.name}").exists(): continue   # a taken-over run's markers stand until its hand fix clears the taken marker (ADR-0166)
        if not (i and i["verdict"] and i["idle"] > 180): continue   # a run still moving keeps its marker
        req = f / "request.txt"
        nums = issues_named(req.read_text()) if req.exists() else set()
        if i["landed"]: nums = {n for n in nums if wave_entry(n, f, i) == "out"}   # a run that landed keeps its markers, but for an issue its landed wave never carried (#467)
        # A child that entered the run's wave is the wave's to decide (wave.sh requeues or stops it), landed run or not: a failed
        # run's local-only landing reads as no landing (#691), so the filter above no longer holds such an issue out of the count.
        cnt = {n for n in nums if wave_entry(n, f, i) != "entered"}
        # A run folder is read once per verdict (#693): auto/counted-<run> holds the verdict whose issues were all decided, so a
        # later pass sends nothing again for a run a rearm cleared from the ledger. A resume's new verdict reads it again.
        cm = st / "auto" / f"counted-{f.name}"
        try: done_v = cm.read_text().strip()
        except OSError: done_v = None
        if i["name"] != "needs-human" and done_v != i["vkey"]:   # needs-human is the owner's already (ADR-0029)
            decided = [count_failed(n, f, i) for n in sorted(cnt, key=int)]
            if all(decided) and not dry: cm.write_text(i["vkey"] + "\n")
        marks ={n: [k for k in ("disp", "bug") if owns(f, i, st / "auto" / f"{k}-{n}")] for n in nums}   # only the markers this run's request wrote (#539)
        marks = {n: m for n, m in marks.items() if m}
        if not marks: continue
        mk = st / "auto" / f"janitor-demoted-{f.name[-6:]}"
        if mk.exists(): continue
        # The once-only marker is written after the decision, and only when no issue was skipped for a reason that passes (a live
        # run on it, a state read that did not answer): written first, one such skip held the issue for good (#534).
        again = False
        why = "; ".join(i["reasons"]) or "no reason recorded"
        for n in sorted(marks, key=int):
            if holding(n):   # a live run works it, or a wave still carries it: not ours to release, not yet
                if n in delivering and n not in byissue and not issue_closed(n):   # observability only (#1423, ADR-0166); a closed issue is named never (#1566)
                    stop = stop_of(n)   # a stopped issue is the owner's, whatever its wave does: the line names the stop, never a hold the landing pass owns (#1958)
                    if stop: say(f"HELD-STOPPED #{n}: run {f.name} ended {i['name']}, its wave is open, and the issue is stopped ({stop}); no pass dispatches it until the owner rearms it")
                    else: say(f"HELD-BY-WAVE #{n}: run {f.name} ended {i['name']}, its wave is open; the landing pass owns the issue")
                again = True; continue
            ist = sh("gh", "api", f"repos/{repo0}/issues/{n}", "-q", ".state").strip()
            if ist != "open": again = again or ist != "closed"; continue
            pc = pr_count(n)
            if pc != "0": again = again or not pc.isdigit(); continue   # a work branch names it: the issue is past dispatch (a refused read: not yet decided)
            moves = []   # the loop-board card is rule R's (one owner, #91): O reads the column R derived and writes none of its own; the marker is dropped either way, nothing must hold a finished run's issue back
            if bproj and "bug" in marks[n] and card_status(bproj, n) == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])   # only the column the dispatch set: never downgrade a card another step moved past it
            mks = " and ".join("auto/" + k + "-" + n for k in marks[n])
            col = expected.get(n)
            park = card_status(proj, n) == "Backlog"   # the owner parked the card: R derived no Todo for it (#2007), and no pass dispatches a Backlog card (ADR-0142)
            where = (f"its card is {col[1]}" if col else "its card keeps its column") + (f" (bug card back in {moves[-1][1]})" if moves else "") + " and"
            kept = unpushed(f)   # #771: the loop pushes no work (#361), so the release names a child's unpushed commits for salvage
            salv = f"; its child left commits on no remote branch, kept for salvage: {', '.join(kept)}" if kept else ""
            stop = stop_of(n)   # the count above may have just stopped the issue (WAVE STOP): the line names the stop, never a dispatch that will not come (#1958)
            nr = i["name"] == "no-repro"   # the bug did not reproduce: the evidence is this comment and the owner decides the issue (ADR-0029)
            nxt = (f"; its card sits in Backlog, which only the owner leaves, so no pass dispatches it until the owner moves it to Todo" if park
                   else "; the bug did not reproduce, so the issue is the owner's under frozen:owner" if nr
                   else f"; the issue is stopped ({stop}), so no pass dispatches it until the owner rearms it" if stop else " so it dispatches again")
            say(f"DISPATCH-FAILED #{n}: run {f.name} ended {i['name']} ({why}), nothing landed; {where} {mks} marker dropped{nxt}{salv}")
            if not dry:
                for p, s in moves: board_write([owner, str(p), s, str(n)], n, f"bug card back in {s} after run {f.name} ended {i['name']}")
                for k in marks[n]: (st / "auto" / f"{k}-{n}").unlink(missing_ok=True)
                if kept: (st / "auto" / f"salvage-{n}").write_text("".join(k + "\n" for k in kept))   # the next dispatch names it (startability.sh)
                (st / "cq").mkdir(exist_ok=True)
                (st / "cq" / f"{CQ}#{n}-dispatchfail-{os.getpid()}.md").write_text(
                    f"Run {f.name} ended without a landed change (verdict {i['name']}: {why}). {where[:1].upper() + where[1:]} its {mks} marker dropped" + (". Its card sits in Backlog, which only the owner leaves, so no pass dispatches it until the owner moves it to Todo." if park else ". The bug did not reproduce, so no work is wrong: the issue is the owner's under frozen:owner, and no pass dispatches it until the owner rearms it." if nr else f". The issue is stopped ({stop}), so no pass dispatches it until the owner rearms it." if stop else ", so the next cycle dispatches the issue again.") +(f" The child left commits on no remote branch, kept for salvage: {', '.join(kept)}. The loop flags them and never pushes them (#361); the next dispatch of this issue names them, so the new run can reuse them." if kept else "") + "\n")
        if not again and not dry: mk.touch()
    # O (#888): a probe's refutation that failed for a transient cause leaves auto/probe-retry-<n>. Rule O above never reads a
    # child that entered its wave, so this sweep sends it again: once a pass, for an open probe frozen for the owner, read from
    # the pass's one open-issue list (no read per issue). A marker this pass wrote waits for the next. A closed probe, or one
    # the owner unfroze, drops its marker; a list that did not answer decides nothing.
    _pr = [m for m in (st / "auto").glob("probe-retry-*") if re.fullmatch(r"probe-retry-\d+", m.name) and (marker_mtime(m) or now) < now]
    if _pr and not dry:
        oans, olabels = open_issues()
        for m in sorted(_pr, key=lambda p: int(p.name.rsplit("-", 1)[1])) if oans else []:
            n = m.name.rsplit("-", 1)[1]
            if not {"probe", "frozen:owner"} <= olabels.get(n, set()):
                say(f"PROBE-RETRY #{n}: no longer an open probe frozen for the owner; its retry marker dropped"); m.unlink(missing_ok=True); continue
            out = _run(["bash", str(pathlib.Path(__file__).parent / "wave.sh"), "probe-retry", n])[1].strip()
            if out: say(f"PROBE-RETRY #{n}: {out}")
    # O: a missing child-worktree run has no record to read, but its marker still identifies the issue it claimed.
    for r, f in missing:
        marks = {}
        for p in (st / "auto").glob("*"):
            mo = re.fullmatch(r"(disp|bug)-(\d+)", p.name)
            if mo and marker_text(p) in (r, f.name): marks.setdefault(mo.group(2), []).append((mo.group(1), p))
        for n in sorted(marks, key=int):
            if holding(n) or issue_info(n)[0] != "open" or pr_count(n) != "0": continue
            mks = " and ".join("auto/" + kind + "-" + n for kind, _ in marks[n])
            say(f"DISPATCH-MISSING #{n}: run {f.name} folder missing; {mks} marker dropped so it dispatches again")
            if not dry:
                for _, p in marks[n]: p.unlink(missing_ok=True)
                (st / "auto" / f"reoffer-{n}").write_text(f.name + "\n")   # the card goes back to Todo, not left in Doing with no marker (#1034)
    # Q: a marker and an In Progress card with no run behind them. Only a definite answer counts: a refused read (not "open", not a digit, no status)
    # touches nothing and leaves no once-only marker, so the next pass decides. A definite "no" is remembered for 10 min, so a landed issue's old marker is not re-read every pass.
    qm = {}
    for k in ("disp", "bug"):
        for m in (st / "auto").glob(k + "-*"):
            if m.name[len(k) + 1:].isdigit(): qm.setdefault(m.name[len(k) + 1:], {})[k] = m
    for n, ms0 in freed_markers.items():
        qm.setdefault(n, {}).update(ms0)
    for n, ms in sorted(qm.items(), key=lambda kv: int(kv[0])):
        if n in held or n in landed_n or n in delivering or any(mt is not None and now - mt < 600 for mt in (marker_mtime(m) for m in ms.values() if m is not None)) or list((st / "auto").glob(f"fork-{n}-*")): continue
        seen = st / "auto" / f"janitor-q-seen-{n}"
        smt = marker_mtime(seen)   # #798: the record can vanish between a test and a stat: a gone record is none
        if smt is not None and now - smt < 600: continue
        def no():   # a definite answer that says "not ours": remember it
            if not dry: seen.touch()
        state = issue_info(n)[0]
        if state not in ("open", "closed"): continue
        if state == "closed": no(); continue
        c = pr_count(n)
        if not c.isdigit(): continue
        if c != "0": no(); continue
        cs = card_status(proj, n); bs = card_status(bproj, n) if bproj and "bug" in ms else ""
        if not cs and not bs: continue
        moves = [[proj, os.environ.get("SG_TODO", "Todo")]] if cs in ("In Progress", "Doing") else []
        if bs == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])
        mks = " and ".join("auto/" + k + "-" + n for k in sorted(ms))
        if not moves and (cs == os.environ.get("SG_TODO", "Todo") or bs == os.environ.get("SG_BUG_TRIAGE", "To triage")):
            # #771: the card is back where a dispatch takes it (rule R put a failed run's card in Todo), but the marker of a run
            # that left active-runs before rule O released it still stands, so the issue read as claimed for good. No run holds
            # it and no work branch names it: the marker goes, the card is not touched.
            say(f"STALE-CLAIM #{n}: {mks} stands behind a card in {cs or bs}, no run works it, no work branch names it; marker dropped so it dispatches again")
            if not dry:
                for m in ms.values():
                    if m is not None: m.unlink(missing_ok=True)
            continue
        if not moves: no(); continue
        say(f"STALE-CARD #{n}: card In Progress behind {mks}, no run works it, no work branch names it; card -> {', '.join(s for _, s in moves)}, marker dropped so it dispatches again")
        if not dry:
            for p, s in moves: board_write([owner, str(p), s, str(n)], n, f"stale card back in {s}")
            for m in ms.values():
                if m is not None: m.unlink(missing_ok=True)
            (st / "cq").mkdir(exist_ok=True)
            (st / "cq" / f"{CQ}#{n}-stalecard-{os.getpid()}.md").write_text(
                f"Its card was In Progress behind {mks}, and no run works this issue (no live run, none in its launch window; the run that took it ended without releasing it). The card is back in {moves[-1][1]} and the marker is dropped, so the next cycle dispatches the issue again.\n")
# Q has read them: what Q left alone (no card, a card past In Progress, a definite no about the issue) is dropped now, so
# autofrontier/autobug dispatch those issues again. The marker Q released is already gone, and its card is back.
left = [p for p in pending if p.exists()]
if left:
    say("reaped runs: markers dropped after the card pass: " + " ".join(p.name for p in left))
    if not dry:
        for p in left: p.unlink(missing_ok=True)
fd = os.environ.get("SG_FORK_DIR"); fb = os.environ.get("SG_FORK_BASE")
if fd and fb:
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from board_columns import canonical, REVIEWING, batching_column   # rule P resolves a column's spellings before a compare (#2151)
    landing = batching_column(os.environ.get("SG_BOARD_VOCAB") == "shipped")   # the vocabulary's own column, never an alias (#2190)
    repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
    for l in sh("git", "-C", fd, "log", "-n", "200", "--format=%h %ct %s", fb).splitlines():
        h, ct, subj = (l.split(None, 2) + ["", ""])[:3]
        m = re.search(r"\(#(\d+)\)", subj)
        if not (m and ct.isdigit() and now - int(ct) > 1800): continue
        n = m.group(1); mk = st / "auto" / f"janitor-fixed-{n}-{h}"
        if mk.exists(): continue
        state = sh("gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state").strip()
        if state == "closed" and not dry: mk.touch()   # settled: the next pass never reads this issue again (#525)
        if state != "open": continue
        if sh("gh", "api", "-X", "GET", "search/issues", "-f", f"q=repo:{repo} is:pr is:open \"Refs #{n}\"", "-q", ".total_count").strip() != "0": continue   # -X GET is load-bearing here too: a bare `-f` POSTs, and /search/issues answers Not Found (#1981)
        say(f"FIXED-NOT-CLOSED #{n}: fork commit {h} \"{subj[:60]}\" names it, the issue is still open")
        if not dry: mk.touch()
    # P: a `fork-<n>-<sha9>` marker is the close pass's claim that it merged that commit into the base. An unwound
    # landing -- a hand revert, a branch reset, a base rebuilt under it -- leaves the marker claiming a delivery the
    # base does not have, and rule O does not own it (its run did not fail, it landed). No close pass can restore it
    # either: that pass merges a branch ahead of the base, and here there is nothing left to merge. So the issue goes
    # back to the column the dispatch reads and its markers are dropped (#1830).
    if owner and proj and repo0:
        def gitrc(*a): return rc("git", "-C", fd, *a)
        for mk in sorted((st / "auto").glob("fork-*")):
            m = re.fullmatch(r"fork-(\d+)-([0-9a-f]{7,40})", mk.name)
            if not m: continue
            n, sha = m.group(1), m.group(2)
            if fork_verdict(n, sha) != "unwound": continue   # honest (the base holds the commit, or its change), ahead (a fix commit is coming and the close retries on the head), or unreadable: not ours
            br = (os.environ.get("SG_FORK_BRANCH") or "issue/%n").replace("%n", n)
            if holding(n): continue                          # a live run works it, or a wave still carries it: not ours to release
            if sh("gh", "api", f"repos/{repo0}/issues/{n}", "-q", ".state").strip() != "open": continue
            if pr_count(n) != "0": continue   # a work branch names it: the issue is past dispatch
            if canonical(card_status(proj, n)) not in {canonical(x) for x in ("In Progress", "Doing", REVIEWING, landing)}: continue   # only what the landing set: a card another step moved past it is never downgraded
            km = st / "auto" / f"janitor-unwound-{n}-{sha[:9]}"
            if km.exists(): continue
            marks = [mk.name] + [f"{k}-{n}" for k in ("disp", "bug") if (st / "auto" / f"{k}-{n}").exists()]
            moves = [[proj, os.environ.get("SG_TODO", "Todo")]]
            if bproj and "bug-" + n in marks and card_status(bproj, n) == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])
            say(f"UNWOUND #{n}: {mk.name} claims {sha[:9]}, which {fb} no longer holds and {br} has nothing left to merge; card -> {', '.join(s for _, s in moves)}, {'+'.join(marks)} dropped so it dispatches again")
            if not dry:
                km.touch()
                for p, s in moves: board_write([owner, str(p), s, str(n)], n, f"unwound landing, card back in {s}")
                for k in marks:
                    f2 = st / "auto" / k
                    if f2.exists(): f2.unlink()
                (st / "cq").mkdir(exist_ok=True)
                (st / "cq" / f"{CQ}#{n}-unwound-{os.getpid()}.md").write_text(
                    f"UNWOUND #{n}: the close pass recorded a merge of {sha[:9]} into {fb}, and {fb} no longer holds that change ({br} has nothing left ahead of it either). The landing was undone, so the issue is back in {moves[-1][1]} with its {' and '.join('auto/' + k for k in marks)} marker(s) dropped and the next cycle builds it again.\n")
repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
hb = pathlib.Path(os.environ.get("SG_HUB_DIR") or co / "runs" / ".hub") / "hub-board.json"; hm = st / "auto" / "janitor-h-last"
if hb.exists() and (not hm.exists() or now - hm.stat().st_mtime > 600):
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import ghthrottle
    from board_columns import canonical, REVIEWING, batching_column   # the comment pass resolves a column's spellings before a compare (#2151)
    landing = batching_column(os.environ.get("SG_BOARD_VOCAB") == "shipped")   # the vocabulary's own column, never an alias (#2190)
    floor = int(os.environ.get("SG_GH_FLOOR") or 300)
    low = next(((pool, left) for pool in ("core", "graphql") if (left := ghthrottle.pool_below(pool, floor)) is not None), None)   # GraphQL has a quota of its own (#1648)
    if low is not None:
        say(f"skipped comment pass: {low[0]} quota {low[1]} is under SG_GH_FLOOR {floor}; the next pass reads again")
    elif not dry: hm.touch()
    try: items = json.loads(hb.read_text()).get("items", [])
    except Exception: items = []
    for it in (items if low is None else []):
        n = it.get("n"); stt = it.get("st"); op = it.get("s") == "open"
        if not (isinstance(n, int) and n >= 1102): continue
        if stt in ("Done", "Closed") and op:
            want = expected.get(str(n), (0, "", "", "", ""))[1]
            if want not in ("Done", "Closed"):
                mk = st / "auto" / f"janitor-regress-{n}-done-open"
                if not mk.exists():
                    repair = f"; repaired to {want}" if want else ""
                    say(f"STATE-REGRESSION #{n}: card is Done but the issue is still open{repair}")
                    if not dry: mk.touch()
            continue
        if not (op and canonical(stt) in {canonical(x) for x in ("Todo", "Blocked", "In Progress", "Doing", REVIEWING, landing)}): continue
        os.environ["SG_GH_PASS"] = "janitor"
        r, comments = door("gh_pages", f"repos/{repo}/issues/{n}/comments?per_page=100", "-q", r'.[]|[.created_at,(.body|gsub("\\n";" "))]|join("\t")')
        cm = [x.split("\t", 1) for x in comments.splitlines() if "\t" in x] if r == 0 else []
        reds = 0
        for _, b in cm:
            if "the fork suite failed after merging" in b: reds += 1
            elif re.search(r"Fork half merged:.*fork suite green|^Done \(fork-only work", b): reds = 0
        if reds >= 3:
            mk = st / "auto" / f"janitor-redloop-{n}-{reds}"
            if not mk.exists():
                say(f"RED-LOOP #{n}: {reds} fork-suite REDs with no green fork close since; the loop is not converging (NEEDS OWNER)")
                if not dry: mk.touch()
        reach = [t for t, b in cm if re.search(r"Card -> Code Complete|merged automatically|Fork half merged:|^Done \(fork-only work", b)]
        if stt in ("Todo", "Blocked") and reach:
            mk = st / "auto" / f"janitor-regress-{n}-{stt}"
            if not mk.exists():
                say(f"STATE-REGRESSION #{n}: card is {stt} but its history reached Code Complete at {reach[-1]}")
                if not dry: mk.touch()
def frontier_idle():   # N (#744): a frontier that keeps Todo cards and starts none says so once past a bound
    """The streak autofrontier.sh leaves in $SG_STATE/frontier-idle: `<epoch> TAB FRONTIER idle: ...`. Past the bound, and
    once per bound, one alert names the Todo count and the reasons; the frontier drops the file when it dispatches.
    A frontier whose every unstartable reason is `no slot` is at its cap, which is backpressure and not a stuck loop: the
    alert says which of the two it is, so a full queue never reads as a call for the owner."""
    bound = int(os.environ.get("SG_FRONTIER_IDLE_SECONDS") or 3600)
    f, mk = st / "frontier-idle", st / "frontier-idle-alert"
    try: since, _, line = f.read_text().partition("\t"); since = float(since)
    except (OSError, ValueError): return
    if not line.startswith("FRONTIER idle:") or now - since < bound: return
    try: last = float(mk.read_text())
    except (OSError, ValueError): last = 0.0
    if now - last < bound: return
    if dry: return
    m = re.search(r"0 startable \(([^)]*)\)", line)
    why = [p.strip() for p in (m.group(1).split(",") if m else []) if p.strip()]
    held = [p for p in why if p.split()[:1] not in (["0"], [])]   # a reason at zero is no reason
    full = bool(held) and all(re.fullmatch(r"\d+ no slot", p) for p in held)
    tail = ("every run slot is busy, so this is backpressure and no owner action is needed" if full
            else "the frontier holds this work and starts none; NEEDS OWNER")
    alert(f"FRONTIER-IDLE {int((now - since) // 60)} min: {line} ({tail})")
    mk.write_text(f"{now}\n")
def _deadlock():   # the deadlock rules (#1983), loaded by path: beside this script first, then the repository tree (#2237).
    # -I keeps the script's directory off sys.path, so the import names the file itself; a host install whose scripts dir
    # carries no copy of every sibling still reads the rules from the tree that SG_SQUAD, SG_CHECKOUT or cwd names.
    if hasattr(sys.modules.get("deadlock"), "audit"): return sys.modules["deadlock"]   # never a foreign stub another loader left
    here = pathlib.Path(__file__).resolve().parent
    roots = [r for r in (str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]
    dirs = [here, *(pathlib.Path(r) / "contrib" / "squad-goal" / "scripts" for r in roots)]
    path = next((d / "deadlock.py" for d in dirs if (d / "deadlock.py").is_file()), here / "deadlock.py")
    spec = importlib.util.spec_from_file_location("deadlock", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["deadlock"] = mod
    spec.loader.exec_module(mod)
    return mod
def deadlock_audit():   # #1983: the open issues' dependency graph, checked on the reads this pass already holds (no call); one alert per cause, and no dispatch is held
    answered, _ = open_issues()
    if not answered or dry: return
    dl = _deadlock()
    path = st / "deadlock-causes.json"
    try: seen = set(json.loads(path.read_text()))
    except Exception: seen = set()
    items = [dict(i, repo=i.get("repo") or repo0) for i in _items if isinstance(i, dict) and isinstance(i.get("number"), int) and not i.get("pull_request")]
    causes = dl.audit(items)
    for c in dl.new_causes(causes, seen): alert(f"DEADLOCK {c['status']}: {c['reason']}")
    path.write_text(json.dumps(sorted(c["cause"] for c in causes)))
def _specstall():   # the spec rules beside this script (#1962); -I keeps the script's directory off sys.path, so the import names it itself
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import specstall
    return specstall
def spec_bound():   # janitor.spec_stall_seconds of the profile (#1962); the plugin default when the project file cannot be read
    root = os.environ.get("SG_SQUAD") or str(pathlib.Path(__file__).resolve().parents[3])
    r, out = _run(["python3", "-m", "squad.project_settings", "spec-stall", str(co)], env={**os.environ, "PYTHONPATH": root})
    return int(out) if r == 0 and out.strip().isdigit() else 86400
def spec_stall():   # #1962: a spec whose open tickets made no dev commit for a bound names them, one alert per bound
    bound = spec_bound()
    answered, _ = open_issues()
    r, out = _run(["git", "-C", str(co), "log", "--format=%ct\t%s", f"origin/{os.environ.get('SG_LAND_BRANCH') or 'dev'}"])
    if not answered or r != 0: return
    sp = _specstall()
    items = [i for i in _items if isinstance(i.get("number"), int) and not i.get("pull_request")]
    commits = [(int(ts), subj) for ts, _, subj in (l.partition("\t") for l in out.splitlines()) if ts.isdigit()]
    created = {i["number"]: t for i in items if (t := stamp_epoch(i.get("created_at"))) is not None}
    stuck = sp.stalled(sp.kids_of(items), {i["number"] for i in items}, sp.progress_of(commits), created, now, bound)
    if not stuck or dry: return
    _, cols = cards(proj) if proj else (False, {})
    cols = {int(k): v for k, v in cols.items() if str(k).isdigit()}
    stuck = sp.started_specs(stuck, cols)   # ADR-0142 clause 5: a spec the owner has not moved to Todo is no call for him (#2002)
    if not stuck: return
    tickets = {t for ts in stuck.values() for t in ts}
    holds = {t: holding(t) for t in tickets}
    standing = {int(k): "diverged" for k, bs in work_branches().items() if any(s == "diverged" for _, s in bs)}
    marker = {t for t in tickets if (st / "auto" / f"disp-{t}").exists()}
    for spec_n, ts in sorted(stuck.items()):
        mk = st / f"spec-stall-{spec_n}"
        try: last = float(mk.read_text())
        except (OSError, ValueError): last = 0.0
        if now - last < bound: continue
        parts = [f"#{t} {sp.state(t, cols, holds, standing, marker)}" for t in sorted(ts)]
        alert(f"SPEC-STALL #{spec_n}: no dev progress for {bound // 3600} h; stuck tickets: {'; '.join(parts)} (NEEDS OWNER)")
        mk.write_text(f"{now}\n")
def watchd_orphan():   # W (#959): watchd.pid names no live supervisor and a lane of this scripts dir still runs; one alert per episode
    here = os.path.dirname(os.path.abspath(__file__)); mk = st / "watchd-orphan-alert"
    try: sup = int((st / "watchd.pid").read_text().strip())
    except (OSError, ValueError): sup = None
    live = sup in procs and f"{here}/watchd.sh" in procs[sup][1].split() and "run" in procs[sup][1].split()
    lanes = [f"{s} pid {p}" for p, (pp, cmd) in procs.items() if pp == 1 for s in ("watch.sh", "fastloop.sh", "cqflush.sh") if f"{here}/{s}" in cmd.split()]
    if live or not lanes:
        if not dry: mk.unlink(missing_ok=True)
        return
    if dry or mk.exists(): return
    alert(f"WATCHD-ORPHAN: watchd.pid names no live supervisor (pid {sup or 'none'}), and {', '.join(lanes)} still runs (NEEDS OWNER)")
    mk.write_text(f"{now}\n")
def live_noise(rel):   # a path no publish makes: a Python bytecode cache, a compiled file, or an editor's .DS_Store (#661)
    parts = rel.split("/")
    return "__pycache__" in parts or rel.endswith((".pyc", ".pyo")) or parts[-1] == ".DS_Store"
def live_mtime():   # M (#659): a change to the live skill copy that no publish made
    live = pathlib.Path(os.environ.get("SG_LIVE_DIR") or pathlib.Path.home() / ".claude" / "skills" / "squad-goal")
    snap, stamp = st / "live-mtime.json", st / "live-publish.stamp"
    if not live.is_dir(): return
    cur = {}
    for p in live.rglob("*"):
        try:
            if p.is_file() and not live_noise(str(p.relative_to(live))): cur[str(p.relative_to(live))] = p.stat().st_mtime
        except OSError: pass
    try: old = json.loads(snap.read_text())
    except Exception: old = None
    if not dry:
        tmp = snap.with_name(snap.name + f".{os.getpid()}"); tmp.write_text(json.dumps({"at": now, "files": cur})); os.replace(tmp, snap)
    if not isinstance(old, dict) or not isinstance(old.get("files"), dict): return
    if stamp.exists() and stamp.stat().st_mtime >= old.get("at", 0): return
    pub = live / "PUBLISHED"   # every publish writes it, a hand publish too, which leaves no stamp in the state dir (#700, #727)
    if pub.exists() and pub.stat().st_mtime >= old.get("at", 0): return
    files = {k: v for k, v in old["files"].items() if not live_noise(k)}   # a cache entry kept by an old snapshot is no removal either
    changed = sorted(k for k in set(cur) | set(files) if cur.get(k) != files.get(k))
    if changed:
        alert(f"LIVE-MTIME {live}: {len(changed)} file(s) changed with no publish: {', '.join(changed[:5])}")
frontier_idle()
spec_stall()
deadlock_audit()
watchd_orphan()
live_mtime()
if acts and not dry:
    kinds = {}
    for a in acts: k = a.split()[0] if not a.startswith("kill") else ("kill " + ("dashboard" if "dashboard" in a else "leftover")); kinds[k] = kinds.get(k, 0) + 1
    print("JANITOR " + ", ".join(f"{v} {k}" for k, v in kinds.items()) + " (details in cycle.log)", flush=True)
note(f"pass {int(time.monotonic() - T0)}s: {CALLS[0]} calls")   # one count line a pass (#1774): a slow pass names its calls
