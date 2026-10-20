#!/usr/bin/env python3
"""Squad hub: one browser page that lists every live run dashboard (agent-squad dashboard_server processes) and embeds them.
usage: hub.py [port]   default 7700, loopback only. Read-only: it discovers servers via ps/lsof and reads record.jsonl."""
import datetime as dt, glob, importlib.util, json, os, re, select, shlex, statistics as st, subprocess, sys, tempfile, threading, time, pathlib, urllib.parse, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
import agyquota, chainhealth, provkey, router_envelope   # the one envelope source, shared with launch.py (#436)
from issue_state import board_state, KINDS, TEST_CASE_TITLE, _utc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_columns_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns_module = importlib.util.module_from_spec(_columns_spec); _columns_spec.loader.exec_module(_columns_module)
# The board draws the shipped definition (#247): its statuses, in its order, with their colours. Open PRs are a lane beside
# the axis, never a column, and it is for tracking only (#384); a work branch has no lane of its own -- its kind reaches the
# kinds view -- and a landed branch is read from its issue card. Every name comes from the table; COLUMN reads a column or a
# lane by its role.
BOARD_COLUMNS = _columns_module.view_columns()
BOARD_LANES = _columns_module.LANES
COLUMN = {**_columns_module.ROLE, **{lane["key"]: lane["name"] for lane in BOARD_LANES}}
BOARD_NAMES_JSON = json.dumps(COLUMN, separators=(",", ":"))
BOARD_COLORS_JSON = json.dumps({c["name"]: c["color"] for c in [*BOARD_COLUMNS, *BOARD_LANES]}, separators=(",", ":"))
BOARD_FLOW_JSON = json.dumps([column["name"] for column in BOARD_COLUMNS], separators=(",", ":"))

def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout
# A tool's absolute path differs by platform: /usr/sbin/lsof holds on macOS and is absent on the CI runner, where a
# hardcoded path turns a missing tool into a failed pass. env.sh resolves it into SG_LSOF; the last entry keeps a tool
# outside both paths reachable.
LSOF = next((p for p in (os.environ.get("SG_LSOF"), "/usr/sbin/lsof", "/usr/bin/lsof") if p and os.path.exists(p)), "lsof")

def _profile():
    """SG_REPO/SG_OWNER/SG_PROJECT/SG_CHECKOUT from the environment (hubmux.py pins them from the seam), else from the
    seam, address.sh, for the profile in force (SG_PROFILE); never from the cwd. A profile the seam refuses gives
    nothing. One profile is one repository: the retired SG_TOOLING_REPO is never read (#20)."""
    keys = ["SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_CHECKOUT"]
    if all(os.environ.get(k) for k in keys): return {k: os.environ.get(k, "") for k in keys}
    here = pathlib.Path(__file__).resolve().parent
    p = subprocess.run(["bash", str(here / "address.sh"), os.environ.get("SG_PROFILE", "")], capture_output=True, text=True, timeout=30)
    ctx = {k: v for k, _, v in (ln.partition("=") for ln in shlex.split(p.stdout))} if p.returncode == 0 else {}
    return {k: os.environ.get(k) or ctx.get(k, "") for k in keys}
PROF = _profile(); REPO, OWNER, PROJECT_NO = PROF["SG_REPO"], PROF["SG_OWNER"], PROF["SG_PROJECT"]
_PROFILE_ENV = {**os.environ, "SG_REPO": REPO, "SG_OWNER": OWNER, "SG_PROJECT": PROJECT_NO}   # the environment the hub was loaded in, kept for the seam
if not REPO: sys.exit("hub: no SG_REPO (set SG_PROFILE to a profile address.sh knows)")
CHECKOUT = PROF["SG_CHECKOUT"]
if not CHECKOUT: sys.exit("hub: no SG_CHECKOUT (the profile in force names no checkout)")
def repos_from(primary, *others):
    """The repositories the board reads, named in ONE place and never guessed: the profile's SG_REPO, plus each
    repository given after it, deduplicated in order (#1820). A profile gives none: one profile is one repository (#20)."""
    out = []
    for r in (primary,) + tuple(others):
        r = (r or "").strip()
        if r and r not in out: out.append(r)
    return out
REPOS = repos_from(REPO)
def repo_tag(repo, repos=None):
    """The short tag a card wears for its repository: the name after the owner when that name stands for exactly one
    repository the board reads, else the full owner/name -- the tag that does not fit (#1820)."""
    repos = REPOS if repos is None else repos
    name = repo.rsplit("/", 1)[-1]
    return name if sum(1 for r in repos if r.rsplit("/", 1)[-1] == name) == 1 else repo
_board_state = None   # the seam once per process (#236): the pane asks it for the board's rows, and holds no call of its own
def board_seam():
    """The pane's one reach of the board: the seam this repository binds the vocabulary to. The carrier,
    its field ids and its retries are the binding's; here the pane projects the rows it draws."""
    global _board_state
    if _board_state is None:   # the binding's caches sit in the hub's own folder; each call goes through subprocess.run as the pass looks it up
        # The profile is the one this hub was loaded for (PROF), never the environment of the first pass: hubmux pins a
        # profile's SG_* only while it imports its hub, so a seam built later from os.environ binds another board.
        _board_state = board_state(OWNER, PROJECT_NO, REPO, env=_PROFILE_ENV, state_dir=HUBDIR, run=lambda a, **kw: subprocess.run(a, **kw))
    return _board_state
HOME = pathlib.Path.home()
def state_dir():
    """$SG_STATE when the runner exports it, else the state directory the seam (address.sh) names, `sg_state_base`, for the
    machine -- the shape single.sh and mkreq.sh already use. Only the seam spells the machine state directory, so a runner
    that exports no SG_STATE reads a state directory under its own home instead of dying with KeyError at import; the loop,
    which exports SG_STATE, keeps the directory it always had."""
    st = os.environ.get("SG_STATE")
    if st: return st
    here = pathlib.Path(__file__).resolve().parent
    p = subprocess.run(["bash", "-c", '. "$1/address.sh" 2>/dev/null; sg_state_base', "_", str(here)], capture_output=True, text=True, timeout=30)
    st = p.stdout.strip()
    if not st: sys.exit("hub: no state directory (export SG_STATE, or repair the seam, address.sh)")
    return st
STATE = pathlib.Path(state_dir())
# hub state lives in the target repo's runs area (already gitignored there), never versioned; SG_HUB_DIR overrides
HUBDIR = pathlib.Path(os.environ.get("SG_HUB_DIR") or pathlib.Path(CHECKOUT) / "runs" / ".hub"); HUBDIR.mkdir(parents=True, exist_ok=True)
def shared_dir():
    """$SG_SHARED, else the seam's `sg_shared_dir` (address.sh), as env.sh resolves it: one directory for every profile, never
    a profile's STATE. `SG_SHARED or STATE` put a hub started without SG_SHARED on its own quota cache, and the loop read another (#678)."""
    sh = os.environ.get("SG_SHARED")
    if sh: return sh
    p = subprocess.run(["bash", "-c", '. "$1/address.sh" 2>/dev/null; sg_shared_dir', "_", str(pathlib.Path(__file__).resolve().parent)], capture_output=True, text=True, timeout=30)
    return p.stdout.strip() or str(STATE)   # STATE only where no seam is shipped beside the hub (a bare test copy)
SHARED = pathlib.Path(shared_dir()); SHARED.mkdir(parents=True, exist_ok=True)
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

# A run's issue is named by its own request (runs/<run>/request.txt, written before the run starts). The forms are
# owner/repo#N, "issue #N" and "Refs #N", with any run of digits: this repository's issues start at one digit.
# The fourth form is the dispatch one, `GitHub issue(s) 10, 12`, which names every issue of one slice in a single go: it
# carries several numbers, it does not always sit at the start of a line, and a sliced child's own request names its
# issues only that way, so without it the card fell through to "no issue: the request names no issue" (#47).
ISSUE_REF = re.compile(r"(?:\b[\w.-]+/[\w.-]+#|\b(?:issue|Refs) #|\bissues?\s*(?:\(s\))?\s+)(\d+(?:\s*,\s*\d+)*)", re.I)
# A dispatch request (autofrontier, autobug) names its children with a `PANEL:` line and one `CHILD — GitHub issue(s) 33, 56 (...)`
# line per child, and pastes each ticket's text under it. The CHILD lines are the issues the run dispatched; a number the pasted
# text quotes ("issue #900") is not, so when a request has CHILD lines they alone name the run's issues (#33).
CHILD_ISSUES = re.compile(r"^CHILD\s+[\u2014-]\s+GitHub\s+issues?(?:\(s\))?\s+([#\d][^()\n:]*)", re.M)

def ref_issues(text):
    """Every issue one reference names: the number after `#N`, or the whole comma list of the dispatch form."""
    return {n for g in ISSUE_REF.findall(text) for n in re.findall(r"\d+", g)}

def request_issues(text):
    """The issues a request names: every number on its CHILD lines when it has any, else every reference in its text."""
    kids = [n for g in CHILD_ISSUES.findall(text) for n in re.findall(r"\d+", g)]
    return set(kids) if kids else ref_issues(text)

def run_action(r):
    """What a run does, in one word: its node kind (`code` runs implement) and then its own flags."""
    if r.get("kind") == "test": return "test"
    if r.get("merge"): return "merge"
    if r.get("bug"): return "fix"
    return "dispatch" if r.get("kind") == "dispatch" else "implement"

def run_label(r):
    """A run card's name, computed here and rendered as given (ADR-0137): the action, every issue it works, else `run <id>`.
    A fix run is named by its PR, and the issue it fixes stays out of the list: that PR is already the name."""
    is_ = ", ".join("#" + i for i in r.get("issues") or [] if int(i) != r.get("fix"))
    if r.get("kind") == "test" or r.get("url"): return "test " + is_
    if r.get("fix"): return f"fix PR #{r['fix']}" + (f" · {is_}" if is_ else "")
    if is_: return run_action(r) + " " + is_ + (f" · on PR #{r['stack']}" if r.get("stack") else "")
    return "run " + str(r.get("id") or "")[-6:]

def scan(p):
    """Parse one record.jsonl into fields. Finished runs come from `hist`, unfinished ones are cached by (mtime, size)."""
    p = str(p)
    if p in hist and "v14" in hist[p]: return hist[p]   # v10 added the worktree paths a run works in (the load attribution reads them); v11 reads the run issue from request.txt; v12 reads every issue of a dispatch request's CHILD lines; v13 reads the test share a gate counted; v14 reads the card's label from the dispatch form too
    try: st = os.stat(p)
    except OSError: return None
    c = live.get(p)
    if c and c[0] == st.st_mtime and c[1] == st.st_size: return c[2]
    spend = sub = api = 0.0; mod = {}; t0 = t1 = ver = None; reasons = []; land = False; issues = set(); ntok = 0; kind = "code"; fix = None; stack = None; bug = False; merge = False; head = None; rc = None; share = None
    rid = pathlib.Path(p).parent.name   # the run folder, read before the loop: the share below rebinds a local of its own, never `p`
    accepted, done_kids, wts = set(), set(), set()
    try: req_issues = request_issues((pathlib.Path(p).parent / "request.txt").read_text(errors="replace")); has_req = True
    except OSError: req_issues = set(); has_req = False
    with open(p) as f:
        for i, l in enumerate(f):
            if i < 200: issues.update(ref_issues(l))   # the fallback when request.txt is missing: a reference quoted in the transcript is not the run's own issue
            if i < 200 and fix is None:
                fm = re.search(r"FIX RUN[^#]{0,120}?PR #(\d+)", l)
                if fm: fix = int(fm.group(1))
            if i < 200 and not merge and "CONFLICT FIX" in l: merge = True   # forkclose conflict-fix run
            # A run that fixes something is named "fix": a bug-fix run (autobug, or a hand request naming the bug issue) and a
            # fork-only fix run alike. forkclose's fix runs name no issue and no `fix/` branch -- their request says "on the
            # local fork branch gui/<N>" -- so without this they fell through to the "build" label with the issue number, and a
            # red-gate fix read as new work (#1495, 2026-10-11).
            if i < 200 and not bug and re.search(r"\(bug\)|branch fix/|on the local fork branch|FIX RUN .*fork", l): bug = True
            if i < 200 and stack is None:   # an optimistically stacked run names its parent PR ("STACKED on #N, whose PR #P")
                sm = re.search(r"STACKED on #\d+[^#]{0,40}PR #(\d+)", l)
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
                h = ((d.get("workspace") or {}).get("head") if isinstance(d.get("workspace"), dict) else None) or ((d.get("effect") or {}).get("head") if isinstance(d.get("effect"), dict) else None)
                if h: head = h
                w = d.get("workspace") if isinstance(d.get("workspace"), dict) else None   # the worktrees this run works in: how a load is traced back to it (see attribute)
                if w and w.get("path") and w.get("name") not in (None, "."): wts.add(w["path"])
                if isinstance(d.get("counts"), dict) and d["counts"]:   # the share of the suite the gate counted (#80): the newest one the record carries wins, so a red gate shows the work it got to
                    np = sum(x.get("passed", 0) for x in d["counts"].values() if isinstance(x, dict))
                    n = sum(x.get("total", 0) for x in d["counts"].values() if isinstance(x, dict))
                    if n: share = {"passed": np, "total": n, "pct": round(100 * np / n)}
            elif e == "run_start":
                k = d.get("kind") or (d.get("engine_options") or {}).get("kind") or (d.get("defaults") or {}).get("kind")   # absent today: every run is code
                if k in ("test", "gui-test"): kind = "test"
            elif e == "land":
                if d.get("outcome") == "pass": land = True
                h = (d.get("effect") or {}).get("head") if isinstance(d.get("effect"), dict) else None
                if h: head = h
            elif e == "verdict": ver, reasons, t1, rc = d.get("verdict"), d.get("reasons") or [], ts, d.get("exit_code")
    if has_req: src, why, issues = ("request", None, req_issues) if req_issues else (None, "request-no-issue", set())   # the request is the truth; the transcript never overrides it
    else: src, why = ("transcript" if issues else None), "request-missing"
    r = {"spend": round(spend, 4), "sub": round(sub, 4), "api": round(api, 4), "mod": mod, "v8": 1, "v9": 1, "v10": 1, "v11": 1, "v12": 1, "v13": 1, "v14": 1, "t0": t0, "t1": t1, "verdict": ver, "reasons": reasons, "land": land, "issues": sorted(issues, key=int), "issue_src": src, "issue_why": why, "kind": kind, "fix": fix, "stack": stack, "bug": bug, "merge": merge, "head": head, "exit_code": rc, "agents": len(accepted - done_kids), "wts": sorted(wts), "share": share}
    r["id"] = rid   # the run folder: the label a run with no issue falls back on
    r["label"] = run_label(r)               # the card's name is computed here, so the page renders a value and holds no rule (ADR-0137)
    if ver is not None:
        hist[p] = r
        with LOCK:
            try: HIST.write_text(json.dumps(hist))
            except OSError: pass
    else: live[p] = (st.st_mtime, st.st_size, r)
    return r

def state_of(r, age):
    ver, reasons = r["verdict"], r["reasons"]
    if ver is None: return "running" if age < STALL_SECS else "stalled"
    if ver == "failed" and r["land"] and reasons and all(str(x).startswith("ac-verify:no_ac_commands") for x in reasons): return "unverified"   # work landed, but the final gate had no acceptance command to run
    return ver

def run_roots(co):   # where a checkout keeps run folders: its runs/, and each run or child worktree's runs/ (#580, #585)
    co = pathlib.Path(co)
    return [co / "runs", *(w / "runs" for kind in ("run", "child") for w in sorted((co / "worktrees" / kind).glob("*")))]

def project_dirs():
    ds = set(run_roots(CHECKOUT))
    for x in os.environ.get("SG_PROJECTS", "").split(":"):
        if x: ds.update(run_roots(x))
    for l in sh("/bin/ps", "-axwwo", "command=").splitlines():
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
def handled_of(st, r, checkout=None):
    """A needs-human run whose head is an ancestor of the fork's local/next (or whose PR is merged) is handled."""
    if st != "needs-human": return st
    if r.get("merged"): return "handled"
    head = r.get("head")
    if not head: return st
    root = pathlib.Path(checkout) if checkout else pathlib.Path(CHECKOUT)
    p = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", str(head), "main"], capture_output=True)
    return "handled" if p.returncode == 0 else st
def badge_of(st, r=None):
    """Hub run-list badge: a needs-human run never prints `exit N`; that lives on the tooltip."""
    r = r or {}
    color = next((x["color"] for x in RUN_STATES if x["state"] == st), "warn")
    if st == "handled": text = "ended · handled (landed in fork)"
    elif st == "needs-human": text = "ended · needs a decision"
    else: text = st
    rc = r.get("exit_code")
    if rc is None and st in ("needs-human", "handled"): rc = 10
    title = f"exit {rc}" if rc is not None and st in ("needs-human", "handled") else ""
    return {"text": text, "color": color, "title": title, "state": st}
LIVE_STATES = ("running", "starting", "stalled")
def group_of(r):
    """RUNNING holds a live process (running/starting/stalled). An ended run, needs-human included, goes to an age bucket."""
    if r.get("live", True) and r.get("state") in LIVE_STATES: return "running"
    h = (r.get("age") or 0) / 3600
    return "1h" if h <= 1 else "2h" if h <= 2 else "4h" if h <= 4 else "8h" if h <= 8 else "24h" if h <= 24 else "d"

STALL_SECS = 1800   # state_of: a run with no verdict and no event for this long reads `stalled`
SQUAD_START = re.compile(r"squad start(?: |$)")
def run_gone(r, age, ps_lines, folder, request=None):
    """True when a run with no verdict has been silent past the stall limit and no process of its own is left: it was killed
    (a restart, a crash) and will never write a verdict, so it is not live however old its record. A process is the run's own when
    its argv names the run folder or a worktree the record named, or when it is a `squad start` that carries the run's request (its
    first line; a run with no request.txt cannot be told apart, so any `squad start` keeps it). The run's dashboard server never counts.
    Rule layer: no hub state, no I/O."""
    if r["verdict"] is not None or age < STALL_SECS: return False
    paths = [x for x in [str(folder), *(r.get("wts") or [])] if x]
    first = next((l.strip() for l in (request or "").splitlines() if l.strip()), "")[:80]
    for l in ps_lines:
        if "dashboard_server" in l: continue
        if any(x in l for x in paths): return False
        if SQUAD_START.search(l) and (not first or first in l): return False
    return True

def gone_badge(b):
    """The badge of a run that is gone: the state stays (`stalled`), the text says why it is not live."""
    return {**b, "text": b["text"] + " · no process"}

def squad_roots(sg_squad, sg_checkout, checkout, here):
    """Where the squad package may live, in order, each once: SG_SQUAD, the SG_CHECKOUT variable, the profile's checkout as the
    hub resolved it (the seam, ADR-0138), then the repository this file sits in (parents[3]; the live copy under ~/.claude has none).
    Rule layer: no hub state, no I/O."""
    out = []
    for r in (sg_squad, sg_checkout, checkout, str(pathlib.Path(here).resolve().parents[3])):
        if r and r not in out: out.append(r)
    return out

def load_squad(roots):
    """`squad.caps` and `squad.run_caps` from the first root that holds the package, and only from that root: a package that
    some other entry of this machine's path happens to hold answers for no root (#452). A root that fails leaves nothing
    behind (not on sys.path, not in sys.modules), so the next one imports clean. Raises the last failure, which names what
    was missing.
    The resolution is one call's business: whatever answers, the caller's `squad` modules are the objects they were and its
    table gains none (#453). This module is read by a longer-lived interpreter too -- a suite that imports it into its own
    process -- and a purge that stayed there parted every class that process already held, so a raise it expected was a
    different class from the one it caught."""
    err = ModuleNotFoundError("no squad package in: " + ", ".join(roots))
    ambient = {m: mod for m, mod in sys.modules.items() if m == "squad" or m.startswith("squad.")}
    for m in ambient: del sys.modules[m]
    try:
        for root in roots:
            added = root not in sys.path
            if added: sys.path.insert(0, root)
            try:
                from squad import caps, run_caps
                here = getattr(caps, "__file__", None)   # the caps module the import found: it must live under the root that was asked
                if here and pathlib.Path(here).resolve().is_relative_to(pathlib.Path(root).resolve()): return caps, run_caps
                err = ModuleNotFoundError(f"{root} holds no squad package (the one imported is {here})")
            except Exception as e:
                err = e
            for m in [m for m in sys.modules if m == "squad" or m.startswith("squad.")]: del sys.modules[m]
            if added: sys.path.remove(root)
        raise err
    finally:
        for m in [m for m in sys.modules if m == "squad" or m.startswith("squad.")]: del sys.modules[m]
        sys.modules.update(ambient)

CAPS_LOGGED = set()
def log_once(line, seen=CAPS_LOGGED, out=None):
    """One stderr line per distinct text per process: a failure that repeats every poll is named once, never silenced and never a flood."""
    if line in seen: return
    seen.add(line); print(line, file=out or sys.stderr, flush=True)

def loop_caps():
    """The machine-wide ceiling (squad/caps.py), from the one resolution the engine and the loop enforce: {runs, children, waiting}.
    `waiting` lists each live wait as {ceiling, holder}. The project file of SG_CHECKOUT may lower the pair. None when the
    resolution fails (no squad package in any root, no caps block, a project that raises it): the failure is logged once and the panel
    then shows plain counts, never a guessed ceiling.
    `per_run` is the other pair, read by squad/run_caps.py from the graph the engine resolves (or the newest live run's own);
    it is named in the tooltip and never merged into the machine-wide pair. None when no graph reads."""
    roots = squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        C, RC = load_squad(roots)
        co = os.environ.get("SG_CHECKOUT") or CHECKOUT   # the profile's checkout is the fallback, never the process cwd
        c = C.ceiling(co)
        per_run = RC.read(co, [r.get("dir") for r in list(LOAD_RUNS) if r.get("dir")])
        return {"runs": c.caps["runs"], "children": c.caps["children"], "waiting": c.waiting(), "per_run": per_run}
    except Exception as e:
        log_once(f"hub: loop_caps unavailable, the footer shows plain counts: {type(e).__name__}: {' '.join(str(e).split())} (roots tried: {', '.join(roots)})")
        return None

def waiting_txt(caps):
    """What waits for a slot, named: ` · waiting: <holder> (caps.runs)`. Empty when nothing waits."""
    w = (caps or {}).get("waiting") or []
    return " · waiting: " + ", ".join(f"{x['holder']} ({x['ceiling']})" for x in w) if w else ""

def usd_txt(v):
    if v is None: return "—"
    if v >= 100: return "$" + str(int(round(v)))
    return f"${v:.2f}"

def group_header(kind, rows, caps):
    """RUNNING shows occupancy against the machine-wide ceiling, runs then agents, and names what waits; other groups keep
    count · cost. Spend is the tooltip."""
    n = len(rows)
    spend = sum((r.get("spend") or 0) for r in rows)
    agents = sum((r.get("agents") or 0) for r in rows)
    title = usd_txt(spend)
    if kind == "running":
        per_run = ((caps or {}).get("per_run") or {}).get("label")
        if per_run: title += " · " + per_run
        if caps and isinstance(caps.get("runs"), int) and isinstance(caps.get("children"), int):
            return {"text": f"RUNS {n}/{caps['runs']} · AGENTS {agents}/{caps['children']}{waiting_txt(caps)}", "title": title}
        return {"text": f"{n} · {agents}", "title": title}
    return {"text": f"{n} · {title}", "title": title}
def run_rank(st, live=True):
    return 0 if live and st in LIVE_STATES else 1

# ---------------------------------------------------------------- machine load (#1776)
# The strip answers "is this machine saturated?"; a card answers "because of whom?". Both read ONE cached sample, taken by
# a thread inside this process: serving the page, and polling it, spawn nothing and read no counter, so the hub can never
# become part of the load it reports. CPU and RAM are read from this process (Mach's own counters, else psutil); the GPU
# counter is the one exception — an unprivileged `ioreg` read per sample, the only source macOS offers without root.
LOAD_SAMPLE_SECS = 5      # one sample per interval; the page polls the cached reading on this same cadence
LOAD_WARN_PCT = 80.0      # machine CPU % at or above which the strip warns and a card may be blamed
LOAD_WARN_SAMPLES = 2     # consecutive samples above the threshold: one hot sample is not machine load
LOAD_ERR_PCT = 95.0       # machine % at or above which that ONE reading is red: the machine is at its ceiling right now
                          # (#1816). Amber is the machine-wide, sustained statement above; red is per attribute and immediate
                          # — and it wins over the amber. The page is served this constant, never a literal of its own.
FOOT_WARN_PCT = LOAD_WARN_PCT   # the footer's facts draw the strip's own ladder (#1843): a subscription token at or above
FOOT_ERR_PCT = LOAD_ERR_PCT     # this reads amber, at or above the ceiling reads red. Named here once, beside the pair
                                # they mirror, and never as a magic number inside the render.
LOAD_MIN_SHARE_PCT = 3.0  # a card is marked only for a run whose process tree owns at least this much of the machine
LOAD_HISTORY = 12         # CPU samples kept, so the warn rule can look back (a minute at the default cadence)
GPU_CMD = ("/usr/sbin/ioreg", "-r", "-d", "1", "-c", "IOAccelerator", "-w", "0")
GPU_RE = re.compile(r'"Device Utilization %"\s*=\s*(\d+(?:\.\d+)?)')

def _pct(v):
    """A source's answer as a percentage, or None when it did not really answer: a string, a negative, NaN. A source that
    cannot be read has to show `?` — a 0 would read as an idle machine and hide the load the strip exists to show."""
    try: f = float(v)
    except (TypeError, ValueError): return None
    if f != f or f < 0: return None   # NaN compares false to itself
    return min(f, 100.0)

def pct_cell(v):
    """One cell of the strip: the rounded percentage, or `?` where the hub cannot see."""
    f = _pct(v)
    return "?" if f is None else f"{f:.0f}%"

def load_line(cpu, ram, gpu):
    """The strip's single line, in the order it is read."""
    return f"CPU {pct_cell(cpu)} · RAM {pct_cell(ram)} · GPU {pct_cell(gpu)}"

def sustained(samples, warn=LOAD_WARN_PCT, need=LOAD_WARN_SAMPLES):
    """True when the newest `need` samples all sit at or above `warn`: the load has to outlive a single reading, so a
    one-off spike blames nobody. A sample that could not be read breaks the run instead of passing for a low one."""
    n = max(int(need), 1); tail = list(samples or [])[-n:]
    return len(tail) == n and all(s is not None and s >= warn for s in tail)

def parse_gpu(text):
    """GPU utilisation out of IOAccelerator's PerformanceStatistics. A machine with no accelerator, or a driver that does
    not report utilisation, answers None: the cell shows `?` rather than a zero nobody measured."""
    m = GPU_RE.search(text or "")
    return float(m.group(1)) if m else None

def owners_of(rows):
    """run id -> the paths its processes run in: the run's own folder (its child worktrees live under it) plus every
    worktree its record named. Paths, because a run is told apart by where it runs, not by what its processes are called."""
    out = {}
    for r in rows or []:
        paths = [r.get("dir") or ""] + list(r.get("wts") or [])
        out[r["id"]] = [p for p in dict.fromkeys(paths) if p]
    return out

def attribute(prev, cur, owners):
    """CPU seconds per run between two process snapshots (pid -> (ppid, cpu seconds, argv)).
    A process is credited to the owner whose worktree path its own argv names, and one that names none inherits its
    nearest ancestor's owner, so a whole tree (zsh -> claude -> dotnet test) counts, not just the process that carries
    the path. Attribution is by path and never by process name: every child of every run is the same `claude`, `dotnet`
    or `python`, so a name would credit whichever run started first. Seconds outside every run (the editor, the browser,
    a stray build) belong to nobody, and stay only in the strip's machine-wide figure."""
    hit = {}
    def owner_of(pid, depth=0):
        if pid in hit: return hit[pid]
        row = cur.get(pid)
        if row is None or depth > 64: return None
        cmd = row[2] or ""
        best = None
        for key, paths in owners.items():
            for p in paths:
                if p in cmd and (best is None or len(p) > len(best[1])): best = (key, p)
        found = best[0] if best else owner_of(row[0], depth + 1)
        hit[pid] = found
        return found
    out = {}
    for pid, (ppid, secs, cmd) in cur.items():
        p = prev.get(pid)
        d = secs - p[1] if p else 0.0
        if d <= 0: continue
        k = owner_of(pid)
        if k: out[k] = out.get(k, 0.0) + d
    return out

def share_pct(secs, window, ncpu):
    """One run's CPU seconds as a percentage of the whole machine, the unit the strip's CPU figure uses: a run holding one
    of eight cores holds 12.5% of the machine."""
    if not window or not ncpu: return 0.0
    return round(100.0 * secs / (window * ncpu), 1)

def attach_load(rows, shares, warn):
    """The card's marker: with the machine warned, every run whose process tree owns a real share of that CPU carries
    `cpu`, the percentage it owns. A run that owns nothing of it carries none, and the marker is dropped the moment the
    machine is calm again."""
    for r in rows:
        r.pop("cpu", None)
        if warn and shares.get(r.get("id"), 0.0) >= LOAD_MIN_SHARE_PCT:
            r["cpu"] = round(shares[r["id"]])
    return rows

_MACH = None
def _mach():
    """libSystem's host_statistics, resolved once: the kernel's own CPU and memory counters, read inside this process."""
    global _MACH
    if _MACH is None:
        try:
            import ctypes, ctypes.util
            lib = ctypes.CDLL(ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib")
            lib.mach_host_self.restype = ctypes.c_uint
            lib.host_statistics.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
            lib.host_page_size.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
            _MACH = (ctypes, lib)
        except Exception: _MACH = False
    return _MACH or None

_CPU_TICKS = None
def cpu_pct():
    """Machine CPU as a percentage of all cores, from the delta of the kernel's tick counters. The first read has no
    previous one to compare, so it answers None (`?` in the strip) instead of inventing a number."""
    global _CPU_TICKS
    cur = None
    m = _mach()
    if m:
        ctypes, lib = m
        class Load(ctypes.Structure): _fields_ = [("user", ctypes.c_uint32), ("system", ctypes.c_uint32), ("idle", ctypes.c_uint32), ("nice", ctypes.c_uint32)]
        i = Load(); n = ctypes.c_uint(4)
        if lib.host_statistics(lib.mach_host_self(), 3, ctypes.byref(i), ctypes.byref(n)) == 0:
            cur = (i.user + i.system + i.nice, i.user + i.system + i.nice + i.idle)
    if cur is None:
        try:
            import psutil
            t = psutil.cpu_times()
            cur = (t.user + t.system + getattr(t, "nice", 0.0), t.user + t.system + getattr(t, "nice", 0.0) + t.idle)
        except Exception: cur = None
    prev, _CPU_TICKS = _CPU_TICKS, cur
    if not cur or not prev: return None
    busy, tot = cur[0] - prev[0], cur[1] - prev[1]
    return 100.0 * busy / tot if tot > 0 else None

def ram_pct():
    """Used memory as a percentage of the machine's total, from the kernel's page counters (active + wired + compressed),
    else psutil. None where neither answers."""
    m = _mach()
    if m:
        ctypes, lib = m
        try:
            class VM(ctypes.Structure):
                _fields_ = ([(n, ctypes.c_uint32) for n in ("free", "active", "inactive", "wire")] +
                            [(n, ctypes.c_uint64) for n in ("zero_fill", "reactivations", "pageins", "pageouts", "faults", "cow_faults", "lookups", "hits", "purges", "purgeable", "speculative", "decompressions", "compressions", "swapins", "swapouts")] +
                            [(n, ctypes.c_uint32) for n in ("compressor", "throttled", "external", "internal")] + [("total_uncompressed", ctypes.c_uint64)])
            vm = VM(); n = ctypes.c_uint(ctypes.sizeof(VM) // 4)
            size = ctypes.c_uint(0)
            h = lib.mach_host_self()
            if lib.host_statistics64(h, 4, ctypes.byref(vm), ctypes.byref(n)) == 0 and lib.host_page_size(h, ctypes.byref(size)) == 0 and size.value:
                total = _sysctl_u64("hw.memsize")
                used = (vm.active + vm.wire + vm.compressor) * size.value
                if total: return min(100.0, 100.0 * used / total)
        except Exception: pass
    try:
        import psutil
        return psutil.virtual_memory().percent
    except Exception: return None

def _sysctl_u64(name):
    """One integer out of sysctl, in-process (ctypes), for hw.memsize."""
    m = _mach()
    if not m: return 0
    ctypes, lib = m
    try:
        lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        out = ctypes.c_uint64(0); size = ctypes.c_size_t(8)
        if lib.sysctlbyname(name.encode(), ctypes.byref(out), ctypes.byref(size), None, 0) != 0: return 0
        return int(out.value) if size.value == 8 else 0
    except Exception: return 0

def gpu_pct():
    """The accelerator's own utilisation counter, one unprivileged `ioreg` read. An ioreg that is missing, slow or has no
    accelerator to report answers None: the cell shows `?`."""
    try: out = subprocess.run(list(GPU_CMD), capture_output=True, text=True, timeout=10).stdout
    except Exception: return None
    return parse_gpu(out)

SOURCES = {"cpu": cpu_pct, "ram": ram_pct, "gpu": gpu_pct}
def sample_load(src=None):
    """One reading of the three cells. A source that raises, hangs or answers with something that is not a percentage is
    unavailable for that cell, and the page shows `?`: a bad source never prints a traceback and never fakes a number."""
    out = {}
    for k in ("cpu", "ram", "gpu"):
        fn = (src if src is not None else SOURCES).get(k)
        try: out[k] = _pct(fn()) if fn else None
        except Exception: out[k] = None
    return out

LOAD_STATE = {"cpu": [], "ram": None, "gpu": None, "asof": 0.0, "runs": {}}
LOAD_RUNS = []            # the light rows the last /api/runs served: id, folder and worktrees, for owners_of
_PROC_PREV, _PROC_AT = None, 0.0

def proc_snapshot():
    """pid -> (ppid, CPU seconds, argv) for the processes this user can see: psutil when it is importable (a read inside
    this process), else one `ps`. Only the sampler calls this, and only while the machine is warned."""
    try:
        import psutil
        out = {}
        for p in psutil.process_iter(["pid", "ppid", "cpu_times", "cmdline"]):
            i = p.info
            try: out[i["pid"]] = (i["ppid"], float(sum(i["cpu_times"][:3])), " ".join(i["cmdline"] or []))
            except Exception: continue
        if out: return out
    except Exception: pass
    out = {}
    for l in sh("/bin/ps", "-axwwo", "pid=,ppid=,time=,command=").splitlines():
        f = l.split(None, 3)
        if len(f) < 4 or not f[0].isdigit(): continue
        out[int(f[0])] = (int(f[1]) if f[1].isdigit() else 0, _ps_time(f[2]), f[3])
    return out

def _ps_time(t):
    """`ps -o time=` prints CPU time as [[dd-]hh:]mm:ss[.ss]; seconds, 0.0 when it cannot be read."""
    d, dash, rest = t.partition("-")
    if not dash: rest = d
    secs = 0.0
    try:
        for p in rest.split(":"): secs = secs * 60 + float(p)
        return secs + (int(d) * 86400 if dash else 0)
    except ValueError: return 0.0

def attribute_pass():
    """CPU per run over the last interval, as a share of the machine. The sampler calls it only while the machine is
    warned: a calm machine never builds a process table."""
    global _PROC_PREV, _PROC_AT
    cur = proc_snapshot()
    prev, _PROC_PREV = _PROC_PREV, cur
    now = time.time(); window = now - (_PROC_AT or now); _PROC_AT = now
    if not prev or window <= 0: return {}
    ncpu = os.cpu_count() or 1
    return {k: share_pct(v, window, ncpu) for k, v in attribute(prev, cur, owners_of(LOAD_RUNS)).items()}

def load_tick(sample=None):
    """One pass of the sampler: publish the reading, keep the history the warn rule looks back over, and — only while the
    machine is warned — work out which run owns that CPU."""
    s = sample if sample is not None else sample_load()
    LOAD_STATE["cpu"] = (LOAD_STATE["cpu"] + [s["cpu"]])[-LOAD_HISTORY:]
    LOAD_STATE["ram"], LOAD_STATE["gpu"], LOAD_STATE["asof"] = s["ram"], s["gpu"], time.time()
    LOAD_STATE["runs"] = attribute_pass() if sustained(LOAD_STATE["cpu"]) else {}
    return LOAD_STATE

# ---------------------------------------------------------------- the footer's facts (#1797)
# The line answers two questions: "is this machine saturated?" (the three load cells, first and never reordered) and "is
# the squad working, or merely busy?" (the facts appended after them, in this order). A fact earns a place on this line
# only if (a) it reads as one short token — a number, `ok` or `?`; (b) its bad value alone, seen from across the room,
# tells the operator the squad is stuck or lying while the rest of the page looks busy; and (c) the operator could act on
# it within a minute. Anything needing a second number to interpret, or already drawn within one screen of the footer,
# goes to a panel or a column header instead. Rejected here for exactly that reason, with where each lives instead:
#   per-run spend vs `spend_cap`              -> the run's own line in the left pane, where it already is
#   per-model spend, reviewer latency,        -> the cost & usage panel
#   who-is-reviewing-what
#   throughput (runs/hour, median, tks/s)     -> nowhere: `last progress` answers "is it moving?" without a rate that
#                                                changes too fast to read
#   per-column board counts                   -> the column headers directly above the footer
#   the age of the oldest card in a column    -> the column header (`Testing (3) · 2h`), one number per column
#   fork-ahead / branches waiting / last      -> a left-pane accordion; `fork-parked` already reaches the footer
#   base-test duration                           through `health`, and the three reserved here take the fourth accordion
#                                                when they arrive: the numbering here and the page's `ACC` are one list
# Two facts left the line by (b) and (c) together, and each is drawn one screen away (#1797, the owner's ruling 2026-10-16):
# the runs and the agents the line counted are the WORKERS header's own reading (`RUNS n/10 · AGENTS n/20 · waiting: …`,
# `group_header`) and that header carries more than the footer did. The worktree count left the page with the fourth
# accordion that drew it (the owner's ruling 2026-10-16): the count and its `clean now` press read poorly there, and the
# pass that press asked for by hand runs on every third cycle by itself. No other reading used the count or the pass, so
# `wt_plan`, `wt_count`, `wt_parts`, `wt_clean` and the `/api/wtclean` route left with them.
# One block is one token: `landed PR #73 2h ago`, `gate full pass 10m ago`. A block's inner ` · ` was the same glyph as the
# separator between blocks, so seven blocks read as twelve tokens with no grouping -- the whole line read as cramped. Inside a
# block the separator is now drawn by the page (a dimmed `.sep` span) and a fact that is two parts is two tokens the eye binds
# by one colour, never by a dot the eye cannot tell from the row's own. An age reads one rung (`2h`, `16h`, `3d`): a second rung
# is a precision no reader acts on, and the exact span of a fact is in its tooltip. The one fact that needs a second number to
# interpret -- the sha the gate ran at -- is on the line's block as a tooltip, which is how a fact is MOVED and never lost.
# One fact earned the line after that list was drawn, and it earns it by (b) alone (subscriptions, #1839): one token per
# subscription — `CL 42%`, the acronym and quota the provider's own CLI reports. Below the warn ladder, its reset span
# moves to the token tooltip (`resets in 3h 20m`); at or above the ladder it stays in the token. An unreadable reset stays
# visible as `?` at every consumption. SUBS fixes the order. A plan at 100% is exactly the reading that says the squad will
# looks busy, and it is the one the operator acts on within the minute — the plan, not the loop.
# The fact an operator waiting on the queue asks first earns the line the same way (#87): `landed PR #73 2h ago`,
# the newest merged pull request the board pass has read — its number is the landing and its merge time the age. Delivery
# is a pull request (#75), so the merge is the landing; it reads the rows the board pass already holds, so no log line is
# parsed and no GitHub call is made. The `auto/fork-<n>-<sha9>` marker is NOT read: a marker outlives the landing it named,
# and a bar that reports it names a landing nothing made.
# The quota pair (`codex ok · agy reset 14:20`) left the line for the opposite reason (#87): each marker names a CLI whose
# subscription the subs block already carries as a token, so the purple block repeated the green one. `MARKER_SUB` keeps
# the pairing, and a marker returns the day its subscription leaves SUBS: a fact is never lost, only never said twice.
# Every value comes from a source this hub already refreshes — no new polling and no new query. The subscription tokens
# cost none either: they read `quota_row()`, the cached probe the subscriptions panel already renders, inside the pass
# this sampler already makes. A value the hub cannot verify is `?`, never a stale or invented number, and a fact that
# goes unreadable takes its own `?` with it while the rest of the line stands. The line is one row: on a window too
# narrow for it, the trailing facts drop one whole block at a time — nothing wraps, and no number is ever cut into a different one.
FOOT_SEP = " · "
# Every block of the line reads in its own palette colour, so the facts are told apart at a glance without a character
# more on the row (#1843). The colours that are not a state signal are the hub's own variables — neighbours are what must
# never collide. The tone a value earns is NOT a block colour: `ok`, `warn` and `err` stay the load strip's alone, so a
# block colour can never say the value underneath it is fine. `wt` and `occ` left the line (#455); their colours left with
# them, and a block that is no longer drawn carries no palette entry to drift.
FOOT_TONES = ("ok", "warn", "err")   # the three tones the load strip already paints (#1816), reused and never a second ladder
FOOT_NEUTRAL = "neutral"             # a value the hub could not verify: the bar's own plain colour, never one of the tones
FOOT_BLOCK_COLORS = {"burn": "err", "landing": "wait", "gate": "none",
                     "quota": "none", "cycle": "wait", "subs": "run", "loop": "none"}  # subs teal: it shares with landing, which is not its neighbour; loop ends the line (#554)
# A quota marker names the CLI (`codex-limited`, `agy-limited`); the subs token names the same account by its plan.
MARKER_SUB = {"codex": "gpt", "agy": "gemini"}
FORK_MARKER = re.compile(r"fork-(\d+)-([0-9a-f]{7,40})")   # the close pass's marker, the name janitor.py's rule P reads

def limited_until(txt, now=None):
    """The epoch a quota marker names while it skips a model; None when there is no marker or its window has passed (the
    model is back); `?` when the marker is there but names no time the hub can read — a length is not a reset."""
    s = (txt or "").strip()
    if not s: return None
    try: t = float(s)
    except ValueError: return "?"
    return t if t > (time.time() if now is None else now) else None

def _rel_span(x):
    """A span in the page's own `rel()` shape: `3d 2h`, `2h 14m`, `5m` — the same three rungs, never a fourth. A countdown
    keeps them: the reader plans by the reset time, and the page's subscription panel draws the same span. An AGE does not
    (`_age`): three rungs on `16h 13m ago` is a precision nobody reads across the room."""
    d, h, m = int(x // 86400), int(x % 86400 // 3600), int(x % 3600 // 60)
    return f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")

def _age(x):
    """An age at one rung, the page's own `age()` rounded the same way, plus the day rung the row needs: `40s`, `12m`, `16h`,
    `3d`. The page rounds half up (`Math.round`), so the two ends of the same fact never disagree."""
    s = max(int(x), 0)
    return f"{s}s" if s < 90 else (f"{int(s / 60 + 0.5)}m" if s < 5400 else (f"{int(s / 3600 + 0.5)}h" if s < 172800 else f"{int(s / 86400 + 0.5)}d"))

def _landing_parts(ld, now):
    """`landed PR #73 2h ago`: one token — the newest merged pull request, named as a pull request because the row it comes
    from is one (a bare `#73` reads as an issue, and the issue is not what landed), and the age of its merge at one rung.
    No merge, or one whose number or time cannot be read, is `landed ?` in the neutral tone — never a landing that did not happen."""
    if not isinstance(ld, dict): return [{"text": "landed ?", "tone": FOOT_NEUTRAL}]
    n, ts = ld.get("issue"), ld.get("ts")
    if isinstance(n, bool) or not isinstance(n, int) or isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return [{"text": "landed ?", "tone": FOOT_NEUTRAL}]
    return [{"text": f"landed PR #{n} {_age(max(now - ts, 0.0))} ago", "tone": ""}]

def _gate_parts(g, now):
    """`gate fast pass 4m ago`: the latest gate the loop ran (fast for a tip or a wave entry, full for a promotion to stg),
    its result and its age. A pass reads in the ok tone and a fail in the err tone; a line the hub cannot read is `gate ?` in
    the neutral tone, never a pass. No reading at all draws no block.
    The sha it ran at is the block's tooltip: a sha is matched against a list, never read across the room, so it is a second
    number that has to be carried and not displayed — moved, never lost (the section's rule (b))."""
    if g is None: return []
    ok = isinstance(g, dict) and g.get("result") in ("pass", "fail") and g.get("kind") and g.get("sha") \
        and isinstance(g.get("ts"), (int, float)) and not isinstance(g.get("ts"), bool)
    if not ok: return [{"text": "gate ?", "tone": FOOT_NEUTRAL}]
    text = f"gate {g['kind']} {g['result']} {_age(max(now - g['ts'], 0.0))} ago"
    return [{"text": text, "tone": "ok" if g["result"] == "pass" else "err", "tip": f"ran at {str(g['sha'])[:7]}"}]

def _cycle_state(now):
    """The watch cycle's own beat, read off the `HH:MM:SS CYCLE <n>s` lines watch.sh writes to cycle.log: when the last cycle
    ended and how long the last five took. None when the log has no such line."""
    try: lines = (STATE / "cycle.log").read_text(errors="ignore").splitlines()[-4000:]
    except OSError: return None
    rows = [m.groups() for m in (re.match(r"(\d\d:\d\d:\d\d) CYCLE (\d+)s", l) for l in lines) if m]
    if not rows: return None
    day = time.strftime("%Y-%m-%d ", time.localtime(now))
    end = time.mktime(time.strptime(day + rows[-1][0], "%Y-%m-%d %H:%M:%S"))
    if end > now: end -= 86400   # the line is from before midnight
    return {"end": end, "secs": [int(r[1]) for r in rows[-5:]]}
def _span(s):
    s = int(abs(s)); h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s % 60:02d}s"
def _cycle_fact(c, now):
    """`cycle in 3m40s`: the countdown to the end the last cycles' median length predicts; `cycle +1m40s late` once past it
    (a cycle holds while childcc reviews, so its length is not fixed)."""
    if not c or not c.get("secs"): return ""
    left = c["end"] + sorted(c["secs"])[len(c["secs"]) // 2] - now
    return f"cycle in {_span(left)}" if left >= 0 else f"cycle +{_span(left)} late"
def _quota_fact(q, now, subs=None):
    """`codex ok · agy reset 14:20`: one name per account-wide marker the review chain skips models over, each with the
    reset time it names. A limited model is what the operator acts on; one that is back reads `ok`. A marker whose
    subscription the subs block carries (`MARKER_SUB`, against SUBS) is not named twice (#87): with both carried, the
    block is empty and `foot_items` drops it."""
    out = []
    subs = SUBS if subs is None else subs
    for name in ("codex", "agy"):
        if MARKER_SUB.get(name) in subs: continue
        v = (q or {}).get(name)
        if v == "?": out.append(f"{name} ?")
        elif isinstance(v, (int, float)): out.append(name + " reset " + time.strftime("%H:%M", time.localtime(v)))
        else: out.append(name + " ok")
    return FOOT_SEP.join(out)

def _subs_pct(p):
    """A quota percentage as the cost panel paints it (`fpct`): one decimal under 10, whole numbers from there up."""
    return f"{p:.1f}%" if 0 < p < 10 else f"{int(p + 0.5)}%"

def _subs_reset(resets, now):
    """The time to a reset in the page's own `rel()` shape (`3h 20m`), without its `resets in` lead — the token already
    says what the number is. A time the hub cannot read is `?`; one already past reads `now`, as the page reads it."""
    if isinstance(resets, bool) or not isinstance(resets, (int, float)): return "?"
    x = resets - now
    if x <= 0: return "now"
    return _rel_span(x)

def _subs_span(x):
    """The footer's time to a reset, in one compact rung with one decimal: `5.5d` from a day up, `3.3h` under it, `40m`
    under an hour. The owner reads it on every token, green ones too; the tooltip keeps the exact two-rung span."""
    if x >= 86400: return f"{x / 86400:.1f}d"
    if x >= 3600: return f"{x / 3600:.1f}h"
    return f"{int(x // 60)}m"

def consumption_tone(pct):
    """The tone one consumption reading earns (#1843), on the ladder the load strip already draws (#1816): green under
    the warn threshold, amber at or above it, red at or above the ceiling. A reading the hub cannot verify — a string, a
    negative, a NaN — earns no tone at all: it reads in the bar's own neutral colour, never as if it carried a number."""
    f = _pct(pct)
    if f is None: return FOOT_NEUTRAL
    return "err" if f >= FOOT_ERR_PCT else ("warn" if f >= FOOT_WARN_PCT else "ok")

def _subs_parts(subs, now):
    """One part per subscription in SUBS' own order. Every token reads its reset in one compact rung, `CL 42% 3.3h`
    (`_subs_span`), whatever its tone; the exact span is `resets in 3h 20m` in its tooltip. The tooltip also carries the
    reading's age (`read 5h ago`), the age the panel shows. An unreadable reset is `?` and never hides. The quota and reset are `quota_row()`'s first window, the probe the cost panel already shows. A probe
    that did not answer (or names no window) falls back to the percentage the owner typed by hand and reads `?` for the
    reset; a subscription with neither reads `?` alone — neutral, never painted as if it carried a number."""
    out = []
    for k in SUBS:
        a, s = SUB_ACR.get(k) or k.upper(), (subs or {}).get(k) or {}
        w = ((s.get("probe") or {}).get("windows") or [None])[0]
        pct = w.get("pct") if isinstance(w, dict) else None
        hand = s.get("quota")
        if isinstance(pct, (int, float)) and not isinstance(pct, bool):
            r = w.get("resets")
            reset = _subs_reset(r, now)
            short = reset if reset in ("?", "now") else _subs_span(r - now)
            part = {"text": f"{a} {_subs_pct(pct)} {short}", "tone": consumption_tone(pct)}
            tip = [f"resets in {reset}"] if reset not in ("?", "now") else []
            asof = (s.get("probe") or {}).get("asof")   # the age the panel shows beside the same reading
            if isinstance(asof, (int, float)) and not isinstance(asof, bool): tip.append(f"read {_age(now - asof)} ago")
            if tip: part["tip"] = " · ".join(tip)
            out.append(part)
        elif isinstance(hand, (int, float)) and not isinstance(hand, bool):
            out.append({"text": f"{a} {_subs_pct(hand)} ?", "tone": consumption_tone(hand)})
        else: out.append({"text": f"{a} ?", "tone": FOOT_NEUTRAL})
    return out

def _subs_fact(subs, now):
    """The subs block as one string: its parts in the order they are read, which is the text the line carries."""
    return FOOT_SEP.join(p["text"] for p in _subs_parts(subs, now))

def _fact(key, text, bad=False, parts=None, tip=None):
    """One block of the line (#1843): its text, its own palette colour, and the parts it paints. A block with no parts
    of its own is the single part its text already is, with no tone — so the block's colour stands for the whole value.
    A part whose own reading earns a tone keeps it on top of that colour, which is how the block still says WHICH fact
    it is while the value says whether it is the bad one.
    `tip` is what the block does not say out loud but must not lose: a second number (the sha a gate ran at), read on
    hover. It is how a fact is MOVED off a reading bar and never deleted from it. The line is a reading and carries no
    press: the one control this bar ever held was the worktree count's `clean now`, and it left the page with the
    accordion that drew it (the owner's ruling 2026-10-16)."""
    return {"key": key, "text": text, "bad": bad, "color": FOOT_BLOCK_COLORS[key], "tip": tip,
            "parts": parts if parts is not None else [{"text": text, "tone": ""}]}


# --- who is eating the tokens. `tokens.py burners` holds every rule (the window, the threshold, `over`, the order); the
# hub runs it, caches the answer, and draws the burners it names. No script, a nonzero exit or unreadable output is no data.
TOKENS_PY = pathlib.Path(__file__).resolve().parent / "tokens.py"
BURN_HOURS = 24
BURN_TTL = 120.0      # the sampler ticks every few seconds; the burners change by the minute
BURN_MAX_CHARS = 72   # the block's own cap: the page drops whole blocks when the line is too wide, so one over-long
                      # block would take the whole line with it (#446)
_BURN = {"t": 0.0, "v": None}

def burn_read(script=None, state=None, checkout=None, hours=BURN_HOURS):
    """The parsed `tokens.py burners --json` reading, or None when there is no data."""
    script = pathlib.Path(script or TOKENS_PY)
    if not script.is_file(): return None
    try:
        r = subprocess.run([sys.executable, str(script), "burners", str(state or STATE), "--checkout", str(checkout or CHECKOUT),
                            "--hours", str(hours), "--json"], capture_output=True, text=True, timeout=20)
        if r.returncode: return None
        d = json.loads(r.stdout)
    except Exception: return None
    return d if isinstance(d, dict) else None

def burn_cached(now=None):
    now = time.time() if now is None else now
    if now - _BURN["t"] >= BURN_TTL: _BURN.update(t=now, v=burn_read())
    return _BURN["v"]

def _tok(n):
    n = int(n)
    return f"{n/1e6:.1f}M".replace(".0M", "M") if n >= 1e6 else f"{n/1e3:.0f}k" if n >= 1e3 else str(n)

def _burn_row(r):
    """One burner as the line reads it: its consumer's name, shortened to the two ends that tell two burners apart, then
    its tokens. A run's consumer is `run:<run id>:<node>` and the run id is `run-YYYYMMDD-HHMMSS-xxxx`, so the id's own
    tail and the node are what a reader needs: `run:run-20261015-191451-9f6b30:route.router.1` reads `…9f6b30:route.router.1`.
    Any other name is kept whole."""
    parts = str(r["consumer"]).split(":")
    if len(parts) > 2 and parts[1].startswith("run-"): parts = ["…" + parts[1].rsplit("-", 1)[-1], *parts[2:]]
    return ":".join(parts) + " " + _tok(r["tokens"])

BURN_SHOWN = 3        # how many consumers the block's tooltip names: the worst, so the hover stays one readable line

def _burn_rows(b):
    """The consumers the script named, in the script's own order: the only rows the block and its tooltip draw from.
    `over` decides, never the totals: the script is the one that compares."""
    if not isinstance(b, dict) or b.get("over") is not True: return []
    return [x for x in (b.get("burners") or []) if isinstance(x, dict) and x.get("consumer") and isinstance(x.get("tokens"), (int, float))]

def _burn_window(b):
    """`(total tokens, hours, threshold or None)` from one reading: the two numbers the block states and the one it names."""
    rows = _burn_rows(b)
    hours = b.get("window_hours") if isinstance(b, dict) and isinstance(b.get("window_hours"), (int, float)) else BURN_HOURS
    thr = b.get("threshold") if isinstance(b, dict) else None
    return sum(x["tokens"] for x in rows), hours, (thr if isinstance(thr, (int, float)) else None)

def _burn_fact(b):
    """`tokens 62.9M/24h · 79 over 5M` when the script says `over`; empty otherwise. The window, the comparison and the
    order are the script's, never this line's.
    Two numbers and no ids: what the window cost, and how many calls crossed the threshold. What the block named before --
    `…9f6b30:route.router.1 16.9M +78` -- was a machine id nobody reads across the room and a third number the reader had
    to hold while reading it; the owner read the block as soup (2026-10-16). The names are `_burn_tip`'s now, so the fact
    is moved to the hover and never lost.
    A reading the hub cannot compare against a threshold still states both numbers it has, and names the cap as the word it
    can: the block's own colour already says the reading is high. The text is cut at BURN_MAX_CHARS -- a block that never
    ends would empty the line it is the first fact of (#446)."""
    rows = _burn_rows(b)
    if not rows: return ""
    total, hours, thr = _burn_window(b)
    over = f"over {_tok(thr)}" if thr is not None else "over the cap"
    floor = " · floor" if b.get("floor") else ""   # no count came from some calls: the number is a floor, not a measure
    return f"tokens {_tok(total)}/{hours}h · {len(rows)} {over}{floor}"[:BURN_MAX_CHARS]

def _burn_tip(b):
    """What the block no longer says out loud, on hover: how many calls crossed the threshold in the window, the worst
    BURN_SHOWN of them by the script's own name for each, and the count of the rest. None when the block draws nothing."""
    rows = _burn_rows(b)
    if not rows: return None
    _, hours, thr = _burn_window(b)
    lead = f"{len(rows)} calls, each over {_tok(thr)}" if thr is not None else f"{len(rows)} calls, each over the cap"
    names, rest = [_burn_row(x) for x in rows[:BURN_SHOWN]], len(rows) - BURN_SHOWN
    return lead + f" in {hours}h · " + ", ".join(names) + (f" +{rest} more" if rest > 0 else "") \
        + (" · floor, not a measure: some calls carried no token count" if b.get("floor") else "")

def foot_items(src, now=None):
    """The facts, in the order the line reads them, each carrying its palette colour and the parts it paints: a
    subscription token carries the tone its own consumption earns, so the block is never painted as one value (#1843).
    The loop's own health and its last progress were dropped from the line by the owner: the line is a machine reading,
    and both said as much about the loop's mood as about the machine. The worktree count and the occupancy pair left it the
    same way, and each is drawn one screen away — the WORKERS header and the left pane's fourth accordion (#1797, #455):
    `foot_sources` still reads the worktree count for that accordion, and answers this line with what is left."""
    now = time.time() if now is None else now
    src = src or {}
    land = _landing_parts(src.get("landing"), now)
    gate = _gate_parts(src.get("gate"), now)
    items = [_fact("burn", _burn_fact(src.get("burn")), bad=True, tip=_burn_tip(src.get("burn"))),
             _fact("landing", FOOT_SEP.join(p["text"] for p in land), parts=land),
             _fact("gate", FOOT_SEP.join(p["text"] for p in gate), bad=any(p["tone"] == "err" for p in gate),
                   parts=gate or None, tip=(gate[0].get("tip") if gate else None)),
             _fact("quota", _quota_fact(src.get("quota"), now)),
             _fact("cycle", _cycle_fact(src.get("cycle"), now)),
             _fact("subs", _subs_fact(src.get("subs"), now), parts=_subs_parts(src.get("subs"), now))]
    if "loop" in src:   # the loop state ends the line (the owner's ask, #554): one word read across the room, so it survives a narrow window
        on = bool(src["loop"])
        items.append(_fact("loop", "Loop:ON" if on else "Loop:OFF", bad=not on, parts=[{"text": "Loop:ON" if on else "Loop:OFF", "tone": "ok" if on else "err"}]))
    return [i for i in items if i["text"]]   # a block with nothing to say draws no lone `·`

def foot_line(items):
    """The facts as the line reads them, one `·` between each: the page appends this after the three load cells."""
    return FOOT_SEP.join(i["text"] for i in (items or []))

# The sources, each one the loop already refreshes: `health.json` plus the `alerts.log` line that armed each cause, the
# loop's own markers and `cycle.log` (a verdict, a land, a dispatch, a board move), the caps graph, the pair `health.sh`
# writes, and the markers the review chain skips models over. Reading them is a few small files in the sampler thread
# that already runs: no source here costs a query, a `ps` or a GitHub call.

def _read(p):
    try: return pathlib.Path(p).read_text(errors="ignore")
    except OSError: return ""

def _json(p):
    try: return json.loads(pathlib.Path(p).read_text())
    except Exception: return None

def _last_landing():
    """The newest landed branch the board pass has read: `{"issue", "ts"}`, naming the issue it carries, or None.

    Delivery is a work branch landed in the landing branch (#358), and the board pass already holds the landing ledger of
    every configured repository with its landing time. A row the ledger does not mark as landed is not a landing, and a
    row whose time the hub cannot read is skipped rather than counted as now.
    """
    best = None
    for p in board_cache.get("landed") or []:
        if not p.get("m"): continue
        try: ts = dt.datetime.fromisoformat(str(p.get("c") or "").replace("Z", "+00:00")).timestamp()
        except ValueError: continue
        if best is None or ts > best["ts"]: best = {"issue": p.get("n"), "ts": ts}
    return best

def _gate_status():
    """The one line the gate wrapper leaves in $SG_STATE/gate-status, `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`: the latest
    gate, its result and the sha it ran at. None when the file is absent or is not that line."""
    f = _read(STATE / "gate-status").rstrip("\n").split("\t")
    if len(f) != 4 or not f[0].isdigit() or not all(f[1:]): return None
    return {"kind": f[1], "result": f[2], "sha": f[3], "ts": float(f[0])}

def _newest_mtime(*paths):
    ts = []
    for p in paths:
        try: ts.append(pathlib.Path(p).stat().st_mtime)
        except OSError: pass
    return max(ts) if ts else None

def _loop_on(pid_file=None):
    """True while the supervisor's pid file (watchd.sh writes $SG_STATE/watchd.pid) names a live process: a signal 0, never a `ps`."""
    try: pid = int(_read(pid_file or STATE / "watchd.pid").split()[0])
    except (ValueError, IndexError): return False
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True   # alive, owned by another user
    except OSError: return False
    return True

def foot_sources(now=None):
    """One reading of every source the line draws on. A source that is not there reads as `None` for its own fact only,
    so one unreadable file costs one `?` and never the rest of the line."""
    now = time.time() if now is None else now
    out = {"landing": _last_landing(), "gate": _gate_status(),
           "quota": {"codex": limited_until(_read(SHARED / "codex-limited"), now),
                     "agy": limited_until(_read(SHARED / "agy-limited"), now)}}
    plans = plans_get()   # the hand-entered percentage, for a probe that did not answer: the two things #1839's tokens read
    out["burn"] = burn_cached(now)
    out["cycle"] = _cycle_state(now)
    out["loop"] = _loop_on()
    out["subs"] = {k: {"probe": quota_row(k, now), "quota": (plans.get(k) or {}).get("quota")} for k in SUBS}
    return out

def foot_refresh(now=None):
    """One pass of the footer's sources, kept where the page serves it from: the sampler thread calls this, so the line
    can never be written from a figure the loop has stopped refreshing. A failed pass keeps the last good facts."""
    src = foot_sources(now)
    LOAD_STATE["facts"] = foot_items(src, now)
    return LOAD_STATE["facts"]

def load_payload():
    """What /api/load serves: the cached reading, never a fresh one, so three open tabs cost three JSON bodies and no
    sample. `cpu` is the newest reading, `warn` is the two-sample rule over the history, `runs` the shares the cards use,
    and `facts` the footer's own facts as the sampler last read them. The three numbers are the readings themselves, unrounded
    (#1816): the page builds the cells from them, so the text rounds for display while the ceiling rule judges the reading."""
    hist = LOAD_STATE.get("cpu") or []
    cpu = hist[-1] if hist else None
    return {"line": load_line(cpu, LOAD_STATE.get("ram"), LOAD_STATE.get("gpu")),
            "cpu": cpu,
            "ram": LOAD_STATE.get("ram"), "gpu": LOAD_STATE.get("gpu"),
            "warn": sustained(hist), "runs": dict(LOAD_STATE.get("runs") or {}),
            "facts": list(LOAD_STATE.get("facts") or []),
            "secs": LOAD_SAMPLE_SECS, "asof": LOAD_STATE.get("asof", 0.0)}

def load_sampler():
    """The one sampler thread: a failed pass leaves the last reading in place rather than breaking the page."""
    while True:
        try: load_tick()
        except Exception: pass
        try: foot_refresh()
        except Exception: pass
        time.sleep(LOAD_SAMPLE_SECS)

# A coordinator test pass writes `running` at its start and its terminal state at the end; killed in between, the file says
# `running` forever. Four hours without a touch is longer than any pass this coordinator runs, so an untouched file is not a
# running pass: the hub shows it as stalled with this note, past the RUNNING group, instead of counting a live run with no
# process behind it. The runner's file is never
# rewritten and the loop's own readers keep reading it as it is -- this is the hub's display bound, not a claim the test failed.
TEST_LOST_SECS = 4 * 3600
LOST_NOTE = "lost: no state written for over 4 h"
# A lost pass stays visible -- it is the one reading that says a pass died and nobody retried it -- but not forever: past a day
# the ghost of a pass nobody will retry outlives its message, and the left pane fills with entries that are older than the work
# around them. A day is past any pass a reader would still act on, and it is the same drop the finished ones get, so the list
# converges on what is current instead of growing by one row per killed pass.
TEST_LOST_DROP_SECS = 24 * 3600

# ---------------------------------------------------------------- the childcc pass (#598)
# The pass writes auto/childcc-now.json (childcc.py): its pid and start, and one entry per child it holds, with the step it is
# at (review, enter, deferred) and since when it waits. The hub reads that file and nothing else of the pass, and shows it as
# one row of the WORKERS group: the pid and age, one line per child, and the deferred count with the oldest wait.
def _pid_alive(pid):
    try: os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError, OverflowError): return False
    except PermissionError: return True
    return True

def _childcc_line(c):
    who = f"{c.get('run')} {c.get('child')} #{c.get('issue')}"
    if c.get("step") == "review":
        model = _read(STATE / f"review-{c['rk']}.model").strip() if c.get("rk") else ""
        return f"{who} · in review" + (f" with {model}" if model else "")
    return f"{who} · " + ("entering wave" if c.get("step") == "enter" else "deferred")

def childcc_row(now=None):
    """The one WORKERS row of the childcc pass, or None when no pass is live and no child waits. With no live pass every
    child the last pass left still waits, whatever step it was at."""
    now = time.time() if now is None else now
    d = _json(STATE / "auto" / "childcc-now.json")
    if not isinstance(d, dict): return None
    kids = [c for c in d.get("children") or [] if isinstance(c, dict)]
    live = bool(d.get("pid")) and _pid_alive(d["pid"])
    waits = [c for c in kids if c.get("step") == "deferred" or not live]
    if not live and not waits: return None
    oldest = int(max(now - float(c.get("since") or now) for c in waits)) if waits else 0
    sub = f"CHILDCC DEFERRED {len(waits)} · oldest wait {_age(oldest)}" if waits else ""
    age = int(now - float(d.get("t0") or now)) if live else oldest
    return {"id": "childcc", "port": "", "kind": "childcc", "live": live, "rank": 0, "state": "running" if live else "waiting",
            "badge": "childcc" if live else "waiting", "tip": "the childcc pass: one review and one wave entry at a time",
            "label": f"childcc pass · pid {d['pid']}" if live else f"CHILDCC DEFERRED {len(waits)}", "issues": [], "pid": d["pid"] if live else None,
            "age": age, "dur": round(age / 60, 1), "spend": 0, "agents": 0, "project": "childcc", "sub": sub,
            "lines": [_childcc_line(c) for c in kids] if live else [], "deferred": len(waits), "oldest_wait": oldest}

def runs():
    _titles()
    out = []; load_rows = []
    ps_lines = sh("/bin/ps", "-axwwo", "pid=,command=").splitlines()
    for l in ps_lines:
        m = re.match(r"\s*(\d+)\s+\S*python\S*\s+-m\s+squad\.dashboard_server\s+(\S+)", l)
        if not m: continue
        pid, d = m.group(1), m.group(2)
        port = re.search(r"TCP 127\.0\.0\.1:(\d+)|n127\.0\.0\.1:(\d+)", sh(LSOF, "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN", "-Fn"))
        if not port: continue
        p = pathlib.Path(d) / "record.jsonl"
        if not pathlib.Path(d).exists(): continue
        if re.search(r"/pytest-\d+/|/pytest-of-", d): continue   # dashboards started by agent-squad's own test suite are fixtures, not runs   # a dashboard server outliving its run folder (worktree removed) must not break the list
        r = scan(p)
        if r is None:
            b = badge_of("starting")
            out.append({"id": os.path.basename(d), "port": port.group(1) or port.group(2), "state": "starting", "badge": b["text"], "tip": b["title"], "label": "run " + os.path.basename(d)[-6:], "issues": [], "age": 0, "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": 0, "spend": 0, "dur": 0, "kind": "code", "live": True, "agents": 0}); continue
        age = time.time() - p.stat().st_mtime; st = state_of(r, age)
        try: request = (pathlib.Path(d) / "request.txt").read_text(errors="replace")
        except OSError: request = None
        gone = run_gone(r, age, ps_lines, d, request)   # killed: no verdict will come, so it is not live whatever its age
        st = demoted(st, r, {i["n"] for i in board_cache.get("items", []) if i.get("s") == "closed"})
        merged = {p["n"] for p in board_cache.get("landed", []) if p.get("m")}
        st = handled_of(st, {**r, "merged": r.get("fix") in merged or any(int(n) in merged for n in r["issues"])})
        b = badge_of(st, r)
        if gone: b = gone_badge(b)
        rank = run_rank(st, live=not gone)
        t1 = r["t1"] or time.time()
        out.append({"id": os.path.basename(d), "port": port.group(1) or port.group(2), "state": st, "badge": b["text"], "tip": b["title"], "label": r.get("label"), "issues": r["issues"], "issue_src": r.get("issue_src"), "issue_why": r.get("issue_why"), "age": int(age), "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": rank, "spend": r["spend"], "dur": round((t1 - (r["t0"] or t1)) / 60, 1), "kind": r.get("kind", "code"), "fix": r.get("fix"), "stack": r.get("stack"), "bug": r.get("bug"), "merge": r.get("merge"), "titles": {i: TITLE.get(int(i), "") for i in r["issues"]}, "live": not gone, "agents": 0 if gone else r.get("agents") or 0, "share": r.get("share")})
        load_rows.append({"id": os.path.basename(d), "dir": d, "wts": r.get("wts") or []})   # what the load attribution needs, and nothing else
    for f in sorted((STATE / "tests").glob("test-*.json")):   # coordinator test passes (test.sh)
        try: d = json.loads(f.read_text())
        except Exception: continue
        age = time.time() - f.stat().st_mtime
        raw = d.get("state") or "running"
        lost = raw == "running" and age > TEST_LOST_SECS   # the pass died before it could write its terminal state (#1783)
        if lost and age > TEST_LOST_DROP_SECS: continue    # a ghost nobody will retry stops being listed at all
        if raw != "running" and age > 4 * 3600: continue
        rec = {"head": d.get("head"), "merged": d.get("merged"), "exit_code": d.get("rc")}
        st = handled_of("stalled" if lost else raw, rec); t1 = d.get("t1") or time.time(); b = badge_of(st, rec)
        tip = LOST_NOTE if lost else b["title"]
        if lost: b = {**b, "text": b["text"] + " · lost"}   # the badge carries the note too: the state alone reads as a live stall
        out.append({"id": f.stem, "port": "", "url": "/test/" + f.stem, "state": st, "badge": b["text"], "tip": tip, "label": "test #" + str(d.get("issue")), "issues": [str(d.get("issue"))], "age": int(age), "project": "test", "rank": run_rank(st, live=not lost), "spend": 0, "dur": round((t1 - d.get("t0", t1)) / 60, 1), "kind": "test", "titles": {str(d.get("issue")): TITLE.get(int(d.get("issue") or 0), "")}, "live": not lost and st in LIVE_STATES, "agents": 0})
    cc = childcc_row()
    if cc: out.append(cc)   # the pass is a worker beside the runs, never one of them: working() and the header count skip its kind
    LOAD_RUNS[:] = load_rows   # the attribution works off the runs the hub just showed, so it never needs a query of its own
    attach_load(out, LOAD_STATE.get("runs") or {}, sustained(LOAD_STATE.get("cpu") or []))
    out.sort(key=lambda r: (r["rank"], r["age"]))   # live runs first, finished next; then most recently updated first
    return out

RUN_WATCH_SECS = 0.2
RUN_POLL_SECS = 3.0
RUN_LIST = []
RUN_LIST_AT = 0.0
RUN_LIST_LOCK = threading.Lock()

def run_watch_state(dirs=None):
    """The record files a watch pass sees: their nanosecond mtimes and sizes, so a new run and its verdict both differ."""
    out = {}
    for d in project_dirs() if dirs is None else dirs:
        try: records = pathlib.Path(d).glob("run-*/record.jsonl")
        except OSError: continue
        for p in records:
            try:
                s = p.stat(); out[str(p)] = (s.st_mtime_ns, s.st_size)
            except OSError: pass
    return out

def refresh_runs():
    """Refresh the displayed run list after a record change, or the fixed fallback poll."""
    global RUN_LIST_AT
    rows = runs()
    with RUN_LIST_LOCK:
        RUN_LIST[:] = rows
        RUN_LIST_AT = time.monotonic()
    return rows

def run_watch_tick(before, dirs=None):
    """Refresh when a watched record appeared, changed or left the runs directory."""
    after = run_watch_state(dirs)
    if after != before: refresh_runs()
    return after

def run_list():
    """The displayed list, with its fixed poll as a fallback for a missed watch event."""
    if time.monotonic() - RUN_LIST_AT >= RUN_POLL_SECS: refresh_runs()
    with RUN_LIST_LOCK: return list(RUN_LIST)

def watch_runs(stop=None):
    """Watch the known runs directories without a platform-specific dependency; the API poll remains the fallback.
    The project directories are read on each tick, so a project added later is watched. `stop` (an Event) ends the loop: the test seam."""
    state = run_watch_state()
    while not (stop is not None and stop.is_set()):
        time.sleep(RUN_WATCH_SECS)
        try: state = run_watch_tick(state)
        except Exception: pass

def working(rows):
    """The ids of the runs that work: a run folder with no verdict and a dashboard that answers, as the list above groups them. Test passes are not runs."""
    return [r["id"] for r in rows if r.get("kind") not in ("test", "childcc") and group_of(r) == "running"]

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
    models = sorted(({**x, "spend": round(x["spend"], 2), "box": model_box(x["model"])} for x in mt.values()), key=lambda x: -(x["in"] + x["out"]))
    return {"buckets": rows, "totals": {k: round(v, 2) for k, v in tot.items()}, "models": models}

BOARD = pathlib.Path(os.environ.get("SG_BOARD_CACHE") or HUBDIR / "hub-board.json"); BOARD.parent.mkdir(parents=True, exist_ok=True)
try: board_cache = json.loads(BOARD.read_text())
except Exception: board_cache = {"t": 0, "items": [], "prs": [], "err": None}
# A cache written before the board read more than one repository (#1820) has rows with no repository on them: they are
# the profile repository's, which is the only one that pass read.
for _k in ("prs", "landed"):
    for _x in board_cache.get(_k) or []: _x.setdefault("r", REPO)
board_cache.setdefault("errs", [])
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
def apply_holders(items):
    """Name on each card the run that holds it, from the seam's one holder read (#241): `h` is {"run", "started"}, and a
    card no run holds carries none. A holder read that fails keeps the holder the last good pass read, never a blank."""
    try: held = board_seam().holders()
    except Exception:
        last = {i["n"]: i["h"] for i in board_cache.get("items") or [] if i.get("h")}
        held = {str(n): h for n, h in last.items()}
    for i in items:
        h = held.get(str(i["n"]))
        if h: i["h"] = h
        else: i.pop("h", None)
def holder_text(i):
    """The text a held card wears: `held by <run> since <start> UTC`, or `held by <run>` when no start was named."""
    h = i.get("h") or {}
    if not h.get("run"): return None
    t = str(h.get("started") or "")
    try: t = dt.datetime.fromtimestamp(float(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")   # an epoch start
    except (ValueError, OverflowError, OSError): t = t[:16].replace("T", " ")   # an ISO start
    return f"held by {h['run']}" + (f" since {t} UTC" if t else "")
def task_count(i):
    """How far a card's own work got, `completed` over `total` (`3/5`), off the sub-issue summary the items route the
    pass already reads hands over (#153). Nothing when the issue has no tasks, and nothing when the summary is missing
    or carries no pair of numbers: a card never wears an invented `0/0`. The rule lives here, not in the page: the card
    renders the value its row carries (ADR-0137)."""
    s = i.get("si") or {}
    t, c = s.get("total"), s.get("completed")
    if isinstance(t, bool) or isinstance(c, bool) or not isinstance(t, int) or not isinstance(c, int): return None
    return f"{c}/{t}" if t > 0 else None
def board_pass(fetch_open=None, fetch_landed=None, repos=None):
    """One board re-read: Board 4 statuses (REST) + one GraphQL page of Issue.blockedBy + the open work branches and the
    landing ledger of every repository the hub is told to read (#1820). The 60 s loop runs exactly this.

    The two reads are independent: the issues are the profile repository's board, the branch lane spans every configured
    repository. A read that fails keeps the last good rows it already had and says so (the pass's error, or the
    repository's own error on the lane), so a failed pass never writes a fresh empty board (#1772, #1820)."""
    items = lc = cases = None
    read_started = time.time()   # when the list read begins: the only stamp the overlay may compare a proved move against
    try:
            out = board_seam().rows(
                '.[]|select(.content_type=="Issue" or .content_type=="DraftIssue")|{n:.content.number,i:.id,t:.content.title,s:.content.state,c:.content.closed_at,st:((.fields[]|select(.name=="Status")|.value.name.raw)//null),k:(((.fields[]?|select(.name=="Kind")|.value.name|if type=="object" then .raw else . end)//"")|ascii_downcase|if .=="" then null else . end),l:[.content.labels[]?.name],si:{total:.content.sub_issues_summary.total,completed:.content.sub_issues_summary.completed}}',
                kind=True, timeout=120)
            items = [json.loads(l) for l in out.splitlines() if l.strip()]
            cases = [i["t"] for i in items if i.get("n") is None and i.get("k") == "test case"]   # a test case is a draft item: it has no number, so it is a row and never a card (#306)
            items = [i for i in items if i.get("n") is not None]
            oi = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO}/issues?state=open&per_page=100", "--jq",
                '.[]|select(.pull_request==null)|{n:.number,t:.title,s:.state,c:null,st:null,l:[.labels[].name],si:{total:.sub_issues_summary.total,completed:.sub_issues_summary.completed}}'], capture_output=True, text=True, timeout=120).stdout
            have = {i["n"] for i in items}   # open issues that never reached the board have no state either
            items += [i for i in (json.loads(l) for l in oi.splitlines() if l.strip()) if i["n"] not in have]
            own, name = REPO.split("/", 1)   # one GraphQL page of open issues; not one REST call per card
            gq = 'query{repository(owner:"%s",name:"%s"){issues(first:100,states:OPEN){nodes{number blockedBy(first:20){nodes{number state}}}}}}' % (own, name)
            go = subprocess.run(["gh", "api", "graphql", "-f", "query=" + gq], capture_output=True, text=True, timeout=60).stdout
            try: apply_open_blockers(items, ((json.loads(go) or {}).get("data") or {}).get("repository", {}).get("issues", {}).get("nodes") or [])
            except Exception: pass
            lo = subprocess.run(["gh", "api", "--paginate", f"repos/{REPO}/labels?per_page=100", "--jq", '.[]|{n:.name,c:.color}'], capture_output=True, text=True, timeout=60).stdout
            lc = {x["n"]: x["c"].lower() for x in (json.loads(l) for l in lo.splitlines() if l.strip())}
    except Exception as e: board_cache["err"] = str(e)[:80]   # a failed pass keeps the last good rows on the board (#1810)
    if items: apply_holders(items)
    if items: board_cache.update(t=time.time(), rt=int(read_started), items=items, cases=cases or [], lc=lc or board_cache.get("lc", {}), err=None)
    rs = REPOS if repos is None else repos
    try:
            prs, landed, errs = prs_pass(fetch_open, fetch_landed, rs)
            board_cache.update(prs=prs, landed=landed, errs=errs)
            # a pass no repository answered is not a refresh: `t` keeps saying when the board last really moved, which
            # is the age the header shows (#1810), instead of claiming a fresh board nothing came back for.
            if len(errs) < len(rs): board_cache["t"] = time.time()
    except Exception as e: board_cache["err"] = str(e)[:80]
    save_board()
    return board_cache
def save_board():
    """The cache file writes the rows the hub holds. The temp name is the writer's own (#512): hubmux runs one hub per
    profile in one process, so the pid alone is shared."""
    _tmp = BOARD.with_name(f"{BOARD.name}.{os.getpid()}.{threading.get_ident()}.tmp"); _tmp.write_text(json.dumps(board_cache)); os.replace(_tmp, BOARD)
def refresh_board():
    """board_pass() every 60 s, or within 2 s of a board move made through scripts/board.sh (board-dirty flag).
    The items route lags a write by seconds, so a pass a move asked for is read again 16 s later: the card never waits a minute in its old column."""
    quick = False
    while True:
        try: board_pass()
        except Exception as e: board_cache["err"] = str(e)[:80]   # one failed pass never ends the loop (#512): the next one still runs
        for _ in range(8 if quick else 30):
            time.sleep(2)
            try:
                if (STATE / "board-dirty").stat().st_mtime > board_cache.get("t", 0): quick = True; break
            except OSError: pass
        else: quick = False
ORDER = [column["name"] for column in BOARD_COLUMNS]
def _marker_mtime(kind, pr, sha9):
    """mtime of a loop marker in auto/ (`launched-<pr>-<sha9>`, `rereview-<pr>-<sha9>`), i.e. when the loop acted on this head."""
    try: return (STATE / "auto" / f"{kind}-{pr}-{sha9}").stat().st_mtime
    except OSError: return None
def rereview_minutes():
    """The loop's own re-queue window (SG_REREVIEW_MINUTES, 20 min by default): how long a head waits for the next attempt."""
    try: return float(os.environ.get("SG_REREVIEW_MINUTES") or 20)
    except ValueError: return 20.0
def review_rows(pr):
    """review-times.tsv rows for PR `pr`: one per model attempt (timestamp, PR, model, seconds, exit code), written by review.sh."""
    rows = []
    try: lines = (STATE / "review-times.tsv").read_text(errors="ignore").splitlines()
    except OSError: return rows
    for l in lines:
        f = l.rstrip("\n").split("\t")
        if len(f) < 5 or f[1] != str(pr) or not f[3].isdigit() or not f[4].lstrip("-").isdigit(): continue
        try: ts = time.mktime(time.strptime(f[0].strip(), "%Y-%m-%dT%H:%M:%S"))
        except ValueError: continue
        rows.append({"ts": ts, "model": f[2], "secs": int(f[3]), "exit": int(f[4])})
    return rows
def review_attempts(pr, sha9):
    """The loop's review attempts on PR `pr`'s current head `sha9`, newest first. `launched-<pr>-<sha9>` (or a re-queue in
    `rereview-<pr>-<sha9>`) is the loop's record that it started work on this head: a row written before that belongs to the
    head that moved, so a new head leaves no attempts and the card goes back to `awaiting review`."""
    start = _marker_mtime("launched", pr, sha9)
    if start is None: start = _marker_mtime("rereview", pr, sha9)
    if start is None: return []
    att = [r for r in review_rows(pr) if r["ts"] >= start - 5]   # 5 s of slop: the row follows the marker, never precedes it
    for r in att: r["result"] = "passed" if r["exit"] == 0 else ("timed out" if r["exit"] == 142 else "failed")
    return sorted(att, key=lambda r: r["ts"], reverse=True)
def summary_lines(pr, sha9):
    """The run summaries of revci-<pr>.out for head `sha9`: the `#<pr> <sha9> ` namespace carries the run's summary line
    only, and a summary always carries `CI=`. The run's progress and terminal notices (CI_DISPATCH, SUPERSEDED,
    CI_TIMEOUT) are printed with a `NOTICE ` prefix instead, so no reader can take one for a result (#1767)."""
    try: ls = (STATE / f"revci-{pr}.out").read_text(errors="ignore").splitlines()
    except OSError: return []
    return [l for l in ls if l.startswith(f"#{pr} {sha9} ") and "CI=" in l]
def _verdict_of(pr, sha9):
    """The verdict of the last completed run of the review loop for this head, from the revci-<pr>.out summary line."""
    ls = summary_lines(pr, sha9)
    return (ls[-1].split("VERDICT:", 1)[1].strip() if ls and "VERDICT:" in ls[-1] else "")
def review_state(pr, sha9, now=None):
    """The card's review chip when the loop's latest attempt on this head did not pass; None keeps today's chip.
    A timeout (exit 142) names the model and the seconds it burned; any other failure names the model and keeps the reason
    (the run's own verdict, else the exit code) in the tooltip. The retry line comes from the loop's re-queue marker.
    Returns [css class, chip text, tooltip, retry line]."""
    att = review_attempts(pr, sha9)
    now = time.time() if now is None else now
    if not att or att[0]["exit"] == 0: return None
    last = att[0]
    reason = _verdict_of(pr, sha9) or f"exit {last['exit']}"
    chip = f"review timed out · {last['model']} · {last['secs']} s" if last["exit"] == 142 else "review failed"
    lines = [f"{a['model']} {a['secs']} s · {a['result']}" for a in att]   # newest first, so the card's tooltip reads top-down
    lines[0] += f" ({reason})"
    retry = ""
    mk = _marker_mtime("rereview", pr, sha9)
    if mk is not None:
        if last["ts"] < mk: retry = "retrying now"   # the re-queue already started the attempt this head is owed
        else:
            left = mk + rereview_minutes() * 60 - now
            retry = "retrying now" if left <= 0 else f"retry in {int(-(-left // 60))} min"
    return {"cls": "changes", "chip": chip, "tip": f"attempts on {sha9}, newest first:\n" + "\n".join(lines), "retry": retry}
GATE_SLOP = 5.0     # the gate line follows the pass stamp by less than a second; 5 s covers a coarse clock
WATCH_CYCLE = 90.0  # one watch.sh cycle: three 30 s sleeps
GATE_IDLE_AFTER = 3 * WATCH_CYCLE   # three cycles with no pass: the loop is not deciding anything, so no card may say it did
def _pass_epoch():
    """auto/.pass: the epoch of the loop's last pass, stamped at the top of every loop pass."""
    try: return float((STATE / "auto" / ".pass").read_text().strip())
    except (OSError, ValueError): return None
def _ago(secs): return f"{int(secs)} s" if secs < 90 else f"{int(round(secs / 60))} min"
def gate_line(n):
    """auto/gate-<n>: `<head sha9><TAB><epoch><TAB><reason><TAB><what it means>`, the pass's latest decision about this PR,
    overwritten at every exit so the file carries the latest reason and only it (#1766). None when the file is absent or
    unreadable — a half-written line cannot be seen, the pass writes it through a temp file + mv."""
    try: f = (STATE / "auto" / f"gate-{n}").read_text(errors="ignore").rstrip("\n").split("\t")
    except OSError: return None
    if len(f) != 4 or not f[1].isdigit(): return None
    return f[0], float(f[1]), f[2], f[3]
def gate_state(n, sha9, now=None):
    """The card's chip from the loop's own reason (#1771), as [css class, chip, tooltip]. The hub used to derive readiness
    from the review line, so a card could claim `ready to merge` for a head the loop was holding — the loop's verdict is
    already on disk, so the card reports it instead of guessing, and the two cannot disagree by construction. The reason in
    auto/gate-<n> is the loop's word only for the head it names, written during the pass auto/.pass stamps; `ready to merge`
    is reserved for a gate line that says the merge is going ahead. On any other head the chip says which honest state
    applies: `loop off` (auto/off), `loop idle` (no pass for three watch cycles), `new head, not seen yet`."""
    now = time.time() if now is None else now
    if (STATE / "auto" / "off").exists():
        return ["idle", "loop off", "auto/off is present: the loop is off, so no card can say what it decided"]
    p = _pass_epoch()
    if p is None or now - p > GATE_IDLE_AFTER:
        last = "has never run" if p is None else f"last ran {_ago(now - p)} ago"
        return ["idle", "loop idle", f"the loop {last} (auto/.pass, idle past {_ago(GATE_IDLE_AFTER)} = three watch cycles)"]
    g = gate_line(n)
    if not g or g[0] != sha9: return ["idle", "new head, not seen yet",
        f"the loop's last reason {'names no head' if not g else f'is for {g[0]}, not {sha9}'}: it has not judged this head"]
    if g[1] < p - GATE_SLOP: return ["idle", "new head, not seen yet",
        f"the loop's last reason is from the pass before this one ({g[0]}, written {_ago(now - g[1])} ago): the running pass has not judged this head"]
    reason, text = g[2], g[3]
    if reason in ("merged", "merging"): cls, chip = "ready", "ready to merge"   # the gate says the merge is going ahead
    elif reason.startswith("hold-"): cls, chip = "changes", "held: " + reason[5:]
    else: cls, chip = "running", reason   # the loop's own word for what it is doing on this head (reviewing, fix-round, ...)
    return [cls, chip, f"{reason}: {text}\n{g[0]} · written {_ago(now - g[1])} ago"]
STRIP_MAX_AGE = 180.0   # the strip's own honesty window: past this the facts are no longer the poll's (#1772)

# One GraphQL query per poll for every open PR: the list, the head and base, the merge state, the labels, the review
# threads, the status rollup, the author and the issues it closes (#440), in place of a REST list plus two REST calls per
# PR every poll (#1772). Nothing in it is optional to the reader: a field the query did not return stays None and renders
# `?`, never a passing word.
OPEN_PR_QUERY = """query{repository(owner:"%s",name:"%s"){pullRequests(first:100,states:OPEN,orderBy:{field:UPDATED_AT,direction:DESC}){nodes{
number title isDraft updatedAt createdAt headRefOid baseRefName mergeStateStatus
author{login}
labels(first:30){nodes{name}}
closingIssuesReferences(first:5){nodes{number}}
reviewThreads(first:100){totalCount nodes{isResolved}}
commits(last:1){nodes{commit{committedDate statusCheckRollup{state contexts(first:100){nodes{__typename ... on CheckRun{databaseId conclusion status} ... on StatusContext{state}}}}}}}}}}}"""

LIVE_BAD = ("failure", "timed_out", "action_required", "startup_failure", "cancelled", "error")
LIVE_WAIT = ("queued", "in_progress", "pending", "waiting", "requested", "expected", "completed")
CI_WAIT = ("queued", "in_progress", "pending", "waiting", "requested")   # a check GitHub reports as unfinished
CI_QUEUED = ("queued",)          # ... and that no runner has picked up: a queue is not a run (#149)
CI_QUEUE_MAX = 20 * 60.0         # the timeout `.github/workflows/ci.yml` gives its job: still queued past it, no run started

def _live_states(rollup):
    """The states of the live rollup's contexts: a CheckRun reports its conclusion once it has one and its status before
    that, a StatusContext reports its state. None when the rollup itself is absent (unknown, not empty)."""
    if rollup is None: return None
    out = []
    for c in ((rollup.get("contexts") or {}).get("nodes") or []):
        v = c.get("conclusion") or c.get("status") or c.get("state")
        if v: out.append(str(v).lower())
    return out

def _live_rollup(states):
    """The live rollup in the same coarse words the loop's own `CI=` snapshot is read in: pass, fail, pending, none."""
    if states is None: return None
    if any(s in LIVE_BAD for s in states): return "fail"
    if any(s in LIVE_WAIT for s in states): return "pending"
    return "pass" if states else "none"

CLOSES_RE = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)

def _title_closes(title):
    """The issue numbers a title names as closed (`Closes #12`, `Fixes #34`): the fallback for a plan whose query does not
    carry `closingIssuesReferences`, read from the title alone (#440). The body is not in the query, so it is never read."""
    return [int(m) for m in CLOSES_RE.findall(title or "")]

def prs_from_graphql(payload, repo=None):
    """One row per open PR out of the batched OPEN_PR_QUERY response, or None when the call returned nothing usable
    (failed, rate-limited, no repository): None means the caller keeps the last good rows, never that no PR is open
    (#1772). A fact the query did not return stays None, so the strip renders `?` for it. The row also carries the author
    (`by`) and the issues the PR closes (`cl`, from `closingIssuesReferences`, or the title when the plan lacks it) (#440).
    `repo` is the repository the payload was read from: it rides on every row as `r`, so a card links, opens and resolves
    its issue in its own repository (#1820)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    nodes = (((data or {}).get("repository") or {}).get("pullRequests") or {}).get("nodes")
    if nodes is None: return None
    rows = []
    for nd in nodes:
        commit = ((nd.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}
        contexts = ((commit.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes") or []
        states = _live_states(commit.get("statusCheckRollup"))
        checks = [{"id": c.get("databaseId"), "status": c.get("status"), "conclusion": c.get("conclusion")} for c in contexts if c.get("__typename") == "CheckRun"]
        th = nd.get("reviewThreads")
        threads = None if th is None or (th.get("totalCount") or 0) > len(th.get("nodes") or []) \
            else sum(1 for t in (th.get("nodes") or []) if not t.get("isResolved"))
        labels = [l["name"] for l in ((nd.get("labels") or {}).get("nodes") or [])] if nd.get("labels") is not None else None
        ms = "" if (nd.get("mergeStateStatus") or "").upper() == "UNKNOWN" else (nd.get("mergeStateStatus") or "").lower()
        base = nd.get("baseRefName") or None
        cr = nd.get("closingIssuesReferences")   # the issues the PR closes, from the same query (#440); absent on a plan that does not carry it
        closes = [x["number"] for x in (cr.get("nodes") or []) if isinstance(x, dict) and isinstance(x.get("number"), int)] \
            if cr is not None else _title_closes(nd.get("title"))
        author = (nd.get("author") or {}).get("login")
        rows.append({"n": nd["number"], "t": nd.get("title") or "", "d": bool(nd.get("isDraft")), "u": nd.get("updatedAt") or "",
                     "o": nd.get("createdAt") or "", "h": nd.get("headRefOid") or "", "base": base, "ms": ms, "l": labels, "by": author, "cl": closes,
                     "th": threads, "ci": states, "checks": checks, "hc": commit.get("committedDate") or None, "rollup": _live_rollup(states), "r": repo,
                     "facts": not (states is None or threads is None or labels is None or not ms or not base)})
    return rows

def gh_door_read(*args):
    """One GitHub read through the door, `ghcall.sh` (ADR-0277): the answer on stdout, or an error carrying the door's
    own line -- GitHub's refusal, or a local failure before GitHub reached a verdict (#252). A read that did not answer
    never reaches a parser, so the column names GitHub's message instead of the first line of its error body."""
    door = pathlib.Path(__file__).resolve().parent / "ghcall.sh"
    try:
        p = subprocess.run(["bash", "-c", '. "$0"; "$@"', str(door), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"the GitHub read did not finish: {e}") from e
    if p.returncode: raise RuntimeError(" ".join(p.stdout.split()) or f"the GitHub read exited {p.returncode}")
    return p.stdout

LAND = os.environ.get("SG_LAND_BRANCH") or "dev"   # the branch a work branch lands in: a branch is open while it holds commits this one lacks
WORK_BRANCH = re.compile(r"(?:fix|gui)/(\d+)(?:\D.*)?$")

_compares = {}   # (repo, landing sha, branch sha) -> the compare answer the rows read (#524)

def open_branches_of(repo):
    """The open work branches of ONE repository: every remote `fix|gui/<n>-...` branch with commits the landing branch lacks.
    A row is `{"n": issue, "t": branch, "u": date of its newest commit, "ps": "ahead N", "br": True, "r": repo}`. No pull
    request is read: the branch list and one compare per work branch, through the door."""
    rows = []
    branches = json.loads(gh_door_read("gh_api", f"repos/{repo}/branches?per_page=100") or "[]")
    land = next((b["commit"]["sha"] for b in branches if b.get("name") == LAND), None)
    for b in branches:
        m = WORK_BRANCH.match(b.get("name") or "")
        if not m: continue
        # A compare is a function of the two tips: kept by both shas, it is read again only when one moves (#524). One per
        # work branch on every pass spent the REST quota.
        key = (repo, land, b["commit"]["sha"])
        cmp = _compares.get(key) if land else None
        if cmp is None:
            cmp = json.loads(gh_door_read("gh_api", f"repos/{repo}/compare/{LAND}...{b['commit']['sha']}") or "{}")
            if land:
                for k in [k for k in _compares if k[0] == repo and k[1] != land]: del _compares[k]   # the landing tip moved: the old answers never return
                _compares[key] = {"status": cmp.get("status"), "ahead_by": cmp.get("ahead_by"), "commits": (cmp.get("commits") or [])[-1:]}
        if cmp.get("status") not in ("ahead", "diverged"): continue   # the tip is in the landing branch: the branch is landed, not open
        last = ((cmp.get("commits") or [{}])[-1].get("commit") or {}).get("committer") or {}
        rows.append({"n": int(m.group(1)), "t": b["name"], "u": last.get("date") or "", "o": "", "h": b["commit"]["sha"],
                     "ps": f"ahead {cmp.get('ahead_by', 0)}", "br": True, "r": repo})
    return rows

def _landed_ledger():
    """The rows `branchland.sh` keeps for each branch it put in the landing branch: epoch, branch, landed sha, issue."""
    try: lines = (STATE / "landed.tsv").read_text().splitlines()
    except OSError: return []
    return [f for f in (l.split("\t") for l in lines) if len(f) >= 4 and f[0].isdigit() and f[3].isdigit()]

def landed_branches_of(repo):
    """The latest page of the branches landed in the landing branch, from the ledger (a landed branch is deleted, so the
    remote no longer lists it). Each row names the branch and the landed sha, and carries the landing time as `c`.

    The board draws no lane for these rows (#384): a landed branch is read from its issue card, Done in dev. The pass
    keeps them for its own last-landing fact and for the run a merge already delivered."""
    return [{"n": int(f[3]), "t": f"{f[1]} · {f[2][:7]}", "c": dt.datetime.fromtimestamp(int(f[0]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "m": True, "r": repo} for f in _landed_ledger()[-100:]]

def prs_pass(fetch_open=None, fetch_landed=None, repos=None):
    """One read of the open branch lane and of the landing ledger for EVERY repository the hub is told to read (#1820),
    in place of the single-repository read the column carried before it. Each repository is read on its own, through the
    two fetchers it is given (the seam the tests stub), and contributes its own rows -- each row named by `r`.

    A repository the hub cannot read adds its error to the returned `errs` and keeps the last good rows of ITS OWN from
    the cache: an unreachable repository is an error on the lane, never a silently empty one, and never a reason to
    drop the rows of the repository that answered. Returns (rows, landed, errs)."""
    fetch_open = fetch_open or open_branches_of
    fetch_landed = fetch_landed or landed_branches_of
    repos = REPOS if repos is None else repos
    last_prs, last_landed = board_cache.get("prs") or [], board_cache.get("landed") or []
    prs, landed, errs = [], [], []
    for r in repos:
        try:
            rows = fetch_open(r)
            if rows is None: raise ValueError("the open-branch query returned nothing; the last good rows stay in place")
            rows_landed = fetch_landed(r) or []
        except Exception as e:
            errs.append(f"{r}: {str(e)}")   # the whole message: the door's refusal line carries GitHub's own words (#252)
            prs += [x for x in last_prs if x.get("r") == r]     # this repository's own last good rows, and no other's
            landed += [x for x in last_landed if x.get("r") == r]
            continue
        prs += rows; landed += rows_landed
    return prs, landed, errs

def _ci_state(tok):
    """The coarse state of a `CI=` snapshot (`success:9`, `failure:1,success:8`, `none`, `skipped`): pass, fail, pending, none."""
    parts = [p.split(":")[0] for p in (tok or "").split(",") if p]
    if not parts: return "?"
    if any(p in LIVE_BAD for p in parts): return "fail"
    if any(p in LIVE_WAIT for p in parts): return "pending"
    return "pass" if "success" in parts else "none"

def ci_snapshot(n, sha9):
    """The loop's own `CI=` word for this head, out of the run's summary line; None when it printed none for this head."""
    ls = summary_lines(n, sha9)
    return ls[-1].split("CI=", 1)[1].split()[0] if ls and "CI=" in ls[-1] else None

def _verdict_token(v):
    """`OK`, `CHG`, `BLK` for the loop's verdict text, `?` for a word this strip does not know."""
    if v.startswith("APPROVE"): return "OK"
    if v.startswith("CHANGES"): return "CHG"
    if v.startswith("BLOCKED"): return "BLK"
    return "?"

_REVFORMAT = {}
def _revformat():
    """revformat.py, the module that reads a finding's severity off the section it sits in (#1777). The strip asks the
    same module the merge path asks, so a debt-only CHANGES cannot be called blocking on one surface and merged on the other."""
    if "m" not in _REVFORMAT:
        try: _REVFORMAT["m"] = importlib.import_module("revformat")
        except Exception:
            spec = importlib.util.spec_from_file_location("revformat", pathlib.Path(__file__).resolve().parent / "revformat.py")
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); _REVFORMAT["m"] = m
    return _REVFORMAT["m"]

def review_debt_only(n):
    """True when the review the loop wrote for this PR raises DEBT only (`blocking=0 debt>0`). The loop's own passes treat one
    as an approval, so no card may render it as blocking (#1772). A module that cannot answer is not an answer:
    no debt claim without it."""
    try: review = (STATE / f"review-{n}.md").read_text(errors="ignore")
    except OSError: return False
    if not review.strip(): return False
    try: c = _revformat().count(review)
    except Exception: return False
    return c.get("Blocking", 0) == 0 and c.get("Debt", 0) > 0

def _summary_any_head(n):
    """Every summary line the loop printed for this PR, whatever head it names."""
    try: ls = (STATE / f"revci-{n}.out").read_text(errors="ignore").splitlines()
    except OSError: return []
    return [l for l in ls if l.startswith(f"#{n} ") and "CI=" in l]

def review_cell(n, sha9):
    """The R cell: the review verdict *on this head* (#1772). `-` = the loop has not judged this head, and the tooltip
    says which head its last word was about; `...` = it queued a review for this head; `debt` = a debt-only CHANGES."""
    v = _verdict_of(n, sha9)
    if v:
        tok = _verdict_token(v)
        if tok == "CHG" and review_debt_only(n): tok = "debt"
        tip = f"the loop's review of this head ({sha9}): VERDICT: {v}"
        if tok == "debt": tip += " -- a debt-only CHANGES: the loop's own passes treat it as an approval, so it does not block"
        return tok, tip
    old = _summary_any_head(n)
    if old:
        osha, oline = old[-1].split()[1], old[-1]
        ov = oline.split("VERDICT:", 1)[1].strip() if "VERDICT:" in oline else ""
        if osha != sha9 and ov.startswith("APPROVE"):
            return "-", f"the loop's APPROVE is on {osha}, an older head (this head is {sha9}): an APPROVE is a word about the head it names"
        if osha != sha9: return "-", f"the loop's last review named {osha}, not this head ({sha9})"
    if (STATE / "auto" / f"launched-{n}-{sha9}").exists():
        return "...", f"the loop queued a review for {sha9} (auto/launched-{n}-{sha9}) and has printed no verdict yet"
    return "-", f"the loop has not reviewed this head ({sha9})"

def gate_strip(x, t=None, now=None):
    """The five-cell strip under the card's chip: R C T B H, each cell its own word and tooltip (#1772).

    R the review verdict on this head, C the loop's own CI snapshot for it (marked `live!=` when GitHub's live rollup
    disagrees), T the live unresolved review threads, B base and merge state, H labels and holds. A fact the batched
    query did not return renders `?` -- unknown never reads as passing -- and the whole strip is `old` past 180 s of
    `t` (the poll's stamp), so a failed poll greys out instead of standing in as a fresh one."""
    now = time.time() if now is None else now
    n, sha9 = x["n"], (x.get("h") or "")[:9]
    age = int(now - (t or 0))
    rv, rtip = review_cell(n, sha9)
    snap = ci_snapshot(n, sha9)
    if snap is None:
        cv = ("?", "the loop has printed no CI snapshot for this head" +
              (f"; GitHub's live rollup says {x['rollup']}" if x.get("rollup") else "; GitHub's live rollup is not in this poll either"))
    elif x.get("rollup") and _ci_state(snap) != x["rollup"]:
        cv = (f"{snap} live!={x['rollup']}", f"the loop's CI snapshot for {sha9} is {snap}; GitHub's live rollup says {x['rollup']} for the same head, so they disagree")
    else:
        cv = (snap, f"the loop's own CI snapshot for {sha9} (the head it was taken on), from the run's summary line")
    th = x.get("th")
    tv = ("?", "the batched query did not return this PR's review threads") if th is None else \
         (str(th), f"{th} unresolved review threads on GitHub (resolved ones are not counted)")
    base, ms = x.get("base") or "?", x.get("ms")
    bv = ("?", "GitHub has not computed the merge state for this head" if x.get("facts") is not False else "the batched query did not return this PR") \
        if not ms else (f"{base}·{ms}", f"base {base}, merge state {ms}")
    labels, holds = x.get("l"), []
    g = gate_line(n)
    if g and g[2].startswith("hold-"): holds.append("hold:" + g[2][5:])
    if x.get("d"): holds.append("draft")
    if labels is None:
        hv = ("?", "the batched query did not return this PR's labels")
    else:
        shown = ", ".join(labels[:3]) + (f" +{len(labels) - 3}" if len(labels) > 3 else "")
        hv = ("-", "no labels, no hold")
        if labels or holds: hv = (" ".join(filter(None, [shown, "·".join(holds)])), f"labels: {', '.join(labels) or 'none'}; holds: {'·'.join(holds) or 'none'}")
    return {"cells": [{"k": "R", "v": rv, "tip": rtip}, {"k": "C", "v": cv[0], "tip": cv[1]}, {"k": "T", "v": tv[0], "tip": tv[1]},
                      {"k": "B", "v": bv[0], "tip": bv[1]}, {"k": "H", "v": hv[0], "tip": hv[1]}],
            "age": age, "old": age > STRIP_MAX_AGE}

_CI_STATE = {}
def _ci_state_module():
    """The no-step rule shared with the landing gate."""
    if "m" not in _CI_STATE:
        spec = importlib.util.spec_from_file_location("ci_state", pathlib.Path(__file__).resolve().parent / "ci_state.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); _CI_STATE["m"] = module
    return _CI_STATE["m"]

def ci_cannot_run(x):
    """The platform stop of a failed check, with its own annotation where GitHub supplied one."""
    repo = x.get("r") or REPO
    for check in x.get("checks") or []:
        ident = check.get("id")
        if not ident or check.get("conclusion") not in ("failure", "startup_failure"): continue
        try:
            job = gh_json(f"repos/{repo}/actions/jobs/{ident}")
            detail = gh_json(f"repos/{repo}/check-runs/{ident}")
            if _ci_state_module().cannot_run(job, detail): return _ci_state_module().reason(detail)
        except Exception: continue
    return None

def _head_pushed_age(x):
    """The seconds since the head commit was pushed, from GitHub's own `committedDate` on the row; None when the query
    did not return it. A fact nobody has is unknown, so it never carries a stop (ADR-0018)."""
    at = x.get("hc")
    if not at: return None
    try: return time.time() - dt.datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError: return None

def pr_state(x):
    """One word for an open PR, from GitHub facts (CI, mergeable state) and the squad's review files (revci-<pr>.out, auto/launched-*).
    Worst first: draft, conflict, ci-failed, changes, review-timeout/failed, ci-cannot-run, ci-waiting, reviewing, ready,
    merged-soon, awaiting-review.
    Everything the card claims about the loop's own decision comes from auto/gate-<pr> instead of being derived (#1771).
    A row the batched query did not fill in says so instead of falling through to a passing word (#1772), and a debt-only
    CHANGES is the loop's to decide, not a block: the loop's own passes treat one as an approval.
    The CI chip reads the word GitHub reports (#149): a check that is `queued` says queued, never that a run is in
    progress -- `queued` and `in_progress` are two different words, and only a runner that picked the check up makes one
    of them a run. A head whose unfinished checks are all still queued past the timeout the ci job gives itself cannot
    be answered at all, and the chip says that instead of implying progress."""
    n = x["n"]; sha9 = (x.get("h") or "")[:9]
    if (STATE / "auto" / "off").exists(): return gate_state(n, sha9)   # the loop is off: no card reports a verdict of its own
    if x.get("d"): return ["draft", "draft"]
    ci = x.get("ci") or []
    if x.get("facts") is False: return ["idle", "facts unknown",
        "the batched open-PR query did not return this PR, so its CI, merge state and threads are unknown -- not passing"]
    if x.get("ms") == "dirty": return ["conflict", "merge conflict"]
    cannot = ci_cannot_run(x)
    if cannot is not None: return ["changes", "CI cannot run: account", cannot or "GitHub reported that the job never started"]
    if any(c in ("failure", "timed_out", "action_required", "startup_failure") for c in ci): return ["broke", "CI failed"]
    line = (summary_lines(n, sha9) or [""])[-1]   # a notice (NOTICE #<pr> <sha9> ...) is not a result: see summary_lines
    if "CHANGES" in line:
        if review_debt_only(n): return gate_state(n, sha9)   # a debt-only CHANGES is not blocking: the loop's own passes treat it as an approval (#1772)
        return ["changes", "changes requested"]
    rs = review_state(n, sha9)   # an attempt on this head failed or timed out: that is what the card is waiting on, not `awaiting review`
    if rs: return [rs["cls"], rs["chip"], rs["tip"], rs["retry"]]
    if "BLOCKED" in line: return ["broke", "review blocked"]
    wait = [c for c in ci if c in CI_WAIT]   # the checks GitHub reports as unfinished
    if wait:
        if all(c in CI_QUEUED for c in wait):   # not one runner has picked a check up: a queue, not a run
            age = _head_pushed_age(x)
            if age is not None and age > CI_QUEUE_MAX:
                return ["broke", "CI cannot run", f"the head {sha9} was pushed {_ago(age)} ago and every unfinished check on it is still queued, past the {int(CI_QUEUE_MAX / 60)} min the job gives itself: no run ever started, so this head answers nothing"]
            return ["running", "CI queued", f"every unfinished check on {sha9} reads queued, not a run in progress: no runner has picked one up" + (f" (head pushed {_ago(age)} ago)" if age is not None else " (the query returned no head date)")]
        return ["running", "CI running"]
    if not line and (STATE / "auto" / f"launched-{n}-{sha9}").exists(): return ["running", "reviewing"]
    if "APPROVE" in line: return gate_state(n, sha9)   # a green review line is not the loop's verdict: the gate file is
    return ["idle", "awaiting review"]
def pr_badge(x):
    """The one state a pull request's lane card wears as a badge (#247): conflict, checks red, approved or green, in that
    precedence; None while nothing is settled. Approved is the review loop's verdict on this head, never a guess."""
    if x.get("ms") == "dirty": return "conflict"
    ci = x.get("ci") or []
    if any(c in LIVE_BAD for c in ci): return "checks red"
    if "APPROVE" in (summary_lines(x["n"], (x.get("h") or "")[:9]) or [""])[-1]: return "approved"
    if ci and x.get("rollup") == "pass": return "green"
    return None
MOVES = STATE / "board-moves.json"   # the door's record of every move it proved (#649), keyed by the Project item id
MOVES_LOCK = threading.Lock()        # one writer in this process: two HTTP threads never drop the same entry twice
def read_moves():
    """The door's record of proved moves: {item id: {"column", "at"}}. A record that is missing, unreadable or not an
    object is no record: the board is drawn from the read alone, and nothing is written."""
    try: d = json.loads(MOVES.read_text())
    except (OSError, ValueError): return {}
    return d if isinstance(d, dict) else {}
def move_time(entry):
    """The instant the door proved a move, as whole epoch seconds; None when the entry carries no readable instant.
    `_utc` returns an aware UTC datetime, so its timestamp is the door's own instant, and both sides of the comparison
    below are whole seconds. A record without one is never taken as agreed: the board keeps its mark rather than lose
    a proved move."""
    stamp = entry.get("at") if isinstance(entry, dict) else None
    if not isinstance(stamp, str): return None
    try: return int(_utc(stamp).timestamp())
    except (ValueError, OverflowError, OSError): return None
def read_carried(move_at, read_at):
    """Whether a list read that began at `read_at` can have carried a move proved at `move_at`, both whole epoch
    seconds. The read must have begun in a later second: in the same second nothing tells which came first, so the
    mark stays rather than lose a proved move (a later read drops it)."""
    return move_at is not None and read_at is not None and move_at < read_at
PENDING_WINDOW_DEFAULT = 120.0   # seconds a proved move may stand with no list read agreeing (#651)
PENDING_WARNED = set()           # bad window values already named on stderr, so a page reload never repeats the line
def pending_window():
    """The window a proved move may stand without a list read agreeing (`SG_PENDING_WINDOW_SECONDS`, 120 s by default).
    A value that is not a number, or is negative, is the default, and one stderr line names the value and the default:
    a setting the hub ignored never passes silently."""
    raw = os.environ.get("SG_PENDING_WINDOW_SECONDS")
    if not raw: return PENDING_WINDOW_DEFAULT
    try: w = float(raw)
    except ValueError: w = None
    if w is None or w < 0:
        if raw not in PENDING_WARNED:
            PENDING_WARNED.add(raw)
            print(f"hub: SG_PENDING_WINDOW_SECONDS={raw!r} is not a non-negative number: the pending window is "
                  f"{PENDING_WINDOW_DEFAULT:g} s", file=sys.stderr)
        return PENDING_WINDOW_DEFAULT
    return w
def overlay_on():
    """Whether the served board applies the door's record (`SG_PENDING_OVERLAY`, on by default). `off`, `0`, `false`
    or `no` serves the list read alone, so a suspected overlay defect is ruled out without a code change (#651)."""
    return (os.environ.get("SG_PENDING_OVERLAY") or "").strip().lower() not in ("0", "off", "false", "no")
def report_expired(n, item, recorded, read, window):
    """One line in the loop's log (`$SG_STATE/cycle.log`) for an entry that outlived its window with no list read
    agreeing (#651): the item, the column the door recorded and the column the read holds. It is called only for an
    entry the drop really removed, so the report is idempotent without process memory: the retry that succeeds reports
    once, and every later serve finds no entry to report."""
    line = (f"{time.strftime('%T')} hub PENDING-EXPIRED #{n} (item {item}): the door recorded {recorded}, "
            f"the board read holds {read}; no read agreed within {window:g} s, so the entry was dropped")
    try:
        with (STATE / "cycle.log").open("a") as f: f.write(line + "\n")
    except OSError:
        pass   # the report is bookkeeping: the served board never fails for it
DROP_FAILURES = 0   # record drops this process whose write failed: the entry stays, and the next serve retries it
def drop_moves(ids):
    """Drop the entries the board read agreed with; every other entry stays (ADR-0030: formed whole beside the record,
    then one rename). The hub writes the record for nothing else (#650). An entry the door replaced while the board was
    read is kept: this pass drops nothing, and the next one drops what its own read agrees with. Returns the ids the
    record no longer holds: None when this pass wrote nothing, so an expiry is reported only for an entry really gone."""
    if not ids: return set()
    with MOVES_LOCK:
        try: before = MOVES.read_text(); moves = json.loads(before)
        except (OSError, ValueError): return None
        if not isinstance(moves, dict): return None
        left = {k: v for k, v in moves.items() if k not in ids}
        if len(left) == len(moves): return set()
        try:
            if MOVES.read_text() != before: return None   # the door wrote meanwhile: never lose a move this pass did not read
        except OSError: return None
        _tmp = MOVES.with_name(f"{MOVES.name}.{os.getpid()}.{threading.get_ident()}.tmp")   # the temp name is the writer's own, as save_board's is (#512)
        try:
            _tmp.write_text(json.dumps(left, sort_keys=True) + "\n"); os.replace(_tmp, MOVES)
        except OSError:
            global DROP_FAILURES
            DROP_FAILURES += 1   # the entry stays; the failure is counted, and the next serve retries (dropping is bookkeeping, never the served board)
            try: _tmp.unlink()   # a rename removes the temp; a failed write leaves it behind, and it is never the record
            except OSError: pass
            return None
        return set(moves) - set(left)
def board():
    overlay = overlay_on()   # one setting turns the overlay off: the served board is then the list read alone (#651)
    moves = read_moves() if overlay else {}   # the record is read on every serve, so a page reload sees the same overlay (#650)
    read_at = board_cache.get("rt")   # the instant the list read began, never the completion stamp `t`: a read that began before a move cannot have carried it, however late it finished
    window = pending_window()   # an entry older than the window expires even with no read agreeing (#651)
    agreed, expired = set(), {}   # expired: item id -> (card number, the recorded column, the column the read holds)
    by = {}
    for i in board_cache["items"]:
        closed = str(i["s"]).lower() == "closed"   # the row's own closedness: the freeze below is the card's, not the read's
        st, fz = _columns_module.view_column(i["st"], closed, i.get("l"))   # the column the table names, and the freeze over it
        pending = False
        entry = moves.get(str(i["i"])) if i.get("i") else None   # the door keys an entry by the Project item id
        if isinstance(entry, dict) and entry.get("column"):
            recorded = _columns_module.SHIPPED_NAME.get(entry["column"], entry["column"])   # an old Status name reads as its shipped column
            at = move_time(entry)   # the instant the door proved the move, in whole epoch seconds
            if recorded not in ORDER:
                pass   # no column this board ships: the entry is never drawn, and the read says nothing about it, so the hub leaves it (never writes the record for it)
            elif recorded == st and read_carried(at, read_at):   # the read began after the move and carries its column: the entry's work is done, however old it is
                agreed.add(str(i["i"]))
            elif at is not None and read_at is not None and read_at - at >= window:
                # The move is past its window at the read's own instant with no agreement: the read's column stands and
                # the entry is dropped. The window is the read's clock, never the wall clock, so a cache that stopped
                # updating never expires a move no read has had a window to agree with (#651).
                expired[str(i["i"])] = (i["n"], recorded, st)
            else:
                # The read began in or before the move's second, or contradicts it: draw the move at once and keep the
                # entry until a read that began after the move agrees (#650). A column that matches by coincidence is no proof.
                st, pending = recorded, True
                fz = _columns_module.view_column(recorded, closed, i.get("l"))[1]   # the freeze of the column drawn, over the row's own closedness, never the read's
        row = {"n": i["n"], "t": i["t"], "c": i.get("c"), "l": i.get("l") or []}
        if pending: row["pd"] = True   # the mark the page draws; the rule lives here, never in the view (ADR-0137)
        if fz: row["fz"] = fz
        if _columns_module.SPEC_LABEL in row["l"]: row["sp"] = True   # a spec is an issue with the spec label: the Specs filter reads this
        # The owner's one move (#560): a card in Backlog shows the arrow that starts it, and a spec card never does
        # (ADR-0142). The rule lives here, not in the page: the card renders the fact its row carries (ADR-0137).
        if st == COLUMN["unscheduled"] and not row.get("sp") and str(i["s"]).lower() != "closed": row["pm"] = True
        if i.get("b"): row["b"] = i["b"]
        sc = task_count(i)                       # the card's own count, a value: the page renders it and holds no rule
        if sc: row["sc"] = sc
        ho = holder_text(i)                      # the run that holds the card, read from the seam (#241)
        if ho: row["ho"] = ho
        by.setdefault(st, []).append(row)
    names = ORDER + sorted(n for n in by if n not in ORDER)   # every shipped column shows, even at 0; a user column follows
    def wave(x):   # `wave:NN` label first, then issue number; cards with no wave go last
        w = [int(m.group(1)) for l in x["l"] for m in [re.match(r"wave:(\d+)$", l)] if m]
        return (min(w) if w else 10**9, x["n"])
    groups = [{"name": n, "n": len(by.get(n, [])), "items": sorted(by.get(n, []), key=lambda x: (x["c"] or "", x["n"]), reverse=True) if n == COLUMN["issue_closed"] else sorted(by.get(n, []), key=wave)} for n in names]
    prs = sorted(board_cache["prs"], key=lambda p: p["u"], reverse=True)
    for x in prs:
        if x.get("br"): x.setdefault("gs", None); x.setdefault("pb", None); x["kind"] = _columns_module.BRANCH_KIND; continue   # a work branch has no review strip and no badge: its chip is its standing against dev
        x["ps"] = pr_state(x); x["gs"] = gate_strip(x, t=board_cache.get("t")); x["pb"] = pr_badge(x); x["kind"] = _columns_module.PR_KIND   # the chip, the R C T B H strip under it (#1772), the lane's badge (#247)
    # Every card names its repository (#1820): `r` is the full owner/name (what a link and the popup read), `rt` the tag
    # the card wears (MesenAI / agent-squad, or owner/repo when the short name names more than one repository).
    # Every row carries its own kind (#440): a pull request is `pr`, a work branch is `branch`. A work branch is served by
    # no view of the board (owner, 2026-10-16: the Kinds view went): it is read from the landing ledger and the issue card.
    cases = []   # the rows beside the axis, from the draft items the pass already read: the age is the pass's own clock against the pass's end
    for title in board_cache.get("cases") or []:
        m = TEST_CASE_TITLE.match(title)
        if m: cases.append({"issue": int(m[1]), "result": m[2], "commit": m[3], "ended": m[4], "age": max(0, int(time.time() - _utc(m[4]).timestamp()))})
    perr = "; ".join(board_cache.get("errs") or [])   # the repositories this pass could not read, named on the column
    # The lane carries the pull requests only (#440): a work branch has no lane and no other view. Each row names its own
    # kind, so the page reads a pull request by the row, never by the lane's name. An empty lane is still served, `n` 0.
    open_prs = [p for p in prs if p["kind"] == _columns_module.PR_KIND]
    lane = {"name": COLUMN["pr_open"], "tracking": True, "n": len(open_prs), "err": perr or None,
            "items": [{"n": p["n"], "t": p["t"], "u": p["u"], "ps": p["ps"], "gs": p.get("gs"), "pb": p.get("pb"),
                       "rollup": p.get("rollup"), "ci": p.get("ci"), "o": p.get("o"), "by": p.get("by"), "cl": p.get("cl"),
                       "r": p.get("r") or REPO, "rt": repo_tag(p.get("r") or REPO), "kind": p["kind"]} for p in open_prs]}
    if agreed or expired:
        dropped = drop_moves(agreed | set(expired))   # agreement settles an entry; the window settles one no read ever agreed with (#651)
        for item, (n, recorded, read) in expired.items():
            if dropped and item in dropped: report_expired(n, item, recorded, read, window)   # only a drop that really happened is reported, so a retry never repeats a line
    return {"t": board_cache["t"], "err": board_cache["err"], "prs": lane, "groups": groups, "cases": cases, "labels": sorted({l for i in board_cache["items"] for l in (i.get("l") or [])}), "lc": board_cache.get("lc", {})}

PROMOTE_TO = COLUMN["issue_todo"]   # the column the arrow moves a card into: the owner's go signal (ADR-0142)
def promote_write(issue):
    """The one writer call of the owner's move: `<owner> <project> Todo <n>`, the shape reconcile.py writes, so the
    board seam (`board.sh`) is the only reach of the carrier."""
    return subprocess.run(["bash", str(pathlib.Path(__file__).resolve().parent / "board.sh"), OWNER, str(PROJECT_NO), PROMOTE_TO, str(issue)], capture_output=True, text=True, timeout=120)
def promote(issue):
    """Move one open card from Backlog to Todo through the board seam (#560). Only a card the hub holds in Backlog now
    is accepted: a closed issue keeps its column (ADR-0143), a spec is never work (ADR-0142), and a card already in
    Todo sends no write (ADR-0144). A proven write updates the cache, so the card moves at the next refresh without a
    GitHub read right after the write."""
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0: return {"err": "issue: a number"}
    item = next((i for i in board_cache.get("items") or [] if i.get("n") == issue), None)
    if item is None: return {"err": "not on the board"}
    if str(item.get("s") or "").lower() == "closed": return {"err": "the issue is closed: a closed card keeps its column"}
    col, _ = _columns_module.view_column(item.get("st"), False, item.get("l"))
    if col != COLUMN["unscheduled"]: return {"err": f"the card is in {col}, not {COLUMN['unscheduled']}: only a Backlog card moves"}
    if _columns_module.SPEC_LABEL in (item.get("l") or []): return {"err": "a spec is never work: its tickets are (ADR-0142)"}
    try: r = promote_write(issue)
    except Exception as e: return {"err": str(e)[:200] or "the writer did not answer"}
    if r.returncode:
        lines = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
        return {"err": (next((l for l in lines if l.startswith("#")), lines[0] if lines else "") or "the writer refused")[:300]}
    item["st"] = PROMOTE_TO   # the cache follows the write, so the next refresh shows the move without a GitHub read after it
    save_board()
    return {"ok": True, "issue": issue, "to": PROMOTE_TO}

_issue_cache = {}
def gh_json(path, timeout=30):
    """One `gh api` read as JSON, or None when it returned nothing (the one seam the issue panel reads through)."""
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=timeout).stdout or "null")
def issue_detail(n, repo=None):
    """Issue (or PR) body + the last 5 comments, fetched on click and cached 60 s. n is an int, never interpolated raw.
    `repo` is the repository the card belongs to (#1820): a number is only a number, and PR #1821 of the tooling
    repository is not PR #1821 of the emulator -- a repository the hub was not told to read is never read, so the panel
    resolves a card in its own repository or in the profile's one."""
    repo = repo if repo in REPOS else REPO
    hit = _issue_cache.get((repo, n))
    if hit and time.time() - hit[0] < 60: return hit[1]
    g = gh_json
    it = g(f"repos/{repo}/issues/{n}")
    if not it or "number" not in it: return {"err": "not found"}
    cm = g(f"repos/{repo}/issues/{n}/comments?per_page=100") or []
    d = {"n": n, "r": repo, "title": it["title"], "state": it["state"], "pr": "pull_request" in it, "url": it["html_url"], "by": it["user"]["login"],
         "created": it["created_at"], "updated": it["updated_at"], "labels": [l["name"] for l in it["labels"]], "body": (it.get("body") or "")[:3000],
         "total": len(cm), "comments": [{"by": c["user"]["login"], "at": c["created_at"], "body": (c.get("body") or "")[:1200]} for c in cm[-5:]]}
    if d["pr"]:   # a PR also gets its branch, size, merge state, CI summary and the latest review verdicts
        p = g(f"repos/{repo}/pulls/{n}") or {}
        ck = (g(f"repos/{repo}/commits/{p['head']['sha']}/check-runs?per_page=100") or {}).get("check_runs", []) if p.get("head") else []
        cnt = {}
        for c in ck: k = c.get("conclusion") or c["status"]; cnt[k] = cnt.get(k, 0) + 1
        rv = {}
        for r in g(f"repos/{repo}/pulls/{n}/reviews?per_page=100") or []:
            if r["state"] != "COMMENTED": rv[r["user"]["login"]] = r["state"]
        d["pull"] = {"head": p.get("head", {}).get("ref"), "base": p.get("base", {}).get("ref"), "add": p.get("additions"), "del": p.get("deletions"), "files": p.get("changed_files"),
                     "draft": p.get("draft"), "merged": bool(p.get("merged_at")), "mstate": p.get("mergeable_state"), "checks": cnt, "reviews": rv}
        if d["pull"]["merged"]: d["state"] = "merged"
    _issue_cache[(repo, n)] = (time.time(), d); return d

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
    {"state": "handled", "label": "Needs-human run, landed in the fork", "color": "ok", "css": ""},
    {"state": "running", "label": "Run in progress", "color": "run", "css": ""},
    {"state": "starting", "label": "Run starting", "color": "run", "css": "border-style:dotted"},
    {"state": "stalled", "label": "Run stalled", "color": "wait", "css": "border-style:dashed"},
    {"state": "no verdict yet", "label": "No verdict yet", "color": "none", "css": ""},
]

PAGE = r"""<!doctype html><meta charset=utf-8><title>Squad hub</title>
<style>
:root{--bg:#0f1115;--panel:#161a21;--line:#2a303b;--fg:#d8dde6;--mute:#8a93a3;--ok:#3ecf8e;--warn:#f5b041;--err:#ff6b6b;--run:#2dd4bf;--none:#b79cf6;--wait:#e8d44d;--pin:#5aa9ff}
:root{--col-w:185px}   /* the docked strip's column geometry: the one place its width is set (#1791); a column holding no cards is not drawn at all, so it reserves none (#1809) */
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px ui-monospace,Menlo,monospace;display:flex;flex-direction:column;height:100vh}
#win{flex:1;min-height:0;display:flex;overflow:hidden}
#side{width:300px;border-right:1px solid var(--line);overflow:auto;background:var(--panel);flex:none}
#top{position:sticky;top:0;background:linear-gradient(#162033,#121a28);padding:10px 10px 10px 12px;border-bottom:2px solid var(--pin);border-left:3px solid var(--pin);z-index:2;box-shadow:0 2px 8px #0008}#top>b{font-size:13px;letter-spacing:.12em;color:var(--pin)}
#top .row{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:6px}
button{background:#222a35;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:2px 8px;cursor:pointer;font:inherit}
button.on{border-color:var(--pin);color:var(--pin)}
.kd{margin-right:5px;font-size:12px}.r{padding:7px 10px;border-top:1px solid var(--line);cursor:pointer;display:flex;gap:6px;justify-content:space-between}.r:hover{background:#1c222b}
.r .st{font-size:11px;color:#c9d1d9;opacity:.8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
/* The share a gate counted (#80): how much of the suite passed, so a red gate still shows the work it got to. The full
   share reads as good, a partial one as in progress; a run no gate counted carries no chip at all, never a 0%. */
.r .sh{color:var(--warn);font-variant-numeric:tabular-nums}.r .sh.done{color:var(--ok)}
.r.show{background:#1f2a3a;box-shadow:inset 3px 0 0 var(--pin)}.r.pinned .pn{color:var(--pin)}
.pn{color:var(--mute);flex:none}.gh.op{color:var(--fg)}.gh+.r{border-top:0}
.b{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;border:1px solid}
.b.waiting{color:var(--wait);border-style:dashed}   /* the childcc pass after it ended: no state of RUN_STATES says a pass that waits (#598) */
.landed,.pass,.ok{color:var(--ok)}
/*RUN_STATE_CSS*/
.m{color:var(--mute);font-size:11px;margin-top:2px}
#q{width:100%;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:3px 6px;font:inherit;margin-top:6px}
.sp{border-bottom:1px solid var(--line);font-size:11px}#mdl>summary{border-top:0}.sp>summary,.gh{list-style:none;display:flex;align-items:center;gap:6px;height:34px;padding:0 10px;font-size:12px;font-weight:600;color:var(--fg);cursor:pointer;user-select:none;background:#10141a;border-top:1px solid var(--line);outline:none}
.sp>summary::-webkit-details-marker{display:none}.sp>summary::before,.gh::before{content:"▸";flex:none;width:10px;color:var(--mute);font-weight:400}.sp[open]>summary::before,.gh.op::before{content:"▾"}.sp>summary:hover,.gh:hover{background:#171c24}.sp>summary:focus-visible,.gh:focus-visible{box-shadow:inset 0 0 0 1px var(--pin)}
.sp>summary .sr,.gh .sr{margin-left:auto;font-weight:400;color:var(--mute);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}.sp[open]>summary .sr{display:none}.sp .sb{padding:4px 10px 10px}
.sp h4{display:flex;align-items:baseline;justify-content:space-between;gap:4px;margin:10px 0 6px;font-size:12px;color:var(--fg);font-weight:600;white-space:nowrap}.sp h4 small{font-weight:400;color:var(--mute);font-size:10px;white-space:nowrap}
.tot{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.tot div{background:#10141a;border:1px solid var(--line);border-radius:6px;padding:4px 6px;font-size:9px;color:var(--mute)}.tot b{display:block;font-size:15px;color:var(--fg);font-weight:600}.tot b.m{color:var(--mute);font-size:10px;line-height:1.5}
.tot .cash,.cash{grid-column:1/-1;background:none;border:0;padding:0;margin:2px 0 0;font-size:10px;color:var(--mute);line-height:1.35}
#src,#per{display:inline-flex;flex-wrap:nowrap;gap:2px;margin:0}#src button{padding:1px 5px;font-size:10px}#per button{padding:1px 3px;font-size:9px}
.kr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-auto-rows:min-content;column-gap:8px;row-gap:2px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line);font-size:11px;min-width:0}.kr:last-child{border:0}.kr .l{grid-row:1/3;min-width:0;font-size:11px;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kr .l small{display:inline;color:var(--mute);font-size:9.5px}.kr b{grid-column:2;justify-self:end;font-size:14px;font-weight:600;line-height:1}.kr b small{font-size:10px;font-weight:400;color:var(--mute)}.kr b.m{color:var(--mute)}.kr b.w{color:var(--warn)}
.kr .d{grid-column:2;justify-self:end;font-size:9px;padding:0;white-space:nowrap;color:var(--mute)}.kr .d .good{color:#5fd38d}.kr .d .bad{color:#ff7b7b}
#mbody{padding:0}.mc{background:#10141a;border:1px solid var(--line);border-radius:8px;padding:10px;margin:6px 0 8px}.mc .mt{font-size:12px;font-weight:600;color:var(--fg);margin:0 0 2px}.mc .mx{font-size:11px;color:var(--mute);margin:0 0 8px;line-height:1.35}
.mr{display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;gap:5px 8px;padding:7px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);margin-bottom:5px;font-size:12px}.mr.p{border-color:#2f5d45;background:#13211b}.mr .rk{flex:none;width:22px;height:22px;border-radius:11px;background:#222a35;color:var(--mute);font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center}.mr.p .rk{background:var(--ok);color:#0b1a12}
.mr .mn{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3;font-size:12px;color:var(--fg)}.mr .ct{grid-column:1/3;display:flex;flex-wrap:wrap;gap:3px;align-items:center;min-width:0}.mr .mi{font-size:10px;color:var(--mute);margin-left:4px}.mr .ur{color:var(--warn)}.mr .pv{flex:1 1 auto;min-width:0;font-size:9.5px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mr .pv b{color:var(--ok);font-weight:600}.mr .el{flex:none;font-size:9px;color:var(--mute);margin-left:2px}.mr .mn small{display:block;font-size:10px;color:var(--mute)}.mr .ef{flex:none;width:62px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:11px;height:26px;padding:0 2px;cursor:pointer;text-overflow:ellipsis}.mr .ef.set{color:var(--warn);border-color:#6b5223}.mr .hr{flex:none;width:50px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:10.5px;height:26px;padding:0 2px;text-align:center}.mr .hr::placeholder{color:var(--mute)}.mr .hr:focus{outline:none;border-color:var(--pin)}.mr .hr.set{color:var(--warn);border-color:#6b5223}
.mr .mb{flex:none;display:flex;gap:2px;margin-left:4px}.mr .mb button{min-width:24px;height:24px;padding:0;font-size:12px;line-height:1}.mr .mb button:hover:not(:disabled){background:#2e3846;border-color:#4a5566}.mr .mb button:disabled{opacity:.25;cursor:default}.mr .mb button.x:hover:not(:disabled){color:var(--err);border-color:var(--err)}
/* The switch (#1819): a state, so it reads as a word, not a glyph. Off de-emphasises the row and marks the rank badge
   -- the order itself is untouched, which is the point of the switch. */
.mr .mb button.sw{min-width:30px;font-size:10px;letter-spacing:.02em}.mr .mb button.sw.off{color:var(--warn);border-color:#6b5223;background:#241d10}
.mr .bk{grid-column:2;justify-self:start;font-size:11px;color:var(--warn);border:1px solid var(--warn);border-radius:4px;padding:0 5px}.mr.off .mn,.mr.off .pv,.mr.off .el{opacity:.55}.mr.off .rk{background:#3a2a2a;color:var(--warn)}.mr.off .ef,.mr.off .hr{opacity:.75}
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
.subx{color:var(--mute);font-size:10.5px;line-height:1.45;margin:8px 0 4px}
.pie{display:flex;flex-direction:column;gap:6px;align-items:stretch}.pie svg{flex:none;width:112px;height:112px;margin:0 auto}.pie .lg{flex:1;min-width:0;font-size:11px}.pie .lg div{display:flex;align-items:center;gap:6px;padding:2px 0}.pie .lg i{width:9px;height:9px;border-radius:2px;flex:none}.pie .ln{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pie .lg b{font-weight:600}.warnl{margin-top:4px;font-size:9px;color:var(--warn)}
.up{color:var(--err)}.dn{color:var(--ok)}svg{display:block;width:100%}
#main{flex:1;min-width:0;display:grid;gap:4px;padding:4px}
.cell{display:flex;flex-direction:column;min-height:0;min-width:0}.cell small{color:var(--mute);padding:2px 4px;display:flex;justify-content:space-between}.cell .snap{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:12px;overflow:hidden;color:var(--mute);border:1px solid var(--line);background:#0f1115}.cell[tabindex]{cursor:pointer}.cell[tabindex]:hover .snap,.cell[tabindex]:focus-visible .snap{border-color:var(--pin);color:var(--fg,inherit)}
#board{position:relative;width:360px;border-left:1px solid var(--line);overflow:auto;background:var(--panel);flex:none}
#board.off{width:26px;overflow:hidden}#board.off #bbody{display:none}#bhead{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel);z-index:2}
#board.off #bhead b{display:none}#board.off #bhead{padding:8px 3px}
#bbody .ah{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-weight:600;position:sticky;top:34px;background:#1b212b;z-index:1}   /* the same gap as the docked header: `Todo0` is never a reading, in either rendering (#1809) */
#bbody .ah .n{background:#2a303b;border-radius:9px;padding:0 8px;font-weight:400}
#bbody .ah i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:var(--mute)}
#bbody .it{margin:6px 8px;padding:8px 10px;border:1px solid var(--line);border-left:3px solid var(--cc,var(--mute));border-radius:6px;background:var(--bg,#1c222c);line-height:1.35;font-size:12.5px;white-space:normal;overflow-wrap:anywhere;box-shadow:0 1px 2px #0006}
#bbody .it .ft{display:flex;justify-content:flex-end;gap:6px;margin-top:6px}#bbody .age,#bbody .tsk{display:inline-block;padding:0 6px;border-radius:9px;font-size:10.5px;font-variant-numeric:tabular-nums;color:var(--mute);border:1px solid var(--line)}#bbody .age.warm{color:#e3b341;border-color:#6b5223}#bbody .age.old{color:#f85149;border-color:#6e2a27}
#bbody .pbg{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;font-size:10.5px;font-weight:700;border:1px solid}#bbody .pbg.conflict,#bbody .pbg.checks-red{color:#f85149;border-color:#6e2a27}#bbody .pbg.green{color:#3fb950;border-color:#2e6b3a}#bbody .pbg.approved{color:#3fb950;border-color:#3fb950;background:#12261a}   /* the lane badge (#247) */
#bbody .fz{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:10px;color:var(--warn);border:1px dashed var(--warn)}   /* the freeze marker: the card keeps its column */
#bbody .it .pnd{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#0b1a12;background:var(--warn);border:1px solid var(--warn)}   /* the proved move the board read has not carried yet (#650) */
#bsp[data-m=only],#bsp[data-m=hidden]{color:var(--warn);border-color:#6b5223}   /* the two modes that leave the specs out of the way, or ask for them: the one that draws every card is the resting colour (#456) */
#bbody .pst{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;font-size:10.5px;font-weight:600;vertical-align:1px;border:1px solid;max-width:100%;overflow-wrap:anywhere}
#bbody .pst.ready{color:#3fb950;border-color:#2e6b3a;background:#12261a}#bbody .pst.running{color:#5aa9ff;border-color:#24496e;background:#0f1d2c}
#bbody .pst.changes{color:#e3b341;border-color:#6b5223;background:#261e0f}#bbody .pst.broke,#bbody .pst.conflict{color:#f85149;border-color:#6e2a27;background:#2a1212}
#bbody .pst.idle,#bbody .pst.draft{color:var(--mute);border-color:var(--line);background:#12161c}
#bbody .gstrip{display:flex;gap:4px;flex-wrap:wrap;margin-top:3px;font-size:10px;color:var(--mute)}
#bbody .gstrip.old{opacity:.45;filter:grayscale(1)}
#bbody .gc{border:1px solid var(--line);border-radius:7px;padding:0 5px;white-space:nowrap;background:#12161c}
#bbody .gc b{color:var(--fg);font-weight:600;margin-left:3px}
#bbody .gc.ok b{color:#3fb950}#bbody .gc.warn b{color:#e3b341}#bbody .gc.bad b{color:#f85149}
#bbody .gc.debt b{color:#5aa9ff}#bbody .gc.unk{border-color:#6b5223}#bbody .gc.unk b{color:#e3b341}
#bbody .it:hover{background:var(--bgh,#232b37);border-color:#3a4352}
#bbody .it .id{display:inline-block;background:#12161c;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-bottom:4px;font-size:11px;color:var(--pin);text-decoration:none;font-weight:700}
#lf{display:inline-block;position:relative;font-size:11px;font-weight:400}#lf summary{list-style:none;cursor:pointer;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:2px 8px;max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#lf summary::-webkit-details-marker{display:none}#lf.set summary{color:var(--warn);border-color:#6b5223}
#lfl{position:absolute;right:0;top:calc(100% + 4px);z-index:20;min-width:200px;max-height:320px;overflow-y:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px;box-shadow:0 6px 18px #0008}#lfl label{display:flex;gap:6px;align-items:center;padding:2px 4px;border-radius:4px;cursor:pointer;white-space:nowrap}#lfl label:hover{background:#1c222b}#lfl .lft{display:flex;gap:4px;justify-content:space-between;align-items:center;padding:0 2px 6px;margin-bottom:4px;border-bottom:1px solid var(--line)}#lfl .lft button{font:inherit;font-size:10.5px}#lfl .lft button.on{color:var(--warn);border-color:#6b5223}#bbody .it .lbs{display:block;margin-top:4px}#bbody .it .lbs span{display:inline-block;background:var(--lb,#2a303b);border:1px solid transparent;border-radius:9px;padding:0 6px;margin:0 3px 2px 0;font-size:9.5px;color:#c9d1d9}#bbody .it .lbs span.on{border-color:var(--warn);color:var(--fg)}
#bbody .it .id:hover{border-color:var(--pin)}#bbody .it .tt{display:block}
#bbody .it .pmp{display:inline-block;margin-top:4px;font:inherit;font-size:10.5px;padding:0 7px;border-radius:9px;cursor:pointer;color:var(--pin);background:#12161c;border:1px solid var(--line)}#bbody .it .pmp:hover{border-color:var(--pin)}#bbody .it .pmp:disabled{opacity:.5;cursor:default}   /* the owner's move, on a Backlog card only (#560) */
#bbody .it .prr{display:block;margin-top:2px;font-size:10.5px;color:var(--mute);max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bbody .it .blk{display:block;margin-top:4px;font-size:10.5px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#bbody .it .blk a{color:var(--pin);text-decoration:none}
#bbody .prs .it .cir{display:inline-block;margin-left:6px;font-size:9px;letter-spacing:.04em;text-transform:uppercase;color:var(--cc);border:1px solid color-mix(in srgb,var(--cc) 45%,transparent);border-radius:999px;padding:0 5px;white-space:nowrap}   /* the pull request's own CI rollup (#440) */
#bbody .prs .it .au{color:var(--mute);font-size:10.5px}   /* the author the pull request was opened by (#440) */
#bbody .prs .it .clos{display:block;margin-top:2px;font-size:10.5px;color:var(--mute)}#bbody .prs .it .clos a{color:var(--pin);text-decoration:none}   /* the issues the pull request closes (#440) */
#bbody .it.m{border-style:dashed;text-align:center;background:none;box-shadow:none}
#bbody .pad{height:6px}#bbody .sc{max-height:58vh;overflow:auto}#bbody .it .dt{float:right;color:var(--mute);font-size:11px}#bbody .it .hld{color:var(--mute);font-size:10.5px}
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
body.dockb #win{display:grid;grid-template-columns:300px 1fr;grid-template-rows:minmax(0,1fr) auto}
body.dockb #side{grid-row:1/3}body.dockb #main{grid-column:2;grid-row:1}
body.dockb #board{grid-column:2;grid-row:2;width:auto;height:320px;border-left:0;border-top:1px solid var(--line);display:flex;flex-direction:column}
body.dockb #board.off{height:34px;width:auto}body.dockb #board.off #bhead b{display:inline}body.dockb #board.off #bhead{padding:8px 10px}
body.dockb #bbody{flex:none;height:calc(100% - 41px);min-height:0;display:flex;overflow-x:auto;overflow-y:hidden}

body.dockb #bhead{position:static}
/* The board's maximize: the kanban takes the run pane's cell as well as its own, so it is read at full height. Only the two cells of the right column move — the left bar keeps its column and its rows. The run pane is a live, cross-origin dashboard: its frame is detached while the pane is out of sight (liveSync in the script), so it holds no stream, and `visibility` only hides the empty cells. The board is no longer a fixed-height row, so the grip has nothing to resize. */
body.dockb.max #win{grid-template-rows:minmax(0,1fr) 0}
body.dockb.max #board{grid-row:1/-1;height:auto;border-top:0;overflow:hidden}
body.dockb.max #main{visibility:hidden}
#grip{display:none}body.dockb #grip{display:block;height:7px;margin-bottom:-3px;cursor:ns-resize;flex:none;position:relative;z-index:3}body.dockb #grip:hover,body.dockb #grip.drag{background:var(--pin);opacity:.6}body.dragging{user-select:none;cursor:ns-resize}body.dragging iframe{pointer-events:none}body.dockb.max #grip{display:none}
/* The pip over the maximized board (#1782): a small card of the run's own dashboard, floating on the board. It is the board's own child, so `absolute` is measured against the board and never the viewport; z-index 10 keeps it above the board header (2) and the column headers and below the filter popover (20), so an open popover is never trapped behind it. In the split the window is hidden with `visibility` and holds no dashboard (its source is blank), so it opens no stream; maximizing sources it and detaches the run pane's frame. `overscroll-behavior:contain` and a non-passive wheel listener on the chrome keep a wheel over the window off the board behind it. */
#pip{position:absolute;z-index:10;display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--panel);border:1px solid var(--line);border-radius:6px;box-shadow:0 8px 22px #0009;overflow:hidden;overscroll-behavior:contain}
body:not(.dockb.max) #pip{visibility:hidden}
#piph{display:flex;align-items:center;gap:6px;padding:5px 6px 5px 9px;background:#1b212b;border-bottom:1px solid var(--line);cursor:move;touch-action:none;flex:none;font-size:11px}
#piph:hover,#piph.drag{background:#232b37}#piph b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
#piph button{font:inherit;font-size:11px;line-height:1;padding:2px 6px;cursor:pointer}
#pipb{flex:1;min-height:0;display:flex}#pipf{border:0;border-radius:0 0 5px 5px}
#pip.off #pipb{height:0;visibility:hidden}
#pipm{display:none;flex:1;padding:10px;font-size:11px;line-height:1.45;color:var(--mute)}
#pip.noframe #pipb{visibility:hidden;height:0}#pip.noframe #pipm{display:block}
body.dockb #bbody>.col{flex:1 1 0;min-width:var(--col-w);display:flex;flex-direction:column;height:100%;border-right:1px solid var(--line);min-height:0}
body.dockb .ch{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);font-weight:600;background:#1b212b;flex:none}   /* the gap is stated: without it a header that is asked to pack its spans reads `Todo0` (#1809) */
body.dockb .col>.sc{flex:1 1 0;min-height:0;max-height:none;overflow-y:auto;overscroll-behavior:contain}
.prs{background:linear-gradient(color-mix(in srgb,var(--cc) 8%,transparent),color-mix(in srgb,var(--cc) 3%,transparent))}   /* the open-PR lane wears the colour colOf gave it, faint, so a tracking lane never looks like a column of work */
#bbody .acc.prs{border-bottom:2px solid var(--cc);margin-bottom:4px}
#bbody .prs>.ah,#bbody .prs>.ch{background:color-mix(in srgb,var(--cc) 12%,#0f1115);color:var(--cc);border-bottom:1px solid color-mix(in srgb,var(--cc) 40%,transparent);letter-spacing:.04em;text-transform:uppercase}
#bbody .prs .it{border:1px solid color-mix(in srgb,var(--cc) 33%,transparent);border-left:4px solid var(--cc);background:color-mix(in srgb,var(--cc) 8%,#10141a);border-radius:10px}
#bbody .prs .it .id{background:color-mix(in srgb,var(--cc) 13%,transparent);border-color:var(--cc);color:var(--cc)}
#bbody .prs .it .rpo{font-size:9px;letter-spacing:.04em;text-transform:uppercase;color:var(--cc);border:1px solid color-mix(in srgb,var(--cc) 45%,transparent);border-radius:999px;padding:0 5px;white-space:nowrap}   /* the tag naming the card's repository (#1820) */
#bbody .perr{margin:0 0 6px;padding:4px 6px;font-size:10px;line-height:1.35;border-radius:8px;overflow-wrap:anywhere;color:#ffb4b4;background:color-mix(in srgb,#ff6b6b 12%,#10141a);border:1px solid color-mix(in srgb,#ff6b6b 40%,transparent)}   /* the unreadable repository, on the column (#1820) */
body.dockb #bbody>.col.prs{box-shadow:inset 0 2px 0 var(--cc);flex:1.15 1 0}
#bcases:empty{display:none}#bcases{display:flex;flex-wrap:wrap;gap:4px 10px;padding:3px 8px;font-size:10px;border-bottom:1px solid var(--line)}#bcases .tcrow{display:flex;gap:6px;align-items:baseline}#bcases .tcrow.fail .tr{color:#ff6b6b}#bcases .tcrow.pass .tr{color:#a3e635}   /* a test case row, beside the axis (#306) */
body.dockb #bbody>.col.lane+.col:not(.lane){border-left:3px double var(--line)}#bbody .acc.lane+.acc:not(.lane){margin-top:10px}   /* the PR lanes sit ahead of the axis: a rule marks where the columns start (#247) */
body.dockb #bbody>.col.prcol{background:linear-gradient(color-mix(in srgb,var(--cc) 8%,transparent),color-mix(in srgb,var(--cc) 3%,transparent));box-shadow:inset 0 2px 0 var(--cc);flex:1.15 1 0}
body.dockb .sec{display:flex;flex-direction:column;min-height:0;flex:none}body.dockb .sec.open{flex:1 1 0}body.dockb .sec:not(.open)>.sc{display:none}
body.dockb .sec>.ch{cursor:pointer;border-top:1px solid #2dd4bf33}body.dockb .sec:first-child>.ch{border-top:0}
.eye{cursor:pointer;margin-left:8px;opacity:.55;display:inline-flex;vertical-align:middle}.eye:hover{opacity:1}#bbody .acc.hidcol>.ah{opacity:.45}#bbody .acc:not(.open)>.sc,#bbody .acc:not(.open)>.pad{display:none}
.hidbar{flex:none;align-self:stretch;padding:8px 10px;color:var(--mute);font-size:11px;display:flex;flex-direction:column;gap:4px;border-right:1px solid var(--line);min-width:120px;max-width:120px;min-height:0;overflow:hidden}   /* the hidden: strip, the docked row's last child: align-self:stretch is what lets it be divided by height. Both ends of the width are pinned and the strip clips, so a long user-created column name cannot widen the column now that the label sits on its own line (#1792) */
.hidbar .chip{flex:1 1 0;min-height:0;overflow:hidden;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:2px 8px;background:#2a303b;border:1px solid #353c49;border-radius:9px;cursor:pointer;color:#939cab}   /* one full-height tile per hidden column: flex:1 1 0 splits the strip's height equally between however many rows there are, so two rows are half each and eight are an eighth (#1792). The chip's own colour is the one #1809's quiet lines are measured against: the name inside the tile wears the caption's --mute (#1793), so the chip keeps the fill and the text colour the two kinds are told apart by */
.hidbar .chip:hover{background:#333a47;border-color:#414957}
.hidbar .chip .lbl{flex:0 1 auto;min-height:0;overflow:hidden;max-width:100%;font-size:10px;line-height:11px;color:var(--mute);text-overflow:ellipsis;white-space:nowrap}   /* the name is the tile's caption: it ellipsizes, it never wraps and its font never shrinks. It is flex:0 1 auto and the number is flex:none, so when a row is shorter than its content the name is the one clipped away */
.hidbar .chip b{flex:none;font-size:20px;line-height:20px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--pin)}   /* the number is what the tile is for, and it is never the element that gets cut. --pin is the fallback for a column the table below does not name. These rules are scoped to .hidbar on purpose: a bare .chip would also repaint the sidebar's .sub .chip */
/* The strip's own mapping from column to category, one rule per category: the colour says what the count means and
   never how big it is, because magnitude would light up the two archival piles. The hexes are #1793's, under the
   names the table gives them; the open branch lane's #5aa9ff is --pin, so it is written as the token it is. */
/*HID_CHIP_RULES*/
.hidbar .hidsep{border-top:1px solid var(--line);margin:3px 0 1px}   /* the divider the auto-hidden columns sit under: below everything the user hid (#1809) */
.hidbar .hidline{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:0 2px 0 6px;border-left:2px solid var(--cc,var(--mute));color:#6f7987;font-weight:400}   /* a column hidden because it is empty, not because the user asked: no chip fill and a dimmer text than the chip's #939cab, with a 2px tick in the column's own colour -- the value the header's dot carries (#1793) -- so the two kinds are never confusable at a glance (#1809) */
iframe{flex:1;width:100%;border:1px solid var(--line);background:#0f1115;min-height:0}
#foot{flex:none;display:flex;gap:10px;align-items:center;padding:5px 10px;background:var(--panel);border-top:1px solid var(--line);font-size:11px;color:var(--mute);font-variant-numeric:tabular-nums}
#foot b{color:var(--fg);font-weight:600}#foot.warn{border-top-color:#6b5223}#foot.warn b{color:var(--warn)}#foot.warn b.ldred{color:var(--err)}#foot b.ldred{color:var(--err)}
#ldfacts{margin-left:20px;white-space:nowrap}#ldfacts .bad{color:var(--warn)}#ldfacts .sep{margin:0 3px;color:#4b5563}
.load{display:inline-block;margin-left:4px;padding:0 5px;border-radius:9px;background:#3a2a12;color:var(--warn);font-size:9.5px;border:1px solid #6b5223}
</style>
<div id=win>
<div id=side>
<details id=plans class=sp><summary>subscriptions<span class=sr id=plsr></span></summary><div class=sb><div id=subs></div></div></details>
<details id=mdl class=sp><summary>squad models<span class=sr id=mdsr></span></summary><div class=sb id=mbody></div></details>
<details id=stats class=sp><summary>cost &amp; usage<span class=sr id=stsr></span></summary><div class=sb><h4>Spend <span class=row id=src title="all = plans + API. plans = Claude, Grok, GPT and Gemini. API = every other model (deepseek…) and calls that name no model. Figures at API list prices (API-equivalent)."></span></h4><div class=tot id=tot></div><div class=cash id=cash></div><h4>Spend over time <span class=row id=per></span></h4><div id=chart></div><div id=kpi></div><div id=open></div><h4>Tokens by model <small class=m id=piet>all time</small></h4><div id=pie></div></div></details>
<div id=list></div>
</div>
<div id=main></div>
<div id=pop><div id=pbox></div></div>
<div id=board><div id=grip title="drag to resize"></div><div id=bhead><b>TASKS <span id=bt class=m></span></b><span><details id=lf><summary title="filter cards by label">labels</summary><div id=lfl></div></details> <button id=bsp data-m=all title="the Specs filter holds a mode: every card, only the spec-labelled cards, or every card but those. A press cycles the three, and the label states the one it holds.">Specs: all</button> <button id=bz title="maximize the board over the run pane" aria-pressed=false></button> <button id=bx title="collapse" aria-pressed=false>&raquo;</button></span></div><div id="pip"><div id="piph" tabindex="0" title="drag to move the window; arrow keys move it 16px (64 with shift); Esc collapses it"><b id="pipt"></b><button id=pipx title="collapse the window to its header">&#8211;</button><button id=pipo title="close the window and restore the split">&#215;</button></div><div id="pipb"><iframe id="pipf" title="one card of the run panel"></iframe></div><div id="pipm"></div></div><div id=bcases></div><div id=bbody></div></div>
</div>
<div id=foot title="machine load: read in the hub process every few seconds; ? means the hub cannot see that source"><span>load</span><b id=ldline>CPU ? · RAM ? · GPU ?</b><span id=ldfacts title="the squad's own state: health, progress, occupancy, quotas, subscriptions"></span></div>
<script>
const LAYOUTS={1:[1,1],2:[2,1],3:[3,1],4:[2,2],6:[3,2]};
const $=s=>document.querySelector(s),ls=JSON.parse(localStorage.hub||"{}");
let allRuns=[],runs=[],q=ls.q||"",per=ls.per||"day",src=ls.src||"both",N=1,pins=new Set((ls.pins||[]).slice(0,1)),slots=[],frames=new Map();
const save=()=>localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,src});
const usd=v=>v==null?"—":v>=100?"$"+Math.round(v):"$"+v.toFixed(2);
const usdc=v=>v==null?"—":"$"+(+v).toFixed(2);   // plan prices carry cents, so a month's cash total keeps them: usd would round $282.23 to $282
const age=s=>s<90?s+"s":s<5400?Math.round(s/60)+"m":Math.round(s/3600)+"h";
// a run's name says what it is for: "fix PR #1202 · #1178", "implement #1179, #1182", "dispatch #31, #25"; the hub computes
// that string (hub.run_label) and the run id moves to the detail line -- the page renders the value, it holds no rule of its own
const label=r=>r.label||("run "+String(r.id||"").slice(-6));
// where the label's issue came from, or why the run has none: the request names it; the transcript is only the fallback when the request is missing
const labelTip=r=>{if((r.issues||[]).length)return r.issue_src==="transcript"?"issue read from the transcript: request.txt is missing":"issue named by the run's request";
  return r.issue_why==="request-missing"?"no issue: the request is missing (no request.txt in the run folder)":r.issue_why==="request-no-issue"?"no issue: the request names no issue":""};
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
const GROUPS=[["running","WORKERS"],["1h","≤ 1 h"],["2h","1–2 h"],["4h","2–4 h"],["8h","4–8 h"],["24h","8–24 h"],["d","24 h+ (days)"]];
const CAPS=__CAPS__;
const LOAD_MS=Number("__LOADMS__")||5000;   // the sampler's cadence, substituted when the page is served; the fallback keeps a slice of this script evaluable on its own
function loadMark(r){return r.cpu?`<span class=load title="this run owns ${r.cpu}% of this machine's CPU right now">cpu ${r.cpu}%</span>`:""}   // the card answers "because of whom?"; the strip answers "is it saturated?"
// The strip's cells (#1816). One line of text could not carry a per-attribute colour, so the page builds the three cells
// from the numbers `/api/load` already serves and paints each on its own. These mirror the server's `pct_cell` — the same
// order, the same ` · `, the same rounding — so the reading still reads exactly as it read; only a cell at the machine's
// ceiling is red, and that is `LOAD_ERR`, substituted when the page is served. A source the hub cannot see stays `?`:
// a missing reading is not a high one. Red sits above the older, machine-wide amber of `#foot.warn`, and never replaces it.
const LOAD_ERR=Number("__LOADERR__");
const MODELID_SRC="__MODELID__";   // the one model-id shape, substituted when the page is served (#221): the Add boxes test it, never a copy of it
const MODELRE=new RegExp("^"+MODELID_SRC+"$");   // an unsubstituted page still evaluates: the placeholder is a string, never a bare name
function ldNum(v){if(v===null||v===undefined||v===""||typeof v==="boolean")return null;const f=Number(v);return(f!==f||f<0)?null:Math.min(f,100)}
function ldRound(f){const fl=Math.floor(f),fr=f-fl;return fr>.5?fl+1:fr<.5?fl:fl+(fl%2)}   // half to even, exactly as `:.0f` rounds
function ldCell(v){const f=ldNum(v);return{t:f===null?"?":ldRound(f)+"%",red:f!==null&&f>=LOAD_ERR}}
function ldCells(d){return[["CPU","cpu"],["RAM","ram"],["GPU","gpu"]].map(p=>{const c=ldCell((d||{})[p[1]]);return{k:p[0],t:c.t,red:c.red}})}
function ldPaint(l,d){l.textContent="";ldCells(d).forEach((c,i)=>{if(i)l.appendChild(document.createTextNode(" · "));const b=document.createElement("b");if(c.red)b.className="ldred";b.textContent=c.k+" "+c.t;l.appendChild(b)})}
// The facts after the cells, served by the same tick. On a window too narrow for the whole row the trailing facts drop
// (6 -> 5 -> 4) exactly as they are — the line never wraps and no number is cut into a different one. The last block is
// never dropped: a window too narrow even for it cuts it at the budget instead, because a bar with a fact to carry never
// draws the machine alone, and the first block is the one that matters most (#446). The budget is in characters of the
// font the cells beside them are drawn in, measured off the cells themselves.
function footFit(items,cols){const k=(items||[]).slice();while(k.length>1&&k.map(i=>i.text).join(" · ").length>cols)k.pop();if(k.length&&k[0].text.length>cols)k[0]={...k[0],text:k[0].text.slice(0,Math.max(0,cols-1)).trimEnd()+"…",parts:null};return k}
function footText(items,cols){return footFit(items,cols).map(i=>i.text).join(" · ")}
// The colour of each fact (#1843). A block wears its own palette colour, so the six blocks are told apart without one
// character more; a token whose own reading earned one of the three tones the load strip already paints (#1816) keeps
// it ON that colour, so a block colour never says the value is fine, and `neutral` is the bar's own plain colour for a
// value the hub could not verify. The parts of a block keep the line's own separator between them, the text the hub
// serves already carries it. The tone is decided where the number is — the hub serves it — never here:
const FTONE={ok:"var(--ok)",warn:"var(--warn)",err:"var(--err)",neutral:"var(--mute)"};
function footParts(i){return (i.parts||[{text:i.text}]).map(p=>{const t=FTONE[p.tone]?`<span style="color:${FTONE[p.tone]}">${esc(p.text)}</span>`:esc(p.text);return p.tip?`<span title="${esc(p.tip)}">${t}</span>`:t}).join(" · ")}
function footItem(i){const t=footParts(i),s=i.bad?`<span class=bad>${t}</span>`:`<span style="color:var(--${i.color})">${t}</span>`;return i.tip?`<span title="${esc(i.tip)}">${s}</span>`:s}
// The facts' own width budget: what the bar has left after the `load` label, the three cells, their gaps and the facts'
// own margin. The line drops whole blocks when it is too narrow, so a budget that promises more room than the bar has
// would let one block run past the window instead of dropping.
function footCols(){const f=$("#foot"),l=$("#ldline"),x=$("#ldfacts");if(!f||!l||!f.clientWidth)return 1e9;
 const cs=getComputedStyle(f),xcs=x?getComputedStyle(x):null,gap=parseFloat(cs.columnGap||cs.gap||"0")||0;
 let used=0;for(const c of f.children)if(c!==x)used+=c.getBoundingClientRect().width;
 const avail=f.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight)-used-gap*Math.max(f.children.length-1,0)
   -(xcs?parseFloat(xcs.marginLeft)||0:0)-8;
 const w=l.getBoundingClientRect().width,n=Math.max((l.textContent||"").length,1);
 return Math.max(0,Math.floor(avail*n/w))}
// The row: the blocks the bar has room for, joined by a separator the page DRAWS — a dimmed span with its own margin,
// never the ` · ` a block carries between its own parts. The two were the same glyph, so blocks ran together; the drawn
// one is a different thing to the eye, and it is narrower than the ` · ` the fit counts, so the row never overflows.
const FOOT_SEP_HTML='<span class=sep>&middot;</span>';
function footDraw(d){const l=$("#ldline"),f=$("#ldfacts");if(l)ldPaint(l,d);
 if(f)f.innerHTML=footFit(d.facts,footCols()).map(footItem).join(FOOT_SEP_HTML)}
async function loadPoll(){try{const d=await (await fetch("/api/load")).json(),fo=$("#foot");
 footDraw(d);if(fo)fo.classList.toggle("warn",!!d.warn)}catch(e){}}
function groupHeader(k,rows){const n=rows.filter(r=>r.kind!=="childcc").length,spend=rows.reduce((a,r)=>a+r.spend,0),agents=rows.reduce((a,r)=>a+(r.agents||0),0);let title=usd(spend);let text=n+" · "+title;if(k==="running"&&CAPS&&CAPS.per_run&&CAPS.per_run.label)title+=" · "+CAPS.per_run.label;if(k==="running")text=(CAPS&&CAPS.runs&&CAPS.children)?`RUNS ${n}/${CAPS.runs} · AGENTS ${agents}/${CAPS.children}`+((CAPS.waiting||[]).length?" · waiting: "+CAPS.waiting.map(w=>`${w.holder} (${w.ceiling})`).join(", "):""):`${n} · ${agents}`;return {text,title}}
// The open groups belong to the user: a click on a header toggles that one group, any number stay open, and the set is stored
// beside the pins. `null` means nothing is stored yet — the first draw opens the first filled group once, and only then, so a
// run appearing, ending or moving to another age bucket can never move what the user opened.
let openGroups=Array.isArray(ls.groups)?new Set(ls.groups.filter(k=>GROUPS.some(g=>g[0]===k))):null;
const saveGroups=()=>{ls.groups=[...openGroups];save()};
// runs of one issue share the issue label ("#1281"), and today three runs can carry it at once: the list and the panel header
// then read the same twice. Disambiguate only the labels that repeat, with the run's start time (run-YYYYMMDD-HHMMSS-xxxx).
const short=r=>{const m=/(\d{8})-(\d{2})(\d{2})\d{2}/.exec(r.id||"");return m?m[2]+":"+m[3]:""};   // run-…-HHMMSS-xxxx and test-<n>-YYYYMMDD-HHMMSS both carry it
const name=r=>{const l=label(r);return runs.filter(x=>label(x)===l).length>1&&short(r)?`${l} · ${short(r)}`:l};
const groupOf=r=>{if(r.rank===0)return"running";const h=r.age/3600;return h<=1?"1h":h<=2?"2h":h<=4?"4h":h<=8?"8h":h<=24?"24h":"d"};
function drawList(){
  const shown=new Set(slots),by={};allRuns.filter(r=>r.kind==="childcc").concat(runs).forEach(r=>(by[groupOf(r)]=by[groupOf(r)]||[]).push(r));
  if(openGroups===null){const first=GROUPS.find(g=>by[g[0]]);openGroups=new Set(first?[first[0]]:[]);saveGroups()}   // nothing stored yet: the first filled group opens once
  const filled=GROUPS;   // the whole age ladder stays visible, so a band that holds no run still draws its header, and an open group that emptied keeps its header and shows no rows
  const ccRow=r=>`<div class="r cc" data-id="${r.id}"><div><span class="b ${r.state}"${r.tip?` title="${esc(r.tip)}"`:""}>${esc(r.badge)}</span> <b>${esc(r.label)}</b>${r.sub?`<div class=st title="${esc(r.sub)}">${esc(r.sub)}</div>`:""}${(r.lines||[]).map(l=>`<div class=st>${esc(l)}</div>`).join("")}${r.live?`<div class=m>age ${age(r.age)}</div>`:""}</div></div>`;
  const row=r=>r.kind==="childcc"?ccRow(r):`<div class="r ${shown.has(r.id)?"show":""} ${pins.has(r.id)?"pinned":""}" data-id="${r.id}"><div>${r.kind==="test"?`<span class=kd title="GUI test run">🧪</span>`:""}<span class="b ${r.state}"${r.tip?` title="${esc(r.tip)}"`:""}>${esc(r.badge||r.state)}</span> <b title="${esc(labelTip(r))}">${esc(name(r))}</b>${loadMark(r)}${!(r.issues||[]).length&&r.issue_why&&!r.fix&&!r.url?`<div class=st>${esc(labelTip(r))}</div>`:""}${sub(r)?`<div class=st title="${esc(sub(r))}">${esc(sub(r))}</div>`:""}<div class=m>${r.share?`<span class="sh${r.share.pct===100?" done":""}" title="the newest gate that counted ran ${r.share.total} tests, ${r.share.passed} passed">${r.share.passed}/${r.share.total} · ${r.share.pct}%</span> · `:""}${usd(r.spend)} · ${Math.round(r.dur)}m · ${age(r.age)} ago · ${r.id.slice(-6)} · :${r.port}</div></div><span class=pn title="shown in the panel">${pins.has(r.id)?"●":"○"}</span></div>`;
  $("#list").innerHTML=filled.map(([k,t])=>{const rows=by[k]||[],h=groupHeader(k,rows);return `<div class="gh ${openGroups.has(k)?"op":""}" data-g="${k}" tabindex=0>${t}<span class=sr title="${esc(h.title)}">${h.text}</span></div>${openGroups.has(k)?rows.map(row).join(""):""}`}).join("")||"<div class=m style=padding:10px>no live dashboards</div>";
  document.querySelectorAll(".gh").forEach(e=>e.onclick=()=>{const k=e.dataset.g;   // the header is the only writer: a click toggles this group and nothing else
    if(openGroups.has(k))openGroups.delete(k);else openGroups.add(k);saveGroups();drawList()});
  document.querySelectorAll(".r:not(.cc)").forEach(e=>e.onclick=()=>{const id=e.dataset.id;
    if(pins.has(id))pins.delete(id);else{if(N===1)pins.clear();pins.add(id);focus=id}
    save();refresh(true)});
}
let built="",focus=null;
/*PANES_JS*/
// A pane is a cell: the focused run's pane holds the one live dashboard frame, every other pane shows the state this page already polled.
// A frame is dropped from the DOM when its pane stops following (its stream closes with it); it is never hidden (#34).
const snapText=r=>r.state+" · "+usd(r.spend)+" · "+age(r.age)+" ago · :"+r.port;
const paintCell=(cell,r,id,live)=>{cell.head.innerHTML=`<span>${name(r)} · ${r.state}</span><span>${pins.has(id)?"pinned":"auto"}${id===live?" · live":""}</span>`;
  if(cell.snap)cell.snap.textContent=snapText(r)};
function drawMain(force){
  const [c,rw]=LAYOUTS[N],live=embedPlan(slots,focus,!!bo.max).pane,key=N+":"+slots.join(",")+"|"+live;
  const main=$("#main");main.style.gridTemplateColumns=`repeat(${c},1fr)`;main.style.gridTemplateRows=`repeat(${rw},1fr)`;
  const byId=new Map(runs.map(r=>[r.id,r]));
  if(key!==built){built=key;
    for(const id of [...frames.keys()])if(id!==live)frames.delete(id);
    main.innerHTML=slots.length?"":"<div class=m style=\"padding:20px\">"+PIP_NORUNS+"</div>";
    slots.forEach(id=>{const r=byId.get(id);
      const cell=document.createElement("div");cell.className="cell";cell.dataset.id=id;cell.head=document.createElement("small");cell.appendChild(cell.head);
      if(id===live){let f=frames.get(id);
        if(!f||f.dataset.port!==(r.port||r.url)){f=document.createElement("iframe");f.src=r.url||"http://127.0.0.1:"+r.port+"/";f.dataset.port=r.port;frames.set(id,f)}
        cell.appendChild(f)}
      else if(live){cell.snap=document.createElement("div");cell.snap.className="snap";cell.snap.dataset.snap=id;cell.appendChild(cell.snap);
        cell.onclick=()=>{focus=id;drawMain()}}   // pressing a pane moves the one live frame to it
      main.appendChild(cell)})}
  for(const cell of main.children){const r=byId.get(cell.dataset.id);if(r)paintCell(cell,r,cell.dataset.id,live)}
}
function liveSync(){drawMain();pipDraw()}   // the run pane went out of sight or came back: the frame follows the plan
function refresh(){plan();drawList();liveSync()}
function applyFilter(){runs=allRuns.filter(r=>r.kind!=="childcc")}   // the childcc pass is drawn in the WORKERS group, never a pane (#598)
let known=null;   // run ids already seen: a run that appears running after the first poll is selected on its own (pinned, its group opened)
function selectNew(){const ids=allRuns.filter(r=>r.kind!=="childcc").map(r=>r.id);if(known===null){known=new Set(ids);return}
  const fresh=allRuns.filter(r=>r.kind!=="childcc"&&!known.has(r.id)&&r.rank===0);ids.forEach(i=>known.add(i));
  if(!fresh.length)return;const id=fresh.sort((x,y)=>x.age-y.age)[0].id;
  if(N===1)pins.clear();else if(pins.size>=N)pins.delete([...pins][0]);pins.add(id);save()}   // a run starting moves the pins, never the accordions
async function poll(){try{allRuns=await (await fetch("/api/runs")).json();selectNew();applyFilter();refresh()}catch(e){}}
function bars(rows,W=280,H=40){if(!rows.length)return"";const mx=Math.max(...rows.map(r=>r.spend),0.01),bw=W/rows.length,pad=12,xl=t=>String(t).replace(/^\d{4}-/,"");
  return `<svg viewBox="0 0 ${W} ${H+pad+14}">`
    +`<line x1="0" y1="${pad}" x2="${W}" y2="${pad}" stroke="#3ecf8e" stroke-dasharray="3 2" stroke-width="1"/>`
    +`<text x="${W}" y="${pad-3}" fill="#3ecf8e" font-size="9" text-anchor="end">max ${usd(mx)}</text>`
    +rows.map((r,i)=>`<rect x="${i*bw+1}" y="${pad+H-r.spend/mx*H}" width="${Math.max(bw-2,1)}" height="${r.spend/mx*H}" fill="${r.spend>=mx?"#3ecf8e":"#5aa9ff"}"><title>${xl(r.t)}: ${usd(r.spend)} · ${r.runs} runs</title></rect>`).join("")
    +`<text x="0" y="${H+pad+12}" fill="#8a93a3" font-size="9">${xl(rows[0].t)}</text>`
    +`<text x="${W}" y="${H+pad+12}" fill="#8a93a3" font-size="9" text-anchor="end">now</text></svg>`}
function kr(label,sub,v,unit,base,lowerBetter=true){if(v==null)return`<div class=kr><span class=l>${label}${sub?`<small>${sub}</small>`:""}</span><b>—</b></div>`;
  const u=unit?String(unit).trim():"";
  let chip="";if(base){const d=Math.round((v-base)/base*100),good=lowerBetter?d<0:d>0;
    const pct=`<span class="${good?"good":"bad"}">${Math.abs(d)}% ${lowerBetter?(d<0?"faster":"slower"):(d>0?"more":"fewer")}</span>`;
    chip=d===0?`<span class=d>was ${base}${u?" "+u:""}</span>`:`<span class=d>was ${base}${u?" "+u:""} · ${pct}</span>`}
  return `<div class=kr><span class=l>${label}${sub?`<small>${sub}</small>`:""}</span><b${v===0?" class=m":""}>${v}${u?`<small>${u}</small>`:""}</b>${chip}</div>`}
function openRows(o){return `<h4>Open branches <small>oldest first</small></h4>`
  +(o&&o.length?o.map((x,i)=>`<div class=kr><span class=l>#${x.n} · <b${x.min>=30?" class=w":""}>${x.min}</b> min${i===0?" (warns at 30)":""}</span></div>`).join("")
   :`<div class=kr><span class=l>none open</span></div>`)}
let mdl=null,mdlSrv="",mdlMsg="";const MLAB={coding_models:"Coding",review_models:"Review",vision_models:"Vision"},MDESC={coding_models:"Writes the code in squad runs.",review_models:"Reviews each PR independently.",vision_models:"Looks at screenshots and images."},EFF=["low","medium","high","max"];
function mdlEff(k,m){return (mdl.efforts&&mdl.efforts[k]&&mdl.efforts[k][m])||""}
function mdlSetEff(k,m,v){mdl.efforts=mdl.efforts||{};mdl.efforts[k]=mdl.efforts[k]||{};if(EFF.includes(v)&&v!=="medium")mdl.efforts[k][m]=v;else delete mdl.efforts[k][m]}
const HRP=["22-07","07-22","20-08","18-06"],HRX=/^([01]\d|2[0-3])-([01]\d|2[0-3])$/;
function mdlHrs(k,m){return (mdl.hours&&mdl.hours[k]&&mdl.hours[k][m])||""}
function mdlSetHrs(k,m,v){v=(v||"").trim();mdl.hours=mdl.hours||{};mdl.hours[k]=mdl.hours[k]||{};if(HRX.test(v)&&v.slice(0,2)!==v.slice(3))mdl.hours[k][m]=v;else delete mdl.hours[k][m]}
/* The row's switch (#1819): a state, not an action. Out of the chain the model keeps its place, its rank, its effort
   and its hrs, and every consumer of the chain skips it exactly like a model outside its window. */
function mdlOff(k,m){return !!(mdl.off&&mdl.off[k]&&mdl.off[k][m])}
function mdlSetOff(k,m,v){mdl.off=mdl.off||{};mdl.off[k]=mdl.off[k]||{};if(v)mdl.off[k][m]=true;else delete mdl.off[k][m]}
/* What a model box shows is the hub's answer, model_box() (#677, ADR-0137): the registry label and its route, or
   "unregistered". The page holds no provider rule. An id just typed into an Add box has no answer until the save. */
const MBOX0={llm:null,route:null,registered:null,text:"resolved on save"};
function mdlBox(k,m){const b=mdl.boxes||{};return (b[k]||{})[m]||(b.known||{})[m]||MBOX0}
function mdlName(x,m){return x.llm?`${esc(x.llm)} <span class=mi>${esc(m)}</span>`:`${esc(m)}`}
function mdlRoute(x,dflt){return x.registered===false?`<span class=ur>unregistered</span>`:esc(x.route||(x.registered?dflt:x.text))}
/* The router's own chain (#95): the dispatch envelope of the run graph. It is a queue like the role chains, so it
   reads the same way -- rank, ↑ ↓, ✕, Add -- but it is graph data: it lives in the run graph, never in models.json
   (ADR-0106), and the Save writes it there and restamps the record the graph ships beside. */
function mdlRouter(){const ids=mdl.router_models||[],e=mdl.envelope||{};
 const rk=i=>i?"fallback "+i:"primary";
 return `<div class=mc><div class=mt>Router</div><div class=mx>The models the router may ask a child for, in order, from the run graph${e.graph?` <code>${esc(e.graph)}</code>`:""}${e.node?` (<code>${esc(e.node)}</code>)`:""}. A request outside this list is refused whole; the first entry is the model a child takes when it names none.</div>`
 +ids.map((m,i)=>{const x=mdlBox("router_models",m);return`<div class="mr${i?"":" p"}"><span class=rk title="${rk(i)}">${i+1}</span><span class=mn title="${esc(m)} · ${esc(x.text)}">${mdlName(x,m)}</span><span class=ct><span class=pv title="${rk(i)} · ${esc(x.text)}">${mdlRoute(x,rk(i))}</span><span class=mb><button data-rv="${i}|-1" ${i?"":"disabled"} title="move up">↑</button><button data-rv="${i}|1" ${i<ids.length-1?"":"disabled"} title="move down">↓</button><button class=x data-rrm="${i}" ${ids.length>1?"":"disabled"} title="remove from the envelope">✕</button></span></span></div>`}).join("")
 +`<div class=madd><input list=mkr data-radd placeholder="add a model id the gateway serves…"><button data-raddb="router">Add</button></div>`
 +`<div class=mx>efforts ${(e.efforts||[]).map(x=>`<code>${esc(x)}</code>`).join(" ")||"—"} · workspaces ${(e.workspaces||[]).map(x=>`<code>${esc(x)}</code>`).join(" ")||"—"} · children ≤ ${e.max_children??"–"} · parallel ≤ ${e.max_parallel??"–"} · depth ≤ ${e.max_depth??"–"}</div></div>`}
function mdlRender(){const b=$("#mbody");if(!mdl){b.innerHTML="<span class=m>loading…</span>";return}
 {const lc=mdl.last_coding||(mdl.coding_models||[])[0]||"",x=lc?mdlBox("coding_models",lc):MBOX0;$("#mdsr").textContent=x.llm?`${x.llm} · ${lc}`:lc}   // the model the last run coded with, not the chain's head
 const dirty=JSON.stringify(mdl)!==mdlSrv,known=mdl.known||[],lbl=m=>(mdl.labels||{})[m]||"";   // the registry label: display only, the saved value stays the id (#672)
 const ch=mdl.chain||{},chOut=ch.out||[],chBan=ch.banner?`<div class="mx err" role=alert>${esc(ch.banner)}</div>`:(ch.notice?`<div class=mx>${esc(ch.notice)}</div>`:"");
 b.innerHTML=chBan+`<datalist id=mk>${known.map(m=>`<option value="${esc(m)}" label="${esc(lbl(m))}">`).join("")}</datalist><datalist id=mkr>${(mdl.router_known||[]).map(m=>`<option value="${esc(m)}" label="${esc(lbl(m))}">`).join("")}</datalist><datalist id=hrp>${HRP.map(v=>`<option value="${v}">`).join("")}</datalist>`+Object.keys(MLAB).map(k=>`<div class=mc><div class=mt>${MLAB[k]}</div><div class=mx>${MDESC[k]} Tried top to bottom; the first healthy model wins, and a model switched off is skipped like one outside its hrs window.</div>`+mdl[k].map((m,i)=>{const ef=mdlEff(k,m),hr=mdlHrs(k,m),x=mdlBox(k,m),of=mdlOff(k,m),bl=((mdl.blocked||{})[k]||[]).includes(m),rk=i?"fallback "+i:"primary";return`<div class="mr${i?"":" p"}${of?" off":""}"><span class=rk title="${rk}${of?" · switched off":""}">${i+1}</span><span class=mn title="${esc(m)} · ${esc(x.text)}">${mdlName(x,m)}</span><span class=ct><span class=pv title="${rk} · ${esc(x.text)}">${mdlRoute(x,rk)}${of?" · out of the chain":""}</span>${k==="review_models"&&chOut.find(o=>o.model===m&&o.until_text)?`<span class=bk title="${esc(chOut.find(o=>o.model===m).reason)}">${esc(chOut.find(o=>o.model===m).reason)}, resets ${esc(chOut.find(o=>o.model===m).until_text)}</span>`:""}${bl?`<span class=bk title="the router envelope does not allow ${esc(m)}: a request naming it is refused whole, so the router picks an allowed model instead. Add it to the Router queue or take it out of this chain">blocked by envelope</span>`:""}<span class=el>effort</span><select class="ef${ef?" set":""}" data-ef="${k}|${esc(m)}" title="effort for this model (medium is the default)">${EFF.map(e=>`<option value="${e==="medium"?"":e}"${(ef||"medium")===e?" selected":""}>${e}</option>`).join("")}</select><span class=el>hrs</span><input class="hr${hr?" set":""}" list=hrp data-hr="${k}|${esc(m)}" value="${esc(hr)}" placeholder="always" title="local hours this model may run (HH-HH, wraps midnight; empty = always)"><span class=mb><button data-mv="${k}|${i}|-1" ${i?"":"disabled"} title="move up">↑</button><button data-mv="${k}|${i}|1" ${i<mdl[k].length-1?"":"disabled"} title="move down">↓</button><button class="sw${of?" off":""}" data-sw="${k}|${esc(m)}" aria-pressed="${of}" title="${of?"out of the chain — it never runs; its rank, effort and hrs are kept, press to put it back":"in the chain — press to take it out without removing it: it keeps its rank, its effort and its hrs"}">${of?"off":"on"}</button><button class=x data-rm="${k}|${i}" ${mdl[k].length>1?"":"disabled"} title="remove from chain">✕</button></span></span></div>`}).join("")+`<div class=madd><input list=mk data-add="${k}" placeholder="add a model id…"><button data-addb="${k}">Add</button></div></div>`).join("")
 +mdlRouter()
 +`<div class=mx><b>effort</b> · <b>hrs</b> local time <code>HH-HH</code>, wraps midnight, empty = always · <b>switch</b> <code>off</code> skips a model and keeps its place · <b>✕</b> deletes the row. A coding row must name an id the dispatch envelope allows.</div>`
 +`<div class="msave${dirty?" dirty":""}"><button id=msv ${dirty?"":"disabled"}>Save</button><button id=mrv ${dirty?"":"disabled"}>Revert</button><span class="st${mdlMsg.startsWith("!")?" err":mdlMsg?" okm":""}">${mdlMsg?esc(mdlMsg.replace(/^!/,"")):(dirty?"<span class=dot></span>unsaved changes":"saved in models.json")}</span></div>`}
async function mdlLoad(){try{mdl=await (await fetch("/api/models")).json();mdlSrv=JSON.stringify(mdl);mdlRender()}catch(e){}}
$("#mbody").addEventListener("change",e=>{const t=e.target;if(!mdl)return;mdlMsg="";if(t.dataset.ef){const[k,m]=t.dataset.ef.split("|");mdlSetEff(k,m,t.value)}
 else if(t.dataset.hr){const[k,m]=t.dataset.hr.split("|");mdlSetHrs(k,m,t.value)}
 else return;mdlRender()});
$("#mbody").addEventListener("click",async e=>{const t=e.target.closest("button");if(!t||!mdl)return;mdlMsg="";
 if(t.dataset.mv){const[k,i,d]=t.dataset.mv.split("|"),a=mdl[k],j=+i+ +d;[a[i],a[j]]=[a[j],a[i]]}
 else if(t.dataset.sw){const[k,m]=t.dataset.sw.split("|");mdlSetOff(k,m,!mdlOff(k,m))}
 else if(t.dataset.rm){const[k,i]=t.dataset.rm.split("|");const[m]=mdl[k].splice(+i,1);mdlSetEff(k,m,"");mdlSetOff(k,m,false)}
 else if(t.dataset.rv){const[i,d]=t.dataset.rv.split("|"),a=mdl.router_models,j=+i+ +d;[a[i],a[j]]=[a[j],a[i]]}
 else if(t.dataset.rrm){mdl.router_models.splice(+t.dataset.rrm,1)}
 else if(t.dataset.raddb){const v=t.parentElement.querySelector("input").value.trim();if(!MODELRE.test(v))mdlMsg="!invalid model id";else if(mdl.router_models.includes(v))mdlMsg="!already in the envelope";else mdl.router_models.push(v)}
 else if(t.dataset.addb){const k=t.dataset.addb,v=t.parentElement.querySelector("input").value.trim();if(!MODELRE.test(v))mdlMsg="!invalid model id";else if(mdl[k].includes(v))mdlMsg="!already in the chain";else mdl[k].push(v)}
 else if(t.id==="mrv")mdl=JSON.parse(mdlSrv);
 else if(t.id==="msv"){try{const r=await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(mdl)}),d=await r.json();if(d.err)mdlMsg="!"+d.err;else{mdl=d;mdlSrv=JSON.stringify(d);mdlMsg="saved · new runs use it"}}catch(x){mdlMsg="!save failed"}}
 mdlRender()});
/* accordions: each of the three answers its own header and leaves the others alone; the open set is stored like the pins */
const ACC=["plans","stats","mdl"];
let openAcc=new Set(Array.isArray(ls.acc)?ls.acc.filter(i=>ACC.includes(i)):[]);   // the old single-id string is ignored, not trusted, and not fatal
const saveAcc=()=>{ls.acc=[...openAcc];save()};
ACC.forEach(i=>{const e=$("#"+i);e.open=openAcc.has(i);e.addEventListener("toggle",()=>{if(e.open)openAcc.add(i);else openAcc.delete(i);saveAcc();if(i==="mdl"&&e.open)mdlLoad()})});mdlLoad();
const PCOL=["#5aa9ff","#3ecf8e","#f5b041","#b79cf6","#2dd4bf","#ff6b6b","#c678dd","#e5c07b","#8a93a3"],tk=n=>n>=1e9?(n/1e9).toFixed(2)+"B":n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e3?(n/1e3).toFixed(0)+"K":""+n;
const OTHER=0.01;
function pie(ms){const rows=ms.map(m=>({...m,v:m.in+m.out})).filter(m=>m.v>0);if(!rows.length)return"<span class=m>no token data</span>";
 const tot=rows.reduce((a,m)=>a+m.v,0);const keep=[],fold=[];
 for(const m of rows) (m.v/tot<OTHER?fold:keep).push(m);
 while(keep.length+(fold.length?1:0)>6) fold.unshift(keep.pop());
 const nm=m=>(m.box&&m.box.registered===false?m.model+" · unregistered":(m.box&&m.box.llm)||m.model),sl=keep.map(m=>({n:nm(m),v:m.v,t:`${nm(m)} (${m.model}): ${tk(m.in)} in · ${tk(m.out)} out · ${tk(m.cache)} cache read · ${m.calls} calls · ${usd(m.spend)}`}));
 if(fold.length){const v=fold.reduce((a,m)=>a+m.v,0),t=fold.map(m=>`${m.model}: ${(m.v/tot*100).toFixed(2)}%`).join("\n");sl.push({n:`other · ${fold.length} models`,v,t})}
 let a0=-Math.PI/2;const R=52,C=60,arc=sl.map((x,i)=>{const a1=a0+x.v/tot*2*Math.PI,big=a1-a0>Math.PI?1:0,p=sl.length===1?`<circle cx=${C} cy=${C} r=${R} fill="${PCOL[i]}"><title>${esc(x.t)}</title></circle>`:`<path d="M${C} ${C}L${C+R*Math.cos(a0)} ${C+R*Math.sin(a0)}A${R} ${R} 0 ${big} 1 ${C+R*Math.cos(a1)} ${C+R*Math.sin(a1)}Z" fill="${PCOL[i]}" stroke="#161a21" stroke-width=1><title>${esc(x.t)}</title></path>`;a0=a1;return p}).join("");
 return `<div class=pie><svg viewBox="0 0 120 120" width=112 height=112>${arc}<circle cx=${C} cy=${C} r=26 fill="#161a21"/><text x=${C} y=${C-1} text-anchor=middle fill="#d8dde6" font-size=11 font-weight=600>${tk(tot)}</text><text x=${C} y=${C+12} text-anchor=middle fill="#8a93a3" font-size=8>tokens</text></svg><div class=lg>${sl.map((x,i)=>`<div title="${esc(x.t)}"><i style="background:${PCOL[i]}"></i><span class=ln>${esc(x.n)}</span><b>${Math.round(x.v/tot*100)}%</b></div>`).join("")}</div></div>`}
const ago=t=>{const m=Math.round((Date.now()/1000-t)/60);return m<2?"just now":m<90?m+" min ago":Math.round(m/60)+" h ago"},rs=t=>t?new Date(t*1000).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"?";
const rel=t=>{if(!t)return"";const x=t-Date.now()/1000;if(x<=0)return"resets now";const d=Math.floor(x/86400),h=Math.floor(x%86400/3600),m=Math.floor(x%3600/60);return"resets in "+(d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`)};
const lvl=p=>p>=90?"lv-err":p>=60?"lv-warn":"lv-ok",fpct=p=>p<10&&p>0?p.toFixed(1):String(Math.round(p));
const qbar=(label,pct,resets,sm,tip)=>`<div class="qw${sm?" sm":""}"><div class=ql><span>${esc(label)} <b>${fpct(pct)}%</b></span>${resets?`<span class=rst title="${esc(rs(resets))}">${rel(resets)}</span>`:""}</div><div class=tr title="${esc(tip)}"><div class="fl ${lvl(pct)}" style="width:${Math.min(pct,100)}%"></div></div></div>`;
const autoq=a=>{const QT="Quota used, read from the provider's own CLI. 100% = quota exhausted.";if(a.unavailable)return `<div class=fr><span class="chip err">unavailable</span><span class=fs title="${esc(a.err)}">${esc(a.err)}</span>${a.asof?`<span class=fs>· last probed ${ago(a.asof)}</span>`:""}</div>`;return a.windows.map((w,i)=>qbar(w.label,w.pct,w.resets,i>0,QT)).join("")+`<div class=fr>${a.reached?`<span class="chip err">limit reached</span>`:""}<span class=fs>CLI · ${ago(a.asof)}</span></div>`};
const CUR={"R$":"BRL","U$":"USD","US$":"USD","$":"USD","€":"EUR","£":"GBP","¥":"JPY"},curOf=v=>{v=v.trim().toUpperCase();return CUR[v]||v};
const money=(a,c)=>{try{return new Intl.NumberFormat(undefined,{style:"currency",currency:c}).format(a)}catch(e){return a+" "+c}};
async function pollSubs(){try{const d=await (await fetch("/api/subs")).json(),ae=document.activeElement;if(ae&&ae.closest&&ae.closest("#subs")&&ae.tagName==="INPUT")return;
 const tot=Object.values(d.subs).reduce((a,v)=>a+(v.saved||0),0),any=Object.values(d.subs).some(v=>v.saved!=null);$("#plsr").textContent=any||d.api?`plans ${usd(d.plans)} + API ${usd(d.api)} = ${usd(d.total)}`:"set plan prices";
 $("#cash").textContent="at API list prices (API-equivalent) · cash this month: "+(any||d.api?`plans ${usd(d.plans)} + API ${usd(d.api)} = ${usd(d.total)}`:"set plan prices");
 $("#stsr").textContent=`month ${usdc(d.total)}`;
 $("#subs").innerHTML=`<div class=subx>Per subscription: quota used, as each provider's CLI reports it. With no reading, the bar is the API-equivalent usage vs the plan price.</div><datalist id=curs>${["USD","BRL","EUR","GBP","ARS","MXN","CAD","JPY"].map(c=>`<option value=${c}>`).join("")}</datalist>`+Object.entries(d.subs).map(([k,v])=>{const ok=v.saved!=null,pl=v.plan,pr=d.presets[k],custom=pl&&pl.plan==="custom";
  const sel=`<select data-plan=${k} data-f=plan><option value="" ${pl?"":"selected"}>choose a plan…</option>${Object.entries(pr).map(([i,p])=>`<option value=${i} ${pl&&pl.plan===i?"selected":""}>${esc(p.name)} · US$${p.usd}</option>`).join("")}<option value=custom ${custom?"selected":""}>Custom amount…</option></select>`;
  const cust=custom?`<span class=pr><span>amount</span><input data-plan=${k} data-f=amount type=number min=0 step=0.01 value="${pl.amount}"> <input data-plan=${k} data-f=cur list=curs value="${esc(pl.currency)}" placeholder="BRL" title="R$, U$, € or any 3-letter code"></span>`:"";
  const note=v.noRate?`<span class=warn>no exchange rate for ${esc(pl.currency)} right now</span>`:(pl&&pl.currency!=="USD"&&v.usd!=null?`<span class=dt>≈ <b>US$${v.usd}</b> / month · ${(+v.rate).toFixed(2)} ${esc(pl.currency)} per US$</span>`:"");
  const est=pl&&v.usd?v.eq/v.usd*100:null,aq=v.quota,aw=aq&&aq.windows&&aq.windows[0],q=aw?aw.pct:(pl&&pl.quota!=null?pl.quota:null),win=aw?aw.label:(pl&&pl.qwin||""),pct=q!=null?q:est,today=d.frac*100,cls=q!=null?"warnq":(ok?(v.saved>=0?"good":"bad"):"");
  const MT="Fill: API-equivalent used by squad runs ÷ monthly plan price (partial: other uses of the plan are not seen). 100% = plan paid off. Tick: how much of the month has passed.";
  const bar=aw||(aq&&aq.unavailable)?autoq(aq):q!=null?qbar(win||"quota used",q,null,false,"Quota % entered by hand. 100% = quota exhausted."):pct!=null?`<div class=qw><div class=ql><span>used <b>${fpct(pct)}%</b> of plan value</span><span class=rst>month ${Math.round(today)}%${pct>=100?" · paid off ✓":""}</span></div><div class=tr title="${MT}"><div class="fl ${ok?(v.saved>=0?"lv-ok":"lv-err"):"lv-est"}" style="width:${Math.min(pct,100)}%"></div><div class=tk style="left:${Math.min(today,100)}%"></div></div></div>`:"";
  const man=aw||!pl?"":`<div class=pr><span>quota used</span><input data-plan=${k} data-f=quota type=number min=0 max=100 step=1 value="${q??""}" placeholder="%" style="width:60px"><input data-plan=${k} data-f=qwin value="${esc(win)}" placeholder="window, e.g. weekly · all models" maxlength=40 style="flex:1;min-width:0"></div>`;
  return `<div class=sub><div class=hd><span class=nm>${esc(v.name)}</span>${ok?"":`<span class="sv none">choose a plan</span>`}</div><div class=pr>${sel}</div>${cust}${note}${bar}${man}</div>`}).join("")}catch(e){console.error(e)}}
$("#subs").addEventListener("change",async e=>{const k=e.target.dataset.plan;if(!k)return;const row=e.target.closest(".sub");let body;
 const f=e.target.dataset.f,pick=row.querySelector("[data-f=plan]").value;
 if(f==="quota"||f==="qwin"){const x=row.querySelector("[data-f=quota]").value,w=row.querySelector("[data-f=qwin]").value.trim();body={quota:x===""?null:+x,qwin:w||null}}
 else if(f==="plan"){body=pick===""?null:pick==="custom"?{plan:"custom",amount:0,currency:"USD"}:{plan:pick}}
 else{const a=row.querySelector("[data-f=amount]"),c=row.querySelector("[data-f=cur]");c.value=curOf(c.value);body={plan:"custom",amount:+a.value||0,currency:c.value}}
 try{await fetch("/api/plans",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({[k]:body})})}catch(x){}pollSubs()});
async function pollStats(){try{pollSubs();
  const st=await (await fetch("/api/stats?period="+per+"&src="+src)).json(),sp=await (await fetch("/api/speed")).json();
  $("#per").innerHTML=["hour","day","month"].map(p=>`<button data-p=${p} class="${p===per?"on":""}">per ${p}</button>`).join("");
  $("#src").innerHTML=[["both","all"],["sub","plans"],["api","API"]].map(([v,l])=>`<button data-s=${v} class="${v===src?"on":""}">${l}</button>`).join("");
  document.querySelectorAll("#src button").forEach(b=>b.onclick=()=>{src=b.dataset.s;save();pollStats()});
  document.querySelectorAll("#per button").forEach(b=>b.onclick=()=>{per=b.dataset.p;save();pollStats()});
  $("#chart").innerHTML=bars(st.buckets);$("#pie").innerHTML=pie(st.models||[]);
  $("#piet").textContent=tk((st.models||[]).reduce((a,m)=>a+m.in+m.out,0))+" all time";
  const same=st.totals.all===st.totals.month;
  $("#tot").innerHTML=`<div>today<b>${usd(st.totals.today)}</b></div><div>this month<b>${usd(st.totals.month)}</b></div><div>all time<b${same?" class=m":""}>${same?"= this month":usd(st.totals.all)}</b></div>`;
  const b=sp.baseline,n=sp.pr.after.n,cut=b.cutoff.slice(5,16).replace("T"," ");
  $("#kpi").innerHTML=`<h4>Speed <small>since ${cut} UTC · ${n} PR${n===1?"":"s"}</small></h4>`
   +kr("Merge time",", median",sp.pr.after.median,"min",b.pr.median)
   +kr("Merge time",", average",sp.pr.after.mean,"min",b.pr.mean)
   +kr("Run time",", median today",sp.runs.today_median,"min",b.run_median)
   +kr("Review rounds",", average",sp.rounds.mean,"",null)
   +kr("Hung runs killed","",sp.kills.count,"",null)
  +(n<10?`<div class=warnl>Provisional: only ${n} PR${n===1?"":"s"} so far; the comparison firms up at ~10.</div>`:"")+(sp.pr.stale?"<div class=warnl>PR data stale</div>":"");
  $("#open").innerHTML=openRows(sp.pr.open);
}catch(e){}}
pollStats();setInterval(pollStats,30000);
poll();setInterval(poll,3000);
loadPoll();setInterval(loadPoll,LOAD_MS);   // the window owns the reading: one timer and one /api/load per tick, whatever else the page renders
const bo=ls.bo||{},saveB=()=>{ls.bo=bo;localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,bo})};
bo.dock="bottom";   // the board always docks at the bottom
if(bo.max)bo.off=false;else if(bo.off)bo.max=false;   // both controls live in one saved record, so a reload can never bring back a board that is maximized and collapsed at once
const sizeBoard=()=>{$("#board").style.height=document.body.classList.contains("dockb")&&!bo.off&&!bo.max?(bo.h||320)+"px":""};
// The board's maximize takes the run pane's cell as well as its own, so the kanban is read at full height. The run pane
// is a live, cross-origin dashboard: while it is out of sight its frame is detached (liveSync), so it holds no stream and no
// timer on its run's server, and it is built again when the split comes back.
const MAXI='<svg viewBox="0 0 24 24" width=13 height=13 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M4 9V4h5M20 15v5h-5M4 4l6 6M20 20l-6-6"/></svg>',
MINI='<svg viewBox="0 0 24 24" width=13 height=13 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M9 4v5H4M15 20v-5h5M9 9L4 4M15 15l5 5"/></svg>';
const paintBoard=()=>{document.body.classList.toggle("dockb",bo.dock==="bottom");document.body.classList.toggle("max",!!bo.max);
  $("#board").classList.toggle("off",!!bo.off);sizeBoard();
  $("#bx").innerHTML=bo.dock==="bottom"?(bo.off?"&uarr;":"&darr;"):"&raquo;";$("#bx").setAttribute("aria-pressed",String(!!bo.off));
  $("#bz").innerHTML=bo.max?MINI:MAXI;$("#bz").setAttribute("aria-pressed",String(!!bo.max));
  $("#bz").title=bo.max?"restore the split: run pane and board":"maximize the board over the run pane"};
const applyDock=paintBoard;
const setOff=v=>{bo.off=v;if(v)bo.max=false;paintBoard();saveB();liveSync()};   // a collapsed board is never a maximized one
const setMax=v=>{bo.max=v;if(v)bo.off=false;paintBoard();saveB();liveSync()};  // maximizing a collapsed board restores it first
const escBoard=()=>{if(bo.max)setMax(false)};
paintBoard();
$("#bx").onclick=()=>setOff(!bo.off);
$("#bz").onclick=()=>setMax(!bo.max);
const isPR=n=>n===COLNAME.pr_open;   // the one lane beside the axis: open work branches, for tracking only (#384)
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
let irepo="__REPO__";   // the repository the open card belongs to: a `Refs #N` in its markdown links in THAT repository (#1820)
const inl=x=>x.replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>").replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:]|$)/g,"$1<i>$2</i>").replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g,'<a target=_blank rel=noopener href="$2">$1</a>').replace(/(^|[\s(])#(\d{1,6})\b/g,`$1<a target=_blank rel=noopener href="https://github.com/${irepo}/issues/$2">#$2</a>`);
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
pop.onclick=e=>{if(e.target===pop)pop.classList.remove("on")};addEventListener("keydown",e=>{if(e.key==="Escape"){pop.classList.remove("on");escBoard()}else if(pop.classList.contains("on")&&(e.key==="ArrowLeft"||e.key==="ArrowRight")&&!/INPUT|TEXTAREA/.test(document.activeElement.tagName))step(e.key==="ArrowLeft"?-1:1)});
let nav=[],cur=0,irepo="__REPO__";   // nav carries [number, repository] pairs: a number alone does not name a card now that two repositories fill the column (#1820)
const ni=()=>nav.findIndex(x=>x[0]===cur&&x[1]===irepo);
const step=k=>{const i=ni()+k;if(i>=0&&i<nav.length)showIssue(nav[i][0],nav[i][1])};
async function showIssue(n,r){cur=+n;irepo=r||"__REPO__";pop.classList.add("on");$("#pbox").innerHTML="<span class=m>loading #"+n+"…</span>";
try{const d=await (await fetch("/api/issue?n="+n+(r?"&r="+encodeURIComponent(r):""))).json();if(d.err){$("#pbox").textContent=d.err;return}
$("#pbox").innerHTML=`<div class=bar><span class=m>${d.pr?"PR":"Issue"} #${d.n} · ${d.state}${d.r&&d.r!=="__REPO__"?` · ${esc(d.r)}`:""}</span><span><button id=pprev title="previous card (←)" ${ni()>0?"":"disabled"}>‹</button> <span class=m>${nav.length>1?ni()+1+"/"+nav.length:""}</span> <button id=pnext title="next card (→)" ${ni()>=0&&ni()<nav.length-1?"":"disabled"}>›</button> <a class=gh target=_blank href="${esc(d.url)}">Open on GitHub ↗</a> <button id=px>✕</button></span></div><h2>${esc(d.title)}</h2>
${d.pull?`<div class=prbar><span class=pill>${esc(d.pull.head||"?")} &rarr; ${esc(d.pull.base||"?")}</span><span class=pill>+${d.pull.add??0} &minus;${d.pull.del??0} · ${d.pull.files??0} files</span>${d.pull.draft?"<span class=pill>draft</span>":""}<span class="pill ${d.pull.mstate==="clean"?"ok":d.pull.mstate==="dirty"?"bad":""}">merge: ${esc(d.pull.mstate||"?")}</span>${Object.entries(d.pull.checks).map(([k,v])=>`<span class="pill ${k==="success"?"ok":/failure|cancelled|timed_out|action_required/.test(k)?"bad":""}">CI ${esc(k)} ${v}</span>`).join("")}${Object.entries(d.pull.reviews).map(([u,st])=>`<span class="pill ${st==="APPROVED"?"ok":st==="CHANGES_REQUESTED"?"bad":""}">${esc(u)}: ${esc(st.toLowerCase().replace("_"," "))}</span>`).join("")}</div>`:""}<div class=meta>by ${esc(d.by)} · opened ${when(d.created)} · updated ${when(d.updated)}</div>${d.labels.map(l=>`<span class=lb>${esc(l)}</span>`).join("")}
<div class=md>${d.body?md(d.body):"<span class=m>(no description)</span>"}</div><h3 class=sec>Last ${d.comments.length} of ${d.total} comments</h3>`+(d.comments.map(c=>`<div class=cm><div class=meta>${esc(c.by)} · ${when(c.at)}</div><div class=md>${md(c.body)}</div></div>`).join("")||"<span class=m>no comments</span>");
$("#px").onclick=()=>pop.classList.remove("on");$("#pprev").onclick=()=>step(-1);$("#pnext").onclick=()=>step(1);$("#pbox").scrollTop=0}catch(e){$("#pbox").textContent="failed to load"}}
document.addEventListener("click",e=>{const c=e.target.closest("#bbody .it");if(!c||e.target.closest("a")||e.target.closest("button")||c.classList.contains("m"))return;window.open("https://github.com/"+(c.dataset.r||"__REPO__")+"/"+(c.dataset.pr!==undefined?"pull/":"issues/")+c.dataset.n,"_blank","noopener");return;   // every card opens its GitHub page; the popup is no longer used from the board   // a PR card opens its GitHub page in a new tab (owner, 2026-10-09); an issue card keeps the popup
nav=[...c.closest(".sc").querySelectorAll(".it[data-n]")].map(x=>[+x.dataset.n,x.dataset.r||"__REPO__"]);showIssue(c.dataset.n,c.dataset.r)});
const hm=location.hash.match(/issue=(\d+)/);if(hm)showIssue(+hm[1]);   // deep link: /#issue=1171
document.addEventListener("click",e=>{const h=e.target.closest("#bbody .ah");if(!h||e.target.closest("[data-eye]"))return;const g=h.parentElement.dataset.g;if(g===COLNAME.pr_open)bo.prOpen=bo.prOpen===false;else bo.open=g;saveB();pollBoard()});
$("#grip").onpointerdown=e=>{e.preventDefault();document.body.classList.remove("dragging");const g=e.currentTarget;g.setPointerCapture(e.pointerId);g.classList.add("drag");document.body.classList.add("dragging");
const mv=ev=>{if(!ev.buttons){up();return}bo.h=Math.round(Math.min(innerHeight-160,Math.max(90,innerHeight-ev.clientY)));sizeBoard()},up=()=>{g.classList.remove("drag");document.body.classList.remove("dragging");g.removeEventListener("pointermove",mv);g.removeEventListener("pointerup",up);g.removeEventListener("pointercancel",up);g.removeEventListener("lostpointercapture",up);saveB()};
g.addEventListener("pointermove",mv);g.addEventListener("pointerup",up);g.addEventListener("pointercancel",up);g.addEventListener("lostpointercapture",up)};
// ---- the pip over the maximized board (#1782)
// Maximized, the run pane is out of sight, and with it the one thing worth glancing at while the board is read. This
// window floats on the board and shows ONE card of the run's own dashboard — the card is the dashboard's single-card
// mode, `?card=<name>`, documented in docs/design/README.md. The two pages are served on different ports, so there is no
// DOM to reach into: the URL is the whole interface, and the hub never renders a second copy of the card, which would
// drift from the panel's. The window belongs to the maximize mode — CSS hides it in the split and never detaches it — so
// nobody closes it twice. The frame is sourced once per run switch while maximized: collapsing the window, dragging and resizing never
// touch it, so the second view is one connection per run switch and one replay of that run's record, paid when the left
// bar moves to another run and never per paint. Outside the maximize mode the window is hidden and its frame holds no
// source, so the page keeps one live embedded dashboard in all (panes.js). A dashboard too old to answer the mode says so inside the window
// instead of leaving an empty frame.
const PIP_NORUNS="No open runs. Click a finished run in the list to look at it; the next run that starts shows up here on its own.";
const PIP={card:"groups",w:360,h:300,minW:240,minH:160,cw:240,ch:34,inset:12,step:16,run:64,wait:2500};
const PIP_CARDS={groups:"Groups"};
const PIP_OLD="This run's dashboard has no single-card mode, so the window has nothing to show. It is a dashboard built before `?card=` (docs/design/README.md); the run pane below is unaffected.";
const pip=()=>bo.pip||(bo.pip={});
const pipCard=p=>(p&&p.card)||PIP.card;
const pipClamp=(v,lo,hi)=>hi<lo?lo:Math.max(lo,Math.min(hi,v));
const pipURL=(r,card)=>r?((r.url||"http://127.0.0.1:"+r.port+"/")+"?card="+encodeURIComponent(card)):"";
const pipBox=()=>{const b=$("#board").getBoundingClientRect(),h=$("#bhead").getBoundingClientRect();return{w:b.width,h:b.height,head:h.height}};
// The size it takes on this board: the minimum first, so a place remembered on a wider board shrinks onto a narrow one and still fits.
const pipSize=(box,off)=>off?{w:PIP.cw,h:PIP.ch}:{w:box.w<PIP.w?PIP.minW:PIP.w,h:box.h-box.head<PIP.h?PIP.minH:PIP.h};
// A place is kept against the board's top-left and clamped: never left of the board, never above its header (measured, not assumed), never past its far edge.
const pipPlace=(box,s,x,y)=>({x:pipClamp(Math.round(x),0,box.w-s.w),y:pipClamp(Math.round(y),box.head,box.h-s.h)});
let pipSrc="",pipSeen="",pipDead="",pipTimer=0;
const pipRun=()=>{const id=paneFocus(slots,focus);return id?runs.find(r=>r.id===id)||null:null};   // the run the panel follows is the run the window shows
const pipHead=r=>{const c=PIP_CARDS[pipCard(pip())]||pipCard(pip());return r?name(r)+" · "+r.state+" · "+c:c};
const pipSet=o=>{Object.assign(pip(),o);saveB();pipDraw()};
function pipDraw(){
  const el=$("#pip"),p=pip(),off=!!p.off,box=pipBox(),s=pipSize(box,off);
  const at=pipPlace(box,s,p.x===undefined?box.w-s.w-PIP.inset:p.x,p.y===undefined?box.h-s.h-PIP.inset:p.y);
  el.style.left=at.x+"px";el.style.top=at.y+"px";el.style.width=s.w+"px";el.style.height=s.h+"px";
  el.classList.toggle("off",off);$("#pipx").innerHTML=off?"&#43;":"&#8211;";
  if(at.x!==p.x||at.y!==p.y){p.x=at.x;p.y=at.y;saveB()}   // a place remembered on a wider board is pulled back onto this one
  const r=pipRun(),card=pipCard(p),url=embedPlan(slots,focus,!!bo.max).pip?pipURL(r,card):"",f=$("#pipf");   // out of the maximize mode the window follows nothing: its frame holds no source (#34)
  if(!url&&pipSrc){pipSrc="";f.src="about:blank";pipSeen="";pipDead="";clearTimeout(pipTimer)}
  if(url)if(url!==pipSrc){pipSrc=url;f.src=url;pipSeen="";pipDead="";clearTimeout(pipTimer);
    pipTimer=setTimeout(()=>{if(pipSrc&&pipSeen!==pipSrc){pipDead=pipSrc;pipDraw()}},PIP.wait)}   // an older dashboard never answers: say so, never an empty frame
  const miss=!!url&&pipSrc===pipDead&&pipSeen!==pipSrc;
  el.classList.toggle("noframe",!r||miss);
  $("#pipm").textContent=!r?PIP_NORUNS:miss?PIP_OLD:"";
  $("#pipt").textContent=pipHead(r);
}
pipDraw();
$("#pipx").onclick=()=>pipSet({off:!pip().off});
$("#pipo").onclick=()=>setMax(false);   // closing the window is leaving the maximize mode: the board is never maximized without it
$("#piph").onpointerdown=e=>{e.preventDefault();const g=e.currentTarget;g.setPointerCapture(e.pointerId);g.classList.add("drag");document.body.classList.add("dragging");
const el=$("#pip"),ox=e.clientX-(parseFloat(el.style.left)||0),oy=e.clientY-(parseFloat(el.style.top)||0);
const mv=ev=>{if(!ev.buttons){up();return}const box=pipBox(),s=pipSize(box,!!pip().off),at=pipPlace(box,s,ev.clientX-ox,ev.clientY-oy);el.style.left=at.x+"px";el.style.top=at.y+"px"},
up=()=>{g.classList.remove("drag");document.body.classList.remove("dragging");g.removeEventListener("pointermove",mv);g.removeEventListener("pointerup",up);g.removeEventListener("pointercancel",up);g.removeEventListener("lostpointercapture",up);
const p=pip();p.x=parseFloat(el.style.left)||0;p.y=parseFloat(el.style.top)||0;saveB()};
g.addEventListener("pointermove",mv);g.addEventListener("pointerup",up);g.addEventListener("pointercancel",up);g.addEventListener("lostpointercapture",up)};
$("#piph").addEventListener("keydown",e=>{
  if(e.key==="Escape"){e.preventDefault();e.stopPropagation();if(pip().off)$("#bz").focus();else pipSet({off:true});return}   // never restores the split from inside
  const d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];if(!d)return;
  e.preventDefault();e.stopPropagation();
  const p=pip(),step=e.shiftKey?PIP.run:PIP.step,box=pipBox(),s=pipSize(box,!!p.off),at=pipPlace(box,s,(p.x||0)+d[0]*step,(p.y||0)+d[1]*step);
  pipSet({x:at.x,y:at.y})});
addEventListener("message",e=>{const d=e.data;if(!d||d.squadCard===undefined)return;pipSeen=pipSrc;pipDead="";pipDraw()});   // the dashboard answered the mode
$("#pip").addEventListener("wheel",e=>e.preventDefault(),{passive:false});   // a wheel over the chrome does not reach the board behind it
$("#piph").addEventListener("wheel",e=>e.preventDefault(),{passive:false});
addEventListener("resize",()=>pipDraw());
// ---- the pip ends here
document.addEventListener("click",e=>{const t=e.target.closest("[data-eye]");if(!t)return;e.preventDefault();e.stopPropagation();const g=t.dataset.eye;
 if(t.closest(".hidline")){const k=new Set(bo.kept||[]);k.add(g);bo.kept=[...k]}   // the hidden: strip's line: show the column again and keep it shown, even while it holds nothing (#1809)
 else{const h=new Set(bo.hidden||[]),k=new Set(bo.kept||[]);if(h.has(g))h.delete(g);else{h.add(g);k.delete(g)}bo.hidden=[...h];bo.kept=[...k]}   // the header's eye: the user's own hidden set, and the keep it ends
 saveB();pollBoard()},true);
const lim={},scr={};
function restore(){document.querySelectorAll("#bbody .sc").forEach(e=>{e.scrollTop=scr[e.dataset.g]||0;e.onscroll=()=>{scr[e.dataset.g]=e.scrollTop;if(e.scrollTop+e.clientHeight>=e.scrollHeight-40&&e.querySelector(".it.m")&&!lim.busy){const g=e.dataset.g;lim[g]=(lim[g]||25)+25;pollBoard()}}})}
// One colour per board column, resolved here and nowhere else: the header's dot, the card's --cc border and the
// hidden: strip's number all call colOf(name, columnNames). The nine named columns are the board's own — the eight
// shipped statuses plus the one open-PR lane — and the flow reads left to right: grey (not started), yellow (being
// coded), violet (built), teal (verified), green (finished), amber for the pile nobody has triaged, red for the one
// colour that means look here, and blue for the open-PR lane. Any other name is a Status option the Project gained
// later — a user column — and takes the next colour of the reserved ring, by its alphabetical position among those user
// columns. The name always stands beside the colour (column header, chip label), so the colour is a second cue and never
// the only one — which is what lets the ring cycle.
const COLORS=/*BOARD_COLORS*/;
const COLNAME=/*BOARD_NAMES*/;   // the columns' names by the fact that sets them: the board's script holds no column name of its own
const RING=["#a3e635","#d8b4fe","#ff9ecd","#7dd3fc"];   // reserved for the columns the user creates, and never one of the named columns
const colOf=(n,names)=>{const u=(names||[]).filter(x=>!Object.hasOwn(COLORS,x)).sort(),i=u.indexOf(n);return COLORS[n]||RING[(i<0?0:i)%RING.length]};
const caseRows=cs=>(cs||[]).map(c=>{const a=c.age<3600?Math.max(1,Math.round(c.age/60))+"m":c.age<172800?Math.round(c.age/3600)+"h":Math.round(c.age/86400)+"d";return `<div class="tcrow ${c.result==="fail"?"fail":"pass"}" data-key="test case:${c.issue}@${esc(c.commit)}"><span class=tr>${esc(c.result)}</span><span class=id>#${c.issue}</span><span class=ta>${a}</span><code class=tm>${esc(c.commit)}</code></div>`}).join("");   // one row for each test case item, in the model's order: a strip beside the axis, never a column, and the pane reads no cache of its own
async function pollBoard(){lim.busy=1;try{const b=await (await fetch("/api/board")).json(),FLOW=/*BOARD_FLOW*/,   // development flow, left to right
all=[b.prs,...b.groups.sort((x,y)=>{const i=n=>{const k=FLOW.indexOf(n);return k<0?FLOW.length:k};return i(x.name)-i(y.name)})],f=q.trim().replace(/^#/,"");
if(bo.open===undefined||!all.some(g=>g.name===bo.open))bo.open=(all.find(g=>g.n&&g.name!==COLNAME.issue_closed&&!isPR(g.name))||all[2]).name;
const CN=all.map(g=>g.name);   // every column of this board: what colOf reads the user columns out of, never a colour of its own
$("#bcases").innerHTML=caseRows(b.cases);$("#bt").textContent=b.err?"(stale)":"";$("#bsp").setAttribute("aria-pressed",String(!!bo.specs));
const fam=ls=>ls.reduce((o,l)=>{const k=l.includes(":")?l.split(":")[0]:"";(o[k]=o[k]||[]).push(l);return o},{});   // label group = prefix before ":"
if(bo.label!==undefined){bo.labels=bo.label?[bo.label]:[];delete bo.label;saveB()}   // the single-label filter of the previous version
const sel=bo.labels=bo.labels||[],LB=[...new Set([...(b.labels||[]),...sel])].sort(),lf=$("#lf"),lfl=$("#lfl");
if(lfl.dataset.k!==LB.join("\n")){lfl.dataset.k=LB.join("\n");lfl.innerHTML=`<div class=lft><span><button data-lm=any title="OR inside a group (same prefix, like wave:*; labels without a prefix form one group), AND across groups">by group</button> <button data-lm=all title="a card with every selected label">all</button></span><button data-lc=1>clear</button></div>`+LB.map(l=>`<label><input type=checkbox value="${esc(l)}">${esc(l)}</label>`).join("")}
lfl.querySelectorAll("input").forEach(c=>c.checked=sel.includes(c.value));lfl.querySelectorAll("[data-lm]").forEach(x=>x.classList.toggle("on",(bo.lmode||"any")===x.dataset.lm));
lf.classList.toggle("set",sel.length>0);lf.querySelector("summary").textContent=sel.length?(bo.lmode==="all"&&sel.length>1?"all of: ":"")+(bo.lmode==="all"?sel.join(", "):Object.values(fam(sel)).map(g=>g.length>1?"("+g.join(" | ")+")":g[0]).join(" & ")):"labels";
const lok=x=>!sel.length||(bo.lmode==="all"?sel.every(l=>(x.l||[]).includes(l)):Object.values(fam(sel)).every(g=>g.some(l=>(x.l||[]).includes(l))));
const hid=new Set(bo.hidden||[]),bottom=bo.dock==="bottom",eye=(g,off=hid.has(g.name))=>`<span class=eye data-eye="${esc(g.name)}" title="${off?"show":"hide"} this column"><svg viewBox="0 0 24 24" width=14 height=14 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx=12 cy=12 r=3 />${off?'<path d="M3 3l18 18"/>':""}</svg></span>`;
const LC=b.lc||{},rgb=h=>[0,2,4].map(i=>parseInt(h.slice(i,i+2),16)),mix=(a,t)=>a.map((v,i)=>Math.round(v*t+[28,34,44][i]*(1-t))),   // label color blended into the card's #1c222c
tint=x=>{const cs=(x.l||[]).map(l=>LC[l]).filter(c=>/^[0-9a-f]{6}$/.test(c||"")).map(rgb);if(!cs.length)return"";const avg=[0,1,2].map(i=>cs.reduce((s,c)=>s+c[i],0)/cs.length);return`;--bg:rgb(${mix(avg,.10)});--bgh:rgb(${mix(avg,.16)})`},
lbc=l=>/^[0-9a-f]{6}$/.test(LC[l]||"")?` style="--lb:rgba(${rgb(LC[l])},.30)"`:"";
const age=o=>{let m=Math.max(0,Math.floor((Date.now()-Date.parse(o))/60000));const d=Math.floor(m/1440),h=Math.floor(m%1440/60);m%=60;return "⏱ "+(d?d+"d "+h+"h":h?h+"h "+String(m).padStart(2,"0")+"m":m+"m")};
const ageC=o=>{const h=(Date.now()-Date.parse(o))/3600000;return h>=24?"old":h>=4?"warm":""};
setInterval(()=>document.querySelectorAll("#bbody .age").forEach(e=>{e.textContent=age(e.dataset.o);e.className="age "+ageC(e.dataset.o)}),30000);
const gcls=c=>c.v==="?"?"unk":c.v==="debt"?"debt":c.v.startsWith("OK")?"ok":(c.v.startsWith("CHG")||c.v.startsWith("BLK"))?"bad":"";
const gstrip=s=>`<div class="gstrip${s.old?" old":""}" title="${esc(s.old?`these facts are ${s.age} s old: the strip greys out past 180 s, so nothing here is being claimed as fresh`:"R review · C CI · T threads · B base · H labels and holds")}">`+s.cells.map(c=>`<span class="gc ${gcls(c)}" title="${esc(c.tip||"")}">${c.k}<b>${esc(c.v)}</b></span>`).join("")+`</div>`;
const cards=(g,it,shown)=>shown.map(x=>{const pr=x.kind==="pr";return `<div class=it data-n="${x.n}" data-r="${esc(x.r||"__REPO__")}"${pr?" data-pr":""} style="--cc:${colOf(g.name,CN)}${tint(x)}"><a class=id target=_blank href="https://github.com/${esc(x.r||"__REPO__")}/${pr?"pull":"issues"}/${x.n}">${pr?"PR ":""}#${x.n}</a>${x.rt?`<span class=rpo title="the repository this card belongs to: ${esc(x.r)}">${esc(x.rt)}</span>`:""}${x.pb?`<span class="pbg ${x.pb.replace(" ","-")}">${esc(x.pb)}</span>`:""}${x.rollup?`<span class="cir ${esc(x.rollup)}" title="CI states on the head${(x.ci||[]).length?": "+esc((x.ci||[]).join(", ")):""}">CI ${esc(x.rollup)}</span>`:""}${x.fz?`<span class=fz title="a freeze is a marker, never a move: the card keeps its column">${esc(x.fz)}</span>`:""}${x.pd?`<span class=pnd title="pending: the board door proved this move and the board read has not carried it yet">pending</span>`:""}${x.ps?`<span class="pst ${x.ps[0]}"${x.ps[2]?` title="${esc(x.ps[2])}"`:""}>${esc(x.ps[1])}</span>`:""}${x.c?`<span class=dt>${x.c.slice(5,16).replace("T"," ")}</span>`:""}<span class=tt>${esc(x.t)}</span>${x.pm&&!pr?`<button class=pmp data-n="${x.n}" title="move this card to Todo: the owner's go signal (ADR-0142)">→ Todo</button>`:""}${x.ps&&x.ps[3]?`<div class=prr>${esc(x.ps[3])}</div>`:""}${x.gs?gstrip(x.gs):""}${(x.l||[]).length?`<span class=lbs>${x.l.map(l=>`<span class="${(bo.labels||[]).includes(l)?"on":""}"${lbc(l)}>${esc(l)}</span>`).join("")}</span>`:""}${(x.b&&x.b.length)?`<div class=blk>blocked by ${x.b.map(n=>`<a target=_blank rel=noopener href="https://github.com/__REPO__/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${x.ho?`<div class=hld>${esc(x.ho)}</div>`:""}${(x.cl||[]).length?`<div class=clos title="the issues this pull request closes">closes ${x.cl.map(n=>`<a target=_blank rel=noopener href="https://github.com/${esc(x.r||"__REPO__")}/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${(x.o||x.sc||x.by)?`<div class=ft>${x.sc?`<span class=tsk title="sub-issues closed / total: ${esc(x.sc)}">${esc(x.sc)}</span>`:""}${x.o?`<span class="age ${ageC(x.o)}" data-o="${x.o}" title="open since ${x.o.slice(0,16).replace("T"," ")} UTC">${age(x.o)}</span>`:""}${x.by?`<span class=au title="opened by ${esc(x.by)}">${esc(x.by)}</span>`:""}</div>`:""}</div>`}).join("")+(it.length>shown.length?`<div class="it m">${it.length-shown.length} more · scroll</div>`:"");
// A visible column holding no cards is not drawn at all: the columns that do have cards take the width, and the
// collapsed one is accounted for by a line at the foot of the hidden: strip. Which columns those are is derived on
// every render -- from the filtered count, the user's hidden set and the set the line's eye added -- and never
// stored, so a card appearing brings the column back on its own and the last one leaving collapses it again (#1809).
const perr=g=>g.err?`<div class=perr title="a repository the hub was told to read and could not: the rows this column still shows for it are its last good ones">&#9888; ${esc(g.err)}</div>`:"";   // a repository the hub cannot read is an error on the column, never an empty column (#1820)
const fits={};all.forEach(g=>fits[g.name]=g.items.filter(x=>(!f||String(x.n).includes(f)||x.t.toLowerCase().includes(f.toLowerCase()))&&lok(x)&&(isPR(g.name)||spOk(x))));   // the Specs filter shows, only shows, or hides the spec-labelled items, never a column of its own (#247, #456)
const kept=new Set(bo.kept||[]),
 autoHidden=g=>!hid.has(g.name)&&!kept.has(g.name)&&!fits[g.name].length&&!g.tracking,   // the columns the strip's lines stand for, and the only ones: a column the user hid is a chip, one the user kept is drawn, and the lane is always drawn -- empty, it says 0 (#440)
 vis=all.filter(g=>!bottom||(!hid.has(g.name)&&!autoHidden(g))),autos=bottom?all.filter(autoHidden):[];
const badge=g=>`<span class=n>${f||sel.length?fits[g.name].length+"/":""}${g.n}</span>`;   // the number the header shows, and the number the strip's line repeats
const one=g=>{const it=fits[g.name],shown=it.slice(0,lim[g.name]||25),dot=`<i style="background:${colOf(g.name,CN)}"></i>`,cnt=badge(g),isOpen=g.name===COLNAME.pr_open?bo.prOpen!==false:bo.open===g.name;
if(bottom)return `<div class="col${isPR(g.name)?" prs lane":""}" style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><div class=ch><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}</span></div><div class=sc data-g="${esc(g.name)}">${perr(g)}${cards(g,it,shown)}</div></div>`;
return `<div class="acc${isPR(g.name)?" prs lane":""} ${hid.has(g.name)?"hidcol":""} ${isOpen?"open":""}" style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><div class=ah><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}</span></div><div class=sc data-g="${esc(g.name)}">${perr(g)}${cards(g,it,shown)}</div><div class=pad></div></div>`};
const fmtN=n=>n>=1000?n.toLocaleString("en-US"):""+n;   // "688" is one token; only 1000 and over needs separators
// One tile of the hidden: strip: the name as a caption over the count, the count's colour deciding what the count
// means. data-col names the column, so the colour is a CSS rule per category rather than an inline value; the one
// exception is a column #1793's map does not name, which takes the ring colour and can only come from colOf here.
const hidchip=g=>{const c=colOf(g.name,CN),ring=!Object.hasOwn(COLORS,g.name);return `<span class=chip data-eye="${esc(g.name)}" data-col="${esc(g.name)}" title="${esc(g.name+" — "+fmtN(g.n))}"><span class=lbl>${esc(g.name)}</span><b${ring?` style="color:${c}"`:""}>${fmtN(g.n)}</b></span>`};
const html=vis.map(one).join("")+(bottom&&hid.size?`<div class=hidbar>${all.filter(g=>hid.has(g.name)).map(hidchip).join("")}`:"")+(bottom&&(hid.size||autos.length)?`${hid.size?"":`<div class=hidbar>`}${autos.length?(hid.size?'<div class=hidsep></div>':"")+autos.map(g=>`<span class=hidline style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><span>${esc(g.name)}</span><b>${badge(g)}</b>${eye(g,true)}</span>`).join(""):""}</div>`:"");   // one quiet line per column hidden only because it is empty, behind a divider (#1809)
if($("#bbody").dataset.h!==html){$("#bbody").dataset.h=html;$("#bbody").innerHTML=html;restore()}   // same cards: leave the DOM alone, so a scroll in progress is never interrupted
}catch(e){}finally{lim.busy=0}}
// The Specs control holds three modes (#456): every card the other filters leave, only the cards that carry the spec
// label, or every card but those. It is one control because it is one question -- are the specs in the way, or am I
// looking for them? -- and its own label states the mode it holds, so the mode is read without a hover. The stored value
// is the mode; the boolean an older page stored is read as the two modes it named (true = only, false = all), so a board
// opens where it was left. `spOk` is the whole rule, and the columns ask it once per card: the modes are the page's, the
// spec label is the hub's (`x.sp`, from the table's own SPEC_LABEL), and no column is named here.
const SP=[["all","every card the other filters leave"],["only","only the cards that carry the spec label"],["hidden","every card but those"]];
const specMode=()=>{const v=bo.specs;if(v===true)return 1;if(v===false||v===undefined||v===null)return 0;
 const n=Math.trunc(+v);return n===1||n===2?n:0};   // 1 = only, 2 = hidden: a value the page cannot read is `all`, never a silent hide
const spOk=x=>{const m=specMode();return m===0||(m===1?!!x.sp:!x.sp)};
const spPaint=()=>{const m=specMode(),b=$("#bsp");b.dataset.m=SP[m][0];b.textContent="Specs: "+SP[m][0];b.title=`the Specs filter holds ${SP[m][0]}: a press moves it to ${SP[(m+1)%3][0]}, and this mode draws ${SP[m][1]}.`};
$("#bsp").onclick=()=>{bo.specs=(specMode()+1)%3;saveB();spPaint();pollBoard()};
spPaint();
$("#lfl").onchange=e=>{const v=e.target.value,h=new Set(bo.labels||[]);e.target.checked?h.add(v):h.delete(v);bo.labels=[...h];saveB();pollBoard()};
$("#lfl").onclick=e=>{const t=e.target.closest("button");if(!t)return;e.preventDefault();if(t.dataset.lc)bo.labels=[];else bo.lmode=t.dataset.lm;saveB();pollBoard()};
// The owner's one move (#560): the arrow a Backlog card carries delegates to the hub, and the card shows the reason a
// refusal gives. Which cards carry the arrow is the row's own fact (`x.pm`), never the page's rule.
document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .pmp");if(!b)return;e.preventDefault();e.stopPropagation();const n=+b.dataset.n,card=b.closest(".it");b.disabled=true;
try{const r=await fetch("/api/promote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue:n})}),d=await r.json();
 if(d.err){card.insertAdjacentHTML("beforeend",`<div class=perr>${esc(d.err)}</div>`);b.disabled=false}
 else{b.textContent="moved to Todo ✓";pollBoard()}}catch(x){card.insertAdjacentHTML("beforeend","<div class=perr>the hub did not answer</div>");b.disabled=false}});
document.addEventListener("click",e=>{if(!e.target.closest("#lf"))$("#lf").open=false});
pollBoard();setInterval(pollBoard,5000);
let ver=null;setInterval(async()=>{try{const v=(await (await fetch("/api/version")).json()).v;if(ver===null)ver=v;else if(v!==ver)location.reload()}catch(e){}},3000);
</script>"""
PAGE = PAGE.replace("/*PANES_JS*/", (pathlib.Path(__file__).resolve().parent / "panes.js").read_text())
# The strip paints one rule per category, keyed by the fact that sets the column: the name comes from the table.
HID_CHIP_TOKENS = (("abandoned", "err"), ("dev_ci", "run"), ("unscheduled", "warn"), ("issue_closed", "ok"), ("pr_open", "pin"))
PAGE = PAGE.replace("/*HID_CHIP_RULES*/", "\n".join('.hidbar .chip[data-col="%s"] b{color:var(--%s)}' % (COLUMN[fact], token) for fact, token in HID_CHIP_TOKENS))
PAGE = PAGE.replace("/*BOARD_COLORS*/", BOARD_COLORS_JSON).replace("/*BOARD_FLOW*/", BOARD_FLOW_JSON).replace("/*BOARD_NAMES*/", BOARD_NAMES_JSON)
PAGE = PAGE.replace("/*RUN_STATE_CSS*/", "".join(".b.%s{color:var(--%s);%s}" % (r["state"].replace(" ", "-"), r["color"], r["css"]) for r in RUN_STATES))


PLANS = pathlib.Path(__file__).resolve().parent / "plans.json"
SUBS = ("claude", "grok", "gpt", "gemini")
SUB_ACR = dict(zip(SUBS, ("CL", "GK", "GPT", "GEM")))   # the footer's token for each subscription (#1839): 2-3 letters, spelled here and nowhere else
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
QUOTA = SHARED / "hub-quota.json"
try: quota = json.loads(QUOTA.read_text())
except Exception: quota = {}
def q_claude():
    """`claude -p` streams a rate_limit_event with the five-hour and seven-day utilisation of the subscription (one tiny haiku call, no session kept). --strict-mcp-config: the probe reads a rate limit and needs no MCP server; without it the machine's server set loads into the call and pays tens of thousands of input tokens of tool schemas before the one word it reads."""
    out = subprocess.run(["claude", "-p", "ok", "--strict-mcp-config", "--model", "haiku", "--output-format", "stream-json", "--verbose", "--max-turns", "1", "--no-session-persistence"],
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
GROK_LOG = HOME / ".grok/logs/unified.jsonl"
GROK_SETTINGS = HOME / ".grok/settings_cache.json"   # the config the grok CLI caches at startup
GROK_SCAN_MAX = 64 * 1024 * 1024   # how far back the probe walks before calling the log blind
GROK_KEPT = HUBDIR / "hub-grok.json"   # the newest credit reading seen: the log rotates, and its one credit line with it (#553)
def grok_no_source(first):
    """The true reason for no reading: the grok CLI writes creditUsagePercent only when a TUI session starts."""
    since = f" since its first entry at {first}" if first else ""
    return f"no credit line in ~/.grok/logs/unified.jsonl{since}: open the grok TUI once to write one"
def grok_keep(k):
    """Keep reading k ({pct, start, end, asof}) when it is newer than the kept one. Atomic: tmp, then rename."""
    try: old = json.loads(GROK_KEPT.read_text())
    except Exception: old = None
    if isinstance(old, dict) and (old.get("asof") or 0) >= k["asof"]: return old
    tmp = GROK_KEPT.with_name(f"{GROK_KEPT.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try: tmp.write_text(json.dumps(k)); os.replace(tmp, GROK_KEPT)
    except OSError: tmp.unlink(missing_ok=True)
    return k
def grok_plan():
    """The plan the grok CLI itself caches; it is also what proves the settings file is being read at all."""
    try: p = json.loads(json.loads(GROK_SETTINGS.read_text())["payload"])
    except Exception: return None
    return ((p.get("settings") or {}).get("subscription_tier_display")) or None
def q_grok():
    """Read the newest log line carrying a BillingConfig: `creditUsagePercent` + `currentPeriod{start,end}`.
    The grok CLI pushes that payload in the line's `ctx.config`, and its message wording drifts between versions
    (`billing: fetched credits config` in older ones), so match the fields and never the text. Finding none is a
    reading of its own — `unavailable` with the reason — so refresh_quota cannot keep showing yesterday's number.
    The newest reading is kept in GROK_KEPT and read back after the log rotates; `until` is its period end (quota_row)."""
    last = first = None
    try:
        with open(GROK_LOG, "rb") as fh:
            fh.seek(0, 2); size = fh.tell(); pos = size; floor = max(0, size - GROK_SCAN_MAX)
            while pos > floor and last is None:
                hi = pos; step = min(4_000_000, pos - floor); pos -= step
                if hi < size:   # the read would end inside a line: carry it to its newline, so this chunk holds that
                    fh.seek(hi)  # whole line and not its head alone, the tail of it being what the older chunk drops
                    while hi < size and hi < pos + 8_000_000:
                        b = fh.read(65_536); hi += len(b)
                        if b"\n" in b or not b: break
                fh.seek(pos); chunk = fh.read(hi - pos)
                if pos:   # read from the middle: the first line is half a line unless the byte before is the newline
                    fh.seek(pos - 1)
                    if fh.read(1) != b"\n": chunk = chunk.split(b"\n", 1)[-1]
                for l in chunk.decode("utf8", "ignore").splitlines():
                    try: d = json.loads(l)
                    except Exception: continue
                    if not isinstance(d, dict): continue
                    if first is None and d.get("ts"): first = d["ts"]   # the first line of the oldest chunk read
                    c = (d.get("ctx") or {}).get("config") or {}
                    if c.get("creditUsagePercent") is not None and isinstance(c.get("currentPeriod"), dict): last = (d, c)
    except OSError: pass
    k = None
    if last:
        d, c = last; per = c.get("currentPeriod") or {}
        end = dt.datetime.fromisoformat(per["end"].replace("Z", "+00:00")).timestamp() if per.get("end") else None
        k = grok_keep({"pct": c.get("creditUsagePercent"), "end": end,
                       "asof": dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()})
    else:
        try: k = json.loads(GROK_KEPT.read_text())
        except Exception: k = None
    if not isinstance(k, dict) or k.get("pct") is None:
        return {"windows": [], "unavailable": True, "err": grok_no_source(first), "plan": grok_plan()}
    return {"windows": [{"label": "weekly", "pct": k["pct"], "resets": k.get("end")}], "plan": grok_plan(),
            "asof": k.get("asof"), "until": k.get("end")}
def q_agy():
    """`agy -p /quota` prints one line per model group (no model call): name, `Weekly Limit Remaining`, N%, reset ISO. The Gemini group is the Google plan's."""
    r = subprocess.run(["agy", "-p", "/quota"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    w = agyquota.gemini_window(r.stdout)   # review.sh reads the agy-limited reset through the same module
    return {"windows": [{"label": "weekly", "pct": w[0], "resets": w[1]}]} if w else None
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
QUOTA_PROBES = {"claude": q_claude, "gpt": q_codex, "grok": q_grok, "gemini": q_agy}
def refresh_quota_once():
    """One pass: ask each CLI for its quota. A probe that answers with no windows replaces the row outright, so a
    reading that stopped tracking reality cannot pass as current; a probe that raises keeps the last good value."""
    for k, fn in QUOTA_PROBES.items():
        try:
            r = fn()
            if r:
                r.setdefault("asof", time.time()); quota[k] = r
                if r.get("windows"): note_week(k, r)
        except Exception as e: quota.setdefault(k, {"windows": []})["err"] = str(e)[:120]
    try: QUOTA.write_text(json.dumps(quota))
    except OSError: pass
def refresh_quota():
    """Every 20 min, one refresh_quota_once pass (the age of the last good value is shown on the row)."""
    while True:
        refresh_quota_once(); time.sleep(1200)
QUOTA_MAX_AGE = 3 * 3600   # a probe repolls every 20 min; a reading older than this stopped tracking the account
def quota_row(k, now=None):
    """What the cost panel and the footer show for provider k: the CLI's own reading, or the reason there is no reading.
    A reading past QUOTA_MAX_AGE is no reading, and says when the last one was taken. A reading with `until` (the kept
    grok reading: the CLI writes a credit line only when a TUI session starts) holds to its period end, then is stale."""
    r = quota.get(k); now = time.time() if now is None else now
    if not r: return None
    if r.get("windows") and r.get("until"):
        if now <= r["until"]: return r
        return {"unavailable": True, "stale": True, "asof": r.get("asof"), "plan": r.get("plan"),
                "err": f"stale: the last reading ({r['windows'][0].get('pct')}%) is for a period that ended "
                       f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r['until']))}; open the grok TUI once to write a new one"}
    if r.get("windows"):
        age = now - r["asof"] if r.get("asof") else 0
        if age <= QUOTA_MAX_AGE: return r
        return {"unavailable": True, "asof": r["asof"], "plan": r.get("plan"),
                "err": f"no reading: the last one is {round(age / 3600)} h old (the limit is {QUOTA_MAX_AGE // 3600} h), taken {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r['asof']))}"}
    if r.get("err"): return {"unavailable": True, "err": r["err"], "asof": r.get("asof"), "plan": r.get("plan")}
    return None

BRANDS = {"claude": "Claude", "grok": "Grok", "gpt": "ChatGPT", "gemini": "Google"}
def sub_name(k, plan, detected):
    """The card name comes from the account: the plan the probe detected, else the plan chosen in the card, else the provider."""
    name = detected or (PRESETS[k].get(plan["plan"], (None,))[0] if plan else None)
    if not name: return BRANDS[k]
    name = str(name); name = name[:1].upper() + name[1:]
    return name if BRANDS[k].lower() in name.lower() else f"{BRANDS[k]} {name}"

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
    for k in out:
        out[k]["quota"] = quota_row(k)
        out[k]["name"] = sub_name(k, out[k]["plan"], (quota.get(k) or {}).get("plan"))
    return {"subs": out, "frac": round(frac, 3), "plans": round(plans, 2), "api": round(api, 2), "total": round(plans + api, 2)}

MODELS = pathlib.Path(__file__).resolve().parent / "models.json"
CHAINS = ("coding_models", "review_models", "vision_models")   # all three live in models.json
ROUTER_CHAIN = "router_models"   # the router's own chain, written into the run graph (the dispatch envelope, #95)
READONLY = ("known", "router_known", "labels", "boxes", "last_coding", "envelope", "blocked", "chain")   # fields models_get() adds for the panel: posted back, never stored
# One model-id shape, in one place (#221): a bare id, and the context suffix the gateway serves beside it
# (`claude-deepseek-v4-flash[1m]`). The save validates with MODEL_ID and the page's Add box tests the same source,
# substituted when the page is served, so a second spelling cannot refuse an id the save takes -- the shape without the
# bracket refused every Save of the Router queue, because the queue always carries the `[1m]` id.
MODEL_ID_SRC = r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}(\[[A-Za-z0-9]{1,8}\])?"
MODEL_ID = re.compile("^" + MODEL_ID_SRC + "$")
# What /model offers that no file lists: the Claude 5 family from the session, the review (codex) ids and the Antigravity ids from `agy models`.
# The add box carries the provider catalogues as they are, minus the two things that made it unreadable: the
# workflow graph's prose (see known_models) and the same model under two spellings (the alias fold below). The
# gemini/gpt-oss ids are the Antigravity catalogue; refresh them from `agy models` when it moves.
EXTRA_MODELS = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
    "claude:claude-opus-5-5", "claude:claude-sonnet-5-5", "claude:claude-fable-5-1",
    "claude-deepseek-v4-flash", "claude-deepseek-flash", "gpt-5.6-terra", "gpt-6.1-sol", "claude-session",
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.7-flash-low",
    "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
    "gemini-3.1-pro-high", "gemini-3.1-pro-low",
    "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium",
    "grok-4.5", "grok-4.6"]
ALIAS = re.compile(r"^claude-(grok|gpt|gemini)-")
# The gateway's own tier suffix, which no chain consumer serves: review.sh hands a bare id to `codex exec`, never
# to the gateway. Folded onto the bare id (`gpt-5.6-terra-fast`) it reads as a codex model, so an entry picked
# from the box failed every call and fell through the chain (#1564).
GATEWAY_ONLY = re.compile(r"-fast$")
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
    try: ds = sorted((d for runs in run_roots(CHECKOUT) for d in runs.glob("run-*")), key=lambda d: d.stat().st_mtime, reverse=True)[:limit]
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

GATEWAY = (os.environ.get("ANTHROPIC_BASE_URL") or "http://localhost:8016").rstrip("/")
_gw = {"t": 0.0, "ids": []}
def gateway_models(ttl=120):
    """The ids the gateway itself serves, read from its own `/v1/models` -- the same list Claude Code's `/model` picker
    shows as "From gateway". Live, because the catalogue changes on the gateway's side and a hardcoded copy went stale
    (the GPT family was missing from the add box, 2026-10-11); cached briefly, because the panel asks on every load.
    Empty when the gateway is down, which leaves the other sources to fill the box."""
    if time.time() - _gw["t"] < ttl: return _gw["ids"]
    ids = []
    try:
        with urllib.request.urlopen(GATEWAY + "/v1/models", timeout=3) as r:
            d = json.loads(r.read().decode("utf8", "replace"))
        ids = [m["id"] for m in (d.get("data") or d.get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception: pass
    _gw.update(t=time.time(), ids=ids)
    return ids

def runnable_models(ids):
    """The ids a chain consumer can actually run, which the sources behind the box are not all of.

    No chain consumer goes through the gateway: review.sh sends a bare id to `codex exec`, a `claude:`-prefixed one
    to `claude -p --model`, `grok-*` to the grok CLI and `gemini-*` to agy. The gateway's `-fast` tier answers only
    on the gateway route -- the alias that serves it lives in the interactive shell wrapper, which review.sh does
    not go through -- and under its folded id it reached `codex exec` as a model codex does not have: the 400
    "The 'gpt-5.6-terra-fast' model is not supported when using Codex with a ChatGPT account" (#1564). Both sources
    spell it: the gateway's own catalogue and the Claude Code settings, which register the same wrapper aliases.
    """
    return [m for m in ids if not GATEWAY_ONLY.search(m)]

def router_known():
    """The Router queue's add box: the ids the gateway serves, in its own spelling (#225).

    A queue entry is handed to the engine, which runs every call through the gateway, so the box offers only what the
    gateway's catalogue holds. The other CLIs' ids (codex, agy, grok) stay in `known`, for the role chains."""
    return sorted({m for m in gateway_models() if MODEL_ID.match(m) and not provkey.closed(m)})   # no key, never offered (#482)


def model_labels(ids):
    """{id: registry label} for the ids the one registry knows (ADR-0147); an unknown id has no row (#672).

    Display only: the add box matches a typed "4.1" against the label, and the value it saves stays the id."""
    out = {}
    for m in ids:
        e = router_envelope.modelreg.model(m)
        if e is not None and e.label: out[m] = e.label
    return out


ROUTE_TEXT = {"gateway": "gateway", "direct": "direct API"}   # a cli route reads as its own CLI, named by the registry's argv


def model_box(raw, chain=None):
    """What one hub box that stands for a model shows (#677): the registry label beside the route, or "unregistered".

    The rule lives here, never in the page (ADR-0137). `chain` picks the spelling that chain stores (chain_id), so a
    review row of an Anthropic id reads as the claude CLI it runs through. A cli route is named by its argv's program
    (claude, codex, grok, agy)."""
    reg = router_envelope.modelreg
    spelling = chain_id(chain, raw) if chain else raw
    e, ref = reg.model(spelling), reg.normalize(spelling)
    if e is None or not e.label:
        return {"id": raw, "llm": None, "route": None, "registered": False, "text": "unregistered"}
    argv = ((e.routes.get("cli") or {}).get("argv") or [None]) if ref.route == "cli" else [None]
    route = f"{pathlib.Path(argv[0]).name} CLI" if argv[0] else ROUTE_TEXT.get(ref.route or "", ref.route)
    return {"id": raw, "llm": e.label, "route": route, "registered": True, "text": f"{e.label} · {route}" if route else e.label}


ANTHROPIC_ID = re.compile(r"^claude-(opus|sonnet|haiku|fable)-[0-9][0-9a-z.\-]*$")


def router_resolve(mid, served):
    """The gateway's spelling of one queue entry, or None when the gateway does not serve it (#225).

    The box folds `claude-gpt-5.6-terra` onto `gpt-5.6-terra`, so a bare id is tried under the `claude-` prefix too; a
    suffix such as `[1m]` is kept, because it is part of the id the gateway serves. An Anthropic id is not in the
    gateway's list and the gateway passes it through, so it resolves to itself."""
    if ANTHROPIC_ID.match(mid): return mid
    b = bare_id(mid)
    for c in (b, "claude-" + b):
        if c in served: return c
    return None


def known_models(d):
    """Suggestions for the add box: the models the chains already use, the Claude Code settings, plus EXTRA_MODELS.

    The workflow graph is no longer read: it names a dozen ids in its own prose (the ADR-0113 history of this
    flow), so scraping it filled the box with models that appear nowhere else.

    One model, one row. A non-Anthropic provider is reachable as `grok-4.6` and as `claude-grok-4.6` and both
    spellings were listed: the alias is folded onto the bare id, which is the form the chains name and the
    bridge answers.

    An id offered here is one a chain consumer runs: the assembled list goes through runnable_models, so the
    gateway's `-fast` tier never reaches the box, where it costs a whole run to discover.
    """
    out = list(EXTRA_MODELS) + gateway_models()
    for k in CHAINS: out += d.get(k, [])
    try: out += list(json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text()).get("modelSettings", {}))
    except Exception: pass
    return sorted({bare_id(ALIAS.sub(r"\1-", m)) for m in runnable_models(out) if MODEL_ID.match(m) and not provkey.closed(m)})   # no key, never offered (#482)
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
def model_off(d, chain, mid):
    """True when the panel switched the model out of its chain (#1819). The row keeps its place, its rank, its effort
    and its hrs: the switch says "do not run this one", it never edits the chain."""
    o = d.get("off") if isinstance(d.get("off"), dict) else {}
    ok = o.get(chain) if isinstance(o.get(chain), dict) else {}
    return bool(ok.get(mid)) and mid in d.get(chain, [])
def off_of(d):
    """Per-chain {model-id: True} from models.json's optional top-level `off`; only entries whose model is in that chain."""
    o = d.get("off") if isinstance(d.get("off"), dict) else {}
    return {k: {m: True for m, v in (o.get(k) or {}).items() if v and model_off(d, k, m)} for k in CHAINS}
def may_run(d, chain, mid, when=None):
    """True when the model may run at `when`: a model switched off is skipped exactly like one outside its window.

    One predicate for both reasons, because the consumers do not care which one it was -- `in_hours` and `model_off`
    are only apart in the panel, where the row shows which of the two it is."""
    return not model_off(d, chain, mid) and in_hours(d, chain, mid, when) and not provkey.closed(mid)   # a provider key that is absent closes it too (#482)
def hours_of(d):
    """Per-chain {model-id: "HH-HH"} from models.json's optional top-level `hours`; only entries whose model is in that chain."""
    h = d.get("hours") if isinstance(d.get("hours"), dict) else {}
    return {k: {m: v for m, v in (h.get(k) or {}).items() if model_hours(d, k, m) == v} for k in CHAINS}
def efforts_of(d):
    """Per-chain {model-id: effort} from models.json's optional top-level `efforts`; every chain present, only entries whose model is in that chain."""
    e = d.get("efforts") if isinstance(d.get("efforts"), dict) else {}
    return {k: {m: v for m, v in (e.get(k) or {}).items() if isinstance(e.get(k), dict) and m in d.get(k, []) and v in EFFORTS and v != "medium"} for k in CHAINS}
def _graph_path():
    """The run graph the loop starts, as launch.py resolves it: the project's own workflow file first, else the seed.

    The checkout comes from the seam (CHECKOUT, resolved by address.sh), never from the cwd: the hub serves every
    repository of the profile, so its working directory says nothing about whose graph it is reading."""
    for p in (pathlib.Path(CHECKOUT) / "docs/squad/workflows/default.graph.json",
              pathlib.Path(CHECKOUT) / "docs/squad/workflows/dynamic.graph.json",
              pathlib.Path(CHECKOUT) / "docs/design/router.graph.json"):
        if p.is_file(): return p
    return None


def _graph_doc():
    p = _graph_path()
    if p is None: return {}
    try: return json.loads(p.read_text())
    except (OSError, ValueError): return {}


def router_get():
    """The router's own chain (#95): the models the dispatch envelope allows, in the order it lists them. It is the
    same list the router reads to name a child's model, and the same one a request outside of which is refused whole."""
    return [bare_id(m) for m in router_envelope.envelope_models(_graph_doc())]


def router_set(ids):
    """Write that chain into the run graph and restamp the record it ships beside.

    The envelope is graph data, never a models.json chain (ADR-0106): the graph is the author's, and the panel is its
    editor. The record carries the graph hash `check_record.py` enforces, so the two move together."""
    p = _graph_path()
    if p is None: return {"err": "no run graph to write"}
    try: doc = json.loads(p.read_text())
    except (OSError, ValueError): return {"err": "the run graph does not parse"}
    hit = False
    for n in doc.get("nodes") or []:
        if n.get("kind") == "dispatch":
            n.setdefault("allow", {})["models"] = list(ids); hit = True
    if not hit: return {"err": "the run graph has no dispatch node"}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n"); tmp.replace(p)
    venv = pathlib.Path(CHECKOUT) / ".venv" / "bin" / "python"
    r = subprocess.run([str(venv) if venv.exists() else sys.executable,
                        str(pathlib.Path(CHECKOUT) / "scripts" / "stamp_router_record.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {"err": "the graph was written and its record could not be restamped: " + (r.stdout + r.stderr).strip()[-200:]}
    return {}


def envelope_of():
    """The dispatch envelope of that graph: what the router may ask a child for. A request outside it is refused whole."""
    d = _graph_doc()
    for n in d.get("nodes") or []:
        if n.get("kind") == "dispatch":
            a = n.get("allow") or {}
            return {"node": n.get("name"), "graph": d.get("name"),
                    "models": router_envelope.envelope_models(d), "efforts": a.get("efforts") or [],
                    "tools": a.get("tools") or [], "workspaces": a.get("workspaces") or [],
                    "max_children": n.get("max_children"), "max_parallel": n.get("max_parallel"),
                    "max_depth": n.get("max_depth")}
    return {}


def models_get():
    """The three chains as the panel reads them: bare ids, so one model is one row in every chain and in the box.

    The file keeps the spelling each role needs (chain_id); the panel never sees it, so a reader cannot pick the
    wrong one of two spellings of the same model."""
    d = json.loads(MODELS.read_text())
    return {**{k: [bare_id(m) for m in d.get(k, [])] for k in CHAINS},
            "efforts": {k: {bare_id(m): v for m, v in ek.items()} for k, ek in efforts_of(d).items()},
            "hours": {k: {bare_id(m): v for m, v in hk.items()} for k, hk in hours_of(d).items()},
            "off": {k: {bare_id(m): True for m in ok} for k, ok in off_of(d).items()},
            "known": known_models(d), "last_coding": last_coding_model(), "envelope": envelope_of(),
            "router_models": router_get(), "router_known": router_known(),
            "labels": model_labels(set(known_models(d)) | set(router_known()) | {bare_id(m) for k in CHAINS for m in d.get(k, [])}),
            "boxes": {**{k: {bare_id(m): model_box(bare_id(m), k) for m in d.get(k, [])} for k in CHAINS},
                      ROUTER_CHAIN: {m: model_box(m) for m in router_get()},
                      "known": {m: model_box(m) for m in set(known_models(d)) | set(router_known()) | ({last_coding_model()} - {None, ""})}},
            "chain": chainhealth.chain_state(d, "review_models", SHARED),
            "blocked": {k: router_envelope.envelope_blocked([bare_id(m) for m in d.get(k, [])], router_get()) for k in ("coding_models",)}}
def models_set(new):
    """Replace the three role chains in models.json; every other key is kept. Atomic write, so a reader never sees half a file."""
    # The panel posts back whatever models_get() handed it, so every read-only field it carries comes back here: `known`
    # (the add box's list) and `last_coding` (the header's line). Dropping only `known` made the other one read as a chain
    # the panel invented -- "unknown chain" on every Save (2026-10-11).
    new = {k: v for k, v in new.items() if k not in READONLY} if isinstance(new, dict) else new
    if not isinstance(new, dict) or set(new) - set(CHAINS) - {"efforts", "hours", "off", ROUTER_CHAIN}: return {"err": "unknown chain"}
    # The router chain is the dispatch envelope, so it lives in the run graph and never in this file (#95)
    rchain = new.pop(ROUTER_CHAIN, None)
    if rchain == []:
        rchain = None       # no run graph under this checkout: the panel has no envelope to write, and says nothing
    if rchain is not None:
        if not isinstance(rchain, list) or not 1 <= len(rchain) <= 8 or \
           not all(isinstance(m, str) and MODEL_ID.match(m) for m in rchain) or len(set(rchain)) != len(rchain):
            return {"err": f"{ROUTER_CHAIN}: need 1-8 distinct model ids"}
        # One entry is one id the engine can run (#225): the gateway's spelling, or a refusal that names the entry.
        # A Save that did not move this queue leaves the graph alone, so a role-chain edit is never refused for it.
        if rchain != router_get():
            served = set(gateway_models())
            res = [router_resolve(m, served) for m in rchain]
            bad = [m for m, r in zip(rchain, res) if r is None]
            if bad: return {"err": f"{ROUTER_CHAIN}: the gateway does not serve " + ", ".join(bad)}
            rchain = res
            if len(set(rchain)) != len(rchain): return {"err": f"{ROUTER_CHAIN}: need 1-8 distinct model ids"}
        if rchain != router_get():          # a Save that did not move this queue leaves the graph and its record alone
            err = router_set(rchain)
            if err: return err
    d = json.loads(MODELS.read_text()); eff = new.pop("efforts", None); hrs = new.pop("hours", None); off = new.pop("off", None)
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
    if off is not None:
        # The switch (#1819) lives beside the chains like efforts and hours: a model out of its chain is a state of the
        # row, so the array of strings other scripts jq stays exactly what it was. Switching every entry off is allowed:
        # the consumers then take the "no model may run now" path instead of falling back to a model the owner took out.
        if not isinstance(off, dict) or set(off) - set(CHAINS): return {"err": "off: unknown chain"}
        for k, ok in off.items():
            if not isinstance(ok, dict) or not all(isinstance(m, str) and m in {bare_id(x) for x in d.get(k, [])} and v for m, v in ok.items()):
                return {"err": f"off.{k}: model must be in the chain, value true"}
        d["off"] = {k: {chain_id(k, m): True for m, v in (off.get(k) or {}).items() if v} for k in CHAINS}
    if "off" in d:
        d["off"] = {k: v for k, v in off_of(d).items() if v}
        if not d["off"]: del d["off"]
    if "hours" in d:
        d["hours"] = {k: v for k, v in hours_of(d).items() if v}
        if not d["hours"]: del d["hours"]
    if "efforts" in d:
        d["efforts"] = {k: v for k, v in efforts_of(d).items() if v}
        if not d["efforts"]: del d["efforts"]
    tmp = MODELS.with_suffix(".tmp"); tmp.write_text(json.dumps(d, indent=2) + "\n"); os.replace(tmp, MODELS)
    return models_get()

def served_page():
    """The page as the hub serves it, every placeholder substituted. The model-id shape replaces its quoted placeholder:
    json.dumps brings its own quotes, and a second pair is a syntax error that empties the page."""
    return PAGE.replace("__REPO__", REPO).replace('"__MODELID__"', json.dumps(MODEL_ID_SRC)).replace("__CAPS__", json.dumps(loop_caps())).replace("__LOADMS__", str(int(LOAD_SAMPLE_SECS * 1000))).replace("__LOADERR__", str(LOAD_ERR_PCT))


def own_host(h):
    """The one Host rule for GET and POST: the hub's own loopback name and port, so a page that rebinds its name to 127.0.0.1 is refused. `h` is any handler, the mux's too, which runs these routes."""
    return (h.headers.get("Host") or "") in (f"127.0.0.1:{h.server.server_port}", f"localhost:{h.server.server_port}")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if not own_host(self):
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/test/"):   # a test.sh pass: its log, refreshed while it runs
            n = re.sub(r"[^\w-]", "", self.path[6:]); T = STATE / "tests"
            try: d = json.loads((T / (n + ".json")).read_text())
            except Exception: d = {}
            try: lg = (T / (n + ".log")).read_text(errors="replace")[-60000:]
            except Exception: lg = "(no output yet)" if d.get("state") == "running" else "no such test"   # test.sh makes the log after the json
            import html as _h
            ref = '<meta http-equiv="refresh" content="3">' if d.get("state") == "running" else ""
            body = f'<!doctype html><meta charset=utf-8>{ref}<body style="background:#0d1117;color:#c9d1d9;font:12px ui-monospace,monospace;margin:12px"><div style="color:#58a6ff">test #{d.get("issue")} · {d.get("state")}{" (exit "+str(d.get("rc"))+")" if "rc" in d else ""} · {_h.escape(d.get("dir",""))}</div><div style="color:#8b949e">$ {_h.escape(d.get("cmd",""))}</div><pre style="white-space:pre-wrap">{_h.escape(lg)}</pre><script>scrollTo(0,document.body.scrollHeight)</script>'.encode(); ct = "text/html; charset=utf-8"
        elif self.path.startswith("/api/runs"): body, ct = json.dumps(run_list()).encode(), "application/json"
        elif self.path.startswith("/api/stats"): body, ct = json.dumps(stats((re.search(r"period=(\w+)", self.path) or [0, "day"])[1] if re.search(r"period=(hour|day|month)", self.path) else "day", (re.search(r"src=(sub|api)", self.path) or [0, "both"])[1])).encode(), "application/json"
        elif self.path.startswith("/api/issue"):
            m = re.search(r"[?&]n=(\d{1,6})\b", self.path); rr = re.search(r"[?&]r=([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)", self.path)
            body, ct = json.dumps(issue_detail(int(m[1]), rr[1] if rr else None) if m else {"err": "bad n"}).encode(), "application/json"
        elif self.path.startswith("/api/board"):
            try: body, ct = json.dumps(board()).encode(), "application/json"
            except ValueError as e: body, ct = json.dumps({"error": str(e)}).encode(), "application/json"
        elif self.path.startswith("/api/version"): body, ct = json.dumps({"v": BOOT}).encode(), "application/json"
        elif self.path.startswith("/api/subs"): body, ct = json.dumps({**subs_month(), "presets": {k: {i: {"name": n, "usd": p} for i, (n, p) in v.items()} for k, v in PRESETS.items()}}).encode(), "application/json"
        elif self.path.startswith("/api/models"): body, ct = json.dumps(models_get()).encode(), "application/json"
        elif self.path.startswith("/api/speed"): body, ct = json.dumps(speed()).encode(), "application/json"
        elif self.path.startswith("/api/load"): body, ct = json.dumps(load_payload()).encode(), "application/json"
        else: body, ct = served_page().encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        # loopback only, and a cross-site page must not be able to write: require our own origin and a JSON body
        org = self.headers.get("Origin") or ""
        ok = self.path in ("/api/models", "/api/plans", "/api/promote") and own_host(self) and re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", org) and "json" in (self.headers.get("Content-Type") or "")
        try:
            raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20))   # read before any answer: a refusal that leaves the body unread resets the connection; the models panel posts back ~20 KB of read-only fields (#684)
            if not ok: out = {"err": "forbidden"}
            else:
                body = json.loads(raw or b"{}")
                if self.path == "/api/promote": out = promote(body.get("issue")) if isinstance(body, dict) else {"err": "bad request"}   # the owner's move (#560)
                else: out = (plans_set if self.path == "/api/plans" else models_set)(body)
        except Exception as e: out = {"err": "bad request"}
        body = json.dumps(out).encode(); self.send_response(400 if "err" in out else 200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

SELF = pathlib.Path(__file__).resolve(); LOADED = SELF.stat().st_mtime_ns
BOOT = time.time_ns()   # the page's version is this process, not this file: a restart for a changed hubmux.py, panes.js or address.sh leaves hub.py's mtime alone, and the open page must still reload
def watch_self():
    """Re-exec when this file changes on disk, so an edit goes live without anyone restarting the hub; open pages reload on the new /api/version."""
    while True:
        time.sleep(2)
        try:
            if SELF.stat().st_mtime_ns != LOADED: os.execv(sys.executable, [sys.executable, str(SELF)] + sys.argv[1:])
        except OSError: pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7700
    refresh_runs()
    threading.Thread(target=refresh_prs, daemon=True).start(); threading.Thread(target=refresh_quota, daemon=True).start(); threading.Thread(target=watch_self, daemon=True).start(); threading.Thread(target=refresh_board, daemon=True).start(); threading.Thread(target=load_sampler, daemon=True).start(); threading.Thread(target=watch_runs, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
