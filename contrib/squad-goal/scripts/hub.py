#!/usr/bin/env python3
"""Squad hub: one browser page that lists every live run dashboard (agent-squad dashboard_server processes) and embeds them.
usage: hub.py [port]   default 7700, loopback only. Read-only: it discovers servers via ps/lsof and reads record.jsonl."""
import datetime as dt, glob, json, os, re, select, statistics as st, subprocess, sys, tempfile, threading, time, pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout

def _profile():
    """SG_REPO/SG_OWNER/SG_PROJECT/SG_CHECKOUT from the environment, else from env.sh (the profile matched from the cwd's origin)."""
    keys = ["SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_CHECKOUT"]
    if all(os.environ.get(k) for k in keys): return {k: os.environ[k] for k in keys}
    here = pathlib.Path(__file__).resolve().parent
    out = subprocess.run(["bash", "-c", '. "$1/env.sh" >/dev/null 2>&1; printf "%s\\n" "$SG_REPO" "$SG_OWNER" "$SG_PROJECT" "$SG_CHECKOUT"', "_", str(here)], capture_output=True, text=True, timeout=30).stdout.split("\n")
    return {k: os.environ.get(k) or (out[i] if i < len(out) else "") for i, k in enumerate(keys)}
PROF = _profile(); REPO, OWNER, PROJECT_NO = PROF["SG_REPO"], PROF["SG_OWNER"], PROF["SG_PROJECT"]
if not REPO: sys.exit("hub: no SG_REPO (set SG_PROFILE or run from a checkout a profile matches)")
def status_field():
    """REST id of the board's Status field (the items endpoint needs it to return Status values)."""
    o = subprocess.run(["gh", "api", f"/users/{OWNER}/projectsV2/{PROJECT_NO}/fields?per_page=100", "--jq", '.[]|select(.name=="Status")|.id'], capture_output=True, text=True, timeout=60).stdout.split()
    return o[0] if o else ""
HOME = pathlib.Path.home(); STATE = pathlib.Path(os.environ.get("SG_STATE", HOME / ".cache/squad-goal"))
# hub state lives in the target repo's runs area (already gitignored there), never versioned; SG_HUB_DIR overrides
HUBDIR = pathlib.Path(os.environ.get("SG_HUB_DIR") or pathlib.Path(os.environ.get("SG_CHECKOUT") or os.getcwd()) / "runs" / ".hub"); HUBDIR.mkdir(parents=True, exist_ok=True)
(HUBDIR / ".gitignore").write_text("*\n")   # keeps the folder untracked even where runs/ is not ignored
for _n in ("hub-history.json", "hub-prs.json", "hub-kills.log"):   # one-time move from the old per-user cache
    if (STATE / _n).exists() and not (HUBDIR / _n).exists(): (HUBDIR / _n).write_bytes((STATE / _n).read_bytes())
HIST = HUBDIR / "hub-history.json"; LOCK = threading.Lock()
BASE = {"pr": {"n": 31, "median": 38, "mean": 73, "max": 266, "over60": 13}, "run_median": 13, "cutoff": "2026-10-09T19:00:00Z"}
try: hist = json.loads(HIST.read_text())          # finished runs never get re-parsed: {record path: fields}
except Exception: hist = {}
live = {}                                          # unfinished runs: {path: (mtime, size, fields)}

# Billing source of a call, from the model on its outcome: Claude (sonnet/opus/fable/haiku), Grok and GPT run on the owner's subscriptions;
# every other model (deepseek, gemini…) and any call that names no model is pay-per-use API.
SUBSCRIPTION = re.compile(r"^(claude-)?(sonnet|opus|fable|haiku|grok|gpt|gemini)\b")

# The SDK prices every model it does not know at Claude rates (a DeepSeek run showed ~$410 where the platform bills a few dollars), so calls to
# gateway models are re-priced from their tokens. USD per 1M tokens: (cache hit, cache miss, output); first match wins. Short-context tier (an
# outcome sums many turns, so the per-request context is unknown and the 200k/272k surcharge cannot be applied). DeepSeek uses PEAK rates (off-peak
# is half), so it is an upper bound. Sources 2026-10-09: api-docs.deepseek.com/quick_start/pricing, docs.x.ai/docs/models, developers.openai.com/api/docs/pricing.
# For subscriptions (Grok, GPT) this is the API-equivalent, not what is billed. grok-composer has no listed price: it keeps the SDK figure.
PRICES = [
    (r"deepseek-.*pro", (0.044, 1.32, 3.96)), (r"deepseek", (0.006, 0.3, 1.2)),
    (r"grok-4\.5", (0.30, 2.00, 6.00)), (r"grok-4\.[67]", (0.50, 2.00, 6.00)),
    (r"gpt-5\.6-sol-fast", (0.80, 8.00, 40.00)), (r"gpt-5\.6-sol", (0.40, 4.00, 20.00)), (r"gpt-5\.6-terra-fast", (0.40, 4.00, 24.00)), (r"gpt-5\.6-terra", (0.20, 2.00, 12.00)),
    (r"gpt-5\.5-fast", (1.25, 12.50, 75.00)), (r"gpt-5\.5", (0.50, 5.00, 30.00)), (r"gpt-6\.1-sol-fast", (0.20, 4.00, 20.00)), (r"gpt-6\.1-sol", (0.10, 2.00, 10.00)),
]
PRICES = [(re.compile(rx), p) for rx, p in PRICES]
def priced(d):
    m = d.get("model") or ""
    for rx, (hit, miss, out) in PRICES:
        if rx.search(m):
            tin, cr = d.get("tokens_in") or 0, d.get("cache_read") or 0
            return ((cr * hit) + (max(tin - cr, 0) * miss) + ((d.get("tokens_out") or 0) * out)) / 1e6
    return d["spend"]

def scan(p):
    """Parse one record.jsonl into fields. Finished runs come from `hist`, unfinished ones are cached by (mtime, size)."""
    p = str(p)
    if p in hist and "v8" in hist[p]: return hist[p]
    try: st = os.stat(p)
    except OSError: return None
    c = live.get(p)
    if c and c[0] == st.st_mtime and c[1] == st.st_size: return c[2]
    spend = sub = api = 0.0; mod = {}; t0 = t1 = ver = None; reasons = []; land = False; issues = set(); ntok = 0; kind = "code"; fix = None; stack = None; bug = False; merge = False
    accepted, done_kids = set(), set()
    with open(p) as f:
        for i, l in enumerate(f):
            if i < 200: issues.update(re.findall(r"(?:issue|Refs) #(\d{3,5})", l))
            if i < 200 and fix is None:
                fm = re.search(r"FIX RUN[^#]{0,120}?PR #(\d{3,5})", l)
                if fm: fix = int(fm.group(1))
            if i < 200 and not merge and "CONFLICT FIX" in l: merge = True   # forkclose conflict-fix run
            if i < 200 and not bug and re.search(r"\(bug\)|branch fix/", l): bug = True   # a bug-fix run (autobug or a hand request for a bug issue)
            if i < 200 and stack is None:   # an optimistically stacked run names its parent PR ("STACKED on #N, whose PR #P")
                sm = re.search(r"STACKED on #\d{3,5}[^#]{0,40}PR #(\d{3,5})", l)
                if sm: stack = int(sm.group(1))
            if '"ts"' not in l: continue
            try: d = json.loads(l)
            except Exception: continue
            ts = d.get("ts"); e = d.get("event")
            if ts and t0 is None: t0 = ts
            if e == "spawn_accepted" and d.get("child"): accepted.add(d["child"])
            if e == "outcome" and d.get("node"): done_kids.add(d["node"])
            if e == "outcome" and d.get("spend") is not None:
                d["spend"] = priced(d)
                spend += d["spend"]
                if SUBSCRIPTION.match(d.get("model") or ""): sub += d["spend"]
                else: api += d["spend"]
            if e == "outcome":
                x = mod.setdefault(d.get("model") or "unknown", {"in": 0, "out": 0, "cache": 0, "spend": 0.0, "calls": 0})
                x["in"] += d.get("tokens_in") or 0; x["out"] += d.get("tokens_out") or 0; x["cache"] += d.get("cache_read") or 0
                x["spend"] += d.get("spend") or 0; x["calls"] += 1
            elif e == "run_start":
                k = d.get("kind") or (d.get("engine_options") or {}).get("kind") or (d.get("defaults") or {}).get("kind")   # absent today: every run is code
                if k in ("test", "gui-test"): kind = "test"
            elif e == "land" and d.get("outcome") == "pass": land = True
            elif e == "verdict": ver, reasons, t1 = d.get("verdict"), d.get("reasons") or [], ts
    r = {"spend": round(spend, 4), "sub": round(sub, 4), "api": round(api, 4), "mod": mod, "v8": 1, "t0": t0, "t1": t1, "verdict": ver, "reasons": reasons, "land": land, "issues": sorted(issues), "kind": kind, "fix": fix, "stack": stack, "bug": bug, "merge": merge, "agents": len(accepted - done_kids)}
    if ver is not None:
        hist[p] = r
        with LOCK:
            try: HIST.write_text(json.dumps(hist))
            except OSError: pass
    else: live[p] = (st.st_mtime, st.st_size, r)
    return r

def state_of(r, age):
    ver, reasons = r["verdict"], r["reasons"]
    if ver is None: return "running" if age < 1800 else "stalled"
    if ver == "failed" and r["land"] and reasons and all(str(x).startswith("ac-verify:no_ac_commands") for x in reasons): return "unverified"   # work landed, but the final gate had no acceptance command to run
    return ver

def project_dirs():
    ds = {pathlib.Path(PROF["SG_CHECKOUT"] or os.getcwd()) / "runs"}
    for x in os.environ.get("SG_PROJECTS", "").split(":"):
        if x: ds.add(pathlib.Path(x) / "runs")
    for l in sh("/bin/ps", "-axo", "command=").splitlines():
        m = re.search(r"squad\.dashboard_server\s+(\S+)", l)
        if m: ds.add(pathlib.Path(m.group(1)).parent)
    return [d for d in ds if d.is_dir()]

def all_runs():
    """Every run folder of every known project, with spend and times (history for the stats)."""
    out = []
    for d in project_dirs():
        for rd in d.glob("run-*"):
            r = scan(rd / "record.jsonl")
            if not r: continue
            t0 = r["t0"]
            if not t0:
                m = re.match(r"run-(\d{8})-(\d{6})", rd.name)
                t0 = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else 0
            t1 = r["t1"] or time.time()
            out.append({"id": rd.name, "dir": str(rd), "t0": t0, "t1": t1, "spend": r["spend"], "sub": r["sub"], "api": r["api"], "mod": r["mod"], "dur": (t1 - t0) / 60, "done": r["verdict"] is not None})
    return out

TITLE = {}   # issue number -> short title, refreshed from the board cache (no extra API calls)
def _titles():
    for it in board_cache.get("items", []):
        t = re.sub(r"^GUI test squad( pilot)?: ", "", it.get("t") or ""); TITLE[it["n"]] = t[:60] + ("…" if len(t) > 60 else "")
def demoted(st, r, closed):
    """A failed / unverified / needs-human run whose every issue was closed afterwards (verified by hand, evidence on the issue) is no longer open business."""
    if st in ("failed", "unverified", "needs-human") and r["issues"] and all(int(n) in closed for n in r["issues"]) and (r["land"] or st == "needs-human"): return "verified"
    return st
LIVE_STATES = ("running", "starting", "stalled")
def group_of(r):
    """RUNNING holds a live process (running/starting/stalled). An ended run, needs-human included, goes to an age bucket."""
    if r.get("live", True) and r.get("state") in LIVE_STATES: return "running"
    h = (r.get("age") or 0) / 3600
    return "1h" if h <= 1 else "2h" if h <= 2 else "4h" if h <= 4 else "8h" if h <= 8 else "d"

def loop_caps():
    """Occupancy denominators from the live workflow graph. max_parallel is run.max_parallel; max_children is run.max_children or a dispatch node's. None when the graph cannot be read."""
    p = pathlib.Path(os.environ.get("SG_CHECKOUT") or os.getcwd()) / "docs/squad/workflows/dynamic.graph.json"
    try:
        d = json.loads(p.read_text())
        run = d.get("run") if isinstance(d.get("run"), dict) else {}
        mp, mc = run.get("max_parallel"), run.get("max_children")
        if not isinstance(mc, int):
            for n in d.get("nodes") or []:
                if isinstance(n, dict) and isinstance(n.get("max_children"), int):
                    mc = n["max_children"]; break
        if isinstance(mp, int) and mp > 0 and isinstance(mc, int) and mc > 0:
            return {"max_parallel": mp, "max_children": mc}
    except Exception:
        pass
    return None

def usd_txt(v):
    if v is None: return "—"
    if v >= 100: return "$" + str(int(round(v)))
    return f"${v:.2f}"

def group_header(kind, rows, caps):
    """RUNNING shows occupancy (live runs and live children over the caps); other groups keep count · cost. Spend is the tooltip."""
    n = len(rows)
    spend = sum((r.get("spend") or 0) for r in rows)
    agents = sum((r.get("agents") or 0) for r in rows)
    title = usd_txt(spend)
    if kind == "running":
        if caps and isinstance(caps.get("max_parallel"), int) and isinstance(caps.get("max_children"), int):
            return {"text": f"{n}/{caps['max_parallel']} runs · {agents}/{caps['max_children']} agents", "title": title}
        return {"text": f"{n} · {agents}", "title": title}
    return {"text": f"{n} · {title}", "title": title}
def run_rank(st, live=True):
    return 0 if live and st in LIVE_STATES else 1
def runs():
    _titles()
    out = []
    for l in sh("/bin/ps", "-axo", "pid=,command=").splitlines():
        m = re.match(r"\s*(\d+)\s+\S*python\S*\s+-m\s+squad\.dashboard_server\s+(\S+)", l)
        if not m: continue
        pid, d = m.group(1), m.group(2)
        port = re.search(r"TCP 127\.0\.0\.1:(\d+)|n127\.0\.0\.1:(\d+)", sh("/usr/sbin/lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN", "-Fn"))
        if not port: continue
        p = pathlib.Path(d) / "record.jsonl"
        if not pathlib.Path(d).exists(): continue
        if re.search(r"/pytest-\d+/|/pytest-of-", d): continue   # dashboards started by agent-squad's own test suite are fixtures, not runs   # a dashboard server outliving its run folder (worktree removed) must not break the list
        r = scan(p)
        if r is None: out.append({"id": os.path.basename(d), "port": port.group(1) or port.group(2), "state": "starting", "issues": [], "age": 0, "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": 0, "spend": 0, "dur": 0, "kind": "code", "live": True, "agents": 0}); continue
        age = time.time() - p.stat().st_mtime; st = state_of(r, age)
        st = demoted(st, r, {i["n"] for i in board_cache.get("items", []) if i.get("s") == "closed"})
        rank = run_rank(st, live=True)
        t1 = r["t1"] or time.time()
        out.append({"id": os.path.basename(d), "port": port.group(1) or port.group(2), "state": st, "issues": r["issues"], "age": int(age), "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": rank, "spend": r["spend"], "dur": round((t1 - (r["t0"] or t1)) / 60, 1), "kind": r.get("kind", "code"), "fix": r.get("fix"), "stack": r.get("stack"), "bug": r.get("bug"), "merge": r.get("merge"), "titles": {i: TITLE.get(int(i), "") for i in r["issues"]}, "live": True, "agents": r.get("agents") or 0})
    for f in sorted((STATE / "tests").glob("test-*.json")):   # coordinator test passes (test.sh)
        try: d = json.loads(f.read_text())
        except Exception: continue
        age = time.time() - f.stat().st_mtime
        if d.get("state") != "running" and age > 4 * 3600: continue
        st = d.get("state") or "running"; t1 = d.get("t1") or time.time()
        out.append({"id": f.stem, "port": "", "url": "/test/" + f.stem, "state": st, "issues": [str(d.get("issue"))], "age": int(age), "project": "test", "rank": run_rank(st, live=st in LIVE_STATES), "spend": 0, "dur": round((t1 - d.get("t0", t1)) / 60, 1), "kind": "test", "titles": {str(d.get("issue")): TITLE.get(int(d.get("issue") or 0), "")}, "live": st in LIVE_STATES, "agents": 0})
    out.sort(key=lambda r: (r["rank"], r["age"]))   # live runs first, finished next; then most recently updated first
    return out

def stats(period, src="both"):
    fmt = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}[period]
    b = {}; amt = (lambda r: r["spend"]) if src == "both" else (lambda r: r["sub"] if src == "sub" else r["api"])
    allr = [dict(r, spend=amt(r)) for r in all_runs()]
    for r in allr:
        k = time.strftime(fmt, time.localtime(r["t0"])); x = b.setdefault(k, {"t": k, "spend": 0.0, "runs": 0, "durs": []})
        x["spend"] += r["spend"]; x["runs"] += 1
        if r["done"]: x["durs"].append(r["dur"])
    ks = sorted(b)[-48:]
    rows = [{"t": k, "spend": round(b[k]["spend"], 2), "runs": b[k]["runs"], "median_dur": round(st.median(b[k]["durs"]), 1) if b[k]["durs"] else None} for k in ks]
    now = time.localtime(); tot = {"today": 0.0, "month": 0.0, "all": 0.0}
    for r in allr:
        lt = time.localtime(r["t0"]); tot["all"] += r["spend"]
        if lt[:2] == now[:2]: tot["month"] += r["spend"]
        if lt[:3] == now[:3]: tot["today"] += r["spend"]
    mt = {}
    for r in allr:
        for m, v in r["mod"].items():
            if src == "sub" and not SUBSCRIPTION.match(m) or src == "api" and SUBSCRIPTION.match(m): continue
            x = mt.setdefault(m, {"model": m, "in": 0, "out": 0, "cache": 0, "spend": 0.0, "calls": 0})
            for k in ("in", "out", "cache", "calls"): x[k] += v[k]
            x["spend"] += v["spend"]
    models = sorted(({**x, "spend": round(x["spend"], 2)} for x in mt.values()), key=lambda x: -(x["in"] + x["out"]))
    return {"buckets": rows, "totals": {k: round(v, 2) for k, v in tot.items()}, "models": models}

BOARD = HUBDIR / "hub-board.json"
try: board_cache = json.loads(BOARD.read_text())
except Exception: board_cache = {"t": 0, "items": [], "prs": [], "err": None}
def apply_open_blockers(items, nodes):
    """Attach open native blockers (`Issue.blockedBy`) onto each item as `b`. A closed blocker drops off by itself."""
    by = {n.get("number"): n for n in nodes or []}
    for i in items:
        node = by.get(i.get("n"))
        if not node: continue
        open_ns = [x["number"] for x in (node.get("blockedBy") or {}).get("nodes") or [] if (x.get("state") or "").upper() == "OPEN"]
        if open_ns: i["b"] = open_ns
        else: i.pop("b", None)
    return items
def refresh_board():
    """Board 4 statuses (REST) + one GraphQL page of Issue.blockedBy + open PRs, every 60 s; last good data survives errors and restarts."""
    sf = ""
    while True:
        try:
            sf = sf or status_field()
            out = subprocess.run(["gh", "api", "--paginate", f"/users/{OWNER}/projectsV2/{PROJECT_NO}/items?per_page=100&fields[]={sf}", "--jq",
                '.[]|select(.content_type=="Issue")|{n:.content.number,t:.content.title,s:.content.state,c:.content.closed_at,st:((.fields[]|select(.name=="Status")|.value.name.raw)//null),l:[.content.labels[]?.name]}'],
                capture_output=True, text=True, timeout=120).stdout
            items = [json.loads(l) for l in out.splitlines() if l.strip()]
            po = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO}/pulls?state=open&per_page=100", "--jq",
                '.[]|{n:.number,t:.title,d:.draft,u:.updated_at,h:.head.sha,o:.created_at}'], capture_output=True, text=True, timeout=60).stdout
            prs = [json.loads(l) for l in po.splitlines() if l.strip()]
            for x in prs:   # per open PR: check-run conclusions and mergeable state (2 REST calls each; open PRs are few)
                try:
                    ck = subprocess.run(["gh", "api", f"repos/{REPO}/commits/{x['h']}/check-runs?per_page=100", "--jq", "[.check_runs[]|(.conclusion//.status)]"],
                                        capture_output=True, text=True, timeout=30).stdout
                    x["ci"] = json.loads(ck or "[]")
                    x["ms"] = subprocess.run(["gh", "api", f"repos/{REPO}/pulls/{x['n']}", "--jq", ".mergeable_state"], capture_output=True, text=True, timeout=30).stdout.strip()
                except Exception: pass
            pc = subprocess.run(["gh", "api", f"repos/{REPO}/pulls?state=closed&per_page=100&sort=updated&direction=desc", "--jq",
                '.[]|{n:.number,t:.title,c:(.merged_at//.closed_at),m:(.merged_at!=null)}'], capture_output=True, text=True, timeout=60).stdout
            prev = [json.loads(l) for l in pc.splitlines() if l.strip()]
            pt = subprocess.run(["gh", "api", f"search/issues?q=repo:{REPO}+is:pr+is:closed&per_page=1", "--jq", ".total_count"], capture_output=True, text=True, timeout=60).stdout.strip()
            prev_n = int(pt) if pt.isdigit() else len(prev)   # the real total; the list itself is only the latest page
            oi = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO}/issues?state=open&per_page=100", "--jq",
                '.[]|select(.pull_request==null)|{n:.number,t:.title,s:.state,c:null,st:null,l:[.labels[].name]}'], capture_output=True, text=True, timeout=120).stdout
            have = {i["n"] for i in items}   # open issues that never reached the board have no state either
            items += [i for i in (json.loads(l) for l in oi.splitlines() if l.strip()) if i["n"] not in have]
            own, name = REPO.split("/", 1)   # one GraphQL page of open issues; not one REST call per card
            gq = 'query{repository(owner:"%s",name:"%s"){issues(first:100,states:OPEN){nodes{number blockedBy(first:20){nodes{number state}}}}}}' % (own, name)
            go = subprocess.run(["gh", "api", "graphql", "-f", "query=" + gq], capture_output=True, text=True, timeout=60).stdout
            try: apply_open_blockers(items, ((json.loads(go) or {}).get("data") or {}).get("repository", {}).get("issues", {}).get("nodes") or [])
            except Exception: pass
            lo = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO}/labels?per_page=100", "--jq", '.[]|{n:.name,c:.color}'], capture_output=True, text=True, timeout=60).stdout
            lc = {x["n"]: x["c"].lower() for x in (json.loads(l) for l in lo.splitlines() if l.strip())} or board_cache.get("lc", {})   # label colors as GitHub shows them
            if items: board_cache.update(t=time.time(), items=items, prs=prs, prev=prev, prev_n=prev_n, lc=lc, err=None); BOARD.write_text(json.dumps(board_cache))
        except Exception as e: board_cache["err"] = str(e)[:80]
        for _ in range(30):   # every 60 s, or within 2 s of a board move made through scripts/board.sh (board-dirty flag)
            time.sleep(2)
            try:
                if (STATE / "board-dirty").stat().st_mtime > board_cache.get("t", 0): break
            except OSError: pass
ORDER = ["In Progress", "Code Complete", "Testing", "Todo", "Blocked", "No status", "Done"]
def pr_state(x):
    """One word for an open PR, from GitHub facts (CI, mergeable state) and the squad's review files (revci-<pr>.out, auto/launched-*).
    Worst first: draft, conflict, ci-failed, changes, ci-running, reviewing, ready, merged-soon, awaiting-review."""
    if x.get("d"): return ["draft", "draft"]
    ci = x.get("ci") or []; sha9 = (x.get("h") or "")[:9]; n = x["n"]
    if x.get("ms") == "dirty": return ["conflict", "merge conflict"]
    if any(c in ("failure", "timed_out", "action_required", "startup_failure") for c in ci): return ["broke", "CI failed"]
    line = ""
    try: line = [l for l in (STATE / f"revci-{n}.out").read_text().splitlines() if l.startswith(f"#{n} {sha9} ")][-1]
    except Exception: pass
    if "CHANGES" in line: return ["changes", "changes requested"]
    if "BLOCKED" in line: return ["broke", "review blocked"]
    if any(c in ("queued", "in_progress", "pending", "waiting", "requested") for c in ci): return ["running", "CI running"]
    if not line and (STATE / "auto" / f"launched-{n}-{sha9}").exists(): return ["running", "reviewing"]
    if "APPROVE" in line: return ["ready", "merging" if (STATE / "auto" / f"merged-{n}").exists() else "ready to merge"]
    return ["idle", "awaiting review"]
def board():
    by = {}
    for i in board_cache["items"]:
        st = "Done" if i["s"] == "closed" else (i["st"] or "No status")   # a closed issue is Done whatever the card still says
        row = {"n": i["n"], "t": i["t"], "c": i.get("c"), "l": i.get("l") or []}
        if i.get("b"): row["b"] = i["b"]
        by.setdefault(st, []).append(row)
    names = [n for n in ORDER if n in by or n != "No status"] + sorted(n for n in by if n not in ORDER)   # the working states always show, even at 0
    def wave(x):   # `wave:NN` label first, then issue number; cards with no wave go last
        w = [int(m.group(1)) for l in x["l"] for m in [re.match(r"wave:(\d+)$", l)] if m]
        return (min(w) if w else 10**9, x["n"])
    groups = [{"name": n, "n": len(by.get(n, [])), "items": sorted(by.get(n, []), key=lambda x: (x["c"] or "", x["n"]), reverse=True) if n == "Done" else sorted(by.get(n, []), key=wave)} for n in names]
    prs = sorted(board_cache["prs"], key=lambda p: p["u"], reverse=True)
    for x in prs: x["ps"] = pr_state(x)
    prev = sorted(board_cache.get("prev", []), key=lambda p: p["c"] or "", reverse=True)
    return {"t": board_cache["t"], "err": board_cache["err"], "prev": {"name": "Closed PRs", "n": max(board_cache.get("prev_n") or 0, len(prev)), "items": [{"n": p["n"], "t": p["t"] + ("" if p["m"] else " (closed, not merged)"), "c": p["c"]} for p in prev]}, "prs": {"name": "Open PRs", "n": len(prs), "items": [{"n": p["n"], "t": p["t"], "u": p["u"], "ps": p["ps"], "o": p.get("o")} for p in prs]}, "groups": groups, "labels": sorted({l for i in board_cache["items"] for l in (i.get("l") or [])}), "lc": board_cache.get("lc", {})}

_issue_cache = {}
def issue_detail(n):
    """Issue (or PR) body + the last 5 comments, fetched on click and cached 60 s. n is an int, never interpolated raw."""
    hit = _issue_cache.get(n)
    if hit and time.time() - hit[0] < 60: return hit[1]
    g = lambda path: json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=30).stdout or "null")
    it = g(f"repos/{REPO}/issues/{n}")
    if not it or "number" not in it: return {"err": "not found"}
    cm = g(f"repos/{REPO}/issues/{n}/comments?per_page=100") or []
    d = {"n": n, "title": it["title"], "state": it["state"], "pr": "pull_request" in it, "url": it["html_url"], "by": it["user"]["login"],
         "created": it["created_at"], "updated": it["updated_at"], "labels": [l["name"] for l in it["labels"]], "body": (it.get("body") or "")[:3000],
         "total": len(cm), "comments": [{"by": c["user"]["login"], "at": c["created_at"], "body": (c.get("body") or "")[:1200]} for c in cm[-5:]]}
    if d["pr"]:   # a PR also gets its branch, size, merge state, CI summary and the latest review verdicts
        p = g(f"repos/{REPO}/pulls/{n}") or {}
        ck = (g(f"repos/{REPO}/commits/{p['head']['sha']}/check-runs?per_page=100") or {}).get("check_runs", []) if p.get("head") else []
        cnt = {}
        for c in ck: k = c.get("conclusion") or c["status"]; cnt[k] = cnt.get(k, 0) + 1
        rv = {}
        for r in g(f"repos/{REPO}/pulls/{n}/reviews?per_page=100") or []:
            if r["state"] != "COMMENTED": rv[r["user"]["login"]] = r["state"]
        d["pull"] = {"head": p.get("head", {}).get("ref"), "base": p.get("base", {}).get("ref"), "add": p.get("additions"), "del": p.get("deletions"), "files": p.get("changed_files"),
                     "draft": p.get("draft"), "merged": bool(p.get("merged_at")), "mstate": p.get("mergeable_state"), "checks": cnt, "reviews": rv}
        if d["pull"]["merged"]: d["state"] = "merged"
    _issue_cache[n] = (time.time(), d); return d

PRS = HUBDIR / "hub-prs.json"
try: prs_cache = json.loads(PRS.read_text())     # last good PR data survives a restart
except Exception: prs_cache = {"t": 0, "rows": [], "err": None}
def refresh_prs():
    while True:
        try:
            out = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO}/pulls?state=closed&per_page=100&sort=updated&direction=desc", "--jq", ".[]|select(.merged_at!=null)|{n:.number,c:.created_at,m:.merged_at}"], capture_output=True, text=True, timeout=120).stdout
            rows = []
            for l in out.splitlines():
                p = json.loads(l)
                if p["n"] < 1115: continue
                f = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                rows.append({"n": p["n"], "merged": p["m"], "min": round((f(p["m"]) - f(p["c"])).total_seconds() / 60, 1)})
            if rows: prs_cache.update(t=time.time(), rows=sorted(rows, key=lambda r: r["merged"]), err=None)
            if rows: PRS.write_text(json.dumps(prs_cache))
        except Exception as e: prs_cache["err"] = str(e)[:80]
        time.sleep(600)

def speed():
    rows = prs_cache["rows"]; cut = BASE["cutoff"]
    def agg(v): return {"n": len(v), "median": round(st.median(v), 1), "mean": round(st.mean(v), 1), "max": round(max(v), 1), "over60": sum(x > 60 for x in v)} if v else {"n": 0}
    after = [r["min"] for r in rows if r["merged"] >= cut]
    rounds = []
    for f in glob.glob(str(STATE / "auto/rounds-*")):
        try: rounds.append(len([x for x in open(f) if x.strip()]))
        except OSError: pass
    kills = []
    try: kills = [int(x.split()[0]) for x in open(HUBDIR / "hub-kills.log") if x.strip()]
    except OSError: pass
    openp = []; now_ts = time.time()   # epoch arithmetic: dt.UTC needs 3.11 and this must run on the system python3 too
    for x in board_cache.get("prs", []):
        try: openp.append({"n": x["n"], "min": round((now_ts - dt.datetime.fromisoformat(x["o"].replace("Z", "+00:00")).timestamp()) / 60)})
        except Exception: pass
    openp.sort(key=lambda r: -r["min"])
    done = [r["dur"] for r in all_runs() if r["done"] and r["dur"] > 0]
    today = [r["dur"] for r in all_runs() if r["done"] and r["dur"] > 0 and time.localtime(r["t0"])[:3] == time.localtime()[:3]]
    return {"baseline": BASE, "pr": {"open": openp[:8], "after": agg(after), "series": [{"n": r["n"], "min": r["min"], "after": r["merged"] >= cut} for r in rows[-40:]], "stale": bool(prs_cache["err"]) or time.time() - prs_cache["t"] > 1800},
            "runs": {"today_median": round(st.median(today), 1) if today else None, "all_median": round(st.median(done), 1) if done else None},
            "rounds": {"prs": len(rounds), "mean": round(st.mean(rounds), 2) if rounds else None}, "kills": {"count": len(kills), "last": max(kills) if kills else None}}

# One run-state vocabulary for the hub's run badges. The run dashboard's RUN_STATES (dashboard/core/sources.js) names and paints the
# same states alike; tests/test_run_state_vocabulary.py holds the two tables equal. `color` is a palette variable of :root; `css` is extra style.
RUN_STATES = [
    {"state": "completed", "label": "Run completed", "color": "ok", "css": ""},
    {"state": "verified", "label": "Failed or unverified run, verified by hand", "color": "ok", "css": "font-style:italic"},
    {"state": "unverified", "label": "Work landed, the final gate had no command", "color": "ok", "css": "border-style:dashed"},
    {"state": "failed", "label": "Run failed", "color": "err", "css": ""},
    {"state": "needs-human", "label": "Run needs a person", "color": "warn", "css": ""},
    {"state": "running", "label": "Run in progress", "color": "run", "css": ""},
    {"state": "starting", "label": "Run starting", "color": "run", "css": "border-style:dotted"},
    {"state": "stalled", "label": "Run stalled", "color": "warn", "css": "border-style:dashed"},
    {"state": "no verdict yet", "label": "No verdict yet", "color": "none", "css": ""},
]

PAGE = r"""<!doctype html><meta charset=utf-8><title>Squad hub</title>
<style>
:root{--bg:#0f1115;--panel:#161a21;--line:#2a303b;--fg:#d8dde6;--mute:#8a93a3;--ok:#3ecf8e;--warn:#f5b041;--err:#ff6b6b;--run:#2dd4bf;--none:#b79cf6;--pin:#5aa9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px ui-monospace,Menlo,monospace;display:flex;height:100vh}
#side{width:300px;border-right:1px solid var(--line);overflow:auto;background:var(--panel);flex:none}
#top{position:sticky;top:0;background:linear-gradient(#162033,#121a28);padding:10px 10px 10px 12px;border-bottom:2px solid var(--pin);border-left:3px solid var(--pin);z-index:2;box-shadow:0 2px 8px #0008}#top>b{font-size:13px;letter-spacing:.12em;color:var(--pin)}
#top .row{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:6px}
button{background:#222a35;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:2px 8px;cursor:pointer;font:inherit}
button.on{border-color:var(--pin);color:var(--pin)}
.kd{margin-right:5px;font-size:12px}.r{padding:7px 10px;border-top:1px solid var(--line);cursor:pointer;display:flex;gap:6px;justify-content:space-between}.r:hover{background:#1c222b}
.r .st{font-size:11px;color:#c9d1d9;opacity:.8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
.r.show{background:#1f2a3a;box-shadow:inset 3px 0 0 var(--pin)}.r.pinned .pn{color:var(--pin)}
.pn{color:var(--mute);flex:none}.gh.op{color:var(--fg)}.gh+.r{border-top:0}
.b{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;border:1px solid}
.landed,.pass,.ok{color:var(--ok)}
/*RUN_STATE_CSS*/
.m{color:var(--mute);font-size:11px;margin-top:2px}
#q{width:100%;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:3px 6px;font:inherit;margin-top:6px}
.sp{border-bottom:1px solid var(--line);font-size:11px}#mdl>summary{border-top:0}.sp>summary,.gh{list-style:none;display:flex;align-items:center;gap:6px;height:34px;padding:0 10px;font-size:12px;font-weight:600;color:var(--fg);cursor:pointer;user-select:none;background:#10141a;border-top:1px solid var(--line);outline:none}
.sp>summary::-webkit-details-marker{display:none}.sp>summary::before,.gh::before{content:"▸";flex:none;width:10px;color:var(--mute);font-weight:400}.sp[open]>summary::before,.gh.op::before{content:"▾"}.sp>summary:hover,.gh:hover{background:#171c24}.sp>summary:focus-visible,.gh:focus-visible{box-shadow:inset 0 0 0 1px var(--pin)}
.sp>summary .sr,.gh .sr{margin-left:auto;font-weight:400;color:var(--mute);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}.sp[open]>summary .sr{display:none}.sp .sb{padding:4px 10px 10px}
.sp h4{display:flex;align-items:baseline;justify-content:space-between;gap:6px;margin:10px 0 6px;font-size:12px;color:var(--fg);font-weight:600}.sp h4 small{font-weight:400;color:var(--mute);font-size:10px;white-space:nowrap}
.tot{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.tot div{background:#10141a;border:1px solid var(--line);border-radius:6px;padding:4px 6px;font-size:9px;color:var(--mute)}.tot b{display:block;font-size:15px;color:var(--fg);font-weight:600}
.kr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-auto-rows:min-content;column-gap:8px;row-gap:2px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line);font-size:11px}.kr:last-child{border:0}.kr .l{grid-row:1/3;min-width:0;font-size:11px;line-height:1.3}.kr .l small{display:block;color:var(--mute);font-size:9.5px}.kr b{grid-column:2;justify-self:end;font-size:14px;font-weight:600;line-height:1}.kr b small{font-size:10px;font-weight:400;color:var(--mute)}
.kr .d{grid-column:2;justify-self:end;font-size:9px;padding:1px 6px;border-radius:9px;white-space:nowrap;background:#1c222b;color:var(--mute)}.kr .d.good{background:#14301f;color:#5fd38d}.kr .d.bad{background:#3a1a1a;color:#ff7b7b}
#mbody{padding:0}.mc{background:#10141a;border:1px solid var(--line);border-radius:8px;padding:10px;margin:6px 0 8px}.mc .mt{font-size:12px;font-weight:600;color:var(--fg);margin:0 0 2px}.mc .mx{font-size:11px;color:var(--mute);margin:0 0 8px;line-height:1.35}
.mr{display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;gap:5px 8px;padding:7px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);margin-bottom:5px;font-size:12px}.mr.p{border-color:#2f5d45;background:#13211b}.mr .rk{flex:none;width:22px;height:22px;border-radius:11px;background:#222a35;color:var(--mute);font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center}.mr.p .rk{background:var(--ok);color:#0b1a12}
.mr .mn{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3;font-size:12px;color:var(--fg)}.mr .ct{grid-column:1/3;display:flex;overflow:hidden;gap:3px;align-items:center;min-width:0}.mr .pv{flex:1 1 auto;min-width:0;font-size:9.5px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mr .pv b{color:var(--ok);font-weight:600}.mr .el{flex:none;font-size:9px;color:var(--mute);margin-left:2px}.mr .mn small{display:block;font-size:10px;color:var(--mute)}.mr .ef{flex:none;width:62px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:11px;height:26px;padding:0 2px;cursor:pointer;text-overflow:ellipsis}.mr .ef.set{color:var(--warn);border-color:#6b5223}.mr .hr{flex:none;width:50px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:10.5px;height:26px;padding:0 2px;text-align:center}.mr .hr::placeholder{color:var(--mute)}.mr .hr:focus{outline:none;border-color:var(--pin)}.mr .hr.set{color:var(--warn);border-color:#6b5223}
.mr .mb{flex:none;display:flex;gap:2px;margin-left:4px}.mr .mb button{min-width:24px;height:24px;padding:0;font-size:12px;line-height:1}.mr .mb button:hover:not(:disabled){background:#2e3846;border-color:#4a5566}.mr .mb button:disabled{opacity:.25;cursor:default}.mr .mb button.x:hover:not(:disabled){color:var(--err);border-color:var(--err)}
.madd{display:flex;gap:5px;margin-top:4px}.madd input{flex:1;min-width:0;height:28px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:0 8px;font:inherit;font-size:12px}.madd input:focus{outline:none;border-color:var(--pin)}.madd button{height:28px;padding:0 12px}
.msave{position:sticky;bottom:0;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 0 0;margin:12px 0 0;background:var(--panel);border-top:1px solid var(--line);font-size:11px;z-index:1}.msave.dirty{background:#1f1b12;border-top-color:#6b5223}.msave button{height:28px;padding:0 12px}.msave button#msv:not(:disabled){background:var(--warn);color:#1a1300;border-color:var(--warn);font-weight:600}.msave button:disabled{opacity:.35;cursor:default}
.msave .st{flex:1 1 100%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mute)}.msave .st.err{color:var(--err)}.msave .st .dot{display:inline-block;width:8px;height:8px;border-radius:4px;background:var(--warn);margin-right:5px;vertical-align:middle}.msave .st.okm{color:var(--ok)}.sub{display:flex;flex-direction:column;gap:6px;margin:8px 0;padding:8px 10px;background:#10141a;border:1px solid var(--line);border-radius:8px;font-size:11px}
.sub .hd{display:flex;align-items:center;gap:6px;margin-bottom:2px}.sub .nm{font-weight:600;font-size:13px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sub .sv{font-weight:600;font-size:14px}.sub .sv.good{color:#5fd38d}.sub .sv.bad{color:#ff7b7b}.sub .sv.none{color:var(--mute);font-size:11px;font-weight:400}
.sub .qw{display:flex;flex-direction:column;gap:3px}.sub .ql{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:11px;color:var(--mute)}.sub .ql b{color:var(--fg);font-weight:600;font-size:12px}.sub .ql .rst{white-space:nowrap;font-size:10px}
.sub .qw.sm .ql{font-size:10px}.sub .qw.sm .ql b{font-size:10.5px;color:#b9c0cc}.sub .qw .tr{position:relative;height:8px;border-radius:4px;background:#1c222b}.sub .qw.sm .tr{height:4px;border-radius:2px}.sub .qw .fl{height:100%;border-radius:inherit;transition:width .3s}
.sub .fl.lv-ok{background:var(--ok)}.sub .fl.lv-warn{background:var(--warn)}.sub .fl.lv-err{background:var(--err)}.sub .fl.lv-est{background:#5aa9ff}.sub .qw .tk{position:absolute;top:-3px;bottom:-3px;width:2px;background:#d8dde6;border-radius:1px}
.sub .fr{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:-1px}.sub .fs{color:var(--mute);font-size:9.5px;margin-left:auto}.sub .chip{display:inline-block;padding:0 7px;border-radius:9px;font-size:10px;font-weight:600;line-height:16px}.sub .chip.err{background:#3a1a1a;color:#ff7b7b;border:1px solid #ff6b6b55}
.sub .xx{padding:1px 7px;border-radius:9px;background:#14301f;color:#5fd38d;font-size:10px;margin-left:5px}.sub .xx.bad{background:#3a1a1a;color:#ff7b7b}
.sub .pr{display:flex;gap:6px;align-items:center;color:var(--mute);font-size:10px}.sub select{flex:1;min-width:0;max-width:100%}.sub input{padding:3px 6px;font-size:11px;min-width:0}.sub input[data-f=amount]{flex:1}.sub input[data-f=cur]{width:56px;text-transform:uppercase}
.sub .dt{color:var(--mute);font-size:10px;line-height:1.4}.sub .dt b{color:var(--fg);font-weight:600}.sub .warn{color:var(--warn);font-size:10px}
.mb{margin-top:2px}.mb .lab{display:flex;justify-content:space-between;font-size:10px;color:var(--mute);margin-bottom:3px}.mb .lab b{color:var(--fg);font-weight:600}.mb .tr{position:relative;height:9px;border-radius:5px;background:#1c222b;overflow:visible}.mb .fl{height:100%;border-radius:5px;background:#e0a23a}.mb .fl.good{background:#3ecf8e}.mb .fl.bad{background:#d9534f}.mb .fl.warnq{background:#5aa9ff}.mb .tk{position:absolute;top:-3px;bottom:-3px;width:2px;background:#d8dde6;border-radius:1px}.mb .sc2{display:flex;justify-content:space-between;font-size:9px;color:var(--mute);margin-top:2px}
.tot3{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:4px}.tot3 div{background:#10141a;border:1px solid var(--line);border-radius:6px;padding:6px 7px;font-size:10px;color:var(--mute);line-height:1.3}.tot3 b{display:block;font-size:15px;color:var(--fg);font-weight:600;margin:1px 0}.tot3 span{font-size:9px;display:block}.tot3 div:last-child b{color:var(--ok)}.subx{color:var(--mute);font-size:10.5px;line-height:1.45;margin:8px 0 4px}
.pie{display:flex;flex-direction:column;gap:6px;align-items:stretch}.pie svg{flex:none;width:112px;height:112px;margin:0 auto}.pie .lg{flex:1;min-width:0;font-size:11px}.pie .lg div{display:flex;align-items:center;gap:6px;padding:2px 0}.pie .lg i{width:9px;height:9px;border-radius:2px;flex:none}.pie .ln{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pie .lg b{font-weight:600}.warnl{margin-top:4px;font-size:9px;color:var(--warn)}
.up{color:var(--err)}.dn{color:var(--ok)}svg{display:block;width:100%}
#main{flex:1;min-width:0;display:grid;gap:4px;padding:4px}
.cell{display:flex;flex-direction:column;min-height:0;min-width:0}.cell small{color:var(--mute);padding:2px 4px;display:flex;justify-content:space-between}
#board{width:360px;border-left:1px solid var(--line);overflow:auto;background:var(--panel);flex:none}
#board.off{width:26px;overflow:hidden}#board.off #bbody{display:none}#bhead{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel);z-index:2}
#board.off #bhead b{display:none}#board.off #bhead{padding:8px 3px}
#bbody .ah{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-weight:600;position:sticky;top:34px;background:#1b212b;z-index:1}
#bbody .ah .n{background:#2a303b;border-radius:9px;padding:0 8px;font-weight:400}
#bbody .ah i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:var(--mute)}
#bbody .it{margin:6px 8px;padding:8px 10px;border:1px solid var(--line);border-left:3px solid var(--cc,var(--mute));border-radius:6px;background:var(--bg,#1c222c);line-height:1.35;font-size:12.5px;white-space:normal;overflow-wrap:anywhere;box-shadow:0 1px 2px #0006}
#bbody .it .ft{display:flex;justify-content:flex-end;margin-top:6px}#bbody .age{display:inline-block;padding:0 6px;border-radius:9px;font-size:10.5px;font-variant-numeric:tabular-nums;color:var(--mute);border:1px solid var(--line)}#bbody .age.warm{color:#e3b341;border-color:#6b5223}#bbody .age.old{color:#f85149;border-color:#6e2a27}
#bbody .pst{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;font-size:10.5px;font-weight:600;vertical-align:1px;border:1px solid}
#bbody .pst.ready{color:#3fb950;border-color:#2e6b3a;background:#12261a}#bbody .pst.running{color:#5aa9ff;border-color:#24496e;background:#0f1d2c}
#bbody .pst.changes{color:#e3b341;border-color:#6b5223;background:#261e0f}#bbody .pst.broke,#bbody .pst.conflict{color:#f85149;border-color:#6e2a27;background:#2a1212}
#bbody .pst.idle,#bbody .pst.draft{color:var(--mute);border-color:var(--line);background:#12161c}
#bbody .it:hover{background:var(--bgh,#232b37);border-color:#3a4352}
#bbody .it .id{display:inline-block;background:#12161c;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-bottom:4px;font-size:11px;color:var(--pin);text-decoration:none;font-weight:700}
#lf{display:inline-block;position:relative;font-size:11px;font-weight:400}#lf summary{list-style:none;cursor:pointer;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:2px 8px;max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#lf summary::-webkit-details-marker{display:none}#lf.set summary{color:var(--warn);border-color:#6b5223}
#lfl{position:absolute;right:0;top:calc(100% + 4px);z-index:20;min-width:200px;max-height:320px;overflow-y:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px;box-shadow:0 6px 18px #0008}#lfl label{display:flex;gap:6px;align-items:center;padding:2px 4px;border-radius:4px;cursor:pointer;white-space:nowrap}#lfl label:hover{background:#1c222b}#lfl .lft{display:flex;gap:4px;justify-content:space-between;align-items:center;padding:0 2px 6px;margin-bottom:4px;border-bottom:1px solid var(--line)}#lfl .lft button{font:inherit;font-size:10.5px}#lfl .lft button.on{color:var(--warn);border-color:#6b5223}#bbody .it .lbs{display:block;margin-top:4px}#bbody .it .lbs span{display:inline-block;background:var(--lb,#2a303b);border:1px solid transparent;border-radius:9px;padding:0 6px;margin:0 3px 2px 0;font-size:9.5px;color:#c9d1d9}#bbody .it .lbs span.on{border-color:var(--warn);color:var(--fg)}
#bbody .it .id:hover{border-color:var(--pin)}#bbody .it .tt{display:block}
#bbody .it .blk{display:block;margin-top:4px;font-size:10.5px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#bbody .it .blk a{color:var(--pin);text-decoration:none}
#bbody .it.m{border-style:dashed;text-align:center;background:none;box-shadow:none}
#bbody .pad{height:6px}#bbody .sc{max-height:58vh;overflow:auto}#bbody .it .dt{float:right;color:var(--mute);font-size:11px}
#pop{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;z-index:50}#pop.on{display:flex}
#pbox{width:min(760px,92vw);max-height:86vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;box-shadow:0 10px 40px #000c}
#pbox h2{margin:0 0 6px;font-size:15px;line-height:1.35}#pbox .meta{color:var(--mute);font-size:11.5px;margin-bottom:8px}
#pbox .lb{display:inline-block;background:#2a303b;border-radius:9px;padding:0 8px;margin:0 4px 4px 0;font-size:11px}
#pbox pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.45 ui-monospace,Menlo,monospace;background:#12161c;border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0}
#pbox h3{font-size:12px;color:var(--mute);margin:14px 0 4px}#pbox .cm{border-left:3px solid var(--pin);padding-left:8px;margin:8px 0}
#pbox .bar button{min-width:28px}#pbox .bar button:disabled{opacity:.35;cursor:default}#pbox .bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}#pbox a.gh{color:var(--pin);font-weight:700}
#bbody .it{cursor:pointer}
#pbox{width:min(860px,94vw);padding:22px 28px}#pbox h2{font:600 19px/1.35 -apple-system,system-ui,sans-serif;margin:4px 0 8px}
#pbox .md{font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:#d8dde6;overflow-wrap:anywhere}
#pbox .md>:first-child{margin-top:0}#pbox .md p{margin:.6em 0}#pbox .md h2,#pbox .md h3,#pbox .md h4,#pbox .md h5{font-weight:600;margin:1.3em 0 .4em;line-height:1.3;color:#eef1f6}
#pbox .md h2{font-size:16px;border-bottom:1px solid var(--line);padding-bottom:4px}#pbox .md h3{font-size:14.5px}#pbox .md h4,#pbox .md h5{font-size:13.5px;color:var(--mute)}
#pbox .md ul,#pbox .md ol{margin:.5em 0;padding-left:1.4em}#pbox .md li{margin:.25em 0}#pbox .md li:has(.ck){list-style:none;margin-left:-1.2em}
#pbox .md code{font:12px ui-monospace,Menlo,monospace;background:#232b37;border-radius:4px;padding:1px 5px}
#pbox .md pre{background:#12161c;border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin:.7em 0;overflow:auto}#pbox .md pre code{background:none;padding:0;white-space:pre}
#pbox .md a{color:var(--pin);text-decoration:none}#pbox .md a:hover{text-decoration:underline}#pbox .md hr{border:0;border-top:1px solid var(--line);margin:1.2em 0}
#pbox .md blockquote{margin:.7em 0;padding:2px 12px;border-left:3px solid var(--line);color:var(--mute)}
#pbox .md table{border-collapse:collapse;margin:.7em 0;font-size:13px}#pbox .md th,#pbox .md td{border:1px solid var(--line);padding:4px 10px;text-align:left}#pbox .md th{background:#1b212b}
#pbox .md details{border:1px solid var(--line);border-radius:6px;margin:.6em 0;padding:2px 12px;background:#161b22}#pbox .md details[open]{padding-bottom:8px}
#pbox .md summary{cursor:pointer;font-weight:600;padding:4px 0;color:#eef1f6}#pbox .md img{max-width:100%;border-radius:4px}#pbox .md kbd{font:11px ui-monospace,monospace;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:#232b37}
#pbox .md blockquote.alert{border-left-width:4px;background:#1b212b;border-radius:0 6px 6px 0;color:var(--fg)}#pbox .md .at{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-top:4px}
#pbox .md .alert.note{border-color:var(--pin)}#pbox .md .alert.tip{border-color:var(--ok)}#pbox .md .alert.important{border-color:#b48ead}#pbox .md .alert.warning{border-color:var(--warn)}#pbox .md .alert.caution{border-color:var(--err)}
#pbox .md .alert.warning .at{color:var(--warn)}#pbox .md .alert.caution .at{color:var(--err)}#pbox .md .alert.note .at{color:var(--pin)}#pbox .md .alert.tip .at{color:var(--ok)}#pbox .md .alert.important .at{color:#b48ead}
#pbox .md blockquote>:first-child{margin-top:.3em}#pbox .md blockquote>:last-child{margin-bottom:.3em}
#pbox .ck{display:inline-block;width:13px;height:13px;border:1.5px solid var(--mute);border-radius:3px;margin-right:8px;vertical-align:-2px;position:relative}
#pbox .ck.on{background:var(--ok);border-color:var(--ok)}#pbox .ck.on::after{content:"";position:absolute;left:3px;top:0;width:3px;height:7px;border:solid #0f1115;border-width:0 2px 2px 0;transform:rotate(45deg)}
#pbox .cm{border:1px solid var(--line);border-left:3px solid var(--pin);border-radius:8px;background:#1a2029;padding:8px 14px 10px;margin:10px 0}#pbox .cm .meta{margin-bottom:2px}
#pbox .prbar{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px}#pbox .pill{font-size:11.5px;border:1px solid var(--line);background:#1b212b;border-radius:10px;padding:1px 9px}#pbox .pill.ok{border-color:#3ecf8e88;color:var(--ok)}#pbox .pill.bad{border-color:#ff6b6b88;color:var(--err)}
#pbox h3.sec{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:20px 0 4px}
body.dockb{display:grid;grid-template-columns:300px 1fr;grid-template-rows:minmax(0,1fr) auto}
body.dockb #side{grid-row:1/3}body.dockb #main{grid-column:2;grid-row:1}
body.dockb #board{grid-column:2;grid-row:2;width:auto;height:320px;border-left:0;border-top:1px solid var(--line);display:flex;flex-direction:column}
body.dockb #board.off{height:34px;width:auto}body.dockb #board.off #bhead b{display:inline}body.dockb #board.off #bhead{padding:8px 10px}
body.dockb #bbody{flex:none;height:calc(100% - 41px);min-height:0;display:flex;overflow-x:auto;overflow-y:hidden}

body.dockb #bhead{position:static}
#grip{display:none}body.dockb #grip{display:block;height:7px;margin-bottom:-3px;cursor:ns-resize;flex:none;position:relative;z-index:3}body.dockb #grip:hover,body.dockb #grip.drag{background:var(--pin);opacity:.6}body.dragging{user-select:none;cursor:ns-resize}body.dragging iframe{pointer-events:none}
body.dockb #bbody>.col{flex:1 1 0;min-width:185px;display:flex;flex-direction:column;height:100%;border-right:1px solid var(--line);min-height:0}
body.dockb .ch{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);font-weight:600;background:#1b212b;flex:none}
body.dockb .col>.sc{flex:1 1 0;min-height:0;max-height:none;overflow-y:auto;overscroll-behavior:contain}
.prs{--cc:var(--run);background:linear-gradient(#2dd4bf14,#2dd4bf08)}
#bbody .acc.prs{border-bottom:2px solid var(--run);margin-bottom:4px}
#bbody .prs>.ah,#bbody .prs>.ch{background:#12332f;color:#7ff0df;border-bottom:1px solid #2dd4bf66;letter-spacing:.04em;text-transform:uppercase}
#bbody .prs .it{border:1px solid #2dd4bf55;border-left:4px solid var(--run);background:#14262a;border-radius:10px}
#bbody .prs .it .id{background:#2dd4bf22;border-color:var(--run);color:#7ff0df}
body.dockb #bbody>.col.prs{box-shadow:inset 0 2px 0 var(--run)}
body.dockb #bbody>.col.prcol{background:linear-gradient(#2dd4bf14,#2dd4bf08);box-shadow:inset 0 2px 0 var(--run);flex:1.15 1 0}
body.dockb .sec{display:flex;flex-direction:column;min-height:0;flex:none}body.dockb .sec.open{flex:1 1 0}body.dockb .sec:not(.open)>.sc{display:none}
body.dockb .sec>.ch{cursor:pointer;border-top:1px solid #2dd4bf33}body.dockb .sec:first-child>.ch{border-top:0}
.eye{cursor:pointer;margin-left:8px;opacity:.55;display:inline-flex;vertical-align:middle}.eye:hover{opacity:1}#bbody .acc.hidcol>.ah{opacity:.45}#bbody .acc:not(.open)>.sc,#bbody .acc:not(.open)>.pad{display:none}
.hidbar{flex:none;align-self:flex-start;padding:8px 10px;color:var(--mute);font-size:11px;display:flex;flex-direction:column;gap:4px;border-right:1px solid var(--line);min-width:120px}.chip b{font-weight:600;color:var(--pin)}.chip{display:flex;justify-content:space-between;gap:10px;align-items:center;cursor:pointer;background:#2a303b;border-radius:9px;padding:1px 8px;color:var(--fg)}
iframe{flex:1;width:100%;border:1px solid var(--line);background:#0f1115;min-height:0}
</style>
<div id=side>
<details id=plans class=sp><summary>subscriptions<span class=sr id=plsr></span></summary><div class=sb><div id=subs></div></div></details>
<details id=mdl class=sp><summary>squad models<span class=sr id=mdsr></span></summary><div class=sb id=mbody></div></details>
<details id=stats class=sp><summary>cost &amp; usage<span class=sr id=stsr></span></summary><div class=sb><div class=tot3 id=tot3></div><h4>Spend <span class=row id=src title="Subscription = Claude, Grok, GPT and Gemini models. API = every other model (deepseek…) and calls that name no model. DeepSeek, Grok and GPT are re-priced from their tokens at list prices (API-equivalent)." style="display:inline-flex;margin:0"></span></h4><div class=tot id=tot></div><h4>Spend per <span class=row id=per style="display:inline-flex;margin:0"></span></h4><div id=chart></div><h4>Tokens by model <small class=m>in + out · all time</small></h4><div id=pie></div><div id=kpi></div></div></details>
<div id=list></div></div>
<div id=main></div>
<div id=pop><div id=pbox></div></div>
<div id=board><div id=grip title="drag to resize"></div><div id=bhead><b>ISSUES &amp; PRs <span id=bt class=m></span></b><span><details id=lf><summary title="filter cards by label">labels</summary><div id=lfl></div></details> <button id=bx title="collapse">&raquo;</button></span></div><div id=bbody></div></div>
<script>
const LAYOUTS={1:[1,1],2:[2,1],3:[3,1],4:[2,2],6:[3,2]};
const $=s=>document.querySelector(s),ls=JSON.parse(localStorage.hub||"{}");
let allRuns=[],runs=[],q=ls.q||"",per=ls.per||"day",src=ls.src||"both",N=1,pins=new Set((ls.pins||[]).slice(0,1)),slots=[],frames=new Map();
const save=()=>localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,src});
const usd=v=>v==null?"—":v>=100?"$"+Math.round(v):"$"+v.toFixed(2);
const age=s=>s<90?s+"s":s<5400?Math.round(s/60)+"m":Math.round(s/3600)+"h";
// a run's name says what it is for: "fix PR #1202 · #1178", "panel · #1179 #1182", "#1183"; the run id moves to the detail line
const label=r=>{const is=(r.issues||[]).filter(i=>+i!==r.fix).map(i=>"#"+i).join(" ");
  if(r.url)return "test "+is;
  return r.fix?`fix PR #${r.fix}`+(is?" · "+is:""):is?(r.merge?"merge ":r.bug?"fix ":"build ")+is+(r.stack?` · on PR #${r.stack}`:""):"run "+r.id.slice(-6)};   // a run is named by what it does: fix a PR, or build issues (stacked on a parent PR); "test" is kept for test-only runs
const sub=r=>{const t=r.titles||{},ks=(r.issues||[]).filter(i=>+i!==r.fix&&t[i]);return ks.length===1?t[ks[0]]:ks.length?ks.map(i=>"#"+i+" "+t[i].split(" ").slice(0,4).join(" ")).join(" · "):""};
function plan(){ // which runs fill the N slots: pinned first, then most recently updated; slots stay stable, top slot has hysteresis
  const byId=new Map(runs.map(r=>[r.id,r])),want=[];
  runs.filter(r=>pins.has(r.id)).forEach(r=>want.push(r.id));
  const open=runs.filter(r=>!pins.has(r.id)&&r.rank===0);
  // live runs auto-fill; with none running, fall back to the most recently finished (landed) ones
  (open.length?open:runs.filter(r=>!pins.has(r.id)&&r.rank===1)).forEach(r=>want.push(r.id));
  const top=want.slice(0,N);
  slots=slots.filter(id=>byId.has(id));
  // keep placed runs that are still wanted; for a lone unpinned current top, only follow a newer run after 30s of lead
  const keep=slots.filter(id=>top.includes(id));
  if(N===1&&slots[0]&&!pins.size&&byId.has(slots[0])&&top[0]!==slots[0]&&byId.get(slots[0]).age-byId.get(top[0]).age<30)return;
  const next=[];for(let i=0;i<N;i++){const cur=slots[i];next[i]=(cur&&top.includes(cur))?cur:null}
  top.forEach(id=>{if(!next.includes(id)){const j=next.indexOf(null);if(j>=0)next[j]=id}});
  slots=next.filter(Boolean);
}
const GROUPS=[["running","RUNNING"],["1h","≤ 1 h"],["2h","1–2 h"],["4h","2–4 h"],["8h","4–8 h"],["d","8 h+ (days)"]];
const CAPS=__CAPS__;
function groupHeader(k,rows){const n=rows.length,spend=rows.reduce((a,r)=>a+r.spend,0),agents=rows.reduce((a,r)=>a+(r.agents||0),0),title=usd(spend);let text=n+" · "+title;if(k==="running")text=(CAPS&&CAPS.max_parallel&&CAPS.max_children)?`${n}/${CAPS.max_parallel} runs · ${agents}/${CAPS.max_children} agents`:`${n} · ${agents}`;return {text,title}}
let openGroup=null,userPicked=false;
// runs of one issue share the issue label ("#1281"), and today three runs can carry it at once: the list and the panel header
// then read the same twice. Disambiguate only the labels that repeat, with the run's start time (run-YYYYMMDD-HHMMSS-xxxx).
const short=r=>{const m=/(\d{8})-(\d{2})(\d{2})\d{2}/.exec(r.id||"");return m?m[2]+":"+m[3]:""};   // run-…-HHMMSS-xxxx and test-<n>-YYYYMMDD-HHMMSS both carry it
const name=r=>{const l=label(r);return runs.filter(x=>label(x)===l).length>1&&short(r)?`${l} · ${short(r)}`:l};
const groupOf=r=>{if(r.rank===0)return"running";const h=r.age/3600;return h<=1?"1h":h<=2?"2h":h<=4?"4h":h<=8?"8h":"d"};
function drawList(){
  const shown=new Set(slots),by={};runs.forEach(r=>(by[groupOf(r)]=by[groupOf(r)]||[]).push(r));
  const filled=GROUPS.filter(g=>by[g[0]]);
  if(!openGroup||!by[openGroup]||!userPicked)openGroup=(filled[0]||[])[0]||null;   // one group open at a time; follows the newest until the user picks one
  const row=r=>`<div class="r ${shown.has(r.id)?"show":""} ${pins.has(r.id)?"pinned":""}" data-id="${r.id}"><div>${r.kind==="test"?`<span class=kd title="GUI test run">🧪</span>`:""}<span class="b ${r.state}">${r.state}</span> <b>${esc(name(r))}</b>${sub(r)?`<div class=st title="${esc(sub(r))}">${esc(sub(r))}</div>`:""}<div class=m>${usd(r.spend)} · ${Math.round(r.dur)}m · ${age(r.age)} ago · ${r.id.slice(-6)} · :${r.port}</div></div><span class=pn title="shown in the panel">${pins.has(r.id)?"●":"○"}</span></div>`;
  $("#list").innerHTML=filled.map(([k,t])=>{const h=groupHeader(k,by[k]);return `<div class="gh ${openGroup===k?"op":""}" data-g="${k}" tabindex=0>${t}<span class=sr title="${esc(h.title)}">${h.text}</span></div>${openGroup===k?by[k].map(row).join(""):""}`}).join("")||"<div class=m style=padding:10px>no live dashboards</div>";
  document.querySelectorAll(".gh").forEach(e=>e.onclick=()=>{openGroup=e.dataset.g;userPicked=true;drawList()});
  document.querySelectorAll(".r").forEach(e=>e.onclick=()=>{const id=e.dataset.id;
    if(pins.has(id))pins.delete(id);else{if(N===1)pins.clear();pins.add(id)}
    save();refresh(true)});
}
let built="";
function drawMain(force){
  const [c,rw]=LAYOUTS[N],key=N+":"+slots.join(",");
  const main=$("#main");main.style.gridTemplateColumns=`repeat(${c},1fr)`;main.style.gridTemplateRows=`repeat(${rw},1fr)`;
  const byId=new Map(runs.map(r=>[r.id,r]));
  if(key!==built){built=key;
    for(const id of [...frames.keys()])if(!slots.includes(id))frames.delete(id);
    main.innerHTML=slots.length?"":"<div class=m style=\"padding:20px\">No open runs. Click a finished run in the list to look at it; the next run that starts shows up here on its own.</div>";
    slots.forEach(id=>{const r=byId.get(id);let f=frames.get(id);
      if(!f||f.dataset.port!==(r.port||r.url)){f=document.createElement("iframe");f.src=r.url||"http://127.0.0.1:"+r.port+"/";f.dataset.port=r.port;frames.set(id,f)}
      const cell=document.createElement("div");cell.className="cell";cell.innerHTML=`<small><span>${name(r)} · ${r.state}</span><span>${pins.has(id)?"pinned":"auto"}</span></small>`;cell.appendChild(f);main.appendChild(cell)})}
}
function refresh(){plan();drawList();drawMain()}
function applyFilter(){runs=allRuns}
let known=null;   // run ids already seen: a run that appears running after the first poll is selected on its own (pinned, its group opened)
function selectNew(){const ids=allRuns.map(r=>r.id);if(known===null){known=new Set(ids);return}
  const fresh=allRuns.filter(r=>!known.has(r.id)&&r.rank===0);ids.forEach(i=>known.add(i));
  if(!fresh.length)return;const id=fresh.sort((x,y)=>x.age-y.age)[0].id;
  if(N===1)pins.clear();else if(pins.size>=N)pins.delete([...pins][0]);pins.add(id);userPicked=false;save()}
async function poll(){try{allRuns=await (await fetch("/api/runs")).json();selectNew();applyFilter();refresh()}catch(e){}}
function bars(rows,W=280,H=40){if(!rows.length)return"";const mx=Math.max(...rows.map(r=>r.spend),0.01),bw=W/rows.length;
  return `<svg viewBox="0 0 ${W} ${H+14}">`+rows.map((r,i)=>`<rect x="${i*bw+1}" y="${H-r.spend/mx*H}" width="${Math.max(bw-2,1)}" height="${r.spend/mx*H}" fill="#5aa9ff"><title>${r.t}: ${usd(r.spend)} · ${r.runs} runs</title></rect>`).join("")+`<text x="0" y="${H+11}" fill="#8a93a3" font-size="9">${rows[0].t}</text><text x="${W}" y="${H+11}" fill="#8a93a3" font-size="9" text-anchor="end">${rows[rows.length-1].t} · max ${usd(mx)}</text></svg>`}
function kr(label,sub,v,unit,base,lowerBetter=true){if(v==null)return`<div class=kr><span class=l>${label}<small>${sub}</small></span><b>—</b></div>`;
  let chip="";if(base){const d=Math.round((v-base)/base*100),good=lowerBetter?d<0:d>0;chip=d===0?`<span class=d>same as before (${base}${unit})</span>`:`<span class="d ${good?"good":"bad"}">${Math.abs(d)}% ${lowerBetter?(d<0?"faster":"slower"):(d>0?"more":"fewer")} · was ${base}${unit}</span>`}
  return `<div class=kr><span class=l>${label}<small>${sub}</small></span><b>${v}<small>${unit}</small></b>${chip}</div>`}
let mdl=null,mdlSrv="",mdlMsg="";const MLAB={coding_models:"Coding",review_models:"Review",vision_models:"Vision"},MDESC={coding_models:"Writes the code in squad runs.",review_models:"Reviews each PR independently.",vision_models:"Looks at screenshots and images."},EFF=["low","medium","high","max"];
function mdlEff(k,m){return (mdl.efforts&&mdl.efforts[k]&&mdl.efforts[k][m])||""}
function mdlSetEff(k,m,v){mdl.efforts=mdl.efforts||{};mdl.efforts[k]=mdl.efforts[k]||{};if(EFF.includes(v)&&v!=="medium")mdl.efforts[k][m]=v;else delete mdl.efforts[k][m]}
const HRP=["22-07","07-22","20-08","18-06"],HRX=/^([01]\d|2[0-3])-([01]\d|2[0-3])$/;
function mdlHrs(k,m){return (mdl.hours&&mdl.hours[k]&&mdl.hours[k][m])||""}
function mdlSetHrs(k,m,v){v=(v||"").trim();mdl.hours=mdl.hours||{};mdl.hours[k]=mdl.hours[k]||{};if(HRX.test(v)&&v.slice(0,2)!==v.slice(3))mdl.hours[k][m]=v;else delete mdl.hours[k][m]}
function mdlProv(m){const x=m.replace(/^claude:/,"");return /^claude-(sonnet|opus|haiku|fable)/.test(x)||x==="claude-session"?"Claude":/^claude-/.test(x)?"via CLI":/^gpt/.test(x)?"Codex":/^grok/.test(x)?"xAI":/^gemini/.test(x)?"Google":""}
function mdlRender(){const b=$("#mbody");if(!mdl){b.innerHTML="<span class=m>loading…</span>";return}
 $("#mdsr").textContent=mdl.last_coding||(mdl.coding_models||[])[0]||"";   // the model the last run coded with, not the chain's head
 const dirty=JSON.stringify(mdl)!==mdlSrv,known=mdl.known||[];
 b.innerHTML=`<datalist id=mk>${known.map(m=>`<option value="${esc(m)}">`).join("")}</datalist><datalist id=hrp>${HRP.map(v=>`<option value="${v}">`).join("")}</datalist>`+Object.keys(MLAB).map(k=>`<div class=mc><div class=mt>${MLAB[k]}</div><div class=mx>${MDESC[k]} Tried top to bottom; the first healthy model wins.</div>`+mdl[k].map((m,i)=>{const ef=mdlEff(k,m),hr=mdlHrs(k,m),pv=mdlProv(m);return`<div class="mr${i?"":" p"}"><span class=rk title="${i?"fallback "+i:"primary"}">${i+1}</span><span class=mn title="${esc(m)}">${esc(m)}</span><span class=ct><span class=pv title="${i?"fallback "+i:"primary"}${pv?" · "+pv:""}">${pv||(i?"fallback "+i:"primary")}</span><span class=el>effort</span><select class="ef${ef?" set":""}" data-ef="${k}|${esc(m)}" title="effort for this model (medium is the default)">${EFF.map(e=>`<option value="${e==="medium"?"":e}"${(ef||"medium")===e?" selected":""}>${e}</option>`).join("")}</select><span class=el>hrs</span><input class="hr${hr?" set":""}" list=hrp data-hr="${k}|${esc(m)}" value="${esc(hr)}" placeholder="always" title="local hours this model may run (HH-HH, wraps midnight; empty = always)"><span class=mb><button data-mv="${k}|${i}|-1" ${i?"":"disabled"} title="move up">↑</button><button data-mv="${k}|${i}|1" ${i<mdl[k].length-1?"":"disabled"} title="move down">↓</button><button class=x data-rm="${k}|${i}" ${mdl[k].length>1?"":"disabled"} title="remove from chain">✕</button></span></span></div>`}).join("")+`<div class=madd><input list=mk data-add="${k}" placeholder="add a model id…"><button data-addb="${k}">Add</button></div></div>`).join("")
 +`<div class=mx>Each row sets its own <b>effort</b> and its own <b>hrs</b>: the local hours that model may run, <code>HH-HH</code>, start inclusive and end exclusive, wrapping midnight (<code>22-07</code>). Empty = always. A model outside its window is skipped like an unhealthy one and the chain moves on. Written per model, beside the chains, in models.json.</div>`
 +`<div class="msave${dirty?" dirty":""}"><button id=msv ${dirty?"":"disabled"}>Save</button><button id=mrv ${dirty?"":"disabled"}>Revert</button><span class="st${mdlMsg.startsWith("!")?" err":mdlMsg?" okm":""}">${mdlMsg?esc(mdlMsg.replace(/^!/,"")):(dirty?"<span class=dot></span>unsaved changes":"saved in models.json")}</span></div>`}
async function mdlLoad(){try{mdl=await (await fetch("/api/models")).json();mdlSrv=JSON.stringify(mdl);mdlRender()}catch(e){}}
$("#mbody").addEventListener("change",e=>{const t=e.target;if(!mdl)return;mdlMsg="";if(t.dataset.ef){const[k,m]=t.dataset.ef.split("|");mdlSetEff(k,m,t.value)}
 else if(t.dataset.hr){const[k,m]=t.dataset.hr.split("|");mdlSetHrs(k,m,t.value)}
 else return;mdlRender()});
$("#mbody").addEventListener("click",async e=>{const t=e.target.closest("button");if(!t||!mdl)return;mdlMsg="";
 if(t.dataset.mv){const[k,i,d]=t.dataset.mv.split("|"),a=mdl[k],j=+i+ +d;[a[i],a[j]]=[a[j],a[i]]}
 else if(t.dataset.rm){const[k,i]=t.dataset.rm.split("|");const[m]=mdl[k].splice(+i,1);mdlSetEff(k,m,"")}
 else if(t.dataset.addb){const k=t.dataset.addb,v=t.parentElement.querySelector("input").value.trim();if(!/^[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}$/.test(v))mdlMsg="!invalid model id";else if(mdl[k].includes(v))mdlMsg="!already in the chain";else mdl[k].push(v)}
 else if(t.id==="mrv")mdl=JSON.parse(mdlSrv);
 else if(t.id==="msv"){try{const r=await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(mdl)}),d=await r.json();if(d.err)mdlMsg="!"+d.err;else{mdl=d;mdlSrv=JSON.stringify(d);mdlMsg="saved · new runs use it"}}catch(x){mdlMsg="!save failed"}}
 mdlRender()});
/* accordion: one group open at a time */
const ACC=["plans","stats","mdl"];if(!ACC.includes(ls.acc))ls.acc="";
ACC.forEach(i=>{const e=$("#"+i);e.open=i===ls.acc;e.addEventListener("toggle",()=>{if(e.open){ACC.forEach(j=>{if(j!==i)$("#"+j).open=false});ls.acc=i;save();if(i==="mdl")mdlLoad()}else if(ls.acc===i&&!ACC.some(j=>$("#"+j).open)){ls.acc="";save()}})});mdlLoad();
const PCOL=["#5aa9ff","#3ecf8e","#f5b041","#b79cf6","#2dd4bf","#ff6b6b","#c678dd","#e5c07b","#8a93a3"],tk=n=>n>=1e9?(n/1e9).toFixed(2)+"B":n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e3?(n/1e3).toFixed(0)+"k":""+n;
function pie(ms){const rows=ms.map(m=>({...m,v:m.in+m.out})).filter(m=>m.v>0);if(!rows.length)return"<span class=m>no token data</span>";
 const top=rows.slice(0,8),rest=rows.slice(8).reduce((a,m)=>a+m.v,0),sl=top.map(m=>({n:m.model,v:m.v,t:`${m.model}: ${tk(m.in)} in · ${tk(m.out)} out · ${tk(m.cache)} cache read · ${m.calls} calls · ${usd(m.spend)}`}));if(rest)sl.push({n:"other",v:rest,t:"other models"});
 const tot=sl.reduce((a,x)=>a+x.v,0);let a0=-Math.PI/2;const R=52,C=60,arc=sl.map((x,i)=>{const a1=a0+x.v/tot*2*Math.PI,big=a1-a0>Math.PI?1:0,p=sl.length===1?`<circle cx=${C} cy=${C} r=${R} fill="${PCOL[i]}"><title>${esc(x.t)}</title></circle>`:`<path d="M${C} ${C}L${C+R*Math.cos(a0)} ${C+R*Math.sin(a0)}A${R} ${R} 0 ${big} 1 ${C+R*Math.cos(a1)} ${C+R*Math.sin(a1)}Z" fill="${PCOL[i]}" stroke="#161a21" stroke-width=1><title>${esc(x.t)}</title></path>`;a0=a1;return p}).join("");
 return `<div class=pie><svg viewBox="0 0 120 120" width=112 height=112>${arc}<circle cx=${C} cy=${C} r=26 fill="#161a21"/><text x=${C} y=${C+4} text-anchor=middle fill="#d8dde6" font-size=11 font-weight=600>${tk(tot)}</text></svg><div class=lg>${sl.map((x,i)=>`<div title="${esc(x.t)}"><i style="background:${PCOL[i]}"></i><span class=ln>${esc(x.n)}</span><b>${Math.round(x.v/tot*100)}%</b></div>`).join("")}</div></div>`}
const ago=t=>{const m=Math.round((Date.now()/1000-t)/60);return m<2?"just now":m<90?m+" min ago":Math.round(m/60)+" h ago"},rs=t=>t?new Date(t*1000).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"?";
const rel=t=>{if(!t)return"";const x=t-Date.now()/1000;if(x<=0)return"resets now";const d=Math.floor(x/86400),h=Math.floor(x%86400/3600),m=Math.floor(x%3600/60);return"resets in "+(d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`)};
const lvl=p=>p>=90?"lv-err":p>=60?"lv-warn":"lv-ok",fpct=p=>p<10&&p>0?p.toFixed(1):String(Math.round(p));
const qbar=(label,pct,resets,sm,tip)=>`<div class="qw${sm?" sm":""}"><div class=ql><span>${esc(label)} <b>${fpct(pct)}%</b></span>${resets?`<span class=rst title="${esc(rs(resets))}">${rel(resets)}</span>`:""}</div><div class=tr title="${esc(tip)}"><div class="fl ${lvl(pct)}" style="width:${Math.min(pct,100)}%"></div></div></div>`;
const autoq=a=>{const QT="Quota used, read from the provider's own CLI. 100% = quota exhausted.";return a.windows.map((w,i)=>qbar(w.label,w.pct,w.resets,i>0,QT)).join("")+`<div class=fr>${a.reached?`<span class="chip err">limit reached</span>`:""}<span class=fs>CLI · ${ago(a.asof)}</span></div>`};
const SUBL={claude:"Claude Team",grok:"SuperGrok",gpt:"ChatGPT Plus",gemini:"Google AI Plus"};
const CUR={"R$":"BRL","U$":"USD","US$":"USD","$":"USD","€":"EUR","£":"GBP","¥":"JPY"},curOf=v=>{v=v.trim().toUpperCase();return CUR[v]||v};
const money=(a,c)=>{try{return new Intl.NumberFormat(undefined,{style:"currency",currency:c}).format(a)}catch(e){return a+" "+c}};
async function pollSubs(){try{const d=await (await fetch("/api/subs")).json(),ae=document.activeElement;if(ae&&ae.closest&&ae.closest("#subs")&&ae.tagName==="INPUT")return;
 const tot=Object.values(d.subs).reduce((a,v)=>a+(v.saved||0),0),any=Object.values(d.subs).some(v=>v.saved!=null);$("#plsr").textContent=any||d.api?`plans ${usd(d.plans)} + API ${usd(d.api)} = ${usd(d.total)}`:"set plan prices";
 $("#tot3").innerHTML=`<div>plans<b>${usd(d.plans)}</b><span>per month</span></div><div>API<b>${usd(d.api)}</b><span>so far this month</span></div><div>total<b>${usd(d.total)}</b><span>plans + API</span></div>`;
 $("#subs").innerHTML=`<div class=subx>Per subscription: API-equivalent usage vs the plan price so far.</div><datalist id=curs>${["USD","BRL","EUR","GBP","ARS","MXN","CAD","JPY"].map(c=>`<option value=${c}>`).join("")}</datalist>`+Object.entries(d.subs).map(([k,v])=>{const ok=v.saved!=null,pl=v.plan,pr=d.presets[k],custom=pl&&pl.plan==="custom";
  const sel=`<select data-plan=${k} data-f=plan><option value="" ${pl?"":"selected"}>choose a plan…</option>${Object.entries(pr).map(([i,p])=>`<option value=${i} ${pl&&pl.plan===i?"selected":""}>${esc(p.name)} · US$${p.usd}</option>`).join("")}<option value=custom ${custom?"selected":""}>Custom amount…</option></select>`;
  const cust=custom?`<span class=pr><span>amount</span><input data-plan=${k} data-f=amount type=number min=0 step=0.01 value="${pl.amount}"> <input data-plan=${k} data-f=cur list=curs value="${esc(pl.currency)}" placeholder="BRL" title="R$, U$, € or any 3-letter code"></span>`:"";
  const note=v.noRate?`<span class=warn>no exchange rate for ${esc(pl.currency)} right now</span>`:(pl&&pl.currency!=="USD"&&v.usd!=null?`<span class=dt>≈ <b>US$${v.usd}</b> / month · ${(+v.rate).toFixed(2)} ${esc(pl.currency)} per US$</span>`:"");
  const est=pl&&v.usd?v.eq/v.usd*100:null,aq=v.quota,aw=aq&&aq.windows[0],q=aw?aw.pct:(pl&&pl.quota!=null?pl.quota:null),win=aw?aw.label:(pl&&pl.qwin||""),pct=q!=null?q:est,today=d.frac*100,cls=q!=null?"warnq":(ok?(v.saved>=0?"good":"bad"):"");
  const MT="Fill: API-equivalent used by squad runs ÷ monthly plan price (partial: other uses of the plan are not seen). 100% = plan paid off. Tick: how much of the month has passed.";
  const bar=aw?autoq(aq):q!=null?qbar(win||"quota used",q,null,false,"Quota % entered by hand. 100% = quota exhausted."):pct!=null?`<div class=qw><div class=ql><span>used <b>${fpct(pct)}%</b> of plan value</span><span class=rst>month ${Math.round(today)}%${pct>=100?" · paid off ✓":""}</span></div><div class=tr title="${MT}"><div class="fl ${ok?(v.saved>=0?"lv-ok":"lv-err"):"lv-est"}" style="width:${Math.min(pct,100)}%"></div><div class=tk style="left:${Math.min(today,100)}%"></div></div></div>`:"";
  const man=aw||!pl?"":`<div class=pr><span>quota used</span><input data-plan=${k} data-f=quota type=number min=0 max=100 step=1 value="${q??""}" placeholder="%" style="width:60px"><input data-plan=${k} data-f=qwin value="${esc(win)}" placeholder="window, e.g. weekly · all models" maxlength=40 style="flex:1;min-width:0"></div>`;
  return `<div class=sub><div class=hd><span class=nm>${SUBL[k]}</span>${ok?"":`<span class="sv none">choose a plan</span>`}</div><div class=pr>${sel}</div>${cust}${note}${bar}${man}</div>`}).join("")}catch(e){console.error(e)}}
$("#subs").addEventListener("change",async e=>{const k=e.target.dataset.plan;if(!k)return;const row=e.target.closest(".sub");let body;
 const f=e.target.dataset.f,pick=row.querySelector("[data-f=plan]").value;
 if(f==="quota"||f==="qwin"){const x=row.querySelector("[data-f=quota]").value,w=row.querySelector("[data-f=qwin]").value.trim();body={quota:x===""?null:+x,qwin:w||null}}
 else if(f==="plan"){body=pick===""?null:pick==="custom"?{plan:"custom",amount:0,currency:"USD"}:{plan:pick}}
 else{const a=row.querySelector("[data-f=amount]"),c=row.querySelector("[data-f=cur]");c.value=curOf(c.value);body={plan:"custom",amount:+a.value||0,currency:c.value}}
 try{await fetch("/api/plans",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({[k]:body})})}catch(x){}pollSubs()});
async function pollStats(){try{pollSubs();
  const st=await (await fetch("/api/stats?period="+per+"&src="+src)).json(),sp=await (await fetch("/api/speed")).json();
  $("#per").innerHTML=["hour","day","month"].map(p=>`<button data-p=${p} class="${p===per?"on":""}">${p}</button>`).join("");
  $("#src").innerHTML=[["both","both"],["sub","subscription"],["api","API"]].map(([v,l])=>`<button data-s=${v} class="${v===src?"on":""}">${l}</button>`).join("");
  document.querySelectorAll("#src button").forEach(b=>b.onclick=()=>{src=b.dataset.s;save();pollStats()});
  document.querySelectorAll("#per button").forEach(b=>b.onclick=()=>{per=b.dataset.p;save();pollStats()});
  $("#chart").innerHTML=bars(st.buckets);$("#pie").innerHTML=pie(st.models||[]);
  $("#stsr").textContent=(src==="both"?"":(src==="sub"?"subscription ":"API "))+"today "+usd(st.totals.today);
  $("#tot").innerHTML=`<div>today<b>${usd(st.totals.today)}</b></div><div>this month<b>${usd(st.totals.month)}</b></div><div>all time<b>${usd(st.totals.all)}</b></div>`;
  const b=sp.baseline,n=sp.pr.after.n,cut=b.cutoff.slice(5,16).replace("T"," ");
  $("#kpi").innerHTML=`<h4>Speed <small>vs. before ${cut} UTC</small></h4>`
   +kr("Time to merge a PR","median, open → merged",sp.pr.after.median,"min",b.pr.median)
   +kr("Time to merge a PR","average",sp.pr.after.mean,"min",b.pr.mean)
   +kr("Run duration","median, today",sp.runs.today_median,"min",b.run_median)
   +kr("PRs merged since then","sample size",n,"",0,false)
   +kr("Review rounds per PR","average",sp.rounds.mean,"",null)
   +kr("Hung runs killed","by the heartbeat",sp.kills.count,"",null)
   +((sp.pr.open&&sp.pr.open.length)?`<h4>Open now <small>oldest first · the loop ages a PR past 30 min</small></h4>`
     +sp.pr.open.map(o=>`<div class=kr><div class=l>#${o.n}</div><b${o.min>=30?' style="color:var(--warn)"':''}>${o.min}<small>min</small></b></div>`).join(""):"")
  +(n<10?`<div class=warnl>Provisional: only ${n} PR${n===1?"":"s"} so far; the comparison firms up at ~10.</div>`:"")+(sp.pr.stale?"<div class=warnl>PR data stale</div>":"");
}catch(e){}}
pollStats();setInterval(pollStats,30000);
poll();setInterval(poll,3000);
const bo=ls.bo||{},saveB=()=>{ls.bo=bo;localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,bo})};
bo.dock="bottom";   // the board always docks at the bottom
const sizeBoard=()=>{$("#board").style.height=document.body.classList.contains("dockb")&&!bo.off?(bo.h||320)+"px":""};
const applyDock=()=>{document.body.classList.toggle("dockb",bo.dock==="bottom");sizeBoard();$("#bx").innerHTML=bo.dock==="bottom"?(bo.off?"&uarr;":"&darr;"):"&raquo;"};
$("#board").classList.toggle("off",!!bo.off);applyDock();$("#bx").onclick=()=>{bo.off=!bo.off;$("#board").classList.toggle("off",bo.off);applyDock();saveB()};
const isPR=n=>n==="Open PRs"||n==="Closed PRs";
const esc=t=>String(t).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const OKTAG=new Set("details summary hr br b i strong em code kbd sub sup p div span ul ol li table thead tbody tr th td h1 h2 h3 h4 h5 h6 blockquote pre a img del s u".split(" ")),VOID=new Set(["br","hr","img"]);
const OKATTR={a:["href"],img:["src","alt","width","height"],td:["colspan","rowspan","align"],th:["colspan","rowspan","align"],details:["open"]};
function safeTag(close,name,attrs,self){name=name.toLowerCase();if(!OKTAG.has(name))return null;if(close)return VOID.has(name)?"":`</${name}>`;
let out="";for(const m of attrs.matchAll(/([a-zA-Z-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g)){const k=m[1].toLowerCase(),v=m[2]??m[3]??m[4]??"";if(!(OKATTR[name]||[]).includes(k))continue;
if((k==="href"||k==="src")&&!/^https?:\/\//i.test(v))continue;out+=k==="open"?" open":` ${k}="${v.replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;")}"`}
if(name==="a")out+=' target=_blank rel=noopener';if(name==="img")out+=' referrerpolicy=no-referrer loading=lazy';return `<${name}${out}>`}
function md(src){const keep=[],hold=h=>"\u0001"+(keep.push(h)-1)+"\u0001";
// 1) code fences and a small, attribute-filtered HTML allow-list are set aside before escaping; comments are dropped, anything else is shown as text
let t=(src||"").replace(/\r/g,"").replace(/<!--[\s\S]*?-->/g,"").replace(/```[^\n]*\n([\s\S]*?)```/g,(_,c)=>hold("<pre><code>"+esc(c)+"</code></pre>"));
t=t.replace(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s[^<>]*?)?)\s*\/?>/g,(all,c,n,a)=>{const r=safeTag(!!c,n,a);return r===null?all:hold(r)});
t=esc(t);
const inl=x=>x.replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>").replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:]|$)/g,"$1<i>$2</i>").replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g,'<a target=_blank rel=noopener href="$2">$1</a>').replace(/(^|[\s(])#(\d{1,6})\b/g,'$1<a target=_blank rel=noopener href="https://github.com/__REPO__/issues/$2">#$2</a>');
const onlyHold=l=>/^\s*(\u0001\d+\u0001\s*)+$/.test(l);
function block(txt){const out=[],L=txt.split("\n");let i=0;
while(i<L.length){const l=L[i];let m;
if(onlyHold(l)){out.push(l.trim());i++}
else if(m=l.match(/^(#{1,4})\s+(.*)$/)){out.push(`<h${m[1].length+1}>${inl(m[2])}</h${m[1].length+1}>`);i++}
else if(/^\s*(-{3,}|\*{3,})\s*$/.test(l)){out.push("<hr>");i++}
else if(/^\s*([-*]|\d+\.)\s+/.test(l)){const ord=/^\s*\d+\./.test(l),it=[];while(i<L.length&&(m=L[i].match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/))){let c=m[1],ck="";const k=c.match(/^\[( |x|X)\]\s+(.*)$/);if(k){ck=`<span class="ck${k[1]===" "?"":" on"}"></span>`;c=k[2]}it.push(`<li>${ck}${inl(c)}</li>`);i++}out.push(`<${ord?"ol":"ul"}>${it.join("")}</${ord?"ol":"ul"}>`)}
else if(/^&gt;\s?/.test(l)){const q=[];while(i<L.length&&/^&gt;\s?/.test(L[i])){q.push(L[i].replace(/^&gt;\s?/,""));i++}
let kind="";const am=(q[0]||"").match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i);if(am){kind=am[1].toLowerCase();q.shift()}
out.push(`<blockquote${kind?` class="alert ${kind}"`:""}>${kind?`<div class=at>${kind}</div>`:""}${block(q.join("\n"))}</blockquote>`)}
else if(/^\|.*\|\s*$/.test(l)){const rows=[];while(i<L.length&&/^\|.*\|\s*$/.test(L[i])){rows.push(L[i]);i++}const cells=r=>r.replace(/^\||\|\s*$/g,"").split("|").map(c=>inl(c.trim())),sep=rows[1]&&/^[\s|:-]+$/.test(rows[1]);
out.push("<table>"+rows.filter((_,j)=>!(sep&&j===1)).map((r,j)=>"<tr>"+cells(r).map(c=>sep&&j===0?`<th>${c}</th>`:`<td>${c}</td>`).join("")+"</tr>").join("")+"</table>")}
else if(!l.trim())i++;
else{const p=[];while(i<L.length&&L[i].trim()&&!onlyHold(L[i])&&!/^(#{1,4}\s|\s*([-*]|\d+\.)\s|&gt;|\|.*\||\s*-{3,}\s*$)/.test(L[i])){p.push(inl(L[i]));i++}out.push("<p>"+p.join("<br>")+"</p>")}}
return out.join("")}
let html=block(t);for(let k=0;k<3;k++)html=html.replace(/\u0001(\d+)\u0001/g,(_,n)=>keep[+n]);return html.replace(/<p>(<summary>[\s\S]*?<\/summary>)<\/p>/g,"$1")}
const pop=$("#pop"),when=t=>t.slice(0,16).replace("T"," ");
pop.onclick=e=>{if(e.target===pop)pop.classList.remove("on")};addEventListener("keydown",e=>{if(e.key==="Escape")pop.classList.remove("on");else if(pop.classList.contains("on")&&(e.key==="ArrowLeft"||e.key==="ArrowRight")&&!/INPUT|TEXTAREA/.test(document.activeElement.tagName))step(e.key==="ArrowLeft"?-1:1)});
let nav=[];const step=k=>{const i=nav.indexOf(cur)+k;if(i>=0&&i<nav.length)showIssue(nav[i])};let cur=0;
async function showIssue(n){cur=+n;pop.classList.add("on");$("#pbox").innerHTML="<span class=m>loading #"+n+"…</span>";
try{const d=await (await fetch("/api/issue?n="+n)).json();if(d.err){$("#pbox").textContent=d.err;return}
$("#pbox").innerHTML=`<div class=bar><span class=m>${d.pr?"PR":"Issue"} #${d.n} · ${d.state}</span><span><button id=pprev title="previous card (←)" ${nav.indexOf(cur)>0?"":"disabled"}>‹</button> <span class=m>${nav.length>1?nav.indexOf(cur)+1+"/"+nav.length:""}</span> <button id=pnext title="next card (→)" ${nav.indexOf(cur)>=0&&nav.indexOf(cur)<nav.length-1?"":"disabled"}>›</button> <a class=gh target=_blank href="${esc(d.url)}">Open on GitHub ↗</a> <button id=px>✕</button></span></div><h2>${esc(d.title)}</h2>
${d.pull?`<div class=prbar><span class=pill>${esc(d.pull.head||"?")} &rarr; ${esc(d.pull.base||"?")}</span><span class=pill>+${d.pull.add??0} &minus;${d.pull.del??0} · ${d.pull.files??0} files</span>${d.pull.draft?"<span class=pill>draft</span>":""}<span class="pill ${d.pull.mstate==="clean"?"ok":d.pull.mstate==="dirty"?"bad":""}">merge: ${esc(d.pull.mstate||"?")}</span>${Object.entries(d.pull.checks).map(([k,v])=>`<span class="pill ${k==="success"?"ok":/failure|cancelled|timed_out|action_required/.test(k)?"bad":""}">CI ${esc(k)} ${v}</span>`).join("")}${Object.entries(d.pull.reviews).map(([u,st])=>`<span class="pill ${st==="APPROVED"?"ok":st==="CHANGES_REQUESTED"?"bad":""}">${esc(u)}: ${esc(st.toLowerCase().replace("_"," "))}</span>`).join("")}</div>`:""}<div class=meta>by ${esc(d.by)} · opened ${when(d.created)} · updated ${when(d.updated)}</div>${d.labels.map(l=>`<span class=lb>${esc(l)}</span>`).join("")}
<div class=md>${d.body?md(d.body):"<span class=m>(no description)</span>"}</div><h3 class=sec>Last ${d.comments.length} of ${d.total} comments</h3>`+(d.comments.map(c=>`<div class=cm><div class=meta>${esc(c.by)} · ${when(c.at)}</div><div class=md>${md(c.body)}</div></div>`).join("")||"<span class=m>no comments</span>");
$("#px").onclick=()=>pop.classList.remove("on");$("#pprev").onclick=()=>step(-1);$("#pnext").onclick=()=>step(1);$("#pbox").scrollTop=0}catch(e){$("#pbox").textContent="failed to load"}}
document.addEventListener("click",e=>{const c=e.target.closest("#bbody .it");if(!c||e.target.closest("a")||c.classList.contains("m"))return;window.open("https://github.com/__REPO__/"+(c.dataset.pr!==undefined?"pull/":"issues/")+c.dataset.n,"_blank","noopener");return;   // every card opens its GitHub page; the popup is no longer used from the board   // a PR card opens its GitHub page in a new tab (owner, 2026-10-09); an issue card keeps the popup
nav=[...c.closest(".sc").querySelectorAll(".it[data-n]")].map(x=>+x.dataset.n);showIssue(c.dataset.n)});
const hm=location.hash.match(/issue=(\d+)/);if(hm)showIssue(+hm[1]);   // deep link: /#issue=1171
document.addEventListener("click",e=>{const h=e.target.closest("#bbody .ah");if(!h||e.target.closest("[data-eye]"))return;const g=h.parentElement.dataset.g;if(g==="Open PRs")bo.prOpen=bo.prOpen===false;else if(g==="Closed PRs")bo.prevOpen=!bo.prevOpen;else bo.open=g;saveB();pollBoard()});
$("#grip").onpointerdown=e=>{e.preventDefault();document.body.classList.remove("dragging");const g=e.currentTarget;g.setPointerCapture(e.pointerId);g.classList.add("drag");document.body.classList.add("dragging");
const mv=ev=>{if(!ev.buttons){up();return}bo.h=Math.round(Math.min(innerHeight-160,Math.max(90,innerHeight-ev.clientY)));sizeBoard()},up=()=>{g.classList.remove("drag");document.body.classList.remove("dragging");g.removeEventListener("pointermove",mv);g.removeEventListener("pointerup",up);g.removeEventListener("pointercancel",up);g.removeEventListener("lostpointercapture",up);saveB()};
g.addEventListener("pointermove",mv);g.addEventListener("pointerup",up);g.addEventListener("pointercancel",up);g.addEventListener("lostpointercapture",up)};
document.addEventListener("click",e=>{const t=e.target.closest("[data-eye]");if(!t)return;e.preventDefault();e.stopPropagation();const h=new Set(bo.hidden||[]),g=t.dataset.eye;h.has(g)?h.delete(g):h.add(g);bo.hidden=[...h];saveB();pollBoard()},true);
const lim={},scr={};
function restore(){document.querySelectorAll("#bbody .sc").forEach(e=>{e.scrollTop=scr[e.dataset.g]||0;e.onscroll=()=>{scr[e.dataset.g]=e.scrollTop;if(e.scrollTop+e.clientHeight>=e.scrollHeight-40&&e.querySelector(".it.m")&&!lim.busy){const g=e.dataset.g;lim[g]=(lim[g]||25)+25;pollBoard()}}})}
async function pollBoard(){lim.busy=1;try{const b=await (await fetch("/api/board")).json(),FLOW=["Open PRs","Closed PRs","No status","Blocked","Todo","In Progress","Code Complete","Testing","Done"],   // development flow, left to right
all=[b.prs,b.prev,...b.groups].sort((x,y)=>{const i=n=>{const k=FLOW.indexOf(n);return k<0?FLOW.length-1.5:k};return i(x.name)-i(y.name)}),f=q.trim().replace(/^#/,"");
if(bo.open===undefined||!all.some(g=>g.name===bo.open))bo.open=(all.find(g=>g.n&&g.name!=="Done"&&!isPR(g.name))||all[2]).name;
const col={"Open PRs":"var(--run)","Closed PRs":"var(--run)","In Progress":"var(--warn)","Code Complete":"#a371f7",Testing:"var(--pin)",Todo:"#c9d1d9",Blocked:"var(--err)","No status":"#b48ead",Done:"var(--ok)"};
$("#bt").textContent=b.err?"(stale)":"";
const fam=ls=>ls.reduce((o,l)=>{const k=l.includes(":")?l.split(":")[0]:"";(o[k]=o[k]||[]).push(l);return o},{});   // label group = prefix before ":"
if(bo.label!==undefined){bo.labels=bo.label?[bo.label]:[];delete bo.label;saveB()}   // the single-label filter of the previous version
const sel=bo.labels=bo.labels||[],LB=[...new Set([...(b.labels||[]),...sel])].sort(),lf=$("#lf"),lfl=$("#lfl");
if(lfl.dataset.k!==LB.join("\n")){lfl.dataset.k=LB.join("\n");lfl.innerHTML=`<div class=lft><span><button data-lm=any title="OR inside a group (same prefix, like wave:*; labels without a prefix form one group), AND across groups">by group</button> <button data-lm=all title="a card with every selected label">all</button></span><button data-lc=1>clear</button></div>`+LB.map(l=>`<label><input type=checkbox value="${esc(l)}">${esc(l)}</label>`).join("")}
lfl.querySelectorAll("input").forEach(c=>c.checked=sel.includes(c.value));lfl.querySelectorAll("[data-lm]").forEach(x=>x.classList.toggle("on",(bo.lmode||"any")===x.dataset.lm));
lf.classList.toggle("set",sel.length>0);lf.querySelector("summary").textContent=sel.length?(bo.lmode==="all"&&sel.length>1?"all of: ":"")+(bo.lmode==="all"?sel.join(", "):Object.values(fam(sel)).map(g=>g.length>1?"("+g.join(" | ")+")":g[0]).join(" & ")):"labels";
const lok=x=>!sel.length||(bo.lmode==="all"?sel.every(l=>(x.l||[]).includes(l)):Object.values(fam(sel)).every(g=>g.some(l=>(x.l||[]).includes(l))));
const hid=new Set(bo.hidden||[]),bottom=bo.dock==="bottom",eye=g=>`<span class=eye data-eye="${esc(g.name)}" title="${hid.has(g.name)?"show":"hide"} this column"><svg viewBox="0 0 24 24" width=14 height=14 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx=12 cy=12 r=3 />${hid.has(g.name)?'<path d="M3 3l18 18"/>':""}</svg></span>`;
const LC=b.lc||{},rgb=h=>[0,2,4].map(i=>parseInt(h.slice(i,i+2),16)),mix=(a,t)=>a.map((v,i)=>Math.round(v*t+[28,34,44][i]*(1-t))),   // label color blended into the card's #1c222c
tint=x=>{const cs=(x.l||[]).map(l=>LC[l]).filter(c=>/^[0-9a-f]{6}$/.test(c||"")).map(rgb);if(!cs.length)return"";const avg=[0,1,2].map(i=>cs.reduce((s,c)=>s+c[i],0)/cs.length);return`;--bg:rgb(${mix(avg,.10)});--bgh:rgb(${mix(avg,.16)})`},
lbc=l=>/^[0-9a-f]{6}$/.test(LC[l]||"")?` style="--lb:rgba(${rgb(LC[l])},.30)"`:"";
const age=o=>{let m=Math.max(0,Math.floor((Date.now()-Date.parse(o))/60000));const d=Math.floor(m/1440),h=Math.floor(m%1440/60);m%=60;return "⏱ "+(d?d+"d "+h+"h":h?h+"h "+String(m).padStart(2,"0")+"m":m+"m")};
const ageC=o=>{const h=(Date.now()-Date.parse(o))/3600000;return h>=24?"old":h>=4?"warm":""};
setInterval(()=>document.querySelectorAll("#bbody .age").forEach(e=>{e.textContent=age(e.dataset.o);e.className="age "+ageC(e.dataset.o)}),30000);
const cards=(g,it,shown)=>shown.map(x=>`<div class=it data-n="${x.n}"${isPR(g.name)?" data-pr":""} style="--cc:${col[g.name]||"var(--mute)"}${tint(x)}"><a class=id target=_blank href="https://github.com/__REPO__/${isPR(g.name)?"pull":"issues"}/${x.n}">${isPR(g.name)?"PR ":""}#${x.n}</a>${x.ps?`<span class="pst ${x.ps[0]}">${esc(x.ps[1])}</span>`:""}${x.c?`<span class=dt>${x.c.slice(5,16).replace("T"," ")}</span>`:""}<span class=tt>${esc(x.t)}</span>${(x.l||[]).length?`<span class=lbs>${x.l.map(l=>`<span class="${(bo.labels||[]).includes(l)?"on":""}"${lbc(l)}>${esc(l)}</span>`).join("")}</span>`:""}${(x.b&&x.b.length)?`<div class=blk>blocked by ${x.b.map(n=>`<a target=_blank rel=noopener href="https://github.com/__REPO__/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${x.o?`<div class=ft><span class="age ${ageC(x.o)}" data-o="${x.o}" title="open since ${x.o.slice(0,16).replace("T"," ")} UTC">${age(x.o)}</span></div>`:""}</div>`).join("")+(it.length>shown.length?`<div class="it m">${it.length-shown.length} more · scroll</div>`:"");
const vis=all.filter(g=>!bottom||!hid.has(g.name)),one=g=>{const it=g.items.filter(x=>(!f||String(x.n).includes(f)||x.t.toLowerCase().includes(f.toLowerCase()))&&lok(x)),shown=it.slice(0,lim[g.name]||25),dot=`<i style="background:${col[g.name]||"var(--mute)"}"></i>`,cnt=`<span class=n>${f||sel.length?it.length+"/":""}${g.n}</span>`,isOpen=g.name==="Open PRs"?bo.prOpen!==false:g.name==="Closed PRs"?!!bo.prevOpen:bo.open===g.name;
if(bottom)return `<div class="col${isPR(g.name)?" prs":""}" data-g="${esc(g.name)}"><div class=ch><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}</span></div><div class=sc data-g="${esc(g.name)}">${cards(g,it,shown)}</div></div>`;
return `<div class="acc${isPR(g.name)?" prs":""} ${hid.has(g.name)?"hidcol":""} ${isOpen?"open":""}" data-g="${esc(g.name)}"><div class=ah><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}</span></div><div class=sc data-g="${esc(g.name)}">${cards(g,it,shown)}</div><div class=pad></div></div>`};
const html=vis.map(one).join("")+(bottom&&hid.size?`<div class=hidbar>hidden: ${all.filter(g=>hid.has(g.name)).map(g=>`<span class=chip data-eye="${esc(g.name)}"><span>${esc(g.name)}</span><b>${g.n}</b></span>`).join("")}</div>`:"");
if($("#bbody").dataset.h!==html){$("#bbody").dataset.h=html;$("#bbody").innerHTML=html;restore()}   // same cards: leave the DOM alone, so a scroll in progress is never interrupted
}catch(e){}finally{lim.busy=0}}
$("#lfl").onchange=e=>{const v=e.target.value,h=new Set(bo.labels||[]);e.target.checked?h.add(v):h.delete(v);bo.labels=[...h];saveB();pollBoard()};
$("#lfl").onclick=e=>{const t=e.target.closest("button");if(!t)return;e.preventDefault();if(t.dataset.lc)bo.labels=[];else bo.lmode=t.dataset.lm;saveB();pollBoard()};
document.addEventListener("click",e=>{if(!e.target.closest("#lf"))$("#lf").open=false});
pollBoard();setInterval(pollBoard,5000);
let ver=null;setInterval(async()=>{try{const v=(await (await fetch("/api/version")).json()).v;if(ver===null)ver=v;else if(v!==ver)location.reload()}catch(e){}},3000);
</script>"""
PAGE = PAGE.replace("/*RUN_STATE_CSS*/", "".join(".b.%s{color:var(--%s);%s}" % (r["state"].replace(" ", "-"), r["color"], r["css"]) for r in RUN_STATES))


PLANS = pathlib.Path(__file__).resolve().parent / "plans.json"
SUBS = ("claude", "grok", "gpt", "gemini")
def provider(m):
    m = m or ""
    if re.search(r"grok", m): return "grok"
    if re.search(r"gpt", m): return "gpt"
    if re.search(r"gemini", m): return "gemini"
    if re.match(r"^(claude-)?(sonnet|opus|fable|haiku)\b", m): return "claude"
    return None
# Monthly list prices in USD, as published (they differ by country: pick "Custom amount" to enter what you really pay, in any currency).
PRESETS = {
    "claude": {"pro": ("Pro", 20), "max5": ("Max 5x", 100), "max20": ("Max 20x", 200), "team": ("Team standard seat", 30), "teamp": ("Team premium seat", 150)},
    "gpt": {"plus": ("Plus", 20), "pro": ("Pro", 200), "business": ("Business seat", 30)},
    "grok": {"super": ("SuperGrok", 30), "heavy": ("SuperGrok Heavy", 300)},
    "gemini": {"plus": ("AI Plus", 7.99), "pro": ("AI Pro", 19.99), "ultra": ("AI Ultra", 249.99)},
}
RATES = HUBDIR / "hub-rates.json"
def rate(cur):
    """Units of `cur` per 1 USD, from open.er-api.com (no key, only the currency code leaves this machine); cached 12 h, a stale cache beats no rate."""
    if cur == "USD": return 1.0
    try: c = json.loads(RATES.read_text())
    except Exception: c = {"t": 0, "r": {}}
    if time.time() - c["t"] > 43200:
        try:
            r = json.loads(subprocess.run(["curl", "-s", "-m", "10", "https://open.er-api.com/v6/latest/USD"], capture_output=True, text=True, timeout=15).stdout)
            if r.get("result") == "success": c = {"t": time.time(), "r": r["rates"]}; RATES.write_text(json.dumps(c))
        except Exception: pass
    return c["r"].get(cur)
def plans_get():
    try: d = json.loads(PLANS.read_text())
    except Exception: d = {}
    out = {}
    for k in SUBS:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool): v = {"plan": "custom", "amount": v, "currency": "USD"}   # the first format: a bare USD number
        out[k] = v if isinstance(v, dict) else None
    return out
def plans_set(new):
    """One subscription = a preset plan, or a custom monthly amount in any 3-letter currency; null clears it. Atomic write."""
    if not isinstance(new, dict) or set(new) - set(SUBS): return {"err": "unknown subscription"}
    d = plans_get()
    for k, v in new.items():
        if v is None: d[k] = None; continue
        if not isinstance(v, dict): return {"err": f"{k}: bad plan"}
        if "plan" not in v:   # only the quota the owner reads off the provider's usage page changes
            q, w = v.get("quota"), v.get("qwin")
            if not d.get(k) or not v or not set(v) <= {"quota", "qwin"} or (q is not None and (isinstance(q, bool) or not isinstance(q, (int, float)) or not 0 <= q <= 100)) or (w is not None and not (isinstance(w, str) and len(w) <= 40)): return {"err": f"{k}: set a plan first; quota is 0-100, the window label up to 40 characters"}
            d[k] = {**d[k], **{x: v[x] for x in ("quota", "qwin") if x in v}}; continue
        plan = v.get("plan"); keep = {x: d[k][x] for x in ("quota", "qwin") if d.get(k) and d[k].get(x) is not None}
        if plan in PRESETS[k]: d[k] = {"plan": plan, "amount": PRESETS[k][plan][1], "currency": "USD", **keep}; continue
        amt, cur = v.get("amount"), str(v.get("currency") or "").upper()
        if plan != "custom" or isinstance(amt, bool) or not isinstance(amt, (int, float)) or not 0 <= amt <= 1e7 or not re.fullmatch(r"[A-Z]{3}", cur): return {"err": f"{k}: need a plan, or an amount and a 3-letter currency"}
        d[k] = {"plan": "custom", "amount": amt, "currency": cur, **keep}
    tmp = PLANS.with_suffix(".tmp"); tmp.write_text(json.dumps(d, indent=2) + "\n"); os.replace(tmp, PLANS)
    return d
# ---- quota read from each provider's own CLI, so nobody has to type it. Every window is {label, pct, resets (epoch)}.
QUOTA = HUBDIR / "hub-quota.json"
try: quota = json.loads(QUOTA.read_text())
except Exception: quota = {}
def q_claude():
    """`claude -p` streams a rate_limit_event with the five-hour and seven-day utilisation of the subscription (one tiny haiku call, no session kept)."""
    out = subprocess.run(["claude", "-p", "ok", "--model", "haiku", "--output-format", "stream-json", "--verbose", "--max-turns", "1", "--no-session-persistence"],
                         capture_output=True, text=True, timeout=180, cwd=tempfile.gettempdir()).stdout
    w = None
    for l in out.splitlines():
        try: e = json.loads(l)
        except Exception: continue
        if e.get("type") == "rate_limit_event": w = (e.get("rate_limit_info") or {}).get("unifiedWindows") or w
    if not w: return None
    names = {"seven_day": "weekly", "five_hour": "5 h"}
    return {"windows": sorted(({"label": names.get(k, k.replace("_", " ")), "pct": round(v["utilization"] * 100, 1), "resets": v.get("resetsAt")} for k, v in w.items()), key=lambda x: x["label"] != "weekly")}
def q_codex():
    """`codex app-server` answers account/rateLimits/read over stdio (JSON-RPC)."""
    pr = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        def send(o): pr.stdin.write(json.dumps(o) + "\n"); pr.stdin.flush()
        def call(i, m, params):
            send({"id": i, "method": m, "params": params}); end = time.time() + 15
            while time.time() < end:
                if not select.select([pr.stdout], [], [], 0.5)[0]: continue
                l = pr.stdout.readline()
                if not l: return None
                try: e = json.loads(l)
                except Exception: continue
                if e.get("id") == i: return e
        call(1, "initialize", {"clientInfo": {"name": "squad-hub", "version": "1"}}); send({"method": "initialized"})
        r = ((call(2, "account/rateLimits/read", None) or {}).get("result") or {}).get("rateLimits")
    finally: pr.kill()
    if not r: return None
    ws = []
    for key, fallback in (("secondary", "weekly"), ("primary", "5 h")):
        x = r.get(key)
        if x: m = x.get("windowDurationMins") or 0; ws.append({"label": "weekly" if m >= 7 * 1440 else ("5 h" if m == 300 else fallback), "pct": x.get("usedPercent"), "resets": x.get("resetsAt")})
    return {"windows": ws, "plan": r.get("planType"), "reached": r.get("rateLimitReachedType")}
def q_grok():
    """The grok CLI logs `billing: fetched credits config` (creditUsagePercent, current weekly period) each time it runs; read the newest line."""
    f = HOME / ".grok/logs/unified.jsonl"
    with open(f, "rb") as fh:
        fh.seek(0, 2); n = fh.tell(); fh.seek(max(0, n - 3_000_000)); tail = fh.read().decode("utf8", "ignore")
    last = None
    for l in tail.splitlines():
        if "billing: fetched credits config" in l: last = l
    if not last: return None
    d = json.loads(last); c = d["ctx"]["config"]; per = c.get("currentPeriod") or {}
    end = dt.datetime.fromisoformat(per["end"]).timestamp() if per.get("end") else None
    return {"windows": [{"label": "weekly", "pct": c.get("creditUsagePercent"), "resets": end}], "asof": dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()}
def q_agy():
    """`agy -p /quota` prints one line per model group (no model call): name, `Weekly Limit Remaining`, N%, reset ISO. The Gemini group is the Google plan's."""
    r = subprocess.run(["agy", "-p", "/quota"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    for l in r.stdout.splitlines():
        c = l.split("\t")
        if len(c) >= 4 and c[0].startswith("Gemini"):
            return {"windows": [{"label": "weekly", "pct": 100 - float(c[2].rstrip("%")), "resets": dt.datetime.fromisoformat(c[3].replace("Z", "+00:00")).timestamp()}]}
    return None
WEEKS = HUBDIR / "hub-weeks.json"
try: weeks = json.loads(WEEKS.read_text())
except Exception: weeks = {}
def note_week(k, r):
    """Remember the peak weekly % per window (keyed by its reset hour), so a month's quota value can sum the closed weeks too."""
    for w in r["windows"]:
        if w["label"] == "weekly" and w.get("resets") and w.get("pct") is not None:
            e = weeks.setdefault(k, {}).setdefault(str(round(w["resets"] / 3600)), {"end": w["resets"], "pct": 0})
            e["pct"] = max(e["pct"], w["pct"]); e["end"] = w["resets"]
    try: WEEKS.write_text(json.dumps(weeks))
    except OSError: pass
def week_value(k, usd, days, mstart):
    """Quota value this month = sum over weekly windows of peak % x (monthly price x 7 / days in month); a window counts when it started this month or is still open."""
    if usd is None or k not in weeks: return None
    tot = 0.0
    for e in weeks[k].values():
        if e["end"] - 7 * 86400 >= mstart or e["end"] > time.time(): tot += e["pct"] / 100 * usd * 7 / days
    return round(tot, 2)
def refresh_quota():
    """Every 20 min: ask each CLI for its quota; a failed read keeps the last good value (and its age is shown)."""
    while True:
        for k, fn in (("claude", q_claude), ("gpt", q_codex), ("grok", q_grok), ("gemini", q_agy)):
            try:
                r = fn()
                if r: r.setdefault("asof", time.time()); quota[k] = r; note_week(k, r)
            except Exception as e: quota.setdefault(k, {"windows": []})["err"] = str(e)[:120]
        try: QUOTA.write_text(json.dumps(quota))
        except OSError: pass
        time.sleep(1200)

def subs_month():
    """Per subscription, this month: API-equivalent used so far, the plan price prorated to today, and the saving."""
    now = time.localtime(); days = __import__("calendar").monthrange(now.tm_year, now.tm_mon)[1]; frac = (now.tm_mday - 1 + (now.tm_hour * 60 + now.tm_min) / 1440) / days
    used = {k: 0.0 for k in SUBS}; api = 0.0   # api = pay-per-use models (deepseek…): billed per token on top of the plans
    for r in all_runs():
        if time.localtime(r["t0"])[:2] != now[:2]: continue
        for m, v in r["mod"].items():
            p = provider(m)
            if p: used[p] += v["spend"]
            else: api += v["spend"]
    out = {}; mstart = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    for k, plan in plans_get().items():
        eq = round(used[k], 2); usd = None; rt = None
        if plan:
            rt = rate(plan["currency"]); usd = plan["amount"] / rt if rt else None
        pr = round(usd * frac, 2) if usd is not None else None
        out[k] = {"plan": plan, "usd": round(usd, 2) if usd is not None else None, "rate": rt, "noRate": bool(plan and not rt), "eq": eq, "qval": week_value(k, usd, days, mstart), "prorated": pr, "saved": round(eq - pr, 2) if pr is not None else None, "x": round(eq / pr, 1) if pr else None}
    plans = sum(v["usd"] for v in out.values() if v["usd"] is not None)
    for k in out: out[k]["quota"] = quota.get(k) if quota.get(k, {}).get("windows") else None
    return {"subs": out, "frac": round(frac, 3), "plans": round(plans, 2), "api": round(api, 2), "total": round(plans + api, 2)}

MODELS = pathlib.Path(__file__).resolve().parent / "models.json"
CHAINS = ("coding_models", "review_models", "vision_models")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}$")
# What /model offers that no file lists: the Claude 5 family from the session, the review (codex) ids and the Antigravity ids from `agy models`.
# The add box carries the provider catalogues as they are, minus the two things that made it unreadable: the
# workflow graph's prose (see known_models) and the same model under two spellings (the alias fold below). The
# gemini/gpt-oss ids are the Antigravity catalogue; refresh them from `agy models` when it moves.
EXTRA_MODELS = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
    "claude:claude-opus-5-5", "claude:claude-sonnet-5-5", "claude:claude-fable-5-1",
    "claude-deepseek-v4-flash", "gpt-5.6-terra", "gpt-6.1-sol", "claude-session",
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.7-flash-low",
    "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
    "gemini-3.1-pro-high", "gemini-3.1-pro-low",
    "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium",
    "grok-4.5", "grok-4.6"]
ALIAS = re.compile(r"^claude-(grok|gpt|gemini)-")
RUNNER = "claude:"          # review.sh's prefix for "run this one through the claude CLI"
def bare_id(mid):
    """The model's own name, without the review-path prefix. This is what the panel shows and what the box offers."""
    return mid[len(RUNNER):] if mid.startswith(RUNNER) else mid
def chain_id(chain, mid):
    """The spelling one chain needs in models.json. review.sh routes by prefix (claude:* -> the claude CLI,
    gemini-*/grok-* -> agy/grok, anything else -> codex), so an Anthropic id in review_models has to carry
    `claude:`; every other chain names the bare id the squad hands to the gateway. The panel works in bare ids
    and never sees the prefix: which spelling a role needs is this file's business, not the reader's."""
    b = bare_id(mid)
    return RUNNER + b if chain == "review_models" and b.startswith("claude-") else b
def last_coding_model(limit=15):
    """The model the newest run that got far enough to name one actually coded with.

    The chains say what the NEXT child will be given; that is not the same claim, and it read as one while the
    router was pinned to a model whose provider had stopped serving (2026-10-11). A child's outcome event carries
    its model (`route.router.N`), so the last one is read from the records, newest run first.
    """
    runs = pathlib.Path(PROF["SG_CHECKOUT"] or os.getcwd()) / "runs"
    try: ds = sorted((d for d in runs.glob("run-*")), key=lambda d: d.stat().st_mtime, reverse=True)[:limit]
    except OSError: return ""
    for d in ds:
        rec = d / "record.jsonl"
        if not rec.exists(): continue
        best = None
        for line in rec.read_text(errors="replace").splitlines():
            if '"outcome"' not in line or '"route.' not in line: continue
            try: e = json.loads(line)
            except Exception: continue
            if e.get("event") == "outcome" and (e.get("node") or "").startswith("route.") and e.get("model"):
                if best is None or (e.get("ts") or 0) > (best.get("ts") or 0): best = e
        if best: return best["model"]
    return ""

def known_models(d):
    """Suggestions for the add box: the models the chains already use, the Claude Code settings, plus EXTRA_MODELS.

    The workflow graph is no longer read: it names a dozen ids in its own prose (the ADR-0113 history of this
    flow), so scraping it filled the box with models that appear nowhere else.

    One model, one row. A non-Anthropic provider is reachable as `grok-4.6` and as `claude-grok-4.6` and both
    spellings were listed: the alias is folded onto the bare id, which is the form the chains name and the
    bridge answers.
    """
    out = list(EXTRA_MODELS)
    for k in CHAINS: out += d.get(k, [])
    try: out += list(json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text()).get("modelSettings", {}))
    except Exception: pass
    return sorted({bare_id(ALIAS.sub(r"\1-", m)) for m in out if MODEL_ID.match(m)})
EFFORTS = ("low", "medium", "high", "max")
# local hours a model may run, "HH-HH", start inclusive, end exclusive, wrapping midnight (22-07). Absent = always.
HOURS = re.compile(r"^([01]\d|2[0-3])-([01]\d|2[0-3])$")
def model_hours(d, chain, mid):
    """The window for one model, or "" when it may always run."""
    h = d.get("hours") if isinstance(d.get("hours"), dict) else {}
    hk = h.get(chain) if isinstance(h.get(chain), dict) else {}
    v = hk.get(mid)
    return v if isinstance(v, str) and HOURS.match(v) and v[:2] != v[3:] and mid in d.get(chain, []) else ""
def in_hours(d, chain, mid, when=None):
    """True when the model may run at `when` (default: now), local hour, and its own window is a rotation."""
    w = model_hours(d, chain, mid)
    if not w: return True
    h = (when or dt.datetime.now()).hour; a, b = int(w[:2]), int(w[3:])
    return a <= h < b if a <= b else (h >= a or h < b)
def hours_of(d):
    """Per-chain {model-id: "HH-HH"} from models.json's optional top-level `hours`; only entries whose model is in that chain."""
    h = d.get("hours") if isinstance(d.get("hours"), dict) else {}
    return {k: {m: v for m, v in (h.get(k) or {}).items() if model_hours(d, k, m) == v} for k in CHAINS}
def efforts_of(d):
    """Per-chain {model-id: effort} from models.json's optional top-level `efforts`; every chain present, only entries whose model is in that chain."""
    e = d.get("efforts") if isinstance(d.get("efforts"), dict) else {}
    return {k: {m: v for m, v in (e.get(k) or {}).items() if isinstance(e.get(k), dict) and m in d.get(k, []) and v in EFFORTS and v != "medium"} for k in CHAINS}
def models_get():
    """The three chains as the panel reads them: bare ids, so one model is one row in every chain and in the box.

    The file keeps the spelling each role needs (chain_id); the panel never sees it, so a reader cannot pick the
    wrong one of two spellings of the same model."""
    d = json.loads(MODELS.read_text())
    return {**{k: [bare_id(m) for m in d.get(k, [])] for k in CHAINS},
            "efforts": {k: {bare_id(m): v for m, v in ek.items()} for k, ek in efforts_of(d).items()},
            "hours": {k: {bare_id(m): v for m, v in hk.items()} for k, hk in hours_of(d).items()},
            "known": known_models(d), "last_coding": last_coding_model()}
def models_set(new):
    """Replace the three role chains in models.json; every other key is kept. Atomic write, so a reader never sees half a file."""
    new = {k: v for k, v in new.items() if k != "known"} if isinstance(new, dict) else new
    if not isinstance(new, dict) or set(new) - set(CHAINS) - {"efforts", "hours"}: return {"err": "unknown chain"}
    d = json.loads(MODELS.read_text()); eff = new.pop("efforts", None); hrs = new.pop("hours", None)
    for k, v in new.items():
        if not isinstance(v, list) or not 1 <= len(v) <= 8 or not all(isinstance(m, str) and MODEL_ID.match(m) for m in v):
            return {"err": f"{k}: need 1-8 distinct model ids"}
        v = [chain_id(k, m) for m in v]      # the panel works in bare ids; the file gets the spelling the role needs
        if len(set(v)) != len(v): return {"err": f"{k}: need 1-8 distinct model ids"}
        d[k] = v
    if eff is not None:
        # efforts live beside the chains, never inside them (other scripts jq the arrays of strings); "default" = key absent
        if not isinstance(eff, dict) or set(eff) - set(CHAINS): return {"err": "efforts: unknown chain"}
        for k, ek in eff.items():
            if not isinstance(ek, dict) or not all(isinstance(m, str) and m in {bare_id(x) for x in d.get(k, [])} and v in EFFORTS for m, v in ek.items()):
                return {"err": f"efforts.{k}: model must be in the chain, effort one of {'|'.join(EFFORTS)}"}
        # keyed by the spelling the chain stores, like the chain itself (medium is the default: absent)
        d["efforts"] = {k: {chain_id(k, m): v for m, v in (eff.get(k) or {}).items() if v != "medium"} for k in CHAINS}
    if hrs is not None:
        # hours live beside the chains, like efforts; a window equal at both ends is meaningless, so it is dropped
        if not isinstance(hrs, dict) or set(hrs) - set(CHAINS): return {"err": "hours: unknown chain"}
        for k, hk in hrs.items():
            if not isinstance(hk, dict) or not all(isinstance(m, str) and m in {bare_id(x) for x in d.get(k, [])} and isinstance(v, str) and HOURS.match(v) for m, v in hk.items()):
                return {"err": f"hours.{k}: model must be in the chain, window HH-HH (00-23)"}
        d["hours"] = {k: {chain_id(k, m): v for m, v in (hrs.get(k) or {}).items() if v[:2] != v[3:]} for k in CHAINS}
    if "hours" in d:
        d["hours"] = {k: v for k, v in hours_of(d).items() if v}
        if not d["hours"]: del d["hours"]
    if "efforts" in d:
        d["efforts"] = {k: v for k, v in efforts_of(d).items() if v}
        if not d["efforts"]: del d["efforts"]
    tmp = MODELS.with_suffix(".tmp"); tmp.write_text(json.dumps(d, indent=2) + "\n"); os.replace(tmp, MODELS)
    return models_get()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/test/"):   # a test.sh pass: its log, refreshed while it runs
            n = re.sub(r"[^\w-]", "", self.path[6:]); T = STATE / "tests"
            try: d = json.loads((T / (n + ".json")).read_text())
            except Exception: d = {}
            try: lg = (T / (n + ".log")).read_text(errors="replace")[-60000:]
            except Exception: lg = "(no output yet)" if d.get("state") == "running" else "no such test"   # test.sh makes the log after the json
            import html as _h
            ref = '<meta http-equiv="refresh" content="3">' if d.get("state") == "running" else ""
            body = f'<!doctype html><meta charset=utf-8>{ref}<body style="background:#0d1117;color:#c9d1d9;font:12px ui-monospace,monospace;margin:12px"><div style="color:#58a6ff">test #{d.get("issue")} · {d.get("state")}{" (exit "+str(d.get("rc"))+")" if "rc" in d else ""} · {_h.escape(d.get("dir",""))}</div><div style="color:#8b949e">$ {_h.escape(d.get("cmd",""))}</div><pre style="white-space:pre-wrap">{_h.escape(lg)}</pre><script>scrollTo(0,document.body.scrollHeight)</script>'.encode(); ct = "text/html; charset=utf-8"
        elif self.path.startswith("/api/runs"): body, ct = json.dumps(runs()).encode(), "application/json"
        elif self.path.startswith("/api/stats"): body, ct = json.dumps(stats((re.search(r"period=(\w+)", self.path) or [0, "day"])[1] if re.search(r"period=(hour|day|month)", self.path) else "day", (re.search(r"src=(sub|api)", self.path) or [0, "both"])[1])).encode(), "application/json"
        elif self.path.startswith("/api/issue"):
            m = re.search(r"[?&]n=(\d{1,6})\b", self.path); body, ct = json.dumps(issue_detail(int(m[1])) if m else {"err": "bad n"}).encode(), "application/json"
        elif self.path.startswith("/api/board"): body, ct = json.dumps(board()).encode(), "application/json"
        elif self.path.startswith("/api/version"): body, ct = json.dumps({"v": LOADED}).encode(), "application/json"
        elif self.path.startswith("/api/subs"): body, ct = json.dumps({**subs_month(), "presets": {k: {i: {"name": n, "usd": p} for i, (n, p) in v.items()} for k, v in PRESETS.items()}}).encode(), "application/json"
        elif self.path.startswith("/api/models"): body, ct = json.dumps(models_get()).encode(), "application/json"
        elif self.path.startswith("/api/speed"): body, ct = json.dumps(speed()).encode(), "application/json"
        else: body, ct = PAGE.replace("__REPO__", REPO).replace("__CAPS__", json.dumps(loop_caps())).encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        # loopback only, and a cross-site page must not be able to write: require our own origin and a JSON body
        host = (self.headers.get("Host") or "").split(":")[0]; org = self.headers.get("Origin") or ""
        ok = self.path in ("/api/models", "/api/plans") and host in ("127.0.0.1", "localhost") and re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", org) and "json" in (self.headers.get("Content-Type") or "")
        try: out = (plans_set if self.path == "/api/plans" else models_set)(json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 8192)) or b"{}")) if ok else {"err": "forbidden"}
        except Exception as e: out = {"err": "bad request"}
        body = json.dumps(out).encode(); self.send_response(400 if "err" in out else 200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

SELF = pathlib.Path(__file__).resolve(); LOADED = SELF.stat().st_mtime_ns
def watch_self():
    """Re-exec when this file changes on disk, so an edit goes live without anyone restarting the hub; open pages reload on the new /api/version."""
    while True:
        time.sleep(2)
        try:
            if SELF.stat().st_mtime_ns != LOADED: os.execv(sys.executable, [sys.executable, str(SELF)] + sys.argv[1:])
        except OSError: pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7700
    threading.Thread(target=refresh_prs, daemon=True).start(); threading.Thread(target=refresh_quota, daemon=True).start(); threading.Thread(target=watch_self, daemon=True).start(); threading.Thread(target=refresh_board, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
