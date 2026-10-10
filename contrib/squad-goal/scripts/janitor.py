#!/usr/bin/env python3
"""Janitor: one conservative cleanup pass per watch cycle (JANITOR_DRY=1 prints and acts on nothing). Every action goes to stdout and $SG_STATE/cycle.log.
 A  finished run (has a verdict, 3 min old): kill every process whose cwd is inside its run folder (leftover claude/dotnet/test hosts), as a tree.
 B  finished run (1 h old): kill its idle dashboard_server.
 C  zombie run (no verdict, record.jsonl idle 20 min, no process inside its folder, last started call dead): drop it from active-runs.txt.
 D  two live runs that accepted the same issue: report once (never kills).
 E  auto/disp-<n> older than 30 min whose dispatching request log says `verdict failed`: drop it so the frontier can dispatch the issue again.
 F  an open issue named "(#N)" in the subject of a fork commit older than 30 min, with no open PR naming it: report FIXED-NOT-CLOSED once (never closes: closing needs evidence).
 G  an open PR on the base branch whose revci line for its current head says APPROVE (5 min old), mergeable clean, with no auto/merged-<pr>: report READY-NOT-MERGED and run one autopilot pass
    (once per head; never merges itself). Still open 15 min later: report it once more (the autopilot's HOLD lines say why)."""
import json, os, pathlib, re, signal, subprocess, sys, time
st = pathlib.Path(os.environ["SG_STATE"]); co = pathlib.Path(os.environ["SG_CHECKOUT"]).resolve(); dry = bool(os.environ.get("JANITOR_DRY")); now = time.time()
acts = []
def say(m):   # details go to cycle.log (or stdout in a dry run); watch.log gets one summary line per pass
    acts.append(m)
    if dry: print("JANITOR " + m, flush=True)
    else: open(st / "cycle.log", "a").write(time.strftime("%T ") + "janitor " + m + "\n")
def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout
procs = {}
for l in sh("/bin/ps", "-axo", "pid=,ppid=,command=").splitlines():
    p = l.split(None, 2)
    if len(p) == 3 and p[0].isdigit(): procs[int(p[0])] = (int(p[1]), p[2])
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
    v = False; pidc = None; acc = set(); t = f / "record.jsonl"
    if not t.exists(): return None
    for l in t.read_text().splitlines():
        if '"verdict"' in l and '"event": "verdict"' in l: v = True
        if '"event": "call_start"' in l:
            try: pidc = json.loads(l).get("pid") or pidc
            except ValueError: pass
        if '"spawn_accepted"' in l:
            m = re.search(r"GitHub issue #(\d+)", l) or re.search(r"\bissue #(\d+)", l)
            if m: acc.add(m.group(1))
    return {"verdict": v, "pid": pidc, "acc": acc, "idle": now - t.stat().st_mtime}
def inside(f): return [q for q, c in cwd.items() if c == str(f) or c.startswith(str(f) + "/")]
act = st / "active-runs.txt"; names = act.read_text().split() if act.exists() else []
live = {}; keep = []
for r in names:
    f = folder(r); i = read(f)
    if i is None: keep.append(r); continue
    n = f.name
    if i["verdict"]:
        if i["idle"] > 180:
            for q in inside(f):
                if "dashboard_server" not in procs.get(q, (0, ""))[1]: kill(q, f"leftover of finished run {n}")
        if i["idle"] > 3600:
            for q, (_, c) in procs.items():
                if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of finished run {n}")
        keep.append(r); continue
    alive = bool(inside(f))
    if not alive and i["idle"] > 1200 and not (i["pid"] and i["pid"] in procs):
        say(f"zombie run {n} (no verdict, idle {int(i['idle'] // 60)} min, no process) dropped from active-runs")
        for q, (_, c) in procs.items():
            if "dashboard_server" in c and str(f) in c: kill(q, f"dashboard of zombie run {n}")
        continue
    keep.append(r); live[n] = i
if not dry and keep != names: act.write_text("\n".join(keep) + "\n")
byissue = {}
for n, i in live.items():
    for x in i["acc"]: byissue.setdefault(x, []).append(n)
for x, rs in byissue.items():
    mk = st / "auto" / f"janitor-dup-{x}-{'-'.join(sorted(r[-6:] for r in rs))}"
    if len(rs) > 1 and not mk.exists():
        say(f"DUP issue #{x} accepted by live runs {', '.join(rs)} (not killed)")
        if not dry: mk.touch()
for d in (st / "auto").glob("disp-*"):
    if now - d.stat().st_mtime < 1800: continue
    req = d.read_text().strip(); lg = st / f"{req}.log"
    n = d.name[5:]
    if not (lg.exists() and "verdict failed" in lg.read_text() and n not in byissue): continue
    repo = os.environ.get("SG_REPO") or sh("bash", "-c", '. "$0/env.sh" >/dev/null; echo $SG_REPO', str(pathlib.Path(__file__).parent)).strip()
    if sh("gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state").strip() != "open": continue
    if sh("gh", "api", "search/issues", "-f", f"q=repo:{repo} is:pr \"Refs #{n}\"", "-q", ".total_count").strip() != "0": continue   # a PR exists (open or merged): the issue is past dispatch
    say(f"stale {d.name} (request {req} ended failed, issue open, no PR) removed")
    if not dry: d.unlink()
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
        if sh("gh", "api", "search/issues", "-f", f"q=repo:{repo} is:pr is:open \"Refs #{n}\"", "-q", ".total_count").strip() != "0": continue
        say(f"FIXED-NOT-CLOSED #{n}: fork commit {h} \"{subj[:60]}\" names it, the issue is still open")
        if not dry: mk.touch()
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
if acts and not dry:
    kinds = {}
    for a in acts: k = a.split()[0] if not a.startswith("kill") else ("kill " + ("dashboard" if "dashboard" in a else "leftover")); kinds[k] = kinds.get(k, 0) + 1
    print("JANITOR " + ", ".join(f"{v} {k}" for k, v in kinds.items()) + " (details in cycle.log)", flush=True)
