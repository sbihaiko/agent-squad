#!/usr/bin/env python3
"""Janitor: one conservative cleanup pass per watch cycle (JANITOR_DRY=1 prints and acts on nothing). Every action goes to stdout and $SG_STATE/cycle.log.
 A  finished run (has a verdict, 3 min old): kill every process whose cwd is inside its run folder (leftover claude/dotnet/test hosts), as a tree.
 B  finished run (1 h old): kill its idle dashboard_server.
 C  zombie run (no verdict, record.jsonl idle 20 min, no process inside its folder, last started call dead): drop it from active-runs.txt.
 D  two live runs that accepted the same issue: report once (never kills).
 E  auto/disp-<n> older than 30 min whose dispatching request log says `verdict failed`: drop it so the frontier can dispatch the issue again."""
import json, os, pathlib, re, signal, subprocess, sys, time
st = pathlib.Path(os.environ["SG_STATE"]); co = pathlib.Path(os.environ["SG_CHECKOUT"]).resolve(); dry = bool(os.environ.get("JANITOR_DRY")); now = time.time()
def say(m):
    print("JANITOR " + m, flush=True)
    if not dry: open(st / "cycle.log", "a").write(time.strftime("%T ") + "janitor " + m + "\n")
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
