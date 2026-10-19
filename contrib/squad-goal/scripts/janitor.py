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
 C  zombie run (no verdict, record.jsonl idle 20 min, no process inside its folder, last started call dead): drop it from active-runs.txt, write the killed verdict into its record (squad.killed), and free its dispatch markers and leases.
 D  two live runs that accepted the same issue: report once (never kills).
 E  auto/disp-<n> older than 30 min whose dispatching request log says `verdict failed`: drop it so the frontier can dispatch the issue again.
 F  an open issue named "(#N)" in the subject of a fork commit older than 30 min, with no open work branch naming it: report FIXED-NOT-CLOSED once (never closes: closing needs evidence).
 I  a live run whose accepted issues are ALL closed: when every one was closed as not_planned or is titled "[duplicate…", stop it (kill its process tree and its last call);
    otherwise report RUN-ON-CLOSED-ISSUES once and leave it (a closed issue can still have a finishing fix/land run). Once per run.
 J  a run with no record.jsonl 5 min after its request (the squad refused to start): kill its dashboard, drop it from active-runs, and free every issue named in its request (auto/bug-<n> and auto/disp-<n>) so autobug/autofrontier can dispatch again. Comment once ("run X never started, dispatching again", attempt n of 2) quoting the last lines of the run's launch.log. After 2 never-started runs for the same issue, keep the marker, comment the evidence, raise HEALTH, and do not retry a third time.
 K  a disp-<n> marker whose request log says `Nothing ran` or `refusing to start` (10 min old, no live run on the issue) is dropped like rule E does for `verdict failed`.
 O  a run that recorded a verdict and landed nothing (no `land` pass in its record, that event being the landing stage's own -- a router that passed by asking for children is not a landing): the issue's marker is dropped, so autofrontier/autobug dispatch it again. `failed` is not the only way a run ends without landing (#1847): a run that ended any other way (`needs-human`, `pass`) left its auto/disp-<n> behind, autofrontier.sh skips every issue holding that marker, and the frontier starved on a run that is over -- the cause health.sh reports as frontier-idle.
    A run that landed a change, an issue a live run works or a pending wave carries, and an issue with an open work branch keeps its marker; a run drops only a marker its own request wrote (#539). The loop-board card is rule R's: O writes none, and its comment names the column R wrote
    (a failed run -> Todo, a needs-human run -> Blocked). On the bug board, auto/bug-<n> -> To triage, but only from the column the dispatch set ($SG_BUG_DOING), so a card another step moved past it is never downgraded. Once per run; the comment carries the run id and the verdict's reasons.
    Each such run, marker or not (needs-human aside), is one failed dispatch of the issue, counted once by `wave.sh fail` (#609): at SG_REQUEUE_CAP with no new commit in the base the issue is frozen for the owner. A loop-caused one (the run's own review verdict decides, #621) counts only toward SG_LOOP_REQUEUE_CAP.
 Q  a card still In Progress behind its auto/disp-<n> (or auto/bug-<n>) marker, 10 min old, that no run works: not a live run, not a run in its launch window, not a run that landed, no open work branch naming the issue, no fork-<n>-<sha9> marker.
    Rules E and O read a run's verdict, so a run that ended without one (rule C drops a zombie from active-runs) or that left active-runs by any other road freed nothing: the card sat In Progress, autofrontier.sh skipped the issue,
    and health.sh reported runs-board-drift for hours. Rule Q reads the state, never the run's history: the card goes back to Todo (the bug card to To triage), the marker is dropped, one comment is queued. A refused read touches nothing.
 P  a `fork-<n>-<sha9>` marker whose commit left the fork base while the base no longer carries its change and the branch has nothing ahead to merge: an unwound landing (a hand revert, a branch reset, a base rebuilt under it).
    Its issue goes back to the column the dispatch reads (Todo; the bug board's Doing -> To triage) and the fork/disp/bug markers are dropped, so the frontier builds it again (#1830). A marker whose commit is in the base, or whose change the base holds anyway, and a branch still ahead of the base (the close retries on its head) are left alone.
 H  board state against the issue's own history (its loop comments; the REST timeline carries no project-status events), at most once per 10 min:
    RED-LOOP #N     3+ "fork suite failed after merging" comments with no green fork close since: the loop is not converging, a person must look.
    STATE-REGRESSION #N  the card is Todo/Blocked although the history says it reached Code Complete/Testing, or the card is Done while the issue is open; R repairs a Done card to the supported column.
 R  every board card the passes above know of: its expected status is derived from the run's own truth and the evidence beside it, and only a mismatch is written (one card, one PATCH), with the reason named.
    A live run holds the issue (dispatch marker or accepted child) -> In Progress; an issue that is closed or whose work branch is in $SG_BASE -> Done, so work that landed on a parent branch (#127) and never reached the base is not Done;
    a landing is credited to the child that made it, never to every issue the run held (#271): an issue a child's own accept line names follows that child's own `land` event and its own outcome, so a child that
    committed nothing keeps the loop's queue however many of its siblings landed, and only an issue no child of the run names takes the run's landing as its own;
    a completed run, whether or not it landed work, whose branch has not landed -> Code Complete; a failed run with nothing landed -> $SG_TODO (Todo); a needs-human run with nothing landed -> Blocked (ADR-0029: the owner is the next actor,
    and Todo would tell him the loop has it); an issue labelled needs-owner or needs-ratification -> Blocked. R is the one owner of a loop-board card a kept run names: no other rule writes one, so no rule races it.
    An issue labelled frozen:owner keeps the column it carries, and cycle.log names the write R held back (BOARD-FROZEN). A run that stopped needs-human or failed never moves its issue's
    card forward: its Todo or Blocked stands, and a work branch or local landing it left is stale evidence.
    Text fields: Run and Run started carry the run id and its start time while the card is In Progress, and are cleared when it is anything else. A card already in its expected status is not written again, and a write GitHub refuses is named in cycle.log (BOARD-RECONCILE / BOARD-WRITE-FAILED), never swallowed.
    The board is read once per pass (one fields read, one items read per project), and the card cache follows every status the pass writes, so a later rule (O, Q, P) reads the column the pass set, never the stale read. An issue the board answers no card for is added with its expected status in one write (board.sh --add);
    a board that does not answer at all (a refusal, a deadline miss) is told apart from that: nothing is moved on a guess, nothing is reported as a defect.
    With SG_BOARD_VOCAB=shipped every terminal entry carries its condition in one queued comment (#245): Closed names the branch in the landing line (or who closed it, when no branch names it); Abandoned names who decided and why (wontfix, closed as not planned or as a duplicate).
    A terminal write whose condition no read answers is not made (TERMINAL-NO-CONDITION), and a card settled in a terminal column with no tagged comment
    gets one, once (TERMINAL-BACKFILL, JANITOR_TERMINAL_BACKFILL per pass, default 5).
    With SG_BOARD_VOCAB=shipped R is also the one producer of the board facts (board_facts.py, ADR-0145): a run's wave branch on the remote, the review of its tip, and the check runs of the dev tip give wave_branch, landing and dev_ci, and a work branch whose review waits gives review_wait; board_columns.column_for names the column (Code Complete, Pushing, Testing, Code Review, Doing).
    A child of an ended run whose code is done and that has not entered the run's open wave is in Code Review, and no live run or
    review moves a card back from a later column (#603, ADR-0143). No route is added: the branch read is the one work_branches makes, and the dev tip's CI is one door read a pass.
 M  the live skill copy ($SG_LIVE_DIR, else ~/.claude/skills/squad-goal): a file whose mtime changed, or that came or went, since the last pass while no
    publish ran raises LIVE-MTIME (#659). live-guard.sh stamps $SG_STATE/live-publish.stamp when it publishes; a stamp newer than the last snapshot
    excuses the pass's changes. The snapshot is $SG_STATE/live-mtime.json; the first pass only takes it, and a dry pass never keeps it.
Every open issue of the repository carries a card, whether or not a run names it (#92): the pass reads the repository's open-issue list once through the door (gh_aggregate, the throttle) and adds the ones the board does not carry, in the dispatch column, or in Blocked when the issue carries needs-owner or needs-ratification, so an issue created while the loop runs is never left with no status."""
import json, os, pathlib, re, signal, subprocess, sys, tempfile, time, urllib.parse
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
def _run(a, cwd=None, env=None, timeout=None):   # the only runner of this module: it writes into a file, never a pipe, so a grandchild
    """Run one command and return (exit status, stdout), at most DEADLINE seconds. A pipe is never used: a command's
    grandchild that inherits stdout would hold the pipe past the child's exit, and the pass would wait for it for ever
    (the loop stopped for 28 min on 2026-10-13). A deadline miss is one status of 1 and one line in the pass log."""
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
    req = f / "request.txt"
    nums = issues_named(req.read_text()) if req.exists() else set()
    tail = launch_tail(f)
    quoted = f"\n```\n{tail}\n```\n" if tail else "\n"
    for n in sorted(nums, key=int):
        marks = [m for m in (st / "auto" / f"{kind}-{n}" for kind in ("bug", "disp")) if owns(f, None, m)]
        if not marks: continue   # nothing this run's request dispatched: not ours to free, and no comment
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
procs = {}
for l in sh("/bin/ps", "-ewwo", "pid=,ppid=,command=").splitlines():
    p = l.split(None, 2)
    if len(p) == 3 and p[0].isdigit(): procs[int(p[0])] = (int(p[1]), p[2])
starts = {}
for l in sh("/bin/ps", "-eo", "pid=,lstart=").splitlines():
    p = l.split(None, 5)
    if len(p) == 6 and p[0].isdigit():
        try: starts[int(p[0])] = time.mktime(time.strptime(" ".join(p[1:]).strip(), "%a %b %d %H:%M:%S %Y"))
        except ValueError: pass
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
def kill(pidv, why):
    if pidv in me: return
    for q in reversed(tree(pidv)):
        if q in me: continue
        say(f"kill pid={q} ({procs.get(q, (0, '?'))[1][:70]}) {why}")
        if not dry:
            try: os.kill(q, signal.SIGTERM)
            except OSError: pass
OWN_PASS = ("childcc.py",)   # the loop's own passes that work inside a finished run's folder by design (#497)
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
def folder(r): return pathlib.Path(r) if r.startswith("/") else co / "runs" / r
def read(f):
    v = False; pidc = None; started_at = None; acc = set(); t = f / "record.jsonl"; ver = None; rs = []; landed = False; rid = f.name; rts = ""
    # The per-child evidence (#271): the children the run accepted, the issues each child's own accept line named, the
    # landing nodes that stand, and every node's own outcome. A run that asked for children landed through the child
    # that made the landing, so rule R reads each child's own fact instead of crediting the run's landing to every issue.
    kids = set(); kid_issue = {}; land_nodes = set(); outs = {}; kid_review = {}
    # The spawn batch (#480): the engine answers a dispatch's requests in one batch, then starts the children. Any event after
    # the last spawn event is past that batch, so the run's choice of children is decided; spawn_ts is when it was made.
    spawned = decided = False; spawn_ts = 0.0
    if not t.exists(): return None
    for l in t.read_text().splitlines():
        try: d = json.loads(l)
        except ValueError: d = {}
        ev = d.get("event")
        if ev in ("spawn_requested", "spawn_accepted", "spawn_rejected"):
            spawned = True; decided = False
            if isinstance(d.get("ts"), (int, float)): spawn_ts = max(spawn_ts, float(d["ts"]))
        elif spawned and ev:
            decided = True
        if ev == "run_start":
            rid = str(d.get("run_id") or rid); rts = str(d.get("ts") or d.get("started_at") or rts)
        if ev == "verdict":   # the fold takes the last verdict event of a resumed record (ADR-0105)
            v = True; ver = d.get("verdict"); rs = [str(x) for x in (d.get("reasons") or [])]
        if ev == "land":   # only the landing stage's own event counts; a router's `routed: continue` is not a landing
            eff = d.get("effect") or {}
            if isinstance(eff, dict) and isinstance(eff.get("review"), dict) and d.get("node") is not None:
                kid_review[str(d["node"])] = eff["review"]   # the run's own review of the child (#599): rule O reads its verdict (#621)
            # A landing that moved the target's tip by nothing carries no work: it is no delivery, so no card is claimed and
            # no marker stands on it (ADR-0145 rule 12, #464). A record written before the effect carried `before` keeps the
            # old reading, so an old run is judged as it was.
            moved = eff.get("before") is None or eff.get("before") != eff.get("head")
            landed = landed or (d.get("outcome") == "pass" and moved)
            # The node that landed: a dispatch child's own name (`dispatch.py` writes node=child), or the run's own stage.
            # A landing a child's own held-out command turned red was undone, so it is not a landing (subject.py).
            if d.get("outcome") == "pass" and moved and eff.get("held_out") != "fail" and d.get("node") is not None:
                land_nodes.add(str(d["node"]))
        if ev == "outcome":   # the outcome the node itself recorded, child or run stage: the fact rule R reads per child (#271)
            if d.get("node") is not None:
                outs[str(d["node"])] = (str(d.get("outcome") or ""), [str(x) for x in (d.get("reasons") or [])])
        if ev == "call_start":
            pidc = d.get("pid") or pidc; started_at = d.get("started_at") or started_at
        if ev == "spawn_accepted" or '"spawn_accepted"' in l:
            # The router writes the issue down without the hash ("Fix GitHub issue 11 in the repository"), and a child that
            # took several writes them as a list. A hashed-only read lost the run's own issues: rule I and free_reaped read
            # this set (#66).
            found = set()
            # Both dispatchers make every child carry `Refs #N` (autofrontier.sh, autobug.sh), whatever words the router picks around it:
            # a router that wrote "issue 548" matched neither form above, so a live child read as unheld and lost its marker (#551).
            for m in re.finditer(r"GitHub issues? #?([0-9][0-9, ]*)|\bissue #(\d+)|\bRefs #(\d+)", l):
                found.update(x for x in re.split(r"[^0-9]+", m.group(1) or m.group(2) or m.group(3) or "") if x)
            acc |= found
            if d.get("child") is not None:   # the issues one accept line names belong to the child that line accepted, never to the whole run (#271)
                kids.add(str(d["child"]))
                kid_issue.setdefault(str(d["child"]), set()).update(found)
    kid_landed = {n for n in land_nodes if n in kids}   # the children whose own landing stands: the run's landing is theirs
    return {"verdict": v, "name": ver or "no verdict", "pid": pidc, "started_at": started_at, "acc": acc, "idle": now - t.stat().st_mtime,
            "failed": ver == "failed", "reasons": rs, "landed": landed, "run_id": rid, "run_ts": rts,
            "kid_issue": kid_issue, "kid_review": kid_review, "kid_landed": kid_landed, "kid_out": outs, "decided": decided, "spawn_ts": spawn_ts}
def same_process(pidc, started_at):
    if pidc not in procs or not isinstance(started_at, (int, float)):
        return pidc in procs
    started = starts.get(pidc)
    return started is None or started <= started_at
def mark_killed(f):
    """C: the zombie gets the killed verdict in its own record (squad.killed, the one writer), so the hub shows it ended. A record it cannot write stays as it is."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(x for x in (os.environ.get("SG_SQUAD"), os.environ.get("PYTHONPATH")) if x)}
    _run(["python3", "-m", "squad.killed", str(f)], env=env)
def inside(f): return [q for q, c in cwd.items() if c == str(f) or c.startswith(str(f) + "/")]
act = st / "active-runs.txt"; names = act.read_text().split() if act.exists() else []
repo0 = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
GONE = re.compile(r"\bHTTP(?:/[\d.]+)? 410\b|could not resolve to an issue|this issue was deleted", re.I)   # the 410 status or GitHub's stable message, never a bare 410: an issue number, a header or a delay may carry one (#466)
def gone_msg(s): return bool(GONE.search(s or ""))
_is = {}
def issue_read(x):   # one read answers every fact the passes ask of an issue: its state, its reason, its title and its labels
    if x not in _is:
        o = sh("gh", "api", f"repos/{repo0}/issues/{x}", "-q", '[.state, (.state_reason // ""), .title, ([.labels[].name] | join(","))] | @tsv').strip().split("\t")
        _is[x] = (o + ["", "", "", ""])[:4]
    return _is[x]
def issue_is_gone(x):   # a missing issue is not a transient read failure. The pass's one cached read of the issue says it: `gh api`
    # writes GitHub's error body to stdout and ignores -q on a refusal, so a deleted issue reads as GitHub's stable message, no extra call
    return gone_msg("\t".join(issue_read(x)))
live = {}; livef = {}; keep = []; held = set(); landed_n = set(); freed_markers = {}; missing = []   # held: issues a run still in active-runs may be working (a run in its launch window has no record yet); landed_n: issues a finished run landed
def req_issues(f): return issues_named((f / "request.txt").read_text()) if (f / "request.txt").exists() else set()
def owns(f, i, p):   # a dispatch marker names the request that wrote it (or its run): a run releases only a marker its own request wrote, never a newer run's (#539)
    try: c = p.read_text().strip()
    except OSError: return False
    if c and c in (f.name, (i or {}).get("run_id")): return True
    try: return (st / "req" / f"{c}.txt").read_bytes() == (f / "request.txt").read_bytes()
    except OSError: return False
pending = []   # markers of a reaped run, dropped after rule Q has read them
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
for r in names:
    f = folder(r)
    if not f.exists():   # a child worktree can take its run folder with it; no missing folder owns an issue
        missing.append((r, f))
        continue
    i = read(f)
    if i is None:   # J: no record.jsonl = the start was refused (e.g. vendor/skills drift); after 5 min stop its dashboard and drop it
        age = now - (f / "request.txt").stat().st_mtime if (f / "request.txt").exists() else 0
        if age > 300 and not [q for q in inside(f) if "dashboard_server" not in procs.get(q, (0, ""))[1]]:
            say(f"NEVER-STARTED run {f.name} (no record.jsonl after {int(age // 60)} min) dropped from active-runs")
            free_never_started(f)   # the run frees its own issues; say() reports it in a dry run too
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of never-started run {f.name}")
            continue
        held |= req_issues(f); keep.append(r); continue
    n = f.name
    if i["verdict"]:
        nums = req_issues(f) | i["acc"]
        if nums and all(issue_is_gone(x) for x in nums):
            say(f"GONE-RUN {n}: every issue ({', '.join('#' + x for x in sorted(nums, key=int))}) is gone; dropped from active-runs")
            continue
        if i["idle"] > 180:
            for q in inside(f):
                if "dashboard_server" not in procs.get(q, (0, ""))[1] and not own_pass(q): kill(q, f"leftover of finished run {n}")
        if i["idle"] > 3600:
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of finished run {n}")
        if i["landed"]: landed_n |= req_issues(f)
        keep.append(r); continue
    alive = bool(inside(f))
    if not alive and i["idle"] > 1200 and not (i["pid"] and same_process(i["pid"], i["started_at"])):
        say(f"zombie run {n} (no verdict, idle {int(i['idle'] // 60)} min, no process) dropped from active-runs")
        if not dry: mark_killed(f)
        pending += free_reaped(f, i)
        for q, (_, c) in procs.items():
            if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of zombie run {n}")
        continue
    held |= req_issues(f) | i["acc"]; keep.append(r); live[n] = i; livef[n] = f
if not dry and keep != names: act.write_text("\n".join(keep) + "\n")
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
        say(f"DUP issue #{x} accepted by live runs {', '.join(rs)} (not killed)")
        if not dry: mk.touch()
# An issue is held from its dispatch until its delivery ends (#539): a run with a verdict whose wave marker auto/wave-<run>
# stands (wave.sh clears it when the wave lands) still delivers its issues, and so does a child that entered such a wave.
# startability.sh's sg_live_issues reads the same markers for both dispatchers. No rule releases an issue it holds.
delivering = set()
for m in (st / "auto").glob("entered-*"):
    hit = re.fullmatch(r"entered-(.+)-(\d+)", m.name)
    if hit and (st / "auto" / f"wave-{hit.group(1)}").exists(): delivering.add(hit.group(2))
for r in names:
    f = folder(r); i = read(f) if f.exists() else None
    if i and i["verdict"] and any((st / "auto" / f"wave-{x}").exists() for x in {f.name, i["run_id"]} if x): delivering |= req_issues(f) | i["acc"]
def holding(n): return n in byissue or n in delivering   # a live run works it, or a run still lands its wave
base = os.environ.get("SG_BASE", "main")   # the branch a merge must land on to count as delivery (#127)
def issue_info(x):
    return issue_read(x)[:3]
def issue_labels(x): return set(l for l in issue_read(x)[3].split(",") if l)
_open = None
def close_spec(n, why):   # the comment, then the close as completed, both through the door's write (gh_send)
    door = ["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_send"]
    if _run(door + [f"repos/{repo0}/issues/{n}/comments", "-f", f"body={why}"])[0] == 0:
        _run(door + ["-X", "PATCH", f"repos/{repo0}/issues/{n}", "-f", "state=closed", "-f", "state_reason=completed"])
_subs = {}   # issue -> REST's sub_issues_summary, from the same one read as the labels
_kids = {}   # spec -> its sub-issues' numbers (the native relation), from the same one read as the labels
def open_issues():   # every open issue of the repository, ONE read per pass through the door's aggregate route (#92): (answered, {number: labels}). answered is False when the list did not answer (a refusal, a deadline miss, no door beside this module), and then no card is added on a guess. No call per issue: the one list carries every issue's number and labels.
    global _open
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
        _open = (isinstance(items, list), m)
    return _open
for n, i in live.items():
    mk = st / "auto" / f"janitor-closedrun-{n[-6:]}"
    if mk.exists() or not i["acc"] or i["idle"] > 3600: continue
    infos = {x: issue_info(x) for x in sorted(i["acc"])}
    if any(v[0] != "closed" for v in infos.values()): continue
    dup = all(v[1] == "not_planned" or v[2].startswith("[duplicate") for v in infos.values())
    nums = ", ".join("#" + x for x in infos)
    if dup:
        say(f"RUN-ON-CLOSED-ISSUES {n}: every issue ({nums}) is closed as a duplicate / not planned; stopping the run")
        if not dry:
            mk.touch(); f = livef[n]   # the folder active-runs.txt named, a run worktree's too (#585)
            for q in inside(f):
                if "dashboard_server" not in procs.get(q, (0, ""))[1]: kill(q, f"run {n} works only closed duplicate issues")
            if i["pid"] and same_process(i["pid"], i["started_at"]): kill(i["pid"], f"last call of run {n}")
    else:
        say(f"RUN-ON-CLOSED-ISSUES {n}: every issue ({nums}) is closed but not as a duplicate; not stopped (a land or fix run may be finishing)")
        if not dry: mk.touch()
owner = os.environ.get("SG_OWNER"); proj = os.environ.get("SG_PROJECT"); bproj = os.environ.get("SG_BUG_PROJECT")
_cards = {}
class _Done:   # the shape the seam's runner answers: the binding reads returncode and stdout, and stderr is never kept (no pipe, see _run)
    def __init__(self, rc, out): self.returncode, self.stdout, self.stderr = rc, out, ""
def _seam_run(a, input=None, capture_output=True, text=True, timeout=None):   # the seam's calls go through this module's one runner and its deadline; the binding never writes here, so no input is sent
    return _Done(*_run(list(a), timeout=timeout))
def cards(p):   # every card of project p, read once per pass, asked of the seam (#236): (answered, {issue: Status}). answered is False when GitHub did not answer (a refusal, a deadline miss), and then nothing is moved on a guess; an issue missing from an answered map is a card that does not exist
    if p not in _cards:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam sits beside this script; -I keeps the script's directory off sys.path
        from issue_state import board_state
        seam = board_state(owner=owner, project=p, repo=repo0, state_dir=st, run=_seam_run)
        try: _cards[p] = (bool(seam.binding.status_field()["id"]), seam.read_all())
        except (RuntimeError, ValueError): _cards[p] = (False, {})
    return _cards[p]
_holders = {}
def holders(p):   # the Run and Run started every card carries, read once per pass (never once per card), asked of the seam (#327): {issue: {"run", "started"}}; None when the read did not answer, and then every holder is written
    if p not in _holders:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from issue_state import board_state
        try: _holders[p] = board_state(owner=owner, project=p, repo=repo0, state_dir=st, run=_seam_run).holders()
        except Exception: _holders[p] = None
    return _holders[p]
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
def wave_facts(n, runs):   # the board facts of issue n whose child entered the wave of one of `runs` (the folder name or the record's run id)
    import board_facts
    work_branches()
    run = next((r for r in runs if r in _wv), None)
    tip, standing = _wv.get(run, ("", ""))
    approved = bool(run) and review_of("wave/" + run, tip) == "approved"
    return board_facts.issue_facts(entered=bool(run), standing=standing, approved=approved, dev_running=bool(run) and standing in board_facts.IN_DEV and dev_ci_running(), review_waits=False)
def wave_entry(n, f, i):   # the wave's say over issue n of a run (#467): None when the run cut no wave, "entered" when its child entered it (wave.sh's entered-<run>-<n>), "open" when the wave can still take it, "out" when the wave landed without it. A child's own land is in its tree, never in the wave, so for a run that cut a wave only the entry is Code Complete (ADR-0145 rule 12)
    names = {f.name, i["run_id"]}; a = st / "auto"
    if not any((a / f"waved-{r}").exists() or (a / f"wave-{r}").exists() for r in names): return None
    if any((a / f"entered-{r}-{n}").exists() for r in names): return "entered"
    return "open" if any((a / f"wave-{r}").exists() for r in names) else "out"
def pr_count(n):   # how many remote work branches name the issue: one exists once the issue is past dispatch. The name is kept for its callers; no pull request is read
    return str(len(work_branches().get(str(n), [])))
def branch_ahead_count(n):   # how many of its work branches hold commits the landing branch lacks
    return str(sum(1 for _, g in work_branches().get(str(n), []) if g in ("ahead", "diverged")))
def branch_in_dev_count(n):   # how many of its work branches are in the landing branch: the tip is an ancestor of dev
    return str(sum(1 for _, g in work_branches().get(str(n), []) if g in ("behind", "identical")))
_named_in_land = None
def named_in_land():   # {issue} whose fix is a commit in origin/<land>: its subject ends `(#N)` and is no `TDD RED` (a red test alone fixes nothing). A fix pushed straight to dev reached it (#519). The subject only: a body names issues in passing. One local log a pass, no API call; a checkout with no such ref names none
    global _named_in_land
    if _named_in_land is None:
        r = subprocess.run(["git", "-C", str(co), "log", "-n", "2000", "--format=%s", f"origin/{os.environ.get('SG_LAND_BRANCH') or 'dev'}"], capture_output=True, text=True)
        _named_in_land = {m.group(1) for line in (r.stdout.splitlines() if r.returncode == 0 else []) if not line.startswith("TDD RED")
                          for m in [re.search(r"\(#(\d+)\)\s*$", line)] if m}
    return _named_in_land
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
def board_write(args, n, why):   # the one writer of a card: a status write that succeeds updates the card cache, so every later read in this pass sees the column the pass set (#91)
    gone = st / "auto" / f"janitor-gone-{n}"
    if gone.exists(): return False
    r = subprocess.run(["bash", str(pathlib.Path(__file__).parent / "board.sh"), *args], capture_output=True, text=True)
    if r.returncode:
        msg = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
        named = next((l for l in msg if l.startswith("#")), None)   # board.py prints a read note beside the refusal (#96): the refusal is the line that names the card
        err = named or (msg[0] if msg else "no output")
        if gone_msg(err):
            say(f"GONE-ISSUE #{n}: board write refused because the issue is gone: {err}")
            if not dry: gone.touch()
            return False
        say(f"BOARD-WRITE-FAILED #{n}: {why}: {err}")
        if not dry: refuse_comment(n, "BOARD-WRITE-FAILED", err, why)   # a dry run acts on nothing, so it queues nothing
        return False
    a = args[1:] if args and args[0] == "--add" else args
    if len(a) == 4 and not a[0].startswith("--") and a[1] in _cards: _cards[a[1]][1][str(a[3])] = a[2]
    return True
def door_read(route, q):   # one read through the door (gh_api, ADR-0277): (answered, the projection as text); a refusal or a deadline miss is not answered
    r, out = _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_api", route, "-q", q])
    return r == 0, out.strip()
CLOSER = '[.[] | select(.event == "closed") | .actor.login] | last // ""'   # who closed the issue last, from its events (a single-issue read through the door carries no closed_by)
def terminal_facts(n, col):   # the facts a terminal comment states (#245), or None when a read did not answer: nothing is claimed on a guess
    from urllib.parse import quote
    if col == "Closed":
        land = os.environ.get("SG_LAND_BRANCH") or "dev"
        landed = [b for b, g in work_branches().get(str(n), []) if g in ("behind", "identical")]   # the branch is the delivery (#358): no pull request is read
        if not landed:
            ok, who = door_read(f"repos/{repo0}/issues/{n}/events?per_page=100", CLOSER)
            return {"base": land, "closed_by": who, "reason": issue_info(n)[1]} if ok else None   # the close reason: a not-planned close is never called completed (#517)
        ok, sha = door_read(f"repos/{repo0}/branches/{quote(landed[0], safe='')}", '.commit.sha // ""')
        if not ok: return None
        test = ""
        if sha:
            ok, test = door_read(f"repos/{repo0}/commits/{sha}/check-runs", '[.check_runs[] | .name + ": " + (.conclusion // .status)] | join(", ")')
            if not ok: return None
        return {"base": land, "branch": landed[0], "sha": sha, "test": test}
    state, reason, _ = issue_info(n)   # the pass's one issue read: state, reason and labels
    if "wontfix" in issue_labels(n):
        ok, who = door_read(f"repos/{repo0}/issues/{n}/events?per_page=100", '[.[] | select(.event == "labeled" and .label.name == "wontfix") | .actor.login] | last // ""')
        return {"who": who, "why": "labelled wontfix"} if ok else None
    if state == "closed" and reason in ("not_planned", "duplicate"):
        ok, who = door_read(f"repos/{repo0}/issues/{n}/events?per_page=100", CLOSER)
        return {"who": who, "why": "closed as a duplicate (superseded by another issue)" if reason == "duplicate" else "closed as not planned"} if ok else None
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
    say(f"TERMINAL-COMMENT #{n}: {col} -> {note.splitlines()[-1]}")
    note += run_summary(n)   # the run's summary rides the comment this pass already posts: no other write path
    if dry: return
    (st / "cq").mkdir(exist_ok=True)
    (st / "cq" / f"{CQ}#{n}-terminal-{os.getpid()}.md").write_text(note)
    mk.write_text(col + "\n")
expected = {}   # issue -> (rank, status, why, run_id, run_ts): the column R derived for every card it owns; rule O reads it, never the board
def door(fn, *a):   # one call through ghcall.sh: (exit code, output). A read is gh_api, a write gh_send (ADR-0277), so this module's own call sites stay as they were
    return _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), fn, *a])
RUN_PARENT = "run-parent"   # the label of a run's roll-up issue: never work, so no dispatcher takes it (startable) and the board holds no card for it (#500)
def reconcile_board():
    if not (owner and proj and repo0): return
    shipped = os.environ.get("SG_BOARD_VOCAB") == "shipped"   # the loop writes the shipped vocabulary once the board carries it (expand done); until then it writes today's options
    SHIPPED_NAME = FREEZE_LABEL = {}
    TERMINAL = ()
    if shipped:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # board_columns sits beside this script; -I keeps the script's directory off sys.path
        from board_columns import SHIPPED_NAME, FREEZE_LABEL, TERMINAL, TERMINAL_TAG, terminal_comment, column_for, past, CODE_REVIEW
    doing = "Doing" if shipped else "In Progress"
    testing = set()   # issues whose wave is in dev while the CI of the dev tip runs: Done waits for the CI to end
    frozen = set()   # issues that carry the owner freeze this pass: shipped vocabulary only
    stopped = set()   # issues whose newest run stopped needs-human or failed: no stale branch or local landing moves them forward
    held_owner = {}   # issue -> (rank, status, why): the write frozen:owner held back this pass, named in cycle.log when it differs
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
        expected[n] = (rank, status, why, run_id, run_ts)
    def wave_column(n, f, i):   # the facts producer (ADR-0145): Pushing and Testing come from the run's wave branch, its approval and the CI of the dev tip, and the table names the column
        if not shipped: return
        col, _ = column_for(wave_facts(n, {f.name, i["run_id"]}))
        if col == "Pushing": set_expected(n, col, f"the wave of run {i['run_id']} is approved and its push to dev is pending", i["run_id"], i["run_ts"], 65)
        elif col == "Testing": set_expected(n, col, f"the wave of run {i['run_id']} is in dev and the CI of the dev tip runs", i["run_id"], i["run_ts"], 70); testing.add(n)
    def unheld(n, f, i):   # a live run holds the issue, but its spawn batch gave it no child (#480): nobody works it, so it is back in the queue and its dispatch marker goes. A marker written after the batch belongs to a newer dispatch and stays
        set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} gave the issue no child (the router did not choose it, or the engine rejected its spawn)", i["run_id"], i["run_ts"], 20)
        for k in ("disp", "bug"):
            m = st / "auto" / f"{k}-{n}"
            if m.exists() and m.stat().st_mtime <= i["spawn_ts"]:
                say(f"UNHELD #{n}: run {i['run_id']} gave it no child; {m.name} dropped")
                if not dry: m.unlink(missing_ok=True)
    def code_complete(n, f, i, why):   # a landing is Code Complete only once the wave, when the run cut one, holds the issue (#467)
        e = wave_entry(n, f, i)
        if e == "open" and shipped: set_expected(n, CODE_REVIEW, f"{why}; its child's code is done and its review holds it out of the open wave of run {i['run_id']}", i["run_id"], i["run_ts"], 35)   # #603
        elif e == "open": set_expected(n, "In Progress", f"{why}; its child has not entered the open wave of run {i['run_id']} yet", i["run_id"], i["run_ts"], 30)
        elif e == "out": set_expected(n, os.environ.get("SG_TODO", "Todo"), f"{why}, but the wave of run {i['run_id']} landed without its child", i["run_id"], i["run_ts"], 20)
        else:
            set_expected(n, "Code Complete", why, i["run_id"], i["run_ts"], 60)
            wave_column(n, f, i)
    def stopped_run(n, i, evidence):   # a run that stopped needs-human or failed never moves its issue's card forward: its Todo or Blocked stands, whatever branch or local landing it left
        stopped.add(n)
        why = "; ".join(i["reasons"]) or "no reason recorded"
        human = i["name"] == "needs-human"
        set_expected(n, "Blocked" if human else os.environ.get("SG_TODO", "Todo"),
                     f"run {i['run_id']} {evidence}, but the run {'needs a human' if human else 'failed'} ({why})",
                     i["run_id"], i["run_ts"], 40 if human else 20)
    # Rank 20, a run that ended with nothing delivered: Doing back to Todo. It is the one backward move the loop makes, and it
    # undoes the loop's own claim, the dispatch that moved the card out of Todo (ADR-0143, #550). No other rule moves a card
    # back into Todo or Backlog: only the owner does.
    runs = []; newest = {}   # a card follows the newest run that holds its issue: an older run's column never overrides it, whatever its rank (#619)
    for r in keep:
        f = folder(r); i = read(f); nums = req_issues(f)
        if i: nums |= i["acc"]
        age = (1, "") if i is None else (0, str(i["run_ts"]))   # a run with no record yet (a dispatch marker) is the newest
        runs.append((f, i, nums, age))
        for n in nums: newest[n] = max(newest.get(n, age), age)
    for f, i, nums, age in runs:
        nums = {n for n in nums if age >= newest[n]}   # rank orders the signals of one run only (#619)
        by_issue = {}   # issue -> the children whose own accept line named it: that child's outcome decides its card (#271)
        if i:
            for ch in sorted(i["kid_issue"]):
                for x in i["kid_issue"][ch]: by_issue.setdefault(x, []).append(ch)
        for n in nums:
            mine = by_issue.get(n, [])
            landed_kid = next((c for c in mine if c in i["kid_landed"]), None) if i else None   # a child that landed its own work: the run's landing is that child's, never every issue the run held (#271)
            if i is None:
                set_expected(n, "In Progress", f"run {f.name} has dispatch marker", f.name, "", 30)
            elif not i["verdict"] and i["decided"] and not any(n in v for v in i["kid_issue"].values()):
                unheld(n, f, i)
            elif not i["verdict"]:
                set_expected(n, "In Progress", f"run {i['run_id']} is live", i["run_id"], i["run_ts"], 30)
            elif landed_kid:
                if i["failed"] or i["name"] == "needs-human": stopped_run(n, i, f"child {landed_kid} landed work locally")
                else: code_complete(n, f, i, f"run {i['run_id']} child {landed_kid} landed work locally")
            elif holding(n) and (bool(mine and i["kid_landed"]) or not i["landed"]): continue   # nothing landed here, but a live run works the issue or a pending wave carries it: its card is that run's, never this run's Todo or Blocked (#540)
            elif mine and i["kid_landed"]:   # the run landed, but through a sibling: this child committed nothing, so its own outcome decides and the loop's column holds it (#271)
                c = mine[0]; oc, ors = i["kid_out"].get(c, ("", []))
                why = "; ".join(ors) or "no reason recorded"
                if oc == "needs-human" or i["name"] == "needs-human":   # not success (ADR-0029): the owner is the next actor
                    stopped.add(n)
                    set_expected(n, "Blocked", f"run {i['run_id']} child {c} needs a human with nothing landed ({why})", i["run_id"], i["run_ts"], 40)
                else:
                    if oc == "fail": stopped.add(n)
                    set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} child {c} {oc or 'stopped'} with nothing landed ({why})", i["run_id"], i["run_ts"], 20)
            elif i["landed"]:
                if i["failed"] or i["name"] == "needs-human": stopped_run(n, i, "landed work locally")
                else: code_complete(n, f, i, f"run {i['run_id']} landed work locally")
            elif i["failed"]:
                stopped.add(n)
                why = "; ".join(i["reasons"]) or "nothing landed"
                set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} failed with nothing landed ({why})", i["run_id"], i["run_ts"], 20)
            elif i["name"] == "needs-human":   # not success (ADR-0029): the owner is the next actor, and the board says so
                stopped.add(n)
                why = "; ".join(i["reasons"]) or "no reason recorded"
                set_expected(n, "Blocked", f"run {i['run_id']} needs a human with nothing landed ({why})", i["run_id"], i["run_ts"], 40)
            else:   # a run that ended with nothing delivered delivered nothing: the card goes back to the queue, never to Code Complete (ADR-0145 rule 12, #464)
                set_expected(n, os.environ.get("SG_TODO", "Todo"), f"run {i['run_id']} completed with nothing landed", i["run_id"], i["run_ts"], 20)
    for m in sorted((st / "auto").glob("disp-*")):
        n = m.name[5:]; holder = m.read_text().strip()
        if n.isdigit() and holder in keep and not (read(folder(holder)) or {}).get("verdict"):   # a holder with a verdict is over: its own record ranks the card (Todo, Blocked, Code Complete), not the marker it left
            set_expected(n, "In Progress", f"{m.name} holds live run {holder}", holder, "", 30)
    # The loop floor gives a column only to an open issue with no card, once: Todo for a bug, Backlog for anything else
    # (#550). A card that has a column keeps it: the floor never moves a card, so it never sends one back to Backlog or Todo
    # (ADR-0143). Only the owner moves a card there. This rank is below every rule above, so current run, marker, branch,
    # landing, closure, or owner-label evidence wins.
    oans, olabels = open_issues()
    if oans:
        for n in sorted(olabels, key=int):
            if holding(n) and n not in expected: continue   # a run still delivers it and no rule named its column: the floor never sends it back to Todo (#540)
            fresh = (lambda a, m: a and not m.get(str(n)))(*cards(proj))   # an answered read that holds no card for it, or a card with no Status: no column yet
            bug = bool(olabels[n] & {os.environ.get("SG_BUG_LABEL") or "bug"})
            if fresh: set_expected(n, os.environ.get("SG_TODO", "Todo") if bug else "Backlog", "open issue with no card", rank=10)
            if shipped and "wontfix" in olabels[n]: set_expected(n, "Abandoned", "issue carries wontfix", rank=95)
            # A spec is written and not scheduled, so the floor opens it in Backlog, and the owner starts it (ADR-0142). The fold
            # moves it forward only. Its rank sits above the floor and below every rule a run, a marker or a branch raises.
            if "spec" in olabels[n]:
                # A spec's column is a fold over its sub-issues' cards (spec_fold.py): the relation came with the one read above and
                # the cards are read once per pass, so the fold adds no call. The close rule below closes the spec issue itself.
                sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the fold sits beside this script; -I keeps the script's directory off sys.path
                import spec_fold
                have_cards = cards(proj)[1]
                col, reverted = spec_fold.fold([have_cards.get(k, "") for k in _kids.get(n, [])], have_cards.get(n, ""))
                if col: set_expected(n, col, "spec folds over its sub-issues" + (": a child reopened, the card keeps its column" if reverted else ""), rank=15)   # forward only: below Doing the fold writes nothing, and the spec keeps the owner's column (#550)
                # The owner starts a spec by moving its card to Todo, and its children follow it there (#589): a child in Backlog,
                # or with no column yet, goes to Todo. Forward only: a child past Backlog keeps its column (ADR-0143). The fold
                # writes nothing below Doing, so the two rules never fight; every rule a run, a label or a closure raises outranks it.
                # A child whose blockers are open stays held by the dispatch gate, never by its column.
                todo = os.environ.get("SG_TODO", "Todo")
                if have_cards.get(n, "") == todo:
                    for k in _kids.get(n, []):
                        if k in olabels and have_cards.get(k, "") in ("", "Backlog"):
                            set_expected(k, todo, f"its spec #{n} sits in {todo}: the owner started it", rank=12)
                # A spec with at least one sub-issue, every one closed, is done (the owner's rule): the loop closes it with one comment.
                # A spec with no sub-issue proves nothing and stays open.
                sub = _subs.get(n) or {}
                if sub.get("total", 0) >= 1 and sub.get("completed") == sub["total"]:
                    say(f"SPEC-CLOSED #{n}: every sub-issue ({sub['total']}) is closed")
                    if not dry: close_spec(n, f"Closed by the loop: every sub-issue ({sub['total']}) is closed, so the spec is done.")
                if reverted:
                    say(f"SPEC-REVERTED #{n}: a child reopened, the card keeps {col} and carries the tag {spec_fold.TAG}")
                    if not dry and spec_fold.TAG not in olabels[n]: _run(["bash", "-c", '. "$0"; "$@"', str(pathlib.Path(__file__).parent / "ghcall.sh"), "gh_send", "-X", "POST", f"repos/{repo0}/issues/{n}/labels", "-f", f"labels[]={spec_fold.TAG}"])
            if olabels[n] & {"needs-owner", "needs-ratification"}:
                set_expected(n, "Blocked", "issue is blocked by owner label", rank=100)
    for n in sorted(set(expected) | (set(olabels) if oans else set()), key=int):   # every open issue, as when the floor named each one: a rule below can still move a card forward
        state, reason, _ = issue_info(n)
        if state == "closed": set_expected(n, "Done", "issue is closed", "", "", 90)
        if shipped and state == "closed" and reason in ("not_planned", "duplicate"): set_expected(n, "Abandoned", f"issue is closed as {reason}", "", "", 95)   # not delivered, by decision (spec #132)
        labels = issue_labels(n)
        if labels & {"needs-owner", "needs-ratification"}: set_expected(n, "Blocked", "issue is blocked by owner label", "", "", 100)
        if str(n) in named_in_land(): set_expected(n, "Code Complete", "a commit in the landing branch names the issue", "", "", 50)   # a hand fix in dev: the floor never moves it back (ADR-0143, #519)
        if n not in stopped and branch_ahead_count(n) != "0":   # the branch's own state sets the column (#358): no pull request is read. A stopped run's branch is stale: it never lifts the Todo or Blocked its verdict set
            if shipped and review_waits(n): set_expected(n, column_for(wave_facts(n, ()) | {"review_wait": True})[0], "a work branch names the issue and the review of its tip waits", "", "", 50)   # ADR-0145: a child enters the wave only after its review approves, so a review that waits marks Doing
            else: set_expected(n, "Code Complete", "a work branch names the issue and is not in dev yet", "", "", 50)
        if n not in testing and branch_in_dev_count(n) != "0" and cards(proj)[1].get(str(n), "") in ("Code Complete", "Pushing", "Testing"):   # a fresh branch is in dev too, so only a card that reached the wave path is delivered by it
            set_expected(n, "Done", "its work branch is in dev", "", "", 80)
    answered, have_all = cards(proj)
    if not answered: return   # no answer from the board: nothing is moved on a guess (a card that does not exist is an answer, and is created below)
    for n, (_, want, why) in sorted(held_owner.items(), key=lambda kv: int(kv[0])):   # frozen:owner holds the card: R names the write it held back, and writes nothing
        have = have_all.get(str(n))
        if have is not None and have != want:
            say(f"BOARD-FROZEN #{n}: frozen:owner holds the card in {have}; R left it alone (would have written {want}: {why})")
    for n in sorted((k for k in set(expected) | set(olabels) if RUN_PARENT in (olabels.get(k) or issue_labels(k))), key=int):   # a run parent is a roll-up in GitHub, never work: it gets no card, and a card it has is removed, only when the board carries one (#500)
        expected.pop(n, None)
        if str(n) in have_all and board_write(["--remove", owner, str(proj), str(n)], n, "the issue is a run parent"):
            del have_all[str(n)]
            say(f"BOARD-RECONCILE #{n}: card removed (run-parent: a roll-up, never work)")
    for n, (_, want, why, run_id, run_ts) in sorted(expected.items(), key=lambda kv: int(kv[0])):
        have = have_all.get(str(n))
        note = None
        if shipped and want in TERMINAL and have != want:   # the guard (#245): no terminal write without the comment that states its condition
            note = terminal_comment(want, terminal_facts(n, want))
            if note is None:
                say(f"TERMINAL-NO-CONDITION #{n}: the condition of {want} could not be read; card not moved ({why})")
                continue
        if shipped and want in (doing, CODE_REVIEW) and past(have, want): want = have   # a card only moves forward (ADR-0143, #603): a live run or a review never moves it back
        if have is None:   # an issue created while the loop runs has no card until this pass adds one, with its status, in one write (#86)
            if board_write(["--add", owner, str(proj), want, str(n)], n, why): say(f"BOARD-RECONCILE #{n}: card added -> {want} ({why})")
            else: continue
            if note: queue_terminal(n, want, note)
        elif have != want and board_write([owner, str(proj), want, str(n)], n, why):
            say(f"BOARD-RECONCILE #{n}: card -> {want} ({why})")
            if note: queue_terminal(n, want, note)
        if want == doing:
            held = (holders(proj) or {}).get(str(n))
            if run_id and held != {"run": run_id, "started": run_ts or ""}:   # only a holder or a start the card does not carry is written (#327)
                if board_write(["--run-fields", owner, str(proj), str(n), run_id, run_ts], n, f"write Run fields for {why}") and holders(proj) is not None: holders(proj)[str(n)] = {"run": run_id, "started": run_ts or ""}   # every pass: the field names the run that holds the card now; a card no run holds (a spec folded to Doing) gets no Run value
        elif have in ("In Progress", "Doing", "Code Review") and have != want:
            board_write(["--clear-run", owner, str(proj), str(n)], n, f"clear Run fields, the card left {have} for {want}")   # only on the way out, so a card already past In Progress is not written again every cycle
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
            if reason in ("not_planned", "duplicate"):
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
    req = d.read_text().strip(); lg = st / f"{req}.log"
    n = d.name.split("-", 1)[1]; lt = lg.read_text() if lg.exists() else ""
    if d.name.startswith("bug-") and "refusing to start" not in lt: continue   # a bug marker of a run that started is rule O's
    refused = "Nothing ran" in lt or "refusing to start" in lt   # K: squad's own refusal, or launch.py's (the model gate, #523)
    if now - d.stat().st_mtime < (600 if refused else 1800): continue   # K: a refused start frees the issue after 10 min
    if not (("verdict failed" in lt or refused) and not holding(n)): continue
    repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
    state = sh("gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state").strip()
    if state == "closed":   # a closed issue is never dispatched: its marker only made every pass read its state again (#525)
        say(f"stale {d.name} (issue closed) removed")
        if not dry: d.unlink()
        continue
    if state != "open": continue
    if pr_count(n) != "0":   # a work branch exists: the issue is past dispatch, so the marker stands -- and the pass names the issue, so this decision reads apart from a release (#217)
        say(f"DISPATCH-FAILED #{n}: request {req} ended failed, but a work branch names the issue; {d.name} kept so the pass never dispatches it again")
        continue
    say(f"stale {d.name} (request {req} ended failed, issue open, no work branch) removed")
    if not dry: d.unlink()
def count_failed(n, f, i):   # O (#609): a run that ended with nothing delivered for issue n is one failed dispatch, counted
    # by wave.sh fail (one count per issue, keyed by the run, frozen at SG_REQUEUE_CAP), whether or not it still holds a marker.
    # A run the ledger lists is not sent again, so a pass costs no call for a run already counted.
    try:
        if any(l.split("\t")[0] == f.name for k in ("requeues", "loopqueues") if (st / "auto" / f"{k}-{n}.runs").exists()
               for l in (st / "auto" / f"{k}-{n}.runs").read_text().splitlines()): return
    except OSError: pass
    if holding(n) or issue_info(n)[0] != "open" or pr_count(n) != "0": return
    why = f"the wave of run {i['run_id']} landed without its child" if i["landed"] else f"ended {i['name']}: " + ("; ".join(i["reasons"]) or "nothing landed")
    # The run's own review of the child decides over the reason text (#621): CHANGES or BLOCKED a model wrote on the diff is
    # the issue's own failure (`own`); a BLOCKED the chain wrote because every model failed, a review past its deadline or one that
    # never started, is the loop's (`loop`). No review leaves the text to wave.sh.
    kind = ""
    for k, iss in i["kid_issue"].items():
        rv = i["kid_review"].get(k)
        if n not in iss or not rv or str(rv.get("verdict") or "").lower() in ("approve", "no_review", ""): continue
        lost = str(rv.get("verdict")) == "deadline" or "every review model failed" in str(rv.get("findings") or "") or str(rv.get("stderr") or "").startswith("review_not_started")
        kind = "own" if not lost or kind == "own" else "loop"
    if dry: say(f"FAILED-DISPATCH #{n}: run {f.name} would count ({why}{', ' + kind if kind else ''})"); return
    out = _run(["bash", str(pathlib.Path(__file__).parent / "wave.sh"), "fail", n, f.name, why, *([kind] if kind else [])])[1].strip()
    if out: say(f"FAILED-DISPATCH #{n}: {out}")
if owner and proj and repo0:
    for r in names:   # O:a run that ended without landing releases its issues: marker dropped (whatever the verdict was: #1847), card back where the dispatch found it
        f = folder(r); i = read(f)
        if not (i and i["verdict"] and i["idle"] > 180): continue   # a run still moving keeps its marker
        req = f / "request.txt"
        nums = issues_named(req.read_text()) if req.exists() else set()
        if i["landed"]: nums = {n for n in nums if wave_entry(n, f, i) == "out"}   # a run that landed keeps its markers, but for an issue its landed wave never carried (#467)
        if i["name"] != "needs-human":   # needs-human is the owner's already (ADR-0029)
            for n in sorted(nums, key=int): count_failed(n, f, i)
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
            if holding(n): again = True; continue   # a live run works it, or a wave still carries it: not ours to release, not yet
            ist = sh("gh", "api", f"repos/{repo0}/issues/{n}", "-q", ".state").strip()
            if ist != "open": again = again or ist != "closed"; continue
            pc = pr_count(n)
            if pc != "0": again = again or not pc.isdigit(); continue   # a work branch names it: the issue is past dispatch (a refused read: not yet decided)
            moves = []   # the loop-board card is rule R's (one owner, #91): O reads the column R derived and writes none of its own; the marker is dropped either way, nothing must hold a finished run's issue back
            if bproj and "bug" in marks[n] and card_status(bproj, n) == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])   # only the column the dispatch set: never downgrade a card another step moved past it
            mks = " and ".join("auto/" + k + "-" + n for k in marks[n])
            col = expected.get(n)
            where = (f"its card is {col[1]}" if col else "its card keeps its column") + (f" (bug card back in {moves[-1][1]})" if moves else "") + " and"
            say(f"DISPATCH-FAILED #{n}: run {f.name} ended {i['name']} ({why}), nothing landed; {where} {mks} marker dropped so it dispatches again")
            if not dry:
                for p, s in moves: board_write([owner, str(p), s, str(n)], n, f"bug card back in {s} after run {f.name} ended {i['name']}")
                for k in marks[n]: (st / "auto" / f"{k}-{n}").unlink()
                (st / "cq" / f"{CQ}#{n}-dispatchfail-{os.getpid()}.md").write_text(
                    f"Run {f.name} ended without a landed change (verdict {i['name']}: {why}). {where[:1].upper() + where[1:]} its {mks} marker dropped, so the next cycle dispatches the issue again.\n")
        if not again and not dry: mk.touch()
    # O: a missing child-worktree run has no record to read, but its marker still identifies the issue it claimed.
    for r, f in missing:
        marks = {}
        for p in (st / "auto").glob("*"):
            mo = re.fullmatch(r"(disp|bug)-(\d+)", p.name)
            if mo and p.read_text().strip() in (r, f.name): marks.setdefault(mo.group(2), []).append((mo.group(1), p))
        for n in sorted(marks, key=int):
            if holding(n) or issue_info(n)[0] != "open" or pr_count(n) != "0": continue
            mks = " and ".join("auto/" + kind + "-" + n for kind, _ in marks[n])
            say(f"DISPATCH-MISSING #{n}: run {f.name} folder missing; {mks} marker dropped so it dispatches again")
            if not dry:
                for _, p in marks[n]: p.unlink()
    # Q: a marker and an In Progress card with no run behind them. Only a definite answer counts: a refused read (not "open", not a digit, no status)
    # touches nothing and leaves no once-only marker, so the next pass decides. A definite "no" is remembered for 10 min, so a landed issue's old marker is not re-read every pass.
    qm = {}
    for k in ("disp", "bug"):
        for m in (st / "auto").glob(k + "-*"):
            if m.name[len(k) + 1:].isdigit(): qm.setdefault(m.name[len(k) + 1:], {})[k] = m
    for n, ms0 in freed_markers.items():
        qm.setdefault(n, {}).update(ms0)
    for n, ms in sorted(qm.items(), key=lambda kv: int(kv[0])):
        if n in held or n in landed_n or n in delivering or any(m is not None and now - m.stat().st_mtime < 600 for m in ms.values()) or list((st / "auto").glob(f"fork-{n}-*")): continue
        seen = st / "auto" / f"janitor-q-seen-{n}"
        if seen.exists() and now - seen.stat().st_mtime < 600: continue
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
        if not moves: no(); continue
        mks = " and ".join("auto/" + k + "-" + n for k in sorted(ms))
        say(f"STALE-CARD #{n}: card In Progress behind {mks}, no run works it, no work branch names it; card -> {', '.join(s for _, s in moves)}, marker dropped so it dispatches again")
        if not dry:
            for p, s in moves: board_write([owner, str(p), s, str(n)], n, f"stale card back in {s}")
            for m in ms.values():
                if m is not None: m.unlink()
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
            if card_status(proj, n) not in ("In Progress", "Doing", "Code Review", "Code Complete", os.environ.get("SG_TESTING", "Testing")): continue   # only what the landing set: a card another step moved past it is never downgraded
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
                (st / "cq" / f"{CQ}#{n}-unwound-{os.getpid()}.md").write_text(
                    f"UNWOUND #{n}: the close pass recorded a merge of {sha[:9]} into {fb}, and {fb} no longer holds that change ({br} has nothing left ahead of it either). The landing was undone, so the issue is back in {moves[-1][1]} with its {' and '.join('auto/' + k for k in marks)} marker(s) dropped and the next cycle builds it again.\n")
repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
hb = pathlib.Path(os.environ.get("SG_HUB_DIR") or co / "runs" / ".hub") / "hub-board.json"; hm = st / "auto" / "janitor-h-last"
if hb.exists() and (not hm.exists() or now - hm.stat().st_mtime > 600):
    if not dry: hm.touch()
    try: items = json.loads(hb.read_text()).get("items", [])
    except Exception: items = []
    for it in items:
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
        if not (op and stt in ("Todo", "Blocked", "In Progress", "Doing", "Code Review", "Code Complete")): continue
        cm = [x.split("\t", 1) for x in sh("gh", "api", f"repos/{repo}/issues/{n}/comments?per_page=100", "--paginate", "--jq", r'.[]|[.created_at,(.body|gsub("\n";" "))]|join("\t")').splitlines() if "\t" in x]
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
                say(f"STATE-REGRESSION #{n}: card is {stt} but its history reached Code Complete/Testing at {reach[-1]}")
                if not dry: mk.touch()
def live_mtime():   # M (#659): a change to the live skill copy that no publish made
    live = pathlib.Path(os.environ.get("SG_LIVE_DIR") or pathlib.Path.home() / ".claude" / "skills" / "squad-goal")
    snap, stamp = st / "live-mtime.json", st / "live-publish.stamp"
    if not live.is_dir(): return
    cur = {}
    for p in live.rglob("*"):
        try:
            if p.is_file(): cur[str(p.relative_to(live))] = p.stat().st_mtime
        except OSError: pass
    try: old = json.loads(snap.read_text())
    except Exception: old = None
    if not dry:
        tmp = snap.with_name(snap.name + f".{os.getpid()}"); tmp.write_text(json.dumps({"at": now, "files": cur})); os.replace(tmp, snap)
    if not isinstance(old, dict) or not isinstance(old.get("files"), dict): return
    if stamp.exists() and stamp.stat().st_mtime >= old.get("at", 0): return
    files = old["files"]; changed = sorted(k for k in set(cur) | set(files) if cur.get(k) != files.get(k))
    if changed:
        alert(f"LIVE-MTIME {live}: {len(changed)} file(s) changed with no publish: {', '.join(changed[:5])}")
live_mtime()
if acts and not dry:
    kinds = {}
    for a in acts: k = a.split()[0] if not a.startswith("kill") else ("kill " + ("dashboard" if "dashboard" in a else "leftover")); kinds[k] = kinds.get(k, 0) + 1
    print("JANITOR " + ", ".join(f"{v} {k}" for k, v in kinds.items()) + " (details in cycle.log)", flush=True)
