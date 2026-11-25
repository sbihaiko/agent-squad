# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# The board draws the shipped definition (#247): its statuses, in its order, with their colours. Open PRs are a lane beside
# the axis, never a column, and it is for tracking only (#384); a work branch has no lane of its own -- its kind reaches the
# kinds view -- and a landed branch is read from its issue card. Every name comes from the table; COLUMN reads a column or a
# lane by its role.
BOARD_COLUMNS = _columns_module.view_columns()
BOARD_LANES = _columns_module.LANES
COLUMN = {**_columns_module.ROLE, **{lane["key"]: lane["name"] for lane in BOARD_LANES}}
BOARD_NAMES_JSON = json.dumps(COLUMN, separators=(",", ":"))
DOING = _columns_module.DOING   # the one name of the claimed column: the hub names no column of its own
BOARD_DOING_JSON = json.dumps(_columns_module.DOING)
BOARD_REVIEW_JSON = json.dumps(_columns_module.REVIEWING)
BOARD_CHART_ROLES = {**{f"var(--s{i})": f"--chart-series-{i}" for i in range(1, 9)}, "var(--wait)": "--chart-wait", "var(--acc)": "--chart-bar", "var(--ok)": "--chart-ok", "var(--warn)": "--chart-warn", "var(--err)": "--chart-err", "var(--none)": "--chart-none", "var(--run)": "--chart-run", "var(--drop)": "--chart-drop", "var(--mute)": "--chart-series-other"}
COLUMN_COLORS = {c["name"]: f"var({BOARD_CHART_ROLES.get(c['color'], '--chart-series-fallback')})" for c in BOARD_COLUMNS}   # one fill per column (#1604)
BOARD_COLORS = {**COLUMN_COLORS, **{lane["name"]: f"var({BOARD_CHART_ROLES.get(lane['color'], '--chart-series-fallback')})" for lane in BOARD_LANES}}
BOARD_COLORS_JSON = json.dumps(BOARD_COLORS, separators=(",", ":"))
BOARD_FLOW_JSON = json.dumps([column["name"] for column in BOARD_COLUMNS], separators=(",", ":"))

POLL_CLOCK = time.monotonic   # the age reader of every kept poll answer (the spend and profiler caches): a duration, never an
                              # instant, so a wall-clock jump moves a view's own timestamps and leaves the age right. It is
                              # defined here, in the first theme, so every later theme reads one bound clock and a stamp and
                              # its comparison never fall on two clocks (#2440, #2848). A test injects its own.

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
# The profile's own squad package root, read once at import. hubmux.py pins SG_SQUAD around the hub's import and
# restores the shell's after it, so a request-time read of SG_SQUAD found the hub process's own value and the hub of a
# host profile resolved no `squad` package (#2820). The request path passes this value to squad_roots, never the env's.
SQUAD_ROOT = os.environ.get("SG_SQUAD", "")
PROJECTS, FORK_DIR = os.environ.get("SG_PROJECTS", ""), os.environ.get("SG_FORK_DIR", "")   # read once at import: the mux pins them per profile (#859)
# The profile this hub was loaded under, read once. hubmux.py pins the profile environment around the hub's import
# and restores the shell's after it, so a read at request time found no SG_PROFILE and every session route answered
# empty through the mux (#1073). A request path reads this name, never the environment.
PROFILE_NAME = os.environ.get("SG_PROFILE", "")
PROFILES_DIR_AT_IMPORT = os.environ.get("SG_PROFILES_DIR", "")
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
NO_PROFILE_STATE = "_no-profile"   # the state name of a hub no profile resolves for: it holds no loop file, so the hub reads nothing
def state_dir():
    """$SG_STATE when the runner exports it, else the state directory of the profile the loop runs, as the seam (address.sh)
    names it: `sg_state_dir <profile>`, for SG_PROFILE, else for the profile whose checkout is SG_CHECKOUT. The machine base,
    `sg_state_base`, is never read: it is the legacy directory, and an old active-runs.txt there named runs long dead (#1226).
    A runner that no profile resolves for reads `sg_state_dir _no-profile` under its own home, which holds no loop file,
    instead of dying with KeyError at import (#135); the loop, which exports SG_STATE, keeps the directory it always had."""
    st = os.environ.get("SG_STATE")
    if st: return st
    here = pathlib.Path(__file__).resolve().parent
    for who in (os.environ.get("SG_PROFILE"), CHECKOUT):
        if not who: continue
        p = subprocess.run(["bash", str(here / "address.sh"), who], capture_output=True, text=True, timeout=30)
        ctx = {k: v for k, _, v in (ln.partition("=") for ln in shlex.split(p.stdout))} if p.returncode == 0 else {}
        if ctx.get("SG_STATE"): return ctx["SG_STATE"]
    p = subprocess.run(["bash", "-c", '. "$1/address.sh" 2>/dev/null; sg_state_dir "$2"', "_", str(here), NO_PROFILE_STATE], capture_output=True, text=True, timeout=30)
    st = p.stdout.strip()
    if not st: sys.exit("hub: no state directory (export SG_STATE, or repair the seam, address.sh)")
    return st
STATE = pathlib.Path(state_dir())
os.environ.setdefault("SG_STATE", str(STATE))   # the issue ledger reads SG_STATE from the environment (#1022)
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

CLAUDE_PROJECTS = pathlib.Path(os.environ.get("SG_CLAUDE_PROJECTS") or (pathlib.Path.home() / ".claude" / "projects"))   # where Claude Code keeps each session's transcript, in a folder named for its working directory; SG_CLAUDE_PROJECTS is the test seam, so no test reads the owner's transcripts
PROJECT_NAME_MAX = 200   # Claude Code cuts a longer sanitized working directory to this length and adds a hash suffix (#796)
FOLDERS = {}             # (run record, child): (working directory, transcript folder) once found (#802)
USAGE_FILES = {}         # one transcript file's messages, cached by (path, mtime, size): {key: [(message id, model, usage)]}

WRITE_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit"}   # squad/graph.py:WRITE_TOOLS; a child with one of these runs in its own worktree (#802)

def _epoch(s):
    """An ISO timestamp as epoch seconds, None when it is not one or it has none (#802, the loop state strip #1765)."""
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
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
    try: st = os.stat(p)
    except OSError: return hist[p] if p in hist and "v19" in hist[p] else None
    h = hist.get(p)
    if h and "v19" in h and h.get("rec") == [st.st_mtime_ns, st.st_size]: return h   # v10 added the worktree paths a run works in (the load attribution reads them); v11 reads the run issue from request.txt; v12 reads every issue of a dispatch request's CHILD lines; v13 reads the test share a gate counted; v14 reads the card's label from the dispatch form too; v15 splits a run sub/api on the canonical model id (#791, #793); v16 counts a child's tokens from its transcript when no outcome reached the record (#796); v17 counts a fix round's messages after its last outcome, follows a reader to its writer, and drops a shared folder (#802); v18 keeps the record's write time and size, so a record that grew after its verdict (a resume) is read again, and reads the last invocation's verdict only (#1229); v19 keeps each outcome as a call, so a transcript call a record already holds is never counted twice (#1756)
    c = live.get(p)
    if c and c[0] == st.st_mtime and c[1] == st.st_size and c[3] == child_stamp(p, *c[4]): return c[2]
    node = None; model_cur = None
    spend = sub = api = 0.0; mod = {}; t0 = t1 = ver = None; reasons = []; land = False; red = False; issues = set(); ntok = 0; kind = "code"; fix = None; stack = None; bug = False; merge = False; head = None; rc = None; share = None
    rid = pathlib.Path(p).parent.name   # the run folder, read before the loop: the share below rebinds a local of its own, never `p`
    accepted, done_kids, wts, defs, outs = set(), set(), set(), {}, {}
    ocalls = []   # one entry per outcome: the per-call shape a transcript call is matched against (#1756)
    try: req_issues = request_issues((pathlib.Path(p).parent / "request.txt").read_text(errors="replace")); has_req = True
    except OSError: req_issues = set(); has_req = False
    with open(p) as f:
        text = f.read()   # one read: the loop below and the invocation slice share it
        for i, l in enumerate(text.splitlines(keepends=True)):
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
            node = d.get("node") or node   # the last event that names a node is the run's current stage; the record already carries it
            if e == "call_start" and isinstance(d.get("chain"), list) and d["chain"]: model_cur = d["chain"][0]   # the chain head is the model the node calls first
            if e == "outcome" and d.get("model"): model_cur = d["model"]   # the last answered call names the model the run is on
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
                # The call's own span, not the outcome's write time: `elapsed` is its duration, so a gate or a commit that
                # runs between the call and the write moves nothing a transcript call is matched against (#1756).
                el = d.get("elapsed")
                start = ts - el if isinstance(ts, (int, float)) and isinstance(el, (int, float)) else ts
                ocalls.append({"t0": start, "t1": ts, "model": d.get("model") or "unknown", "tokens_in": d.get("tokens_in") or 0,
                               "tokens_out": d.get("tokens_out") or 0, "cache_read": d.get("cache_read") or 0})
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
            if e == "run_start":
                k = d.get("kind") or (d.get("engine_options") or {}).get("kind") or (d.get("defaults") or {}).get("kind")   # absent today: every run is code
                if k in ("test", "gui-test"): kind = "test"
            elif e == "gate_result": red = d.get("outcome") == "fail"   # the latest gate verdict is the measured red (ADR-0164)
            elif e == "land":
                if d.get("outcome") == "pass": land = True
                h = (d.get("effect") or {}).get("head") if isinstance(d.get("effect"), dict) else None
                if h: head = h
    # Only the last verdict after the last start or resume is final: a run resumed after a verdict is live again (ADR-0167, #1229).
    # The run's own process is the engine pid the last call of that invocation names; a pid from before it is another
    # invocation's (#1226). Both come from the one reader, squad/run_liveness.py (#1228).
    RL = liveness(); last = RL.invocation(text)
    fin = RL.verdict(last); pid, pid_at = RL.engine_process(last) or (None, None)
    if fin: ver, reasons, t1, rc = fin.get("verdict"), fin.get("reasons") or [], fin.get("ts"), fin.get("exit_code")
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
    r = {"spend": round(spend, 4), "sub": round(sub, 4), "api": round(api, 4), "mod": mod, "calls": ocalls, "v8": 1, "v9": 1, "v10": 1, "v11": 1, "v12": 1, "v13": 1, "v14": 1, "v15": 1, "v16": 1, "v17": 1, "v18": 1, "v19": 1, "model": model_cur, "rec": [st.st_mtime_ns, st.st_size], "t0": t0, "t1": t1, "verdict": ver, "reasons": reasons, "land": land, "issues": sorted(issues, key=int), "issue_src": src, "issue_why": why, "kind": kind, "fix": fix, "stack": stack, "bug": bug, "merge": merge, "head": head, "exit_code": rc, "agents": len(accepted - done_kids), "wts": sorted(wts), "share": share, "pid": pid, "pid_at": pid_at, "node": node, "measured_red": red}
    r["id"] = rid   # the run folder: the label a run with no issue falls back on
    r["label"] = run_label(r)               # the card's name is computed here, so the page renders a value and holds no rule (ADR-0137)
    if ver is not None or p in hist:   # a finished run is cached; a cached run that resumed leaves the cache
        if ver is not None: hist[p] = r
        else: hist.pop(p)
        with LOCK:
            try: HIST.write_text(json.dumps(hist))
            except OSError: pass
    if ver is None: live[p] = (st.st_mtime, st.st_size, r, stamp, (kids, defs))
    return r

def state_of(r, age):
    ver, reasons = r["verdict"], r["reasons"]
    if ver is None: return "running" if age < STALL_SECS else "stalled"
    if ver == "failed" and r["land"] and reasons and all(str(x).startswith("ac-verify:no_ac_commands") for x in reasons): return "unverified"   # work landed, but the final gate had no acceptance command to run
    if ver == "failed" and not r.get("measured_red", True):   # the janitor's takeover and the wave's age cap name their cause in the verdict (#2073); a cached run without the fact keeps its verdict
        if "killed/taken-over" in reasons: return "taken-over"
        if "killed/age-cap-requeue" in reasons: return "retired-age-cap"
    return ver

def run_roots(co):   # where a checkout keeps run folders: its runs/, and each run or child worktree's runs/ (#580, #585)
    co = pathlib.Path(co)
    return [co / "runs", *(w / "runs" for kind in ("run", "child") for w in sorted((co / "worktrees" / kind).glob("*")))]

def trip_record_files():
    """The run records the Loop tab folds (#2588): every run folder of this checkout's run roots, then the copies wtclean.sh
    keeps in the state's run-archive/ before it removes the tree that held them, so a delivery outlives its tree. A run the
    live folder and the archive both hold is read once, from its live folder, and counts once."""
    seen, out = set(), []
    for root in [*run_roots(CHECKOUT), STATE / "run-archive"]:
        try: files = sorted(root.glob("run-*/record.jsonl"))
        except OSError: continue
        for p in files:
            if p.parent.name in seen: continue
            seen.add(p.parent.name); out.append(p)
    return out

def own_roots():
    """The run roots of this profile: its checkout, SG_PROJECTS and SG_FORK_DIR. A dashboard outside them is another profile's."""
    ds = set(run_roots(CHECKOUT))
    for x in [*PROJECTS.split(":"), FORK_DIR]:
        if x: ds.update(run_roots(x))
    return ds

def project_dirs():
    own = own_roots(); ds = set(own)
    for p in process_table().values():
        m = re.search(r"squad\.dashboard_server\s+(\S+)", p.command)
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
            out.append({"id": rd.name, "dir": str(rd), "t0": t0, "t1": t1, "spend": r["spend"], "sub": r["sub"], "api": r["api"], "mod": r["mod"], "calls": r.get("calls") or [], "dur": (t1 - t0) / 60, "done": r["verdict"] is not None})
    return out

TITLE = {}   # issue number -> short title, refreshed from the board cache (no extra API calls)
def _titles():
    for it in board_cache.get("items", []):
        t = re.sub(r"^GUI test squad( pilot)?: ", "", it.get("t") or ""); TITLE[it["n"]] = t   # the whole title: a row clamps it in CSS, and a truncated string cannot be widened back
def demoted(st, r, closed):
    """A failed / unverified / needs-human run whose every issue was closed afterwards (verified by hand, evidence on the issue) is no longer open business."""
    if st in ("failed", "unverified", "needs-human", "taken-over", "retired-age-cap") and r["issues"] and all(int(n) in closed for n in r["issues"]) and (r["land"] or st == "needs-human"): return "verified"
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
    row = next((x for x in RUN_STATES if x["state"] == st), None)
    color = row["color"] if row else "warn"
    mark = row["mark"] if row else "none"
    if st == "handled": text = "ended · handled (landed in fork)"
    elif st == "needs-human": text = "ended · needs a decision"
    else: text = row["text"] if row else st
    rc = r.get("exit_code")
    if rc is None and st in ("needs-human", "handled"): rc = 10
    title = f"exit {rc}" if rc is not None and st in ("needs-human", "handled") else ""
    return {"text": text, "color": color, "mark": mark, "title": title, "state": st}
LIVE_STATES = ("running", "starting", "stalled")
def group_of(r):
    """RUNNING holds a live process (running/starting/stalled). An ended run, needs-human included, goes to an age bucket."""
    if r.get("live", True) and r.get("state") in LIVE_STATES: return "running"
    h = (r.get("age") or 0) / 3600
    return "1h" if h <= 1 else "2h" if h <= 2 else "4h" if h <= 4 else "8h" if h <= 8 else "24h" if h <= 24 else "d"

STALL_SECS = 1800   # state_of: a run with no verdict and no event for this long reads `stalled`
RUN_LAUNCH_GRACE = 300   # a run with no call yet is a launch this long after its last write; squad/caps.py LAUNCH_GRACE, the janitor's rule-J bound
def run_gone(r, age, procs):
    """True when a run with no verdict has no process of its own: it was killed (a restart, a crash) and will never write a
    verdict, so it is not live, however young its record (#1226). The run's own process is the pid of its last `call_start` or
    `gate_start` after its last `run_start` or `resume` (r["pid"], the engine's os.getpid()); it counts while `procs`, the
    process reading {pid: start epoch or None}, names it, and a process that started after that call (r["pid_at"]) holds a
    reused pid and is not the run's. A process that only names the run folder (its dashboard server, a shell in its worktree)
    never counts. A run with no call yet is a launch: live while its record moved within RUN_LAUNCH_GRACE, which a run that
    waits for a slot keeps, since it writes `ceiling_wait` every minute. A run that waits for a gate slot after a call keeps
    its process. An empty reading is no evidence: nothing is called gone on it.
    Rule layer: no hub state, no I/O."""
    if r["verdict"] is not None or not procs: return False
    pid = r.get("pid")
    if not pid: return age >= RUN_LAUNCH_GRACE
    if pid not in procs: return True
    try: return not liveness().same_process(procs[pid], r.get("pid_at"))   # the one pid identity rule (squad/run_liveness.py)
    except Exception as e:
        log_once(f"hub: the run liveness reader is unavailable, no run is called gone: {type(e).__name__}: {' '.join(str(e).split())}")
        return False

def proc_starts(text):
    """The process reading of one `ps` (LC_ALL=C, pid and lstart first): {pid: start epoch, or None where the start does not
    parse}, parsed by the one reader (squad/run_liveness.py parse_ps)."""
    return {pid: p.start for pid, p in liveness().parse_ps(text).items()}

def procs_mod():
    """squad/procs.py, the one process reader (#1508). The tree this file sits in answers first, as caps.sh reads it: an
    SG_SQUAD checkout behind this change holds an older procs.py with no snapshot, and every table would read empty."""
    own = str(pathlib.Path(__file__).resolve().parents[3])
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    (PR,) = load_squad([own, *[r for r in roots if r != own]] if (pathlib.Path(own) / "squad" / "procs.py").is_file() else roots, ("procs",))
    return PR

PROCSCAN_SECS = 1.0   # the shared process snapshot's tick: every reader format in one second costs one read of the machine

def _machine_snapshot():
    """The machine's raw process table, from the one reader (squad/procs.py): libproc and sysctl, no exec, so a
    sandboxed child reads it too."""
    return procs_mod().snapshot()

def _same(snap):
    """The identity view: the raw table is already the format `process_table` answers."""
    return snap

def process_table(now=None):
    """{pid: Proc} for every process, from one read of the process table (no exec, so a sandboxed child reads it too).

    The read is shared for one second (`PROCSCAN_SECS`, squad/procscan.py `view`): `process_table`, `read_procs` and
    the two samplers each keep their own view of one machine read, so N readers in a tick cost one read. The cache is why
    a run process born inside the tick may read as gone for up to one second on the screen; the kill path (`_ps_rows`)
    reads fresh and never this, so nothing acted on is ever stale.
    {} when the read fails, logged once: a caller then sees no process, never a guessed one."""
    now = time.time() if now is None else now
    try:
        return procscan.view("table", _same, PROCSCAN_SECS, now, _machine_snapshot)[0]
    except Exception as e:
        log_once(f"hub: the process table could not be read: {type(e).__name__}: {' '.join(str(e).split())}")
        return {}

RUN_TABLE_SECS = 2.5   # the run readers' window: the page's live poll is every 3 s, so its own poll refills the scan before it expires

def run_table(now=None):
    """{pid: Proc} for every process, for the run readers: the run liveness, the gate slots and the loop rows all judge one
    read of the table (`RUN_TABLE_SECS`, squad/procscan.py `view`), so a page's poll costs one read however many of them
    it asks (#2582). It reads the same shared scan and the same view as `process_table` (`procscan.view("table", ...)`),
    so a tick that reads both pays one read of the machine; the window is the run readers' own, the longer one. The kill
    path (`_ps_rows`) reads fresh and never this. {} when the read fails, logged once: a caller then sees no process,
    never a guessed one."""
    now = time.time() if now is None else now
    try:
        return procscan.view("table", _same, RUN_TABLE_SECS, now, _machine_snapshot)[0]
    except Exception as e:
        log_once(f"hub: the process table could not be read for the run readers: {type(e).__name__}: {' '.join(str(e).split())}")
        return {}

def _starts(snap):
    """The {pid: start} reading of one process snapshot. An empty snapshot is no evidence, so it raises as the one reader
    does: the caller then calls no run gone."""
    if not snap: raise RuntimeError("cannot read the processes: no process named")
    return {pid: p.start for pid, p in snap.items()}

def read_procs(now=None):
    """The process reading the run list judges liveness on: every pid with its start. It derives from the same one
    snapshot `process_table` reads (`PROCSCAN_SECS`, squad/procscan.py `view`), so the run list costs no read of its own:
    N readers in a tick cost one read. A run process born inside the tick may read as gone for one second on the screen
    only; the kill path (`_ps_rows`) reads fresh. A read that fails is no evidence, logged once: the run list then calls
    no run gone (`run_gone` on an empty reading), never a guessed answer."""
    now = time.time() if now is None else now
    try:
        return procscan.view("procs", _starts, PROCSCAN_SECS, now, _machine_snapshot)[0]
    except Exception as e:
        log_once(f"hub: the process reading failed, no run is called gone on it: {type(e).__name__}: {' '.join(str(e).split())}")
        return {}

def gone_badge(b):
    """The badge of a run that is gone: the state stays (`stalled`), the text says why it is not live."""
    return {**b, "text": b["text"] + " · no process"}

def squad_roots(sg_squad, sg_checkout, checkout, here):
    """Where the squad package may live, in order, each once: the SG_SQUAD the caller names first (the hub passes
    SQUAD_ROOT, the value its profile file set at import; hubmux.py pins it there, #2820), then SG_CHECKOUT, the
    profile's checkout as the hub resolved it (the seam, ADR-0138), then the repository this file sits in (parents[3];
    the live copy under ~/.claude has none). Rule layer: no hub state, no I/O."""
    parents = pathlib.Path(here).resolve().parents
    out = []
    for r in (sg_squad, sg_checkout, checkout, str(parents[3]) if len(parents) > 3 else ""):
        if r and r not in out: out.append(r)
    return out

def _process_lock():
    """The one resolution lock this interpreter holds: hubmux.py loads hub.py once per profile into one process, so a
    module-level Lock leaves one lock per profile and none between them, and two instances purge and import the same
    `sys.modules` at once (#1215). The lock lives in a module object no `load_squad` purge touches, and `setdefault`
    makes its creation one answer for every instance that asks."""
    fresh = types.ModuleType("hub_process_lock"); fresh.LOCK = threading.Lock()
    return sys.modules.setdefault("hub_process_lock", fresh).LOCK
_SQUAD_LOCK = _process_lock()   # one resolution at a time across every profile: a purge another thread imports into is a crash

def _squad_names():
    """The `squad` keys `sys.modules` held at one instant. A plain copy is atomic under the GIL; reading the live dict while
    a second thread imports a module raises "dictionary changed size during iteration", and the live hub reported that as
    a resolution that failed while a profile loaded."""
    return [m for m in sys.modules.copy() if m == "squad" or m.startswith("squad.")]

def _bound(pkg, name, m):
    owner = pkg
    *path, leaf = name.split(".")
    for part in path:
        owner = getattr(owner, part, None)
        if owner is None: return False
    return getattr(owner, leaf, None) is m and getattr(getattr(m, "__spec__", None), "_initializing", True) is False

def _imported(names, mods):
    """True when each module finished its import: its package binds it, and its spec records the import system's finished
    mark (`_initializing` is False once the body has run; a bare module carries no such mark) (#2358)."""
    pkg = sys.modules.get("squad")
    return pkg is not None and all(_bound(pkg, n, m) for n, m in zip(names, mods))

def _held_squad(roots, names):
    """The modules this process already holds, when the first of them lives under the first asked root that holds a squad
    package of its own: the answer needs no purge, so no other thread's import is disturbed. A copy under a later root never
    answers for an earlier one that holds its own: the live tree's parent, ~/.claude, contains the plugin cache's copy, and
    that copy answered for the checkout that stood first (#2474). An empty answer sends the caller to the resolving path."""
    try:
        mods = tuple(importlib.import_module("squad." + n) for n in names)
    except Exception:
        return ()
    here = getattr(mods[0], "__file__", None)
    if not here or not _imported(names, mods): return ()
    for r in roots:
        if r and pathlib.Path(r, "squad", "__init__.py").is_file():
            return (mods,) if pathlib.Path(here).resolve().is_relative_to(pathlib.Path(r).resolve()) else ()
    return ()

def load_squad(roots, names=("caps", "run_caps")):
    """The `squad` modules `names` (default `squad.caps` and `squad.run_caps`) from the first root that holds the package, and only from that root: a package that
    some other entry of this machine's path happens to hold answers for no root (#452). A root that fails leaves nothing
    behind (not on sys.path, not in sys.modules), so the next one imports clean. Raises the last failure, which names what
    was missing.
    The resolution is one call's business: whatever answers, the caller's `squad` modules are the objects they were and its
    table gains none (#453). This module is read by a longer-lived interpreter too -- a suite that imports it into its own
    process -- and a purge that stayed there parted every class that process already held, so a raise it expected was a
    different class from the one it caught."""
    roots = [str(r) for r in roots if r]   # a caller's CHECKOUT may be a Path: the message joins strings and sys.path holds them
    with _SQUAD_LOCK:
        for mods in _held_squad(roots, names):
            return mods
        err = ModuleNotFoundError("no squad package in: " + ", ".join(roots))
        ambient = {m: sys.modules[m] for m in _squad_names() if m in sys.modules}
        for m in _squad_names(): sys.modules.pop(m, None)
        try:
            for root in roots:
                if not pathlib.Path(root, "squad", "__init__.py").is_file():
                    # A root that holds no package is skipped rather than imported against: the import would answer with a
                    # copy another root's parent happens to contain, and the message would name that copy as this root's
                    # (#2474: ~/.claude contains the plugin cache's old squad).
                    err = ModuleNotFoundError(f"{root} holds no squad package")
                    continue
                added = not (sys.path and sys.path[0] == root)   # the asked root goes first, else a squad earlier on sys.path answers for it
                if added: sys.path.insert(0, root)
                try:
                    mods = [importlib.import_module("squad." + n) for n in names]
                    here = getattr(mods[0], "__file__", None)   # the first module the import found: it must live under the root that was asked
                    if here and pathlib.Path(here).resolve().is_relative_to(pathlib.Path(root).resolve()) and _imported(names, mods): return tuple(mods)
                    err = ModuleNotFoundError(f"{root} holds no squad package (the one imported is {here})")
                except Exception as e:
                    err = e
                for m in _squad_names(): sys.modules.pop(m, None)
                if added: sys.path.remove(root)
            raise err
        finally:
            for m in _squad_names(): sys.modules.pop(m, None)
            sys.modules.update(ambient)

def liveness():
    """squad/run_liveness.py, the one reader of a run's liveness, from the roots the hub resolves every squad module from."""
    (RL,) = load_squad(squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), ("run_liveness",))
    return RL

def reaper():
    """squad/reap.py, whose `run_live` is the one predicate that says a held run has a live process (#1661), from the same roots."""
    (R,) = load_squad(squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), ("reap",))
    return R

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
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        C, RC = load_squad(roots)
        co = os.environ.get("SG_CHECKOUT") or CHECKOUT   # the profile's checkout is the fallback, never the process cwd
        c = C.ceiling(co)
        per_run = RC.read(co, [r.get("dir") for r in list(LOAD_RUNS) if r.get("dir")])
        return {"runs": c.caps["runs"], "children": c.caps["children"], "waiting": caps_waiting(C, c.folder), "per_run": per_run}
    except Exception as e:
        log_once(f"hub: loop_caps unavailable, the footer shows plain counts: {type(e).__name__}: {' '.join(str(e).split())} (roots tried: {', '.join(roots)})")
        return None

def caps_waiting(C, folder):
    """The live waits for a slot, each {ceiling, holder}, read and never written: the hub observes the ceiling (ADR-0162). The
    caps module's own count (`Ceiling.waiting`) removes the lease of every dead holder as it counts, which is a write, so the
    hub reads the wait marks itself and asks `C.alive` about their pids, one `ps` for all of them (`C.started_at`)."""
    marks = []
    for p in sorted((pathlib.Path(folder) / "waiting").glob("*.json")):
        try: m = json.loads(p.read_text())
        except (OSError, ValueError): continue
        if isinstance(m, dict): marks.append(m)
    started = C.started_at([m.get("pid", -1) for m in marks])
    return [{"ceiling": m.get("ceiling"), "holder": m.get("holder")} for m in marks if C.alive(m.get("pid", -1), m.get("since"), started)]

def waiting_txt(caps):
    """What waits for a slot, named: ` · waiting: <holder> (caps.runs)`. Empty when nothing waits."""
    w = (caps or {}).get("waiting") or []
    return " · waiting: " + ", ".join(f"{x['holder']} ({x['ceiling']})" for x in w) if w else ""

def usd_txt(v):
    if v is None: return "—"
    if v >= 100: return "$" + str(int(round(v)))
    return f"${v:.2f}"

def group_header(kind, rows, caps):
    """RUNNING shows occupancy against the machine-wide ceiling, runs then agents, in sentence case, and names what waits;
    other groups keep count · cost. Spend is the tooltip. The childcc pass is a worker, never a run: it is drawn in its own
    row and never counted."""
    n = len([r for r in rows if r.get("kind") != "childcc"])
    spend = sum((r.get("spend") or 0) for r in rows)
    agents = sum((r.get("agents") or 0) for r in rows)
    title = usd_txt(spend)
    if kind == "running":
        per_run = ((caps or {}).get("per_run") or {}).get("label")
        if per_run: title += " · " + per_run
        if caps and isinstance(caps.get("runs"), int) and isinstance(caps.get("children"), int):
            return {"text": f"Runs {n}/{caps['runs']} · Agents {agents}/{caps['children']}{waiting_txt(caps)}", "title": title}
        return {"text": f"{n} · {agents}", "title": title}
    return {"text": f"{n} · {title}", "title": title}

def live_run_rows(rows):
    """The live runs the Runs heading and the Now tile both count (SPEC 2498, #2502): a run with a live process. The
    childcc pass is a worker and a hand hold is work the operator does, so neither is a live run, and one count serves
    both readers."""
    return [r for r in rows if r.get("kind") not in ("childcc", "hold") and group_of(r) == "running"]

def run_groups(rows, caps=None):
    """The Live group's header: its key, label, the text and title group_header builds, and `empty` (#1778). The page draws
    what it is given, so the occupancy's wording lives here alone (#1277). The rows are run_list's, each carrying `state`,
    `live`, `rank` and `age`. The childcc pass is a worker, never a run: group_header never counts it."""
    held = [r for r in rows if r.get("kind") == "childcc"] + live_run_rows(rows)
    h = group_header("running", held, caps)
    return [{"key": "running", "label": "WORKERS", "text": h["text"], "title": h["title"], "empty": not held}]
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

LOAD_STATE = {"cpu": [], "ram": None, "gpu": None, "asof": 0.0, "runs": {},
              "record_at": None, "record_cpu_sum": 0.0, "record_cpu_n": 0, "record_rows": None, "record_stamp": None}
              # the minute record's last line, the running sum and count of the ticks since it, and the rows and file
              # identity the view folds (#1768): a sum and a count, never a list that grows with the tick rate
LOAD_RUNS = []            # the light rows the last /api/runs served: id, folder and worktrees, for owners_of
_PROC_PREV, _PROC_AT = None, 0.0

def load_tuples(snap):
    """pid -> (ppid, CPU seconds, argv) for the load attribution, from the one process snapshot of the tick. Only the
    sampler's scan reads this, and only while the machine is warned."""
    return {pid: (p.ppid, p.cpu, p.command) for pid, p in snap.items()}

def proc_snapshot(now=None):
    """The process table of this tick, read once for every profile's hub in this interpreter (#1746): hubmux loads one
    hub.py per profile and each runs its own sampler, so an unshared read scanned the machine's table once per profile
    per tick. `procscan` keeps the scan for one tick, and the tick is `LOAD_SAMPLE_SECS` long. Its view is the tuple
    format the attribution reads, derived from the same snapshot every reader shares (#1848)."""
    now = time.time() if now is None else now
    return procscan.view("load", load_tuples, LOAD_SAMPLE_SECS, now, process_table)

def profiling_table(now=None):
    """The process table of this profiler tick, read once for every profile of this interpreter (#1746, #1790): three
    profile samplers at one reading a second each scanned the machine three times a second. The tick is PROFILER_SECS, so
    each profile still folds a reading of its own interval. Its view is the process table itself, derived from the same
    snapshot the runs list reads (#1848)."""
    now = time.time() if now is None else now
    return procscan.view("profiler", _same, PROFILER_SECS, now, process_table)[0]

def attribute_pass(now=None):
    """CPU per run over the last interval, as a share of the machine. The sampler calls it only while the machine is
    warned: a calm machine never builds a process table. The window runs from the instant of the snapshot it compares
    against, because a tick may serve this profile a scan another profile made a moment earlier."""
    global _PROC_PREV, _PROC_AT
    now = time.time() if now is None else now
    cur, at = proc_snapshot(now)
    prev, _PROC_PREV = _PROC_PREV, cur
    window = at - (_PROC_AT or at); _PROC_AT = at
    if not prev or window <= 0: return {}
    ncpu = os.cpu_count() or 1
    return {k: share_pct(v, window, ncpu) for k, v in attribute(prev, cur, owners_of(LOAD_RUNS)).items()}

# The minute record (#1768): the machine's load average and CPU share, one line a minute, in the profile's state folder.
# The Loop tab's chart reads it on the strip's own window. It is this monitor's record, never the Loop tab's: it is
# written with the CPU sampling switch on or off. Every write replaces the whole file — a read, a join and one atomic
# write of at most LOAD_RECORD_MAX_LINES lines, a cost bounded with the file — and it is synced before it is visible
# (ADR-0030), so an interrupted hub leaves the record as it was, never a partial line.
LOAD_RECORD_SECS = hub_profiler.LOAD_CADENCE_S   # the record's cadence: one line per minute, each on its minute
LOAD_RECORD_MAX_AGE = 24 * 3600           # the largest strip window
LOAD_RECORD_MAX_LINES = LOAD_RECORD_MAX_AGE // LOAD_RECORD_SECS   # 1440: the age in lines, so a shorter cadence or a
                                          # clock that steps back can never grow the file past the age it was read for

def load_record_path():
    """The minute record's file, resolved when it is used: hubmux gives each profile its own STATE, and a resolution per
    call keeps each profile's line in its own state folder (#1768)."""
    return STATE / "load-record.tsv"      # one line a minute: `<epoch> TAB <load average> TAB <CPU share> TAB <memory share>`

def _write_record(path, text):
    """Write `text` to `path` so a reader sees the old file or the whole new one, and a power cut leaves one of the two:
    the temp file is synced, renamed, and then the folder is synced (ADR-0030). A failed rename leaves no temp behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_name(path)
    try:
        with open(tmp, "wb") as f:
            f.write(text.encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try: tmp.unlink()
        except OSError: pass
        raise
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def _loadavg():
    """The one-minute load average, or None where the system does not answer: a missing value is no zero."""
    try: return float(os.getloadavg()[0])
    except (AttributeError, OSError): return None

def load_average(now=None):
    """The one-minute load average, read once a tick for every profile of this hub: `procscan` keeps the read for one
    tick as it keeps the process table, so three profiles cost one read. The minute record adds no reader of its own."""
    now = time.time() if now is None else now
    return procscan.table(_loadavg, LOAD_SAMPLE_SECS, now)[0]

def _minute(t):
    return t - t % LOAD_RECORD_SECS

def _record_tail(limit):
    """The newest `limit` complete lines of the record, read back from its end in blocks, never the whole file."""
    path = load_record_path()
    try:
        with open(path, "rb") as f:
            pos = f.seek(0, os.SEEK_END)
            data = b""
            while pos > 0 and data.count(b"\n") <= limit:
                step = min(4096, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if pos > 0 and lines: lines = lines[1:]   # the first line of a cut is half a line
    return lines[-limit:]

def load_record_add(minute, load, cpu, ram=None):
    """Append the minute's line, keep the lines inside the age and the cap, and return the rows, in one atomic write."""
    keep = {}
    for raw in _record_tail(LOAD_RECORD_MAX_LINES - 1):
        fields = raw.split("\t")
        try: at = float(fields[0])
        except (IndexError, ValueError): continue
        if minute - LOAD_RECORD_MAX_AGE <= at < minute: keep[at] = raw
    keep[minute] = "\t".join((f"{minute:.3f}", f"{load:.2f}", "-" if cpu is None else f"{cpu:.1f}",
                              "-" if ram is None else f"{ram:.1f}"))
    lines = sorted(keep.items())[-LOAD_RECORD_MAX_LINES:]
    _write_record(load_record_path(), "\n".join(raw for _, raw in lines) + "\n")
    return [hub_profiler.load_row(raw) for _, raw in lines]

def load_record_warm(now):
    """Read the record once on a cold state: the rows not after `now`, and the minute of the last one."""
    rows = [r for r in load_record_rows() if r["at"] < now]
    LOAD_STATE["record_rows"], LOAD_STATE["record_stamp"] = rows, load_record_stamp()
    return max((_minute(r["at"]) for r in rows), default=None)

def _record_last(now):
    if LOAD_STATE.get("record_at") is None:
        LOAD_STATE["record_at"] = load_record_warm(now)
    return LOAD_STATE["record_at"]

def load_record_due(now):
    """True when the minute of `now` has no line yet: the one tick whose load the record takes."""
    last = _record_last(now)
    return last is None or _minute(now) > last

def load_record_tick(sample, now, load=None):
    """Keep the tick's CPU in a running mean, and write the minute's line on the tick that turns the minute."""
    if sample.get("cpu") is not None:
        LOAD_STATE["record_cpu_sum"] = LOAD_STATE.get("record_cpu_sum", 0.0) + sample["cpu"]
        LOAD_STATE["record_cpu_n"] = LOAD_STATE.get("record_cpu_n", 0) + 1
    last = _record_last(now)
    minute = _minute(now)
    if last is not None and minute <= last: return
    cold = last is None or minute - last > LOAD_RECORD_SECS
    n = LOAD_STATE.get("record_cpu_n", 0)
    cpu = None if cold else (round(LOAD_STATE.get("record_cpu_sum", 0.0) / n, 1) if n else None)
    rows = None
    if load is not None:
        try: rows = load_record_add(minute, load, cpu, sample.get("ram"))
        except OSError: rows = None   # a gap, never a broken sampler
    LOAD_STATE["record_at"], LOAD_STATE["record_cpu_sum"], LOAD_STATE["record_cpu_n"] = minute, 0.0, 0
    if rows is not None:
        LOAD_STATE["record_rows"], LOAD_STATE["record_stamp"] = rows, load_record_stamp()

def load_tick(sample=None, now=None, load=None):
    """One sampler pass: publish the reading, keep the warn history, and take the minute record on its turn."""
    s = sample if sample is not None else sample_load()
    now = time.time() if now is None else now
    LOAD_STATE["cpu"] = (LOAD_STATE["cpu"] + [s["cpu"]])[-LOAD_HISTORY:]
    LOAD_STATE["ram"], LOAD_STATE["gpu"], LOAD_STATE["asof"] = s["ram"], s["gpu"], now
    LOAD_STATE["runs"] = attribute_pass(now) if sustained(LOAD_STATE["cpu"]) else {}
    due = load_record_due(now)
    if due and load is None: load = load_average(now)
    load_record_tick(s, now, load if due else None)
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
#   the age of the oldest card in a column    -> the column header (`Reviewing (3) · 2h`), one number per column
#   fork-ahead / branches waiting / last      -> a left-pane accordion; `fork-parked` already reaches the footer
#   base-test duration                           through `health`, and the three reserved here take the fourth accordion
#                                                when they arrive: the numbering here and the page's `ACC` are one list
# Two facts left the line by (b) and (c) together, and each is drawn one screen away (#1797, the owner's ruling 2026-10-16):
# the runs and the agents the line counted are the WORKERS header's own reading (`Runs n/10 · Agents n/20 · waiting: …`,
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
# The gate queue earns the line beside the gate (#1173): `queue 2 held · gate lock 1 · gate slots 1 · oldest 2h ago`. It
# answers the question the gate block raises the moment a gate is slow — who waits behind it, and for how long — and it is
# one reading the operator acts on within the minute. Its figures are the issue ledger's open hold episodes, which are the
# one record of a hold (ADR-0149): the hub keeps no count of its own and reads the episodes once per refresh, so a queue of
# twenty children costs the same one read as a queue of one. An empty queue reads `queue empty`; a ledger the hub cannot
# read reads `queue ?`, never either a count or an empty.
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
FOOT_BLOCK_COLORS = {"burn": "err", "landing": "wait", "gate": "none", "queue": "wait", "nightly": "run",
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
    """A span in the page's own `span()` shape: `3d 2h`, `2h 14m`, `5m` — the same three rungs, never a fourth. A countdown
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

QUEUE_CAUSES = (("the gate lock", "gate lock"), ("the gate slots", "gate slots"))   # the causes wave.sh holds a child on (#1173)
QUEUE_CAUSE_MAX = 28   # the characters any other cause keeps on the line: the hold's first clause, cut here
QUEUE_TIP_MAX = 6      # the waiting children the tooltip names before it counts the rest

def _queue_cause(cause):
    """The queue's short name for a hold cause. `gate lock` and `gate slots` are the gate's own words, so the line reads the
    two the operator knows (wave.sh's hold). Any other cause keeps its first clause, cut to QUEUE_CAUSE_MAX; a cause the
    ledger does not spell reads `?`, never a guessed name."""
    c = " ".join(str(cause or "").split()).split(";")[0].strip()
    for lead, name in QUEUE_CAUSES:
        if c.startswith(lead): return name
    return c[:QUEUE_CAUSE_MAX].rstrip() or "?"

def _queue_parts(q):
    """`queue 2 held · gate lock 1 · gate slots 1 · oldest 2h ago`: the children that wait in the gate queue, counted on
    their causes, and the age of the oldest open episode. The figures ARE the ledger's episodes, from one reading of them
    (#1173, ADR-0149): the hub keeps no count of its own and makes no request per child. An empty queue reads `queue
    empty`, which is a reading; a ledger the hub cannot read is `queue ?` in the neutral tone, never empty. A queue is no
    state signal, so the block carries no tone. The tooltip names each waiting child with its cause and age, which is how
    the children are MOVED off the line and never lost (the section's rule (b))."""
    if not isinstance(q, dict) or not q.get("read"): return [{"text": "queue ?", "tone": FOOT_NEUTRAL}]
    eps = q.get("episodes") or []
    if not eps: return [{"text": "queue empty", "tone": ""}]
    counts = {}
    for e in eps:
        name = _queue_cause(e.get("cause"))
        counts[name] = counts.get(name, 0) + 1
    causes = FOOT_SEP.join(f"{name} {k}" for name, k in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    oldest = max(float(e.get("age") or 0.0) for e in eps)
    names = [f"{e.get('key')} {_queue_cause(e.get('cause'))} {_age(e.get('age') or 0.0)} ago" for e in eps[:QUEUE_TIP_MAX]]
    rest = len(eps) - QUEUE_TIP_MAX
    tip = FOOT_SEP.join(names) + (f" +{rest} more" if rest > 0 else "")
    return [{"text": f"queue {len(eps)} held{FOOT_SEP}{causes}{FOOT_SEP}oldest {_age(oldest)} ago", "tone": "", "tip": tip}]

NIGHTLY_MODES = ("pass", "fail")
NIGHTLY_FAILED = re.compile(r"fail(?::signal-\d+)?")   # a red leg, naming the signal that also stopped it (#1183)
NIGHTLY_BOUNDED = re.compile(r"bounded:(?:(?:signal|exit)-\d+|window)")   # window: an hour that closed with no run (#1975)   # a leg that ended without naming a failing test (#1183)

def _nightly_mode(v):
    """A nightly record's mode field: `pass`, `fail`, `fail:signal-<n>` for a red leg a signal also stopped, or
    `bounded:<signal|exit>-<n>` for a leg that measured nothing (a suite a signal killed, or a command that ended non-zero
    and named no test). Anything else is no reading."""
    return isinstance(v, str) and (v == "pass" or NIGHTLY_FAILED.fullmatch(v) is not None
                                   or NIGHTLY_BOUNDED.fullmatch(v) is not None)

def _nightly_red(v):
    """A mode a failing test made red: `fail`, or `fail:signal-<n>` when a signal also stopped that leg. A red holds
    delivery whatever else stopped it; the tag only carries the stop reason."""
    return isinstance(v, str) and v.startswith("fail")

def _nightly_fold(parallel, serial):
    """The one result two modes make: a pass only when both pass, a fail when either named a failing test, and `bounded`
    when a leg was stopped before it measured anything and none was red (#1183)."""
    if _nightly_red(parallel) or _nightly_red(serial): return "fail"
    if parallel == "pass" and serial == "pass": return "pass"
    return "bounded"

def _nightly_shown(n):
    """The result the line shows, and the GitHub half's tooltip phrase (#1975, ADR-0153 rule 1d). The line reads the one
    verdict, never the local record alone, and the tooltip names a GitHub half that did not pass: a local red is fail; a GitHub red at the record's sha is fail; a local pass is
    pass only when the verdict at the record's sha is pass, and unproven otherwise; a bounded local half stays bounded."""
    v = n.get("verdict")
    same = isinstance(v, dict) and v.get("sha") == n["sha"]
    gh = f"github {v.get('github')}" if same and v.get("github") != "pass" else ""   # a green GitHub half adds no words
    if n["result"] == "fail" or (same and v.get("result") == "fail"): return "fail", gh
    if n["result"] == "pass": return ("pass" if same and v.get("result") == "pass" else "unproven"), gh
    return n["result"], gh

def _nightly_parts(n, now):
    """`nightly pass 6h ago`: the last full suite in both modes (nightly.sh, #704), its result and its age. A pass reads in
    the ok tone, a bounded attempt in the warn tone -- the suite never finished, so the tip is unproven, never red (#1183,
    ADR-0164) -- and a fail in the err tone; a record the hub cannot read is `nightly ?` in the neutral tone, never a pass.
    No record at all draws no block.
    The tooltip carries which mode failed, which was stopped, and the sha it ran at. The operator acts on the test ids, so
    every one of them is carried: the tooltip names them all, and `detail` gives the page a list a keyboard and a screen
    reader can open. The line itself stays one compact reading — the ids are MOVED off it, never dropped from the hub, as
    the gate's sha is (the section's rule (b))."""
    if n is None: return []
    modes = NIGHTLY_MODES + ("bounded",)
    ok = isinstance(n, dict) and n.get("result") in modes and n.get("sha") \
        and _nightly_mode(n.get("parallel")) and _nightly_mode(n.get("serial")) \
        and isinstance(n.get("ts"), (int, float)) and not isinstance(n.get("ts"), bool) \
        and isinstance(n.get("ids"), list) and n["result"] == _nightly_fold(n["parallel"], n["serial"]) \
        and (n["result"] != "bounded" or not n["ids"])
    if not ok: return [{"text": "nightly ?", "tone": FOOT_NEUTRAL}]
    shown, gh = _nightly_shown(n)
    text = f"nightly {shown} {_age(max(now - n['ts'], 0.0))} ago"
    tip = [f"parallel {n['parallel']} · serial {n['serial']} · ran at {str(n['sha'])[:7]}"]
    if gh: tip.append(gh)
    ids = [str(x) for x in n["ids"] if x]
    part = {"text": text, "tone": {"pass": "ok", "bounded": "warn", "unproven": "warn"}.get(shown, "err")}
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
    """The time to a reset in the page's own `span()` shape (`3h 20m`), without its `resets in` lead — the token already
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

