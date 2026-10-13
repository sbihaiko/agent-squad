#!/usr/bin/env python3
"""Janitor: one conservative cleanup pass per watch cycle (JANITOR_DRY=1 prints and acts on nothing). Every action goes to stdout and $SG_STATE/cycle.log.
 A  finished run (has a verdict, 3 min old): kill every process whose cwd is inside its run folder (leftover claude/dotnet/test hosts), as a tree.
 B  finished run (1 h old): kill its idle dashboard_server.
 C  zombie run (no verdict, record.jsonl idle 20 min, no process inside its folder, last started call dead): drop it from active-runs.txt and free its dispatch markers and leases.
 D  two live runs that accepted the same issue: report once (never kills).
 E  auto/disp-<n> older than 30 min whose dispatching request log says `verdict failed`: drop it so the frontier can dispatch the issue again.
 F  an open issue named "(#N)" in the subject of a fork commit older than 30 min, with no open PR naming it: report FIXED-NOT-CLOSED once (never closes: closing needs evidence).
 G  an open PR on the base branch whose revci line for its current head says APPROVE (5 min old), mergeable clean, with no auto/merged-<pr>: report READY-NOT-MERGED and run one autopilot pass
    (once per head; never merges itself). Still open 15 min later: report it once more (the autopilot's HOLD lines say why).
 I  a live run whose accepted issues are ALL closed: when every one was closed as not_planned or is titled "[duplicate…", stop it (kill its process tree and its last call);
    otherwise report RUN-ON-CLOSED-ISSUES once and leave it (a closed issue can still have a finishing fix/land run). Once per run.
 J  a run with no record.jsonl 5 min after its request (the squad refused to start): kill its dashboard, drop it from active-runs, and free every issue named in its request (auto/bug-<n> and auto/disp-<n>) so autobug/autofrontier can dispatch again. Comment once ("run X never started, dispatching again", attempt n of 2) quoting the last lines of the run's launch.log. After 2 never-started runs for the same issue, keep the marker, comment the evidence, raise HEALTH, and do not retry a third time.
 K  the same never-started run that was an autofix run (auto/runof-<pr>-<sha9>): retryfix.sh re-arms the PR sha (its queued- marker, max 2 retries), so the fix is dispatched again instead of waiting for a verdict that never comes;
    after the 2 retries the PR is reported by rule M. A disp-<n> marker whose request log says `Nothing ran` (10 min old, no live run on the issue) is dropped like rule E does for `verdict failed`.
 O  a run that recorded a verdict and landed nothing (no `land` pass in its record, that event being the landing stage's own -- a router that passed by asking for children is not a landing): the issue's marker is dropped, so autofrontier/autobug dispatch it again. `failed` is not the only way a run ends without landing (#1847): a run that ended any other way (`needs-human`, `pass`) left its auto/disp-<n> behind, autofrontier.sh skips every issue holding that marker, and the frontier starved on a run that is over -- the cause health.sh reports as frontier-idle.
    A run that landed a change, an issue a live run works, and an issue an open or merged PR names keep their marker. The card goes back to the column the dispatch found it in --
    auto/disp-<n> -> Todo on the loop board, auto/bug-<n> -> To triage on the bug board -- but only from the column the dispatch set (In Progress / $SG_BUG_DOING on the bug board), so a card another step moved past it is never downgraded. Once per run; the comment carries the run id and the verdict's reasons.
 Q  a card still In Progress behind its auto/disp-<n> (or auto/bug-<n>) marker, 10 min old, that no run works: not a live run, not a run in its launch window, not a run that landed, no open or merged PR naming the issue, no fork-<n>-<sha9> marker.
    Rules E and O read a run's verdict, so a run that ended without one (rule C drops a zombie from active-runs) or that left active-runs by any other road freed nothing: the card sat In Progress, autofrontier.sh skipped the issue,
    and health.sh reported runs-board-drift for hours. Rule Q reads the state, never the run's history: the card goes back to Todo (the bug card to To triage), the marker is dropped, one comment is queued. A refused read touches nothing.
 M  an open PR on the base branch that is `dirty` or has a red CI line, unchanged for 20 min, with no live run naming it: report PR-STUCK once per head (cause + retries spent) and raise an alert (alerts.log + macOS notification).
 P  a `fork-<n>-<sha9>` marker whose commit left the fork base while the base no longer carries its change and the branch has nothing ahead to merge: an unwound landing (a hand revert, a branch reset, a base rebuilt under it).
    Its issue goes back to the column the dispatch reads (Todo; the bug board's Doing -> To triage) and the fork/disp/bug markers are dropped, so the frontier builds it again (#1830). A marker whose commit is in the base, or whose change the base holds anyway, and a branch still ahead of the base (the close retries on its head) are left alone.
 H  board state against the issue's own history (its loop comments; the REST timeline carries no project-status events), at most once per 10 min, report-only:
    RED-LOOP #N     3+ "fork suite failed after merging" comments with no green fork close since: the loop is not converging, a person must look.
    STATE-REGRESSION #N  the card is Todo/Blocked although the history says it reached Code Complete/Testing, or the card is Done while the issue is open."""
import json, os, pathlib, re, signal, subprocess, sys, time
st = pathlib.Path(os.environ["SG_STATE"]); co = pathlib.Path(os.environ["SG_CHECKOUT"]).resolve(); dry = bool(os.environ.get("JANITOR_DRY")); now = time.time()
CQ = os.environ["SG_REPO"].replace("/", "__")   # a queued comment is named <owner>__<repo>#<n>-<tag>.md: cqflush.sh posts it to the repository it names
acts = []
def say(m):   # details go to cycle.log (or stdout in a dry run); watch.log gets one summary line per pass
    acts.append(m)
    if dry: print("JANITOR " + m, flush=True)
    else: open(st / "cycle.log", "a").write(time.strftime("%T ") + "janitor " + m + "\n")
def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout
alerts = st / "alerts.log"
def alert(msg):   # alerts.log is what health.sh and the session read; the notification reaches the human away from the terminal
    open(alerts, "a").write(time.strftime("%F %T ") + msg + "\n")
    if not dry: subprocess.run([os.environ.get("JANITOR_NOTIFY", "/usr/bin/osascript"), "-e", 'display notification "' + msg.replace('"', "'")[:180] + '" with title "squad-goal"'], capture_output=True)
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
        marks = [st / "auto" / f"{kind}-{n}" for kind in ("bug", "disp")]
        if not any(m.exists() for m in marks): continue   # nothing dispatched for it: not ours to free, and no comment
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
for l in sh("/bin/ps", "-axo", "pid=,ppid=,command=").splitlines():
    p = l.split(None, 2)
    if len(p) == 3 and p[0].isdigit(): procs[int(p[0])] = (int(p[1]), p[2])
starts = {}
for l in sh("/bin/ps", "-axo", "pid=,lstart=").splitlines():
    p = l.split(None, 5)
    if len(p) == 6 and p[0].isdigit():
        try: starts[int(p[0])] = time.mktime(time.strptime(" ".join(p[1:]).strip(), "%a %b %d %H:%M:%S %Y"))
        except ValueError: pass
cwd = {}; pid = None
for l in sh("/usr/sbin/lsof", "-d", "cwd", "-Fpn").splitlines():
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
def folder(r): return pathlib.Path(r) if r.startswith("/") else co / "runs" / r
def read(f):
    v = False; pidc = None; started_at = None; acc = set(); t = f / "record.jsonl"; ver = None; rs = []; landed = False
    if not t.exists(): return None
    for l in t.read_text().splitlines():
        if '"verdict"' in l and '"event": "verdict"' in l:
            v = True
            try:   # the fold takes the last verdict event of a resumed record (ADR-0105)
                d = json.loads(l); ver = d.get("verdict"); rs = [str(x) for x in (d.get("reasons") or [])]
            except ValueError: ver = None; rs = []
        if '"land"' in l:   # only the landing stage's own event counts, and its name is in the JSON, never guessed
            try:
                d = json.loads(l)   # a node that merely asked for children (the router's `routed: continue`) is not a landing
                landed = landed or (d.get("event") == "land" and d.get("outcome") == "pass")
            except ValueError: pass
        if '"event": "call_start"' in l:
            try:
                d = json.loads(l); pidc = d.get("pid") or pidc; started_at = d.get("started_at") or started_at
            except ValueError: pass
        if '"spawn_accepted"' in l:
            m = re.search(r"GitHub issue #(\d+)", l) or re.search(r"\bissue #(\d+)", l)
            if m: acc.add(m.group(1))
    return {"verdict": v, "name": ver or "no verdict", "pid": pidc, "started_at": started_at, "acc": acc, "idle": now - t.stat().st_mtime, "failed": ver == "failed", "reasons": rs, "landed": landed}
def same_process(pidc, started_at):
    if pidc not in procs or not isinstance(started_at, (int, float)):
        return pidc in procs
    started = starts.get(pidc)
    return started is None or started <= started_at
def inside(f): return [q for q, c in cwd.items() if c == str(f) or c.startswith(str(f) + "/")]
act = st / "active-runs.txt"; names = act.read_text().split() if act.exists() else []
live = {}; keep = []; held = set(); landed_n = set()   # held: issues a run still in active-runs may be working (a run in its launch window has no record yet); landed_n: issues a finished run landed
def req_issues(f): return issues_named((f / "request.txt").read_text()) if (f / "request.txt").exists() else set()
def free_reaped(f, i):
    marks = [st / "auto" / f"disp-{n}" for n in req_issues(f) | i["acc"]]
    holder = f.name; leases = []
    caps = pathlib.Path(os.environ.get("SQUAD_CAPS_DIR") or pathlib.Path.home() / ".cache" / "agent-squad" / "caps")
    for kind in ("runs", "children"):
        for p in (caps / kind).glob("*.json"):
            try: lease = json.loads(p.read_text())
            except (OSError, ValueError): continue
            if lease.get("holder") == holder or str(lease.get("holder", "")).startswith(holder + ":"): leases.append(p)
    if marks or leases: say(f"zombie run {f.name} releases {sum(p.exists() for p in marks)} dispatch markers and {len(leases)} leases")
    if not dry:
        for p in marks + leases: p.unlink(missing_ok=True)
for r in names:
    f = folder(r); i = read(f)
    if i is None:   # J: no record.jsonl = the start was refused (e.g. vendor/skills drift); after 5 min stop its dashboard and drop it
        age = now - (f / "request.txt").stat().st_mtime if (f / "request.txt").exists() else 0
        if age > 300 and not [q for q in inside(f) if "dashboard_server" not in procs.get(q, (0, ""))[1]]:
            say(f"NEVER-STARTED run {f.name} (no record.jsonl after {int(age // 60)} min) dropped from active-runs")
            if not dry:
                subprocess.run(["bash", str(pathlib.Path(__file__).parent / "retryfix.sh"), str(f)], capture_output=True, text=True, cwd=co)   # K: re-arm the PR sha of a never-started autofix run
            free_never_started(f)   # the run frees its own issues; say() reports it in a dry run too
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of never-started run {f.name}")
            continue
        held |= req_issues(f); keep.append(r); continue
    n = f.name
    if i["verdict"]:
        if i["idle"] > 180:
            for q in inside(f):
                if "dashboard_server" not in procs.get(q, (0, ""))[1]: kill(q, f"leftover of finished run {n}")
        if i["idle"] > 3600:
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of finished run {n}")
        if i["landed"]: landed_n |= req_issues(f)
        keep.append(r); continue
    alive = bool(inside(f))
    if not alive and i["idle"] > 1200 and not (i["pid"] and same_process(i["pid"], i["started_at"])):
        say(f"zombie run {n} (no verdict, idle {int(i['idle'] // 60)} min, no process) dropped from active-runs")
        free_reaped(f, i)
        for q, (_, c) in procs.items():
            if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of zombie run {n}")
        continue
    held |= req_issues(f) | i["acc"]; keep.append(r); live[n] = i
if not dry and keep != names: act.write_text("\n".join(keep) + "\n")
byissue = {}
for n, i in live.items():
    for x in i["acc"]: byissue.setdefault(x, []).append(n)
for x, rs in byissue.items():
    mk = st / "auto" / f"janitor-dup-{x}-{'-'.join(sorted(r[-6:] for r in rs))}"
    if len(rs) > 1 and not mk.exists():
        say(f"DUP issue #{x} accepted by live runs {', '.join(rs)} (not killed)")
        if not dry: mk.touch()
repo0 = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
_is = {}
def issue_info(x):
    if x not in _is:
        o = sh("gh", "api", f"repos/{repo0}/issues/{x}", "-q", '[.state, (.state_reason // ""), .title] | @tsv').strip().split("\t")
        _is[x] = (o + ["", "", ""])[:3]
    return _is[x]
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
            mk.touch(); f = folder(n if n.startswith("/") else str(co / "runs" / n))
            for q in inside(f):
                if "dashboard_server" not in procs.get(q, (0, ""))[1]: kill(q, f"run {n} works only closed duplicate issues")
            if i["pid"] and same_process(i["pid"], i["started_at"]): kill(i["pid"], f"last call of run {n}")
    else:
        say(f"RUN-ON-CLOSED-ISSUES {n}: every issue ({nums}) is closed but not as a duplicate; not stopped (a land or fix run may be finishing)")
        if not dry: mk.touch()
def pr_count(n):   # how many PRs (open or merged) name the issue, as GitHub's own text: a refusal is not a digit string. -X GET is load-bearing: a bare `-f` makes gh POST, /search/issues answers Not Found, and its body is not "0" -- which read as a PR forever and left the marker (#1981).
    return sh("gh", "api", "-X", "GET", "search/issues", "-f", f"q=repo:{repo0} is:pr \"Refs #{n}\"", "-q", ".total_count").strip()
for d in (st / "auto").glob("disp-*"):
    req = d.read_text().strip(); lg = st / f"{req}.log"
    n = d.name[5:]; lt = lg.read_text() if lg.exists() else ""
    if now - d.stat().st_mtime < (600 if "Nothing ran" in lt else 1800): continue   # K: a refused start (`Nothing ran`) frees the issue after 10 min
    if not (("verdict failed" in lt or "Nothing ran" in lt) and n not in byissue): continue
    repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
    if sh("gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state").strip() != "open": continue
    if pr_count(n) != "0": continue   # a PR exists (open or merged)
    say(f"stale {d.name} (request {req} ended failed, issue open, no PR) removed")
    if not dry: d.unlink()
owner = os.environ.get("SG_OWNER"); proj = os.environ.get("SG_PROJECT"); bproj = os.environ.get("SG_BUG_PROJECT")
def card_status(p, n):   # the card's Status on project p, "" when unreadable (childcc.py reads it the same way)
    f = sh("gh", "api", f"/users/{owner}/projectsV2/{p}/fields?per_page=100", "--jq", '.[]|select(.name=="Status")|.id').strip()
    return sh("gh", "api", "--paginate", f"/users/{owner}/projectsV2/{p}/items?per_page=100&fields[]={f}", "--jq",
              f'.[]|select(.content_type=="Issue" and .content.number=={n})|((.fields[]?|select(.name=="Status")|.value.name.raw)//"")').strip()
if owner and proj and repo0:
    for r in names:   # O: a run that ended without landing releases its issues: marker dropped (whatever the verdict was: #1847), card back where the dispatch found it
        f = folder(r); i = read(f)
        if not (i and i["verdict"] and not i["landed"] and i["idle"] > 180): continue   # a run that landed work, or one still moving, keeps its marker
        req = f / "request.txt"
        nums = issues_named(req.read_text()) if req.exists() else set()
        marks = {n: [k for k in ("disp", "bug") if (st / "auto" / f"{k}-{n}").exists()] for n in nums}
        marks = {n: m for n, m in marks.items() if m}
        if not marks: continue
        mk = st / "auto" / f"janitor-demoted-{f.name[-6:]}"
        if mk.exists(): continue
        if not dry: mk.touch()
        why = "; ".join(i["reasons"]) or "no reason recorded"
        for n in sorted(marks, key=int):
            if n in byissue: continue   # a live run works it: not ours to release
            if sh("gh", "api", f"repos/{repo0}/issues/{n}", "-q", ".state").strip() != "open": continue
            if pr_count(n) != "0": continue   # a PR names it: the issue is past dispatch
            moves = [[proj, os.environ.get("SG_TODO", "Todo")]] if card_status(proj, n) == "In Progress" else []   # only the column the dispatch set: never downgrade a card another step moved past it (the marker is dropped either way: nothing must hold a finished run's issue back)
            if bproj and "bug" in marks[n] and card_status(bproj, n) == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])
            mks = " and ".join("auto/" + k + "-" + n for k in marks[n])
            where = f"its card is back in {moves[-1][1]} and" if moves else "its card keeps its column and"
            say(f"DISPATCH-FAILED #{n}: run {f.name} ended {i['name']} ({why}), nothing landed; {where} {mks} marker dropped so it dispatches again")
            if not dry:
                for p, s in moves:
                    subprocess.run(["bash", str(pathlib.Path(__file__).parent / "board.sh"), owner, str(p), s, str(n)], capture_output=True)
                for k in marks[n]: (st / "auto" / f"{k}-{n}").unlink()
                (st / "cq" / f"{CQ}#{n}-dispatchfail-{os.getpid()}.md").write_text(
                    f"Run {f.name} ended without a landed change (verdict {i['name']}: {why}). {where[:1].upper() + where[1:]} its {mks} marker dropped, so the next cycle dispatches the issue again.\n")
    # Q: a marker and an In Progress card with no run behind them. Only a definite answer counts: a refused read (not "open", not a digit, no status)
    # touches nothing and leaves no once-only marker, so the next pass decides. A definite "no" is remembered for 10 min, so a landed issue's old marker is not re-read every pass.
    qm = {}
    for k in ("disp", "bug"):
        for m in (st / "auto").glob(k + "-*"):
            if m.name[len(k) + 1:].isdigit(): qm.setdefault(m.name[len(k) + 1:], {})[k] = m
    for n, ms in sorted(qm.items(), key=lambda kv: int(kv[0])):
        if n in held or n in landed_n or any(now - m.stat().st_mtime < 600 for m in ms.values()) or list((st / "auto").glob(f"fork-{n}-*")): continue
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
        moves = [[proj, os.environ.get("SG_TODO", "Todo")]] if cs == "In Progress" else []
        if bs == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])
        if not moves: no(); continue
        mks = " and ".join("auto/" + k + "-" + n for k in sorted(ms))
        say(f"STALE-CARD #{n}: card In Progress behind {mks}, no run works it, no PR names it; card -> {', '.join(s for _, s in moves)}, marker dropped so it dispatches again")
        if not dry:
            for p, s in moves: subprocess.run(["bash", str(pathlib.Path(__file__).parent / "board.sh"), owner, str(p), s, str(n)], capture_output=True)
            for m in ms.values(): m.unlink()
            (st / "cq" / f"{CQ}#{n}-stalecard-{os.getpid()}.md").write_text(
                f"Its card was In Progress behind {mks}, and no run works this issue (no live run, none in its launch window; the run that took it ended without releasing it). The card is back in {moves[-1][1]} and the marker is dropped, so the next cycle dispatches the issue again.\n")
fd = os.environ.get("SG_FORK_DIR"); fb = os.environ.get("SG_FORK_BASE")
if fd and fb:
    repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
    for l in sh("git", "-C", fd, "log", "-n", "200", "--format=%h %ct %s", fb).splitlines():
        h, ct, subj = (l.split(None, 2) + ["", ""])[:3]
        m = re.search(r"\(#(\d+)\)", subj)
        if not (m and ct.isdigit() and now - int(ct) > 1800): continue
        n = m.group(1); mk = st / "auto" / f"janitor-fixed-{n}-{h}"
        if mk.exists(): continue
        if sh("gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state").strip() != "open": continue
        if sh("gh", "api", "-X", "GET", "search/issues", "-f", f"q=repo:{repo} is:pr is:open \"Refs #{n}\"", "-q", ".total_count").strip() != "0": continue   # -X GET is load-bearing here too: a bare `-f` POSTs, and /search/issues answers Not Found (#1981)
        say(f"FIXED-NOT-CLOSED #{n}: fork commit {h} \"{subj[:60]}\" names it, the issue is still open")
        if not dry: mk.touch()
    # P: a `fork-<n>-<sha9>` marker is the close pass's claim that it merged that commit into the base. An unwound
    # landing -- a hand revert, a branch reset, a base rebuilt under it -- leaves the marker claiming a delivery the
    # base does not have, and rule O does not own it (its run did not fail, it landed). No close pass can restore it
    # either: that pass merges a branch ahead of the base, and here there is nothing left to merge. So the issue goes
    # back to the column the dispatch reads and its markers are dropped (#1830).
    if owner and proj and repo0:
        def gitrc(*a): return subprocess.run(["git", "-C", fd, *a], capture_output=True, text=True).returncode
        for mk in sorted((st / "auto").glob("fork-*")):
            m = re.fullmatch(r"fork-(\d+)-([0-9a-f]{7,40})", mk.name)
            if not m: continue
            n, sha = m.group(1), m.group(2)
            if gitrc("merge-base", "--is-ancestor", sha, fb) == 0: continue   # the base holds the commit: the marker is honest
            br = (os.environ.get("SG_FORK_BRANCH") or "issue/%n").replace("%n", n)
            if gitrc("rev-parse", "-q", "--verify", "refs/heads/" + br) == 0 and sh("git", "-C", fd, "rev-list", "--count", f"{fb}..{br}").strip() not in ("", "0"): continue   # ahead: a fix commit is coming and the close retries on the head
            files = sh("git", "-C", fd, "show", "--pretty=format:", "--name-only", sha).split()
            if not files or gitrc("diff", "--quiet", sha, fb, "--", *files) == 0: continue   # the base holds the change itself: the head moved, nothing was unwound
            if n in byissue: continue                                                        # a live run works it: not ours to release
            if sh("gh", "api", f"repos/{repo0}/issues/{n}", "-q", ".state").strip() != "open": continue
            if pr_count(n) != "0": continue   # a PR names it: the issue is past dispatch
            if card_status(proj, n) not in ("In Progress", "Code Complete", os.environ.get("SG_TESTING", "Testing")): continue   # only what the landing set: a card another step moved past it is never downgraded
            km = st / "auto" / f"janitor-unwound-{n}-{sha[:9]}"
            if km.exists(): continue
            marks = [mk.name] + [f"{k}-{n}" for k in ("disp", "bug") if (st / "auto" / f"{k}-{n}").exists()]
            moves = [[proj, os.environ.get("SG_TODO", "Todo")]]
            if bproj and "bug-" + n in marks and card_status(bproj, n) == os.environ.get("SG_BUG_DOING", "Doing"): moves.append([bproj, os.environ.get("SG_BUG_TRIAGE", "To triage")])
            say(f"UNWOUND #{n}: {mk.name} claims {sha[:9]}, which {fb} no longer holds and {br} has nothing left to merge; card -> {', '.join(s for _, s in moves)}, {'+'.join(marks)} dropped so it dispatches again")
            if not dry:
                km.touch()
                for p, s in moves: subprocess.run(["bash", str(pathlib.Path(__file__).parent / "board.sh"), owner, str(p), s, str(n)], capture_output=True)
                for k in marks:
                    f2 = st / "auto" / k
                    if f2.exists(): f2.unlink()
                (st / "cq" / f"{CQ}#{n}-unwound-{os.getpid()}.md").write_text(
                    f"UNWOUND #{n}: the close pass recorded a merge of {sha[:9]} into {fb}, and {fb} no longer holds that change ({br} has nothing left ahead of it either). The landing was undone, so the issue is back in {moves[-1][1]} with its {' and '.join('auto/' + k for k in marks)} marker(s) dropped and the next cycle builds it again.\n")
base = os.environ.get("SG_BASE", "main"); repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
for l in sh("gh", "api", f"repos/{repo}/pulls?state=open&per_page=100", "--jq", r'.[]|select(.draft==false)|"\(.number) \(.head.sha) \(.base.ref)"').splitlines():
    pr, sha, pb = (l.split() + ["", "", ""])[:3]; o = st / f"revci-{pr}.out"; mg = st / "auto" / f"merged-{pr}"
    if not (pr.isdigit() and pb == base and o.exists() and not mg.exists()): continue
    last = [x for x in o.read_text().splitlines() if x.startswith(f"#{pr} {sha[:9]} ")][-1:]
    age = now - o.stat().st_mtime
    if not (last and "APPROVE" in last[0] and "SUPERSEDED" not in last[0] and age > 300): continue
    if sh("gh", "api", f"repos/{repo}/pulls/{pr}", "-q", ".mergeable_state").strip() != "clean": continue
    k = st / "auto" / f"janitor-rnm-{pr}-{sha[:9]}"; k2 = pathlib.Path(str(k) + "-still")
    if not k.exists():
        say(f"READY-NOT-MERGED #{pr} @ {sha[:9]}: APPROVE + clean for {int(age // 60)} min, not merged; running one autopilot pass")
        if not dry:
            k.touch(); subprocess.run(["bash", str(pathlib.Path(__file__).parent / "autopilot.sh")], cwd=co, capture_output=True, timeout=180)
    elif age > 900 and not k2.exists():
        say(f"READY-NOT-MERGED #{pr} @ {sha[:9]}: still unmerged {int(age // 60)} min after the kick; read the HOLD lines in watch.log")
        if not dry: k2.touch()
liveq = " ".join((folder(r) / "request.txt").read_text() for r in keep if (folder(r) / "request.txt").exists() and folder(r).name in live)
for l in sh("gh", "api", f"repos/{repo}/pulls?state=open&per_page=100", "--jq", r'.[]|select(.draft==false)|"\(.number) \(.head.sha) \(.base.ref) \(.head.ref) \(.updated_at)"').splitlines():   # M
    pr, sha, pb, hr, ua = (l.split() + ["", "", "", "", ""])[:5]
    if not (pr.isdigit() and pb == base and ua): continue
    try: upd = time.mktime(time.strptime(ua, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except ValueError: continue
    if now - upd < 1200: continue
    mk = st / "auto" / f"janitor-stuck-{pr}-{sha[:9]}"
    if mk.exists() or any((st / "auto").glob(f"dupiss-{pr}-*")): continue   # dupiss: autopilot found its issue closed as a duplicate; superseded, not stuck
    ms = sh("gh", "api", f"repos/{repo}/pulls/{pr}", "-q", ".mergeable_state").strip()
    ci = sh("bash", "-c", f"grep -h '^#{pr} {sha[:9]} ' '{st}/revci-{pr}.out' 2>/dev/null | tail -1 | sed -n 's/.*CI=\\([^ ]*\\).*/\\1/p'").strip()
    why = "conflicts with the base (dirty)" if ms == "dirty" else ("CI red (" + ci + ")" if re.search(r"failure|timed_out|cancelled", ci) else "")
    if not why or f"#{pr}" in liveq or hr in liveq: continue
    tried = sum(1 for _ in (st / "auto").glob(f"retry-{pr}-{sha[:9]}"))
    say(f"PR-STUCK #{pr} @ {sha[:9]}: {why}, unchanged {int((now - upd) // 60)} min, no live run on it" + (" (retries spent)" if tried else ""))
    if not dry: mk.touch(); alert(f"PR #{pr} stuck: {why}; no live fix run for {int((now - upd) // 60)} min")
hb = pathlib.Path(os.environ.get("SG_HUB_DIR") or co / "runs" / ".hub") / "hub-board.json"; hm = st / "auto" / "janitor-h-last"
if hb.exists() and (not hm.exists() or now - hm.stat().st_mtime > 600):
    if not dry: hm.touch()
    try: items = json.loads(hb.read_text()).get("items", [])
    except Exception: items = []
    for it in items:
        n = it.get("n"); stt = it.get("st"); op = it.get("s") == "open"
        if not (isinstance(n, int) and n >= 1102): continue
        if stt == "Done" and op:
            mk = st / "auto" / f"janitor-regress-{n}-done-open"
            if not mk.exists():
                say(f"STATE-REGRESSION #{n}: card is Done but the issue is still open")
                if not dry: mk.touch()
            continue
        if not (op and stt in ("Todo", "Blocked", "In Progress", "Code Complete")): continue
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
if acts and not dry:
    kinds = {}
    for a in acts: k = a.split()[0] if not a.startswith("kill") else ("kill " + ("dashboard" if "dashboard" in a else "leftover")); kinds[k] = kinds.get(k, 0) + 1
    print("JANITOR " + ", ".join(f"{v} {k}" for k, v in kinds.items()) + " (details in cycle.log)", flush=True)
