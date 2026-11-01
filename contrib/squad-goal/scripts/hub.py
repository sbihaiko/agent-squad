#!/usr/bin/env python3
"""Squad hub: one browser page that lists every live run dashboard (agent-squad dashboard_server processes) and embeds them.
usage: hub.py [port]   default 7700, loopback only. Read-only: it discovers servers via ps/lsof and reads record.jsonl."""
import datetime as dt, glob, hashlib, html, importlib.util, itertools, json, os, re, select, shlex, statistics as st, subprocess, sys, tempfile, threading, time, pathlib, urllib.parse, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
import agyquota, chainhealth, ghread, ghthrottle, modelreg, provkey, session_send, sessions   # the chains and the one registry resolve a model (ADR-0147); ghread is the one GitHub door (ADR-0277)
from issue_state import board_state, KINDS, TEST_CASE_TITLE, _utc
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

_columns_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns_module = importlib.util.module_from_spec(_columns_spec); _columns_spec.loader.exec_module(_columns_module)
_flow_spec = importlib.util.spec_from_file_location("board_flow_history", pathlib.Path(__file__).resolve().parent / "board_flow_history.py")
_flow = importlib.util.module_from_spec(_flow_spec); _flow_spec.loader.exec_module(_flow)
_hist_spec = importlib.util.spec_from_file_location("board_history", pathlib.Path(__file__).resolve().parent / "board_history.py")
_hist = importlib.util.module_from_spec(_hist_spec); _hist_spec.loader.exec_module(_hist)
_cfd_spec = importlib.util.spec_from_file_location("board_cfd", pathlib.Path(__file__).resolve().parent / "board_cfd.py")
_cfd = importlib.util.module_from_spec(_cfd_spec); _cfd_spec.loader.exec_module(_cfd)
_leadtime_spec = importlib.util.spec_from_file_location("board_leadtime", pathlib.Path(__file__).resolve().parent / "board_leadtime.py")
_leadtime = importlib.util.module_from_spec(_leadtime_spec); _leadtime_spec.loader.exec_module(_leadtime)
_fast_spec = importlib.util.spec_from_file_location("board_fast", pathlib.Path(__file__).resolve().parent / "board_fast.py")
board_fast = importlib.util.module_from_spec(_fast_spec); _fast_spec.loader.exec_module(board_fast)
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
PROJECTS, FORK_DIR = os.environ.get("SG_PROJECTS", ""), os.environ.get("SG_FORK_DIR", "")   # read once at import: the mux pins them per profile (#859)
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
live = {}                                          # unfinished runs: {path: (mtime, size, fields, transcript stamp, (children, spawn definitions))}

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

CLAUDE_PROJECTS = pathlib.Path.home() / ".claude" / "projects"   # where Claude Code keeps each session's transcript, in a folder named for its working directory
PROJECT_NAME_MAX = 200   # Claude Code cuts a longer sanitized working directory to this length and adds a hash suffix (#796)
FOLDERS = {}             # (run record, child): (working directory, transcript folder) once found (#802)
USAGE_FILES = {}         # one transcript file's messages, cached by (path, mtime, size): {key: [(message id, model, usage)]}

WRITE_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit"}   # squad/graph.py:WRITE_TOOLS; a child with one of these runs in its own worktree (#802)

def _epoch(s):
    """A transcript line's ISO timestamp as epoch seconds, None when it has none or it is not one (#802)."""
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None

def child_cwd(record, kid, definition, defs=None):
    """The directory a child works in, as squad/runners/children.py:workspace names it (#796, #802). A child that may write runs in
    <run folder>/ws/<logical>-<kid>. One that may not runs after a writer it names in "after" (in that writer's worktree), else in
    <run folder>/ws/<logical> (children.py:workspace), or the shared target when that folder is not there. The shared target is no child's, so None. A record from before the spawn carried its
    definition has none: the one folder ending in "-<kid>" is taken, and none or two are None."""
    run = pathlib.Path(os.path.realpath(str(record))).parent
    ws = run / "ws"
    if definition is None:
        found = []
        if ws.is_dir():
            found = sorted(c for c in ws.iterdir() if c.name.endswith("-" + kid) and len(c.name) > len(kid) + 1)
        if len(found) > 1:
            log_once(f"hub: {len(found)} folders end in -{kid} under {ws}; the child's folder is not known, its tokens are not counted")
        return os.path.realpath(found[0]) if len(found) == 1 else None
    tools = set(definition.get("tools") or [])
    workspace = definition.get("workspace")
    if tools & WRITE_TOOLS:
        logical = "target" if workspace in (None, ".") else str(workspace).replace("/", "_")
        return os.path.realpath(ws / f"{logical}-{kid}")
    for dep in definition.get("after") or []:   # a reader after a writer reads the writer's worktree, as workspace() does
        dd = (defs or {}).get(dep)
        if isinstance(dd, dict) and set(dd.get("tools") or []) & WRITE_TOOLS:
            c = child_cwd(record, dep, dd)
            if c and os.path.isdir(c):
                return c
    path = ws / ("target" if workspace in (None, ".") else str(workspace).replace("/", "_"))
    if workspace in (None, ".") or not path.is_dir():   # children.py:workspace falls back to the shared target when the folder is not there
        log_once(f"hub: child {kid} of {record} works in the shared target; its tokens are not counted")
        return None
    return os.path.realpath(path)

def transcript_folder(cwd):
    """Claude Code's folder for one working directory: the sanitized path, or for a long one the cut path it starts with (#796).
    A long path that more than one folder starts with is no folder of this one (#802)."""
    name = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    if os.path.isdir(CLAUDE_PROJECTS / name):   # isdir: a name over the filesystem limit is no folder, not an error
        return CLAUDE_PROJECTS / name
    if len(name) <= PROJECT_NAME_MAX:
        return None
    prefix = name[:PROJECT_NAME_MAX]
    try:
        names = [n for n in os.listdir(CLAUDE_PROJECTS) if n.startswith(prefix) and len(n) > PROJECT_NAME_MAX]
    except OSError:
        return None
    if len(names) != 1 or not os.path.isdir(CLAUDE_PROJECTS / names[0]):
        return None
    return CLAUDE_PROJECTS / names[0]

def transcript_usage(f):
    """The assistant messages of one transcript file: [(message id, raw model, usage, epoch or None)], cached by (path, mtime, size) (#796)."""
    try:
        st = os.stat(f)
    except OSError:
        return []
    key = (str(f), st.st_mtime, st.st_size)
    if key in USAGE_FILES:
        return USAGE_FILES[key]
    for old in [k for k in USAGE_FILES if k[0] == key[0]]:   # an older version of this file is never read again
        del USAGE_FILES[old]
    try:
        lines = f.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for i, l in enumerate(lines):
        if '"usage"' not in l:
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        m = d.get("message") if isinstance(d, dict) else None
        if not isinstance(m, dict):
            continue
        u, model = m.get("usage"), m.get("model")
        if not isinstance(u, dict) or not isinstance(model, str) or model.startswith("<"):   # "<synthetic>" is no model
            continue
        ts = _epoch(d["timestamp"]) if isinstance(d.get("timestamp"), str) else None
        rows.append((m.get("id") or f"{f}:{i}", model, u, ts))
    USAGE_FILES[key] = rows
    return rows

def transcript_files(folder):
    """Every transcript of one session folder; subagent transcripts sit in subfolders (#796). [] when unreadable."""
    try:
        return sorted(folder.rglob("*.jsonl"))
    except OSError:
        return []

def transcript_stamp(files):
    """The newest mtime of these transcripts, 0 when none. A file that goes between the listing and the stat is skipped (#802)."""
    mt = []
    for f in files:
        try:
            mt.append(os.stat(f).st_mtime)
        except OSError:
            pass
    return max(mt) if mt else 0

def child_folder(record, kid, definition=None, defs=None):
    """(working directory, transcript folder) of one child; the folder is None when it has none, and that is named once on stderr.
    A found folder is cached per (run, child): the polls of a live run do not walk the filesystem again (#802)."""
    key = (str(record), kid)
    if key in FOLDERS:
        return FOLDERS[key]
    cwd = child_cwd(record, kid, definition, defs)
    folder = transcript_folder(cwd) if cwd else None
    if folder is None and cwd is not None:
        log_once(f"hub: no transcript folder for child {kid} of {record} (looked for {cwd}); its tokens are not counted")
    if folder is not None:
        FOLDERS[key] = (cwd, folder)
    return cwd, folder

def child_groups(record, kids, defs, outs):
    """Each child's transcript folder once: {folder: (files, cutoff)}. A folder two working directories name is no child's alone, so
    it is dropped (#802). The cutoff is the latest recorded outcome of its children (only messages after it count), None when none has one."""
    groups = {}
    for kid, dfn in kids:
        cwd, folder = child_folder(record, kid, dfn, defs)
        if folder is None:
            continue
        g = groups.setdefault(folder, {"cwds": set(), "cut": []})
        g["cwds"].add(cwd)
        ts = [t for (k, _), t in outs.items() if k == kid and isinstance(t, (int, float))]
        g["cut"].append(max(ts) if ts else None)
    out = {}
    for folder, g in groups.items():
        if len(g["cwds"]) > 1:
            log_once(f"hub: {len(g['cwds'])} working directories share the transcript folder {folder}; its tokens are not counted")
            continue
        cuts = [c for c in g["cut"] if c is not None]   # a child with no recorded outcome adds no cutoff; none at all counts the whole transcript
        out[folder] = (transcript_files(folder), max(cuts) if cuts else None)
    return out

def child_stamp(record, kids, defs):
    """The newest transcript mtime of the children's folders: an unfinished run is cached by it as well as by its record (#796)."""
    return transcript_stamp([f for files, _ in child_groups(record, kids, defs, {}).values() for f in files])

def folder_usage(files, cutoff=None):
    """The tokens of the messages in these transcripts: {raw model: {in, out, cache, spend, calls}}. With a cutoff (epoch), only the
    messages after it count; a message with no timestamp is not after it (#802). Each message id counts once."""
    msgs = {}
    for f in files:
        for mid, model, u, ts in transcript_usage(f):
            if cutoff is not None and (ts is None or ts <= cutoff):
                continue
            msgs.setdefault(mid, (model, u))
    out = {}
    for model, u in msgs.values():
        fresh = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        cr, o = u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0
        spend, _ = _usage_spend(model, fresh, o, cr)
        x = out.setdefault(model, {"in": 0, "out": 0, "cache": 0, "spend": 0.0, "calls": 0})
        x["in"] += fresh + cr
        x["out"] += o
        x["cache"] += cr
        x["spend"] += spend
        x["calls"] += 1
    return out

def child_transcript_usage(record, kid, definition=None, defs=None, cutoff=None):
    """The tokens of one child session, read from its transcript (#796): {} when it has no folder. `in` includes the cache reads, as an
    outcome's tokens_in does; a cache write is fresh input."""
    _, folder = child_folder(record, kid, definition, defs)
    return folder_usage(transcript_files(folder), cutoff) if folder else {}

def scan(p):
    """Parse one record.jsonl into fields. Finished runs come from `hist`, unfinished ones are cached by (mtime, size)."""
    p = str(p)
    if p in hist and "v17" in hist[p]: return hist[p]   # v10 added the worktree paths a run works in (the load attribution reads them); v11 reads the run issue from request.txt; v12 reads every issue of a dispatch request's CHILD lines; v13 reads the test share a gate counted; v14 reads the card's label from the dispatch form too; v15 splits a run sub/api on the canonical model id (#791, #793); v16 counts a child's tokens from its transcript when no outcome reached the record (#796); v17 counts a fix round's messages after its last outcome, follows a reader to its writer, and drops a shared folder (#802)
    try: st = os.stat(p)
    except OSError: return None
    c = live.get(p)
    if c and c[0] == st.st_mtime and c[1] == st.st_size and c[3] == child_stamp(p, *c[4]): return c[2]
    spend = sub = api = 0.0; mod = {}; t0 = t1 = ver = None; reasons = []; land = False; issues = set(); ntok = 0; kind = "code"; fix = None; stack = None; bug = False; merge = False; head = None; rc = None; share = None
    rid = pathlib.Path(p).parent.name   # the run folder, read before the loop: the share below rebinds a local of its own, never `p`
    accepted, done_kids, wts, defs, outs = set(), set(), set(), {}, {}
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
            if e == "spawn_accepted" and d.get("child"):
                accepted.add(d["child"]); defs[d["child"]] = d.get("definition") if isinstance(d.get("definition"), dict) else None   # the definition names the child's workspace (#796)
            if e == "outcome" and d.get("node"):
                done_kids.add(d["node"])
                outs[(d["node"], d.get("entry"))] = ts   # the last outcome of each entry bounds the transcript (#802)
            if e == "outcome" and d.get("spend") is not None:
                d["spend"] = priced(d)
                spend += d["spend"]
                if SUBSCRIPTION.match(canon_model(d.get("model"))): sub += d["spend"]   # the canonical id, as stats() splits it (#791)
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
    # A child's transcript holds the tokens its record lacks: all of them when no outcome reached the record (killed, or running), and
    # the messages after its last outcome when it has one, so a fix round killed before its own outcome counts (#796, #802). Each
    # transcript folder is read once, and a folder two working directories name is no child's alone (#802).
    kids = [(kid, defs.get(kid)) for kid in sorted(accepted)]
    groups = child_groups(p, kids, defs, outs)
    stamp = transcript_stamp([f for files, _ in groups.values() for f in files])   # read before the transcripts, so a write during the read is seen by the next poll
    for files, cutoff in groups.values():
        for raw, v in folder_usage(files, cutoff).items():
            x = mod.setdefault(raw, {"in": 0, "out": 0, "cache": 0, "spend": 0.0, "calls": 0})
            for k in ("in", "out", "cache", "calls"): x[k] += v[k]
            x["spend"] += v["spend"]; spend += v["spend"]
            if SUBSCRIPTION.match(canon_model(raw)): sub += v["spend"]
            else: api += v["spend"]
    if has_req: src, why, issues = ("request", None, req_issues) if req_issues else (None, "request-no-issue", set())   # the request is the truth; the transcript never overrides it
    else: src, why = ("transcript" if issues else None), "request-missing"
    r = {"spend": round(spend, 4), "sub": round(sub, 4), "api": round(api, 4), "mod": mod, "v8": 1, "v9": 1, "v10": 1, "v11": 1, "v12": 1, "v13": 1, "v14": 1, "v15": 1, "v16": 1, "v17": 1, "t0": t0, "t1": t1, "verdict": ver, "reasons": reasons, "land": land, "issues": sorted(issues, key=int), "issue_src": src, "issue_why": why, "kind": kind, "fix": fix, "stack": stack, "bug": bug, "merge": merge, "head": head, "exit_code": rc, "agents": len(accepted - done_kids), "wts": sorted(wts), "share": share}
    r["id"] = rid   # the run folder: the label a run with no issue falls back on
    r["label"] = run_label(r)               # the card's name is computed here, so the page renders a value and holds no rule (ADR-0137)
    if ver is not None:
        hist[p] = r
        with LOCK:
            try: HIST.write_text(json.dumps(hist))
            except OSError: pass
    else: live[p] = (st.st_mtime, st.st_size, r, stamp, (kids, defs))
    return r

def state_of(r, age):
    ver, reasons = r["verdict"], r["reasons"]
    if ver is None: return "running" if age < STALL_SECS else "stalled"
    if ver == "failed" and r["land"] and reasons and all(str(x).startswith("ac-verify:no_ac_commands") for x in reasons): return "unverified"   # work landed, but the final gate had no acceptance command to run
    return ver

def run_roots(co):   # where a checkout keeps run folders: its runs/, and each run or child worktree's runs/ (#580, #585)
    co = pathlib.Path(co)
    return [co / "runs", *(w / "runs" for kind in ("run", "child") for w in sorted((co / "worktrees" / kind).glob("*")))]

def own_roots():
    """The run roots of this profile: its checkout, SG_PROJECTS and SG_FORK_DIR. A dashboard outside them is another profile's."""
    ds = set(run_roots(CHECKOUT))
    for x in [*PROJECTS.split(":"), FORK_DIR]:
        if x: ds.update(run_roots(x))
    return ds

def project_dirs():
    own = own_roots(); ds = set(own)
    for l in sh("/bin/ps", "-axwwo", "command=").splitlines():
        m = re.search(r"squad\.dashboard_server\s+(\S+)", l)
        if m and pathlib.Path(m.group(1)).parent in own: ds.add(pathlib.Path(m.group(1)).parent)
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

def load_squad(roots, names=("caps", "run_caps")):
    """The `squad` modules `names` (default `squad.caps` and `squad.run_caps`) from the first root that holds the package, and only from that root: a package that
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
            added = not (sys.path and sys.path[0] == root)   # the asked root goes first, else a squad earlier on sys.path answers for it
            if added: sys.path.insert(0, root)
            try:
                mods = [importlib.import_module("squad." + n) for n in names]
                here = getattr(mods[0], "__file__", None)   # the first module the import found: it must live under the root that was asked
                if here and pathlib.Path(here).resolve().is_relative_to(pathlib.Path(root).resolve()): return tuple(mods)
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
FOOT_BLOCK_COLORS = {"burn": "err", "landing": "wait", "gate": "none", "nightly": "run",
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
    part = {"text": text, "tone": "ok" if g["result"] == "pass" else "err", "tip": f"ran at {str(g['sha'])[:7]}"}
    took, avg = g.get("took"), g.get("avg")
    if isinstance(took, int) and isinstance(avg, int) and not isinstance(took, bool):
        part["times"] = f"took {_gate_span(took)} · avg {_gate_span(avg)}"   # the card's gate tile shows it; the footer line does not
    return [part]

def _gate_span(s):
    """A gate's run time: `45s`, `2m07s`. Its own name: the cycle countdown's `_span` reads `0m45s`."""
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"

def _nightly_parts(n, now):
    """`nightly pass 6h ago`: the last full suite in both modes (nightly.sh, #704), its result and its age. A pass reads in
    the ok tone and a fail in the err tone; a record the hub cannot read is `nightly ?` in the neutral tone, never a pass.
    No record at all draws no block.
    The tooltip carries which mode failed and the sha it ran at. The operator acts on the test ids, so every one of them
    is carried: the tooltip names them all, and `detail` gives the page a list a keyboard and a screen reader can open.
    The line itself stays one compact reading — the ids are MOVED off it, never dropped from the hub, as the gate's sha is
    (the section's rule (b))."""
    if n is None: return []
    modes = ("pass", "fail")
    red = isinstance(n, dict) and (n.get("parallel") != "pass" or n.get("serial") != "pass")
    ok = isinstance(n, dict) and n.get("result") in modes and n.get("sha") and n.get("parallel") in modes \
        and n.get("serial") in modes and isinstance(n.get("ts"), (int, float)) and not isinstance(n.get("ts"), bool) \
        and isinstance(n.get("ids"), list) and n["result"] == ("fail" if red else "pass")
    if not ok: return [{"text": "nightly ?", "tone": FOOT_NEUTRAL}]
    text = f"nightly {n['result']} {_age(max(now - n['ts'], 0.0))} ago"
    tip = [f"parallel {n['parallel']} · serial {n['serial']} · ran at {str(n['sha'])[:7]}"]
    ids = [str(x) for x in n["ids"] if x]
    part = {"text": text, "tone": "ok" if n["result"] == "pass" else "err"}
    if ids:
        tip.append(f"{len(ids)} failed: " + ", ".join(ids))
        part["detail"] = ids
    part["tip"] = " · ".join(tip)
    return [part]

def _cycle_state(now):
    """The watch cycle's own beat, read off the `HH:MM:SS CYCLE <n>s` lines watch.sh writes to cycle.log: when the last cycle
    ended and how long the last five took. None when the log has no such line."""
    try:
        log = STATE / "cycle.log"
        lines, wrote = log.read_text(errors="ignore").splitlines()[-4000:], log.stat().st_mtime
    except OSError: return None
    rows = [m.groups() for m in (re.match(r"(\d\d:\d\d:\d\d) CYCLE (\d+)s", l) for l in lines) if m]
    if not rows: return None
    # The line carries no date: it is dated by the log's own last write, never by today, so a loop that stopped yesterday
    # does not read as a few hours late.
    anchor = min(wrote, now)
    day = time.strftime("%Y-%m-%d ", time.localtime(anchor))
    end = time.mktime(time.strptime(day + rows[-1][0], "%Y-%m-%d %H:%M:%S"))
    if end > anchor + 1: end -= 86400   # the line is from before midnight
    return {"end": end, "secs": [int(r[1]) for r in rows[-5:]]}
def loop_log_stamp(line):
    """The UTC time a loop-log line starts with, as epoch seconds. None for a line written with no stamp: its age is unknown, never zero."""
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ", line)
    if not m: return None
    try: return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError: return None

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

def _fact(key, text, bad=False, parts=None, tip=None, detail=None):
    """One block of the line (#1843): its text, its own palette colour, and the parts it paints. A block with no parts
    of its own is the single part its text already is, with no tone — so the block's colour stands for the whole value.
    A part whose own reading earns a tone keeps it on top of that colour, which is how the block still says WHICH fact
    it is while the value says whether it is the bad one.
    `tip` is what the block does not say out loud but must not lose: a second number (the sha a gate ran at), read on
    hover. It is how a fact is MOVED off a reading bar and never deleted from it. `detail` is the same move for a list
    too long to read on the bar: every entry, drawn in an expandable list the page can open (the nightly's failing
    tests). The line is a reading and carries no press: the one control this bar ever held was the worktree count's
    `clean now`, and it left the page with the accordion that drew it (the owner's ruling 2026-10-16)."""
    return {"key": key, "text": text, "bad": bad, "color": FOOT_BLOCK_COLORS[key], "tip": tip, "detail": detail,
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


# --- Spend & limits (#926). The token guard reads its on/off switch, its daily cap and its pause factor from the profile,
# and env.sh sources that profile on every pass, so a value this section writes is read on the guard's next pass with no
# restart. The page renders; reading, validating, diffing, writing and recording live here (ADR-0137). The profile and the
# ledger are each written to a temp file and renamed, so a reader never sees half a file (ADR-0030).
SCRIPTS = pathlib.Path(__file__).resolve().parent
LIMIT_KEY = {"guard": ("SG_TOKEN_GUARD", "on"), "cap": ("SG_TOKEN_BURN_CAP", "1500000"),
             "factor": ("SG_TOKEN_PAUSE_FACTOR", "3"), "window": ("SG_TOKEN_BURN_WINDOW_H", "24")}
LIMIT_LABEL = {"cap": "token cap", "factor": "pause factor"}
LIMIT_LEDGER = "spend-limits-ledger.jsonl"
MODELS_LEDGER = "models-switch-ledger.jsonl"   # one line per model-level switch flip (SPEC 889 #920)

def profiles_dir():
    """The directory the profiles live in: SG_PROFILES_DIR when the runner exports it (env.sh does), else the seam's answer."""
    d = os.environ.get("SG_PROFILES_DIR")
    if d: return pathlib.Path(d)
    try:
        r = subprocess.run(["bash", "-c", '. "$1/address.sh" >/dev/null 2>&1; sg_profiles_dir', "_", str(SCRIPTS)],
                           capture_output=True, text=True, timeout=20)
    except Exception: return None
    return pathlib.Path(r.stdout.strip()) if r.stdout.strip() else None

def profile_file():
    """The profile in force: the one file the token guard sources on every pass. None when no profile is in force, and a
    theme that cannot name the file reads nothing and writes nothing."""
    slug, d = os.environ.get("SG_PROFILE", ""), profiles_dir()
    return d / f"{slug}.sh" if slug and d is not None else None

def profile_limits(path=None):
    """The four settings of that profile, as the guard reads them with the environment silent: address.sh's
    `sg_profile_setting <slug> <KEY> <default>` sources the file exactly as env.sh does. One bash call for all four."""
    path = path or profile_file()
    raw = {k: d for k, (_, d) in LIMIT_KEY.items()}
    if path is None: return raw
    script = '. "$1/address.sh" >/dev/null 2>&1; ' + "; ".join(
        f'printf "%s\\n" "$(sg_profile_setting "$2" {key} {d})"' for key, d in LIMIT_KEY.values())
    try:
        r = subprocess.run(["bash", "-c", script, "_", str(SCRIPTS), path.stem], capture_output=True, text=True, timeout=20,
                           env={**os.environ, "SG_PROFILES_DIR": str(path.parent)})
        got = r.stdout.splitlines() if r.returncode == 0 else []
    except Exception: got = []
    for (k, _), v in zip(LIMIT_KEY.items(), got):
        if v.strip(): raw[k] = v.strip()
    return raw

def _positive_int(v):
    """A daily cap: a positive whole number. A bool is not a number here, and neither is "1.5", "1_000" or "12 34"."""
    if isinstance(v, bool): return None
    try: n = int(str(v).strip())
    except (TypeError, ValueError): return None
    return n if n > 0 and str(v).strip() == str(n) else None

def _positive_num(v):
    """A pause factor: a positive number, whole or fractional. A bool is not one."""
    if isinstance(v, bool): return None
    try: n = float(str(v).strip())
    except (TypeError, ValueError): return None
    return n if n > 0 and n == n and n != float("inf") else None

def _guard_value(v):
    return v if v in ("on", "off") else None

def _num(n):
    return f"{n:g}"

def limits_settings(path=None):
    """The guard's settings as the theme shows and edits them: the profile's value where it is one, else the guard's default."""
    raw = profile_limits(path)
    raw["cap"] = _positive_int(raw["cap"]) or int(LIMIT_KEY["cap"][1])
    raw["factor"] = _positive_num(raw["factor"]) or float(LIMIT_KEY["factor"][1])
    raw["window"] = _positive_int(raw["window"]) or int(LIMIT_KEY["window"][1])
    raw["guard"] = "off" if str(raw["guard"]).strip().lower() in ("off", "0", "no", "false") else "on"
    return raw

def limit_line(cap, factor, window_h):
    """The hourly burn that pauses dispatch: SG_TOKEN_PAUSE_FACTOR times the cap's hourly share, as token-guard.sh computes it."""
    share = cap * 3600 // (window_h * 3600)
    return int(share * factor)

def limits_last():
    """The last change the ledger records, or None: {"at": local time, "text": the diff line}."""
    try: lines = [ln for ln in (STATE / LIMIT_LEDGER).read_text().splitlines() if ln.strip()]
    except OSError: return None
    for ln in reversed(lines):
        try: rec = json.loads(ln)
        except ValueError: continue
        if isinstance(rec, dict) and rec.get("text"): return rec
    return None

def limits_read():
    """The Spend & limits theme's data: the settings the guard will read on its next pass, the burn it measures, the pause
    marker it writes and the last change recorded. Every value is a fact of one file or one marker; the page renders it."""
    path = profile_file()
    s = limits_settings(path)
    hour = burn_read(hours=1)
    window = burn_read(hours=s["window"])
    return {"profile": str(path) if path else None, "guard": s["guard"], "cap": s["cap"], "factor": s["factor"],
            "window_h": s["window"], "pause_line": limit_line(s["cap"], s["factor"], s["window"]),
            "hour_burn": (hour or {}).get("total"), "window_burn": (window or {}).get("total"),
            "paused": (STATE / "token-pause").exists(), "last_change": limits_last()}

def limits_diff(old, new):
    """The diff a Save shows before it writes: one line per changed setting, in the order the theme reads them."""
    out = []
    if new["cap"] != old["cap"]: out.append(f"Spend & limits · token cap: {old['cap']} -> {new['cap']}")
    if new["factor"] != old["factor"]: out.append(f"Spend & limits · pause factor: {_num(old['factor'])} -> {_num(new['factor'])}")
    if new["guard"] != old["guard"]: out.append(f"token guard: {new['guard']}")
    return out

def profile_set_line(text, key, value):
    """One profile's line rewritten in place, every other line kept as it is. A profile writes a setting as
    `: "${KEY:=value}"`, as `KEY=value` or as `export KEY=value`; the last line that names the key is the one bash ends on,
    so it is the one replaced, and a profile that names none gets the line appended in the file's own `:=` form."""
    pats = [re.compile(r"(\$\{" + key + r":=)([^}]*)(\})"),
            re.compile(r"((?:^|\n)[ \t]*(?:export[ \t]+)?" + key + r"=)(\"[^\"]*\"|'[^']*'|[^\s#]*)")]
    hits = [(m.start(), m.end(), m, i) for i, p in enumerate(pats) for m in p.finditer(text)]
    if hits:
        _, _, m, i = max(hits, key=lambda h: h[0])
        return text[:m.start(2)] + value + text[m.end(2):]
    return text + ("" if not text or text.endswith("\n") else "\n") + f': "${{{key}:={value}}}"' + "\n"

def limits_write(path, changes):
    """Write the profile atomically, and only a file bash parses: a profile the guard sources is never half a file, and a
    save that would break the file is refused before it replaces the good one. Returns an error, or None."""
    try: text = path.read_text()
    except OSError: text = ""
    for key, value in changes:
        text = profile_set_line(text, key, value)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        r = subprocess.run(["bash", "-n", str(tmp)], capture_output=True, text=True, timeout=20)
        if r.returncode:
            msg = next((ln for ln in r.stderr.splitlines() if ln.strip()), "bash -n failed")
            return f"the profile would not parse: {msg}"
        os.replace(tmp, path)
    except OSError as e:
        return f"the profile could not be written: {e.strerror or e}"
    finally:
        try: tmp.unlink()
        except OSError: pass
    return None

def limits_record(diff):
    """Every change with its local time, one line per change, in the state directory's own ledger. The file is rewritten
    through a temp file and a rename, so a reader sees whole lines only."""
    path = STATE / LIMIT_LEDGER
    recs = []
    try:
        for ln in path.read_text().splitlines():
            try: recs.append(json.loads(ln))
            except ValueError: continue
    except OSError: recs = []
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    recs += [{"at": at, "text": t} for t in diff]
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
        os.replace(tmp, path)
    except OSError: pass
    finally:
        try: tmp.unlink()
        except OSError: pass

def limits_set(body):
    """POST /api/limits: validate, diff, then write. With `confirm` false it answers the diff and writes nothing, which is
    what the theme shows before it asks. A cap that is not a positive whole number is refused with a message and no write."""
    if not isinstance(body, dict): return {"err": "bad request"}
    path = profile_file()
    if path is None: return {"err": "no profile in force: SG_PROFILE and SG_PROFILES_DIR name none"}
    cur = limits_settings(path)
    cap = _positive_int(body.get("cap", cur["cap"]))
    if cap is None: return {"err": "token cap must be a positive whole number"}
    factor = _positive_num(body.get("factor", cur["factor"]))
    if factor is None: return {"err": "pause factor must be a positive number"}
    guard = _guard_value(body.get("guard", cur["guard"]))
    if guard is None: return {"err": "token guard is on or off"}
    new = {"cap": cap, "factor": factor, "guard": guard}
    diff = limits_diff(cur, new)
    if body.get("confirm") is not True: return {"diff": diff}
    if diff:
        changes = []
        if new["cap"] != cur["cap"]: changes.append((LIMIT_KEY["cap"][0], str(new["cap"])))
        if new["factor"] != cur["factor"]: changes.append((LIMIT_KEY["factor"][0], _num(new["factor"])))
        if new["guard"] != cur["guard"]: changes.append((LIMIT_KEY["guard"][0], new["guard"]))
        err = limits_write(path, changes)
        if err: return {"err": err}
        limits_record(diff)
    return {"diff": diff, "state": limits_read()}

# --- Settings > Communication (#924). How the agent reads and writes Slack for this profile: the prefix, the cadence, the
# channels and the people it answers. The page renders; reading, validating, diffing and writing live here (ADR-0137). The
# file is per profile, gitignored and written through a temp file and a rename (ADR-0030). The hub holds no token: posting
# goes through the owner's Slack account by the Claude Slack connector, and nothing reads this file until a bridge does.
SLACK_LANGS = ("pt-br", "en-US", "es")
SLACK_ROLES = ("Manager", "Colleague")
SLACK_EVENTS = ("stg", "prod", "dev", "bug", "fix")
SLACK_DEFAULT = {"connection": "not connected", "prefix": "[agent]", "lang": "en-US", "tick": 5, "max": 30, "quiet": "",
                 "other": "ignore", "dm": False, "audio": False, "channels": [], "people": [],
                 "templates": {"stg": "<prefix>\nDeployed to STG and the e2e run is green. PR <pr>: <title> (<issue>). Please test on stg.",
                               "prod": "<prefix>\nDeployed to PROD. PR <pr>: <title> (<issue>).",
                               "dev": "<prefix>\nLanded in dev. PR <pr>: <title> (<issue>). Next: promotion to stg.",
                               "bug": "<prefix>\nI confirmed this is a bug: <mechanism>. Filed as <issue>, in Todo. I will post here when the fix reaches stg.",
                               "fix": "<prefix>\n<real state, with dates and links>. Next step: <who>."}}
SLACK_NEW_CHANNEL = {"watch": True, "thread": True, "mention": False,
                     "events": {"stg": True, "prod": True, "dev": False, "bug": True, "fix": True}}
SLACK_LABEL = {"prefix": "prefix", "lang": "default language", "tick": "read every", "max": "up to",
               "quiet": "quiet hours", "other": "anyone not listed", "dm": "owner self-DM", "audio": "audio"}
SLACK_DIFF = "Communication · Slack · "
_SLACK_HHMM = re.compile(r"^([01]\d|2[0-3])-([01]\d|2[0-3])$")
_SLACK_CHANNEL = re.compile(r"^#?[^\s#]{1,80}$")

def slack_file():
    """The per-profile Slack settings beside the profile, or None when no profile is in force."""
    slug, d = os.environ.get("SG_PROFILE", ""), profiles_dir()
    return d / f"{slug}.slack.json" if slug and d is not None else None

def slack_read(path):
    """The saved settings over the defaults; the defaults alone when the file is absent, unreadable or not an object."""
    cfg = json.loads(json.dumps(SLACK_DEFAULT))
    try: saved = json.loads(path.read_text()) if path else {}
    except (OSError, ValueError): saved = {}
    if isinstance(saved, dict): cfg.update({k: v for k, v in saved.items() if k in SLACK_DEFAULT})
    return cfg

def slack_view(path):
    """What the page draws: the saved settings, and the defaults a new channel starts from."""
    return {**slack_read(path), "new_channel": SLACK_NEW_CHANNEL}

def _slack_int(v, lo, hi):
    if isinstance(v, bool): return None
    try: n = int(str(v).strip())
    except (TypeError, ValueError): return None
    return n if lo <= n <= hi and str(v).strip() == str(n) else None

def _slack_rows(items, check):
    """The rows of one list from the form, each checked; a name that appears twice is refused."""
    if not isinstance(items, list): return None, "the list is not a list"
    out = []
    for it in items:
        if not isinstance(it, dict): return None, "a row is not an object"
        row, msg = check(it)
        if msg: return None, msg
        out.append(row)
    if len({r["name"] for r in out}) != len(out): return None, "a name appears more than once"
    return out, None

def _slack_channel(it):
    name = str(it.get("name", "")).strip()
    if not _SLACK_CHANNEL.match(name): return None, "a channel is a name or id with no space"
    if it.get("lang") not in SLACK_LANGS: return None, "a channel language is one of " + ", ".join(SLACK_LANGS)
    flags = {k: it.get(k) for k in ("watch", "thread", "mention")}
    if not all(isinstance(v, bool) for v in flags.values()): return None, "a channel switch is on or off"
    ev = it.get("events") if isinstance(it.get("events"), dict) else None
    if ev is None or set(ev) - set(SLACK_EVENTS) or not all(isinstance(v, bool) for v in ev.values()):
        return None, "a channel announces only the release events, on or off"
    return {"name": name, "lang": it["lang"], **flags, "events": {k: bool(ev.get(k, False)) for k in SLACK_EVENTS}}, None

def _slack_person(it):
    name = str(it.get("name", "")).strip()
    if not name or len(name) > 80: return None, "a person has a name of 1 to 80 characters"
    if it.get("role") not in SLACK_ROLES: return None, "role is Manager or Colleague"
    return {"name": name, "role": it["role"]}, None

def _slack_templates(items):
    if not isinstance(items, dict) or set(items) != set(SLACK_EVENTS): return None, "a template is kept for each release event"
    if not all(isinstance(t, str) and 1 <= len(t) <= 600 for t in items.values()): return None, "a template is text of 1 to 600 characters"
    return {k: items[k] for k in SLACK_EVENTS}, None

def _slack_untracked(path):
    """True when the folder is in a git work tree and git would not ignore the file there."""
    r = subprocess.run(["git", "-C", str(path.parent), "check-ignore", "-q", str(path)], capture_output=True, timeout=20)
    return r.returncode == 1

def slack_clean(body, cur):
    """The typed settings, checked: (settings, None), or (None, the message for the first value that breaks its rule)."""
    prefix = str(body.get("prefix", cur["prefix"])).strip()
    if not prefix or "\n" in prefix or len(prefix) > 40: return None, "prefix is one line of 1 to 40 characters"
    lang = body.get("lang", cur["lang"])
    if lang not in SLACK_LANGS: return None, "default language is one of " + ", ".join(SLACK_LANGS)
    tick = _slack_int(body.get("tick", cur["tick"]), 1, 30)
    if tick is None: return None, "read every is a whole number of minutes from 1 to 30"
    mx = _slack_int(body.get("max", cur["max"]), 2, 120)
    if mx is None or mx < tick: return None, "up to is a whole number of minutes from 2 to 120, not less than read every"
    quiet = str(body.get("quiet", cur["quiet"])).strip()
    if quiet and not _SLACK_HHMM.match(quiet): return None, "quiet hours are HH-HH local time, or empty"
    other = body.get("other", cur["other"])
    if other not in ("ignore", "reply"): return None, "anyone not listed is ignored or gets a fixed reply"
    dm = body.get("dm", cur["dm"])
    audio = body.get("audio", cur["audio"])
    if not isinstance(dm, bool) or not isinstance(audio, bool): return None, "owner self-DM and audio are on or off"
    channels, msg = _slack_rows(body.get("channels", cur["channels"]), _slack_channel)
    if msg: return None, msg
    people, msg = _slack_rows(body.get("people", cur["people"]), _slack_person)
    if msg: return None, msg
    templates, msg = _slack_templates(body.get("templates", cur["templates"]))
    if msg: return None, msg
    return {"connection": cur["connection"], "prefix": prefix, "lang": lang, "tick": tick, "max": mx, "quiet": quiet,
            "other": other, "dm": dm, "audio": audio and dm, "channels": channels, "people": people,
            "templates": templates}, None

def _slack_show(v):
    if v is True: return "on"
    if v is False: return "off"
    return str(v) if str(v) else "always on"

def _slack_rowdiff(kind, name, old, new):
    """The diff lines for one channel or person kept in both lists, by name."""
    if kind == "person":
        return [f"{SLACK_DIFF}person {name} · role: {old['role']} -> {new['role']}"] if old["role"] != new["role"] else []
    out = [f"{SLACK_DIFF}channel {name} · {f}: {_slack_show(old[f])} -> {_slack_show(new[f])}"
           for f in ("lang", "watch", "thread", "mention") if old[f] != new[f]]
    out += [f"{SLACK_DIFF}channel {name} · {ev} posts: {_slack_show(old['events'][ev])} -> {_slack_show(new['events'][ev])}"
            for ev in SLACK_EVENTS if old["events"][ev] != new["events"][ev]]
    return out

def slack_diff(old, new):
    """The diff a Save shows before it writes: one line per changed setting, row or switch, each prefixed for this theme."""
    out = []
    for key, label in SLACK_LABEL.items():
        if new[key] != old[key]: out.append(f"{SLACK_DIFF}{label}: {_slack_show(old[key])} -> {_slack_show(new[key])}")
    for kind, key in (("channel", "channels"), ("person", "people")):
        before = {r["name"]: r for r in old[key]}
        after = {r["name"]: r for r in new[key]}
        for name in after.keys() - before.keys():
            out.append(f"{SLACK_DIFF}{kind} {name} added")
        for name in before.keys() - after.keys():
            out.append(f"{SLACK_DIFF}{kind} {name} removed")
        for name in after.keys() & before.keys():
            out += _slack_rowdiff(kind, name, before[name], after[name])
    out += [f"{SLACK_DIFF}template {ev}: changed" for ev in SLACK_EVENTS if new["templates"][ev] != old["templates"][ev]]
    return out

def slack_write(path, cfg):
    """The settings file, whole or not at all: a temp file, then a rename. Returns an error, or None."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        return f"the settings could not be written: {e.strerror or e}"
    finally:
        try: tmp.unlink()
        except OSError: pass
    return None

def slack_set(body):
    """POST /api/slack: validate, diff, then write. With `confirm` false it answers the diff and writes nothing."""
    if not isinstance(body, dict): return {"err": "bad request"}
    path = slack_file()
    if path is None: return {"err": "no profile in force: SG_PROFILE and SG_PROFILES_DIR name none"}
    cur = slack_read(path)
    new, err = slack_clean(body, cur)
    if err: return {"err": err}
    diff = slack_diff(cur, new)
    if body.get("confirm") is not True: return {"diff": diff}
    if diff:
        if _slack_untracked(path):
            return {"err": "the settings file would be tracked: its folder is in a git work tree and git does not ignore it"}
        err = slack_write(path, new)
        if err: return {"err": err}
    return {"diff": diff, "state": slack_view(path)}

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
    nightly = _nightly_parts(src.get("nightly"), now)
    items = [_fact("burn", _burn_fact(src.get("burn")), bad=True, tip=_burn_tip(src.get("burn"))),
             _fact("landing", FOOT_SEP.join(p["text"] for p in land), parts=land),
             _fact("gate", FOOT_SEP.join(p["text"] for p in gate), bad=any(p["tone"] == "err" for p in gate),
                   parts=gate or None, tip=(gate[0].get("tip") if gate else None)),
             _fact("nightly", FOOT_SEP.join(p["text"] for p in nightly), bad=any(p["tone"] == "err" for p in nightly),
                   parts=nightly or None, tip=(nightly[0].get("tip") if nightly else None),
                   detail=(nightly[0].get("detail") if nightly else None)),
             _fact("quota", _quota_fact(src.get("quota"), now)),
             _fact("cycle", "cycle off" if src.get("loop") is False and src.get("cycle") else _cycle_fact(src.get("cycle"), now)),   # a stopped loop has no next cycle
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
    """The one line the gate wrapper leaves in $SG_STATE/gate-status, `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`, and for a
    gate that ran the test battery a fifth field, its selection (`selected N files` or `all: <reason>`): the latest gate, its
    result, the sha it ran at and that selection. None when the file is absent or is not that line."""
    f = _read(STATE / "gate-status").rstrip("\n").split("\t")
    if len(f) not in (4, 5) or not f[0].isdigit() or not all(f[1:4]) or (len(f) == 5 and not f[4]): return None
    line = {"kind": f[1], "result": f[2], "sha": f[3], "ts": float(f[0])}
    if len(f) == 5: line["selection"] = f[4]
    took, avg = _gate_times(f[1])
    if took is not None: line["took"], line["avg"] = took, avg
    return line

def _gate_times(kind, n=20):
    """How long the last gate of this kind took and the mean of the last `n`, in seconds, from $SG_STATE/gate-history
    (`<finish> TAB <kind> TAB <result> TAB <sha> TAB <start>`). A line whose start equals its finish reused a verdict and
    ran nothing, so it counts in neither. (None, None) when no gate of the kind ran."""
    runs = []
    for ln in _read(STATE / "gate-history").splitlines():
        f = ln.split("\t")
        if len(f) >= 5 and f[1] == kind and f[0].isdigit() and f[4].isdigit() and int(f[0]) > int(f[4]):
            runs.append(int(f[0]) - int(f[4]))
    if not runs: return None, None
    last = runs[-n:]
    return runs[-1], round(sum(last) / len(last))

def _nightly_status():
    """The one result line the nightly full suite leaves in $SG_STATE/nightly-result, `<epoch> TAB <pass|fail> TAB <sha>
    TAB <parallel> TAB <serial> TAB <ids>` (nightly.sh, #704): the last full run in both modes, its result, the sha it ran
    at, each mode's own result and the failing test ids. None when the file is absent or is not that line.

    `<ids>` is ONE JSON array of strings, `[]` when none: a pytest node id may itself carry a space (a parametrized case
    id), so the ids are never read as a whitespace-separated list, which would cut such an id in two.

    A record whose modes are outside pass|fail, whose ids are not that array, or whose result contradicts its modes — a
    `pass` over a red mode — is not that line either: a record the hub cannot trust is no reading, and it never draws a
    pass. The record is one line, and it is read whole: a file with anything after its one line is no reading either,
    because a first line alone never proves the file whole. `gh_dispatch_hold` drops the same lines, so the hub and the
    hold never disagree about which record they are looking at (ADR-0153)."""
    text = _read(STATE / "nightly-result")
    if text.endswith("\n"): text = text[:-1]   # the one line may end in a newline, and nothing else follows it
    if "\n" in text: return None
    f = text.split("\t")
    if len(f) != 6 or not f[0].isdigit() or f[1] not in ("pass", "fail") or not f[2]: return None
    if f[3] not in ("pass", "fail") or f[4] not in ("pass", "fail"): return None
    if f[1] != ("pass" if f[3] == "pass" and f[4] == "pass" else "fail"): return None
    try: ids = json.loads(f[5])
    except (TypeError, ValueError): return None
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids): return None
    return {"result": f[1], "sha": f[2], "parallel": f[3], "serial": f[4], "ids": ids, "ts": float(f[0])}

def _newest_mtime(*paths):
    ts = []
    for p in paths:
        try: ts.append(pathlib.Path(p).stat().st_mtime)
        except OSError: pass
    return max(ts) if ts else None

def _loop_on(pid_file=None):
    """True while the supervisor's pid file (watchd.sh writes $SG_STATE/watchd.pid) names a live process: a signal 0, never a `ps`.
    `auto/off` pauses dispatch, so it reads off even while that pid is live (#788)."""
    pf = pathlib.Path(pid_file) if pid_file else STATE / "watchd.pid"
    if (pf.parent / "auto" / "off").exists(): return False
    if next((pf.parent / "auto").glob("loop-stop-*"), None) is not None: return False   # #956: an open loop-bug holds dispatch
    try: pid = int(_read(pf).split()[0])
    except (ValueError, IndexError): return False
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True   # alive, owned by another user
    except OSError: return False
    return True

LOOP_STAMP = HUBDIR / "loop-stamp.json"   # each on/off change the sampler saw, with the machine's local time of it (#826)

def loop_stamp_next(prev, on, when):
    """The stamp after one sighting: a state never seen before has no time, and a change takes the time it was seen."""
    if not prev or "on" not in prev: return {"on": on, "since": None}
    if bool(prev["on"]) == on: return prev
    return {"on": on, "since": when}

def loop_seen(on, now=None):
    now = time.time() if now is None else now
    prev = _json(LOOP_STAMP)
    nxt = loop_stamp_next(prev, on, dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"))
    if nxt != prev:
        tmp = LOOP_STAMP.with_suffix(".tmp"); tmp.write_text(json.dumps(nxt)); os.replace(tmp, LOOP_STAMP)
    return on

def loop_status():
    on = _loop_on(); prev = _json(LOOP_STAMP) or {}
    return {"on": on, "since": prev.get("since") if bool(prev.get("on")) == on else None}

LOOP_AUDIT = STATE / "loop-audit.jsonl"   # one line per loop switch change: who, when, from where, and what a stop-now killed (#923)
LOOP_READ_ONLY = "read-only from the tailnet: SQUAD_TAILNET_LOOP_WRITE is not on (tailnet.loop_write)"
LOOP_ACTIONS = ("start", "drain", "stop-now")
WATCHD = pathlib.Path(__file__).resolve().parent / "watchd.sh"

def loop_watchd(*args):
    # the mux pins a profile's env only while it imports this file: watchd gets this profile, its state and its checkout, never the hub's cwd
    env = {**_PROFILE_ENV, "SG_STATE": str(STATE), "SG_CHECKOUT": CHECKOUT}
    return subprocess.run(["bash", str(WATCHD), *args], capture_output=True, text=True, timeout=60, env=env, cwd=CHECKOUT).returncode

def _pid_alive(pid):
    try: os.kill(pid, 0)
    except PermissionError: return True
    except OSError: return False
    return True

def loop_alive():
    try: pid = int(_read(STATE / "watchd.pid").split()[0])
    except (ValueError, IndexError): return False
    return _pid_alive(pid)

LOOP_RUN_CMD = os.environ.get("SG_RUN_CMD_RE") or r"[s]quad start( |$)"   # a squad run's command, as heartbeat.sh reads it
LOOP_LAUNCH_S = 60 * float(os.environ.get("HB_DEAD_IDLE_MIN") or 1)   # a record with no call_start that moved this recently is a launch, not a death

def loop_record(run):
    return pathlib.Path(run) / "record.jsonl" if run.startswith("/") else pathlib.Path(CHECKOUT) / "runs" / run / "record.jsonl"

def loop_run_pid(run, rows=None):
    """The pid of a live run's own process: its record's last call_start, while that pid is alive as a squad run (its command
    matches SG_RUN_CMD_RE) and is not this hub. A reused pid that names any other process is never a run's. A run is started
    in its own session (start_new_session), so the supervisor's group stop never reaches it."""
    for raw in reversed(_read(loop_record(run)).splitlines()):
        try: e = json.loads(raw)
        except ValueError: continue
        if e.get("event") == "call_start" and isinstance(e.get("pid"), int):
            pid = e["pid"]
            if pid <= 1 or pid == os.getpid(): return None
            cmd = next((c for p, _, c in (_ps_rows() if rows is None else rows) if p == pid), "")
            return pid if re.search(LOOP_RUN_CMD, cmd) else None
    return None

def loop_run_live(run, rows=None, now=None):
    """A run is live while it has no verdict and its process runs: the last call_start pid is a live squad run, or, before its
    first call, its record moved within the launch window. A run that died with no verdict (a crash, a kill) is not live."""
    f = loop_record(run); text = _read(f)
    if re.search(r'"event":\s*"verdict"', text): return False
    if re.search(r'"event":\s*"call_start"', text): return loop_run_pid(run, rows) is not None
    try: return (time.time() if now is None else now) - f.stat().st_mtime < LOOP_LAUNCH_S
    except OSError: return False

def loop_live():
    """The live runs (an active-runs line whose run still runs, `loop_run_live`) and the held gate (the gate lock's pid file
    naming a live pid): what a stop kills, and what a drain waits for."""
    rows = _ps_rows()
    runs = [r for r in (x.strip() for x in _read(STATE / "active-runs.txt").splitlines()) if r and loop_run_live(r, rows)]
    gate = _read(STATE / "locks" / "gate" / "pid").strip()
    gates = [f"gate pid {gate}"] if gate.isdigit() and _pid_alive(int(gate)) else []
    return {"runs": runs, "gates": gates}

def loop_audit_add(who, frm, action, killed=None, **extra):
    line = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "who": who, "from": frm, "action": action}
    if killed is not None: line["killed"] = killed
    line.update(extra)   # a failed start's reason, or a stop-now's survivors
    with LOOP_AUDIT.open("a") as f: f.write(json.dumps(line) + "\n")

def loop_audit_last(n):
    out = []
    for raw in _read(LOOP_AUDIT).splitlines()[-n:]:
        try: out.append(json.loads(raw))
        except ValueError: pass
    return out

LOOP_STARTING = STATE / "auto" / "starting"   # the hub's pending mark: written before watchd start runs, removed when it returns
LOOP_START_WINDOW = 90   # a mark older than this outlived its hub (the call times out at 60 s): it reads off

def loop_state(last, now=None):
    """on or draining while the supervisor is alive; starting only while a hub start is in progress; off otherwise. A start
    that failed reads off, and the view carries the failure, so no disabled button hides it."""
    if loop_alive(): return "draining" if (STATE / "auto" / "drain").exists() else "on"
    now = time.time() if now is None else now
    try:
        if now - LOOP_STARTING.stat().st_mtime < LOOP_START_WINDOW: return "starting"
    except OSError: pass
    return "off"

LOOP_STATE_ACTIONS = {"off": ["start"], "on": ["drain", "stop-now"], "draining": ["stop-now"], "starting": []}   # the hub's rule, so no page holds one

LOOP_STARTED = STATE / "watchd.started"   # every watchd start stamps it with its epoch second, from the hub or the terminal

def loop_started_after(at):
    """True when a watchd start came after the audit time `at`: an old hub failure then no longer describes the loop."""
    try: when = dt.datetime.fromisoformat(at).timestamp()
    except (TypeError, ValueError): return False
    try: return int(_read(LOOP_STARTED).split()[0]) > int(when)
    except (ValueError, IndexError): return False

def loop_view(a):
    last = (loop_audit_last(1) or [{}])[-1]
    tn = tailnet_block()
    write = a.allowed and (not a.tailnet or tn.get("loop_write") is True)
    state = loop_state(last)
    return {"state": state, "actions": list(LOOP_STATE_ACTIONS.get(state, [])) if write else [], "who": last.get("who", ""), "when": last.get("at", ""), "live": loop_live(),
            "failed": last.get("failed", "") if state == "off" and not loop_started_after(last.get("at", "")) else "",
            "log": loop_audit_last(20)[::-1], "write": write, "reason": "" if write else LOOP_READ_ONLY,
            "tailnet": a.tailnet, "access": {"loop_write": tn.get("loop_write") is True, "terminals_send": tn.get("terminals_send") is True}}   # Settings > Access reads the tailnet rule here

def loop_refusal(a, origin_ok, tn):
    """The reason a loop write is refused, or '' when it may run: the access rule, then the tailnet grant, then the origin and body."""
    if not a.allowed: return a.reason
    if a.tailnet and tn.get("loop_write") is not True: return LOOP_READ_ONLY
    return "" if origin_ok else "forbidden"

def _ps_rows():
    """Every process as (pid, ppid, command), from one `ps`."""
    try: out = subprocess.run(["/bin/ps", "-axwwo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError): return []
    rows = []
    for line in out.splitlines():
        f = line.split(None, 2)
        if len(f) >= 2 and f[0].isdigit() and f[1].isdigit(): rows.append((int(f[0]), int(f[1]), f[2] if len(f) > 2 else ""))
    return rows

def loop_kill(root):
    """End `root` and every process under it: TERM, then KILL what is left. True when `root` is gone after it."""
    rows = _ps_rows(); kids = {}
    for pid, ppid, _ in rows: kids.setdefault(ppid, []).append(pid)
    tree, stack = [], [root]
    while stack:
        pid = stack.pop(); tree.append(pid); stack.extend(kids.get(pid, ()))
    for sig in (15, 9):
        for pid in tree:
            try: os.kill(pid, sig)
            except OSError: pass
        for _ in range(30):
            if not any(_pid_alive(p) for p in tree): return True
            time.sleep(0.1)
    return not _pid_alive(root)

def loop_mark_killed(run):
    """Give a run stop-now ended its verdict at once, through squad.killed (the one writer), so it never reads live again.
    False when it could not be written: the run then still reads not live, since its process is gone."""
    try:
        (K,) = load_squad(squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), ("killed",))
        K.mark_killed(loop_record(run).parent, "killed/stop-now")
        return True
    except Exception as e:
        log_once(f"hub: stop-now could not write the killed verdict of {run}: {type(e).__name__}: {' '.join(str(e).split())}")
        return False

def loop_stop_now(live):
    """End each listed run and gate; answer what died and what survived. A run that died gets its killed verdict."""
    killed, survived = {"runs": [], "gates": []}, {"runs": [], "gates": []}
    for r in live["runs"]:
        pid = loop_run_pid(r)
        if pid and loop_kill(pid):
            killed["runs"].append(r); loop_mark_killed(r)
        else: survived["runs"].append(r)
    for g in live["gates"]:
        pid = int(g.rsplit(" ", 1)[-1])
        (killed if pid != os.getpid() and loop_kill(pid) else survived)["gates"].append(g)
    return killed, survived

def loop_switch(a, body, origin_ok):
    why = loop_refusal(a, origin_ok, tailnet_block())
    if why: return 403, {"err": "forbidden", "reason": why}
    action = body.get("action") if isinstance(body, dict) else None
    if action not in LOOP_ACTIONS: return 400, {"err": "bad request"}
    state = loop_state((loop_audit_last(1) or [{}])[-1])
    if action not in LOOP_STATE_ACTIONS.get(state, []):   # the route applies the rule the view publishes
        return 409, {"err": "the loop is not running" if state == "off" else f"the loop is {state}: {action} does not apply"}
    who, frm = a.login or "owner", "tailnet" if a.tailnet else "loopback"
    if action == "start":
        for f in ("off", "drain", "drain-pause"): (STATE / "auto" / f).unlink(missing_ok=True)
        LOOP_STARTING.parent.mkdir(parents=True, exist_ok=True); LOOP_STARTING.write_text(f"{time.time()}\n")
        try:
            try: rc = loop_watchd("start")
            except subprocess.TimeoutExpired: rc = -1
            failed = "watchd did not start" if rc else "" if loop_alive() else "the supervisor was not running after its start"
        finally: LOOP_STARTING.unlink(missing_ok=True)
        if failed:
            loop_audit_add(who, frm, action, failed=failed)
            return 500, {"err": failed}
        loop_audit_add(who, frm, action)
        return 200, {"ok": True, "action": action}
    if action == "drain":
        if loop_watchd("drain"): return 500, {"err": "watchd did not drain"}
        loop_audit_add(who, frm, action)
        return 200, {"ok": True, "action": action}
    if body.get("confirm") is not True: return 400, {"err": "confirm the stop: it kills the live runs and gates listed"}
    live = loop_live()
    if loop_watchd("stop"): return 500, {"err": "watchd did not stop"}
    killed, survived = loop_stop_now(live)
    extra = {"survived": survived} if survived["runs"] or survived["gates"] else {}
    loop_audit_add(who, frm, action, killed, **extra)
    return 200, {"ok": True, "action": action, "killed": killed, **extra}

def foot_sources(now=None):
    """One reading of every source the line draws on. A source that is not there reads as `None` for its own fact only,
    so one unreadable file costs one `?` and never the rest of the line."""
    now = time.time() if now is None else now
    out = {"landing": _last_landing(), "gate": _gate_status(), "nightly": _nightly_status(),
           "quota": {"codex": limited_until(_read(SHARED / "codex-limited"), now),
                     "agy": limited_until(_read(SHARED / "agy-limited"), now)}}
    plans = plans_get()   # the hand-entered percentage, for a probe that did not answer: the two things #1839's tokens read
    out["burn"] = burn_cached(now)
    out["cycle"] = _cycle_state(now)
    out["loop"] = loop_seen(_loop_on(), now)
    out["subs"] = {k: {"probe": quota_row(k, now), "quota": (plans.get(k) or {}).get("quota")} for k in SUBS}
    return out

def foot_refresh(now=None):
    """One pass of the footer's sources, kept where the page serves it from: the sampler thread calls this, so the line
    can never be written from a figure the loop has stopped refreshing. A failed pass keeps the last good facts."""
    src = foot_sources(now)
    LOAD_STATE["facts"] = foot_items(src, now)
    return LOAD_STATE["facts"]

SPEC_TIP_LINES = 8   # the spec tile's tooltip names at most this many specs, then "+k more"
NOSPEC_HOURS = 24    # the No spec group counts the closed cards opened in this many hours (owner)

def nospec_count(items, moves=None, window=None, now=None):
    """The No spec group (bugs and single tickets): `total` is its open cards plus its closed cards opened in the last
    NOSPEC_HOURS hours by the read's own opened-at (`o`); `closed` is those closed ones. A card is in the group as the board's No spec
    lane holds it: no spec label, no loop-bug label, and no parent spec, or a parent spec the board draws closed (an open
    card only: a closed card of a closed spec is history). An abandoned card, or a closed card with no opened-at, never
    counts. The column is `drawn_column`, with the door's overlay, so the lane, the summary and the tile agree (ADR-0137)."""
    if moves is None: moves = read_moves() if overlay_on() else {}
    if window is None: window = pending_window()
    since = (time.time() if now is None else now) - NOSPEC_HOURS * 3600
    ends, closed_col = (COLUMN["issue_closed"], COLUMN["abandoned"]), COLUMN["issue_closed"]
    cols = [(i, drawn_column(i, moves, window)["st"]) for i in items or []]
    gone = {i.get("n") for i, st in cols if _columns_module.SPEC_LABEL in (i.get("l") or []) and st in ends}
    done = total = 0
    for i, st in cols:
        labels = i.get("l") or []
        if _columns_module.SPEC_LABEL in labels or "loop-bug" in labels or st == COLUMN["abandoned"]: continue
        pa = i.get("pa") if isinstance(i.get("pa"), int) else None
        if pa is not None and (pa not in gone or st == closed_col): continue
        if st != closed_col: total += 1; continue
        try: at = _utc(i["o"]).timestamp() if i.get("o") else None
        except Exception: at = None
        if at is not None and at >= since: done, total = done + 1, total + 1
    return {"closed": done, "total": total}

def spec_progress(items, moves=None, window=None, now=None):
    """The Monitor card's spec tile: closed over total sub-issues, summed over every open spec on the board cache. An open
    spec is a card with the spec label that the board draws in neither Closed nor Abandoned: its column comes from
    `drawn_column`, with the door's overlay, as the board draws it (#829). The count is the one `task_count` reads, so
    no new GitHub read. The percent rounds down, so 100% means every sub-issue is closed; `frac` is the exact fraction
    for the bar width. The page only draws these values (ADR-0137)."""
    if moves is None: moves = read_moves() if overlay_on() else {}
    if window is None: window = pending_window()
    gone = (COLUMN["issue_closed"], COLUMN["abandoned"])
    done = total = 0
    rows = []   # (fraction, number, tip line) per open spec
    for i in items or []:
        if _columns_module.SPEC_LABEL not in (i.get("l") or []): continue
        if drawn_column(i, moves, window)["st"] in gone: continue
        sc = task_count(i)
        if sc:
            c, t = (int(x) for x in sc.split("/"))
            done, total = done + c, total + t
            rows.append((c / t, i.get("n") or 0, f"#{i.get('n')} {i.get('t') or ''} — {sc}"))
        else:
            rows.append((0.0, i.get("n") or 0, f"#{i.get('n')} {i.get('t') or ''} — no sub-issue"))
    rows.sort(key=lambda r: (r[0], r[1]))
    tips = [r[2] for r in rows[:SPEC_TIP_LINES]] + ([f"+{len(rows) - SPEC_TIP_LINES} more"] if len(rows) > SPEC_TIP_LINES else [])
    none = nospec_count(items, moves, window, now)   # the No spec lane's own count: one more group in the total (owner)
    if none["total"]:
        done, total = done + none["closed"], total + none["total"]
        tips.append(f"No spec — {none['closed']}/{none['total']}")
    n = len(rows)
    so = " + ".join(x for x in (f"{n} open" if n else "", "no spec" if none["total"] else "") if x)
    out = {"open": n, "so": so, "done": done, "total": total, "tip": "\n".join(tips), "none": none}
    if not n and not none["total"]: return dict(out, pct=0, frac=0, value="no open spec", state="none", bar=False)
    if not total: return dict(out, pct=0, frac=0, value=f"{n} open · no sub-issue yet", state="info", bar=True)
    pct = done * 100 // total
    return dict(out, pct=pct, frac=done / total, value=f"{done}/{total} · {pct}%", state="ok" if done == total else "info", bar=True)

def subs_gauges(now=None):
    """Each subscription's weekly window for the Monitor: its used share and when it resets, or why there is no reading.
    The weekly window is the one the reset matters for; a provider that reports only a short window shows that one."""
    now = time.time() if now is None else now
    plans = plans_get(); out = []
    for k in SUBS:
        q = quota_row(k, now)
        row = {"key": k, "label": sub_name(k, plans.get(k) or None, (quota.get(k) or {}).get("plan"))}
        ws = (q or {}).get("windows") or []
        if not ws:
            row.update({"pct": None, "resets": None, "why": (q or {}).get("err") or "no reading"})
        else:
            w = next((x for x in ws if str(x.get("label", "")).lower().startswith("weekly")), max(ws, key=lambda x: x.get("resets") or 0))
            row.update({"pct": w.get("pct"), "resets": w.get("resets"), "window": w.get("label")})
        out.append(row)
    return out

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
            "facts": list(LOAD_STATE.get("facts") or []) + [_hist.budget_fact(_hist.history_path(BOARD, REPO), time.time())],
            "specs": spec_progress(board_cache.get("items") or []),   # the open specs' total, off the board cache: no GitHub read
            "subs": subs_gauges(),   # the Monitor's subscription instruments: each weekly window and its reset, off the quota cache
            "secs": LOAD_SAMPLE_SECS, "asof": LOAD_STATE.get("asof", 0.0)}

def status_payload():
    """What the mobile Monitor tab reads below its card: the facts the hub computes, never a figure the page works out."""
    items = board_cache.get("items") or []
    return {"loop": loop_status(),
            "live": sum(1 for r in run_list() if r.get("live")),
            "spend_today": stats("day")["totals"]["today"],
            "open_bugs": sum(1 for i in items if str(i.get("s")).upper() == "OPEN" and "bug" in (i.get("l") or [])),
            "warnings": [{"text": f["text"], "tip": f.get("tip")} for f in (LOAD_STATE.get("facts") or []) if f.get("bad")],
            "load": load_payload()}

def worst_alert():
    """This project's worst alert as text: the first bad foot fact, in the order the line reads them; "" when none (#876)."""
    return next((f["text"] for f in (LOAD_STATE.get("facts") or []) if f.get("bad")), "")

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
            "lines": [_childcc_line(c) for c in kids] if live else [], "deferred": len(waits), "oldest_wait": oldest,
            "held": _held_now(kids)}

def _held_now(kids):
    """The run the pass holds now: the child at review or wave entry, else the first child it holds. A click on the row opens
    that run (#689); the row itself is never a pane (#598)."""
    now = [c for c in kids if c.get("step") in ("review", "enter")] or kids
    return next((str(c["run"]) for c in now if c.get("run")), None)

def held_runs():
    """The run names the childcc pass holds, in the file's order, each once."""
    d = _json(STATE / "auto" / "childcc-now.json")
    kids = d.get("children") or [] if isinstance(d, dict) else []
    return list(dict.fromkeys(str(c["run"]) for c in kids if isinstance(c, dict) and re.fullmatch(r"run-[\w-]+", str(c.get("run") or ""))))

def held_folder(name):
    """The folder of a run the childcc pass holds, from the runs directories the hub knows (project_dirs), or None. Only a
    held run resolves: the name is never a path the page chose."""
    if name not in held_runs(): return None
    return next((str(d / name) for d in sorted(project_dirs()) if (d / name / "record.jsonl").is_file()), None)

def open_held(name, start=None):
    """The port of a dashboard server for a held run, started when none runs (dashboard_server.start), or None. A held run
    orphaned from active-runs has no server, so the hub starts one when the owner opens it (#689)."""
    return _open_run(name, held_folder(name), start)

def ended_folders():
    """(folder, row) of each run folder whose record ended on the machine's local day, latest end first. A run that ended today
    wrote its record today, so a record last written before midnight is not read."""
    today = dt.date.today(); now = time.time(); midnight = dt.datetime.combine(today, dt.time()).timestamp(); out = []
    for d in sorted(project_dirs()):
        for rd in d.glob("run-*"):
            try:
                if (rd / "record.jsonl").stat().st_mtime < midnight: continue
            except OSError: continue
            r = scan(rd / "record.jsonl")
            if not r or r.get("verdict") is None or not r.get("t1"): continue
            ended = float(r["t1"])
            if dt.date.fromtimestamp(ended) != today: continue
            row, _ = run_row(str(rd), "", [])
            out.append((str(rd), {**row, "live": False, "running": False, "ended_at": ended, "age": max(0, int(now - ended))}))
    return sorted(out, key=lambda x: -x[1]["ended_at"])

def ended_today():
    """The runs that ended on the machine's local day, latest end first, each with its end time."""
    return [row for _, row in ended_folders()]

def open_ended(name, start=None):
    """The port of a dashboard server for a run that ended today, or None. dashboard_server.start reuses a server that runs (ADR-0118)."""
    return _open_run(name, next((folder for folder, row in ended_folders() if row["id"] == name), None), start)

def _open_run(name, folder, start):
    if folder is None: return None
    if start is None: start = _start_dashboard
    try: return int(start(folder))
    except Exception as e:
        log_once(f"hub: no dashboard server for {name}: {e}"); return None

def _start_dashboard(folder):
    """dashboard_server.start(folder) in a child interpreter, from the first root that holds the squad package."""
    roots = [r for r in squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__) if (pathlib.Path(r) / "squad" / "dashboard_server.py").is_file()]
    if not roots: raise RuntimeError("no squad package")
    py = pathlib.Path(roots[0]) / ".venv" / "bin" / "python"
    p = subprocess.run([str(py) if py.exists() else sys.executable, "-c", "import sys; from squad import dashboard_server as d; print(d.start(sys.argv[1]))", folder],
                       cwd=roots[0], capture_output=True, text=True, timeout=30)
    if p.returncode != 0: raise RuntimeError((p.stderr or "").strip().splitlines()[-1:] or "exit " + str(p.returncode))
    return int(p.stdout.split()[-1])

def run_row(d, port, ps_lines):
    """One run folder's row of the list and its load-attribution entry (None while the record is empty). `port` is its
    dashboard server's, or "" when it has none."""
    p = pathlib.Path(d) / "record.jsonl"
    r = scan(p)
    if r is None:
        b = badge_of("starting")
        return {"id": os.path.basename(d), "port": port, "state": "starting", "badge": b["text"], "tip": b["title"], "label": "run " + os.path.basename(d)[-6:], "issues": [], "age": 0, "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": 0, "spend": 0, "dur": 0, "kind": "code", "live": True, "running": True, "agents": 0}, None
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
    return {"id": os.path.basename(d), "port": port, "state": st, "badge": b["text"], "tip": b["title"], "label": r.get("label"), "issues": r["issues"], "issue_src": r.get("issue_src"), "issue_why": r.get("issue_why"), "age": int(age), "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": rank, "spend": r["spend"], "dur": round((t1 - (r["t0"] or t1)) / 60, 1), "kind": r.get("kind", "code"), "fix": r.get("fix"), "stack": r.get("stack"), "bug": r.get("bug"), "merge": r.get("merge"), "titles": {i: TITLE.get(int(i), "") for i in r["issues"]}, "live": not gone, "running": r["verdict"] is None and not gone, "agents": 0 if gone else r.get("agents") or 0, "share": r.get("share")}, {"id": os.path.basename(d), "dir": d, "wts": r.get("wts") or []}   # the row, and what the load attribution needs and nothing else

def runs():
    _titles()
    out = []; load_rows = []
    ps_lines = sh("/bin/ps", "-axwwo", "pid=,command=").splitlines()
    own = own_roots()
    for l in ps_lines:
        m = re.match(r"\s*(\d+)\s+\S+\s+-m\s+squad\.dashboard_server\s+(\S+)", l)   # any interpreter: macOS runs the server as `.../MacOS/Python` (#1751)
        if not m: continue
        pid, d = m.group(1), m.group(2)
        port = re.search(r"TCP 127\.0\.0\.1:(\d+)|n127\.0\.0\.1:(\d+)", sh(LSOF, "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN", "-Fn"))
        if not port: continue
        if not pathlib.Path(d).exists(): continue
        if pathlib.Path(d).parent not in own: continue   # another profile's dashboard: the ps scan sees the whole machine
        if re.search(r"/pytest-\d+/|/pytest-of-", d): continue   # dashboards started by agent-squad's own test suite are fixtures, not runs   # a dashboard server outliving its run folder (worktree removed) must not break the list
        row, load = run_row(d, port.group(1) or port.group(2), ps_lines)
        out.append(row)
        if load: load_rows.append(load)
    listed = {r["id"] for r in out}
    for name in held_runs():   # a run the childcc pass holds, orphaned from active-runs, has no dashboard server: it is listed all the same (#689)
        folder = None if name in listed else held_folder(name)
        if not folder or not (pathlib.Path(folder) / "record.jsonl").is_file(): continue
        row, load = run_row(folder, "", ps_lines)
        out.append({**row, "url": "/held/" + name, "held": True})
        if load: load_rows.append(load)
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
        out.append({"id": f.stem, "port": "", "url": "/test/" + f.stem, "state": st, "badge": b["text"], "tip": tip, "label": "test #" + str(d.get("issue")), "issues": [str(d.get("issue"))], "age": int(age), "project": "test", "rank": run_rank(st, live=not lost), "spend": 0, "dur": round((t1 - d.get("t0", t1)) / 60, 1), "kind": "test", "titles": {str(d.get("issue")): TITLE.get(int(d.get("issue") or 0), "")}, "live": not lost and st in LIVE_STATES, "running": not lost and st in LIVE_STATES, "agents": 0})
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

def canon_model(raw):
    """The canonical id of one model spelling, through the one registry (ADR-0147): `claude:claude-opus-5-5` and
    `claude-opus-5-5[1m]` name one model. A spelling the registry does not know keeps its raw form."""
    ref = modelreg.normalize(raw or "unknown")
    return ref.id if ref.known else (raw or "unknown")

CACHE_READ_RATIO = modelreg.CACHE_READ_RATIO   # the one shared ratio (#495, #791); a registry cache_read overrides it per model

def _usage_spend(model, fresh, out, cr):
    """USD of one count of tokens: the PRICES table first, else the registry's display_price, whose cache reads take its cache
    rate or else the shared ratio of the input rate (#789, #791, #795). Returns (spend, unpriced); a model neither names counts 0.0.
    `fresh` is the input that missed the cache, `cr` the cache reads."""
    spend = priced({"model": model, "tokens_in": fresh + cr, "cache_read": cr, "tokens_out": out, "spend": None})   # None: no PRICES match
    if spend is not None: return spend, False
    rate = modelreg.display_price(model) or {}
    rin, rout = rate.get("input"), rate.get("output")
    if not (isinstance(rin, (int, float)) and isinstance(rout, (int, float))): return 0.0, True
    rcr = rate.get("cache_read")   # a null is unknown, priced at the shared ratio: only a number is a rate (#795)
    if not isinstance(rcr, (int, float)): rcr = rin * CACHE_READ_RATIO
    return ((fresh * rin) + (cr * rcr) + out * rout) / 1e6, False

def ledger_calls():
    """The review calls of $SG_STATE/token-usage.tsv (review.sh), one dict per line: t0, model (canonical), in (cache reads
    included, as an outcome's tokens_in is), out, cache and spend. Only review.sh writes the ledger, so every line is a
    review call. A call the PRICES table does not match is priced from the registry's display_price, its cache reads at the
    display_price cache_read, else at the shared CACHE_READ_RATIO share of the input price. A missing or unreadable ledger is no calls."""
    out = []
    try: lines = (STATE / "token-usage.tsv").read_text(errors="replace").splitlines()
    except OSError: return out
    for l in lines:
        p = l.split("\t")
        if len(p) < 6: continue
        try:
            t0 = time.mktime(time.strptime(p[0], "%Y-%m-%dT%H:%M:%S"))
            i, o, cr = int(float(p[3])), int(float(p[4])), int(float(p[5]))
        except (ValueError, OverflowError): continue
        m = canon_model(p[2])
        spend, unpriced = _usage_spend(m, i, o, cr)
        if unpriced: log_once(f"hub: no price for {m}, its review calls count 0.0 in the Spend totals")
        # a 7th field marks a call with no usage count (tokens.py): it is counted as uncounted, never as a measure (#789)
        uncounted = len(p) > 6 and bool(p[6].strip())
        out.append({"t0": t0, "model": m, "in": i + cr, "out": o, "cache": cr, "spend": round(spend, 4), "unpriced": unpriced, "uncounted": uncounted})
    return out

def _billed(src, model, spend):
    """A call's spend under the stats' billing filter: both, the subscriptions only, or the API only (as `stats` filters runs)."""
    if src == "both": return spend
    return spend if bool(SUBSCRIPTION.match(model)) == (src == "sub") else 0.0

def stats(period, src="both"):
    fmt = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}[period]
    b = {}; amt = (lambda r: r["spend"]) if src == "both" else (lambda r: r["sub"] if src == "sub" else r["api"])
    allr = [dict(r, spend=amt(r)) for r in all_runs()]
    calls = ledger_calls()   # review calls: they carry spend and tokens, never a run
    for r in allr:
        k = time.strftime(fmt, time.localtime(r["t0"])); x = b.setdefault(k, {"t": k, "spend": 0.0, "runs": 0, "durs": []})
        x["spend"] += r["spend"]; x["runs"] += 1
        if r["done"]: x["durs"].append(r["dur"])
    for c in calls:
        k = time.strftime(fmt, time.localtime(c["t0"])); x = b.setdefault(k, {"t": k, "spend": 0.0, "runs": 0, "durs": []})
        x["spend"] += _billed(src, c["model"], c["spend"])
    ks = sorted(b)[-48:]
    rows = [{"t": k, "spend": round(b[k]["spend"], 2), "runs": b[k]["runs"], "median_dur": round(st.median(b[k]["durs"]), 1) if b[k]["durs"] else None} for k in ks]
    now = time.localtime(); tot = {"today": 0.0, "month": 0.0, "all": 0.0, "unpriced": sum(1 for c in calls if c["unpriced"]),
           "uncounted": sum(1 for c in calls if c["uncounted"])}
    for r in allr:
        lt = time.localtime(r["t0"]); tot["all"] += r["spend"]
        if lt[:2] == now[:2]: tot["month"] += r["spend"]
        if lt[:3] == now[:3]: tot["today"] += r["spend"]
    for c in calls:
        lt = time.localtime(c["t0"]); sp = _billed(src, c["model"], c["spend"]); tot["all"] += sp
        if lt[:2] == now[:2]: tot["month"] += sp
        if lt[:3] == now[:3]: tot["today"] += sp
    mt = {}
    for r in allr:
        in_month = time.localtime(r["t0"])[:2] == now[:2]
        for raw, v in r["mod"].items():   # keyed on the canonical id, as the review calls are, so one model is one slice (#789)
            m = canon_model(raw)
            if src == "sub" and not SUBSCRIPTION.match(m) or src == "api" and SUBSCRIPTION.match(m): continue
            x = mt.setdefault(m, {"model": m, "in": 0, "out": 0, "cache": 0, "spend": 0.0, "month_cost": 0.0, "month_tokens": 0, "calls": 0, "src": raw, "srcs": []})   # the first spelling names the box; every spelling's route is kept (#791)
            if raw not in x["srcs"]: x["srcs"].append(raw)
            for k in ("in", "out", "cache", "calls"): x[k] += v[k]
            x["spend"] += v["spend"]
            if in_month: x["month_cost"] += v["spend"]; x["month_tokens"] += v["in"] + v["out"]   # the pie counts tokens: dollars are a price list, tokens are the work (#1035)
    for c in calls:
        m = c["model"]
        if src == "sub" and not SUBSCRIPTION.match(m) or src == "api" and SUBSCRIPTION.match(m): continue
        x = mt.setdefault(m, {"model": m, "in": 0, "out": 0, "cache": 0, "spend": 0.0, "month_cost": 0.0, "month_tokens": 0, "calls": 0, "src": m, "srcs": []})
        if m not in x["srcs"]: x["srcs"].append(m)
        x["in"] += c["in"]; x["out"] += c["out"]; x["cache"] += c["cache"]; x["calls"] += 1; x["spend"] += c["spend"]
        if time.localtime(c["t0"])[:2] == now[:2]: x["month_cost"] += c["spend"]; x["month_tokens"] += c["in"] + c["out"]
    def _box(x):
        box = model_box(x["src"])
        box["routes"] = sorted({model_box(s)["route"] for s in x["srcs"]} - {None})   # every route the model was called by (#791)
        return box
    models = sorted(({**{k: v for k, v in x.items() if k not in ("src", "srcs")}, "spend": round(x["spend"], 2), "box": _box(x)} for x in mt.values()),
                    key=lambda x: -(x["in"] + x["out"]))
    return {"buckets": rows, "totals": {k: round(v, 2) for k, v in tot.items()}, "models": models}

def adapter_errors():
    """Every `adapter_error` run verdict of the run folders still on disk, counted by (model, backend, cause) (#698).
    The classification is one pure module, `squad/adapter_classes.py`, resolved through the one model resolver (ADR-0147);
    this panel holds no model table and chooses no fix. It only reads: no run's record is opened for writing, and a folder
    that cannot be read is skipped. A missing squad package is reported here and the panel shows nothing, never a guess."""
    out = {"classes": [], "runs": 0, "err": None}
    folders = []
    for d in project_dirs():
        try: folders += [p for p in sorted(pathlib.Path(d).glob("run-*")) if p.is_dir()]
        except OSError: continue
    out["runs"] = len(folders)
    roots = squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        (AC,) = load_squad(roots, ("adapter_classes",))
        out["classes"] = AC.aggregate(folders)
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {' '.join(str(e).split())}"
        log_once(f"hub: adapter errors unavailable, the panel shows nothing: {out['err']} (roots tried: {', '.join(roots)})")
    return out

def delivery():
    """The loop's delivery metrics, read from its own local records (#705): the issue ledgers, the landing ledger,
    the auto/ markers and the run folders. The computation is one pure module, `squad/delivery_metrics.py`; this
    binds it and gathers the run folders, as `adapter_errors` does. It only reads: no record is opened for writing,
    no work is started or retried, and the panel decides nothing. A missing squad package is reported here and the
    panel shows nothing, never a guess."""
    folders = []
    for d in project_dirs():
        try: folders += [p for p in sorted(pathlib.Path(d).glob("run-*")) if p.is_dir()]
        except OSError: continue
    out = {"err": None, "state": str(STATE), "runs": len(folders)}
    roots = squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        (DM,) = load_squad(roots, ("delivery_metrics",))
        out.update(DM.compute(STATE, folders))
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {' '.join(str(e).split())}"
        log_once(f"hub: delivery metrics unavailable, the panel shows nothing: {out['err']} (roots tried: {', '.join(roots)})")
    return out

BOARD = pathlib.Path(os.environ.get("SG_BOARD_CACHE") or HUBDIR / "hub-board.json"); BOARD.parent.mkdir(parents=True, exist_ok=True)
BOARD_READ = 2   # the version of the items read that wrote the cached rows: a cache that names another is read again (#921); version 2 carries each row's database id, the fast read's join key
try: board_cache = json.loads(BOARD.read_text())
except Exception: board_cache = {"t": 0, "items": [], "prs": [], "err": None}
if board_cache.get("rv") != BOARD_READ:
    # A cache written before the rows carried the close reason (#921) answers no `cr`, so a card closed as not planned
    # would draw Closed and count as delivered until the next whole items read. Its rows are dropped, and the pass reads
    # them again; the rows of a read this version wrote stay. A board with no row is safer than one that miscounts.
    board_cache.update(items=[], cases=[], t=0)
    board_cache["rv"] = BOARD_READ
if board_cache.get("repo") not in (None, REPO):
    # The cache file is one per owner and project (address.sh), so two repositories on one project share it, and the
    # file holds the rows of the hub that wrote last (#1015). Another repository's rows are never this board: they are
    # dropped, and this hub's first pass reads its own. A board with no row is safer than another repository's.
    board_cache.update(items=[], cases=[], prs=[], landed=[], errs=[], lc={}, lt=0, t=0, repo=REPO)
# A cache written before the board read more than one repository (#1820) has rows with no repository on them: they are
# the profile repository's, which is the only one that pass read.
for _k in ("prs", "landed"):
    for _x in board_cache.get(_k) or []: _x.setdefault("r", REPO)
board_cache.setdefault("errs", [])
board_cache.setdefault("parts", {})
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
def apply_parent_specs(items, nodes, complete=True, previous=None):
    """Attach the spec each ticket belongs to as `pa` (#829): the parent issue's number, when that parent carries the
    spec label. The relation read carries the parent with the blockers, so the board spends no read per card.
    A parent that is not a spec is no spec, and its children are ordinary issues that move on their own. A card the read
    names keeps exactly what that read says. A card a read that is not complete never reached keeps the `pa` the last
    good pass recorded, and a card with none is marked `pau`: an unread parent is never "no spec parent", so the rule
    fails closed, never open (ADR-0142)."""
    by = {n.get("number"): n for n in nodes or [] if isinstance(n, dict)}
    had = {i.get("n"): i for i in previous or [] if isinstance(i, dict)}
    for i in items:
        node = by.get(i.get("n"))
        if node is None and not complete:
            kept = (had.get(i.get("n")) or {}).get("pa")
            if isinstance(kept, int): i["pa"] = kept
            else: i["pau"] = True   # the parent is unknown, not absent: promotable() refuses it until a read names it
            continue
        i.pop("pau", None)
        parent = (node or {}).get("parent")
        num = parent.get("number") if isinstance(parent, dict) else None
        labels = {x.get("name") for x in ((parent.get("labels") or {}).get("nodes") or []) if isinstance(x, dict)} if isinstance(parent, dict) else set()
        if isinstance(num, int) and _columns_module.SPEC_LABEL in labels: i["pa"] = num
        else: i.pop("pa", None)
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
def board_items_jq(repo):
    """The board read's jq. A project may hold cards of several repositories and an issue number repeats between them, so an
    Issue item counts only when its repository is `repo`; a draft item has no repository. The issue's own close reason
    rides the same read as `cr` (#921): the key is its own, never `r`, which the hub reads as the row's repository. After
    each page's items it emits one {"_page": n} line, n the page's own item count, so the read says whether its last page
    was short (#894)."""
    return ('''length as $n|(.[]|select((.content_type=="Issue" and ((.content.repository_url//"")|endswith("/" + $repo))) or .content_type=="DraftIssue")|{n:.content.number,fd:.content.id,i:.id,t:.content.title,s:.content.state,cr:((.content.state_reason//null)|if type=="string" then ascii_downcase else . end),c:.content.closed_at,st:((.fields[]|select(.name=="Status")|.value.name.raw)//null),k:(((.fields[]?|select(.name=="Kind")|.value.name|if type=="object" then .raw else . end)//"")|ascii_downcase|if .=="" then null else . end),l:[.content.labels[]?.name],si:{total:.content.sub_issues_summary.total,completed:.content.sub_issues_summary.completed},o:.content.created_at}),{_page:$n}''').replace("$repo", json.dumps(repo))

GH_FLOOR = int(os.environ.get("SG_GH_FLOOR") or 300)   # below this many core requests left, a read pass skips its reads (#855)
LABELS_EVERY = 3600   # seconds between two reads of the repository's labels, and of its closed pull requests (#855)
REST_TIMEOUT = 120   # a REST page that stalls is abandoned at this deadline (ADR-0051); the pass keeps the rows it held
_etags = {}   # route -> (the ETag GitHub answered it with, the answer it carried); a 304 keeps that answer
_SKIPPING = set()   # the reads whose current floor episode has already been logged (#855)
def _under_floor(what):
    """True when the core quota is under SG_GH_FLOOR: the pass is skipped, and one line says so per episode (#855)."""
    low = ghthrottle.core_below(GH_FLOOR)
    if low is None:
        _SKIPPING.discard(what)
        return False
    if what not in _SKIPPING:
        _SKIPPING.add(what)
        print(f"hub: skipped {what}: core quota {low} is under SG_GH_FLOOR {GH_FLOOR}; the next pass reads again", file=sys.stderr, flush=True)
    return True
def _rest_page(route):
    """One REST page through the door, conditional: the ETag it was answered with rides as If-None-Match, and a 304 keeps
    the answer that ETag named. A page that does not answer raises, and the caller keeps what it had."""
    held = _etags.get(route)
    seen = {}
    def transport(r):
        res = (ghread.gh_conditional_transport(r, held[0], timeout=REST_TIMEOUT) if held
               else ghread.gh_transport(r, timeout=REST_TIMEOUT))
        m = re.match(r"HTTP/[\d.]+\s+(\d{3})", res.body)
        seen["status"], seen["etag"] = (int(m.group(1)) if m else None), ghread.headers_of(res.body).get("etag", "")
        return res
    out = ghread.read(route, transport)
    if seen["status"] == 304: return held[1]
    if not out.is_answer: raise RuntimeError(out.line())
    answer = out.answer or []
    if seen["etag"]: _etags[route] = (seen["etag"], answer)
    return answer
def _check_deadline(until):
    """Raises when the full read's overall deadline has passed; a None deadline is no bound."""
    if until is not None and time.time() >= until: raise TimeoutError("full read passed its deadline")
def _bounded(fetch, until):
    """A branch-lane fetcher that never starts a repository's read once the full read's deadline has passed."""
    def bounded(*args):
        _check_deadline(until)
        return fetch(*args)
    return bounded
def gh_list(path, query="", until=None):
    """Every page of a REST list, 100 to a page (ADR-0277): a page shorter than 100 is the last one."""
    rows, page = [], 1
    while True:
        _check_deadline(until)
        answer = _rest_page(f"{path}?{query + '&' if query else ''}per_page=100&page={page}")
        rows += answer
        if len(answer) < 100: return rows
        page += 1

# The relations a card's rule reads: its open blockers, and the parent issue with its labels, so the board knows a ticket
# of a spec without a read per card (#829, #1506). Owner, name and page size are filled in by the read.
OPEN_RELATIONS_QUERY = ('query($cursor:String){repository(owner:"%s",name:"%s"){issues(first:%d,states:OPEN,after:$cursor)'
                        '{pageInfo{hasNextPage endCursor}nodes{number blockedBy(first:20){nodes{number state}} '
                        'parent{number labels(first:20){nodes{name}}}}}}}')
RELATIONS_PAGE = 100       # the page size the relations read asks for
RELATIONS_MAX_PAGES = 20   # a read that would need more pages than this is incomplete, never a silent stop
def open_relations(until=None):
    """Every open issue's blockers and parent, page by page through `gh api graphql` (#829). It returns `(nodes, complete)`:
    a read that fails, asks for another page without a cursor, or runs past the page cap is incomplete, so a caller never
    reads the cards it did not reach as issues with no parent."""
    own, name = REPO.split("/", 1)
    nodes, after = [], None
    for _ in range(RELATIONS_MAX_PAGES):
        _check_deadline(until)
        argv = ["gh", "api", "graphql", "-f", "query=" + OPEN_RELATIONS_QUERY % (own, name, RELATIONS_PAGE)]
        if after: argv += ["-f", "cursor=" + after]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout   # a call that fails raises, and the pass keeps the rows it had
        try: page = (((json.loads(out) or {}).get("data") or {}).get("repository") or {}).get("issues") or {}
        except (TypeError, ValueError):
            return nodes, False
        if not isinstance(page, dict): return nodes, False
        nodes += [n for n in page.get("nodes") or [] if isinstance(n, dict)]
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"): return nodes, True
        after = info.get("endCursor")
        if not after: return nodes, False   # a page that asks for another and names no cursor is incomplete
    return nodes, False
def board_pass(fetch_open=None, fetch_landed=None, repos=None):
    """One board re-read: Board 4 statuses (REST) + every GraphQL page of Issue.blockedBy and Issue.parent + the open work branches and the
    landing ledger of every repository the hub is told to read (#1820). The full-read loop runs exactly this.

    The two reads are independent: the issues are the profile repository's board, the branch lane spans every configured
    repository. A read that fails keeps the last good rows it already had and says so (the pass's error, or the
    repository's own error on the lane), so a failed pass never writes a fresh empty board (#1772, #1820)."""
    if _under_floor("board pass"): return board_cache
    items = lc = cases = None
    gaps = []   # why the flow sample is skipped when the reads answered but are not whole (#894); none when they are whole
    read_started = time.time()   # when the list read begins: each row keeps it as its observation instant (#974)
    until = read_started + FULL_DEADLINE
    try:
            out = board_seam().rows(
                board_items_jq(REPO),
                kind=True, timeout=120)
            lines = [json.loads(l) for l in out.splitlines() if l.strip()]
            # The jq answer always ends each page with its count (board_items_jq), even for no card: a read with none answered nothing (#894).
            # A full last page is no sign of a missing page: a project of exactly 100 items answers the same way.
            if not any("_page" in x for x in lines):
                raise ValueError("items read unparseable: no page count came back")
            items = [x for x in lines if "_page" not in x]
            cases = [i["t"] for i in items if i.get("n") is None and i.get("k") == "test case"]   # a test case is a draft item: it has no number, so it is a row and never a card (#306)
            items = [i for i in items if i.get("n") is not None]
            have = {i["n"] for i in items}   # open issues that never reached the board have no state either
            for i in gh_list(f"repos/{REPO}/issues", "state=open", until):
                if i.get("pull_request") is None and i["number"] not in have:
                    s = i.get("sub_issues_summary") or {}
                    items.append({"n": i["number"], "t": i["title"], "s": i["state"], "c": None, "st": None, "l": [x["name"] for x in i["labels"]],
                                  "si": {"total": s.get("total"), "completed": s.get("completed")}, "o": i["created_at"]})
            nodes, complete = open_relations(until)   # every page of open issues; never one REST call per card
            if not complete: gaps.append("relations read incomplete: a page with no cursor, or past the page cap")
            try: apply_open_blockers(items, nodes)
            except Exception: pass
            # The spec a ticket belongs to: a ticket never moves alone (#829). A read that is not complete keeps what the
            # last good pass recorded, and marks the cards it never reached.
            try: apply_parent_specs(items, nodes, complete, board_cache.get("items") or [])
            except Exception: pass
            if time.time() - board_cache.get("lt", 0) >= LABELS_EVERY:
                lc = {x["name"]: x["color"].lower() for x in gh_list(f"repos/{REPO}/labels", until=until)}
                board_cache["lt"] = time.time()
    except Exception as e:
        with _board_lock:
            board_cache["err"], items = str(e)[:80], None   # a failed pass keeps the last good rows on the board (#1810)
            for part in ("status", "structure"): set_part(part, err=board_cache["err"])
    if items: apply_holders(items)
    if items is not None:
        with _board_lock:
            board_cache.update(t=time.time(), repo=REPO, items=merge_items(board_cache.get("items"), items, int(read_started)),
                               cases=cases or [], lc=lc or board_cache.get("lc", {}), err=None)
            for part in ("status", "structure"): set_part(part, err=None)
    rs = REPOS if repos is None else repos
    try:
            prs, landed, errs = prs_pass(_bounded(fetch_open or open_rows_of, until), _bounded(fetch_landed or landed_branches_of, until), rs)
            with _board_lock:
                board_cache.update(prs=prs, landed=landed, errs=errs)
                set_part("prs", err="; ".join(errs) or None)
                # a pass no repository answered is not a refresh: `t` keeps saying when the board last really moved, which
                # is the age the header shows (#1810), instead of claiming a fresh board nothing came back for.
                if len(errs) < len(rs): board_cache["t"] = time.time()
    except Exception as e:
        with _board_lock:
            board_cache["err"] = str(e)[:80]
            set_part("prs", err=board_cache["err"])
    with _board_lock: save_board()
    # Every pass writes its flow read, whole or not (#894): a failed read is a flow_skip that counts the last whole read's cards.
    rows = items if items is not None else (board_cache.get("items") or [])
    if items is None:
        gaps.insert(0, f"items read failed: {board_cache.get('err')}")
    _flow.append(_flow.history_path(BOARD, REPO), rows, incomplete="; ".join(gaps) or None)
    return board_cache
MERGED_INPUTS = ("st", "s", "l", "cr", "su", "iu")   # the column inputs of a row: Status and its instant, item instant, issue state, labels and close reason (#974)
def merge_items(held, fresh, observed):
    """The rows of one read merged into the rows the cache holds (ADR-0149). Every row takes the read's structure; its column
    inputs are written only when the read's observation (`observed`, whole epoch seconds) is newer than the one the cache
    holds for that row. A row carries the newest observation of what it holds, so the overlay compares a move with it."""
    kept = {i.get("n"): i for i in held or []}
    out = []
    for row in fresh:
        old = kept.get(row.get("n"))
        merged = {**row, "ot": observed}
        if old is not None and (old.get("ot") or 0) >= observed:
            for k in MERGED_INPUTS:
                if k in old: merged[k] = old[k]
                else: merged.pop(k, None)
            merged["ot"] = old["ot"]
        out.append(merged)
    return out
def set_part(name, err):
    """Stamp one part of the cache (status, structure, prs) with its read's instant and error: a failed read keeps the
    part's last good timestamp and names its error (#974)."""
    parts = board_cache.setdefault("parts", {})
    prev = parts.get(name) or {}
    parts[name] = {"t": time.time() if err is None else prev.get("t", 0), "err": err}
_WRITE_SEQ = itertools.count(1)   # one temp name per write, never one per writer: a thread id is reused once its thread ends (#512)
def _temp_name(path):
    """The temp name one write owns: the pid, so two processes never meet, and a counter, because a thread id is reused
    once its thread has ended (#512, #743)."""
    return path.with_name(f"{path.name}.{os.getpid()}.{next(_WRITE_SEQ)}.tmp")
def save_board():
    """The cache file writes the rows the hub holds. The temp name is the write's own (#512): hubmux runs one hub per
    profile in one process, so the pid alone is shared."""
    _tmp = _temp_name(BOARD); _tmp.write_text(json.dumps(board_cache)); os.replace(_tmp, BOARD)
FULL_EVERY = 600   # seconds between full board reads; a board-dirty flag reads at once
FAST_EVERY = 15    # seconds between fast reads of the open cards
FULL_DEADLINE = 300   # the full read's overall deadline (ADR-0051): its page loops and branch fetches stop past it
FAST_DEADLINE = 60   # the fast read's overall deadline (ADR-0051)
FAST_POINTS_PER_HOUR = 2400   # five pages of 100 cards at two points each, every 15 s (GitHub's documented cost, not measured live); spent, it stops for the hour
FAST_BUDGET = board_fast.Budget(FAST_POINTS_PER_HOUR)
_board_lock = threading.Lock()   # the fast read and the full read write one cache; the lock covers the write, never a read
def _fast_root():
    """The GraphQL root of the project's owner: `organization` for an organization, else `user`."""
    return "organization" if board_seam().binding.kind == "orgs" else "user"
def _graphql(query, variables, timeout):
    """One fast-read page through the GitHub door: a refusal, a failure or a partial answer raises, so no page moves a card."""
    out = ghread.graphql_read(query, variables, timeout=timeout, pass_name="fast-board")
    if not out.is_answer: raise RuntimeError(out.line())
    if out.partial: raise RuntimeError("partial answer: " + ", ".join(out.partial))
    return out.answer
def fast_board_pass():
    """One fast read: the column inputs of the cards the cache holds, written where the snapshot is newer. A card the last
    whole snapshot held and this one lacks is checked in one batched read (#976). A failed read moves nothing."""
    observed = time.time()
    deadline = observed + FAST_DEADLINE
    try: nodes = board_fast.read_snapshot(_graphql, _fast_root(), OWNER, PROJECT_NO, _columns_module.TERMINAL, FAST_BUDGET, deadline)
    except board_fast.SnapshotError as e:
        with _board_lock:
            set_part("status", err=str(e)[:80])
        return
    rows = board_fast.rows_of(nodes, REPO)
    with _board_lock:
        lost = board_fast.missing(board_cache.get("fast_seen") or {}, rows, board_cache.get("items") or [])
    verdicts = board_fast.check(_graphql, OWNER, PROJECT_NO, lost, FAST_BUDGET, deadline)
    with _board_lock:
        items = board_fast.apply(board_cache.get("items") or [], rows, int(observed))
        board_cache["items"] = board_fast.settle(items, verdicts, int(observed))
        board_cache["fast_seen"] = board_fast.seen_of(rows, verdicts)
        set_part("status", err=None)
        save_board()
def refresh_fast():
    """fast_board_pass() every FAST_EVERY seconds, on its own thread, so a full read never holds it."""
    while True:
        time.sleep(FAST_EVERY)
        try: fast_board_pass()
        except Exception as e:
            with _board_lock: set_part("status", err=str(e)[:80])   # one failed fast pass never ends the loop
def refresh_board():
    """board_pass() every FULL_EVERY seconds, or within 2 s of a board move made through scripts/board.sh (board-dirty flag).
    The items route lags a write by seconds, so a pass a move asked for is read again 16 s later: the card never waits a minute in its old column."""
    quick = False
    while True:
        try: board_pass()
        except Exception as e:
            with _board_lock: board_cache["err"] = str(e)[:80]   # one failed pass never ends the loop (#512): the next one still runs
        for _ in range(8 if quick else FULL_EVERY // 2):
            time.sleep(2)
            try:
                if (STATE / "board-dirty").stat().st_mtime > board_cache.get("t", 0): quick = True; break
            except OSError: pass
        else: quick = False
ORDER = [column["name"] for column in BOARD_COLUMNS]
FLOW_BANDS = [*ORDER, _cfd.FROZEN, _cfd.NO_COLUMN]   # the chart's bands: the columns in their order, then the cards frozen and the cards in no column (#896)
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

def _door_rollup(check):
    """The live rollup of a pull request from the door's check counts: the words `_live_rollup` gives, or None when unread."""
    if not check or check.get("state") is None: return None
    if check.get("failed"): return "fail"
    if check.get("pending"): return "pending"
    return "pass" if check.get("total") else "none"

def open_prs_of(repo):
    """The open pull requests of ONE repository, through the door's open-list read (ADR-0277): one aggregate request. A
    refusal or a failure raises, so the caller keeps that repository's last good rows instead of an empty lane (#851).
    A fact the door's read does not return stays None, so the page draws `?` for it and never a passing word."""
    r = ghread.read(f"repos/{repo}/pulls?state=open&per_page=100")
    if not r.is_answer: raise RuntimeError(r.line())
    return [{"n": p["number"], "t": p.get("title") or "", "d": bool(p.get("draft")), "u": "", "o": "",
             "h": (p.get("head") or {}).get("sha") or "", "base": (p.get("base") or {}).get("ref") or None, "ms": "",
             "l": [l.get("name") for l in p.get("labels") or []], "by": None, "cl": None, "th": None, "ci": None, "checks": [],
             "hc": None, "rollup": _door_rollup(p.get("check_rollup")), "r": repo, "facts": False} for p in r.answer]

def open_rows_of(repo):
    """Every open row of ONE repository: its open work branches and its open pull requests (#851)."""
    return open_branches_of(repo) + open_prs_of(repo)

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
    fetch_open = fetch_open or open_rows_of
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
def card_transitions(repo=None):
    """The column changes board.py proved for one repository, oldest first. The hub only reads this record (#897)."""
    path = STATE / f"board-transitions.{(repo or REPO).replace('/', '_')}.jsonl"
    if not path.exists(): return []
    out = []
    for line in path.read_text().splitlines():
        try: out.append(json.loads(line))
        except ValueError: continue
    return out
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
MOVE_CONTEXTS = ("owner", "loop")   # the dispatch contexts board.py records with a move (`SG_MOVE_BY`)
def has_context(entry):
    """Whether the door recorded a dispatch context for the move: a move with none does not outrank a read that began after it (#861)."""
    return entry.get("by") in MOVE_CONTEXTS
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
def report_expired(n, item, recorded, read, window, context):
    """One line in the loop's log (`$SG_STATE/cycle.log`) for an entry that outlived its window with no list read
    agreeing (#651): the item, the column the door recorded and the column the read holds. A move with a dispatch context
    was drawn pending for the window; one without was drawn at the read's column at once (#861). It is called only for an
    entry the drop really removed, so the report is idempotent without process memory: the retry that succeeds reports
    once, and every later serve finds no entry to report."""
    if context:
        line = (f"{time.strftime('%T')} hub PENDING-EXPIRED #{n} (item {item}): the door recorded {recorded}, "
                f"the board read holds {read}; no read agreed within {window:g} s, so the entry was dropped")
    else:
        line = (f"{time.strftime('%T')} hub PENDING-EXPIRED #{n} (item {item}): the door recorded {recorded} with no dispatch "
                f"context, so the board drew the read's {read} at once; no read agreed within {window:g} s, so the entry was dropped")
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
        _tmp = _temp_name(MOVES)   # the temp name is the write's own, as save_board's is (#512)
        try:
            _tmp.write_text(json.dumps(left, sort_keys=True) + "\n"); os.replace(_tmp, MOVES)
        except OSError:
            global DROP_FAILURES
            DROP_FAILURES += 1   # the entry stays; the failure is counted, and the next serve retries (dropping is bookkeeping, never the served board)
            try: _tmp.unlink()   # a rename removes the temp; a failed write leaves it behind, and it is never the record
            except OSError: pass
            return None
        return set(moves) - set(left)
def _band_extents(bands):
    """Each band over one day, stacked from the last band of FLOW_BANDS at the foot: lower is the cards in the bands
    after it, upper adds its own."""
    extents, run = {}, 0
    for name in reversed(FLOW_BANDS):
        extents[name] = (run, run + bands[name])
        run += bands[name]
    return extents


def _y_ticks(top):
    """The numbered ticks of the y axis: from zero to the first step of 1, 2 or 5 times a power of ten that covers the
    tallest stack in at most four steps."""
    step = next(s for e in range(7) for s in (10 ** e, 2 * 10 ** e, 5 * 10 ** e) if -(-top // s) <= 4)
    return [k * step for k in range(-(-top // step) + 1)]


def _x_ticks(days):
    """Up to five date ticks, evenly spaced over the history's calendar days."""
    return [{"i": i, "day": days[i]} for i in sorted({round(k * (len(days) - 1) / 4) for k in range(5)})] if days else []


CFD_RANGES = [("7", 7), ("30", 30), ("90", 90), ("all", None)]   # the cumulative flow's range views, in days (#901)
LEAD_TIMEFRAMES = [("7", 7), ("14", 14), ("30", 30), ("90", 90), ("180", 180), ("all", None)]   # the control chart presets (#900)


def lead_rows():
    """The closed-card rows the lead-time arithmetic reads, one per board item (#900). Each row names its spec, its Waves
    key `spec/wave` (#980) and its kind: the values the board's bar filters by (#904)."""
    return [{"n": i["n"], "column": _columns_module.view_column(i["st"], str(i["s"]).lower() == "closed", i.get("l"), i.get("cr"))[0],
             "reason": _leadtime.NOT_DELIVERED if _columns_module.not_planned(i.get("cr")) else None,   # the chart's own word (#921)
             "c": i.get("c") if str(i["s"]).lower() == "closed" else None, "o": i.get("o"), "t": i.get("t", ""),
             "spec": _flow.spec_key(i), "wave": f'{_flow.spec_key(i)}/{_flow.wave_key(i.get("l"))}', "kind": _flow.kind_key(i),
             "type": _flow.type_key(i)}
            for i in board_cache["items"]]


def board_filters(query):
    """The bar's Waves, Specs and Kinds values and the chart's Type from a query string, `fw=5/4,none/none&fs=5&fk=bug&ty=bug`
    (#904, #985): (fw, fs, fk, ty). An absent value is an empty list: all. An absent or unknown type is the default, tasks."""
    q = urllib.parse.parse_qs(query or "")
    def values(key): return [v for v in (q.get(key, [""])[0]).split(",") if v]
    ty = q.get("ty", [""])[0]
    return values("fw"), values("fs"), values("fk"), ty if ty in _flow.TYPES else _flow.DEFAULT_TYPE


def _in_bar(row, fw, fs, fk=(), ty=None):
    """A row the bar keeps: its Waves key is among the waves named, its spec among the specs named, its kind among the kinds
    named, and its type is the chart's Type (#985). Empty lists keep all; no type keeps all."""
    return ((not fw or row["wave"] in fw) and (not fs or row["spec"] in fs) and (not fk or row["kind"] in fk)
            and (ty is None or row["type"] == ty))


def lead_zoom(query):
    """The control chart over a dragged range (`from` and `to`, ISO datetimes) or a timeframe preset (`tf`), `scale` jira
    or log (#900), measured from the start column `a` to the end column `b` (#905). No `a` is the card's opened instant;
    no `b`, or the closed column, is its closed instant. The bar's values in the query filter its rows (#904)."""
    q = urllib.parse.parse_qs(query)
    a, b = (q.get(k, [""])[0] or None for k in ("a", "b"))
    if b == COLUMN["issue_closed"]: b = None
    if any(c is not None and c not in ORDER for c in (a, b)): return {"error": "bad column: a and b name a board column"}
    log = q.get("scale", ["jira"])[0] == "log"
    fw, fs, fk, ty = board_filters(query)
    rows = _leadtime.span([r for r in lead_rows() if _in_bar(r, fw, fs, fk, ty)], card_transitions(), start=a, end=b)
    measure = f"{a or 'opened'} -> {b or 'closed'}"
    if "from" not in q and "to" not in q and "tf" in q:
        days = dict(LEAD_TIMEFRAMES).get(q["tf"][0], 0)
        if days == 0: return {"error": "bad timeframe"}
        return _leadtime.control_chart(rows, timeframe=days, log=log, now=dt.datetime.now(dt.UTC), measure=measure)
    try:
        start, end = (dt.datetime.fromisoformat(q[k][0]) for k in ("from", "to"))
    except (KeyError, IndexError, ValueError):
        return {"error": "bad range: from and to are ISO datetimes"}
    start, end = (m if m.tzinfo else m.replace(tzinfo=dt.UTC) for m in (start, end))
    if end <= start: return {"error": "bad range: to is not after from"}
    return _leadtime.control_chart(rows, log=log, now=dt.datetime.now(dt.UTC), start=start, end=end, measure=measure)


CHARTS = {}   # each chart route's last answer, with the key of the inputs it read (#971)

def chart(name, key, build):
    hit = CHARTS.get(name)
    if hit and hit[0] == key: return hit[1]
    t = time.time()   # the instant the chart reads its data: a cached answer keeps it, however late it is served
    answer = {"t": t, **build()}
    CHARTS[name] = (key, answer)
    return answer

def history_key():
    path = _flow.history_path(BOARD, REPO)
    try: st = path.stat(); return (str(path), st.st_mtime_ns, st.st_size)
    except OSError: return (str(path), None)

def _cfd_payload(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    """The cumulative flow's numbers, computed here from board_cfd; the page draws them and computes none.
    `fw`, `fs` and `fk` are the bar's Waves, Specs and Kinds values: the chart keeps only the cards the bar keeps (#904)."""
    path = _flow.history_path(BOARD, REPO)
    cfd = _cfd.cfd(_flow.samples(path), ORDER, skips=_flow.skips(path), spec=fs, wave=fw, kind=fk, ty=[ty] if ty else [])
    extents = {p["day"]: _band_extents(p["bands"]) for p in cfd["points"]}
    days = sorted([*extents, *cfd["gaps"]])   # one entry per calendar day: a day with no sample carries no value (#894)
    yticks = _y_ticks(max((e[FLOW_BANDS[0]][1] for e in extents.values()), default=0))
    def values(name, k): return [extents[d][name][k] if d in extents else None for d in days]
    points = {p["day"]: p for p in cfd["points"]}
    legacy = {p["day"] for p in cfd["points"] if p.get("legacy")}   # a day whose sample predates the kind, spec and wave (#904)
    def view(width):
        """The chart over the last `width` calendar days (all of them when None): the same arrays, cut to the window (#901)."""
        window = days if width is None else days[-width:]
        kept = [d for d in window if d in extents]
        top = max((extents[d][FLOW_BANDS[0]][1] for d in kept), default=0)
        ticks = _y_ticks(top)
        def cut(arr): return [arr[days.index(d)] for d in window]
        return {"start": dt.datetime.fromtimestamp(cfd["start"], dt.UTC).date().isoformat() if cfd["start"] is not None else None,
                "days": window, "ymax": ticks[-1], "yticks": ticks, "xticks": _x_ticks(window),
                "legacy": sum(1 for d in window if d in legacy),
                "wip": [points[d]["wip"] if d in points else None for d in window],
                "throughput": [points[d]["throughput"] if d in points else None for d in window],
                "bands": [{"name": n, "lower": cut(values(n, 0)), "upper": cut(values(n, 1)),
                           "count": [extents[d][n][1] - extents[d][n][0] if d in extents else None for d in window]}
                          for n in FLOW_BANDS] if kept else []}
    return {"cfd": {**view(None), "ranges": {key: view(width) for key, width in CFD_RANGES}}}


def _lead_payload(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    """The lead-time numbers over the closed cards of the board read, computed here; the page draws them and computes none.
    The bar's values `fw`, `fs` and `fk` filter the rows (#904)."""
    rows = [r for r in lead_rows() if _in_bar(r, fw, fs, fk, ty)]
    lead = _leadtime.lead_time(rows)
    now = dt.datetime.now(dt.UTC)
    control = {scale: {key: _leadtime.control_chart(rows, timeframe=days, log=scale == "log", now=now)
                       for key, days in LEAD_TIMEFRAMES} for scale in ("jira", "log")}   # the Jira-style control chart (#900)
    return {"lead": {"measure": lead["measure"], "type": ty, "window": lead["window"],
                     "excluded": [{"reason": r, "count": c} for r, c in lead["excluded"].items()],
                     "excluded_total": sum(lead["excluded"].values()), "series": lead["series"], "summary": lead["summary"],
                     "control": control, "columns": ORDER}}


def flow_cfd(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    """The cumulative flow for the bar's values and the Type; the cache key holds them, so a change reads anew (#904, #985)."""
    return chart("cfd", (history_key(), tuple(fw), tuple(fs), tuple(fk), ty), lambda: _cfd_payload(fw, fs, fk, ty))

def flow_lead(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    return chart("lead", (board_cache.get("t"), tuple(fw), tuple(fs), tuple(fk), ty), lambda: _lead_payload(fw, fs, fk, ty))


def drawn_column(i, moves, window):
    """The column the served board draws for one row, the freeze that column carries, and what the serve acts on:
    `read` (no entry names a column this board ships, or the read's column stands), `agree` (the read began after the
    move and carries its column), `expired` (the window passed with no read agreeing) or `pending` (the move is drawn at
    once and wears the mark). The board and the promote route ask this one function, so a card is promotable by the
    column both of them draw, never by two different columns (#829, ADR-0144)."""
    read_at = i.get("ot")   # the instant the read that observed this card began: the only stamp the overlay compares a move against
    closed = str(i["s"]).lower() == "closed"   # the row's own closedness: the freeze below is the card's, not the read's
    st, fz = _columns_module.view_column(i["st"], closed, i.get("l"), i.get("cr"))   # the column the table names, and the freeze over it
    entry = moves.get(str(i["i"])) if i.get("i") else None   # the door keys an entry by the Project item id
    if not (isinstance(entry, dict) and entry.get("column")):
        return {"st": st, "fz": fz, "state": "read", "recorded": None, "entry": None}
    recorded = _columns_module.SHIPPED_NAME.get(entry["column"], entry["column"])   # an old Status name reads as its shipped column
    if recorded not in ORDER:   # no column this board ships: the entry is never drawn, and the read says nothing about it, so the hub leaves it (never writes the record for it)
        return {"st": st, "fz": fz, "state": "read", "recorded": recorded, "entry": entry}
    at = move_time(entry)   # the instant the door proved the move, in whole epoch seconds
    if recorded == st and read_carried(at, read_at):   # the read began after the move and carries its column: the entry's work is done, however old it is
        return {"st": st, "fz": fz, "state": "agree", "recorded": recorded, "entry": entry}
    if at is not None and read_at is not None and read_at - at >= window:
        # The move is past its window at the read's own instant with no agreement: the read's column stands and the entry
        # is dropped. The window is the read's clock, never the wall clock, so a cache that stopped updating never expires
        # a move no read has had a window to agree with (#651).
        return {"st": st, "fz": fz, "state": "expired", "recorded": recorded, "entry": entry}
    if read_carried(at, read_at) and not has_context(entry):
        return {"st": st, "fz": fz, "state": "read", "recorded": recorded, "entry": entry}   # a move with no dispatch context stands aside for a read that began after it
    # Draw the move at once and keep the mark until a read that began after it agrees (#650). A move with a dispatch
    # context keeps the mark through a contradicting read too, for the whole window. A column that matches by coincidence is no proof.
    return {"st": recorded, "fz": _columns_module.view_column(recorded, closed, i.get("l"), i.get("cr"))[1], "state": "pending",
            "recorded": recorded, "entry": entry}


def board(moves=None):
    overlay = overlay_on()   # one setting turns the overlay off: the served board is then the list read alone (#651)
    if moves is None: moves = read_moves() if overlay else {}   # the serve passes the record it already read, so one serve reads it once (#973); the record is read on every serve, so a page reload sees the same overlay (#650)
    window = pending_window()   # an entry older than the window expires even with no read agreeing (#651)
    agreed, expired = set(), {}   # expired: item id -> (card number, the recorded column, the column the read holds, the move's dispatch context)
    by, tally, drawn = {}, {}, []
    ends = {COLUMN["issue_closed"], COLUMN["abandoned"]}   # history by role: a closed status is never a column on any page (#969)
    for i in board_cache["items"]:
        d = drawn_column(i, moves, window)   # the one overlay rule the promote route asks too (#829)
        st, fz = d["st"], d["fz"]
        if d["state"] == "agree": agreed.add(str(i["i"]))
        elif d["state"] == "expired": expired[str(i["i"])] = (i["n"], d["recorded"], st, has_context(d["entry"]))
        row = {"n": i["n"], "t": i["t"], "c": i.get("c"), "l": i.get("l") or []}
        if d["state"] == "pending": row["pd"] = True   # the mark the page draws; the rule lives here, never in the view (ADR-0137)
        if fz: row["fz"] = fz
        if _columns_module.SPEC_LABEL in row["l"]: row["sp"] = True   # a spec is an issue with the spec label: the Specs filter reads this
        if isinstance(i.get("pa"), int): row["pa"] = i["pa"]   # the parent spec the read carried (#829): the swimlane groups by it, with no read of its own (#890)
        # The owner's one move (#560): a card the rule marks promotable carries the arrow that starts it, a spec included:
        # the owner's move starts the spec and its open Backlog tickets follow (ADR-0142 section 6, #838). The rule lives
        # here, not in the page: the card renders the fact its row carries (ADR-0137). `pmt` names the column the move
        # goes to, so the page that drags the card (#829) holds no column list of its own.
        if promotable({**i, "st": st}):   # the column the board draws, which a proved move's overlay may have changed
            row["pm"] = True
            row["pmt"] = PROMOTE_TO
        if i.get("b"): row["b"] = i["b"]
        sc = task_count(i)                       # the card's own count, a value: the page renders it and holds no rule
        if sc: row["sc"] = sc
        ho = holder_text(i)                      # the run that holds the card, read from the seam (#241)
        if ho: row["ho"] = ho
        if (i.get("h") or {}).get("run"): row["hr"] = i["h"]["run"]   # the run id the sheet selects its cards by (#919)
        drawn.append((i["n"], st, row.get("pa"), label_wave(i.get("l") or []), _columns_module.SPEC_LABEL in row["l"]))
        if st in ends: tally[st] = tally.get(st, 0) + 1   # a closed row is never served: its count and the summaries carry it (#972)
        else: by.setdefault(st, []).append(row)
    # The reverse of `blocked by`, one map so the view holds no rule of its own (ADR-0137, #919): a card carries the cards
    # it blocks, and the sheet renders that served fact.
    allrows = [r for rs in by.values() for r in rs]
    blocks = {}
    for r in allrows:
        for b in r.get("b") or []: blocks.setdefault(b, []).append(r["n"])
    for r in allrows:
        if r["n"] in blocks: r["bl"] = sorted(blocks[r["n"]])
    names = ORDER + sorted(n for n in by if n not in ORDER)   # every shipped column shows, even at 0; a user column follows
    def wave(x):   # `wave:NN` label first, then issue number; cards with no wave go last
        w = label_wave(x["l"])
        return (10**9 if w is None else w, x["n"])
    groups = [{"name": n, "end": n in ends, "n": tally.get(n, 0) if n in ends else len(by.get(n, [])), "items": sorted(by.get(n, []), key=wave)} for n in names]
    prs = sorted(board_cache["prs"], key=lambda p: p["u"], reverse=True)
    for x in prs:
        if x.get("br"): x.setdefault("gs", None); x.setdefault("pb", None); x["kind"] = _columns_module.BRANCH_KIND; continue   # a work branch has no review strip and no badge: its chip is its standing against dev
        x["ps"] = pr_state(x); x["gs"] = strip_instant(gate_strip(x, t=board_cache.get("t")), board_cache.get("t")); x["pb"] = pr_badge(x); x["kind"] = _columns_module.PR_KIND   # the chip, the R C T B H strip under it (#1772), the lane's badge (#247)
    # Every card names its repository (#1820): `r` is the full owner/name (what a link and the popup read), `rt` the tag
    # the card wears (MesenAI / agent-squad, or owner/repo when the short name names more than one repository).
    # Every row carries its own kind (#440): a pull request is `pr`, a work branch is `branch`. A work branch is served by
    # no view of the board (owner, 2026-10-16: the Kinds view went): it is read from the landing ledger and the issue card.
    cases = []   # the rows beside the axis, from the draft items the pass already read: the age is the pass's own clock against the pass's end
    for title in board_cache.get("cases") or []:
        m = TEST_CASE_TITLE.match(title)
        if m: cases.append({"issue": int(m[1]), "result": m[2], "commit": m[3], "ended": m[4]})   # an absolute instant: the page counts the age, so time passing never changes the body (#973)
    perr = "; ".join(board_cache.get("errs") or [])   # the repositories this pass could not read, named on the column
    # The lane carries the pull requests only (#440): a work branch has no lane and no other view. Each row names its own
    # kind, so the page reads a pull request by the row, never by the lane's name. An empty lane is still served, `n` 0.
    open_prs = [p for p in prs if p["kind"] == _columns_module.PR_KIND]
    # The open pull requests that close a card, on that card's row (#919): the sheet names the PR and its CI from served
    # facts, the same reverse lookup `blocks` makes, never from a rule in the view (ADR-0137).
    closes = {}
    for x in open_prs:
        for n in x.get("cl") or []:
            closes.setdefault(n, []).append({"n": x["n"], "t": x["t"], "st": (x.get("ps") or [None, None])[1] if isinstance(x.get("ps"), list) else None,
                                             "ci": x.get("rollup"), "r": x.get("r") or REPO})
    for g in groups:
        for r in g["items"]:
            if r["n"] in closes: r["prf"] = sorted(closes[r["n"]], key=lambda p: p["n"])
    # A count of one or more is a true lower bound even when a repository failed; a zero is a claim about every repository,
    # so beside a failed read it is `?`, the word the strip uses for a fact not read, never a count (#851).
    count = "?" if not open_prs and perr else len(open_prs)
    lane = {"name": COLUMN["pr_open"], "tracking": True, "n": count, "err": perr or None,
            "items": [{"n": p["n"], "t": p["t"], "u": p["u"], "ps": p["ps"], "gs": p.get("gs"), "pb": p.get("pb"),
                       "rollup": p.get("rollup"), "ci": p.get("ci"), "o": p.get("o"), "by": p.get("by"), "cl": p.get("cl"),
                       "r": p.get("r") or REPO, "rt": repo_tag(p.get("r") or REPO), "kind": p["kind"]} for p in open_prs]}
    if agreed or expired:
        _served_board["settling"] = True   # a build that settles entries is not kept: the next serve retries a drop that failed (#651, #973)
        dropped = drop_moves(agreed | set(expired))   # agreement settles an entry; the window settles one no read ever agreed with (#651)
        for item, (n, recorded, read, context) in expired.items():
            if dropped and item in dropped: report_expired(n, item, recorded, read, window, context)   # only a drop that really happened is reported, so a retry never repeats a line
    return {"t": board_cache["t"], "err": board_cache["err"], "parts": board_cache.get("parts", {}), "prs": lane, "groups": groups, "cases": cases, "labels": sorted({l for i in board_cache["items"] for l in (i.get("l") or [])}), "lc": board_cache.get("lc", {}), "sum": dict(board_summary(drawn), none=nospec_count(board_cache["items"], moves, window))}   # the No spec lane's bar (owner)

def strip_instant(strip, t):
    """The strip with its time-dependent `age` and `old` replaced by `at`, the poll's own instant: an unchanged board keeps its
    ETag while time passes, and the page counts the age (#973). A card the batched query left without facts carries no
    strip at all, and that absence is kept as it is."""
    if not strip: return strip
    return {**{k: v for k, v in strip.items() if k not in ("age", "old")}, "at": t or 0, "max": STRIP_MAX_AGE}

_served_board = {}
def board_served():
    """The served board body and its ETag, built once per change (#973). The key is the cache, the pending-move record and the
    overlay settings after which the board is a pure function; an error answer is never kept. Returns (body, etag or None)."""
    moves = read_moves() if overlay_on() else {}
    key = hashlib.sha256(json.dumps([board_cache, moves, overlay_on(), pending_window()], sort_keys=True, default=str).encode()).hexdigest()
    if _served_board.get("key") == key: return _served_board["body"], _served_board["etag"]
    try: body = json.dumps(board(moves)).encode()   # every card: the page filters the board by the bar (#899)
    except ValueError as e: return json.dumps({"error": str(e)}).encode(), None
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    if _served_board.pop("settling", False): _served_board.pop("key", None); return body, etag
    _served_board.update(key=key, body=body, etag=etag)
    return body, etag

def label_wave(labels):   # the `wave:NN` label's number, or None
    w = [int(m.group(1)) for l in labels for m in [re.match(r"wave:(\d+)$", l)] if m]
    return min(w) if w else None

def board_summary(drawn):   # drawn: (number, column, parent spec, wave, is a spec) for every item the list read holds
    kids, waves = {}, {}
    ends = {COLUMN["issue_closed"], COLUMN["abandoned"]}
    gone = {n for n, st, _, _, sp in drawn if sp and st in ends}   # a ticket of a closed spec reads as No spec in the lanes (#972)
    for _, st, pa, wv, sp in drawn:
        if sp: continue
        for store, key in ((kids, pa), (waves, (None if pa in gone else pa, wv))):
            c = store.setdefault(key, [0, 0])
            c[1] += 1
            if st == COLUMN["issue_closed"]: c[0] += 1
    specs = [{"n": n, "st": st, "closed": kids.get(n, [0, 0])[0], "total": kids.get(n, [0, 0])[1]} for n, st, _, _, sp in drawn if sp]
    return {"specs": specs, "waves": [{"spec": pa, "wave": wv, "closed": c, "total": t} for (pa, wv), (c, t) in waves.items()]}

PROMOTE_TO = COLUMN["issue_todo"]   # the column the arrow moves a card into: the owner's go signal (ADR-0142)
def promotable(item):
    """The one rule of the owner's move (#560, #829): a card is promotable when it is open, the board holds it in Backlog,
    and it is a spec or an issue that no spec holds. A ticket of a spec moves with its spec, never alone (ADR-0142). The
    board route serves this rule as the row's `pm` and `pmt`, and the promote route asks the same function before any
    write, so a card the board marks not promotable is refused by the route that would move it (ADR-0137, #829).
    `pa` is the parent spec the board read recorded on the row; `pau` marks a card whose parent the read never reached,
    and no read has named it since: an unknown parent is never read as no spec parent, so the rule fails closed."""
    if not isinstance(item, dict): return False
    if str(item.get("s") or "").lower() == "closed": return False
    if _columns_module.view_column(item.get("st"), False, item.get("l"))[0] != COLUMN["unscheduled"]: return False
    if _columns_module.SPEC_LABEL in (item.get("l") or []): return True
    if item.get("pa"): return False
    return not item.get("pau")
def promote_write(issues, add=False):
    """The one writer call of the owner's move: `<owner> <project> Todo <n>...`, the shape reconcile.py writes, so the
    board seam (`board.sh`) is the only reach of the carrier. `issues` is one number or a list: a spec and its tickets
    move in one call (#838). `add` asks the same door for `--add` first, for a card the board holds no item of: the row
    came from the open-issue read (#815). The call carries the state directory this hub resolved: hubctl starts the hub
    with no SG_STATE, and board.py reads the key to find its caches (#1750)."""
    nums = issues if isinstance(issues, list) else [issues]
    env = {**os.environ, "SG_STATE": str(STATE), "SG_MOVE_BY": "owner"}   # the owner's arrow names its dispatch context (#861)
    argv = ["bash", str(pathlib.Path(__file__).resolve().parent / "board.sh")]
    if add: argv.append("--add")
    # board.py settles each card it moves (SG_BOARD_SETTLE_SECONDS per card), so the budget is the base plus one settle for
    # every card after the first (#838). A timeout may leave some of the cards moved; promote() says so.
    timeout = PROMOTE_TIMEOUT_BASE + _settle_seconds() * max(0, len(nums) - 1)
    return subprocess.run(argv + [OWNER, str(PROJECT_NO), PROMOTE_TO, *map(str, nums)], capture_output=True, text=True, timeout=timeout, env=env)
PROMOTE_TIMEOUT_BASE = 120.0   # seconds the writer gets for one card before any settle budget (#838)
def _settle_seconds():
    """SG_BOARD_SETTLE_SECONDS, the settle budget board.py gives each card (default 60), read as the writer reads it."""
    try: return max(0.0, float(os.environ.get("SG_BOARD_SETTLE_SECONDS", "60")))
    except ValueError: return 60.0
SUB_ISSUES_PAGE = 100          # the page size the sub-issue read asks for
SUB_ISSUES_MAX_PAGES = 20      # a spec with more sub-issues than this is refused, never half-moved (#838)
# The labels the janitor's rule holds a child out of the column flow for (janitor.py, #589): an owner decision, or a closure.
# A spec's move never puts such a ticket in Todo, where a dispatcher would take it as work (ADR-0142 sections 2 and 6).
TICKET_HOLD_LABELS = ("needs-owner", "needs-ratification", "wontfix")
def _repo_of(url, fallback):
    """The lower-case owner/name a REST `repository_url` names (`https://api.github.com/repos/<owner>/<name>`), or the
    fallback when the answer names none."""
    tail = str(url or "").rsplit("/repos/", 1)
    return (tail[1].strip("/") if len(tail) == 2 else fallback).lower()
def sub_issues(issue, repo):
    """Every sub-issue of one issue, page by page through ghread (#838): a page that comes back full asks for the next
    one. A refusal or a failure on any page answers its reason and no list, so a half-read spec never moves."""
    subs = []
    for page in range(1, SUB_ISSUES_MAX_PAGES + 1):
        r = ghread.read(f"repos/{repo}/issues/{issue}/sub_issues?per_page={SUB_ISSUES_PAGE}&page={page}")
        if not r.is_answer: return None, r.line()[:300]
        if not isinstance(r.answer, list): return None, "sub-issues: the read did not answer a list"
        subs += r.answer
        if len(r.answer) < SUB_ISSUES_PAGE: return subs, None
    return None, f"sub-issues: more than {SUB_ISSUES_MAX_PAGES * SUB_ISSUES_PAGE} answered: nothing moved"
def spec_tickets(issue, item):
    """The tickets a spec moves with (ADR-0142, #838): its open sub-issues that the board cache holds in Backlog, in this
    repository. The sub-issue list is read through ghread, so a refusal or a failure is named and never read as "no ticket":
    it answers `(tickets, None)` or `(None, reason)`. A closed ticket, a ticket in another column, a ticket that is itself a
    spec, and a ticket the owner holds (TICKET_HOLD_LABELS) keep their column (ADR-0143, ADR-0142). A sub-issue of another
    repository is never a card of this board, even when its number is one (write-side: board.sh is bound to SG_REPO)."""
    closed = lambda v: str(v or "").lower() == "closed"
    repo = item.get("r") or REPO
    try: subs, err = sub_issues(issue, repo)
    except Exception as e: return None, f"sub-issues: {str(e)[:200] or 'the read did not answer'}"
    if err: return None, err
    cards = {((i.get("r") or REPO).lower(), i.get("n")): i for i in board_cache.get("items") or []}
    tickets = []
    for sub in subs:
        if not isinstance(sub, dict): continue
        c = cards.get((repo.lower(), sub.get("number")))
        if c is None or _repo_of(sub.get("repository_url"), repo) != repo.lower(): continue
        if closed(sub.get("state")) or closed(c.get("s")): continue
        labels = {*(c.get("l") or []), *(x.get("name") for x in sub.get("labels") or [] if isinstance(x, dict))}
        if _columns_module.SPEC_LABEL in labels or labels & set(TICKET_HOLD_LABELS): continue
        if _columns_module.view_column(c.get("st"), False, c.get("l"))[0] != COLUMN["unscheduled"]: continue
        tickets.append(c)
    return tickets, None
def promote(issue):
    """Move one open card from Backlog to Todo through the board seam (#560). Only a card the hub holds in Backlog now
    is accepted: a closed issue keeps its column (ADR-0143), and a card already in Todo sends no write (ADR-0144). A card
    the board marks not promotable is refused (#829): a ticket of a spec moves with its spec, never alone (ADR-0142). A spec
    moves with its open Backlog tickets in one write (ADR-0142, #838): the owner's move starts the spec and its tickets
    follow. A spec with no ticket moves alone, and a failed ticket read moves nothing. A row the board read never carried
    is an open issue with no Project item, and the seam is asked for `--add` there: a plain move answers it "not on the
    board" and writes nothing (#815). A proven write updates the cache, so the cards move at the next refresh without a
    GitHub read right after the write. The answer names every card the write moved."""
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0: return {"err": "issue: a number"}
    item = next((i for i in board_cache.get("items") or [] if i.get("n") == issue), None)
    if item is None: return {"err": "not on the board"}
    if str(item.get("s") or "").lower() == "closed": return {"err": "the issue is closed: a closed card keeps its column"}
    # The column the board draws, a proved move's overlay included: the same function the board route asks, so the rule
    # reads one column in both routes and a card drawn as moved is never written again (#829).
    st = drawn_column(item, read_moves() if overlay_on() else {}, pending_window())["st"]
    col, _ = _columns_module.view_column(st, False, item.get("l"))
    if col != COLUMN["unscheduled"]: return {"err": f"the card is in {col}, not {COLUMN['unscheduled']}: only a Backlog card moves"}
    if not promotable({**item, "st": st}): return {"err": "the card is a ticket of a spec: it moves with its spec, never alone (ADR-0142)"}
    tickets = []
    if _columns_module.SPEC_LABEL in (item.get("l") or []):
        tickets, err = spec_tickets(issue, item)
        if err: return {"err": err}
    cards = [item, *tickets]
    try: r = promote_write([c["n"] for c in cards], add=any(not c.get("i") for c in cards))   # no item id: the row came from the open-issue read (#815)
    except subprocess.TimeoutExpired as e:
        # board.py may have moved some of the cards before the writer ran out of time. The cache is not written over a move
        # it cannot prove; the board-dirty flag asks the next pass to read the columns as they now stand (#838).
        try: (STATE / "board-dirty").touch()
        except OSError: pass
        return {"err": f"the writer did not answer in {int(e.timeout)} s: the move may be partial, and the board is read again"}
    except Exception as e: return {"err": str(e)[:200] or "the writer did not answer"}
    if r.returncode:
        lines = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
        return {"err": (next((l for l in lines if l.startswith("#")), lines[0] if lines else "") or "the writer refused")[:300]}
    for c in cards: c["st"] = PROMOTE_TO   # the cache follows the write, so the next refresh shows the move without a GitHub read after it
    save_board()
    return {"ok": True, "issue": issue, "to": PROMOTE_TO, "moved": [c["n"] for c in cards]}

_issue_cache = {}
def gh_json(path, timeout=30):
    """One `gh api` read as JSON, or None when it returned nothing (the one seam the issue panel reads through)."""
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=timeout).stdout or "null")

def gh_graphql(query, timeout=30, **vars):
    """One GraphQL read as JSON, or None when it answered nothing (`-F` carries the typed variables, so `number` is an
    Int): the one seam the rendered issue body reads through, and it goes through the door like every other read
    (ADR-0277), so a refusal is never read as an answer."""
    try:
        r = ghread.graphql_read(query, vars, timeout=timeout, pass_name="hub")
        return r.answer if r.is_answer else None
    except (OSError, subprocess.TimeoutExpired, ValueError): return None

# REST carries no rendered body, so the sheet reads GitHub's own HTML through GraphQL (X-Frame-Options keeps the page from
# framing GitHub itself). One read carries the body and the last comment for an issue or a pull request (#919).
ISSUE_HTML_QUERY = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
                    "issueOrPullRequest(number:$number){"
                    "... on Issue{bodyHTML comments(last:1){totalCount nodes{author{login} createdAt bodyHTML url}}}"
                    "... on PullRequest{bodyHTML comments(last:1){totalCount nodes{author{login} createdAt bodyHTML url}}}}}}")
def issue_html(n, repo=None):
    """GitHub's rendered HTML for the issue's body and its last comment (#919): `body`, `total`, and `last`, or `{}` when
    the read failed. A failed rendered read never fails the issue read: the sheet falls back to the plain body."""
    repo = repo if repo in REPOS else REPO
    owner, _, name = repo.partition("/")
    p = gh_graphql(ISSUE_HTML_QUERY, owner=owner, name=name, number=int(n)) or {}
    node = (((p.get("data") or {}).get("repository") or {}).get("issueOrPullRequest")) or {}
    if not node: return {}
    cm = node.get("comments") or {}
    nodes = [x for x in (cm.get("nodes") or []) if isinstance(x, dict)]
    last = nodes[-1] if nodes else None
    d = {"body": render_html(node.get("bodyHTML") or ""), "total": cm.get("totalCount") or 0, "last": None}
    if last:
        d["last"] = {"by": (last.get("author") or {}).get("login") or "", "at": last.get("createdAt") or "",
                     "html": render_html(last.get("bodyHTML") or ""), "url": last.get("url") or ""}
    return d

# The rendered body is GitHub's own HTML, so the hub keeps its structure in its own type and colors and allows only what
# cannot run: one allow-list, on this side of the wire, so neither page holds a sanitizer of its own (ADR-0137, #919).
HTML_TAGS = frozenset("details summary hr br b i strong em code kbd sub sup p div span ul ol li table thead tbody tr th td "
                      "h1 h2 h3 h4 h5 h6 blockquote pre a img del s u input".split())
HTML_ATTR = {"a": ("href",), "img": ("src", "alt", "width", "height"), "td": ("colspan", "rowspan", "align"),
             "th": ("colspan", "rowspan", "align"), "details": ("open",), "input": ("type", "checked", "disabled")}
HTML_VOID = frozenset(("br", "hr", "img", "input"))
HTML_SCAFFOLD = re.compile(r"(?is)<(script|style|svg|iframe|object|embed|video|audio)\b[^>]*>.*?</\1\s*>")
HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
HTML_TAG = re.compile(r"(?s)<(/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s[^<>]*?)?)\s*/?>")
HTML_PAIR = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
HTML_BARE = re.compile(r"(?<![=\w-])(checked|disabled|open)(?=[\s/>]|$)")
def render_html(src):
    """GitHub's rendered body passed through the allow-list: the tags it renders keep their structure, a class or style
    never rides, a relative or `javascript:` link is dropped, and a task-list checkbox keeps its look. Anything else is
    its text alone, and a `<` that opens no whole tag is escaped, so a body never reaches the page with a script, a frame or
    a handler."""
    t = HTML_COMMENT.sub("", HTML_SCAFFOLD.sub("", src or ""))
    def one(m):
        close, name, attrs = bool(m.group(1)), (m.group(2) or "").lower(), m.group(3) or ""
        if name not in HTML_TAGS: return ""
        if close: return "" if name in HTML_VOID else f"</{name}>"
        kept = []
        for am in HTML_PAIR.finditer(attrs):
            k, v = am.group(1).lower(), am.group(2) if am.group(2) is not None else am.group(3)
            if k not in HTML_ATTR.get(name, ()): continue
            if k in ("href", "src") and not re.match(r"(?i)^https?://", v): continue
            if k == "type" and v.lower() != "checkbox": continue
            kept.append(f' {k}="{html.escape(v, quote=True)}"')
        for bare in HTML_BARE.finditer(attrs):
            if bare.group(1) in HTML_ATTR.get(name, ()): kept.append(" " + bare.group(1))
        kept = list(dict.fromkeys(kept))
        if name == "a" and any(k.startswith(" href=") for k in kept): kept += [' target="_blank" rel="noopener"']
        if name == "img": kept += [' referrerpolicy="no-referrer" loading="lazy"']
        return f"<{name}{''.join(kept)}>"
    out, last = [], 0
    for m in HTML_TAG.finditer(t):
        out.append(t[last:m.start()].replace("<", "&lt;"))
        out.append(one(m))
        last = m.end()
    out.append(t[last:].replace("<", "&lt;"))
    return "".join(out)
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
         "total": len(cm), "comments": [{"by": c["user"]["login"], "at": c["created_at"], "body": (c.get("body") or "")[:1200]} for c in cm[-5:]],
         "html": "", "last": None}   # the rendered body and last comment are read below, on open (#919)
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
    try:
        h = issue_html(n, repo)
        d["html"], d["last"] = h.get("body") or "", h.get("last")
    except Exception:
        pass   # a failed rendered read is a plain body in the sheet, never a failed issue read (#919)
    _issue_cache[(repo, n)] = (time.time(), d); return d

PRS = HUBDIR / "hub-prs.json"
try: prs_cache = json.loads(PRS.read_text())     # last good PR data survives a restart
except Exception: prs_cache = {"t": 0, "rows": [], "err": None}
def refresh_closed_prs():
    if time.time() - prs_cache.get("read", 0) < LABELS_EVERY or _under_floor("closed pull request read"): return
    f = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    rows = [{"n": p["number"], "merged": p["merged_at"], "min": round((f(p["merged_at"]) - f(p["created_at"])).total_seconds() / 60, 1)}
            for p in gh_list(f"repos/{REPO}/pulls", "state=closed&sort=updated&direction=desc") if p.get("merged_at") and p["number"] >= 1115]
    prs_cache["read"] = time.time()
    if rows:
        prs_cache.update(t=time.time(), rows=sorted(rows, key=lambda r: r["merged"]), err=None)
        _tmp = _temp_name(PRS); _tmp.write_text(json.dumps(prs_cache)); os.replace(_tmp, PRS)
def refresh_prs():
    while True:
        try: refresh_closed_prs()
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

WEB_TOKENS = (pathlib.Path(__file__).resolve().parent / "web-tokens.css").read_text()
PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Hub</title>
<script>if(matchMedia("(max-width: 600px)").matches&&localStorage.getItem("squad-view")!=="desktop")location.replace(location.pathname.replace(/\/?$/,"/")+"mobile")</script>
<style>
/*WEB_TOKENS*/
:root{--col-w:185px}   /* the docked strip's column geometry: the one place its width is set (#1791); a column holding no cards is not drawn at all, so it reserves none (#1809) */
/* The mock's palette (#889): one dark theme, brighter text, one more card step. The token block above still floors every size and height. */
:root{--card:#1c212b;--fg:#e6e8ee;--mute:#b8c0cf;--acc:#7aa2ff;--ok:#5fd38d;--warn:#f2c14e;--bad:#ff6b7f;--sep:#353d4b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:var(--fs-body)/1.55 var(--font-ui);display:flex;flex-direction:column;height:100vh}
#win{flex:1;min-height:0;display:flex;overflow:hidden}
#side{width:280px;padding:12px;border-right:1px solid #232833;overflow:auto;background:#121519;flex:none}#content{flex:1;min-width:0;overflow:auto}.pane{display:none;padding:12px 16px}.pane.on{display:block}.pane>h2{font-size:var(--fs-min);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:4px 0 8px}
#top{position:sticky;top:0;background:linear-gradient(#162033,#121a28);padding:10px 10px 10px 12px;border-bottom:2px solid var(--acc);border-left:3px solid var(--acc);z-index:2;box-shadow:0 2px 8px #0008}#top>b{font-size:var(--fs-min);letter-spacing:.12em;color:var(--acc)}
#top .row{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:6px}
button{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 12px;cursor:pointer;font:inherit}button:hover{border-color:#3a4252}
button.on{border-color:var(--acc);color:var(--acc)}
.kd{margin-right:5px;font-size:var(--fs-min)}.r{padding:7px 10px;border-top:1px solid var(--line);cursor:pointer;display:flex;gap:6px;justify-content:space-between}.r:hover{background:#1c222b}
.r .st{font-size:var(--fs-min);color:#c9d1d9;opacity:.8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
/* The share a gate counted (#80): how much of the suite passed, so a red gate still shows the work it got to. The full
   share reads as good, a partial one as in progress; a run no gate counted carries no chip at all, never a 0%. */
.r .sh{color:var(--warn);font-variant-numeric:tabular-nums}.r .sh.done{color:var(--ok)}
.r.show{background:#1f2a3a;box-shadow:inset 3px 0 0 var(--acc)}.r.pinned .pn{color:var(--acc)}
.pn{color:var(--mute);flex:none}.gh.op{color:var(--fg)}.gh+.r{border-top:0}
.b{display:inline-block;padding:0 6px;border-radius:9px;font-size:var(--fs-min);border:1px solid}
.b.waiting{color:var(--wait);border-style:dashed}   /* the childcc pass after it ended: no state of RUN_STATES says a pass that waits (#598) */
.landed,.pass,.ok{color:var(--ok)}
/*RUN_STATE_CSS*/
.m{color:var(--mute);font-size:var(--fs-min);margin-top:2px}
#q{width:100%;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:3px 6px;font:inherit;margin-top:6px}
.sp:is(details){border:1px solid #232833;border-radius:8px;margin-bottom:10px;background:#171b22;font-size:var(--fs-min)}.sp>summary{list-style:none;display:flex;align-items:center;gap:6px;min-height:36px;padding:8px 10px;font-size:var(--fs-body);font-weight:600;color:var(--fg);cursor:pointer;user-select:none}.gh{list-style:none;display:flex;align-items:center;gap:6px;height:34px;padding:0 10px;font-size:var(--fs-min);font-weight:600;color:var(--fg);cursor:pointer;user-select:none;background:#10141a;border-top:1px solid var(--line)}
.sp>summary::-webkit-details-marker{display:none}.sp>summary::before,.gh::before{content:"▸";flex:none;width:10px;color:var(--mute);font-weight:400}.sp[open]>summary::before,.gh.op::before{content:"▾"}.sp>summary:hover,.gh:hover{background:#171c24}.sp>summary:focus-visible,.gh:focus-visible{box-shadow:inset 0 0 0 1px var(--acc)}
.sp>summary .sr,.gh .sr{margin-left:auto;font-weight:400;color:var(--mute);font-size:var(--fs-min);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}.sp .sb{padding:4px 10px 10px}
.sp h4{display:flex;align-items:baseline;justify-content:space-between;gap:4px;margin:10px 0 6px;font-size:var(--fs-min);color:var(--fg);font-weight:600;white-space:nowrap}.sp h4 small{font-weight:400;color:var(--mute);font-size:var(--fs-min);white-space:nowrap}
.tot{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.tot div{background:#10141a;border:1px solid var(--line);border-radius:6px;padding:4px 6px;font-size:var(--fs-min);color:var(--mute)}.tot b{display:block;font-size:var(--fs-body);color:var(--fg);font-weight:600}.tot b.m{color:var(--mute);font-size:var(--fs-min);line-height:1.5}
.tot .cash,.cash{grid-column:1/-1;background:none;border:0;padding:0;margin:2px 0 0;font-size:var(--fs-min);color:var(--mute);line-height:1.35}
#src,#per{display:inline-flex;flex-wrap:nowrap;gap:2px;margin:0}#src button{padding:1px 5px;font-size:var(--fs-min)}#per button{padding:1px 3px;font-size:var(--fs-min)}
.kr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-auto-rows:min-content;column-gap:8px;row-gap:2px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line);font-size:var(--fs-min);min-width:0}.kr:last-child{border:0}.kr .l{grid-row:1/3;min-width:0;font-size:var(--fs-min);line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kr .l small{display:inline;color:var(--mute);font-size:var(--fs-min)}.kr b{grid-column:2;justify-self:end;font-size:var(--fs-body);font-weight:600;line-height:1}.kr b small{font-size:var(--fs-min);font-weight:400;color:var(--mute)}.kr b.m{color:var(--mute)}.kr b.w{color:var(--warn)}
.kr .d{grid-column:2;justify-self:end;font-size:var(--fs-min);padding:0;white-space:nowrap;color:var(--mute)}.kr .d .good{color:#5fd38d}.kr .d .bad{color:#ff7b7b}
.mtg{min-height:32px;min-width:32px;padding:2px 10px;border:1px solid var(--line);border-radius:6px;background:#10141a;color:var(--fg);font-size:var(--fs-min);cursor:pointer}.mtg.off{text-decoration:line-through;color:var(--mute)}.mt-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:4px 0}.mt-row .mn{flex:1 1 140px;min-width:0}.mdiff{border-top:1px dashed var(--line);margin-top:8px;padding-top:6px}.mtg:focus-visible{outline:2px solid #8ab4ff;outline-offset:2px}@media (max-width:600px){.mtg{min-height:44px;min-width:44px}}
#mbody{padding:0}.mc{background:#10141a;border:1px solid var(--line);border-radius:8px;padding:10px;margin:6px 0 8px}.mc .mt{font-size:var(--fs-min);font-weight:600;color:var(--fg);margin:0 0 2px}.mc .mx{font-size:var(--fs-min);color:var(--mute);margin:0 0 8px;line-height:1.35}
.mr{display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;gap:5px 8px;padding:7px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);margin-bottom:5px;font-size:var(--fs-min)}.mr.p{border-color:#2f5d45;background:#13211b}.mr .rk{flex:none;width:22px;height:22px;border-radius:11px;background:#222a35;color:var(--mute);font-size:var(--fs-min);font-weight:600;display:flex;align-items:center;justify-content:center}.mr.p .rk{background:var(--ok);color:#0b1a12}
.mr .mn{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3;font-size:var(--fs-min);color:var(--fg)}.mr .ct{grid-column:1/3;display:flex;flex-wrap:wrap;gap:3px;align-items:center;min-width:0}.mr .mi{font-size:var(--fs-min);color:var(--mute);margin-left:4px}.mr .ur{color:var(--warn)}.mr .pv{flex:1 1 auto;min-width:0;font-size:var(--fs-min);color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mr .pv b{color:var(--ok);font-weight:600}.mr .el{flex:none;font-size:var(--fs-min);color:var(--mute);margin-left:2px}.mr .mn small{display:block;font-size:var(--fs-min);color:var(--mute)}.mr .ef{flex:none;width:62px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:var(--fs-min);height:var(--ctl-h);padding:0 2px;cursor:pointer;text-overflow:ellipsis}.mr .ef.set{color:var(--warn);border-color:#6b5223}.mr .hr{flex:none;width:50px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:var(--fs-min);height:var(--ctl-h);padding:0 2px;text-align:center}.mr .hr::placeholder{color:var(--mute)}.mr .hr:focus{outline:var(--focus-w) solid var(--focus);border-color:var(--acc)}.mr .hr.set{color:var(--warn);border-color:#6b5223}
.mr .mb{flex:none;display:flex;gap:2px;margin-left:4px}.mr .mb button{min-width:24px;height:var(--ctl-h);padding:0;font-size:var(--fs-min);line-height:1}.mr .mb button:hover:not(:disabled){background:#2e3846;border-color:#4a5566}.mr .mb button:disabled{opacity:.25;cursor:default}.mr .mb button.x:hover:not(:disabled){color:var(--err);border-color:var(--err)}
/* The switch (#1819): a state, so it reads as a word, not a glyph. Off de-emphasises the row and marks the rank badge
   -- the order itself is untouched, which is the point of the switch. */
.mr .mb button.sw{min-width:30px;font-size:var(--fs-min);letter-spacing:.02em}.mr .mb button.sw.off{color:var(--warn);border-color:#6b5223;background:#241d10}
.mr .bk{grid-column:2;justify-self:start;font-size:var(--fs-min);color:var(--warn);border:1px solid var(--warn);border-radius:4px;padding:0 5px}.mr.off .mn,.mr.off .pv,.mr.off .el{opacity:.55}.mr.off .rk{background:#3a2a2a;color:var(--warn)}.mr.off .ef,.mr.off .hr{opacity:.75}
.madd{display:flex;gap:5px;margin-top:4px}.madd input{flex:1;min-width:0;height:var(--ctl-h);background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:0 8px;font:inherit;font-size:var(--fs-min)}.madd input:focus{outline:var(--focus-w) solid var(--focus);border-color:var(--acc)}.madd button{height:var(--ctl-h);padding:0 12px}
.msave{position:sticky;bottom:0;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 0 0;margin:12px 0 0;background:var(--panel);border-top:1px solid var(--line);font-size:var(--fs-min);z-index:1}.msave.dirty{background:#1f1b12;border-top-color:#6b5223}.msave button{height:var(--ctl-h);padding:0 12px}.msave button#msv:not(:disabled){background:var(--warn);color:#1a1300;border-color:var(--warn);font-weight:600}.msave button:disabled{opacity:.35;cursor:default}
.msave .st{flex:1 1 100%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mute)}.msave .st.err{color:var(--err)}.msave .st .dot{display:inline-block;width:8px;height:8px;border-radius:4px;background:var(--warn);margin-right:5px;vertical-align:middle}.msave .st.okm{color:var(--ok)}.sub{display:flex;flex-direction:column;gap:6px;margin:8px 0;padding:8px 10px;background:#10141a;border:1px solid var(--line);border-radius:8px;font-size:var(--fs-min)}
.sub .hd{display:flex;align-items:center;gap:6px;margin-bottom:2px}.sub .nm{font-weight:600;font-size:var(--fs-min);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sub .sv{font-weight:600;font-size:var(--fs-body)}.sub .sv.good{color:#5fd38d}.sub .sv.bad{color:#ff7b7b}.sub .sv.none{color:var(--mute);font-size:var(--fs-min);font-weight:400}
.sub .qw{display:flex;flex-direction:column;gap:3px}.sub .ql{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:var(--fs-min);color:var(--mute)}.sub .ql b{color:var(--fg);font-weight:600;font-size:var(--fs-min)}.sub .ql .rst{white-space:nowrap;font-size:var(--fs-min)}
.sub .qw.sm .ql{font-size:var(--fs-min)}.sub .qw.sm .ql b{font-size:var(--fs-min);color:#b9c0cc}.sub .qw .tr{position:relative;height:8px;border-radius:4px;background:#1c222b}.sub .qw.sm .tr{height:4px;border-radius:2px}.sub .qw .fl{height:100%;border-radius:inherit;transition:width .3s}
.sub .fl.lv-ok{background:#5a6474}.sub .fl.lv-warn{background:var(--warn)}.sub .fl.lv-err{background:var(--err)}.sub .fl.lv-est{background:#5aa9ff}.sub .qw .tk{position:absolute;top:-3px;bottom:-3px;width:2px;background:#d8dde6;border-radius:1px}
.sub .fr{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:-1px}.sub .fs{color:var(--mute);font-size:var(--fs-min);margin-left:auto}.sub .chip{display:inline-block;padding:0 7px;border-radius:9px;font-size:var(--fs-min);font-weight:600;line-height:16px}.sub .chip.err{background:#3a1a1a;color:#ff7b7b;border:1px solid #ff6b6b55}
.sub .xx{padding:1px 7px;border-radius:9px;background:#14301f;color:#5fd38d;font-size:var(--fs-min);margin-left:5px}.sub .xx.bad{background:#3a1a1a;color:#ff7b7b}
.sub .pr{display:flex;gap:6px;align-items:center;color:var(--mute);font-size:var(--fs-min)}.sub select{flex:1;min-width:0;max-width:100%}.sub input{padding:3px 6px;font-size:var(--fs-min);min-width:0}.sub input[data-f=amount]{flex:1}.sub input[data-f=cur]{width:56px;text-transform:uppercase}
.sub .dt{color:var(--mute);font-size:var(--fs-min);line-height:1.4}.sub .dt b{color:var(--fg);font-weight:600}.sub .warn{color:var(--warn);font-size:var(--fs-min)}
.mb{margin-top:2px}.mb .lab{display:flex;justify-content:space-between;font-size:var(--fs-min);color:var(--mute);margin-bottom:3px}.mb .lab b{color:var(--fg);font-weight:600}.mb .tr{position:relative;height:9px;border-radius:5px;background:#1c222b;overflow:visible}.mb .fl{height:100%;border-radius:5px;background:#e0a23a}.mb .fl.good{background:#3ecf8e}.mb .fl.bad{background:#d9534f}.mb .fl.warnq{background:#5aa9ff}.mb .tk{position:absolute;top:-3px;bottom:-3px;width:2px;background:#d8dde6;border-radius:1px}.mb .sc2{display:flex;justify-content:space-between;font-size:var(--fs-min);color:var(--mute);margin-top:2px}
.subx{color:var(--mute);font-size:var(--fs-min);line-height:1.45;margin:8px 0 4px}
.pie{display:flex;flex-direction:column;gap:6px;align-items:stretch}.pie svg{flex:none;width:112px;height:112px;margin:0 auto}.pie .lg{flex:1;min-width:0;font-size:var(--fs-min)}.pie .lg div{display:flex;align-items:center;gap:6px;padding:2px 0}.pie .lg i{width:9px;height:9px;border-radius:2px;flex:none}.pie .ln{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pie .lg b{font-weight:600}.pie .lg .usd{color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}.pie .lg div:focus-visible{outline:var(--focus-w) solid var(--focus);outline-offset:1px}.pie .lg div.hl{background:#1c222b}.pie .lg .lgx{display:block;padding:0 0 0 15px}.pie .lg summary{display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer;list-style:none}.pie .lg summary::-webkit-details-marker{display:none}.sp #piet{font-size:var(--fs-min);margin:-4px 0 6px}.pie [data-k].hl{stroke:#d8dde6;stroke-width:2}.pie.dim [data-k]:not(.hl){opacity:.35}.warnl{margin-top:4px;font-size:var(--fs-min);color:var(--warn)}
.up{color:var(--err)}.dn{color:var(--ok)}svg{display:block;width:100%}
#main{flex:1;min-width:0;display:grid;gap:4px;padding:4px}
.cell{display:flex;flex-direction:column;min-height:0;min-width:0}.cell small{color:var(--mute);padding:2px 4px;display:flex;justify-content:space-between}.cell .snap{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:12px;overflow:hidden;color:var(--mute);border:1px solid var(--line);background:#0f1115}.cell[tabindex]{cursor:pointer}.cell[tabindex]:hover .snap,.cell[tabindex]:focus-visible .snap{border-color:var(--acc);color:var(--fg,inherit)}
#board{position:relative;width:auto}
#bhead{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}#bhead>label{display:inline-flex;align-items:center;gap:8px}#bhead label.hck{border:1px solid var(--line);border-radius:6px;padding:0 12px 0 10px;min-height:32px;background:var(--panel);cursor:pointer}#bhead label.hck input{margin:0;accent-color:var(--acc)}#bt:empty{display:none}
#flowov{background:var(--panel);border:1px solid var(--line);border-radius:8px;display:flex;flex-direction:column}#flowov[hidden]{display:none}
#flowb{padding:10px;font-size:var(--fs-min)}#flowb svg{width:100%;height:auto;display:block;margin:6px 0;user-select:none;touch-action:pan-y}#flowb .fnote,#flowb .fhead{color:var(--mute);margin:4px 0}#flowb .fhead b{color:var(--fg,inherit)}
#flowb .flg{margin-right:10px;white-space:nowrap}#flowb .flg i{display:inline-block;width:9px;height:9px;margin-right:4px;vertical-align:middle;border-radius:2px}
#bbody .ah{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-weight:600;position:sticky;top:34px;background:#1b212b;z-index:1}   /* the same gap as the docked header: `Todo0` is never a reading, in either rendering (#1809) */
#bbody .ah .n{background:#2a303b;border-radius:9px;padding:0 8px;font-weight:400}
#bbody .ah i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:var(--mute)}
#bbody .it{margin:6px 0;padding:8px 10px 9px;border:1px solid #2f3643;border-top:3px solid var(--lc,var(--mute));border-radius:8px;background:var(--card);line-height:1.4;font-size:var(--fs-min);white-space:normal;overflow-wrap:anywhere;display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;transition:border-color .15s}
#bbody .it .ft{display:flex;justify-content:flex-end;gap:6px;margin-top:6px}#bbody .age,#bbody .tsk{display:inline-block;padding:0 6px;border-radius:9px;font-size:var(--fs-min);font-variant-numeric:tabular-nums;color:var(--mute);border:1px solid var(--line)}#bbody .age.warm{color:#e3b341;border-color:#6b5223}#bbody .age.old{color:#f85149;border-color:#6e2a27}
#bbody .pbg{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;font-size:var(--fs-min);font-weight:700;border:1px solid}#bbody .pbg.conflict,#bbody .pbg.checks-red{color:#f85149;border-color:#6e2a27}#bbody .pbg.green{color:#3fb950;border-color:#2e6b3a}#bbody .pbg.approved{color:#3fb950;border-color:#3fb950;background:#12261a}   /* the lane badge (#247) */
#bbody .fz{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:var(--fs-min);color:var(--warn);border:1px dashed var(--warn)}   /* the freeze marker: the card keeps its column */
#bbody .it .pnd{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:var(--fs-min);font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#0b1a12;background:var(--warn);border:1px solid var(--warn)}   /* the proved move the board read has not carried yet (#650) */
   /* the two modes that leave the specs out of the way, or ask for them: the one that draws every card is the resting colour (#456) */
#bbody .pst{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;font-size:var(--fs-min);font-weight:600;vertical-align:1px;border:1px solid;max-width:100%;overflow-wrap:anywhere}
#bbody .pst.ready{color:#3fb950;border-color:#2e6b3a;background:#12261a}#bbody .pst.running{color:#5aa9ff;border-color:#24496e;background:#0f1d2c}
#bbody .pst.changes{color:#e3b341;border-color:#6b5223;background:#261e0f}#bbody .pst.broke,#bbody .pst.conflict{color:#f85149;border-color:#6e2a27;background:#2a1212}
#bbody .pst.idle,#bbody .pst.draft{color:var(--mute);border-color:var(--line);background:#12161c}
#bbody .gstrip{display:flex;gap:4px;flex-wrap:wrap;margin-top:3px;font-size:var(--fs-min);color:var(--mute)}
#bbody .gstrip.old{opacity:.45;filter:grayscale(1)}
#bbody .gc{border:1px solid var(--line);border-radius:7px;padding:0 5px;white-space:nowrap;background:#12161c}
#bbody .gc b{color:var(--fg);font-weight:600;margin-left:3px}
#bbody .gc.ok b{color:#3fb950}#bbody .gc.warn b{color:#e3b341}#bbody .gc.bad b{color:#f85149}
#bbody .gc.debt b{color:#5aa9ff}#bbody .gc.unk{border-color:#6b5223}#bbody .gc.unk b{color:#e3b341}
#bbody .it:hover{border-color:#4a5262;border-top-color:var(--lc,var(--mute))}
#bbody .it .id{order:1;margin:0 0 0 auto;background:none;border:0;border-radius:0;padding:0;color:var(--mute);font-size:var(--fs-min);font-weight:400;font-variant-numeric:tabular-nums;white-space:nowrap;text-decoration:none}

#bbody .it .lbs{display:block;margin-top:4px}#bbody .it .lbs span{display:inline-block;background:var(--lb,#2a303b);border:1px solid transparent;border-radius:9px;padding:0 6px;margin:0 3px 2px 0;font-size:var(--fs-min);color:#c9d1d9}#bbody .it .lbs span.on{border-color:var(--warn);color:var(--fg)}
#bbody .it .id:hover{color:var(--acc)}#bbody .it .tt{order:2;flex:1 1 100%;font-size:var(--fs-body);color:var(--fg)}#bbody .it>*{order:3}#bbody .it>.id{order:1}#bbody .it>.tt{order:2}#bbody .it>.lchip,#bbody .it>.bugc,#bbody .it>.rpo,#bbody .it>.pbg,#bbody .it>.cir,#bbody .it>.fz,#bbody .it>.pnd,#bbody .it>.pst{order:0;margin:0}#bbody .it>.dt{float:none}#bbody .it>.gstrip,#bbody .it>.blk,#bbody .it>.clos,#bbody .it>.prr,#bbody .it>.perr,#bbody .it>.ft{flex:1 1 100%;margin-top:2px}#bbody .it .hld{flex:1 1 100%;font-size:var(--fs-min);color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#bbody .it .hld::before{content:"\25CF ";color:var(--s5)}#bbody .cell .it .lbs{display:none}
#bbody .lchip{border-radius:10px;padding:0 8px;font-size:var(--fs-min);font-weight:600;border:1px solid #3a4252;line-height:20px;white-space:nowrap;color:var(--mute)}#bbody .lchip[data-k=spec]{color:var(--lc,var(--mute));border-color:var(--lc,#3a4252)}
#bbody .it .pmp{display:inline-block;margin-top:4px;font:inherit;font-size:var(--fs-min);padding:0 7px;border-radius:9px;cursor:pointer;color:var(--acc);background:#12161c;border:1px solid var(--line)}#bbody .it .pmp:hover{border-color:var(--acc)}#bbody .it .pmp:disabled{opacity:.5;cursor:default}   /* the owner's move, on a Backlog card only (#560) */
#bbody .it .prr{display:block;margin-top:2px;font-size:var(--fs-min);color:var(--mute);max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bbody .it .blk{display:block;margin-top:4px;font-size:var(--fs-min);color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#bbody .it .blk a{color:var(--acc);text-decoration:none}
#bbody .prs .it .cir{display:inline-block;margin-left:6px;font-size:var(--fs-min);letter-spacing:.04em;text-transform:uppercase;color:var(--cc);border:1px solid color-mix(in srgb,var(--cc) 45%,transparent);border-radius:999px;padding:0 5px;white-space:nowrap}   /* the pull request's own CI rollup (#440) */
#bbody .prs .it .au{color:var(--mute);font-size:var(--fs-min)}   /* the author the pull request was opened by (#440) */
#bbody .prs .it .clos{display:block;margin-top:2px;font-size:var(--fs-min);color:var(--mute)}#bbody .prs .it .clos a{color:var(--acc);text-decoration:none}   /* the issues the pull request closes (#440) */
#bbody .it.m{border-style:dashed;text-align:center;background:none;box-shadow:none}
#bbody .pad{height:6px}#bbody .sc{max-height:58vh;overflow:auto}#bbody .it .dt{float:right;color:var(--mute);font-size:var(--fs-min)}#bbody .it .hld{color:var(--mute);font-size:var(--fs-min)}
/* The ticket sheet (#919): a side panel on the desktop that pushes the page instead of covering it, so the board keeps
   the card in view. One panel, one read: the card data rides the card, the body and the last comment come from the route. */
#tk{position:fixed;top:0;right:0;bottom:0;width:420px;background:var(--panel);border-left:1px solid var(--line);box-shadow:-12px 0 40px #000a;display:none;flex-direction:column;z-index:50}
#tk.on{display:flex}
body.tkopen #win{margin-right:420px}
#tk>header{display:flex;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--line);flex-wrap:wrap}
#tk>header .sp{flex:1}
#tk>footer{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--line);flex-wrap:wrap}
#tk>footer button,#tk>footer a{min-height:32px}
#tk a.btn{display:inline-flex;align-items:center;padding:3px 12px;border:1px solid var(--line);border-radius:6px;color:var(--fg);text-decoration:none}
#tk .chip{border-radius:9px;padding:0 7px;font-size:var(--fs-min);font-weight:600;border:1px solid var(--line);color:var(--mute)}
#tk .chip.spec{border-color:var(--acc);color:var(--acc)}#tk .chip.wave{color:var(--fg)}
#tkset{display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px 0}#tkset:empty{display:none}#tkset button.on{border-color:var(--acc);color:var(--acc)}
#tknav{display:flex;gap:6px;align-items:center}#tknav button{min-width:36px}
#tk .row{display:flex;gap:8px;font-size:var(--fs-min);color:var(--mute);margin:6px 0}#tk .row b{min-width:84px;font-weight:500}#tk .row span{color:var(--fg)}
#tk .lab{display:inline-block;border:1px solid var(--line);border-radius:9px;padding:0 7px;margin:0 4px 4px 0}
#tk .sec{font-size:var(--fs-min);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:14px 0 4px}
#tk .tkbody{background:#0b0d11;border-radius:6px;padding:10px 12px;margin-top:6px}
#tkb{flex:1;min-height:0;overflow:auto;padding:12px 16px}
#tkb .tkbody input[type=checkbox]{transform:scale(1.05);transform-origin:left center;vertical-align:-2px;margin:0 6px 0 0;accent-color:var(--ok);pointer-events:none}
#tkb h2{margin:0 0 6px;font-size:var(--fs-body);line-height:1.35}#tkb .meta{color:var(--mute);font-size:var(--fs-min);margin-bottom:8px}
#tkb .lb{display:inline-block;background:#2a303b;border-radius:9px;padding:0 8px;margin:0 4px 4px 0;font-size:var(--fs-min)}
#tkb pre{white-space:pre-wrap;overflow-wrap:anywhere;font:var(--fs-min)/1.45 ui-monospace,Menlo,monospace;background:#12161c;border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0}
#tkb h3{font-size:var(--fs-min);color:var(--mute);margin:14px 0 4px}#tkb .cm{border-left:3px solid var(--acc);padding-left:8px;margin:8px 0}
#tkb .bar button{min-width:28px}#tkb .bar button:disabled{opacity:.35;cursor:default}#tkb .bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}#tkb a.gh{color:var(--acc);font-weight:700}
#bbody .it{cursor:pointer}#bbody .it.hit{box-shadow:0 0 0 2px var(--warn)}#bbody .it.hl{box-shadow:0 0 0 2px var(--acc)}#bbody .it.dim{opacity:.35}#list .r.hlr{outline:2px solid var(--acc);outline-offset:-2px}.hlrel{color:var(--acc);font-size:var(--fs-min);margin-left:6px}.hlrel[hidden]{display:none}.ghl{color:var(--mute);margin-left:6px;display:inline-flex;align-items:center;opacity:.65;text-decoration:none}.ghl:hover,.ghl:focus-visible{opacity:1;color:var(--fg)}   /* the cards the sheet's Highlight related marks (#919) */
#tkb h2{font:600 var(--fs-md)/1.35 -apple-system,system-ui,sans-serif;margin:4px 0 8px}
#tkb .md{font:var(--fs-body)/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:#d8dde6;overflow-wrap:anywhere}
#tkb .md>:first-child{margin-top:0}#tkb .md p{margin:.6em 0}#tkb .md h2,#tkb .md h3,#tkb .md h4,#tkb .md h5{font-weight:600;margin:1.3em 0 .4em;line-height:1.3;color:#eef1f6}
#tkb .md h2{font-size:var(--fs-body);border-bottom:1px solid var(--line);padding-bottom:4px}#tkb .md h3{font-size:var(--fs-body)}#tkb .md h4,#tkb .md h5{font-size:var(--fs-body);color:var(--mute)}
#tkb .md ul,#tkb .md ol{margin:.5em 0;padding-left:1.4em}#tkb .md li{margin:.25em 0}#tkb .md li:has(.ck){list-style:none;margin-left:-1.2em}
#tkb .md code{font:var(--fs-min) ui-monospace,Menlo,monospace;background:#232b37;border-radius:4px;padding:1px 5px}
#tkb .md pre{background:#12161c;border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin:.7em 0;overflow:auto}#tkb .md pre code{background:none;padding:0;white-space:pre}
#tkb .md a{color:var(--acc);text-decoration:none}#tkb .md a:hover{text-decoration:underline}#tkb .md hr{border:0;border-top:1px solid var(--line);margin:1.2em 0}
#tkb .md blockquote{margin:.7em 0;padding:2px 12px;border-left:3px solid var(--line);color:var(--mute)}
#tkb .md table{border-collapse:collapse;margin:.7em 0;font-size:var(--fs-min)}#tkb .md th,#tkb .md td{border:1px solid var(--line);padding:4px 10px;text-align:left}#tkb .md th{background:#1b212b}
#tkb .md details{border:1px solid var(--line);border-radius:6px;margin:.6em 0;padding:2px 12px;background:#161b22}#tkb .md details[open]{padding-bottom:8px}
#tkb .md summary{cursor:pointer;font-weight:600;padding:4px 0;color:#eef1f6}#tkb .md img{max-width:100%;border-radius:4px}#tkb .md kbd{font:var(--fs-min) ui-monospace,monospace;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:#232b37}
#tkb .md blockquote.alert{border-left-width:4px;background:#1b212b;border-radius:0 6px 6px 0;color:var(--fg)}#tkb .md .at{font-size:var(--fs-min);font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-top:4px}
#tkb .md .alert.note{border-color:var(--acc)}#tkb .md .alert.tip{border-color:var(--ok)}#tkb .md .alert.important{border-color:#b48ead}#tkb .md .alert.warning{border-color:var(--warn)}#tkb .md .alert.caution{border-color:var(--err)}
#tkb .md .alert.warning .at{color:var(--warn)}#tkb .md .alert.caution .at{color:var(--err)}#tkb .md .alert.note .at{color:var(--acc)}#tkb .md .alert.tip .at{color:var(--ok)}#tkb .md .alert.important .at{color:#b48ead}
#tkb .md blockquote>:first-child{margin-top:.3em}#tkb .md blockquote>:last-child{margin-bottom:.3em}
#tkb .ck{display:inline-block;width:13px;height:13px;border:1.5px solid var(--mute);border-radius:3px;margin-right:8px;vertical-align:-2px;position:relative}
#tkb .ck.on{background:var(--ok);border-color:var(--ok)}#tkb .ck.on::after{content:"";position:absolute;left:3px;top:0;width:3px;height:7px;border:solid #0f1115;border-width:0 2px 2px 0;transform:rotate(45deg)}
#tkb .cm{border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:8px;background:#1a2029;padding:8px 14px 10px;margin:10px 0}#tkb .cm .meta{margin-bottom:2px}
#tkb .prbar{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px}#tkb .pill{font-size:var(--fs-min);border:1px solid var(--line);background:#1b212b;border-radius:10px;padding:1px 9px}#tkb .pill.ok{border-color:#3ecf8e88;color:var(--ok)}#tkb .pill.bad{border-color:#ff6b6b88;color:var(--err)}
#tkb h3.sec{font-size:var(--fs-min);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:20px 0 4px}
body.dockb #bbody{display:flex;overflow-x:auto;min-height:320px}


body.dockb #bbody>.col{flex:1 1 0;min-width:var(--col-w);display:flex;flex-direction:column;height:100%;border-right:1px solid var(--line);min-height:0}
body.dockb .ch{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);font-weight:600;background:#1b212b;flex:none}   /* the gap is stated: without it a header that is asked to pack its spans reads `Todo0` (#1809) */
body.dockb .col>.sc{flex:1 1 0;min-height:0;max-height:none;overflow-y:auto;overscroll-behavior:contain}
.prs{background:linear-gradient(color-mix(in srgb,var(--cc) 8%,transparent),color-mix(in srgb,var(--cc) 3%,transparent))}   /* the open-PR lane wears the colour colOf gave it, faint, so a tracking lane never looks like a column of work */
#bbody .acc.prs{border-bottom:2px solid var(--cc);margin-bottom:4px}
#bbody .prs>.ah,#bbody .prs>.ch{background:color-mix(in srgb,var(--cc) 12%,#0f1115);color:var(--cc);border-bottom:1px solid color-mix(in srgb,var(--cc) 40%,transparent);letter-spacing:.04em;text-transform:uppercase}
#bbody .prs .it{background:color-mix(in srgb,var(--cc) 8%,#10141a);border-radius:10px}
#bbody .prs .it .id{background:color-mix(in srgb,var(--cc) 13%,transparent);border-color:var(--cc);color:var(--cc)}
#bbody .prs .it .rpo{font-size:var(--fs-min);letter-spacing:.04em;text-transform:uppercase;color:var(--cc);border:1px solid color-mix(in srgb,var(--cc) 45%,transparent);border-radius:999px;padding:0 5px;white-space:nowrap}   /* the tag naming the card's repository (#1820) */
#bbody .perr{margin:0 0 6px;padding:4px 6px;font-size:var(--fs-min);line-height:1.35;border-radius:8px;overflow-wrap:anywhere;color:#ffb4b4;background:color-mix(in srgb,#ff6b6b 12%,#10141a);border:1px solid color-mix(in srgb,#ff6b6b 40%,transparent)}   /* the unreadable repository, on the column (#1820) */
body.dockb #bbody>.col.prs{box-shadow:inset 0 2px 0 var(--cc);flex:1.15 1 0}
#bcases:empty{display:none}#bcases{display:flex;flex-wrap:wrap;gap:4px 10px;padding:3px 8px;font-size:var(--fs-min);border-bottom:1px solid var(--line)}#bcases .tcrow{display:flex;gap:6px;align-items:baseline}#bcases .tcrow.fail .tr{color:#ff6b6b}#bcases .tcrow.pass .tr{color:#a3e635}   /* a test case row, beside the axis (#306) */
body.dockb #bbody>.col.lane+.col:not(.lane){border-left:3px double var(--line)}#bbody .acc.lane+.acc:not(.lane){margin-top:10px}   /* the PR lanes sit ahead of the axis: a rule marks where the columns start (#247) */
body.dockb #bbody>.col.prcol{background:linear-gradient(color-mix(in srgb,var(--cc) 8%,transparent),color-mix(in srgb,var(--cc) 3%,transparent));box-shadow:inset 0 2px 0 var(--cc);flex:1.15 1 0}
body.dockb .sec{display:flex;flex-direction:column;min-height:0;flex:none}body.dockb .sec.open{flex:1 1 0}body.dockb .sec:not(.open)>.sc{display:none}
body.dockb .sec>.ch{cursor:pointer;border-top:1px solid #2dd4bf33}body.dockb .sec:first-child>.ch{border-top:0}
.eye{cursor:pointer;margin-left:8px;opacity:.55;display:inline-flex;vertical-align:middle}.eye:hover{opacity:1}#bbody .acc.hidcol>.ah{opacity:.45}#bbody .acc:not(.open)>.sc,#bbody .acc:not(.open)>.pad{display:none}
.hidbar{flex:none;align-self:stretch;padding:8px 10px;color:var(--mute);font-size:var(--fs-min);display:flex;flex-direction:column;gap:4px;border-right:1px solid var(--line);min-width:120px;max-width:120px;min-height:0;overflow:hidden}   /* the hidden: strip, the docked row's last child: align-self:stretch is what lets it be divided by height. Both ends of the width are pinned and the strip clips, so a long user-created column name cannot widen the column now that the label sits on its own line (#1792) */
.hidbar .chip{flex:1 1 0;min-height:0;overflow:hidden;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:2px 8px;background:#2a303b;border:1px solid #353c49;border-radius:9px;cursor:pointer;color:#939cab}   /* one full-height tile per hidden column: flex:1 1 0 splits the strip's height equally between however many rows there are, so two rows are half each and eight are an eighth (#1792). The chip's own colour is the one #1809's quiet lines are measured against: the name inside the tile wears the caption's --mute (#1793), so the chip keeps the fill and the text colour the two kinds are told apart by */
.hidbar .chip:hover{background:#333a47;border-color:#414957}
.hidbar .chip .lbl{flex:0 1 auto;min-height:0;overflow:hidden;max-width:100%;font-size:var(--fs-min);line-height:16px;color:var(--fg);text-overflow:ellipsis;white-space:nowrap}   /* the name is the tile's caption: it ellipsizes, it never wraps and its font never shrinks. It is flex:0 1 auto and the number is flex:none, so when a row is shorter than its content the name is the one clipped away */
.hidbar .chip b{flex:none;font-size:var(--fs-xl);line-height:20px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--acc)}   /* the number is what the tile is for, and it is never the element that gets cut. --acc is the fallback for a column the table below does not name. These rules are scoped to .hidbar on purpose: a bare .chip would also repaint the sidebar's .sub .chip */
/* The strip's own mapping from column to category, one rule per category: the colour says what the count means and
   never how big it is, because magnitude would light up the two archival piles. The hexes are #1793's, under the
   names the table gives them; the open branch lane's #5aa9ff is --acc, so it is written as the token it is. */
/*HID_CHIP_RULES*/
.hidbar .hidsep{border-top:1px solid var(--line);margin:3px 0 1px}   /* the divider the auto-hidden columns sit under: below everything the user hid (#1809) */
.hidbar .hidline{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:0 2px 0 6px;border-left:2px solid var(--cc,var(--mute));color:var(--mute);font-weight:400}   /* a column hidden because it is empty, not because the user asked: no chip fill and a dimmer text than the chip's #939cab, with a 2px tick in the column's own colour -- the value the header's dot carries (#1793) -- so the two kinds are never confusable at a glance (#1809) */
iframe{flex:1;width:100%;border:1px solid var(--line);background:#0f1115;min-height:0}
.load{display:inline-block;margin-left:4px;padding:0 5px;border-radius:9px;background:#3a2a12;color:var(--warn);font-size:var(--fs-min);border:1px solid #6b5223}
#who{padding:3px 12px;color:var(--mute);border-bottom:1px solid var(--line)}#who:empty{display:none}
/* The project switcher (#876): one link per project, its live count and worst alert as text; one click switches. */
#psw{display:none;position:absolute;top:calc(100% - 4px);left:16px;z-index:55;min-width:340px;flex-direction:column;gap:2px;background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 40px #000c;padding:6px;font-size:var(--fs-body)}#psw.on{display:flex}
#psw a{color:var(--fg);text-decoration:none;white-space:nowrap;display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:8px;min-height:36px}#psw a:hover{background:#1f2430}
#psw a[aria-current=page]{color:var(--fg);font-weight:600}#psw .repo,#psw .psw-m{color:var(--mute)}#psw .psw-all{color:var(--ok)}
/* Settings > Spend & limits (#926): the guard's switch, cap and factor, the burn it measures, and the diff before a write. */
#lbody select,#lbody input{background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:var(--fs-min);height:var(--ctl-h);padding:0 6px;min-width:0;max-width:100%}
#lbody input{width:104px;text-align:right;font-variant-numeric:tabular-nums}
#lbody .lv{grid-column:2;justify-self:end;min-width:0;max-width:100%;text-align:right;overflow-wrap:anywhere;color:var(--mute);font-size:var(--fs-min)}
#lbody .ldiff{margin:8px 0 0;padding:8px 10px;background:#1f1b12;border:1px solid #6b5223;border-radius:6px;font-size:var(--fs-min)}
#lbody .ldiff b{display:block;margin-bottom:4px;color:var(--warn)}#lbody .ldiff .ln{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
#lbody .lact{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}#lbody .lact button{height:var(--ctl-h);padding:0 12px}
/* Settings > Communication (#924): a channel or a person is one row. A row is a grid on a desktop and stacks on a phone, and
   each switch carries its own text label. */
#sbody select,#sbody input{background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:var(--fs-min);height:var(--ctl-h);padding:0 6px;min-width:0;max-width:100%}
#sbody input[type=number]{width:72px}
#sbody .slt{display:grid;gap:4px;margin:4px 0 8px}
.slc{display:grid;grid-template-columns:minmax(0,1.6fr) repeat(5,minmax(0,1fr)) auto;gap:6px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:var(--fs-min)}
.slc.slh{font-weight:600;color:var(--mute);border-bottom:0}
.slc.off b{text-decoration:line-through;color:var(--mute)}
.slc .c1{min-width:0;overflow-wrap:anywhere}
.slc small{display:block;color:var(--warn)}
.slc .swt{display:inline-flex;gap:4px;align-items:center;min-height:32px}
.slc .sev{display:inline-flex;gap:2px;align-items:center;margin-right:6px}
#sbody .slad{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
#sbody .sdiff{margin:8px 0 0;padding:8px 10px;background:#1f1b12;border:1px solid #6b5223;border-radius:6px;font-size:var(--fs-min)}
#sbody .sdiff b{display:block;margin-bottom:4px;color:var(--warn)}#sbody .sdiff .ln{overflow-wrap:anywhere}
#sbody .sact{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}#sbody .sact button{height:var(--ctl-h);padding:0 12px}
@media (max-width:600px){.slc{grid-template-columns:1fr}.slc.slh{display:none}.slc [data-l]:not(.swc)::before{content:attr(data-l) ": ";color:var(--mute)}.slc .swt{min-height:44px}}
/* A phone forced into the desktop view (#926): one column, a full-width sidebar, and nothing wider than 390 px. The docked
   board lays the window out as a two-column grid of its own (`body.dockb`), so the phone states both layouts: on a 390 px
   screen the 300 px sidebar and the board column are what a reader has to scroll sideways to reach. */
@media (max-width:600px){body{height:auto}body.tkopen #win{margin-right:0}body.tkopen #board{width:auto}#win{display:block;overflow:visible}body.dockb #win{display:block;grid-template-columns:none;grid-template-rows:none}#side{width:auto;border-right:0;border-bottom:1px solid var(--line)}body.dockb #side{grid-row:auto;grid-column:auto}#main{min-height:50vh}body.dockb #main{grid-column:auto;grid-row:auto}#board{width:auto}body.dockb #board{grid-column:auto;grid-row:auto;height:60vh}#lbody input{width:92px}}
/* The page frame of the mock (#889): the header with its tabs, and the sidebar cards. */
/* The loop switch (#923): the strip's draining and starting states, Settings > Loop and Access, and the stop confirm. */
#lopb .ls.draining,#lopb .ls.starting{color:var(--warn)}#iloop .ls,#lopb .ls{font-weight:600}#iloop .ls.on{color:var(--ok)}#iloop .ls.off{color:var(--warn)}#iloop .ls.draining,#iloop .ls.starting{color:var(--warn)}#iloop small{color:var(--mute)}#loopbtn{padding:2px 10px;font-size:var(--fs-min)}#loopbtn:disabled{opacity:.6;cursor:default}#lopb .ls.on{color:var(--ok)}#lopb .ls.off{color:var(--warn)}
#lopb .lnow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#10141a;margin:6px 0}#lopb .lnow small{display:block;color:var(--mute)}#lopb .lnow button{margin-left:auto}
#lopb .lwhy{color:var(--warn);margin:4px 0}#lopb .lerr{color:var(--err);margin:4px 0}#lopb ul.laud{list-style:none;margin:4px 0 0;padding:0}#lopb ul.laud li{padding:4px 0;border-bottom:1px solid var(--line)}#lopb ul.laud li:last-child{border:0}
#accb .kr b{font-weight:500;font-size:var(--fs-min);line-height:1.3;text-align:right}#accb .ok{color:var(--ok)}#accb .no{color:var(--warn)}#accb p{color:var(--mute);margin:8px 0 0}
#ldg{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:16px;width:min(520px,92vw)}#ldg::backdrop{background:#000a}#ldg .lrad{display:flex;gap:8px;align-items:flex-start;padding:8px 0;cursor:pointer}#ldg .lrad small{display:block;color:var(--mute)}#ldg ul{margin:4px 0 0;padding-left:18px}#ldg .lact{display:flex;gap:6px;margin-top:10px}
header.top{position:relative;display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);flex:none}
header.top h1{font-size:var(--fs-xl);margin:0}header.top .spx{flex:1}
header.top .pill{font-size:var(--fs-min);color:var(--mute)}
#pj{font:inherit;font-weight:700;background:none;border:1px solid transparent;padding:2px 8px;min-height:36px}#pj:hover{border-color:var(--line)}
.tabs{display:flex;gap:4px}.tabs button{white-space:nowrap}.tabs button.on{border-color:var(--acc);color:var(--acc)}
.rkt{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:0 0 12px}.rkt button{background:none;border:0;border-bottom:2px solid transparent;border-radius:0;padding:6px 10px;min-height:36px;color:var(--mute);font-weight:600}.rkt button.on{color:var(--fg);border-bottom-color:var(--acc)}.rkt button:hover{color:var(--fg)}
#tab-flow:has(#flowov:not([hidden])) #main{display:none}#tab-flow #main{min-height:60vh}
#side .sp .sb{padding:0 10px 10px}#side .sp>summary::before{content:"\25B8";color:var(--mute);width:10px}#side .sp[open]>summary::before{content:"\25BE"}
#side .sp>summary .sr{font-size:var(--fs-min)}
#lrun .r{border:1px solid var(--line);border-radius:8px;margin-bottom:8px;background:var(--card);min-width:0;overflow:hidden}#lrun .r>div{min-width:0;flex:1}#lrun .r .st,#lrun .r .m{white-space:normal;overflow-wrap:anywhere;max-width:none}#lrun .r:last-child
#flowb svg{max-width:none}#tab-set details.sp{max-width:1120px}#list{max-width:900px}
/*BOARD_LANES_CSS*/
/*RUN_SHEET_CSS*/
#main .r{display:flex;gap:8px;align-items:baseline;padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px;background:var(--card);cursor:pointer}#main .r:hover,#main .r:focus-visible{border-color:var(--acc)}#main h3{font-size:var(--fs-min);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:12px 0 6px}
#bhead select{min-height:32px;padding:4px 12px;font-size:var(--fs-body);border-radius:6px}#bhead label.hck input{width:18px;aspect-ratio:1;padding:0}
#bbody .it>.hld{order:1;flex:0 0 auto;margin:0;font-size:var(--fs-min);font-weight:600;color:var(--s5);white-space:nowrap}#bbody .it>.hld::before{content:"\25CF";margin-right:4px;color:var(--s5)}
#isub{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 10px;margin:0 0 8px}#isub .ig{display:flex;flex-direction:column;align-items:center;flex:1 0 44%;min-width:0}#isub .gv{font-size:var(--fs-min);color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}#isub .grst{display:block;color:var(--mute);font-size:var(--fs-min)}
#tab-set .sp .sb{padding:0 12px 12px}
/* Settings (#913): a left theme list, one theme at a time, one Save bar; on the phone a select replaces the list and rows stack. */
#tab-set .swrap{display:grid;grid-template-columns:200px minmax(0,1fr);gap:16px;max-width:1120px}#tab-set .spanels{min-width:0}
#tab-set .snav{display:flex;flex-direction:column;gap:4px}#tab-set .snav button{text-align:left;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--fg);min-height:44px}
#tab-set .snav button small{display:block;color:var(--mute);font-size:var(--fs-min)}#tab-set .snav button.on{border-color:var(--acc);background:#16202c}
#tab-set .ssel{display:none}#tab-set details.sp:not(.on){display:none}
#sbar{position:sticky;bottom:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;margin-top:12px;max-width:1120px;background:var(--panel);border-top:1px solid var(--line);z-index:2}#sbar[hidden]{display:none}
#sbar.dirty{background:#1f1b12;border-top-color:#6b5223}#sbar button{height:var(--ctl-h);padding:0 12px}#sbar.dirty #ssave{background:var(--warn);color:#1a1300}#schg{color:var(--mute);font-size:var(--fs-min)}
#sdlg{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px;max-width:min(720px,94vw)}#sdpre{white-space:pre-wrap;font-size:var(--fs-min);max-height:50vh;overflow:auto}#sdlg .sact{display:flex;gap:8px}
.mtab{width:100%;border-collapse:collapse;font-size:var(--fs-min);margin-bottom:8px}.mtab th{text-align:left;color:var(--mute);font-weight:500;padding:4px 6px;border-bottom:1px solid var(--line)}.mtab td{padding:4px 6px;border-bottom:1px solid var(--line)}.mtab tr.off .mn{text-decoration:line-through;color:var(--mute)}
.stc{display:inline-block;font-size:var(--fs-min);border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--mute)}.stc.off{color:var(--warn);border-color:#6b5223}.stc.lim{color:var(--err);border-color:var(--err)}.mr.off .mn{text-decoration:line-through}.mr.rmk .mn,.mr.rmk .pv{text-decoration:line-through;opacity:.55}
.madd select{flex:1;min-width:0;height:var(--ctl-h);background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:4px;font:inherit;font-size:var(--fs-min)}#mbody .lead{margin:4px 0 8px}#mbody details.hlp{margin-top:10px}#mbody details.hlp summary{cursor:pointer;color:var(--acc)}
@media (max-width:600px){#tab-set .swrap{display:block}#tab-set .snav{display:none}#tab-set .ssel{display:flex;gap:8px;align-items:center;margin-bottom:8px}header.top{flex-wrap:wrap}.tabs{order:3;flex:1 0 100%;overflow-x:auto}#tab-set .ssel select{min-height:44px;flex:1}.mr .ct{flex-direction:column;align-items:flex-start}.mr .mb button,.mtg,.madd select,.mr .ef,.mr .hr,#sbar button{min-height:44px;min-width:44px}.mtab th:nth-child(2),.mtab td:nth-child(2){display:none}}
#mobile-link{color:var(--acc)}
/* INST-CSS{ */
/* Monitor (#983): calm flight instruments in the sidebar. Three gauges, three annunciator tiles, two rows. No motion
   but the needle's own short move, and none at all under reduced motion. */
#inst .igs{display:flex;justify-content:space-between;gap:4px;margin:2px 0 8px}#inst .ig{display:flex;flex-direction:column;align-items:center;flex:1;min-width:0}
#inst .ig svg{display:block}#inst .ig .gt{fill:none;stroke:var(--line);stroke-width:3;stroke-linecap:round}#inst .ig .gf{fill:none;stroke-width:3;stroke-linecap:round}
#inst .ig .gn{stroke:var(--fg);stroke-width:1.5;stroke-linecap:round;transform-origin:30px 32px;transition:transform .4s ease-out}#inst .ig .gh0{fill:var(--fg)}
#inst .ig.ldred .gf,#inst.warn .ig.ldred .gf,#inst .ig.ldred .gn{stroke:var(--err)}
#inst .gv{font-size:var(--fs-min);color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}#inst .gv b{color:var(--fg);font-weight:600}#inst.warn .gv b{color:var(--warn)}#inst .ig.ldred .gv b{color:var(--err)}
#inst .ian{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}#inst .ian .at:first-child{grid-column:1/-1}#inst .atm{display:block;color:var(--mute);font-variant-numeric:tabular-nums}
#inst .at{background:#10141a;border:1px solid var(--line);border-radius:6px;padding:5px 7px;min-width:0;font-size:var(--fs-min)}
#inst .at .ah{display:flex;align-items:center;gap:6px;color:var(--mute);font-weight:600}#inst .at .dot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--mute)}
#inst .at.ok .dot{background:var(--ok)}#inst .at.err .dot{background:var(--err)}#inst .at.info .dot{background:var(--acc)}
#inst .at .av{display:block;color:var(--fg);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}#inst .at.err .av{color:var(--err)}#inst .at a{color:var(--acc);text-decoration:none}#inst .at a:hover{text-decoration:underline}
#inst .at details>summary{cursor:pointer;color:var(--mute)}#inst .at details>ul{margin:4px 0 0;padding:0 0 0 14px;color:var(--mute);overflow-wrap:anywhere}
#ispec{margin-bottom:8px}#inst .at .so{margin-left:auto;font-weight:400;color:var(--mute);font-variant-numeric:tabular-nums}#inst .pb{display:block;height:6px;margin:5px 0 2px;border-radius:3px;background:var(--line);overflow:hidden}#inst .pf{display:block;height:100%;border-radius:3px;background:var(--acc);transition:width .4s ease-out}#inst .at.ok .pf{background:var(--ok)}@media (prefers-reduced-motion: reduce){#inst .pf{transition:none}}
#inst .irw{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:3px 0;border-top:1px solid var(--line);font-size:var(--fs-min);color:var(--mute)}
#inst .irw b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums;text-align:right;min-width:0;overflow-wrap:anywhere}#inst .irw b.bad{color:var(--warn)}
@media (prefers-reduced-motion: reduce){#inst .ig .gn{transition:none}}
/* }INST-CSS */
</style>
<div id=who>__WHO__</div>
<script>// One read per URL at a time (#917): a timer fires on a fixed beat whether or not its last read came back, and a slow route
// (models, stats) under load then filled the browser's six connections, so no read finished and the page drew nothing.
// A read already in flight is shared: the caller gets a clone of the same answer.
(()=>{const f=window.fetch.bind(window),live=new Map();window.fetch=(u,o)=>{if(o&&o.method&&o.method!=="GET"||o&&o.signal)return f(u,o);
 const k=String(u);if(!live.has(k))live.set(k,f(u,o).finally(()=>live.delete(k)));return live.get(k).then(r=>r.clone())}})();</script>
<header class=top><h1><button id=pj aria-haspopup=menu aria-expanded=false><span id=pjn></span> &#9662;</button></h1><div id=psw aria-label="Projects"></div><div class=tabs role=tablist><button data-t=board role=tab>Kanban</button><button data-t=flow role=tab>Throughput</button><button data-t=set role=tab>Settings</button></div><span class=spx></span>
<span class=pill id=bread></span></header>
<div id=win>
<aside id=side>
<details id=stats class=sp><summary>Cost &amp; usage<span class=sr id=stsr></span></summary><div class=sb><div class="igs isub" id=isub title="subscriptions: the used share of the weekly window and the days to its reset"></div><h4>Spend <span class=row id=src title="all = plans + API. plans = Claude, Grok, GPT and Gemini. API = every other model (deepseek…) and calls that name no model. Figures at API list prices (API-equivalent)."></span></h4><div class=tot id=tot></div><div class=m id=totnote></div><div class=cash id=cash></div><h4>Spend over time <span class=row id=per></span></h4><div id=chart></div><div id=kpi></div><div id=open></div><h4>Model tokens · this month</h4><div class=m id=piet>in + out</div><div id=pie></div></div></details>
<details id=inst class=sp open><summary>Monitor<span class=sr id=instsr></span></summary><div class=sb id=ibody><div class=igs id=igs title="machine load: read in the hub process every few seconds; ? means the hub cannot see that source"></div><div class=ian id=ian></div><div id=ispec></div><div id=iloop></div><div class=irw><span>Next cycle</span><b id=icyc aria-live=off>?</b></div><div id=iuse></div></div></details>
<details id=lrd class=sp open><summary>Live runs<span class=sr id=lrs></span></summary><div class=sb id=lrun></div></details>
</aside>
<main id=content>
<section id=tab-board class="pane on" aria-label=Board>
<div id=board><div id=bhead><label>Swimlanes <select id=lmode title="swimlanes: one lane per spec (Spec), per wave (Wave), or no lanes, today's board (None)"><option value=spec>Spec</option><option value=wave>Wave</option><option value=none>None</option></select></label> <details id=fw class=fsel><summary>Waves: all</summary><div class=fpop id=fwl></div></details> <details id=fs class=fsel><summary>Specs: all</summary><div class=fpop id=fsl></div></details> <details id=fk class=fsel><summary>Kinds: all</summary><div class=fpop id=fkl></div></details> <button id=lcall title="collapse every swimlane">Collapse all</button> <button id=lexp title="expand every swimlane">Expand all</button> <label class=hck title="hide the swimlanes whose every ticket is closed"><input type=checkbox id=lhd> Hide done lanes</label> <span id=lhc></span><span id=bt class=m></span> <span id=hlrel class=hlrel hidden></span></div><div id=bcases></div><div id=bbody></div></div>
</section>
<section id=tab-flow class=pane aria-label="Flow charts">
<div class=rkt id=flowtabs><button id=bruns title="the live run dashboards">Runs</button><button id=bflow title="the development flow: the cards in each column over time, from the flow history (SPEC 111)">Cumulative Flow Diagram</button><button id=blead title="the lead time of the closed cards, opened to closed (SPEC 111)">Lead Time</button></div>
<div id=main></div>
<div id=flowov hidden><div id=flowb></div></div>
</section>
<dialog id=ldg aria-label="Stop the loop"><form method=dialog id=ldgb></form></dialog>
<section id=tab-set class=pane aria-label=Settings><h2>Settings</h2>
<div class=swrap><nav class=snav aria-label="Settings themes"><button data-sp=lim>Spend &amp; limits<small>token guard and cap</small></button><button data-sp=sub>Subscriptions<small>plan prices and quota</small></button><button data-sp=mdl>Models &amp; chains<small>who does the work, when</small></button><button data-sp=slk>Communication<small>Slack, for this profile</small></button><button data-sp=lop>Loop<small>start, stop, history</small></button><button data-sp=acc>Access<small>tailnet and phone</small></button></nav>
<div class=spanels><label class=ssel>Theme <select id=ssel aria-label="Settings theme"><option value=lim>Spend &amp; limits</option><option value=sub>Subscriptions</option><option value=mdl>Models &amp; chains</option><option value=slk>Communication</option><option value=lop>Loop</option><option value=acc>Access</option></select></label>
<details id=lim class=sp data-sp=lim open><summary>Spend &amp; limits<span class=sr id=limsr></span></summary><div class=sb id=lbody></div></details>
<details id=sub class=sp data-sp=sub><summary>Subscriptions<span class=sr id=subsr></span></summary><div class=sb><div id=subs></div></div></details>
<details id=mdl class=sp data-sp=mdl open><summary>Models &amp; chains<span class=sr id=mdsr></span></summary><div class=sb id=mbody></div></details>
<details id=slk class=sp data-sp=slk><summary>Communication<span class=sr id=slksr></span></summary><div class=sb id=sbody></div></details>
<details id=lop class=sp data-sp=lop open><summary>Loop<span class=sr id=lopsr>start, stop, history</span></summary><div class=sb id=lopb></div><div class=sb id=histb><h4>Run history</h4><div id=list></div></div></details>
<details id=acc class=sp data-sp=acc open><summary>Access<span class=sr>tailnet and phone</span></summary><div class=sb><div id=accb></div><div class=kr><span class=l>This device</span><a id=mobile-link href="#">mobile view</a></div><script>document.getElementById("mobile-link").href=location.pathname.replace(/\/?$/,"/")+"mobile"</script></div></details>
</div></div>
<div class=sbar id=sbar><button id=ssave>Save…</button><button id=sdisc>Discard changes</button><span id=schg>No change</span></div>
<dialog id=sdlg aria-label="Confirm the settings change"><b>Save these settings?</b><pre id=sdpre></pre><p class=m>Written to the project files. Runs already started keep the chains they recorded.</p><div class=sact><button id=sdok>Confirm and save</button><button id=sdno>Back</button></div></dialog>
</section>
</main>
</div>
<div id=tk role=dialog aria-label=Ticket><header><span id=tkchips></span><span class=sp></span><span id=tknav></span><button id=tkx aria-label=Close>✕</button></header><div id=tkset></div><div id=tkb></div><footer><a class=btn id=tkgh target=_blank rel=noopener>Open on GitHub ↗</a><button id=tktodo>→ Todo</button><button id=tkrel>Highlight related</button></footer></div>
<div id=rk role=dialog aria-label=Run><header><span id=rkchips></span><span class=sp></span><button id=rkx aria-label=Close>✕</button></header><div id=rkb></div><footer><a class=btn id=rkgh target=_blank rel=noopener>Open full log ↗</a><button id=rkpause>Pause run</button></footer></div>
<script>/*SWITCHER_JS*/
{const swMe=(location.pathname.match(/^\/([^/?#]+)/)||[])[1]||"";switcherPoll(d=>switcherPaint(document.getElementById("psw"),d,swMe,"/"));}</script>
<script>
{const p=(location.pathname.match(/^\/([^/?#]+)/)||[])[1]||"";document.title=p?`Hub | ${decodeURIComponent(p)}`:"Hub";}   // the tab names the profile it shows, so two open hubs are told apart
const $=s=>document.querySelector(s),ls=JSON.parse(localStorage.hub||"{}");
const HUB_REPO=/*HUB_REPO*/;
let allRuns=[],runs=[],q=ls.q||"",per=ls.per||"day",src=ls.src||"both",N=1,pins=new Set(),slots=[],hl=null,boardRows=[],ENDED=[];   // ENDED: the rows of /api/ended (#984)
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
// a missing reading is not a high one. Red sits above the older, machine-wide amber of `#inst.warn`, and never replaces it.
const LOAD_ERR=Number("__LOADERR__");
function ldNum(v){if(v===null||v===undefined||v===""||typeof v==="boolean")return null;const f=Number(v);return(f!==f||f<0)?null:Math.min(f,100)}
function ldRound(f){const fl=Math.floor(f),fr=f-fl;return fr>.5?fl+1:fr<.5?fl:fl+(fl%2)}   // half to even, exactly as `:.0f` rounds
function ldCell(v){const f=ldNum(v);return{t:f===null?"?":ldRound(f)+"%",red:f!==null&&f>=LOAD_ERR}}
function ldCells(d){return[["CPU","cpu"],["RAM","ram"],["GPU","gpu"]].map(p=>{const c=ldCell((d||{})[p[1]]);return{k:p[0],t:c.t,red:c.red}})}
// Monitor (#983): the card the Settings strip became, drawn as calm flight instruments from what `/api/load` already
// serves: the three load numbers and the facts the sampler last read. Each figure is HTML text; the SVGs only draw and are
// hidden from a screen reader. A tile's state is read off its fact, so the hub keeps deciding the tone and the text.
// INST{
function gaugeGeom(v){const f=ldNum(v),k=f!==null,p=k?f:0,a=Math.PI*p/100;return{known:k,col:k?`hsl(${Math.round(120*(1-p/100))} 65% 42%)`:"var(--line)",deg:Math.round(-90+1.8*p),x:Math.round((30-26*Math.cos(a))*10)/10,y:Math.round((32-26*Math.sin(a))*10)/10}}
function gaugeSvg(v){const g=gaugeGeom(v);return `<svg width=60 height=36 viewBox="0 0 60 36" aria-hidden="true" focusable="false"><path class=gt d="M4 32 A26 26 0 0 1 56 32"/><path class=gf stroke="${g.col}" d="M4 32 A26 26 0 0 1 ${g.x} ${g.y}"/><line class=gn x1=30 y1=32 x2=30 y2=10 style="transform:rotate(${g.deg}deg)"${g.known?"":" visibility=hidden"}/><circle class=gh0 cx=30 cy=32 r=2 /></svg>`}
function subsGauges(d){const xs=(d||{}).subs||[];if(!xs.length)return"";
 return xs.map(s=>{const p=typeof s.pct==="number"?s.pct:null,left=p===null?"?":s.resets?Math.max(0,(s.resets-Date.now()/1000)/86400).toFixed(1)+" d":"?";
  return `<div class="ig${p!==null&&p>=90?" ldred":""}"${s.why||s.window?` title="${iq(s.why||("the "+s.window+" window"))}"`:""}>${gaugeSvg(p)}<span class=gv><b>${esc(s.label)}</b> ${p===null?"?":fpct(p)+"%"}<small class=grst>${esc(left)}</small></span></div>`}).join("")}
function instGauges(d){return ldCells(d).map(c=>`<div class="ig${c.red?" ldred":""}">${gaugeSvg((d||{})[c.k.toLowerCase()])}<span class=gv><b>${c.k}</b> ${esc(c.t)}</span></div>`).join("")}
const instFact=(facts,k)=>(facts||[]).find(f=>f&&f.key===k);
function instTile(k,f,lead){if(!f)return{k,text:"no reading",tone:"neutral"};const p=(f.parts||[])[0]||{},t=String(f.text||"").replace(lead,"").trim()||"?";return{k,text:t,tone:p.tone==="ok"||p.tone==="err"?p.tone:"neutral",tip:f.tip||null,detail:f.detail||null,times:p.times||null}}
function instTiles(facts){const lp=instFact(facts,"loop"),ld=instFact(facts,"landing"),on=lp?/:ON$/.test(lp.text):null,
 land=ld?instTile("Landed",ld,/^landed\s*/):{k:"Landed",text:"no reading",tone:"neutral"},m=/PR #(\d+)/.exec(land.text);
 if(m){land.tone="info";land.href=`https://github.com/__REPO__/pull/${m[1]}`}
 return[instTile("Gate",instFact(facts,"gate"),/^gate\s*/),
  {k:"Loop",text:on===null?"?":on?"ON":"OFF",tone:on===null?"neutral":on?"ok":"err"},land,instTile("GraphQL",instFact(facts,"graphql"),/^graphql\s*/)]}
const iq=t=>esc(t).replace(/"/g,"&quot;");
function tileHtml(t){const v=t.href?esc(t.text).replace(/PR #\d+/,x=>`<a target=_blank rel=noopener href="${iq(t.href)}">${x}</a>`):esc(t.text),
 d=(t.detail||[]).length?`<details><summary>${t.detail.length} failed</summary><ul>${t.detail.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></details>`:"";
 return `<div class="at ${t.tone}"${t.tip?` title="${iq(t.tip)}"`:""}><span class=ah><span class=dot aria-hidden="true"></span>${esc(t.k)}</span><span class=av>${v}</span>${t.times?`<span class=atm>${esc(t.times)}</span>`:""}${d}</div>`}
function instSum(facts){const t=instTiles(facts);return t.find(x=>x.k==="Loop").text+" · "+t.filter(x=>x.k!=="Loop"&&x.k!=="GraphQL"&&x.tone==="err").length+" fail"}
function cycleLeft(text){const m=/^cycle (?:in |\+)(?:(\d+)h(\d\d)m|(\d+)m(\d\d)s)( late)?$/.exec(String(text||""));if(!m)return null;
 const s=m[1]!==undefined?(+m[1])*3600+(+m[2])*60:(+m[3])*60+(+m[4]);return m[5]?-s:s}
function cycleFmt(left){const s=Math.abs(Math.round(left)),h=Math.floor(s/3600),mi=Math.floor(s/60)%60,p=n=>String(n).padStart(2,"0"),
 t=h?`${h}h${p(mi)}m`:`${Math.floor(s/60)}m${p(s%60)}s`;return left<0?`+${t} late`:`in ${t}`}
function instUsage(facts){const f=instFact(facts,"burn");if(!f||!f.text)return null;const p=String(f.text).split(" · ");return{tok:p[0].replace(/^tokens\s*/,""),calls:p.slice(1).join(" · ")}}
function specBar(p){p=p||{};const st=p.state==="ok"||p.state==="info"?" "+p.state:"";   // the hub decides the count, the text and the state; the page only draws them
 const w=100*Math.max(0,Math.min(1,Number(p.frac)||0)),bar=p.bar?`<span class=pb role=progressbar aria-label="Open spec progress" aria-valuemin=0 aria-valuemax=${Number(p.total)||0} aria-valuenow=${Number(p.done)||0}><span class=pf style="width:${w}%"></span></span>`:"";
 return `<div class="at${st}"${p.tip?` title="${iq(p.tip)}"`:""}><span class=ah><span class=dot aria-hidden="true"></span>Specs${p.so?`<span class=so>${esc(p.so)}</span>`:""}</span><span class=av>${esc(p.value||"no open spec")}</span>${bar}</div>`}
let instCyc=null,instLast={};
function instSet(id,h){const e=$("#"+id);if(e&&instLast[id]!==h){instLast[id]=h;e.innerHTML=h}}
let instCycOff=false;   // the hub says "cycle off" when the loop is stopped: no countdown, no "late"
function instTick(){const e=$("#icyc");if(e)e.textContent=instCycOff?"loop off":instCyc===null?"?":cycleFmt(instCyc-Date.now()/1000)}
function instDraw(d){const c=$("#inst");if(!c)return;const facts=(d||{}).facts||[];c.classList.toggle("warn",!!(d||{}).warn);
 const gs=$("#igs");if(gs&&!gs.children.length)gs.innerHTML=instGauges(d);
 else if(gs)ldCells(d).forEach((x,i)=>{const n=gs.children[i],g=gaugeGeom((d||{})[x.k.toLowerCase()]);if(!n)return;n.classList.toggle("ldred",x.red);
  const ln=n.querySelector(".gn"),f=n.querySelector(".gf"),v=n.querySelector(".gv");
  if(ln){ln.style.transform=`rotate(${g.deg}deg)`;ln.setAttribute("visibility",g.known?"visible":"hidden")}if(f){f.setAttribute("d",`M4 32 A26 26 0 0 1 ${g.x} ${g.y}`);f.setAttribute("stroke",g.col)}if(v)v.innerHTML=`<b>${x.k}</b> ${esc(x.t)}`});
 instSet("ian",instTiles(facts).map(tileHtml).join(""));instSet("isub",subsGauges(d||{}));instSet("ispec",specBar((d||{}).specs));
 const u=instUsage(facts);instSet("iuse",u?`<div class=irw><span>Tokens</span><b class=bad>${esc(u.tok)}</b></div><div class=irw><span>Calls over cap</span><b class=bad>${esc(u.calls)}</b></div>`:"<div class=irw><span>Tokens</span><b>no call over the cap</b></div>");
 const s=$("#instsr");if(s)s.textContent=instSum(facts);
 const cy=instFact(facts,"cycle"),l=cy?cycleLeft(cy.text):null;instCycOff=!!cy&&cy.text==="cycle off";instCyc=l===null?null:Date.now()/1000+l;instTick()}
// }INST
async function loadPoll(){try{const d=await (await fetch("/api/load")).json();instDraw(d)}catch(e){}}
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
  const ccRow=r=>{const h=r.held&&runs.some(x=>x.id===r.held)?r.held:null;return `<div class="r cc ${h&&shown.has(h)?"show":""}"${h?` data-id="${esc(h)}" title="open ${esc(h)}"`:""}><div><span class="b ${r.state}"${r.tip?` title="${esc(r.tip)}"`:""}>${esc(r.badge)}</span> <b>${esc(r.label)}</b>${r.sub?`<div class=st title="${esc(r.sub)}">${esc(r.sub)}</div>`:""}${(r.lines||[]).map(l=>`<div class=st>${esc(l)}</div>`).join("")}${r.live?`<div class=m>age ${age(r.age)}</div>`:""}</div></div>`};
  const row=r=>r.kind==="childcc"?ccRow(r):`<div class="r ${shown.has(r.id)?"show":""}" data-id="${r.id}"><div>${r.kind==="test"?`<span class=kd title="GUI test run">🧪</span>`:""}<span class="b ${r.state}"${r.tip?` title="${esc(r.tip)}"`:""}>${esc(r.badge||r.state)}</span> <b title="${esc(labelTip(r))}">${esc(name(r))}</b>${loadMark(r)}${!(r.issues||[]).length&&r.issue_why&&!r.fix&&!r.url?`<div class=st>${esc(labelTip(r))}</div>`:""}${sub(r)?`<div class=st title="${esc(sub(r))}">${esc(sub(r))}</div>`:""}<div class=m>${r.share?`<span class="sh${r.share.pct===100?" done":""}" title="the newest gate that counted ran ${r.share.total} tests, ${r.share.passed} passed">${r.share.passed}/${r.share.total} · ${r.share.pct}%</span> · `:""}${usd(r.spend)} · ${Math.round(r.dur)}m · ${age(r.age)} ago · ${r.id.slice(-6)} · :${r.port}</div></div><span class=pn title="shown in the panel">${pins.has(r.id)?"●":"○"}</span></div>`;
  {const live=runs.filter(r=>r.rank===0);$("#lrun").innerHTML=live.map(row).join("")||"<div class=m>no live runs</div>";$("#lrs").textContent=live.length?`${live.length} \u00b7 ${live.filter(r=>r.state==="running").length} running`:"none"}   // the sidebar card: the runs that are live now, the same rows as the history
  $("#list").innerHTML=filled.map(([k,t])=>{const rows=by[k]||[],h=groupHeader(k,rows);return `<div class="gh ${openGroups.has(k)?"op":""}" data-g="${k}" tabindex=0>${t}<span class=sr title="${esc(h.title)}">${h.text}</span></div>${openGroups.has(k)?rows.map(row).join(""):""}`}).join("")||"<div class=m style=padding:10px>no live dashboards</div>";
  document.querySelectorAll(".gh").forEach(e=>e.onclick=()=>{const k=e.dataset.g;   // the header is the only writer: a click toggles this group and nothing else
    if(openGroups.has(k))openGroups.delete(k);else openGroups.add(k);saveGroups();drawList()});
  document.querySelectorAll(".r[data-id]").forEach(e=>e.onclick=()=>{const id=e.dataset.id;   // the childcc row carries the id of the run it holds, never its own (#598, #689)
    hl={run:id};paintHL();openRun(id)});   // a run opens in the side sheet (#984)
}
// The Runs chart tab lists the live runs and the runs ended today; a row opens the run sheet, never an embedded dashboard (#984).
function drawMain(){if(typeof TAB==="string"&&TAB!=="flow")return;
  const row=r=>`<div class=r data-rid="${esc(r.id)}" tabindex=0 role=button><span class="b ${esc(r.state)}">${esc(r.badge||r.state)}</span><b>${esc(name(r))}</b><span class=m>${usd(r.spend)} \u00b7 ${age(r.age)} ago</span></div>`;
  const live=runs.filter(r=>r.rank===0||r.running),ids=new Set(live.map(r=>r.id)),ended=ENDED.filter(r=>!ids.has(r.id));
  $("#main").innerHTML=(live.length?"<h3>Live</h3>"+live.map(row).join(""):"")+(ended.length?"<h3>Ended today</h3>"+ended.map(row).join(""):"")||"<div class=m>no run is live and none ended today</div>"}
function refresh(){plan();drawList();paintHL();drawMain();rkFollow()}
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
    +`<text x="${W}" y="${pad-3}" fill="#3ecf8e" style="font-size:var(--fs-min)" text-anchor="end">max ${usd(mx)}</text>`
    +rows.map((r,i)=>`<rect x="${i*bw+1}" y="${pad+H-r.spend/mx*H}" width="${Math.max(bw-2,1)}" height="${r.spend/mx*H}" fill="${r.spend>=mx?"#3ecf8e":"#5aa9ff"}"><title>${xl(r.t)}: ${usd(r.spend)} · ${r.runs} runs</title></rect>`).join("")
    +`<text x="0" y="${H+pad+12}" fill="#8a93a3" style="font-size:var(--fs-min)">${xl(rows[0].t)}</text>`
    +`<text x="${W}" y="${H+pad+12}" fill="#8a93a3" style="font-size:var(--fs-min)" text-anchor="end">now</text></svg>`}
function kr(label,sub,v,unit,base,lowerBetter=true){if(v==null)return`<div class=kr><span class=l>${label}${sub?`<small>${sub}</small>`:""}</span><b>—</b></div>`;
  const u=unit?String(unit).trim():"";
  let chip="";if(base){const d=Math.round((v-base)/base*100),good=lowerBetter?d<0:d>0;
    const pct=`<span class="${good?"good":"bad"}">${Math.abs(d)}% ${lowerBetter?(d<0?"faster":"slower"):(d>0?"more":"fewer")}</span>`;
    chip=d===0?`<span class=d>was ${base}${u?" "+u:""}</span>`:`<span class=d>was ${base}${u?" "+u:""} · ${pct}</span>`}
  return `<div class=kr><span class=l>${label}${sub?`<small>${sub}</small>`:""}</span><b${v===0?" class=m":""}>${v}${u?`<small>${u}</small>`:""}</b>${chip}</div>`}
function openRows(o){return `<h4>Open branches <small>oldest first</small></h4>`
  +(o&&o.length?o.map((x,i)=>`<div class=kr><span class=l>#${x.n} · <b${x.min>=30?" class=w":""}>${x.min}</b> min${i===0?" (warns at 30)":""}</span></div>`).join("")
   :`<div class=kr><span class=l>none open</span></div>`)}
// SETV{ the settings view's pure rules (#913): the reset text, the add list, the diff before save. Run in node by tests.
function rstText(t,now){if(!t)return"reset unknown";const x=t-now;if(x<=0)return"resets now";if(x>=86400)return`resets in ${(x/86400).toFixed(1)} d`;if(x>=3600)return`resets in ${Math.round(x/3600)} h`;return`resets in ${Math.max(1,Math.round(x/60))} min`}
function addOpts(sug,chain,isOff){return (sug||[]).filter(s=>!(chain||[]).includes(s.id)&&!isOff(s.id))}
function chainDesc(d,k){return (d[k]||[]).map(m=>{const e=((d.efforts||{})[k]||{})[m]||"",h=((d.hours||{})[k]||{})[m]||"";return [m,e&&e!=="medium"?e:"",h||"always"].filter(Boolean).join(" · ")}).join(" → ")}
function mdlDiffLines(old,cur,lab){const T="Models & chains · ",out=[],ids=[...new Set([...(old.models||[]),...(cur.models||[])].map(r=>r.id))];
 for(const m of ids){const a=!!(old.model_off||{})[m],b=!!(cur.model_off||{})[m];if(a!==b)out.push(`- ${T}model ${m}: ${a?"off":"on"}`,`+ ${T}model ${m}: ${b?"off":"on"}`)}
 for(const k of Object.keys(lab)){const a=chainDesc(old,k),b=chainDesc(cur,k);if(a!==b)out.push(`- ${T}${lab[k]}: ${a||"(empty)"}`,`+ ${T}${lab[k]}: ${b||"(empty)"}`)}
 return out}
function themeLine(theme,l){return l.startsWith(theme+" · ")?l:theme+" · "+l}
// }SETV
let mdl=null,mdlSrv="",mdlMsg="",mdlRmk={};const MLAB={coding_models:"Coding",review_models:"Review",vision_models:"Vision",router_models:"Router"},MDESC={coding_models:"Writes the code in squad runs.",review_models:"Reviews each PR independently.",vision_models:"Looks at screenshots and images.",router_models:"The models the router runs on, tried in order."},EFF=["low","medium","high","max"];
/* ✕ marks a row; the row stays, struck, until Save takes it out of the chain with its effort and hours (#913). */
function mdlRmToggle(k,m){const id=k+"|"+m;if(mdlRmk[id])delete mdlRmk[id];else mdlRmk[id]=true}
const mdlRmd=(k,m)=>!!mdlRmk[k+"|"+m];
/* What Save writes: the panel without its marked rows and without empty keys. Dirty means a diff line, never a byte. */
function mdlPrune(o){for(const g of ["efforts","hours"]){o[g]=o[g]||{};for(const k of Object.keys(o[g]))if(!Object.keys(o[g][k]||{}).length)delete o[g][k]}
 o.model_off=o.model_off||{};for(const m of Object.keys(o.model_off))if(!o.model_off[m])delete o.model_off[m];return o}
function mdlOut(){const o=JSON.parse(JSON.stringify(mdl));for(const k of Object.keys(MLAB)){const keep=[];for(const m of o[k]||[]){if(!mdlRmd(k,m)){keep.push(m);continue}
 for(const g of ["efforts","hours"])if(o[g]&&o[g][k])delete o[g][k][m]}o[k]=keep}return mdlPrune(o)}
function mdlChanged(){return !!mdl&&mdlDiffLines(JSON.parse(mdlSrv),mdlOut(),MLAB).length>0}
function mdlEff(k,m){return (mdl.efforts&&mdl.efforts[k]&&mdl.efforts[k][m])||""}
function mdlSetEff(k,m,v){mdl.efforts=mdl.efforts||{};mdl.efforts[k]=mdl.efforts[k]||{};if(EFF.includes(v)&&v!=="medium")mdl.efforts[k][m]=v;else delete mdl.efforts[k][m]}
const HRP=["22-07","07-22","20-08","18-06"],HRX=/^([01]\d|2[0-3])-([01]\d|2[0-3])$/;
function mdlHrs(k,m){return (mdl.hours&&mdl.hours[k]&&mdl.hours[k][m])||""}
function mdlSetHrs(k,m,v){v=(v||"").trim();mdl.hours=mdl.hours||{};mdl.hours[k]=mdl.hours[k]||{};if(HRX.test(v)&&v.slice(0,2)!==v.slice(3))mdl.hours[k][m]=v;else delete mdl.hours[k][m]}
/* The chain row's old switch (#1819) is read only now: a file that holds it shows "off (chain)" (SPEC 889 #920). */
function mdlOff(k,m){return !!(mdl.off&&mdl.off[k]&&mdl.off[k][m])}
/* The model's switch, one per model for every chain (SPEC 889 #920). The table stages it until Save; a chip writes it at once. */
function mdlSetOffM(m,v){mdl.model_off=mdl.model_off||{};if(v)mdl.model_off[m]=true;else delete mdl.model_off[m]}
/* What a model box shows is the hub's answer, model_box() (#677, ADR-0137): the registry label and its route, or
   "unregistered". The page holds no provider rule. An id just typed into an Add box has no answer until the save. */
const MBOX0={llm:null,route:null,registered:null,text:"resolved on save"};
function mdlBox(k,m){const b=mdl.boxes||{};return (b[k]||{})[m]||(b.known||{})[m]||MBOX0}
function mdlSub(x){if(!x.sub)return"";const q=x.quota||{},when=q.spent&&q.until_text?`, resets ${esc(q.until_text)}`:"";return`<span class="${q.spent?"bk":"el"}" title="${esc(x.sub.label)}${q.reason?" · "+esc(q.reason):""}">${esc(x.sub.label)}${q.spent?" · spent"+when:""}</span>`}
function mdlName(x,m){return x.llm?esc(x.llm):esc(m)}   // the id stays in the row title; only an unregistered row shows it (#687)
function mdlRoute(x,dflt){return x.registered===false?`<span class=ur>unregistered</span>`:esc(x.route||(x.registered?dflt:x.text))}
/* The model switch (SPEC 889 #920): one boolean per model, written by the chips and by the table through POST /api/models.
   The chain row's old switch is read only: a file that holds it shows "off (chain)" and the panel writes none. */
function mdlOffM(m){return !!(mdl.model_off&&mdl.model_off[m])}
function mdlChip(st){return st==="on"?"":`<span class="stc${String(st).startsWith("off")?" off":" lim"}">${esc(String(st).startsWith("off")?"off":st)}</span>`}   // a state chip shows only at limit or off (#913)
function mdlTable(){const rows=mdl.models||[];return `<h3>Models</h3><p class=m>On or off, for every chain. An off model leaves the add lists; a chain that still lists it keeps the row, struck through, and skips it.</p><table class=mtab><thead><tr><th>Model</th><th>Subscription</th><th>State</th><th>On</th></tr></thead><tbody>`+rows.map(r=>{const of=mdlOffM(r.id),x=mdlBox("known",r.id);return`<tr class="${of?"off":""}"><td class=mn title="${esc(r.id)} · ${esc(x.text)}">${mdlName(x,r.id)}</td><td>${mdlSub(r)}</td><td>${mdlChip(of?"off (model)":r.state)}</td><td><button class="mtg${of?" off":""}" data-mt="${esc(r.id)}" aria-pressed="${!of}" aria-label="${esc(r.id)} on" title="${of?"off (model): press to switch it on in every chain":"on: press to switch it off in every chain"}">${of?"off":"on"}</button></td></tr>`}).join("")+`</tbody></table>`}
const MHELP=`<details class=hlp><summary>How it works</summary><ul><li><b>Order</b>: ↑ ↓ move a model; ✕ marks it for removal: the row stays, struck through, until Save, and ↺ keeps it; "+ add a model…" puts one back.</li><li><b>Off</b> is set once per model and applies in every chain. An off model is skipped where a chain still lists it (the row stays, struck through, and ✕ still removes it) and is not offered in "+ add a model…". Switching it on restores every row.</li><li><b>Hours</b> are set per chain row: <code>HH-HH</code> local, may wrap midnight (<code>22-07</code>); empty means always. Outside its hours the row is skipped like off, and the next row runs.</li><li><b>Effort</b> is low to max, per chain row; medium is the default.</li><li><b>Save</b>, at the foot of Settings, shows the change (- before, + after) and writes only when you confirm.</li></ul></details>`;
function mdlRender(){const b=$("#mbody");if(!mdl){b.innerHTML="<span class=m>loading…</span>";return}
 {const lc=mdl.last_coding||(mdl.coding_models||[])[0]||"",x=lc?mdlBox("coding_models",lc):MBOX0;$("#mdsr").textContent=x.llm?`${x.llm} · ${lc}`:lc}   // the model the last run coded with, not the chain's head

 const ch=mdl.chain||{},chOut=ch.out||[],chHit=(m)=>chOut.find(o=>o.until_text&&o.model.split(':').pop()===String(m).split(':').pop()),chTag=(m)=>{const o=chHit(m);return o?`<span class=bk title="${esc(o.reason)}">${esc(o.reason)}, resets ${esc(o.until_text)}</span>`:''},chBan=ch.banner?`<div class="mx err" role=alert>${esc(ch.banner)}</div>`:(ch.notice?`<div class=mx>${esc(ch.notice)}</div>`:"");
 b.innerHTML=chBan+`<p class="note lead">Which models work, in what order, in which hours: a chain is tried top to bottom, and the first model that is on, inside its hours and under its limit does the work.</p>`
 +`<datalist id=hrp>${HRP.map(v=>`<option value="${v}">`).join("")}</datalist>`+mdlTable()+`<h3>Chains</h3>`
 +Object.keys(MLAB).map(k=>`<div class=mc><div class=mt>${MLAB[k]}</div><div class=mx>${MDESC[k]}</div>`+mdl[k].map((m,i)=>{const ef=mdlEff(k,m),hr=mdlHrs(k,m),x=mdlBox(k,m),cof=mdlOff(k,m),mof=mdlOffM(m),o=(mdl.outside&&mdl.outside[k]||{})[m],st=((mdl.models||[]).find(z=>z.id===m)||{}).state;
  const rmk=mdlRmd(k,m),live=mdl[k].filter(z=>!mdlRmd(k,z)).length;
  return`<div class="mr${cof||mof?" off":""}${rmk?" rmk":""}" data-row="${k}|${esc(m)}"><span class=rk>${i+1}</span><span class=mn title="${esc(m)} · ${esc(x.text)}">${mdlName(x,m)}</span><span class=ct><span class=pv title="${esc(x.text)}">${mdlRoute(x,"")}${mdlSub(x)}</span>${mof?`<span class="stc off">off (model)</span>`:cof?`<span class="stc off">off (chain)</span>`:""}${st==="at limit"?`<span class="stc lim">at limit</span>`:""}${k==="review_models"?chTag(m):""}${o?`<span class=bk title="this row runs only inside its hours">outside hours ${esc(o)}</span>`:""}<label class=el>effort <select class="ef${ef?" set":""}" data-ef="${k}|${esc(m)}" aria-label="${esc(m)}: effort" title="effort for this model (medium is the default)">${EFF.map(e=>`<option value="${e==="medium"?"":e}"${(ef||"medium")===e?" selected":""}>${e}</option>`).join("")}</select></label><label class=el>hours <input class="hr${hr?" set":""}" list=hrp data-hr="${k}|${esc(m)}" value="${esc(hr)}" placeholder="always" aria-label="${esc(m)}: hours" title="local hours this model may run (HH-HH, wraps midnight; empty = always)"></label><span class=mb><button data-mv="${k}|${i}|-1" ${i?"":"disabled"} aria-label="move up" title="move up">↑</button><button data-mv="${k}|${i}|1" ${i<mdl[k].length-1?"":"disabled"} aria-label="move down" title="move down">↓</button><button class=x data-rm="${k}|${i}" ${rmk||live>1?"":"disabled"} aria-label="${rmk?"undo remove":"remove"} ${esc(m)}" title="${rmk?"keep this model in the chain":"remove from chain on Save"}">${rmk?"↺":"✕"}</button></span></span></div>`}).join("")
  +`<div class=madd><select data-add="${k}" aria-label="${MLAB[k]}: add a model"><option value="">+ add a model…</option>${addOpts((mdl.suggest||{})[k],mdl[k],mdlOffM).map(s=>`<option value="${esc(s.id)}">${esc(s.text)}</option>`).join("")}</select></div></div>`).join("")
  +MHELP+(mdlMsg?`<div class="mx${mdlMsg.startsWith("!")?" err":""}" role=status>${esc(mdlMsg.replace(/^!/,""))}</div>`:"");
 if(typeof setBar==="function")setBar()}
async function mdlLoad(){try{mdl=await (await fetch("/api/models")).json();mdlSrv=JSON.stringify(mdl);mdlRmk={};mdlRender()}catch(e){}}
/* One write route for both places (SPEC 889 #920): a chip flips at once; the table stages its switch until Save. */
async function mdlPost(body){const r=await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});return r.json()}
$("#mbody").addEventListener("change",e=>{const t=e.target;if(!mdl)return;mdlMsg="";if(t.dataset.ef){const[k,m]=t.dataset.ef.split("|");mdlSetEff(k,m,t.value)}
 else if(t.dataset.hr){const[k,m]=t.dataset.hr.split("|"),v=t.value.trim();if(v&&!HRX.test(v))mdlMsg="!hours are HH-HH, for example 22-07";mdlSetHrs(k,m,v)}
 else if(t.dataset.add){const k=t.dataset.add,v=t.value;if(v&&!mdl[k].includes(v)&&!mdlOffM(v))mdl[k].push(v)}
 else return;mdlRender()});
$("#mbody").addEventListener("click",e=>{const t=e.target.closest("button");if(!t||!mdl)return;mdlMsg="";
 if(t.dataset.mt){const m=t.dataset.mt;mdlSetOffM(m,!mdlOffM(m))}
 else if(t.dataset.mv){const[k,i,d]=t.dataset.mv.split("|"),a=mdl[k],j=+i+ +d;[a[i],a[j]]=[a[j],a[i]]}
 else if(t.dataset.rm){const[k,i]=t.dataset.rm.split("|");mdlRmToggle(k,mdl[k][+i])}
 else return;
 mdlRender()});
/* Settings > Spend & limits (#926): the token guard's on/off switch, its daily cap and its pause factor, with the burn
   it measures and whether dispatch is paused now. The page renders and asks; the hub validates, shows the diff, writes
   the profile and records the change (ADR-0137), and the guard reads it on its next pass with no restart. */
let limS=null,limMsg="",limDiff=null,limPend=null;   // limPend holds the typed settings while the diff asks for a confirm
const limN=v=>v==null?"—":tk(v);
const limVals=()=>limPend||{guard:($("#limg")||{}).value||"",cap:($("#limc")||{}).value||"",factor:($("#limf")||{}).value||""};
function limRender(){const b=$("#lbody");if(!b)return;if(!limS){b.innerHTML="<span class=m>loading…</span>";return}
 const s=limS,v=limPend||s;const sr=$("#limsr");if(sr)sr.textContent=s.guard==="off"?"off":s.paused?"paused":"on";
 let h=`<div class=kr><span class=l>token guard</span><select id=limg aria-label="token guard"><option value=on${v.guard==="on"?" selected":""}>on</option><option value=off${v.guard==="off"?" selected":""}>off</option></select></div>`
 +`<div class=kr><span class=l>daily token cap</span><input id=limc type=number min=1 step=1 inputmode=numeric value="${v.cap}" aria-label="daily token cap"></div>`
 +`<div class=kr><span class=l title="the hourly burn that pauses dispatch, as a multiple of the cap's hourly share">pause factor</span><input id=limf type=number min=0.1 step=0.5 inputmode=decimal value="${v.factor}" aria-label="pause factor"></div>`
 +`<div class=kr><span class=l>dispatch pauses over</span><b>${limN(s.pause_line)}<small>tokens/h</small></b></div>`
 +`<div class=kr><span class=l>last hour</span><b${s.hour_burn==null?" class=m":""}>${limN(s.hour_burn)}<small>tokens</small></b></div>`
 +`<div class=kr><span class=l>window <small>${s.window_h} h</small></span><b${s.window_burn==null?" class=m":""}>${limN(s.window_burn)}<small>tokens</small></b></div>`
 +`<div class=kr><span class=l>dispatch now</span><b class="${s.paused?"w":"ok"}">${s.paused?"paused":"running"}</b></div>`
 +`<div class=kr><span class=l>last change</span><span class=lv title="${esc(s.profile||"")}">${s.last_change?esc(s.last_change.at+" · "+s.last_change.text):"none recorded"}</span></div>`;
 if(limMsg)h+=`<div class="mx${limMsg.startsWith("!")?" err":""}" role=status>${esc(limMsg.replace(/^!/,""))}</div>`;
 b.innerHTML=h;if(typeof setBar==="function")setBar()}
async function limLoad(){try{limS=await (await fetch("/api/limits")).json();limDiff=null;limMsg="";limRender()}catch(e){limMsg="!load failed";limRender()}}
async function limPost(confirm){const vals=limVals();if(!confirm)limPend={guard:vals.guard,cap:vals.cap,factor:vals.factor};   // the diff is asked about what was typed, not about what is saved
 try{const r=await fetch("/api/limits",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...vals,confirm})}),d=await r.json();
 if(d.err){limDiff=null;limMsg="!"+d.err}
 else if(confirm){limS=d.state||limS;limDiff=null;limPend=null;limMsg=(d.diff||[]).length?"saved · the guard reads it on its next pass":"nothing changed"}
 else if((d.diff||[]).length)limDiff=d.diff;else{limMsg="nothing changed";limPend=null}}catch(x){limMsg="!save failed"}
 limRender()}
$("#lbody").addEventListener("input",()=>{limMsg="";setBar()});
/* Settings > Communication (#924): how the agent reads and writes Slack for this profile. The page renders and asks; the hub
   validates, shows the diff and writes the profile's gitignored file on confirm (ADR-0137). The hub holds no token. */
let slS=null,slDraft=null,slD=null,slMsg="";
const SL_LANG=["pt-br","en-US","es"],SL_ROLE={Manager:"drafts the answer; posts only after the owner's OK",Colleague:"answered directly; a bug it reports becomes an issue in Todo at once, with an acknowledgment and the link"};
const SL_EV={stg:"STG release",prod:"PROD release",dev:"dev landing",bug:"bug acknowledgment",fix:"status correction"};
const slA=t=>esc(t).replace(/"/g,"&quot;");
const slClone=o=>JSON.parse(JSON.stringify(o));
const slDirty=()=>!!slDraft&&JSON.stringify(slDraft)!==JSON.stringify(slS);
const slOpts=(list,cur)=>list.map(x=>`<option${x===cur?" selected":""}>${slA(x)}</option>`).join("");
const slChan=(c,i)=>{const on=c.watch?"":" disabled",n=slA(c.name);
 return `<div class="slc${c.watch?"":" off"}" role=row><span class=c1 role=cell><b>${esc(c.name)}</b>${c.watch?"":"<small>do not watch</small>"}</span>`
 +`<span role=cell data-l="language"><select data-sl="ch:${i}:lang" aria-label="${n}: language"${on}>${slOpts(SL_LANG,c.lang)}</select></span>`
 +`<span class=swc role=cell data-l="watch"><label class=swt><input type=checkbox data-sl="ch:${i}:watch"${c.watch?" checked":""} aria-label="${n}: watch"> watch</label></span>`
 +`<span class=swc role=cell data-l="thread only"><label class=swt><input type=checkbox data-sl="ch:${i}:thread"${c.thread?" checked":""}${on} aria-label="${n}: thread only"> thread only</label></span>`
 +`<span class=swc role=cell data-l="mention only"><label class=swt><input type=checkbox data-sl="ch:${i}:mention"${c.mention?" checked":""}${on} aria-label="${n}: mention only"> mention only</label></span>`
 +`<span role=cell data-l="announce">${Object.keys(SL_EV).map(k=>`<label class=sev title="${SL_EV[k]} posted here"><input type=checkbox data-sl="ch:${i}:ev:${k}"${c.events[k]?" checked":""}${on} aria-label="${n}: ${k} posts"> ${k}</label>`).join("")}</span>`
 +`<span class=swc role=cell><button data-slrm="ch:${i}" aria-label="remove ${n}">Remove</button></span></div>`};
const slPerson=(p,i)=>`<div class=slc role=row><span class=c1 role=cell><b>${esc(p.name)}</b></span>`
 +`<span role=cell data-l="role"><select data-sl="pp:${i}:role" aria-label="${slA(p.name)}: role">${slOpts(Object.keys(SL_ROLE),p.role)}</select></span>`
 +`<span class=c1 role=cell data-l="the agent">${esc(SL_ROLE[p.role])}</span>`
 +`<span class=swc role=cell><button data-slrm="pp:${i}" aria-label="remove ${slA(p.name)}">Remove</button></span></div>`;
function slRender(){const b=$("#sbody");if(!b)return;if(!slS){b.innerHTML="<span class=m>loading…</span>";return}
 const d=slDraft,sr=$("#slksr");if(sr)sr.textContent=slS.connection;
 let h=`<div class=kr><span class=l>connection</span><b class="${slS.connection==="connected"?"ok":"w"}">${esc(slS.connection)}</b></div>`
 +`<p class=m>Posts through your own account by the Claude Slack connector. No token is stored here.</p>`
 +`<div class=kr><span class=l>prefix</span><input data-sl=prefix value="${slA(d.prefix)}" maxlength=40 aria-label="prefix"></div>`
 +`<div class=kr><span class=l>default language</span><select data-sl=lang aria-label="default language">${slOpts(SL_LANG,d.lang)}</select></div>`
 +`<div class=kr><span class=l>read every</span><span><input data-sl=tick type=number min=1 max=30 value="${slA(d.tick)}" aria-label="read every, minutes"> min</span></div>`
 +`<div class=kr><span class=l>up to, when quiet</span><span><input data-sl=max type=number min=2 max=120 value="${slA(d.max)}" aria-label="up to, minutes"> min</span></div>`
 +`<div class=kr><span class=l>quiet hours</span><input data-sl=quiet value="${slA(d.quiet)}" placeholder="always on" aria-label="quiet hours, HH-HH"></div>`
 +`<div class=kr><span class=l>anyone not listed</span><select data-sl=other aria-label="anyone not listed"><option value=ignore${d.other==="ignore"?" selected":""}>is ignored</option><option value=reply${d.other==="reply"?" selected":""}>gets a fixed reply</option></select></div>`
 +`<h4>Channels</h4><div class=slt role=table><div class="slc slh" role=row><span>Channel</span><span>Language</span><span>Watch</span><span>Thread only</span><span>Mention only</span><span>Announce</span><span></span></div>`
 +(d.channels.map(slChan).join("")||`<p class=m>No channel: the agent reads and posts nowhere.</p>`)+`</div>`
 +`<div class=slad><input id=slch placeholder="#channel or id" aria-label="channel to add"><button id=slcha>Add channel</button></div>`
 +`<h4>People</h4><div class=slt role=table><div class="slc slh" role=row><span>Person</span><span>Role</span><span>What the agent does</span><span></span></div>`
 +(d.people.map(slPerson).join("")||`<p class=m>No person: the agent answers nobody by name.</p>`)+`</div>`
 +`<div class=slad><input id=slpn placeholder="name" aria-label="person to add"><select id=slpr aria-label="role">${slOpts(Object.keys(SL_ROLE),"Colleague")}</select><button id=slpa>Add person</button></div>`
 +`<h4>Templates</h4>`+Object.keys(SL_EV).map(k=>`<details class=tp><summary>${SL_EV[k]} <span class=m>${d.channels.filter(c=>c.watch&&c.events[k]).map(c=>esc(c.name)).join(", ")||"no channel"}</span></summary><textarea data-sl="tpl:${k}" rows=3 maxlength=600 aria-label="${SL_EV[k]} template">${esc(d.templates[k])}</textarea></details>`).join("")
 +`<h4>Optional</h4><div class=kr><label class=swt><input type=checkbox data-sl=dm${d.dm?" checked":""} aria-label="owner self-DM as an inbox"> Owner self-DM as an inbox</label></div>`
 +`<div class=kr><label class=swt><input type=checkbox data-sl=audio${d.audio?" checked":""}${d.dm?"":" disabled"} aria-label="audio in and out"> Audio in and out</label></div>`;
 if(slMsg)h+=`<div class="mx${slMsg.startsWith("!")?" err":""}" role=status>${esc(slMsg.replace(/^!/,""))}</div>`;
 b.innerHTML=h;if(typeof setBar==="function")setBar()}
function slEdit(k,t){const p=k.split(":"),v=t.type==="checkbox"?t.checked:t.value;
 if(p[0]==="ch"){const c=slDraft.channels[+p[1]];if(p[2]==="ev")c.events[p[3]]=v;else c[p[2]]=v}
 else if(p[0]==="pp")slDraft.people[+p[1]].role=v;
 else if(p[0]==="tpl")slDraft.templates[p[1]]=v;
 else if(k==="tick"||k==="max")slDraft[k]=Number(v);
 else slDraft[k]=v}
async function slLoad(){try{slS=await (await fetch("/api/slack")).json();slDraft=slClone(slS);slD=null;slMsg="";slRender()}catch(e){slMsg="!load failed";slRender()}}
async function slPost(confirm){try{const r=await fetch("/api/slack",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...slDraft,confirm})}),d=await r.json();
 if(d.err){slD=null;slMsg="!"+d.err}
 else if(confirm){slS=d.state;slDraft=slClone(slS);slD=null;slMsg="saved"}
 else if((d.diff||[]).length){slD=d.diff;slMsg=""}
 else{slD=null;slMsg="nothing changed"}}catch(x){slMsg="!save failed"}
 slRender()}
$("#sbody").addEventListener("change",e=>{const k=e.target.dataset.sl;if(!k||!slDraft)return;slD=null;slMsg="";slEdit(k,e.target);slRender()});
$("#sbody").addEventListener("click",e=>{const b=e.target.closest("button");if(!b||!slDraft)return;
 if(b.dataset.slrm){const[t,i]=b.dataset.slrm.split(":");(t==="ch"?slDraft.channels:slDraft.people).splice(+i,1);slD=null;slRender()}
 else if(b.id==="slcha"){const v=$("#slch").value.trim();if(v)slDraft.channels.push({name:v,lang:slDraft.lang,...slS.new_channel,events:{...slS.new_channel.events}});slD=null;slRender()}
 else if(b.id==="slpa"){const v=$("#slpn").value.trim();if(v&&!slDraft.people.some(p=>p.name===v))slDraft.people.push({name:v,role:$("#slpr").value});slD=null;slRender()}
 });
/* Settings (#913): a left theme list (a select on the phone) shows one theme at a time, and one page-level Save shows
   the diff of every theme with a change, each line naming its theme, and writes only after the confirm. Loop and
   Access have no Save: the bar hides there. */
const STH={lim:"Spend & limits",sub:"Subscriptions",mdl:"Models & chains",slk:"Communication",lop:"Loop",acc:"Access"};
let sTheme=STH[ls.sth]?ls.sth:"mdl",sPend=null;
const limDirty=()=>{if(!limS||!$("#limc"))return false;const v=limVals();return String(v.guard)!==String(limS.guard)||String(v.cap)!==String(limS.cap)||String(v.factor)!==String(limS.factor)};
const mdlDirty=()=>mdlChanged();
const setDirty=()=>[["lim",limDirty()],["mdl",mdlDirty()],["slk",slDirty()]].filter(x=>x[1]).map(x=>x[0]);
function setBar(){const bar=$("#sbar");if(!bar)return;const d=setDirty();bar.hidden=sTheme==="lop"||sTheme==="acc"||sTheme==="sub";bar.classList.toggle("dirty",d.length>0);
 $("#ssave").disabled=!d.length;$("#sdisc").disabled=!d.length;$("#schg").textContent=d.length?"Unsaved: "+d.map(k=>STH[k]).join(", "):`${STH[sTheme]}: no change`}
function setTheme(sp){if(!STH[sp])sp="mdl";sTheme=sp;ls.sth=sp;save();
 document.querySelectorAll("#tab-set details.sp").forEach(e=>{const on=e.dataset.sp===sp;e.classList.toggle("on",on);if(on&&!e.open)e.open=true});
 document.querySelectorAll(".snav button").forEach(b=>b.classList.toggle("on",b.dataset.sp===sp));$("#ssel").value=sp;setBar()}
$(".snav").addEventListener("click",e=>{const b=e.target.closest("button[data-sp]");if(b)setTheme(b.dataset.sp)});
$("#ssel").addEventListener("change",e=>setTheme(e.target.value));
const setAskOne=async(url,body,theme)=>{try{const r=await (await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...body,confirm:false})})).json();
 return r.err?["! "+themeLine(theme,r.err)]:(r.diff||[]).map(l=>"  "+themeLine(theme,l))}catch(x){return["! "+theme+" · the diff could not be read"]}};
$("#ssave").onclick=async()=>{const d=setDirty(),lines=[];sPend=d;
 if(d.includes("mdl"))lines.push(...mdlDiffLines(JSON.parse(mdlSrv),mdlOut(),MLAB));
 if(d.includes("lim")){const v=limVals();limPend={guard:v.guard,cap:v.cap,factor:v.factor};lines.push(...await setAskOne("/api/limits",limPend,"Spend & limits"))}
 if(d.includes("slk"))lines.push(...await setAskOne("/api/slack",slDraft,"Communication"));
 $("#sdpre").textContent=lines.join("\n")||"No change to save.";$("#sdok").disabled=!lines.length||lines.some(l=>l.startsWith("!"));$("#sdlg").showModal()};
$("#sdno").onclick=()=>{sPend=null;limPend=null;$("#sdlg").close()};
$("#sdok").onclick=async()=>{const d=sPend||[];sPend=null;$("#sdlg").close();
 if(d.includes("mdl")){try{const r=await mdlPost(mdlOut());if(r.err)mdlMsg="!"+r.err;else{mdl=r;mdlSrv=JSON.stringify(r);mdlRmk={};mdlMsg="saved · new runs use it"}}catch(x){mdlMsg="!save failed"}mdlRender()}
 if(d.includes("lim"))await limPost(true);
 if(d.includes("slk"))await slPost(true);
 setBar()};
$("#sdisc").onclick=()=>{if(mdl){mdl=JSON.parse(mdlSrv);mdlRmk={};mdlMsg="";mdlRender()}if(limS){limPend=null;limMsg="";limRender()}if(slS){slDraft=slClone(slS);slMsg="";slRender()}setBar()};
/* accordions: each of the five answers its own header and leaves the others alone; the open set is stored like the pins */
const ACC=["stats","mdl","lim","slk"];   // only the cards the page carries: a name with no element used to kill the whole script (#1032)
let openAcc=new Set(Array.isArray(ls.acc)?ls.acc.filter(i=>ACC.includes(i)):[]);   // the old single-id string is ignored, not trusted, and not fatal
const saveAcc=()=>{ls.acc=[...openAcc];save()};
ACC.forEach(i=>{const e=$("#"+i);if(!e)return;e.open=openAcc.has(i);e.addEventListener("toggle",()=>{if(e.open)openAcc.add(i);else openAcc.delete(i);saveAcc();if(i==="mdl"&&e.open){mdlLoad()}else if(i==="lim"&&e.open){limLoad()}else if(i==="slk"&&e.open){slLoad()}})});mdlLoad();
if(openAcc.has("lim"))limLoad();
if(openAcc.has("slk"))slLoad();
const PCOL=["#5aa9ff","#3ecf8e","#f5b041","#b79cf6","#2dd4bf","#ff6b6b","#c678dd","#e5c07b","#8a93a3"],tk=n=>n>=1e9?(n/1e9).toFixed(2)+"B":n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e3?(n/1e3).toFixed(0)+"K":""+n;
const TOP=3;
function pie(ms){
 // One slice per model NAME, never per spelling: one model answers under several (deepseek-flash, deepseek-flash[1m],
 // claude-deepseek-flash), and keying the rows on the raw id drew the same label three times (#1750).
 const byName=new Map();
 for(const m of ms){const v=m.month_tokens;if(!(v>0))continue;
  const k=m.box&&m.box.registered===false?m.model+" · unregistered":(m.box&&m.box.llm)||m.model;
  const r=byName.get(k)||{n:k,v:0,ids:[],routes:[]};
  r.v+=v;
  if(!r.ids.includes(m.model))r.ids.push(m.model);
  for(const rt of (m.box&&m.box.routes)||[])if(!r.routes.includes(rt))r.routes.push(rt);
  byName.set(k,r)}
 const rows=[...byName.values()].sort((a,b)=>b.v-a.v);if(!rows.length)return"<span class=m>no tokens this month</span>";
 const tot=rows.reduce((a,m)=>a+m.v,0),keep=rows.slice(0,TOP),fold=rows.slice(TOP);
 const sl=keep.map(m=>({k:m.n,n:m.n,v:m.v,t:`${m.n} (${m.ids.join(", ")})${m.routes.length?" via "+m.routes.join(", "):""}: ${tk(m.v)} tokens`}));
 if(fold.length){const v=fold.reduce((a,m)=>a+m.v,0);sl.push({k:"other",n:`other · ${fold.length} models`,v,t:fold.map(m=>`${m.n}: ${tk(m.v)} tokens`).join("\n")})}
 const col=k=>k==="other"?"#8a93a3":PCOL[[...String(k)].reduce((h,c)=>(h*31+c.charCodeAt(0))>>>0,0)%PCOL.length];   // a model keeps its colour across renders: the colour follows its name, not its place in the list
 const dot=(k,n,v)=>`<i style="background:${col(k)}"></i><span class=ln>${esc(n)}</span><span class=usd>${tk(v)}</span>`;   // the rows are built outside the page template: a nested backtick there breaks the pie matcher in tests
 const subRows=fold.map(m=>`<div>${dot(m.n,m.n,m.v)}</div>`).join("");   // built outside the legend template: a nested backtick there breaks the pie matcher in tests
 const legendRows=sl.map(x=>x.k==="other"
   ?`<details class=lgo data-k="other"><summary tabindex=0 title="${esc(x.t)}">${dot(x.k,x.n,x.v)}</summary><div class=lgx>${subRows}</div></details>`
   :`<div tabindex=0 data-k="${esc(x.k)}" title="${esc(x.t)}">${dot(x.k,x.n,x.v)}</div>`).join("");
 let a0=-Math.PI/2;const R=52,C=60,arc=sl.map((x,i)=>{const a1=a0+x.v/tot*2*Math.PI,big=a1-a0>Math.PI?1:0,p=sl.length===1?`<circle cx=${C} cy=${C} r=${R} data-k="${esc(x.k)}" fill="${col(x.k)}"><title>${esc(x.t)}</title></circle>`:`<path d="M${C} ${C}L${C+R*Math.cos(a0)} ${C+R*Math.sin(a0)}A${R} ${R} 0 ${big} 1 ${C+R*Math.cos(a1)} ${C+R*Math.sin(a1)}Z" data-k="${esc(x.k)}" fill="${col(x.k)}" stroke="#161a21" stroke-width=1><title>${esc(x.t)}</title></path>`;a0=a1;return p}).join("");
 return `<div class=pie><svg viewBox="0 0 120 120" width=112 height=112>${arc}<circle cx=${C} cy=${C} r=26 fill="#161a21"/><text x=${C} y=${C-1} text-anchor=middle fill="#d8dde6" style="font-size:var(--fs-min)" font-weight=600>${tk(tot)}</text><text x=${C} y=${C+12} text-anchor=middle fill="#8a93a3" style="font-size:var(--fs-min)">tokens · month</text></svg><div class=lg>${legendRows}</div></div>`}
function hlPie(k){document.querySelectorAll("#pie [data-k]").forEach(el=>el.classList.toggle("hl",!!k&&el.dataset.k===k));document.querySelector("#pie .pie")?.classList.toggle("dim",!!k)}
$("#pie").addEventListener("mouseover",e=>{const el=e.target.closest("[data-k]");hlPie(el?el.dataset.k:"")});
$("#pie").addEventListener("mouseout",e=>{if(!$("#pie").contains(e.relatedTarget))hlPie("")});
$("#pie").addEventListener("focusin",e=>{const el=e.target.closest("[data-k]");hlPie(el?el.dataset.k:"")});
$("#pie").addEventListener("focusout",()=>hlPie(""));
const ago=t=>{const m=Math.round((Date.now()/1000-t)/60);return m<2?"just now":m<90?m+" min ago":Math.round(m/60)+" h ago"},rs=t=>t?new Date(t*1000).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"?";
const rel=t=>{if(!t)return"";const x=t-Date.now()/1000;if(x<=0)return"resets now";const d=Math.floor(x/86400),h=Math.floor(x%86400/3600),m=Math.floor(x%3600/60);return"resets in "+(d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`)};
const qcol=p=>`hsl(${Math.round(120*(1-Math.min(Math.max(+p||0,0),100)/100))} 65% 42%)`;   // a quota bar fills green when empty and red when full; only the fill takes colour (owner)
const lvl=p=>p>=90?"lv-err":p>=60?"lv-warn":"lv-ok",fpct=p=>p<10&&p>0?p.toFixed(1):String(Math.round(p));
const qbar=(label,pct,resets,sm,tip)=>`<div class="qw${sm?" sm":""}"><div class=ql><span>${esc(label)} <b>${fpct(pct)}%</b></span><span class=rst>${esc(rstText(resets,Date.now()/1000))}${resets?" · "+esc(rs(resets)):""}</span></div><div class=tr title="${esc(tip)}"><div class="fl ${lvl(pct)}" style="width:${Math.min(pct,100)}%;background:${qcol(pct)}"></div></div></div>`;
const autoq=a=>{const QT="Quota used, read from the provider's own CLI. 100% = quota exhausted.";if(a.unavailable)return `<div class=fr><span class="chip err">unavailable</span><span class=fs title="${esc(a.err)}">${esc(a.err)}</span>${a.asof?`<span class=fs>· last probed ${ago(a.asof)}</span>`:""}</div>`;return a.windows.map((w,i)=>qbar(w.label,w.pct,w.resets,i>0,QT)).join("")+`<div class=fr>${a.reached?`<span class="chip err">limit reached</span>`:""}<span class=fs>CLI · ${ago(a.asof)}</span></div>`};
const CUR={"R$":"BRL","U$":"USD","US$":"USD","$":"USD","€":"EUR","£":"GBP","¥":"JPY"},curOf=v=>{v=v.trim().toUpperCase();return CUR[v]||v};
const money=(a,c)=>{try{return new Intl.NumberFormat(undefined,{style:"currency",currency:c}).format(a)}catch(e){return a+" "+c}};
async function pollSubs(){try{const d=await (await fetch("/api/subs")).json(),ae=document.activeElement;if(ae&&ae.closest&&ae.closest("#subs")&&ae.tagName==="INPUT")return;
 const tot=Object.values(d.subs).reduce((a,v)=>a+(v.saved||0),0),any=Object.values(d.subs).some(v=>v.saved!=null);{const lims=Object.values(d.subs).filter(v=>{const aq=v.quota,aw=aq&&aq.windows&&aq.windows[0],pl=v.plan,q=aw?aw.pct:(pl&&pl.quota!=null?pl.quota:null);return q!=null&&q>=100});
 $("#subsr").textContent=lims.length?`${lims.length} at limit`:(any||d.api?"none at limit":"set plan prices")}   // the card says what needs a look; the prices stay in the cost card
 $("#cash").textContent="at API list prices (API-equivalent) · cash this month: "+(any||d.api?`plans ${usd(d.plans)} + API ${usd(d.api)} = ${usd(d.total)}`:"set plan prices");
 $("#subs").innerHTML=`<div class=subx>Per subscription: quota used, as each provider's CLI reports it. With no reading, the bar is the API-equivalent usage vs the plan price.</div><datalist id=curs>${["USD","BRL","EUR","GBP","ARS","MXN","CAD","JPY"].map(c=>`<option value=${c}>`).join("")}</datalist>`+Object.entries(d.subs).map(([k,v])=>{const ok=v.saved!=null,pl=v.plan,pr=d.presets[k],custom=pl&&pl.plan==="custom";
  const sel=`<select data-plan=${k} data-f=plan aria-label="${k} plan"><option value="" ${pl?"":"selected"}>choose a plan…</option>${Object.entries(pr).map(([i,p])=>`<option value=${i} ${pl&&pl.plan===i?"selected":""}>${esc(p.name)} · US$${p.usd}</option>`).join("")}<option value=custom ${custom?"selected":""}>Custom amount…</option></select>`;
  const cust=custom?`<span class=pr><span>amount</span><input data-plan=${k} data-f=amount type=number min=0 step=0.01 value="${pl.amount}" aria-label="${k} custom amount"> <input data-plan=${k} data-f=cur list=curs value="${esc(pl.currency)}" placeholder="BRL" aria-label="${k} currency" title="R$, U$, € or any 3-letter code"></span>`:"";
  const note=v.noRate?`<span class=warn>no exchange rate for ${esc(pl.currency)} right now</span>`:(pl&&pl.currency!=="USD"&&v.usd!=null?`<span class=dt>≈ <b>US$${v.usd}</b> / month · ${(+v.rate).toFixed(2)} ${esc(pl.currency)} per US$</span>`:"");
  const est=pl&&v.usd?v.eq/v.usd*100:null,aq=v.quota,aw=aq&&aq.windows&&aq.windows[0],q=aw?aw.pct:(pl&&pl.quota!=null?pl.quota:null),win=aw?aw.label:(pl&&pl.qwin||""),pct=q!=null?q:est,today=d.frac*100,cls=q!=null?"warnq":(ok?(v.saved>=0?"good":"bad"):"");
  const MT="Fill: API-equivalent used by squad runs ÷ monthly plan price (partial: other uses of the plan are not seen). 100% = plan paid off. Tick: how much of the month has passed.";
  const bar=aw||(aq&&aq.unavailable)?autoq(aq):q!=null?qbar(win||"quota used",q,null,false,"Quota % entered by hand. 100% = quota exhausted."):pct!=null?`<div class=qw><div class=ql><span>used <b>${fpct(pct)}%</b> of plan value</span><span class=rst>month ${Math.round(today)}%${pct>=100?" · paid off ✓":""}</span></div><div class=tr title="${MT}"><div class="fl ${ok?(v.saved>=0?"lv-ok":"lv-err"):"lv-est"}" style="width:${Math.min(pct,100)}%"></div><div class=tk style="left:${Math.min(today,100)}%"></div></div></div>`:"";
  const man=aw||!pl?"":`<div class=pr><span>quota used</span><input data-plan=${k} data-f=quota type=number min=0 max=100 step=1 value="${q??""}" placeholder="%" aria-label="${k} quota used" style="width:60px"><input data-plan=${k} data-f=qwin value="${esc(win)}" placeholder="window, e.g. weekly · all models" aria-label="${k} quota window"maxlength=40 style="flex:1;min-width:0"></div>`;
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
  $("#stsr").textContent=usd(st.totals.today)+" today";
  $("#chart").innerHTML=bars(st.buckets);$("#pie").innerHTML=pie(st.models||[]);
  const same=st.totals.all===st.totals.month;
  $("#tot").innerHTML=`<div>today<b>${usd(st.totals.today)}</b></div><div>this month<b>${usd(st.totals.month)}</b></div><div>all time<b${same?" class=m":""}>${same?"= this month":usd(st.totals.all)}</b></div>`;
  $("#totnote").innerHTML=(st.totals.unpriced||st.totals.uncounted)?`floor: ${st.totals.unpriced?st.totals.unpriced+" review call"+(st.totals.unpriced===1?" is":"s are")+" unpriced and count 0.0":""}${st.totals.unpriced&&st.totals.uncounted?"; ":""}${st.totals.uncounted?st.totals.uncounted+" with no usage count":""}. The total is a floor.`:"";
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
loadPoll();setInterval(loadPoll,LOAD_MS);setInterval(instTick,1000);   // the window owns the reading: one timer and one /api/load per tick; the countdown ticks in place between readings, and its node is aria-live=off, so no tick is announced
const bo=ls.bo||{},saveB=()=>{ls.bo=bo;localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,bo})};
if(!["spec","wave","none"].includes(bo.lmode))bo.lmode="spec";   // an older page saved a mode no option names, and the select drew blank (#978)
bo.dock="bottom";   // the board always docks at the bottom
dropRemoved(bo);   // a saved state from a control the board no longer has is dropped (#899)
const sizeBoard=()=>{$("#board").style.height=""};   // the board is one pane of the Board tab: its height is the page's, never a dragged one (#889)
const paintBoard=()=>{document.body.classList.add("dockb");sizeBoard()};
const applyDock=paintBoard;
paintBoard();
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
const tsheet=$("#tk"),when=t=>t.slice(0,16).replace("T"," ");
// The card data the page already holds rides the card (`data-*`); the sheet reads it, and the body and the last comment
// come from `/api/issue` on open. An empty array encodes as `%5B%5D`, so a card with no blockers decodes to `[]`.
const encArr=a=>encodeURIComponent(JSON.stringify(a||[])),decArr=s=>{try{return JSON.parse(decodeURIComponent(s||"%5B%5D"))||[]}catch(e){return []}};
const decQ=v=>{try{return decodeURIComponent(v||"")}catch(e){return v||""}};
const cardOf=el=>el&&el.dataset?{n:+el.dataset.n,r:el.dataset.r||"__REPO__",col:decQ(el.dataset.col),pm:el.dataset.pm==="1",
  b:decArr(decQ(el.dataset.b)),bl:decArr(decQ(el.dataset.bl)),ho:decQ(el.dataset.ho),hr:decQ(el.dataset.hr),prf:decArr(decQ(el.dataset.prf))}:null;
const boardCards=()=>[...document.querySelectorAll("#bbody .it[data-n]")];
let nav=[],cur=0,irepo="__REPO__";   // nav carries [number, repository] pairs: a number alone does not name a card now that two repositories fill the column (#1820)
const ni=()=>nav.findIndex(x=>x[0]===cur&&x[1]===irepo);
function openCard(n,r){const el=boardCards().find(x=>+x.dataset.n===+n&&(x.dataset.r||"__REPO__")===(r||"__REPO__"));if(el)showIssue(n,r,el);else{nav=[[+n,r||"__REPO__"]];showIssue(n,r)}}   // a blocker link opens the same sheet, never a new tab
const step=k=>{const i=ni()+k;if(i>=0&&i<nav.length)showIssue(nav[i][0],nav[i][1])};
// One close rule: the panel goes, the page widens, and the selection the run or the column drew is cleared.
function closeSheet(){tsheet.classList.remove("on");document.body.classList.remove("tkopen");document.querySelectorAll("#bbody .it.hit").forEach(x=>x.classList.remove("hit"));nav=[];cur=0;irepo="__REPO__";$("#tkset").innerHTML="";$("#tknav").innerHTML="";if(hl&&hl.run==null)hl=null;paintHL();escBoard()}
async function showIssue(n,r,el){cur=+n;irepo=r||"__REPO__";hl={n:+n,r:irepo,kind:boardRows.some(x=>x.kind==="pr"&&+x.n===+n&&x.r===irepo)?"pr":"issue"};paintHL();
 // A card a run holds steps through that run's cards; any other card steps through its column (the selection the board shows).
 if(el){const c=cardOf(el),set=c.hr?boardCards().filter(x=>(x.dataset.hr||"")===c.hr):[...el.closest(".sc").querySelectorAll(".it[data-n]")];nav=set.map(x=>[+x.dataset.n,x.dataset.r||"__REPO__"])}
 if(!nav.length)nav=[[cur,irepo]];
 tsheet.classList.add("on");document.body.classList.add("tkopen");$("#tkb").innerHTML="<span class=m>loading #"+n+"…</span>";
try{const d=await (await fetch("/api/issue?n="+n+(r?"&r="+encodeURIComponent(r):""))).json();if(d.err){$("#tkb").textContent=d.err;return}
const c=cardOf(el)||cardOf(boardCards().find(x=>+x.dataset.n===+n&&(x.dataset.r||"__REPO__")===irepo))||{n:+n,r:irepo,col:"",pm:false,b:[],bl:[],ho:"",hr:"",prf:[]};
const wave=(d.labels||[]).map(l=>(String(l).match(/^wave:(\d+)$/)||[])[1]).filter(Boolean)[0];
$("#tkchips").innerHTML=`${(d.labels||[]).includes("spec")?'<span class="chip spec">SPEC</span>':""}${wave?`<span class="chip wave">W${wave}</span>`:""}<span class=m>#${d.n}${d.r&&d.r!=="__REPO__"?" · "+esc(d.r):""}</span>`;
const link=m=>`<a href="#" data-o="${m}">#${m}</a>`,k=ni();
$("#tknav").innerHTML=nav.length>1?`<button id=tkp aria-label="previous card" ${k>0?"":"disabled"}>‹</button><span class=m>${k+1} of ${nav.length}</span><button id=tkn aria-label="next card" ${k<nav.length-1?"":"disabled"}>›</button>`:"";
$("#tkset").innerHTML=nav.length>1?nav.map(([m,rr])=>`<button data-o="${m}" data-r="${rr}" class="${m===cur&&rr===irepo?"on":""}">#${m}</button>`).join(""):"";
$("#tkb").innerHTML=`<h2>${esc(d.title)}</h2>
<div class=row><b>Column</b><span>${esc(c.col||"—")}</span></div>
<div class=row><b>Labels</b><span>${(d.labels||[]).length?d.labels.map(l=>`<span class=lab>${esc(l)}</span>`).join(""):"none"}</span></div>
<div class=row><b>Blocked by</b><span>${c.b.length?c.b.map(link).join(", "):"none"}</span></div>
<div class=row><b>Blocks</b><span>${c.bl.length?c.bl.map(link).join(", "):"none"}</span></div>
<div class=row><b>Runs</b><span>${c.ho?esc(c.ho):"none yet"}</span></div>
<div class=row><b>PR · CI</b><span>${c.prf.length?c.prf.map(x=>`PR #${x.n} · ${esc(x.st||"")}${x.ci?` · CI ${esc(x.ci)}`:""}`).join("<br>"):"none"}</span></div>
<div class=row><b>Opened</b><span>${when(d.created)} by ${esc(d.by)}</span></div>
<div class=sec>Issue</div>${d.html?`<div class="md tkbody">${d.html}</div>`:`<div class="md tkbody">${d.body?md(d.body):"<span class=m>(no description)</span>"}</div>`}
<div class=sec>Last comment</div>${d.last?`<div class="cm md"><div class=meta>${esc(d.last.by)} · ${when(d.last.at)}</div><div class=md>${d.last.html||(d.last.by?"":"")}</div></div>`:"<span class=m>no comments</span>"}`;
$("#tktodo").hidden=!(c.pm&&!d.pr);   // the owner's go signal, only for a card the board marks promotable (ADR-0142)
$("#tkgh").href=/^https:\/\/github\.com\//.test(d.url||"")?d.url:"#";
$("#tkb").scrollTop=0;
const shown=boardCards().find(x=>+x.dataset.n===+n&&(x.dataset.r||"__REPO__")===irepo);if(shown)shown.scrollIntoView({block:"nearest",inline:"nearest",behavior:"smooth"});
}catch(e){$("#tkb").textContent="failed to load"}}
tsheet.addEventListener("click",e=>{if(e.target.id==="tkp")step(-1);else if(e.target.id==="tkn")step(1);const b=e.target.closest("#tkset button");if(b)openCard(+b.dataset.o,b.dataset.r)});
$("#tkb").addEventListener("click",e=>{const a=e.target.closest("a[data-o]");if(a){e.preventDefault();openCard(+a.dataset.o,irepo)}});
$("#tkx").onclick=closeSheet;
$("#tkrel").onclick=()=>{const on=!nav.every(([m,rr])=>boardCards().some(x=>+x.dataset.n===m&&(x.dataset.r||"__REPO__")===rr&&x.classList.contains("hit")));nav.forEach(([m,rr])=>boardCards().forEach(x=>{if(+x.dataset.n===m&&(x.dataset.r||"__REPO__")===rr)x.classList.toggle("hit",on)}))};
$("#tktodo").onclick=()=>{const b=boardCards().find(x=>+x.dataset.n===cur&&(x.dataset.r||"__REPO__")===irepo),p=b&&b.querySelector(".pmp");if(p)p.click()};   // the one write the sheet makes is the card's own move
/*RUN_SHEET*/
// The run sheet (#984): the ticket sheet's shape, for a run. It reads the run row (/api/runs, /api/ended) and the run's record
// through the /dash/ proxy; an ended run with no dashboard server gets one from /ended/ first. Its one write is the pause
// request, the run dashboard's own control route (ADR-0128). The full run dashboard opens in a new tab.
const rsheet=$("#rk");let RK=null;   // RK: {id, row, rec, tab, f, paused}
const rkRow=id=>allRuns.find(r=>r.id===id)||ENDED.find(r=>r.id===id)||null;
const rkLive=r=>!!r&&(r.rank===0||!!r.running);
function rkPaint(force){if(!RK||!RK.row)return;const m=rkModel(RK.row,RK.rec||[]),c=m.chips;   // force: the person acted; a poll paints only what changed
 const ch=$("#rkchips"),chips=`<span class="chip ${c.state==="live"?"live":""}">${c.state==="live"?"&#9679; live":"ended"}</span><span class=chip title="${esc(c.id)}">${esc(c.id)}</span><span class=chip>${esc(c.cost)} · ${esc(c.el)}</span>`;if(ch.innerHTML!==chips)ch.innerHTML=chips;
 rkRender($("#rkb"),RK.rec?rkHtml(m,RK.tab,RK.f):`<h2>${esc(m.label||m.id)}</h2><p class=m>${RK.err?esc(RK.err):"loading the record…"}</p>`,document,force);
 $("#rkgh").href=rkLive(RK.row)&&RK.row.port?"/dash/"+encodeURIComponent(RK.id)+"/":"/ended/"+encodeURIComponent(RK.id);
 const p=$("#rkpause");p.textContent=rkLive(RK.row)?(RK.paused?"Resume run":"Pause run"):"Open its record";
 p.title=RK.note||(rkLive(RK.row)?(RK.paused?"a pause is requested: the run holds at its next node boundary (ADR-0128)":"request a pause: the run holds at its next node boundary (ADR-0128)"):"the full run dashboard, with the record and its replay")}
async function rkLoad(){if(!RK)return;const id=RK.id;
 try{if(!(RK.row&&RK.row.port)&&!RK.opened){RK.opened=true;const o=await fetch("/ended/"+encodeURIComponent(id));if(!o.ok){RK.err="no dashboard server for this run";rkPaint();return}}
  const t=await (await fetch("/dash/"+encodeURIComponent(id)+"/run/record.jsonl")).text();if(!RK||RK.id!==id)return;
  RK.rec=t.split("\n").map(l=>{try{return JSON.parse(l)}catch(e){return null}}).filter(Boolean);   // a torn last line is left out: the valid prefix is read
  if(rkLive(RK.row)){try{const c=await (await fetch("/dash/"+encodeURIComponent(id)+"/run/control.json")).json();RK.paused=!!c.whole}catch(e){}}
 }catch(e){if(RK&&RK.id===id&&!RK.rec)RK.err="the record did not load"}
 if(RK&&RK.id===id)rkPaint()}
function openRun(id){const row=rkRow(id);if(!row)return;if(tsheet.classList.contains("on"))closeSheet();
 RK={id,row,rec:null,tab:"ov",f:{},paused:false,at:Date.now()};rsheet.classList.add("on");document.body.classList.add("tkopen");hl={run:id};paintHL();const b=$("#rkb");b.rkHtml=null;b.scrollTop=0;rkPaint(true);rkLoad()}
function closeRun(){rsheet.classList.remove("on");RK=null;if(!tsheet.classList.contains("on"))document.body.classList.remove("tkopen");if(hl&&hl.run!=null)hl=null;paintHL()}
// The poll keeps the row fresh; a live run's record is reread every few seconds while the sheet is open.
function rkFollow(){if(!RK)return;const r=rkRow(RK.id);if(r)RK.row=r;if(rkLive(RK.row)&&Date.now()-RK.at>4000){RK.at=Date.now();rkLoad()}else rkPaint()}
$("#rkx").onclick=closeRun;
// A control's action (rkAct, rkKey in run-sheet.js): a link opens its ticket, the rest sets the tab and the filters.
function rkApply(a){if(a.o){closeRun();openCard(a.o);return}RK.tab=a.st.tab;RK.f=a.st.f;rkPaint(true)}
$("#rkb").addEventListener("click",e=>{if(!RK)return;const a=rkAct(RK,e.target);if(a){if(a.o||e.target.closest("[data-rkt]"))e.preventDefault();rkApply(a)}});
$("#rkb").addEventListener("change",e=>{const s=e.target.closest("select[data-rkf]");if(s&&RK){RK.f={...RK.f,[s.dataset.rkf]:s.value};rkPaint(true)}});
$("#rkb").addEventListener("keydown",e=>{const a=rkKey(RK||{},e.target,e.key);if(a&&RK){e.preventDefault();rkApply(a)}});   // a node or a model row filters from the keyboard too
$("#rkpause").onclick=async()=>{if(!RK)return;if(!rkLive(RK.row)){window.open($("#rkgh").href,"_blank","noopener");return}
 const id=RK.id,action=RK.paused?"resume":"pause",p=$("#rkpause");p.disabled=true;
 try{const r=await fetch("/dash/"+encodeURIComponent(id)+"/run/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
  if(RK&&RK.id===id){RK.note="";if(r.ok)RK.paused=action==="pause";else RK.note=r.status===409?"the run has ended: there is nothing to pause":"the run refused the request ("+r.status+")"}}
 catch(e){if(RK)RK.note="the run did not answer"}
 p.disabled=false;rkPaint(true)};
addEventListener("keydown",e=>{const w=rkEscape(e.key,rsheet.classList.contains("on"),tsheet.classList.contains("on"));if(w==="run")closeRun();else if(w==="ticket")closeSheet();if(e.key==="Escape")return;
 if(!tsheet.classList.contains("on")||/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName))return;
 if(e.key==="ArrowRight"||e.key==="j")step(1);if(e.key==="ArrowLeft"||e.key==="k")step(-1)});
document.addEventListener("click",e=>{const c=e.target.closest("#bbody .it");if(!c||e.target.closest("a")||e.target.closest("button")||c.classList.contains("m"))return;showIssue(c.dataset.n,c.dataset.r,c)});   // a card opens the sheet in place: the hub is never left
const hm=location.hash.match(/issue=(\d+)/);if(hm)openCard(+hm[1]);   // deep link: /#issue=1171
document.addEventListener("click",e=>{const h=e.target.closest("#bbody .ah");if(!h||e.target.closest("[data-eye]"))return;const g=h.parentElement.dataset.g;if(g===COLNAME.pr_open)bo.prOpen=bo.prOpen===false;else bo.open=g;saveB();pollBoard()});
document.addEventListener("click",e=>{const t=e.target.closest("[data-eye]");if(!t)return;e.preventDefault();e.stopPropagation();const g=t.dataset.eye;
 if(t.closest(".hidline")){const k=new Set(bo.kept||[]);k.add(g);bo.kept=[...k]}   // the hidden: strip's line: show the column again and keep it shown, even while it holds nothing (#1809)
 else{const h=new Set(hidCols()),k=new Set(bo.kept||[]);if(h.has(g))h.delete(g);else{h.add(g);k.delete(g)}bo.hidden=[...h];bo.kept=[...k]}   // the header's eye: the user's own hidden set, and the keep it ends
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
const hidCols=()=>bo.hidden??[];
const RING=["#a3e635","#d8b4fe","#ff9ecd","#7dd3fc"];   // reserved for the columns the user creates, and never one of the named columns
const colOf=(n,names)=>{const u=(names||[]).filter(x=>!Object.hasOwn(COLORS,x)).sort(),i=u.indexOf(n);return COLORS[n]||RING[(i<0?0:i)%RING.length]};
const caseRows=cs=>(cs||[]).map(c=>{const ag=Math.max(0,Math.round((Date.now()-Date.parse(c.ended))/1000)),a=ag<3600?Math.max(1,Math.round(ag/60))+"m":ag<172800?Math.round(ag/3600)+"h":Math.round(ag/86400)+"d";return `<div class="tcrow ${c.result==="fail"?"fail":"pass"}" data-key="test case:${c.issue}@${esc(c.commit)}"><span class=tr>${esc(c.result)}</span><span class=id>#${c.issue}</span><span class=ta>${a}</span><code class=tm>${esc(c.commit)}</code></div>`}).join("");   // one row for each test case item, in the model's order: a strip beside the axis, never a column, and the pane reads no cache of its own
// flow-charts:
let FLOWK=null,FLOWD={};   // each chart's answer from its own route, by name: its numbers and the instant its data was read (#971)
let flowW=800;const flowH=340,flowP=36;   // the width follows the pane, so the chart fills it and its text keeps its size (#889)
const flowX=(i,n)=>n<2?flowP:flowP+i*(flowW-2*flowP)/(n-1);
const flowY=(v,max)=>max?flowH-flowP-v*(flowH-2*flowP)/max:flowH-flowP;
const flowAxis=(max,unit)=>`<line x1=${flowP} y1=${flowH-flowP} x2=${flowW-flowP} y2=${flowH-flowP} stroke=var(--line)></line><text x=${flowP-6} y=${flowY(max,max)+4} text-anchor=end fill=var(--mute) style="font-size:var(--fs-min)">${max} ${unit}</text>`;
const cfdAxis=c=>{const n=c.days.length,base=flowH-flowP;   // every number and date arrives from the hub; the page places a mark and a label for each (#896)
  const ys=c.yticks.map(v=>{const y=flowY(v,c.ymax);return `<line x1=${flowP-4} y1=${y} x2=${flowP} y2=${y} stroke=var(--mute)></line><text x=${flowP-7} y=${y+4} text-anchor=end fill=var(--mute) style="font-size:var(--fs-min)">${v}</text>`}).join("");
  const xs=c.xticks.map(t=>{const x=flowX(t.i,n);return `<line x1=${x} y1=${base} x2=${x} y2=${base+4} stroke=var(--mute)></line><text x=${x} y=${base+17} text-anchor=middle fill=var(--mute) style="font-size:var(--fs-min)">${esc(t.day)}</text>`}).join("");
  return `<line x1=${flowP} y1=${flowP-10} x2=${flowP} y2=${base} stroke=var(--line)></line><line x1=${flowP} y1=${base} x2=${flowW-flowP} y2=${base} stroke=var(--line)></line>${ys}${xs}`+
    `<text x=4 y=${flowP-16} text-anchor=start fill=var(--mute) style="font-size:var(--fs-min)">cards</text>`+
    `<text x=${flowW-flowP} y=${base+31} text-anchor=end fill=var(--mute) style="font-size:var(--fs-min)">day (UTC)</text>`;};
let CF={r:"30"};   // the cumulative flow's range, 30 days at first (#901)
const CFD_R=[["7","7 days"],["30","30 days"],["90","90 days"],["all","All"]];   // the cumulative flow's range views (#901)
const flowCFD=c=>{const sel=`<label class=fhead>Range <select id=cfdr>${CFD_R.map(([k,v])=>`<option value=${k}${CF.r===k?" selected":""}>${v}</option>`).join("")}</select></label>`;
  if(!c.days.length)return sel+"<p class=fnote>the flow history has no sample yet</p>";
  const n=c.days.length,cw=(flowW-2*flowP)/(n<2?1:n);
  const runs=b=>{const out=[];let cur=null;c.days.forEach((_,i)=>{if(b.upper[i]===null){cur=null;return}if(!cur){cur=[];out.push(cur)}cur.push(i)});return out};
  const band=(b,run)=>{const one=run.length===1,h=3;   // a one-point run is a mark 2h wide, never a zero-width line (#894)
    const up=run.flatMap(i=>one?[[flowX(i,n)-h,b.upper[i]],[flowX(i,n)+h,b.upper[i]]]:[[flowX(i,n),b.upper[i]]]);
    const low=[...run].reverse().flatMap(i=>one?[[flowX(i,n)+h,b.lower[i]],[flowX(i,n)-h,b.lower[i]]]:[[flowX(i,n),b.lower[i]]]);
    return [...up,...low].map(([x,v])=>`${x},${flowY(v,c.ymax)}`).join(" ");};
  const polys=b=>runs(b).map(run=>`<polygon points="${band(b,run)}" fill="${COLORS[b.name]||"#888"}" fill-opacity=".85"><title>${esc(b.name)}</title></polygon>`).join("");
  const hover=c.days.map((d,i)=>{const gap=c.bands.length&&c.bands[0].upper[i]===null;   // a day's counts, WIP and throughput, on hover (#901)
    const parts=c.bands.map(b=>`${esc(b.name)} ${(b.count||[])[i]}`).join(", "),tp=c.throughput?c.throughput[i]:null;
    const text=gap?`${esc(d)}: no whole read this day`:`${esc(d)}: ${parts} · WIP ${(c.wip||[])[i]} · throughput ${tp===null?"none":tp}`;
    return `<rect x=${flowX(i,n)-cw/2} y=${flowP-10} width=${cw} height=${flowH-2*flowP+10} fill=transparent><title>${text}</title></rect>`;}).join("");
  return sel+`<svg viewBox="0 0 ${flowW} ${flowH}">${c.bands.map(polys).join("")}${cfdAxis(c)}${hover}</svg>`+
    `<p class=fnote>history from ${esc(c.start)} UTC</p>${c.legacy?`<p class=fnote>${c.legacy} ${c.legacy===1?"day comes":"days come"} from before the kind, spec, wave and type history: they answer none of these</p>`:""}<p>${c.bands.map(b=>`<span class=flg><i style="background:${COLORS[b.name]||"#888"}"></i>${esc(b.name)}</span>`).join("")}</p>`;};
const EXREASON={not_planned:"closed as not planned",abandoned:"closed as abandoned",open:"still open",no_opened:"no opened date"};   // the cards the chart leaves out, in words (#921)
const LTS=ls.lt||{},LT_TYPES=[["spec","Specs"],["task","Tasks"],["bug","Bugs"]];   // the chart's own settings, restored from the page's storage (#985)
let LT={tf:["7","14","30","90","180","all"].includes(LTS.tf)?LTS.tf:"30",log:LTS.log===true,ty:LT_TYPES.some(([k])=>k===LTS.ty)?LTS.ty:"task",pick:null,zc:null,zr:null,rng:null,tok:0,noclick:false,a:"",b:"",cols:[]};   // the control chart's timeframe, its scale, its type, the cluster listed under it, and a drag zoom: the hub's chart over zr=[from,to] (#900, #985)
const saveLT=()=>{ls.lt={tf:LT.tf,log:LT.log,ty:LT.ty};save()};
const ltSpan=(r,a,b)=>{const t0=Date.parse(r.start),t1=Date.parse(r.end),at=f=>new Date(t0+f*(t1-t0)).toISOString();return [at(a<0?0:a),at(b>1?1:b)]};   // plot fractions to the dates they name (#900)
const LT_TF=[["7","Past week"],["14","Past 2 weeks"],["30","Past month"],["90","Past 3 months"],["180","Past 6 months"],["all","All"]];
const ltX=x=>flowP+x*(flowW-2*flowP),ltY=y=>flowH-flowP-y*(flowH-2*flowP);
const ltLine=(v,col,dash,name)=>v?`<line x1=${flowP} x2=${flowW-flowP} y1=${ltY(v.y)} y2=${ltY(v.y)} stroke="${col}" stroke-width=2${dash?` stroke-dasharray="${dash}"`:""}><title>${esc(name)} ${esc(v.text)}</title></line>`:"";
const ltChart=c=>{const n=c.dots.length,base=flowH-flowP,ink="#b8c0cf",mute="var(--mute)";
  const ctl=`<p class=fhead><label>Timeframe <select id=ltr>${LT_TF.map(([k,v])=>`<option value=${k}${LT.tf===k?" selected":""}>${v}</option>`).join("")}</select></label> `+
    `<label>Scale <select id=lts><option value=jira${LT.log?"":" selected"}>Jira scale</option><option value=log${LT.log?" selected":""}>Log scale</option></select></label> `+
    `<label>Type <select id=lty>${LT_TYPES.map(([k,v])=>`<option value=${k}${LT.ty===k?" selected":""}>${v}</option>`).join("")}</select></label> `+
    `<label>Start <select id=lta><option value="">Created</option>${LT.cols.map(k=>`<option${LT.a===k?" selected":""}>${esc(k)}</option>`).join("")}</select></label> `+
    `<label>End <select id=ltb>${LT.cols.map(k=>{const v=k===COLNAME.issue_closed?"":k;return `<option value="${esc(v)}"${LT.b===v?" selected":""}>${esc(k)}</option>`}).join("")}</select></label> `+   // the span each dot measures, from the recorded transitions (#905)
    `<button id=ltz${LT.zr?"":" disabled"} title="drag across the plot to zoom to those dates">Reset zoom</button></p>`;
  LT.rng=c.range;
  const note=`<p class=fnote>${esc(c.text)}</p>`;
  if(!n)return ctl+note+"<p class=fnote>no closed card in this timeframe</p>";
  const up=c.dots.map(d=>`${ltX(d.x)},${ltY(d.rolling.y_high)}`),low=[...c.dots].reverse().map(d=>`${ltX(d.x)},${ltY(d.rolling.y_low)}`);
  const band=`<polygon points="${[...up,...low].join(" ")}" fill="var(--s3)" fill-opacity=".16"><title>Standard deviation</title></polygon>`;
  const roll=`<polyline fill=none stroke="var(--s3)" stroke-width=2 points="${c.dots.map(d=>`${ltX(d.x)},${ltY(d.rolling.y)}`).join(" ")}"><title>Rolling average (${c.window.size} issues, centered)</title></polyline>`;
  const ys=c.ticks.map(t=>`<line x1=${flowP-4} y1=${ltY(t.y)} x2=${flowP} y2=${ltY(t.y)} stroke=${mute}></line><text x=${flowP-7} y=${ltY(t.y)+4} text-anchor=end fill=${ink} style="font-size:var(--fs-min)">${esc(t.text)}</text>`).join("");
  const xs=c.xticks.map(t=>`<text x=${ltX(t.x)} y=${base+17} text-anchor=middle fill=${ink} style="font-size:var(--fs-min)">${esc(t.text)}</text>`).join("");
  const axes=`<line x1=${flowP} y1=${flowP-10} x2=${flowP} y2=${base} stroke=var(--line)></line><line x1=${flowP} y1=${base} x2=${flowW-flowP} y2=${base} stroke=var(--line)></line>`+
    `<text x=${flowP-7} y=${flowP-16} text-anchor=end fill=${ink} style="font-size:var(--fs-min)">elapsed time${c.axis==="log"?" (log)":c.axis==="cube"?" (cube root)":""}</text>`;
  const lines=ltLine(c.average,"var(--s6)","","Average")+ltLine(c.median,"var(--ok)","2 3","Median")+ltLine(c.p85,"var(--s5)","5 3","p85");
  const dots=c.clusters.map((k,i)=>{const m=k.dots.length,title=m>1?`${m} issues`:`#${k.dots[0].n} ${esc(k.dots[0].title)}`;
    return `<circle data-k=${i} cx=${ltX(k.x)} cy=${ltY(k.y)} r=${m>1?6:4.5} fill="${m>1?"var(--bg)":"var(--s3)"}" stroke="var(--s3)" stroke-width=2 style="cursor:pointer"><title>${esc(title)} · ${esc(k.dots[0].text)} · closed ${esc(k.dots[0].closed)}</title></circle>`+(m>1?`<text x=${ltX(k.x)} y=${ltY(k.y)+4} fill="var(--s3)" style="font-size:var(--fs-min)" text-anchor=middle pointer-events=none>${m}</text>`:"")}).join("");
  const S=c.average?`<p class=fhead>Average <b>${esc(c.average.text)}</b> · Median <b>${esc(c.median.text)}</b> · p85 <b>${esc(c.p85.text)}</b></p>`:"";
  const legend=`<p class=flg><span><i style="background:var(--s3)"></i>Rolling average (${c.window.size} issues, centered)</span><span><i style="background:var(--s3);opacity:.3"></i>Standard deviation</span><span><i style="background:var(--s6)"></i>Average</span><span><i style="background:var(--ok)"></i>Median</span><span><i style="background:var(--s5)"></i>p85</span></p>`;
  const pk=LT.pick!=null&&c.clusters[LT.pick]?c.clusters[LT.pick]:null;
  const rows=pk?`<table class=rt><thead><tr><th>Issue</th><th>Title</th><th>Elapsed</th><th>Closed</th></tr></thead><tbody>${pk.dots.map(d=>`<tr data-o=${d.n} style="cursor:pointer"><td>#${d.n}</td><td>${esc(d.title)}</td><td>${esc(d.text)}</td><td>${esc(d.closed)}</td></tr>`).join("")}</tbody></table>`:"";
  return ctl+S+legend+`<svg data-lt viewBox="0 0 ${flowW} ${flowH}">${axes}${ys}${xs}${band}${roll}${lines}${dots}</svg>`+note+rows;};
const flowLead=l=>{const S=l.summary,ex=l.excluded.map(e=>`${e.count} ${esc(EXREASON[e.reason]||e.reason)}`).join(", ");
  const head=`<p class=fhead>${esc(LT_TYPES.find(([k])=>k===l.type)[1].toLowerCase())} · <b>${esc(l.measure)}</b> · window ${l.window.size} ${esc(l.window.unit)} · excluded ${l.excluded_total}${ex?" ("+ex+")":""}</p>`;
  LT.cols=l.columns||[];if(!S)return head+"<p class=fnote>no closed card yet</p>";
  return head+ltChart(LT.zc||l.control[LT.log?"log":"jira"][LT.tf]);};
const drawFlow=()=>{{const w=$("#flowb").clientWidth-20;flowW=w<600?600:w>1600?1600:(w|0)}const cfd=FLOWK==="cfd";
  const a=FLOWD[FLOWK];
  $("#flowb").innerHTML=!a?"<p class=fnote>loading the chart from the hub</p>":(cfd?flowCFD(a.cfd.ranges[CF.r]):flowLead(a.lead))+`<p class=fnote>data read ${new Date(a.t*1000).toLocaleTimeString()}</p>`};
const flowOpen=async k=>{FLOWK=k;$("#flowov").hidden=false;drawFlow();const q=barQuery();try{const a=await (await fetch(`/api/chart/${k}?${q}`)).json();if(q!==barQuery())return;FLOWD[k]=a;if(FLOWK===k)drawFlow()}catch(e){}};
const ltQ=()=>`&scale=${LT.log?"log":"jira"}&a=${encodeURIComponent(LT.a)}&b=${encodeURIComponent(LT.b)}&${barQuery()}`,ltOwn=()=>LT.a!==""||LT.b!=="";   // a span other than created to closed is the hub's chart for it (#905); the bar's values filter it (#904)
const ltZoom=async(f,t)=>{const tok=++LT.tok;try{const c=await (await fetch(`/api/leadtime?from=${encodeURIComponent(f)}&to=${encodeURIComponent(t)}${ltQ()}`)).json();if(tok!==LT.tok||c.error)return;LT.zc=c;LT.zr=[f,t];LT.pick=null;drawFlow()}catch(e){}};   // the hub recomputes the chart for the dragged range (#900)
const ltGet=async()=>{const tok=++LT.tok;LT.zc=null;LT.pick=null;if(!ltOwn()){drawFlow();return}try{const c=await (await fetch(`/api/leadtime?tf=${LT.tf}${ltQ()}`)).json();if(tok!==LT.tok||c.error)return;LT.zc=c;drawFlow()}catch(e){}};   // the timeframe's chart for the chosen span (#905)
const ltPx=(e,s)=>{const r=s.getBoundingClientRect();return (e.clientX-r.left)/r.width*flowW};
const ltClamp=x=>x<flowP?flowP:x>flowW-flowP?flowW-flowP:x,ltLo=(p,q)=>p<q?p:q,ltHi=(p,q)=>p<q?q:p;
let LTD=null;   // the drag across a control chart's plot in progress (#900)
$("#flowb").onpointerdown=e=>{const s=e.target.closest("svg[data-lt]");LT.noclick=false;if(!s||e.button!==0||!LT.rng)return;const x=ltClamp(ltPx(e,s));LTD={s,x0:x,x1:x,moved:false}};
document.addEventListener("pointermove",e=>{if(!LTD)return;const x1=ltClamp(ltPx(e,LTD.s));LTD.x1=x1;const dx=x1-LTD.x0;if(!LTD.moved&&dx*dx<36)return;LTD.moved=true;
  let sel=LTD.s.querySelector("rect.ltsel");if(!sel){sel=document.createElementNS("http://www.w3.org/2000/svg","rect");sel.setAttribute("class","ltsel");sel.setAttribute("y",flowP-10);sel.setAttribute("height",flowH-2*flowP+10);sel.setAttribute("fill","var(--s3)");sel.setAttribute("fill-opacity",".18");sel.setAttribute("pointer-events","none");LTD.s.appendChild(sel)}
  sel.setAttribute("x",ltLo(LTD.x0,x1));sel.setAttribute("width",dx<0?-dx:dx)});
document.addEventListener("pointerup",()=>{const d=LTD;LTD=null;if(!d||!d.moved)return;d.s.querySelector("rect.ltsel")?.remove();LT.noclick=true;
  const a=(ltLo(d.x0,d.x1)-flowP)/(flowW-2*flowP),b=(ltHi(d.x0,d.x1)-flowP)/(flowW-2*flowP);if(b-a<0.005||!LT.rng)return;const [f,t]=ltSpan(LT.rng,a,b);ltZoom(f,t)});
$("#flowb").onchange=e=>{if(e.target.id==="cfdr"){CF.r=e.target.value;drawFlow();return}
  if(e.target.id==="ltr"){LT.tf=e.target.value;saveLT();LT.zr=null;ltGet();return}
  if(e.target.id==="lty"){LT.ty=e.target.value;saveLT();barMoved();return}
  if(e.target.id==="lta"||e.target.id==="ltb"){LT[e.target.id==="lta"?"a":"b"]=e.target.value;LT.pick=null;if(LT.zr){ltZoom(LT.zr[0],LT.zr[1]);return}ltGet();return}
  if(e.target.id==="lts"){LT.log=e.target.value==="log";saveLT();LT.pick=null;if(LT.zr){ltZoom(LT.zr[0],LT.zr[1]);return}if(ltOwn()){ltGet();return}drawFlow()}};
$("#flowb").onclick=e=>{if(LT.noclick){LT.noclick=false;return}
  if(e.target.id==="ltz"){LT.zr=null;ltGet();return}
  const k=e.target.closest("circle[data-k]");if(k){LT.pick=+k.dataset.k;drawFlow();return}const r=e.target.closest("tr[data-o]");if(r)showIssue(+r.dataset.o,"__REPO__")};
$("#bflow").onclick=()=>flowOpen("cfd");$("#blead").onclick=()=>flowOpen("lead");addEventListener("resize",()=>{if(!$("#flowov").hidden)drawFlow()});
// flow-charts:end
const barQuery=()=>["fw","fs","fk"].map(k=>`${k}=${encodeURIComponent((bo[k]||[]).join(","))}`).join("&")+`&ty=${LT.ty}`;   // the bar's Waves, Specs and Kinds and the chart's Type, read by both diagrams; the board filters on the page (#904, #985)
let boardTag=null,boardBody=null;   // the board the page holds and its ETag: a 304 keeps it (#973)
async function pollBoard(){lim.busy=1;try{const r=await fetch("/api/board",boardTag?{headers:{"If-None-Match":boardTag}}:{});let b;if(r.status===304&&boardBody)b=boardBody;else{b=await r.json();if(b.groups){boardTag=r.headers.get("ETag");boardBody=b}}const FLOW=/*BOARD_FLOW*/,   // development flow, left to right
all=[b.prs,...b.groups.sort((x,y)=>{const i=n=>{const k=FLOW.indexOf(n);return k<0?FLOW.length:k};return i(x.name)-i(y.name)})],f=q.trim().replace(/^#/,"");
if(bo.open===undefined||!all.some(g=>g.name===bo.open))bo.open=(all.find(g=>g.n&&!g.end&&!isPR(g.name))||all[2]).name;
const CN=all.map(g=>g.name);   // every column of this board: what colOf reads the user columns out of, never a colour of its own
lastRead=Date.now();readStale=!!b.err;$("#bcases").innerHTML=caseRows(b.cases);$("#bt").textContent=(b.err?"(stale) ":"")+["status","structure","prs"].map(k=>{const p=(b.parts||{})[k];return k+" "+(p&&p.t?Math.round(Date.now()/1000-p.t)+"s":"never")+(p&&p.err?" !":"")}).join(" · ");
const fw=bo.fw||[],fk=bo.fk||[],boardGroups=all.filter(g=>!isPR(g.name)),allRows=boardGroups.filter(g=>!g.end).flatMap(g=>g.items),specTitle=new Map();   // the filter bar: the waves, specs and kinds the open cards hold (#899, #904, #969)
allRows.forEach(x=>{if(x.sp)specTitle.set(String(x.n),x.t)});
const wOpts=waveOptions(allRows),sOpts=specOptions(boardGroups,b.sum),SLOTS=specSlots(sOpts.filter(s=>s!=="none"));   // a finished spec is no option (#981); one color per spec option on the board (#917)
const fs=keptSpecs(bo.fs||[],boardGroups,b.sum);
if(!b.err&&fs.length!==(bo.fs||[]).length){bo.fs=fs;saveB()}   // a saved key whose spec finished is dropped (#981)
const hidKept=keptHidden(bo.hidden||[],all);
if(!b.err&&hidKept.length!==(bo.hidden||[]).length){bo.hidden=hidKept;saveB()}   // a saved hidden set drops a closed status on load (#969)
const fillBar=(id,opts,label,sel,name,col)=>{const box=$("#"+id),k=opts.join("|");
 if(box.dataset.k!==k){box.dataset.k=k;box.innerHTML=`<div class=fpopb><button type=button data-clear>clear</button></div>`+opts.map(v=>`<label><input type=checkbox value="${esc(v)}"> ${col?`<i class=sw style="background:${col(v)}"></i>`:""}${esc(label(v))}</label>`).join("")}
 box.querySelectorAll("input").forEach(c=>c.checked=sel.includes(c.value));
 box.parentElement.querySelector("summary").textContent=sel.length?`${name} (${sel.length})`:`${name}: all`;box.parentElement.classList.toggle("set",sel.length>0)};
fillBar("fwl",wOpts.map(o=>o.key),k=>wOpts.find(o=>o.key===k).label,fw,"Waves");
fillBar("fsl",sOpts,s=>s==="none"?"No spec":"#"+s+(specTitle.get(s)?" "+specTitle.get(s):""),fs,"Specs",s=>specColor(SLOTS,s==="none"?null:s));   // the spec filter is the legend (#917)
fillBar("fkl",KIND_OPTIONS.map(o=>o[0]),k=>KIND_OPTIONS.find(o=>o[0]===k)[1],fk,"Kinds");
const hid=new Set(hidCols()),bottom=true,eye=(g,off=hid.has(g.name))=>`<span class=eye data-eye="${esc(g.name)}" title="${off?"show":"hide"} this column"><svg viewBox="0 0 24 24" width=14 height=14 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx=12 cy=12 r=3 />${off?'<path d="M3 3l18 18"/>':""}</svg></span>`;
const LC=b.lc||{},rgb=h=>[0,2,4].map(i=>parseInt(h.slice(i,i+2),16)),mix=(a,t)=>a.map((v,i)=>Math.round(v*t+[28,34,44][i]*(1-t))),   // label color blended into the card's #1c222c
tint=x=>{const cs=(x.l||[]).map(l=>LC[l]).filter(c=>/^[0-9a-f]{6}$/.test(c||"")).map(rgb);if(!cs.length)return"";const avg=[0,1,2].map(i=>cs.reduce((s,c)=>s+c[i],0)/cs.length);return`;--bg:rgb(${mix(avg,.10)});--bgh:rgb(${mix(avg,.16)})`},
lbc=l=>/^[0-9a-f]{6}$/.test(LC[l]||"")?` style="--lb:rgba(${rgb(LC[l])},.30)"`:"";
const age=o=>{let m=Math.max(0,Math.floor((Date.now()-Date.parse(o))/60000));const d=Math.floor(m/1440),h=Math.floor(m%1440/60);m%=60;return "⏱ "+(d?d+"d "+h+"h":h?h+"h "+String(m).padStart(2,"0")+"m":m+"m")};
const ageC=o=>{const h=(Date.now()-Date.parse(o))/3600000;return h>=24?"old":h>=4?"warm":""};
setInterval(()=>document.querySelectorAll("#bbody .age").forEach(e=>{e.textContent=age(e.dataset.o);e.className="age "+ageC(e.dataset.o)}),30000);
const gcls=c=>c.v==="?"?"unk":c.v==="debt"?"debt":c.v.startsWith("OK")?"ok":(c.v.startsWith("CHG")||c.v.startsWith("BLK"))?"bad":"";
const gstrip=s=>{const age=Math.round(Date.now()/1000-s.at),old=age>s.max;return `<div class="gstrip${old?" old":""}" title="${esc(old?`these facts are ${age} s old: the strip greys out past 180 s, so nothing here is being claimed as fresh`:"R review · C CI · T threads · B base · H labels and holds")}">`+s.cells.map(c=>`<span class="gc ${gcls(c)}" title="${esc(c.tip||"")}">${c.k}<b>${esc(c.v)}</b></span>`).join("")+`</div>`};
const cards=(g,it,shown)=>shown.map(x=>{const pr=x.kind==="pr";return `<div class=it data-n="${x.n}" data-r="${esc(x.r||"__REPO__")}"${pr?" data-pr":""} data-col="${encodeURIComponent(g.name==null?"":String(g.name))}" data-pm="${x.pm&&!pr?1:""}" data-b="${encodeURIComponent(JSON.stringify(x.b||[]))}" data-bl="${encodeURIComponent(JSON.stringify(x.bl||[]))}" data-ho="${encodeURIComponent(x.ho==null?"":String(x.ho))}" data-hr="${encodeURIComponent(x.hr==null?"":String(x.hr))}" data-prf="${encodeURIComponent(JSON.stringify(x.prf||[]))}" style="--cc:${colOf(g.name,CN)};--lc:${specColor(SLOTS,cardSpec(x))}${tint(x)}"><a class=id target=_blank href="https://github.com/${esc(x.r||"__REPO__")}/${pr?"pull":"issues"}/${x.n}">${pr?"PR ":""}#${x.n}</a>${x.kind!=="pr"&&x.pa!=null&&!x.sp?`<span class=lchip data-k=spec title="spec #${x.pa}">${x.pa}</span>`:""}${waveChip(x)}${(x.l||[]).includes("loop-bug")?'<span class="bugc loop">loop-bug</span>':(x.l||[]).includes("bug")?'<span class="bugc">bug</span>':""}${x.rt?`<span class=rpo title="the repository this card belongs to: ${esc(x.r)}">${esc(x.rt)}</span>`:""}${x.pb?`<span class="pbg ${x.pb.replace(" ","-")}">${esc(x.pb)}</span>`:""}${x.rollup?`<span class="cir ${esc(x.rollup)}" title="CI states on the head${(x.ci||[]).length?": "+esc((x.ci||[]).join(", ")):""}">CI ${esc(x.rollup)}</span>`:""}${x.fz?`<span class=fz title="a freeze is a marker, never a move: the card keeps its column">${esc(x.fz)}</span>`:""}${x.pd?`<span class=pnd title="pending: the board door proved this move and the board read has not carried it yet">pending</span>`:""}${x.ps?`<span class="pst ${x.ps[0]}"${x.ps[2]?` title="${esc(x.ps[2])}"`:""}>${esc(x.ps[1])}</span>`:""}${x.c?`<span class=dt>${x.c.slice(5,16).replace("T"," ")}</span>`:""}<span class=tt>${esc(String(x.t).replace(/^\s*\[SPEC(?:\s*#?\d+)?\]\s*/i,""))}</span>${x.pm&&!pr?`<button class=pmp data-n="${x.n}" title="move this card to ${esc(x.pmt)}: the owner's go signal (ADR-0142)">→ ${esc(x.pmt)}</button>`:""}${x.ps&&x.ps[3]?`<div class=prr>${esc(x.ps[3])}</div>`:""}${x.gs?gstrip(x.gs):""}${(x.l||[]).length?`<span class=lbs>${x.l.map(l=>`<span class="${(bo.labels||[]).includes(l)?"on":""}"${lbc(l)}>${esc(l)}</span>`).join("")}</span>`:""}${(x.b&&x.b.length)?`<div class=blk>blocked by ${x.b.map(n=>`<a target=_blank rel=noopener href="https://github.com/__REPO__/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${x.ho?`<span class=hld title="${esc(x.ho)}">running</span>`:""}${(x.cl||[]).length?`<div class=clos title="the issues this pull request closes">closes ${x.cl.map(n=>`<a target=_blank rel=noopener href="https://github.com/${esc(x.r||"__REPO__")}/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${(x.o||x.sc||x.by)?`<div class=ft>${x.sc?`<span class=tsk title="sub-issues closed / total: ${esc(x.sc)}">${esc(x.sc)}</span>`:""}${x.o?`<span class="age ${ageC(x.o)}" data-o="${x.o}" title="open since ${x.o.slice(0,16).replace("T"," ")} UTC">${age(x.o)}</span>`:""}${x.by?`<span class=au title="opened by ${esc(x.by)}">${esc(x.by)}</span>`:""}</div>`:""}</div>`}).join("")+(it.length>shown.length?`<div class="it m">${it.length-shown.length} more · scroll</div>`:"");
// A visible column holding no cards is not drawn at all: the columns that do have cards take the width, and the
// collapsed one is accounted for by a line at the foot of the hidden: strip. Which columns those are is derived on
// every render -- from the filtered count, the user's hidden set and the set the line's eye added -- and never
// stored, so a card appearing brings the column back on its own and the last one leaving collapses it again (#1809).
const laneHtml=m=>{const ccount=c=>m.lanes.reduce((s,l)=>s+(l.counts[c]||0),0),pct=l=>l.total?Math.round(l.closed/l.total*100):0;
 if(!m.lanes.length)return `<div class=lempty>${f?"No card matches this search.":"No card on the board."}</div>`;
 let h=`<div class=lgrid style="--vc:${Math.max(1,m.vc.length)}">`+m.vc.map(c=>`<div class=lcolh style="--cc:${colOf(c,CN)}"><span>${esc(c)}</span><b>${ccount(c)}</b>${eye({name:c})}</div>`).join("");
 for(const l of m.lanes){const cl=l.collapsed,bugs=l.kind==="bugs",lc=bugs?"var(--bad)":l.num!=null?specColor(SLOTS,l.num):SPEC_GREY,   // a spec lane and a wave lane of a spec take its card colour (#917, #980)
   lid=l.kind==="s"?"SPEC "+l.num:bugs?"Loop bugs":l.title,ltt=l.kind==="s"?l.title:bugs?"defects of the loop itself: they halt dispatch, never a landing (ADR-0146)":l.kind==="none"?"bugs and single tickets":"";   // the lane reads as the mock: a coloured id, then its title or its subtitle (#889)
  h+=`<section class="lane${cl?" coll":""}${l.kind==="bugs"?" alarm":""}${l.done?" done":""}" data-lane="${esc(l.id)}" style="--lc:${lc}"><header class=lh role=button tabindex=0 aria-expanded="${!cl}" data-lane="${esc(l.id)}"><span class=tw>${cl?"&#9656;":"&#9662;"}</span>`
    +`<span class="lid${l.kind==="sw"?" sw":""}">${l.kind==="sw"?`<span class=sid>${esc(l.sid)}</span> · ${esc(l.wl)}`:esc(lid)}</span>`+(ltt?`<span class="ltt${l.kind==="s"?"":" subt"}">${esc(ltt)}</span>`:"")
    +(l.kind==="s"||l.kind==="none"?`<span class=bar title="${l.closed} of ${l.total} closed"><i style="width:${pct(l)}%"></i></span><span class=pc>${l.closed}/${l.total}</span>`+(l.st?`<span class=lst>${esc(l.st)}</span>`:"")+(l.kind==="s"?`<a class=ghl target=_blank rel=noopener title="open SPEC ${l.num} on GitHub" aria-label="open SPEC ${l.num} on GitHub" href="https://github.com/${esc(l.r||"__REPO__")}/issues/${l.num}"><svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg></a>`:""):"")
    +`<span class=sum>${cl?m.cols.filter(c=>l.counts[c]).map(c=>`<span>${esc(c)} <b>${l.counts[c]}</b></span>`).join(""):""}</span></header>`;
  if(!cl)for(const c of m.vc){const key=l.id+"|"+c,v=cellView(l.cells[c],!!(bo.cx||{})[key]);h+=`<div class=cell data-lane="${esc(l.id)}" data-col="${esc(c)}">${cards({name:c},v.shown,v.shown)}${v.more?`<button class=more data-cell="${esc(key)}" title="show every card of this cell">+${v.more} more</button>`:""}</div>`}
  h+="</section>"}
 return h+"</div>"};
const perr=g=>g.err?`<div class=perr title="a repository the hub was told to read and could not: the rows this column still shows for it are its last good ones">&#9888; ${esc(g.err)}</div>`:"";   // a repository the hub cannot read is an error on the column, never an empty column (#1820)
const fits={};all.forEach(g=>fits[g.name]=g.items.filter(x=>(!f||String(x.n).includes(f)||x.t.toLowerCase().includes(f.toLowerCase()))&&(isPR(g.name)?boardOk({...x,kind:"pr"},[],[],fk):boardOk(x,fw,fs,fk))));   // the Specs filter shows, only shows, or hides the spec-labelled items, never a column of its own (#247, #456)
const kept=new Set(bo.kept||[]),
 autoHidden=g=>!hid.has(g.name)&&!kept.has(g.name)&&!fits[g.name].length&&!g.tracking,   // the columns the strip's lines stand for, and the only ones: a column the user hid is a chip, one the user kept is drawn, and the lane is always drawn -- empty, it says 0 (#440)
 vis=all.filter(g=>!g.end&&(!bottom||(!hid.has(g.name)&&!autoHidden(g)))),autos=bottom?all.filter(g=>!g.end&&autoHidden(g)):[];   // a closed status is never a column, so it has no eye and no line to show it (#969)
const badge=g=>`<span class=n>${f||fw.length||fs.length||fk.length?fits[g.name].length+"/":""}${g.n}</span>`;   // the number the header shows, and the number the strip's line repeats
const one=g=>{const it=fits[g.name],shown=it.slice(0,lim[g.name]||25),dot=`<i style="background:${colOf(g.name,CN)}"></i>`,cnt=badge(g),isOpen=g.name===COLNAME.pr_open?bo.prOpen!==false:bo.open===g.name;
if(bottom)return `<div class="col${isPR(g.name)?" prs lane":""}" style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><div class=ch><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}</span></div><div class=sc data-g="${esc(g.name)}">${perr(g)}${cards(g,it,shown)}</div></div>`;
return `<div class="acc${isPR(g.name)?" prs lane":""} ${hid.has(g.name)?"hidcol":""} ${isOpen?"open":""}" style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><div class=ah><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}</span></div><div class=sc data-g="${esc(g.name)}">${perr(g)}${cards(g,it,shown)}</div><div class=pad></div></div>`};
const fmtN=n=>n>=1000?n.toLocaleString("en-US"):""+n;   // "688" is one token; only 1000 and over needs separators
// One tile of the hidden: strip: the name as a caption over the count, the count's colour deciding what the count
// means. data-col names the column, so the colour is a CSS rule per category rather than an inline value; the one
// exception is a column #1793's map does not name, which takes the ring colour and can only come from colOf here.
const hidchip=g=>{const c=colOf(g.name,CN),ring=!Object.hasOwn(COLORS,g.name);return `<span class=chip data-eye="${esc(g.name)}" data-col="${esc(g.name)}" title="${esc(g.name+" — "+fmtN(g.n))}"><span class=lbl>${esc(g.name)}</span><b${ring?` style="color:${c}"`:""}>${fmtN(g.n)}</b></span>`};
const endLines=all.filter(g=>g.end).map(g=>`<span class=hidline><span>${esc(g.name)}</span><b>${fmtN(g.n)}</b></span>`).join("");   // a closed status is a text total: no eye, no show action (#969)
const html=vis.map(one).join("")+(bottom&&(hid.size||autos.length||endLines)?`<div class=hidbar>${all.filter(g=>!g.end&&hid.has(g.name)).map(hidchip).join("")}${hid.size&&autos.length?'<div class=hidsep></div>':""}${autos.map(g=>`<span class=hidline style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><span>${esc(g.name)}</span><b>${badge(g)}</b>${eye(g,true)}</span>`).join("")}${endLines}</div>`:"");   // one quiet line per column hidden only because it is empty, behind a divider (#1809)
const model=bo.lmode==="none"?null:buildBoard(all.filter(g=>!isPR(g.name)),{mode:bo.lmode||"spec",fw:bo.fw||[],fs:bo.fs||[],fk:bo.fk||[],closed:COLNAME.issue_closed,sum:b.sum,hidden:hidCols(),lc:bo.lc||{},hideDone:!!bo.hideDone,search:!!f,text:x=>!f||String(x.n).includes(f)||x.t.toLowerCase().includes(f.toLowerCase())});   // the lanes read every row; the search and the filters only hide rows (#890)
const lanesHtml=model?laneHtml(model):null;lanesNow=model;$("#lhd").checked=!!bo.hideDone;
$("#lmode").value=bo.lmode||"spec";$("#bbody").classList.toggle("lanes",!!model);
$("#lhc").innerHTML=model&&model.hc.length?`<button class=hcpill id=lhcx title="columns with no card in an expanded lane: click to expand the lanes that hold their cards">+${model.hc.length} hidden: ${model.hc.map(x=>x.user?`<span class=hcu data-eye="${esc(x.name)}" title="show this column">${esc(x.name)} <b>${x.count}</b></span>`:esc(x.name)+" <b>"+x.count+"</b>").join(" · ")}</button>`:"";
if(lanesHtml!==null){if($("#bbody").dataset.h!==lanesHtml){$("#bbody").dataset.h=lanesHtml;$("#bbody").innerHTML=lanesHtml;restore()}}
else if($("#bbody").dataset.h!==html){$("#bbody").dataset.h=html;$("#bbody").innerHTML=html;restore()}   // same cards: leave the DOM alone, so a scroll in progress is never interrupted
boardRows=all.flatMap(g=>g.items);paintHL();
}catch(e){}finally{lim.busy=0}}
// highlight:begin
// The highlight follows the selection and is never stored: `hl` is {run: id} for a run, or {n, r, kind} for a card. Its
// matches come from the rows the page already holds, so the rule adds no read.
const hlKind=x=>x.kind==="pr"?"pr":"issue",hlRepo=x=>x.r||HUB_REPO,hlKey=x=>hlRepo(x)+"#"+hlKind(x)+"#"+x.n;
const hlRunNames=(run,x)=>!!run&&hlRepo(x)===HUB_REPO&&(hlKind(x)==="pr"?run.fix!=null&&+x.n===+run.fix:(run.issues||[]).some(i=>+i===+x.n));
const hlCards=(run,items)=>run?items.filter(x=>hlRunNames(run,x)):[];
const hlRuns=(card,runList)=>card?runList.filter(r=>hlRunNames(r,card)):[];
const hlHidden=(matched,rendered)=>matched.filter(x=>!rendered.has(hlKey(x)));
function paintHL(){
  if(hl&&hl.run!=null&&!runs.some(r=>r.id===hl.run))hl=null;
  const run=hl&&hl.run!=null?runs.find(r=>r.id===hl.run):null,card=hl&&hl.run==null?hl:null;
  const matched=hlCards(run,boardRows),mk=new Set(matched.map(hlKey)),rendered=new Set();
  document.querySelectorAll("#bbody .it[data-n]").forEach(el=>{const k=el.dataset.r+"#"+(el.hasAttribute("data-pr")?"pr":"issue")+"#"+el.dataset.n;
    rendered.add(k);   // a card the board draws is in #bbody; a collapsed swimlane and a filter both leave it out
    el.classList.toggle("hl",mk.has(k));el.classList.toggle("dim",!!run&&!mk.has(k))});
  const hidden=hlHidden(matched,rendered),hb=$("#hlrel");hb.hidden=!hidden.length;hb.textContent=hidden.length+" related";
  document.querySelectorAll("#bbody .lane[data-lane]").forEach(e=>{const l=(lanesNow&&lanesNow.lanes||[]).find(x=>x.id===e.dataset.lane),n=l&&run?hlHidden(hlCards(run,l.all),rendered).length:0;
    let s=e.querySelector(".lhrel");if(!n){s&&s.remove();return}
    if(!s){s=document.createElement("span");s.className="hlrel lhrel";e.querySelector(".lh").append(s)}
    s.textContent=n+" related"});
  const hits=new Set(hlRuns(card,runs).map(r=>r.id));
  document.querySelectorAll("#list .r[data-id]").forEach(e=>e.classList.toggle("hlr",hits.has(e.dataset.id)));
}
// highlight:end

// The frame of the mock (#889): four tabs over one page. A tab only shows one pane; every pane keeps its own poll.
let TAB="board",lastRead=0,readStale=false;
const TABS={board:"#tab-board",flow:"#tab-flow",set:"#tab-set"};
function setTab(t,o){if(!TABS[t])t="board";TAB=t;ls.tab=t;save();
 for(const k in TABS)$(TABS[k]).classList.toggle("on",k===t);
 document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on",b.dataset.t===t));
 if(t==="set")setTheme(sTheme);   // entering the tab shows one theme, the last one picked (#913)
 if(t==="flow"){if(o&&o.runs)$("#flowov").hidden=true;else if(!o||!o.keep){if($("#flowov").hidden)flowOpen("cfd")}}
 drawMain()}
document.querySelector(".tabs").addEventListener("click",e=>{const b=e.target.closest("button[data-t]");if(b)setTab(b.dataset.t)});
$("#bruns").onclick=()=>{$("#flowov").hidden=true;flowTabs()};
function flowTabs(){const h=$("#flowov").hidden,k=typeof FLOWK==="string"?FLOWK:"cfd";$("#bruns").classList.toggle("on",h);$("#bflow").classList.toggle("on",!h&&k==="cfd");$("#blead").classList.toggle("on",!h&&k!=="cfd")}
$("#main").addEventListener("click",e=>{const r=e.target.closest(".r[data-rid]");if(r)openRun(r.dataset.rid)});   // a run row on the Runs tab opens the sheet (#984)
$("#main").addEventListener("keydown",e=>{const r=e.target.closest(".r[data-rid]");if(r&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openRun(r.dataset.rid)}});
new MutationObserver(flowTabs).observe($("#flowov"),{attributes:true});
{const pn=(location.pathname.match(/^\/([^/?#]+)/)||[])[1]||"";$("#pjn").textContent=pn?decodeURIComponent(pn):"hub";
 const pj=$("#pj"),psw=$("#psw");pj.onclick=e=>{e.stopPropagation();const on=!psw.classList.contains("on");psw.classList.toggle("on",on);pj.setAttribute("aria-expanded",on)};
 document.addEventListener("click",e=>{if(!e.target.closest("#psw")&&psw.classList.contains("on")){psw.classList.remove("on");pj.setAttribute("aria-expanded","false")}})}
// "board read N s ago": the stamp of the last board answer
setInterval(()=>{const b=$("#bread");if(!b)return;b.textContent=lastRead?`board read ${Math.max(0,Math.round((Date.now()-lastRead)/1000))} s ago${readStale?" (stale)":""}`:""},1000);
// loop-switch:start
// The loop switch (#923, ADR-0162): /api/loop is the one read and the one write. The strip and Settings > Loop draw one view;
// a start posts at once, a stop opens the confirm, which drains by default and lists every live run and gate a stop-now kills.
// The renderers are pure: tests/test_hub_loop_switch_page.py runs them in node.
let loopV=null,loopErr="";
const loopWhen=s=>{const m=String(s||"").match(/^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)/);return m?m[1]+" "+m[2]:String(s||"")};
const loopBy=v=>v.who?`${v.who} · ${loopWhen(v.when)}`:"";
const loopBtn=v=>({on:"Stop…",draining:"Stop now…",starting:"Starting…"})[v.state]||"Start loop";
const loopTip=v=>!v.write?v.reason:v.state==="on"?"stop after the live runs and gates finish, or now":v.state==="draining"?"stop now: kills what is still live":v.state==="starting"?"the loop is starting":"start the loop (watchd)";
const loopB=v=>`<button data-loop${v.write&&v.state!=="starting"?"":" disabled"} title="${esc(loopTip(v))}">${loopBtn(v)}</button>`;
const loopAct=e=>e.failed?`${String(e.action||"")} failed: ${e.failed}`:e.action==="drain"?"stop after the live runs and gates finish":e.action==="stop-now"?`stop now${e.killed?` (killed ${(e.killed.runs||[]).length} runs, ${(e.killed.gates||[]).length} gates${e.survived?`; ${(e.survived.runs||[]).length+(e.survived.gates||[]).length} not ended`:""})`:""}`:String(e.action||"");
const loopRun=r=>String(r).split("/").filter(Boolean).pop()||String(r);
const loopCardHtml=v=>`<div class=irw id=loopst title="${esc(v.state+(v.who?` · changed by ${v.who} at ${loopWhen(v.when)}`:""))}"><span>Loop: <span class="ls ${esc(v.state)}">${esc(v.state)}</span>${loopBy(v)?` <small>${esc(loopBy(v))}</small>`:""}</span>${loopB(v).replace("<button ","<button id=loopbtn ")}</div>`;
const loopSetHtml=v=>`<div class=lnow><span><span class="ls ${esc(v.state)}">Loop: ${esc(v.state)}</span><small>${v.who?`changed by ${esc(v.who)} at ${esc(loopWhen(v.when))}`:"no change from the hub yet"}${v.state==="draining"?` · ${v.live.runs.length+v.live.gates.length} live to finish`:""}</small></span>${loopB(v)}</div>`
 +(v.write?"":`<div class=lwhy>${esc(v.reason)}. The rule is in Access.</div>`)+(v.failed?`<div class=lerr role=alert>The last start failed: ${esc(v.failed)}</div>`:"")+(loopErr?`<div class=lerr role=alert>${esc(loopErr)}</div>`:"")
 +`<p class=m>A stop drains by default: no new dispatch, the live runs and gates finish, then the loop stops. Each change writes one line to the change log.</p>`
 +`<h4>Changes</h4><ul class=laud>${v.log.length?v.log.map(e=>`<li><b>${esc(loopWhen(e.at))}</b> · ${esc(e.who)} (${esc(e.from)}) · ${esc(loopAct(e))}</li>`).join(""):"<li class=m>no change yet</li>"}</ul>`;
const loopConfirmHtml=v=>{const r=v.live.runs,g=v.live.gates,drain=v.state==="on";
 return `<b>Stop the loop?</b><p class=m>Live now: ${r.length} run${r.length===1?"":"s"}, ${g.length} gate${g.length===1?"":"s"}.</p>`
 +(drain?`<label class=lrad><input type=radio name=lm value=drain checked><span><b>Stop after the live runs and gates finish</b><small>No new dispatch. The live work runs to its end, then the loop stops. The safe default.</small></span></label>`:"")
 +`<label class=lrad><input type=radio name=lm value=stop-now${drain?"":" checked"}><span><b>Stop now</b><small>Kills these at once; their work in progress is lost:</small><ul>${r.map(x=>`<li>run ${esc(loopRun(x))}</li>`).join("")}${g.map(x=>`<li>${esc(x)}</li>`).join("")}${r.length||g.length?"":"<li>no live run or gate</li>"}</ul></span></label>`
 +`<div class=lact><button value=ok id=lok>Confirm</button><button value=no id=lno>Back</button></div>`};
const accHtml=v=>{const a=v.access||{},yn=x=>x?"<span class=ok>allowed</span>":"<span class=no>refused</span>";
 return `<div class=kr><span class=l>This page is reached</span><b>${v.tailnet?"over the tailnet, through the hub":"from this machine (loopback)"}</b></div>`
 +`<div class=kr><span class=l>Loop start and stop from the tailnet</span><b>${yn(a.loop_write)} <small>SQUAD_TAILNET_LOOP_WRITE (tailnet.loop_write)</small></b></div>`
 +`<div class=kr><span class=l>Terminal send from the tailnet</span><b>${yn(a.terminals_send)} <small>SQUAD_TAILNET_TERMINALS_SEND (tailnet.terminals_send)</small></b></div>`
 +`<div class=kr><span class=l>Run dashboards over the tailnet</span><b>read only, through the hub</b></div>`
 +`<div class=kr><span class=l>Phone check</span><b><code>/agent-squad:start-mobile</code></b></div>`
 +`<p>Read here; the owner sets them in the project file or <code>.env</code> on the machine (ADR-0152, ADR-0162). A write the tailnet refuses still works from this machine.</p>`};
// loop-switch:end
function instLoopDraw(){const e=$("#iloop");if(!e||!loopV)return;const h=loopCardHtml(loopV);if(e.dataset.h!==h){e.dataset.h=h;e.innerHTML=h}}
function loopDraw(){if(!loopV)return;const b=$("#lopb"),h=loopSetHtml(loopV);if(b.dataset.h!==h){b.dataset.h=h;b.innerHTML=h}
 const c=$("#accb"),ah=accHtml(loopV);if(c.dataset.h!==ah){c.dataset.h=ah;c.innerHTML=ah}
 $("#lopsr").textContent=`${loopV.state}${loopV.who?` · ${loopV.who} · ${loopWhen(loopV.when)}`:""}`;instLoopDraw()}
async function loopPoll(){try{loopV=await (await fetch("/api/loop")).json();loopDraw()}catch(e){}}
async function loopPost(body){loopErr="";try{const r=await fetch("/api/loop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),d=await r.json();if(!r.ok)loopErr=d.reason||d.err||"refused"}catch(e){loopErr="the hub did not answer"}
 await loopPoll();loopDraw()}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-loop]");if(!b||b.disabled||!loopV)return;e.preventDefault();
 if(loopV.state==="off"){loopPost({action:"start"});return}
 const d=$("#ldg");$("#ldgb").innerHTML=loopConfirmHtml(loopV);d.showModal()});
$("#ldg").addEventListener("close",()=>{const d=$("#ldg");if(d.returnValue!=="ok")return;const m=(d.querySelector('input[name="lm"]:checked')||{}).value;
 if(m==="drain")loopPost({action:"drain"});else if(m==="stop-now")loopPost({action:"stop-now",confirm:true})});
setInterval(loopPoll,5000);loopPoll();
/*BOARD_LANES*/
$("#lmode").onchange=e=>{bo.lmode=e.target.value;saveB();pollBoard()};
// The swimlanes' state (#891): collapse and open state live in `bo`, so the device keeps them across a reload.
// A lane opens and closes with an eased height (Web Animations on its cells; the lane itself is display:contents). Closing
// runs first and the board redraws after it; opening shows a loading mark in the lane until the read redraws it, then grows.
const LANE_MS=matchMedia("(prefers-reduced-motion: reduce)").matches?0:220;
const laneCells=id=>[...document.querySelectorAll(`#bbody .cell[data-lane="${CSS.escape(id)}"]`)];
const laneGrow=(id,dir)=>Promise.all(laneCells(id).map(c=>{const hh=c.scrollHeight;c.style.overflow="hidden";
 const a=c.animate(dir>0?[{height:"0px",opacity:0},{height:hh+"px",opacity:1}]:[{height:hh+"px",opacity:1},{height:"0px",opacity:0}],{duration:LANE_MS,easing:"ease-in-out",fill:dir>0?"none":"forwards"});
 return a.finished.then(()=>{if(dir>0)c.style.overflow=""}).catch(()=>{})}));
async function laneToggle(id,open){
 if(!open){await laneGrow(id,-1);bo.lc[id]=true;saveB();await pollBoard();return}
 const s=document.querySelector(`#bbody .lane[data-lane="${CSS.escape(id)}"]`);
 if(s&&!s.querySelector(".lload"))s.querySelector(".lh").insertAdjacentHTML("afterend",`<div class=lload role=status aria-live=polite><i></i><span>loading…</span></div>`);
 bo.lc[id]=false;saveB();await pollBoard();laneGrow(id,1)}
let lanesNow=null;   // the model the board last drew: the controls act on its lanes and its hidden columns
$("#lhd").onchange=e=>{bo.hideDone=e.target.checked;saveB();pollBoard()};
const laneSet=v=>{bo.lc=bo.lc||{};for(const l of(lanesNow?lanesNow.lanes:[]))bo.lc[l.id]=v;saveB();pollBoard()};
$("#lcall").onclick=()=>laneSet(true);$("#lexp").onclick=()=>laneSet(false);
document.addEventListener("click",e=>{if(e.target.closest(".ghl"))return;const h=e.target.closest("#bbody .lh");if(h){const id=h.dataset.lane,coll=h.closest(".lane").classList.contains("coll");bo.lc=bo.lc||{};laneToggle(id,coll);return}
 const m=e.target.closest("#bbody .more");if(m){bo.cx=bo.cx||{};bo.cx[m.dataset.cell]=true;saveB();pollBoard();return}   // a more click opens its own cell only
 const p=e.target.closest("#lhcx");if(p&&lanesNow){const names=new Set(lanesNow.hc.map(x=>x.name));bo.lc=bo.lc||{};   // a hidden column's pill expands the lanes that hold its cards
  for(const l of lanesNow.lanes)if(l.collapsed&&Object.keys(l.counts).some(c=>names.has(c)))bo.lc[l.id]=false;saveB();pollBoard()}});
const barMoved=()=>{LT.tok++;LT.zc=null;LT.zr=null;FLOWD={};if(!$("#flowov").hidden&&FLOWK){flowOpen(FLOWK);if(FLOWK==="lead"&&ltOwn())ltGet()}};   // a bar value changes both charts: each reads its route anew, and a chosen span its own chart (#904, #905)
const pickFilter=(id,key)=>{const box=$("#"+id);box.addEventListener("change",e=>{const v=e.target.value,h=new Set(bo[key]||[]);e.target.checked?h.add(v):h.delete(v);bo[key]=[...h];barMoved();saveB();pollBoard()});box.addEventListener("click",e=>{if(!e.target.closest("[data-clear]"))return;e.preventDefault();bo[key]=[];barMoved();saveB();pollBoard()})};
pickFilter("fwl","fw");pickFilter("fsl","fs");pickFilter("fkl","fk");
// The owner's one move (#560): the arrow a Backlog card carries delegates to the hub, and the card shows the reason a
// refusal gives. Which cards carry the arrow is the row's own fact (`x.pm`), never the page's rule.
document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .pmp");if(!b)return;e.preventDefault();e.stopPropagation();const n=+b.dataset.n,card=b.closest(".it");b.disabled=true;
try{const r=await fetch("/api/promote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue:n})}),d=await r.json();
 if(d.err){card.insertAdjacentHTML("beforeend",`<div class=perr>${esc(d.err)}</div>`);b.disabled=false}
 else{b.textContent="moved to Todo ✓";pollBoard()}}catch(x){card.insertAdjacentHTML("beforeend","<div class=perr>the hub did not answer</div>");b.disabled=false}});
document.addEventListener("click",e=>{for(const d of["#fw","#fs"])if($(d)&&!e.target.closest(d))$(d).open=false});
pollBoard();setInterval(pollBoard,5000);
// Last ended: the runs that ended today, read from the hub (#889)
async function endedPoll(){try{const rows=await (await fetch("/api/ended")).json();ENDED=rows;drawMain();rows.slice(0,5).map(r=>`<div class=r data-rid="${esc(r.id)}" tabindex=0 role=button><div><span class="b ${r.state}">${esc(r.badge||r.state)}</span> <b>${esc(name(r))}</b><div class=m>${usd(r.spend)} \u00b7 ${age(r.age)} ago</div></div></div>`).join("")||"<div class=m>no run ended today</div>"}catch(e){}}
endedPoll();setInterval(endedPoll,30000);
setTab(location.hash==="#set"?"set":TABS[ls.tab]?ls.tab:"board");   // the phone menu links here with #set (#913)
let ver=null;setInterval(async()=>{try{const v=(await (await fetch("/api/version")).json()).v;if(ver===null)ver=v;else if(v!==ver)location.reload()}catch(e){}},3000);
</script>"""
PAGE = PAGE.replace("/*WEB_TOKENS*/", WEB_TOKENS) + "\n<!--OVERLAY-->"
SWITCHER_JS = (pathlib.Path(__file__).resolve().parent / "hub-switcher.js").read_text()   # the project switcher, both pages (#876)
PAGE = PAGE.replace("/*SWITCHER_JS*/", SWITCHER_JS)
BOARD_FILTER_JS = (pathlib.Path(__file__).resolve().parent / "board-filter.js").read_text()
PAGE = PAGE.replace("/*BOARD_LANES*/", (pathlib.Path(__file__).resolve().parent / "board-lanes.js").read_text())   # the swimlane rule (#890)
PAGE = PAGE.replace("/*BOARD_LANES_CSS*/", (pathlib.Path(__file__).resolve().parent / "board-lanes.css").read_text())   # its look (#890, #891)
PAGE = PAGE.replace("/*RUN_SHEET*/", (pathlib.Path(__file__).resolve().parent / "run-sheet.js").read_text())   # the run sheet's model (#984)
PAGE = PAGE.replace("/*RUN_SHEET_CSS*/", (pathlib.Path(__file__).resolve().parent / "run-sheet.css").read_text())   # its look (#984)
# The strip paints one rule per category, keyed by the fact that sets the column: the name comes from the table.
HID_CHIP_TOKENS = (("abandoned", "err"), ("dev_ci", "run"), ("unscheduled", "warn"), ("issue_closed", "ok"), ("pr_open", "pin"))
PAGE = PAGE.replace("/*HID_CHIP_RULES*/", "\n".join('.hidbar .chip[data-col="%s"] b{color:var(--%s)}' % (COLUMN[fact], token) for fact, token in HID_CHIP_TOKENS))
PAGE = PAGE.replace("/*BOARD_COLORS*/", BOARD_COLORS_JSON).replace("/*BOARD_FLOW*/", BOARD_FLOW_JSON).replace("/*BOARD_NAMES*/", BOARD_NAMES_JSON).replace("/*HUB_REPO*/", json.dumps(REPO))
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
    tmp = _temp_name(GROK_KEPT)
    try: tmp.write_text(json.dumps(k)); os.replace(tmp, GROK_KEPT)
    except OSError: tmp.unlink(missing_ok=True)
    return k
def grok_plan():
    """The plan the grok CLI itself caches; it is also what proves the settings file is being read at all."""
    try: p = json.loads(json.loads(GROK_SETTINGS.read_text())["payload"])
    except Exception: return None
    return ((p.get("settings") or {}).get("subscription_tier_display")) or None
def q_grok():
    """Read the newest log line carrying a BillingConfig: `currentPeriod{start,end}`, and `creditUsagePercent`
    when the CLI writes it. The grok CLI pushes that payload in the line's `ctx.config`, and its message wording
    drifts between versions (`billing: fetched credits config` in older ones), so match the fields and never the
    text. A line whose `currentPeriod` carries an `end` is a reading; the 1.0.62 CLI omits `creditUsagePercent`
    when the period's usage is 0%, so a missing field on such a line reads as 0%, never as no reading.
    Finding none is a reading of its own — `unavailable` with the reason — so refresh_quota cannot keep showing
    yesterday's number. The newest reading is kept in GROK_KEPT and read back after the log rotates; `until` is
    its period end (quota_row)."""
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
                    if isinstance(c.get("currentPeriod"), dict) and c["currentPeriod"].get("end"): last = (d, c)
    except OSError: pass
    k = None
    if last:
        d, c = last; per = c.get("currentPeriod") or {}
        end = dt.datetime.fromisoformat(per["end"].replace("Z", "+00:00")).timestamp() if per.get("end") else None
        pct = c.get("creditUsagePercent")
        k = grok_keep({"pct": 0.0 if pct is None else pct, "end": end,
                       "asof": dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()})
    else:
        try: k = json.loads(GROK_KEPT.read_text())
        except Exception: k = None
    if not isinstance(k, dict) or k.get("pct") is None:
        return {"windows": [], "unavailable": True, "err": grok_no_source(first), "plan": grok_plan()}
    return {"windows": [{"label": "weekly", "pct": k["pct"], "resets": k.get("end")}], "plan": grok_plan(),
            "asof": k.get("asof"), "until": k.get("end")}
NOBROWSE = None
def no_browser_dir():
    """A PATH entry whose `open` does nothing. A quota probe must never raise a login page: agy falls back to an
    interactive OAuth flow when the HOME it runs under carries no credential, and that flow opens a Google account
    page in the operator's browser. The probe runs with this entry first, so the page cannot open, whatever the CLI
    decides about its own auth."""
    global NOBROWSE
    if NOBROWSE is None:
        d = pathlib.Path(tempfile.mkdtemp(prefix="hub-nobrowse-"))
        for name in ("open", "xdg-open"):
            f = d / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(0o755)
        NOBROWSE = d
    return NOBROWSE
def q_agy():
    """`agy -p /quota` prints one line per model group (no model call): name, `Weekly Limit Remaining`, N%, reset ISO. The Gemini group is the Google plan's.

    A probe is not a login: it never opens a browser (see `no_browser_dir`), and a CLI that answers `not
    authenticated` reads as no quota at all."""
    env = {**os.environ, "PATH": f"{no_browser_dir()}:{os.environ.get('PATH', '')}", "BROWSER": "/usr/bin/true"}
    r = subprocess.run(["agy", "-p", "/quota"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, env=env)
    if "not authenticated" in (r.stdout + r.stderr).lower():
        return None
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
CHAINS = ("coding_models", "review_models", "vision_models", "router_models")   # all four live in models.json (SPEC 750)
ROUTER_CHAIN = "router_models"   # the router's own chain: the models the router agent runs on, in rank order (#95)
def usable(ids, chain):
    """The ids one chain keeps: an unregistered id stays, so the panel shows it as unregistered; a registered id stays
    only when the registry says the chain can run it (ADR-0147, #545)."""
    return [m for m in ids if modelreg.model(m) is None or modelreg.compatible(m, chain)[0]]
def models_doc():
    """models.json as the panel reads it: a v2 file's chains under the v1 keys the page draws (v1_view)."""
    return v1_view(json.loads(MODELS.read_text()))
def v1_view(d):
    """The panel's reading of models.json: a v2 file's chains as the resolver reads them, under the v1 keys the panel draws (ADR-0147)."""
    if d.get("schema_version") != 2: return d
    view = {k: v for k, v in d.items() if k not in ("schema_version", "chains")}
    rows = {k: [e for e in modelreg.chain(d, k.removesuffix("_models")).entries if usable([e.raw], k.removesuffix("_models"))] for k in CHAINS}
    view.update({k: [e.raw for e in rows[k]] for k in CHAINS})
    view["off"] = {k: {e.raw: True for e in rows[k] if not e.on} for k in CHAINS}
    view["hours"] = {k: {e.raw: e.hrs for e in rows[k] if e.hrs} for k in CHAINS}
    view["efforts"] = {k: {e.raw: e.effort for e in rows[k] if e.effort} for k in CHAINS}
    return view
READONLY = ("known", "router_known", "suggest", "version", "labels", "boxes", "last_coding", "chain", "models", "outside")   # fields models_get() adds for the panel: posted back, never stored
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
    gateway's catalogue holds. The other CLIs' ids (codex, agy, grok) stay in `known`, for the role chains.

    The gateway's catalogue lists no Anthropic id, and it passes one through, so the box also offers every live
    registry id that matches ANTHROPIC_ID (#687)."""
    reg = modelreg.default_registry()
    anthropic = {m for m, e in reg.entries.items() if ANTHROPIC_ID.match(m) and not e.retired}
    return sorted({m for m in set(gateway_models()) | anthropic if MODEL_ID.match(m) and not provkey.closed(m)})   # no key, never offered (#482)


def model_labels(ids):
    """{id: registry label} for the ids the one registry knows (ADR-0147); an unknown id has no row (#672).

    Display only: the add box matches a typed "4.1" against the label, and the value it saves stays the id."""
    out = {}
    for m in ids:
        e = modelreg.model(m)
        if e is not None and e.label: out[m] = e.label
    return out


def model_suggestions(ids):
    """The add dropdown's options (#687): [{id, text}], the registry label as the text and the id beside it.

    An id no registry entry knows is left out (a gateway-only id such as claude-gpt-5.2): display never guesses a
    name, and a raw id can still be typed. An effort spelling adds its effort word ("Gemini 3.8 Flash high"); two ids
    that still share a text add their route; a last tie adds the id. So every text in one list is unique, and the
    page maps a picked text back to its id."""
    reg = modelreg
    rows = []
    for m in sorted(set(ids)):
        e = reg.model(m)
        if e is None or not e.label: continue
        eff = next((w for w in EFFORTS if m.endswith("-" + w) and reg.model(m[:-len(w) - 1]) is e), None)
        rows.append({"id": m, "text": f"{e.label} {eff}" if eff else e.label})
    for key in (lambda r: f"{r['text']} · {model_box(r['id'])['route']}", lambda r: f"{r['text']} · {r['id']}"):
        counts = {}
        for r in rows: counts[r["text"]] = counts.get(r["text"], 0) + 1
        for r in rows:
            if counts[r["text"]] > 1: r["text"] = key(r)
    return sorted(rows, key=lambda r: (r["text"], r["id"]))


ROUTE_TEXT = {"gateway": "gateway", "direct": "direct API"}   # a cli route reads as its own CLI, named by the registry's argv


def model_box(raw, chain=None):
    """What one hub box that stands for a model shows (#677): the registry label beside the route, or "unregistered".

    The rule lives here, never in the page (ADR-0137). `chain` picks the spelling that chain stores (chain_id), so a
    review row of an Anthropic id reads as the claude CLI it runs through. A cli route is named by its argv's program
    (claude, codex, grok, agy)."""
    reg = modelreg
    spelling = chain_id(chain, raw) if chain else raw
    e, ref = reg.model(spelling), reg.normalize(spelling)
    if e is None or not e.label:
        return {"id": raw, "llm": None, "route": None, "registered": False, "text": "unregistered"}
    argv = ((e.routes.get("cli") or {}).get("argv") or [None]) if ref.route == "cli" else [None]
    route = f"{pathlib.Path(argv[0]).name} CLI" if argv[0] else ROUTE_TEXT.get(ref.route or "", ref.route)
    return {"id": raw, "llm": e.label, "route": route, "registered": True, "text": f"{e.label} · {route}" if route else e.label}


ANTHROPIC_ID = re.compile(r"^claude-(opus|sonnet|haiku|fable)-[0-9][0-9a-z.\-]*$")


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
def model_off_ids(d):
    """The models switched off at model level (SPEC 889 #920): normalized ids from models.json's top-level `model_off`.
    The one registry reads the same key (modelreg.chain), so the hub and every consumer agree on the spelling."""
    mo = d.get("model_off") if isinstance(d.get("model_off"), dict) else {}
    return {modelreg.normalize(m).id for m, v in mo.items() if v is True and isinstance(m, str)}
def model_off(d, chain, mid):
    """True when the model is switched off at model level: off in every chain it sits in (SPEC 889 #920)."""
    return modelreg.normalize(mid).id in model_off_ids(d) and mid in d.get(chain, [])
def chain_off(d, chain, mid):
    """True when a chain row already in the file carries the old chain-level switch (`off`). The hub writes no new one
    (SPEC 889 #920); a file that holds one keeps honoring it, and the panel shows it as "off (chain)"."""
    o = d.get("off") if isinstance(d.get("off"), dict) else {}
    ok = o.get(chain) if isinstance(o.get(chain), dict) else {}
    return bool(ok.get(mid)) and mid in d.get(chain, [])
def off_of(d):
    """Per-chain {model-id: True} from models.json's optional top-level `off`; only entries whose model is in that chain."""
    return {k: {m: True for m in d.get(k, []) if chain_off(d, k, m)} for k in CHAINS}
def may_run(d, chain, mid, when=None):
    """True when the model may run at `when`: a model switched off is skipped exactly like one outside its window.

    One predicate for every reason, because the consumers do not care which one it was -- the panel's row shows which."""
    return not (model_off(d, chain, mid) or chain_off(d, chain, mid)) and in_hours(d, chain, mid, when) and not provkey.closed(mid)   # a provider key that is absent closes it too (#482)
def hours_of(d):
    """Per-chain {model-id: "HH-HH"} from models.json's optional top-level `hours`; only entries whose model is in that chain."""
    h = d.get("hours") if isinstance(d.get("hours"), dict) else {}
    return {k: {m: v for m, v in (h.get(k) or {}).items() if model_hours(d, k, m) == v} for k in CHAINS}
def efforts_of(d):
    """Per-chain {model-id: effort} from models.json's optional top-level `efforts`; every chain present, only entries whose model is in that chain."""
    e = d.get("efforts") if isinstance(d.get("efforts"), dict) else {}
    return {k: {m: v for m, v in (e.get(k) or {}).items() if isinstance(e.get(k), dict) and m in d.get(k, []) and v in EFFORTS and v != "medium"} for k in CHAINS}
def router_get(doc=None):
    """The router's own chain (#95, SPEC 750): the models the router agent runs on, in rank order, read from
    models.json's `chains.router` like the three role chains. The panel shows the entries the chain can run (usable)."""
    d = models_doc() if doc is None else v1_view(doc)
    return [bare_id(m) for m in d.get(ROUTER_CHAIN, [])]


def subscription_states():
    """Each subscription's quota now, read once per page (#545): its marker, its quota cache, or neither."""
    reg = modelreg.default_registry()
    return {k: {"quota": modelreg.quota(k, shared=SHARED), "label": info.get("label", k)} for k, info in reg.subscriptions.items()}
def model_status(raw, states):
    """The subscription that bills one model, and its quota: spent when a marker or a window closes it (#545)."""
    e = modelreg.model(raw)
    if e is None: return {"sub": None, "quota": None}
    q = states[e.subscription]["quota"]
    return {"sub": {"key": e.subscription, "label": states[e.subscription]["label"]},
            "quota": {"spent": bool(q["limited"]), "reason": q["reason"], "until": q["until"],
                      "until_text": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(q["until"])) if q["until"] else None}}
def model_rows(d, states):
    """The models table (SPEC 889 #920): one row per model across the four chains, with its subscription and quota and
    its state. A model switched off at model level reads "off (model)"; a chain row the file still holds off reads
    "off (chain)"; a spent subscription reads "at limit"; otherwise "on". The table holds no hours (#913)."""
    ids = list(dict.fromkeys(bare_id(m) for k in CHAINS for m in d.get(k, [])))
    chain_off_ids = {bare_id(m) for k in CHAINS for m in d.get(k, []) if chain_off(d, k, m)}
    off = model_off_ids(d)
    rows = []
    for b in ids:
        st = model_status(b, states)
        is_off = modelreg.normalize(b).id in off
        spent = bool(st["quota"] and st["quota"]["spent"])
        state = "off (model)" if is_off else "off (chain)" if b in chain_off_ids else "at limit" if spent else "on"
        rows.append({"id": b, "off": is_off, "state": state, **st})
    return rows
def not_switched_off(suggestions, off):
    """The add list never offers a model switched off at model level (SPEC 889 #920): it keeps its place in the chains."""
    return [s for s in suggestions if modelreg.normalize(s["id"]).id not in off]
def models_get():
    """The four chains as the panel reads them: bare ids, so one model is one row in every chain and in the box.

    The file keeps the spelling each role needs (chain_id); the panel never sees it, so a reader cannot pick the
    wrong one of two spellings of the same model."""
    d = models_doc()
    known, rknown, states = known_models(d), router_known(), subscription_states()
    off = model_off_ids(d)
    rows = model_rows(d, states)
    return {**{k: [bare_id(m) for m in d.get(k, [])] for k in CHAINS},
            "efforts": {k: {bare_id(m): v for m, v in ek.items()} for k, ek in efforts_of(d).items()},
            "hours": {k: {bare_id(m): v for m, v in hk.items()} for k, hk in hours_of(d).items()},
            "off": {k: {bare_id(m): True for m in ok} for k, ok in off_of(d).items()},
            "model_off": {r["id"]: True for r in rows if r["off"]},
            "models": rows,
            "outside": {k: {bare_id(m): model_hours(d, k, m) for m in d.get(k, []) if model_hours(d, k, m) and not in_hours(d, k, m)} for k in CHAINS},
            "known": known, "last_coding": last_coding_model(),
            "router_known": rknown, "version": models_version(),
            # The router's add box offers what the gateway serves, never the other CLIs' ids (#225): the router row is a
            # chain like the other three, so its own catalogue answers for it (#687).
            "suggest": {**{k: not_switched_off(model_suggestions(usable(known, k.removesuffix("_models"))), off) for k in CHAINS if k != ROUTER_CHAIN},
                        ROUTER_CHAIN: not_switched_off(model_suggestions(usable(rknown, "router")), off)},
            "labels": model_labels(set(known) | set(rknown) | {bare_id(m) for k in CHAINS for m in d.get(k, [])}),
            "boxes": {**{k: {bare_id(m): {**model_box(bare_id(m), k), **model_status(bare_id(m), states)} for m in d.get(k, [])} for k in CHAINS},
                      "known": {m: model_box(m) for m in set(known) | set(rknown) | ({last_coding_model()} - {None, ""})}},
            "chain": chainhealth.chain_state(d, "review_models", SHARED)}
def models_version():
    """What a Save must have read (#688): a hash of models.json, parsed, so a rewrite that keeps the same data keeps
    the version. An unreadable models.json hashes as its raw bytes. The four chains all live in that one file (SPEC
    750), so no run graph enters the hash: a graph edit no longer makes a panel tab stale."""
    try: m = json.loads(MODELS.read_text())
    except (OSError, ValueError):
        try: m = MODELS.read_bytes().hex()
        except OSError: m = None
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def models_record(changes):
    """One line per model-level flip in the state directory's models switch ledger (SPEC 889 #920): the model, its new
    state, who and when. Whole lines, appended; a Save that flips nothing writes nothing."""
    if not changes: return
    who, at = os.environ.get("SG_OWNER") or "owner", time.strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE / MODELS_LEDGER, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps({"at": at, "model": m, "state": st, "who": who}, ensure_ascii=False) + "\n" for m, st in changes))

_SAVE = threading.Lock()   # one Save at a time, so the version check and the write see the same files


def models_set(new):
    """Replace the four chains in models.json; every other key is kept. Atomic write, so a reader never sees half a file.

    A body whose version is not the current one comes from a page that did not read the last Save (#688): it is
    refused whole, before any write, so an old tab never takes back what a newer Save wrote."""
    with _SAVE:
        if not isinstance(new, dict) or new.get("version") != models_version(): return {"err": "stale: reload the page"}
        return _models_set(new)


def _new_raw(k, mid, reg):
    """The spelling a new row takes in the file: the one the route its chain prefers serves, through chain_id's rule.

    The posting id is the owner's choice and stays when that route serves it, context suffix included. When it does
    not, the route's own spelling answers: the gateway names `claude-gpt-5.6-terra` for `gpt-5.6-terra`, while the
    bare spelling there is the codex CLI's. A spelling without an effort suffix wins, so a gemini row keeps the
    effort the panel chose."""
    e = modelreg.model(mid, reg)
    route = modelreg._default_route(k.removesuffix("_models"), e)
    served = ((e.routes.get(route) or {}).get("served") or []) if (e is not None and route) else []
    if not served or mid in served or mid.removeprefix(RUNNER) in served: return chain_id(k, mid)
    ref = modelreg.normalize(mid, reg)
    same = next((x for x in served if modelreg.normalize(x, reg).ctx == ref.ctx
                 and modelreg.normalize(x, reg).effort == ref.effort), None)
    plain = next((x for x in served if not modelreg.normalize(x, reg).effort), None)
    return chain_id(k, same or plain or mid)

_ABSENT = object()   # a setting section the Save body leaves out: the file's own value stays, never cleared


def _entry(k, mid, prev, effort, window):
    """One v2 chain entry for a panel row, in the shape `modelreg.migrate` writes: `id`, `raw` when the spelling
    differs, an explicit route, and the row's effort and window. The switch is per model now (SPEC 889 #920): the hub
    writes no chain-level `on`, and an `on: false` a file already holds stays as the file has it.

    `raw` is not the panel's id (ADR-0147): review's Anthropic row carries `claude:`, and a gateway row carries the
    `claude-` alias of its bare model. A row already in the file keeps the spelling and the route it holds; a new row
    takes the spelling its chain needs. A `-low`/`-medium` suffix fixes an effort, so a changed effort drops it.

    A setting the body leaves out (`_ABSENT`) keeps the value the file holds. A value the body names -- the empty
    string or the default effort included -- replaces it, so an omitted section never clears a row."""
    ref = modelreg.normalize(mid)
    want = chain_id(k, mid)
    raw = _new_raw(k, mid, None)
    route = modelreg._default_route(k.removesuffix("_models"), modelreg.model(mid))
    if effort is _ABSENT: effort = prev.effort if prev is not None else None
    if window is _ABSENT: window = prev.hrs if prev is not None else None
    if prev is not None and prev.raw == want:
        held = modelreg.normalize(prev.raw)
        if not held.effort or held.effort == (effort or "medium"):
            raw = prev.raw
        elif mid.endswith("-" + held.effort):   # the owner set the default effort: drop the suffix that fixed another
            raw = _new_raw(k, mid[: -len(held.effort) - 1], None)
        route = prev.route or route
    out = {"id": ref.id}
    if raw != ref.id: out["raw"] = raw
    if route: out["route"] = route
    if prev is not None:   # what the panel does not draw stays: review's timeout and tool rule, an explicit context
        if prev.ctx: out["ctx"] = prev.ctx
        if prev.timeout_s is not None: out["timeout_s"] = prev.timeout_s
        if prev.no_tools: out["no_tools"] = True
    if effort and effort != "medium": out["effort"] = effort
    if isinstance(window, str) and HOURS.match(window) and window[:2] != window[3:]: out["hrs"] = window
    if prev is not None and not prev.on: out["on"] = False   # honored, never written: a row the file already holds off
    return out


def _setting(sec, k, raw):
    """One row's setting from a posted section: `_ABSENT` when the body left the chain (or the whole section) out, so
    the file's value stays; the row's own value otherwise, `None` when the section names the chain but not this row.

    A row is looked up by the spelling the file stores, by the panel's bare id, or by the model id the resolver folds
    both onto, so a caller may key a section by either spelling of one model."""
    if sec is _ABSENT or not isinstance(sec, dict) or k not in sec: return _ABSENT
    rows, want = sec.get(k) or {}, modelreg.normalize(raw).id
    for name, value in rows.items():
        if name in (raw, bare_id(raw)) or modelreg.normalize(name).id == want: return value
    return None


def _v2_chain(k, posted, doc, eff, hrs):
    """One v2 chain for the Save, from the panel's list in rank order (or the file's own list when the body omits it).

    An entry the panel's view hid -- `usable()` drops a model its chain cannot run (ADR-0147) -- is not in the posted
    list; it stays where it was, else a Save of another chain would delete the owner's entry. An entry the owner
    removed with ✕ was shown, so it goes.

    One model is one row. Two posted spellings can fold onto one stored spelling (`gpt-5.6-terra` and
    `claude-gpt-5.6-terra` both store as the gateway alias), so the fold drops the later one here: a file with two
    identical entries cannot be read back or saved again."""
    canon = k.removesuffix("_models")
    old = list(modelreg.chain(doc, canon).entries)
    if posted is None:   # the body names a section but no list: apply the section, keep the file's rank and rows
        return [_entry(k, e.raw, e, _setting(eff, k, e.raw), _setting(hrs, k, e.raw)) for e in old]
    by_id = {modelreg.normalize(e.raw).id: e for e in old}
    out, seen = [], set()
    for m in posted:
        ref = modelreg.normalize(m)
        seen.add(ref.id)   # two spellings of one model may both be posted: the panel drew two rows, so both stay
        out.append(_entry(k, m, by_id.get(ref.id), _setting(eff, k, m), _setting(hrs, k, m)))
    shown = {modelreg.normalize(m).id for m in v1_view(doc).get(k, [])}
    for i, e in enumerate(old):
        ref = modelreg.normalize(e.raw)
        if ref.id in shown or ref.id in seen: continue
        out.insert(min(i, len(out)), _entry(k, e.raw, e, _ABSENT, _ABSENT))
        seen.add(ref.id)
    written, unique = set(), []
    for e in out:   # one stored spelling, one row: the fold above can land two posted ids on the same `raw`
        spelling = e.get("raw") or e["id"]
        if spelling in written: continue
        written.add(spelling); unique.append(e)
    return unique


def _models_set(new):
    # The panel posts back whatever models_get() handed it, so every read-only field it carries comes back here: `known`
    # (the add box's list) and `last_coding` (the header's line). Dropping only `known` made the other one read as a chain
    # the panel invented -- "unknown chain" on every Save (2026-10-11).
    new = {k: v for k, v in new.items() if k not in READONLY} if isinstance(new, dict) else new
    if not isinstance(new, dict) or set(new) - set(CHAINS) - {"efforts", "hours", "off", "model_off"}: return {"err": "unknown chain"}
    d = json.loads(MODELS.read_text())
    eff, hrs = new.pop("efforts", _ABSENT), new.pop("hours", _ABSENT)
    new.pop("off", None)   # the chain-level switch is retired (SPEC 889 #920): an old page's `off` is read past, never written
    moff = new.pop("model_off", _ABSENT)   # the model-level switch (SPEC 889 #920): {model id: true|false}; absent keeps the file
    if moff is not _ABSENT:
        if not isinstance(moff, dict): return {"err": "model_off: must name models"}
        for m, v in moff.items():
            if not (isinstance(m, str) and MODEL_ID.match(m) and isinstance(v, bool)): return {"err": f"model_off: {m} must be true or false"}
    posted = {}
    for k, v in new.items():
        # An empty chain is a legal state: the reader tolerates it, and a file that carries one (the router's, until the
        # owner ranks it) must not refuse every Save. The panel's row controls keep at least one entry (SPEC 750).
        if not isinstance(v, list) or len(v) > 8 or not all(isinstance(m, str) and MODEL_ID.match(m) for m in v):
            return {"err": f"{k}: need at most 8 distinct model ids"}
        stored = [chain_id(k, m) for m in v]      # the panel works in bare ids; the file gets the spelling the role needs
        if len(set(stored)) != len(stored): return {"err": f"{k}: need at most 8 distinct model ids"}
        posted[k] = [bare_id(m) for m in v]
    # ✕ takes a row out of its chain, and the panel still posts that row's effort, hrs and switch back (#685): a setting
    # of a model no longer in its chain goes with the row, never refuses the Save. A bad value of a kept row still refuses,
    # and the sentence names the row and the rule, so a caller learns what to send.
    for name, sec, rule, ok in (("efforts", eff, "must name one of low|medium|high|max", lambda v: v in EFFORTS),
                                ("hours", hrs, "must be an HH-HH window (00-23)", lambda v: isinstance(v, str) and HOURS.match(v))):
        if sec is _ABSENT: continue
        if not isinstance(sec, dict) or set(sec) - set(CHAINS): return {"err": f"{name}: unknown chain"}
        for k, rows in sec.items():
            if not isinstance(rows, dict): return {"err": f"{name}.{k}: must name models"}
            for m, v in rows.items():
                if not (isinstance(m, str) and ok(v)): return {"err": f"{name}.{k}: {m} {rule}"}
    # The file is v2, and every chain writes there: the router row is a chain row like the other three (SPEC 750). The
    # graph and its record are never touched by a Save; a run reads the router chain from this file.
    changes = []
    if moff is not _ABSENT:
        was = model_off_ids(d)
        mo = dict(d.get("model_off") or {}) if isinstance(d.get("model_off"), dict) else {}
        for m, v in moff.items():
            key = modelreg.normalize(m).id
            if v: mo[key] = True
            else: mo.pop(key, None)
            if (key in was) != bool(v): changes.append((bare_id(m), "off" if v else "on"))   # one line per flip, never per read
        if mo: d["model_off"] = mo
        else: d.pop("model_off", None)
    chains = {k.removesuffix("_models"): _v2_chain(k, posted.get(k), d, eff, hrs) for k in CHAINS}
    modelreg.write_chains(MODELS, chains, d)
    models_record(changes)
    return models_get()

profiles_of_cwd = sessions.profiles_of

def sessions_payload():
    """What /api/sessions serves: this profile's live sessions, grouped by project (#833)."""
    return sessions.list_sessions(os.environ.get("SG_PROFILE", ""), profiles_of=profiles_of_cwd)

def conversation_payload(query):
    """What /api/sessions/conversation serves: one page of a live session of this profile, None when it is not one (#840)."""
    q = urllib.parse.parse_qs(query)
    before = (q.get("before") or [""])[0]
    return sessions.conversation(os.environ.get("SG_PROFILE", ""), (q.get("sid") or [""])[0], int(before) if before.isdigit() else None,
                                 profiles_of=profiles_of_cwd)

SEND_SETTING = "SQUAD_TAILNET_TERMINALS_SEND"
def send_rule(a):
    """(may this request send to a session, why not): loopback always sends; the tailnet sends only with its grant (ADR-0152 rule 9)."""
    if not a.tailnet: return True, ""
    if tailnet_block().get("terminals_send") is True: return True, ""
    return False, f"Sending from the tailnet is off. The owner sets {SEND_SETTING} (tailnet.terminals_send) to send."

def project_payload(a):
    """What /api/sessions/project serves (#898): the sessions in this profile's checkout and the send rule for this request."""
    ok, why = send_rule(a)
    return {"sessions": sessions.project_sessions(os.environ.get("SG_PROFILE", ""), CHECKOUT, profiles_of=profiles_of_cwd), "send": ok, "why": why}

# The session overlay (#898): one source, hub-overlay.js, on every page the hub serves. Each page names where its session
# routes are: the hub and mobile pages a quoted path the mux gives the profile prefix, a run dashboard its own path up to
# /dash/, which the mux never rewrites.
OVERLAY_JS = (pathlib.Path(__file__).resolve().parent / "hub-overlay.js").read_text()
OVERLAY_HUB = 'async()=>[{label:"",api:"/api/sessions"}]'
OVERLAY_DASH = 'async()=>[{label:"",api:location.pathname.split("/dash/")[0]+"/api"+"/sessions"}]'
def overlay_tag(sources):
    return f"<script>window.SQUAD_OV_SOURCES={sources};\n{OVERLAY_JS}</script>"

def with_overlay(body, dash=False):
    """A page with the overlay before its closing body tag, or at its end when it has none."""
    tag = overlay_tag(OVERLAY_DASH if dash else OVERLAY_HUB).encode()
    i = body.rfind(b"</body>")
    return body[:i] + tag + body[i:] if i >= 0 else body + tag

MOBILE_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'"

GESTURE_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-gesture.js").read_text()
MOVE_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-move.js").read_text()
RUN_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-run.js").read_text()
# The card rules of the desktop board (#917): spec color and wave, one block of board-lanes.js, so the mobile page holds no
# rule of its own and never the board's column names (ADR-0137).
CARD_RULES_JS = re.search(r"// <card-rules>.*?// </card-rules>", (pathlib.Path(__file__).resolve().parent / "board-lanes.js").read_text(), re.S).group(0)
# The Monitor card (#983) on the phone's first tab: its markup, style and script are cut from the desktop page, never
# copied, so one change draws both. The cell helpers and the ceiling ride along; the repository and the ceiling are this hub's.
INST_CARD = re.search(r"<details id=inst\b.*?</details>", PAGE, re.S).group(0)
INST_CSS = re.search(r"^/\* INST-CSS\{ \*/$.*?^/\* \}INST-CSS \*/$", PAGE, re.M | re.S).group(0)
INST_JS = "\n".join([re.search(r"^const LOAD_ERR=.*$", PAGE, re.M).group(0),
                     *re.findall(r"^function ld(?:Num|Round|Cell|Cells)\(.*$", PAGE, re.M),
                     re.search(r"^// INST\{.*?^// \}INST$", PAGE, re.M | re.S).group(0)]).replace("__LOADERR__", str(LOAD_ERR_PCT)).replace("__REPO__", REPO)
MOBILE_PAGE = (pathlib.Path(__file__).resolve().parent / "hub-mobile.html").read_text().replace("<!--OVERLAY-->", overlay_tag(OVERLAY_HUB)).replace("<!--INST_CARD-->", INST_CARD).replace("/*INST_CSS*/", INST_CSS).replace("/*INST_JS*/", INST_JS).replace("/*SWITCHER_JS*/", SWITCHER_JS).replace("/*GESTURE*/", GESTURE_JS).replace("/*MOVE*/", MOVE_JS).replace("/*RUN*/", RUN_JS).replace("/*BOARD_FILTER*/", BOARD_FILTER_JS).replace("/*WEB_TOKENS*/", WEB_TOKENS).replace("/*CARD_RULES*/", CARD_RULES_JS)
MOBILE_SHELL = ["/mobile/", "/mobile/manifest.webmanifest", "/mobile/icon.svg"]
SW_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-sw.js").read_text().replace("/*SHELL*/[]", json.dumps(MOBILE_SHELL))
MANIFEST_JSON = json.dumps({"id": "/mobile/", "name": f"Squad hub {REPO}", "short_name": "Hub", "start_url": "/mobile/", "scope": "/mobile/", "display": "standalone", "background_color": "#0f1115", "theme_color": "#0f1115", "icons": [{"src": "/mobile/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}).encode()
MOBILE_ICON = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="36" fill="#0f1115"/><circle cx="96" cy="96" r="52" fill="#3ecf8e"/></svg>'
MOBILE_ASSETS = {"/mobile": (MOBILE_PAGE.encode(), "text/html; charset=utf-8"), "/mobile/": (MOBILE_PAGE.encode(), "text/html; charset=utf-8"),
                 "/mobile/manifest.webmanifest": (MANIFEST_JSON, "application/manifest+json"), "/mobile/icon.svg": (MOBILE_ICON, "image/svg+xml"),
                 "/mobile/sw.js": (SW_JS.encode(), "text/javascript; charset=utf-8")}

def test_log_page(d, lg):
    import html as _h
    ref = '<meta http-equiv="refresh" content="3">' if d.get("state") == "running" else ""
    return (f'<!doctype html><meta charset=utf-8><style>{WEB_TOKENS}</style>{ref}<body style="background:#0d1117;color:#c9d1d9;font:var(--fs-body) ui-monospace,monospace;margin:12px"><div style="color:#58a6ff">test #{d.get("issue")} · {d.get("state")}{" (exit "+str(d.get("rc"))+")" if "rc" in d else ""} · {_h.escape(d.get("dir",""))}</div><div style="color:#8b949e">$ {_h.escape(d.get("cmd",""))}</div><pre style="white-space:pre-wrap">{_h.escape(lg)}</pre><script>scrollTo(0,document.body.scrollHeight)</script>').encode()


def mobile_refusal(reason):
    return ('<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Hub</title>'
            f'<style>{WEB_TOKENS}</style><body style="font:var(--fs-body) system-ui;margin:24px"><h1>The hub refused this login</h1><p>{html.escape(reason)}</p>').encode()

def served_page(login=""):
    """The page as the hub serves it, every placeholder substituted. The model-id shape replaces its quoted placeholder:
    json.dumps brings its own quotes, and a second pair is a syntax error that empties the page."""
    who = f"signed in as {html.escape(login)}" if login else ""
    return PAGE.replace("__WHO__", who).replace("__REPO__", REPO).replace("__CAPS__", json.dumps(loop_caps())).replace("__LOADMS__", str(int(LOAD_SAMPLE_SECS * 1000))).replace("__LOADERR__", str(LOAD_ERR_PCT)).replace("<!--OVERLAY-->", overlay_tag(OVERLAY_HUB))


def own_host(h):
    """The one Host rule for GET and POST: the hub's own loopback name and port, so a page that rebinds its name to 127.0.0.1 is refused. `h` is any handler, the mux's too, which runs these routes."""
    return (h.headers.get("Host") or "") in (f"127.0.0.1:{h.server.server_port}", f"localhost:{h.server.server_port}")


PLUGIN_ID = "agent-squad@agent-squad"
def plugin_squad():
    """The agent-squad `squad` package a host profile's hub reads its settings through: SG_SQUAD when it names one, else the
    install path the plugin registry records, as env.sh and launch.py already resolve it (#871). Empty when neither answers,
    so a hub with no package refuses every tailnet request as before. Rule layer: one registry read, no hub state."""
    if os.environ.get("SG_SQUAD"): return os.environ["SG_SQUAD"]
    try:
        registry = pathlib.Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        return json.loads(registry.read_text())["plugins"][PLUGIN_ID][0]["installPath"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ""

def tailnet_roots():
    """Where the tailnet rule reads its seam and settings reader: the tree this file sits in first, so a stale SG_SQUAD never
    answers for the hub's own code; then the profile's roots; then the agent-squad package the plugin install holds, so a host
    profile whose tree and checkout both hold no squad package still reads its own project file (#871)."""
    roots = [str(pathlib.Path(__file__).resolve().parents[3]),
             *squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), plugin_squad()]
    return [r for i, r in enumerate(roots) if r and r not in roots[:i]]

_SEAM = {}
def tailnet_seam():
    """The tailnet access seam (squad/tailnet_access.py), loaded by path from the first root that holds it; None when no root does."""
    if "seam" not in _SEAM:
        _SEAM["seam"] = None
        for root in tailnet_roots():
            path = pathlib.Path(root) / "squad" / "tailnet_access.py"
            if path.is_file():
                spec = importlib.util.spec_from_file_location("tailnet_access", path)
                seam = importlib.util.module_from_spec(spec); spec.loader.exec_module(seam)
                _SEAM["seam"] = seam
                break
        if _SEAM["seam"] is None:
            log_once("hub: no squad/tailnet_access.py, every tailnet request is refused")
    return _SEAM["seam"]

_PROJECT = {}
def tailnet_block():
    """The `tailnet` block of the project file under the profile's checkout (squad/project_settings.py). Empty when the file or
    the squad package does not load, so the seam refuses every tailnet request."""
    try:
        if "ps" not in _PROJECT:
            (_PROJECT["ps"],) = load_squad(tailnet_roots(), ("project_settings",))
        data, _ = _PROJECT["ps"].load(CHECKOUT)
    except Exception as e:
        log_once(f"hub: tailnet settings unavailable, every tailnet request is refused: {type(e).__name__}: {' '.join(str(e).split())}")
        return {}
    return (data or {}).get("tailnet") or {}

class Access(NamedTuple):
    allowed: bool
    reason: str
    login: str      # the login the page header shows; empty off the tailnet
    tailnet: bool   # the request is addressed to the tailnet name

LOGIN_HEADER = "Tailscale-User-Login"   # the seam's name, for a hub with no seam: a request that carries it is never loopback
def access(h, method):
    """The one rule for every route: the loopback Host as own_host has always answered it, a tailnet request through the seam.
    A request that carries the tailnet login header is a tailnet request whatever its Host: on a loopback Host it reads, and
    it never writes, so a `tailscale serve` that hands the hub a loopback Host never earns a loopback write (ADR-0152 rule 9)."""
    seam = tailnet_seam()
    login = h.headers.get(seam.LOGIN_HEADER if seam else LOGIN_HEADER) or ""
    if own_host(h) and not login: return Access(True, "", "", False)
    if seam is None: return Access(False, "no tailnet access seam", "", bool(login))
    if own_host(h):   # a read passes as a loopback read always has; a write needs the tailnet Host (rules 3 and 5)
        read = method.upper() in seam.READ_METHODS
        return Access(read, "" if read else "forbidden host", "", True)
    host = h.headers.get("Host") or ""
    tn = tailnet_block()
    v = seam.decide(host=host, origin=h.headers.get("Origin") or "", method=method, login=login,
                    port=h.server.server_port, tailnet_host=tn.get("host") or "", allowed_logins=tn.get("allow") or [])
    return Access(v.allowed, v.reason, login if v.allowed else "", bool(tn.get("host")) and host == tn["host"])

DASH_PREFIXES = ("/dash/", "//")
DASH_PATH = re.compile(r"/dash/([\w-]+)(/[\w./-]*)?")   # a run id, then plain names only: no escape, host or port
DASH_TIMEOUT_SECS = 60.0
DASH_REPLY_HEADERS = ("content-type", "content-length", "cache-control", "x-content-type-options", "referrer-policy")
def own_origin(h):
    """A loopback write's one Origin rule: the hub's own exact origin, on either loopback name and its own port (#824)."""
    org = h.headers.get("Origin") or ""
    return org in (f"http://127.0.0.1:{h.server.server_port}", f"http://localhost:{h.server.server_port}")

def dash_port(run_id):
    """The port the run list names for this run id, or None: the one address a forwarded request can reach."""
    for r in run_list():
        if r.get("id") == run_id and str(r.get("port") or "").isdigit(): return int(r["port"])
    return None

def dash_target(path):
    """(run id, path on that dashboard) for a /dash/ path, or None when it names anything but a run's own path."""
    m = DASH_PATH.fullmatch(path.split("?", 1)[0])
    if not m: return None
    rest = m[2] or "/"
    if ".." in rest.split("/"): return None
    return m[1], rest

def request_target(h):
    """The target as the client sent it: the handler turns a leading // into /, which would make //host read as a page."""
    parts = h.requestline.split()
    return parts[1] if len(parts) > 1 and parts[1].startswith("//") else h.path

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def refuse(self, reason, page=False):
        body = mobile_refusal(reason) if page else reason.encode()
        self.send_response(403); self.send_header("Content-Type", "text/html; charset=utf-8" if page else "text/plain; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def not_found(self):
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    def dash(self, method, body=b""):
        """A run's own dashboard, reached on loopback with its own Host and Origin, so its checks stay as they are. The body
        streams back as it arrives. Any other run id, port or host answers 404 and opens no connection."""
        raw = request_target(self)
        target = dash_target(raw)
        port = dash_port(target[0]) if target else None
        if port is None or (method == "POST" and target[1] != "/run/control"): return self.not_found()
        rest = target[1] + ("?" + raw.split("?", 1)[1] if "?" in raw else "")
        headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
        for k in ("Content-Type", "Last-Event-ID"):
            if self.headers.get(k): headers[k] = self.headers[k]
        up = http.client.HTTPConnection("127.0.0.1", port, timeout=DASH_TIMEOUT_SECS)
        try:
            up.request(method, rest, body=body if method == "POST" else None, headers=headers)
            r = up.getresponse()
        except (OSError, http.client.HTTPException):
            up.close(); msg = b"dashboard unreachable\n"
            self.send_response(502); self.send_header("Content-Length", str(len(msg))); self.end_headers(); self.wfile.write(msg); return
        page = method == "GET" and target[1] == "/" and r.status == 200 and "text/html" in (r.getheader("Content-Type") or "")
        try:
            data = with_overlay(r.read(), dash=True) if page else None   # the run's page gains the session overlay (#898); a stream is never held
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() in DASH_REPLY_HEADERS and not (data is not None and k.lower() == "content-length"): self.send_header(k, v)
            if data is not None: self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close"); self.end_headers(); self.close_connection = True
            if data is not None: self.wfile.write(data); return
            while chunk := r.read1(65536):
                self.wfile.write(chunk); self.wfile.flush()
        except (OSError, http.client.HTTPException): pass
        finally: up.close()
    def do_GET(self):
        a = access(self, "GET")
        if not a.allowed: return self.refuse(a.reason, page=self.path.split("?", 1)[0] in ("/mobile", "/mobile/"))
        if request_target(self).startswith(DASH_PREFIXES): return self.dash("GET")
        if self.path.startswith(("/held/", "/ended/")):   # a run the childcc pass holds, or one that ended today: its dashboard (#689, #830)
            name = re.sub(r"[^\w-]", "", self.path.split("/", 2)[2].split("?")[0])
            if (open_held if self.path.startswith("/held/") else open_ended)(name) is None: return self.not_found()
            refresh_runs()   # the run list names the new dashboard, so the forward finds it
            self.send_response(302); self.send_header("Location", f"/dash/{name}/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/test/"):   # a test.sh pass: its log, refreshed while it runs
            n = re.sub(r"[^\w-]", "", self.path[6:]); T = STATE / "tests"
            try: d = json.loads((T / (n + ".json")).read_text())
            except Exception: d = {}
            try: lg = (T / (n + ".log")).read_text(errors="replace")[-60000:]
            except Exception: lg = "(no output yet)" if d.get("state") == "running" else "no such test"   # test.sh makes the log after the json
            body, ct = test_log_page(d, lg), "text/html; charset=utf-8"
        elif self.path.startswith("/api/runs"): body, ct = json.dumps(run_list()).encode(), "application/json"
        elif self.path.startswith("/api/adapter-errors"): body, ct = json.dumps(adapter_errors()).encode(), "application/json"
        elif self.path.startswith("/api/delivery"): body, ct = json.dumps(delivery()).encode(), "application/json"
        elif self.path.startswith("/api/ended"): body, ct = json.dumps(ended_today()).encode(), "application/json"
        elif self.path.startswith("/api/stats"): body, ct = json.dumps(stats((re.search(r"period=(\w+)", self.path) or [0, "day"])[1] if re.search(r"period=(hour|day|month)", self.path) else "day", (re.search(r"src=(sub|api)", self.path) or [0, "both"])[1])).encode(), "application/json"
        elif self.path.startswith("/api/issue"):
            m = re.search(r"[?&]n=(\d{1,6})\b", self.path); rr = re.search(r"[?&]r=([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)", self.path)
            body, ct = json.dumps(issue_detail(int(m[1]), rr[1] if rr else None) if m else {"err": "bad n"}).encode(), "application/json"
        elif self.path.startswith("/api/board"):
            body, etag = board_served(); ct = "application/json"
            if etag and etag in [t.strip().removeprefix("W/") for t in (self.headers.get("If-None-Match") or "").split(",")]:   # the page holds this board (#973)
                self.send_response(304); self.send_header("ETag", etag); self.send_header("Cache-Control", "no-store"); self.end_headers(); return
            self.board_etag = etag
        elif self.path.startswith("/api/chart/cfd"): body, ct = json.dumps(flow_cfd(*board_filters(self.path.partition("?")[2]))).encode(), "application/json"
        elif self.path.startswith("/api/chart/lead"): body, ct = json.dumps(flow_lead(*board_filters(self.path.partition("?")[2]))).encode(), "application/json"
        elif self.path.startswith("/api/leadtime"): body, ct = json.dumps(lead_zoom(self.path.partition("?")[2])).encode(), "application/json"   # the control chart over a dragged range (#900)
        elif self.path.startswith("/api/version"): body, ct = json.dumps({"v": BOOT}).encode(), "application/json"
        elif self.path.startswith("/api/subs"): body, ct = json.dumps({**subs_month(), "presets": {k: {i: {"name": n, "usd": p} for i, (n, p) in v.items()} for k, v in PRESETS.items()}}).encode(), "application/json"
        elif self.path.startswith("/api/models"): body, ct = json.dumps(models_get()).encode(), "application/json"
        elif self.path.startswith("/api/limits"): body, ct = json.dumps(limits_read()).encode(), "application/json"   # Settings > Spend & limits (#926)
        elif self.path.startswith("/api/slack"): body, ct = json.dumps(slack_view(slack_file())).encode(), "application/json"   # Settings > Communication (#924)
        elif self.path.startswith("/api/speed"): body, ct = json.dumps(speed()).encode(), "application/json"
        elif self.path.startswith("/api/load"): body, ct = json.dumps(load_payload()).encode(), "application/json"
        elif self.path.startswith("/api/status"): body, ct = json.dumps(status_payload()).encode(), "application/json"
        elif self.path.startswith("/api/sessions/send"):
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            out = session_send.status(os.environ.get("SG_PROFILE", ""), (q.get("sid") or [""])[0], (q.get("client_id") or [""])[0], profiles_of=profiles_of_cwd, root=STATE)
            body, ct = json.dumps(out).encode(), "application/json"
        elif self.path.split("?", 1)[0] == "/api/loop": body, ct = json.dumps(loop_view(a)).encode(), "application/json"
        elif self.path.split("?", 1)[0] == "/api/sessions/project": body, ct = json.dumps(project_payload(a)).encode(), "application/json"
        elif self.path.startswith("/api/sessions/conversation"):
            page = conversation_payload(self.path.partition("?")[2])
            if page is None: return self.not_found()
            body, ct = json.dumps(page).encode(), "application/json"
        elif self.path.startswith("/api/sessions"): body, ct = json.dumps(sessions_payload()).encode(), "application/json"
        elif (asset := MOBILE_ASSETS.get(self.path.split("?", 1)[0])): body, ct = asset
        else: body, ct = served_page(a.login).encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store")
        if self.path.startswith("/api/board") and getattr(self, "board_etag", None): self.send_header("ETag", self.board_etag)
        if self.path.split("?", 1)[0] in ("/mobile", "/mobile/"): self.send_header("Content-Security-Policy", MOBILE_CSP)
        self.end_headers(); self.wfile.write(body)

    def loop_post(self, a):
        """The loop switch's write (#923): the access rule, the tailnet grant, the own or tailnet origin and a JSON body, then the switch."""
        length = self.headers.get("Content-Length") or "0"
        raw = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
        tn = tailnet_block()
        origin_ok = "json" in (self.headers.get("Content-Type") or "") and ((self.headers.get("Origin") or "") == f"https://{tn.get('host')}" if a.tailnet else own_origin(self))
        try: body = json.loads(raw or b"{}")
        except ValueError: body = None
        code, out = loop_switch(a, body, origin_ok)
        data = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def send_to_session(self, a):
        """A message to a live session (#834, #898): a JSON body, and from loopback the hub's own Origin, from the tailnet an
        allowed login, the tailnet Origin and the grant (ADR-0152 rule 9)."""
        length = self.headers.get("Content-Length") or "0"
        raw = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
        if not a.allowed: return self.refuse(a.reason)
        tn = tailnet_block()
        origin_ok = (self.headers.get("Origin") or "") == f"https://{tn.get('host')}" if a.tailnet else own_origin(self)
        if not (send_rule(a)[0] and origin_ok and "json" in (self.headers.get("Content-Type") or "")): return self.refuse("forbidden")
        try: body = json.loads(raw)
        except ValueError: body = None
        if not isinstance(body, dict) or set(body) != {"sid", "text", "client_id"}:
            out, code = {"err": "bad request"}, 400
        else:
            out, code = session_send.send(os.environ.get("SG_PROFILE", ""), body["sid"], body["text"], body["client_id"], profiles_of=profiles_of_cwd, root=STATE), 200
        data = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        # a cross-site page must not be able to write: a loopback write needs our own origin, a tailnet write the tailnet origin (the seam), and a JSON body
        a = access(self, "POST")
        if request_target(self).startswith(DASH_PREFIXES):   # the body is read before any refusal, as below
            length = self.headers.get("Content-Length") or "0"
            raw = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
            if not a.allowed: return self.refuse(a.reason)
            if not (a.tailnet or own_origin(self)) or "json" not in (self.headers.get("Content-Type") or ""): return self.refuse("forbidden")
            return self.dash("POST", raw)
        if self.path.split("?", 1)[0] == "/api/sessions/send": return self.send_to_session(a)
        if self.path.split("?", 1)[0] == "/api/loop": return self.loop_post(a)
        ok = a.allowed and self.path in ("/api/models", "/api/plans", "/api/promote", "/api/limits", "/api/slack") and (a.tailnet or own_origin(self)) and "json" in (self.headers.get("Content-Type") or "")
        try:
            raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20))   # read before any answer: a refusal that leaves the body unread resets the connection; the models panel posts back ~20 KB of read-only fields (#684)
            if not ok: out = {"err": "forbidden"}
            else:
                body = json.loads(raw or b"{}")
                if self.path == "/api/promote": out = promote(body.get("issue")) if isinstance(body, dict) else {"err": "bad request"}   # the owner's move (#560)
                elif self.path == "/api/limits": out = limits_set(body)   # Settings > Spend & limits (#926)
                elif self.path == "/api/slack": out = slack_set(body)   # Settings > Communication (#924)
                else: out = (plans_set if self.path == "/api/plans" else models_set)(body)
        except Exception as e: out = {"err": "bad request"}
        if not a.allowed and a.tailnet: return self.refuse(a.reason)
        body = json.dumps(out).encode(); self.send_response(400 if "err" in out else 200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

SELF = pathlib.Path(__file__).resolve(); LOADED = SELF.stat().st_mtime_ns
BOOT = time.time_ns()   # the page's version is this process, not this file: a restart for a changed hubmux.py, run-sheet.js or address.sh leaves hub.py's mtime alone, and the open page must still reload
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
    threading.Thread(target=refresh_prs, daemon=True).start(); threading.Thread(target=refresh_quota, daemon=True).start(); threading.Thread(target=watch_self, daemon=True).start(); threading.Thread(target=refresh_board, daemon=True).start(); threading.Thread(target=refresh_fast, daemon=True).start();threading.Thread(target=load_sampler, daemon=True).start(); threading.Thread(target=watch_runs, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
