#!/usr/bin/env python3
"""Launch one squad run fully detached. The run's dashboard server starts (the Squad hub lists it) but no browser tab opens:
SQUAD_HEADLESS=1 (owner, 2026-10-09: "nao abra mais o dashboard, vamos usar apenas o Squad-Hub"). Never open a dashboard tab.
usage: launch.py <request-file>   (run from the target checkout, normally $SG_CHECKOUT)
Prints the run id. nohup+disown dies with the Bash task; a new session survives.
Before launching it checks the workflow graph: a root that is not $SG_ROOT_NAME is renamed (root and node name together) and validated.
While waiting for the run folder it watches the child: if squad exits first, it fails at once with the log tail instead of waiting 3 min."""
import json, os, pathlib, re, secrets, socket, subprocess, sys, time

here = pathlib.Path(__file__).resolve().parent
if not os.environ.get("SG_SQUAD"):   # env.sh not sourced (SG_PROFILE alone selects a profile, it does not load it): pick up its SG_* values, else the run silently uses the installed plugin instead of the profile's fork
    out = subprocess.run(["bash", "-c", f'. "{here}/env.sh" >/dev/null && env'], capture_output=True, text=True).stdout
    os.environ.update(dict(l.split("=", 1) for l in out.splitlines() if l.startswith("SG_") and "=" in l))
squad = os.environ.get("SG_SQUAD") or json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))["plugins"]["agent-squad@agent-squad"][0]["installPath"]
def _names_option(name):   # a probe of the build that starts, never this launcher: SG_SQUAD or the installed plugin can be older than this tree
    try: return name in (pathlib.Path(squad) / "squad" / "options.py").read_text()
    except OSError: return False
state = pathlib.Path(os.environ["SG_STATE"])
state.mkdir(parents=True, exist_ok=True); (state / "cq").mkdir(exist_ok=True)
req_file = pathlib.Path(sys.argv[1]).resolve(); cwd = pathlib.Path.cwd().resolve(); runs = cwd / "runs"

import importlib.util   # provkey.py sits beside this script: a model whose provider needs an absent key is closed (#482)
_pk_spec = importlib.util.spec_from_file_location("provkey", here / "provkey.py")
provkey = importlib.util.module_from_spec(_pk_spec); _pk_spec.loader.exec_module(provkey)
_mr_spec = importlib.util.spec_from_file_location("modelreg", here / "modelreg.py")   # the quota cache closes a spent subscription (#678)
modelreg = importlib.util.module_from_spec(_mr_spec); _mr_spec.loader.exec_module(modelreg)

# the coding model comes from models.json (the hub's "squad models" panel), never from memory or a doc
mdoc = json.load(open(here / "models.json"))
def _chain(name):   # one chain's entries, read through the resolver: a v1 or a v2 models.json (ADR-0147)
    return modelreg.chain(mdoc, name.removesuffix("_models")).entries
def _row(chain, mid):
    return next((e for e in _chain(chain) if e.raw == mid), None)
def _in_hours(chain, mid):   # mirrors hub.py's in_hours: the model's own window, read by the resolver's grammar, "" = always
    row = _row(chain, mid); w = row.hrs if row else None
    if not isinstance(w, str) or not modelreg.window_valid(w): return True
    return modelreg.in_window(w, time.time())
def _spent(mid):   # #508: the subscription is spent until the marker's time (codex-limited for gpt-, agy-limited for gemini-), as mkreq.sh reads it
    if provkey.closed(mid): return True   # #482: no key is a provider that never answers, so it is closed like a spent one
    f = "codex-limited" if "gpt-" in mid else "agy-limited" if "gemini-" in mid else None
    try: return bool(f and os.environ.get("SG_SHARED")) and time.time() < int((pathlib.Path(os.environ["SG_SHARED"]) / f).read_text().strip())
    except (OSError, ValueError): return False
def _may_run(chain, mid):   # mirrors hub.py's may_run: the panel's switch first, then the model's own window, then its subscription
    row = _row(chain, mid)
    return (row is None or row.on) and _in_hours(chain, mid) and not _spent(mid)
cm = [e.raw for e in _chain("coding_models")]
# the model the request must name is the first of the chain that may run now: a window in the hub's models panel
# (e.g. claude-sonnet-5-5 at night) is honoured here, and so is the row's switch, and the reason is printed when one
# is skipped. Every entry off or out of window is the same refusal: no model may run now.
primary = next((m for m in cm if _may_run("coding_models", m)), None)
if cm and not primary and not os.environ.get("SG_ALLOW_MODEL"):
    sys.exit(f"every coding model is switched off, or outside its `hours` window, in models.json right now ({cm}): "
             "put one back in the chain (or widen its window) in the hub's models panel, or set SG_ALLOW_MODEL=1 to override.")
# The "Coding model:" line is the one that decides what the child runs, so it is the line this gate reads: a substring
# test over the whole request passed whenever the body merely mentioned the primary elsewhere, which let a request name an
# out-of-window model (an Anthropic one during the day) and still clear the hours gate.
txt = req_file.read_text()
m = re.search(r"^Coding model:\s*(\S+)", txt, re.M)
named = m.group(1) if m else None
if primary and not os.environ.get("SG_ALLOW_MODEL"):
    why = f"coding_models[0:] = {cm}; no window in models.json contains the hour now" if not primary else \
          f"models.json coding_models, first whose `hours` window contains the hour now (fallback {cm[cm.index(primary) + 1] if cm.index(primary) + 1 < len(cm) else 'none'})"
    if named and named != primary:
        sys.exit(f"request names the coding model {named!r}, but the model that may run now is {primary!r} ({why}). "
                 "Name that one, or set SG_ALLOW_MODEL=1 for a deliberate exception.")
    if not named and primary not in txt:
        sys.exit(f"request does not name the configured coding model {primary!r} ({why}). "
                 "Fix the request, or set SG_ALLOW_MODEL=1 for a deliberate exception.")

# The switch above only guarded the request's "Coding model:" line; the nodes of the graph run another value (#60): the router
# pins `opus`, a child with no model of its own takes `allow.models[0]`, and `claude-sonnet-5-5` is the defaults file's own. So the
# switch is also read against the model each node of the graph this run starts runs: an alias is resolved to its provider id and
# the node is refused when the panel switched that id off in any chain it sits in. The alias table mirrors the CLI's own.
ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5-20251001"}
def _off_ids():   # {bare model id: chain}: hub.py's model_off (the switch counts when the model is in the chain); review_models spells an Anthropic id `claude:<id>`
    out = {}
    for chain in ("coding_models", "review_models", "vision_models"):
        for e in _chain(chain):
            if not e.on: out.setdefault(e.raw.removeprefix("claude:"), chain)
    # a model outside its `hours` window is closed the same way: the router and the dispatch fallbacks must not pick it
    for chain in ("coding_models", "review_models", "vision_models"):
        for e in _chain(chain):
            if e.hrs and not _in_hours(chain, e.raw): out.setdefault(e.raw.removeprefix("claude:"), chain + " hours window")
    return out
def _graph_path(workflow):   # mirrors squad/workflows.py resolve: no id, `default` or `dynamic` is the owner's default file, else the dynamic seed
    folder = cwd / "docs" / "squad" / "workflows"; sq = pathlib.Path(squad)
    if workflow in (None, "", "default", "dynamic"):
        return next((p for p in (folder / "default.graph.json", folder / "dynamic.graph.json") if p.is_file()), sq / "docs" / "design" / "router.graph.json")
    mine = folder / f"{workflow}.graph.json"
    return mine if mine.is_file() else sq / "docs" / "workflows" / f"{workflow}.graph.json"
def _dispatch_models(n):   # every model a dispatch node may hand a child, in the order it reads them
    allow = n.get("allow") or {}
    kinds = allow.get("kinds")
    if kinds:   # SPEC 750: the envelope names kinds, and each kind's models are its chain in models.json
        return [e.raw for k in kinds for e in _chain(f"{k}_models")]
    return list(allow.get("models") or [])
def _run_models(doc):   # [(node, model as the graph names it, first: whether it is the one the node runs first)]: an agent's own model; a dispatch's child default and fallbacks
    for n in doc.get("nodes", []):
        if n.get("kind") == "agent" and n.get("model"): yield n["name"], n["model"], True
        if n.get("kind") == "dispatch":
            for i, m in enumerate(_dispatch_models(n)): yield n["name"], m, i == 0
# A shared marker (`codex-limited` for `gpt-`, `agy-limited` for `gemini-`) names a subscription that is spent until its epoch. The
# engine's chain waits for that provider's refusal before it moves on, so the ids the graph runs on a spent provider go to the run
# as `--spent-models` (ADR-0106): the chain leaves them out. A marker with no readable epoch ahead of now closes nothing.
SPENT_MARKERS = (("gpt-", "codex-limited"), ("gemini-", "agy-limited"))
def _spent_models(doc, shared, now=None):
    now = time.time() if now is None else now
    def closed(name):
        try: return float((pathlib.Path(shared) / name).read_text().strip()) > now
        except (OSError, ValueError): return False
    ids = {ALIASES.get(m, m) for _, m, _ in _run_models(doc)}
    return sorted({m for m in ids for prefix, name in SPENT_MARKERS if shared and m.removeprefix("claude-").startswith(prefix) and closed(name)}
                  | {m for m in ids if provkey.closed(m)}   # #482: a model with no key leaves the chain the same way
                  | set(modelreg.spent(sorted(ids), shared=shared, now=now) if shared else ()))   # #678: a marker or a spent quota window
off_ids = _off_ids()
if off_ids:
    try: gdoc = json.loads(_graph_path(os.environ.get("SG_WORKFLOW")).read_text())
    except (OSError, ValueError): gdoc = {}   # an absent or malformed graph is the squad's own refusal to make, not this gate's
    hits = [(nm, m, ALIASES.get(m, m), off_ids[ALIASES.get(m, m)], first) for nm, m, first in _run_models(gdoc) if ALIASES.get(m, m) in off_ids]
    # A dispatch whose first model is only outside its hours window, with an open model after it, starts: the request names
    # the first open coding model (mkreq.sh) and the router takes it (#433). Refused, no run started all night (#522).
    open_in = {n["name"] for n in gdoc.get("nodes", []) if n.get("kind") == "dispatch"
               and any(ALIASES.get(m, m) not in off_ids for m in _dispatch_models(n))}
    hits = [h[:4] + (False,) if h[4] and h[0] in open_in and h[3].endswith("hours window") else h for h in hits]
    said = lambda h: f"node {h[0]!r} runs {h[1]}{'' if h[1] == h[2] else f' -> {h[2]}'}, which the models panel switched off, or whose hours window excludes now, in models.json ({h[3]})"
    for h in hits:
        if not h[4] or os.environ.get("SG_ALLOW_MODEL"): print(f"warning: {said(h)}" + ("" if h[4] else " (a fallback of the dispatch)"), file=sys.stderr)
    if any(h[4] for h in hits) and not os.environ.get("SG_ALLOW_MODEL"):
        sys.exit("refusing to start: " + "; ".join(said(h) for h in hits if h[4]) + ". Switch the model on in the hub's models panel, "
                 "or change the model in the workflow graph, or set SG_ALLOW_MODEL=1 for a deliberate exception.")

# The router only asks a child for a model in the dispatch envelope, and a request outside it is refused whole, so the router
# takes an allowed id and the "Coding model:" line loses in silence (#433). Say so by name. A blocked fallback only warns; a
# blocked first coding model refuses the start (#778), since the run would spend on a model the coding chain does not name.
if primary:
    import importlib.util   # the shared envelope module sits beside this script, as hub.py's does (#436)
    _spec = importlib.util.spec_from_file_location("router_envelope", here / "router_envelope.py")
    router_envelope = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(router_envelope)
    try: _env = router_envelope.envelope_models(json.loads(_graph_path(os.environ.get("SG_WORKFLOW")).read_text()))
    except (OSError, ValueError): _env = []
    for _m in router_envelope.envelope_blocked([m for m in cm if _may_run("coding_models", m)], _env):   # every eligible model, the primary and each fallback
        print(f"warning: {_m} is not in the router envelope (allow.models of the dispatch node: {', '.join(_env)}); the router cannot request it, "
              + ("so the child runs an allowed model instead of the Coding model line" if _m == primary else "so this fallback is never requested")
              + ". Add it to the dispatch node's `allow.models` in the workflow graph, or change the coding chain.", file=sys.stderr)
    if primary in router_envelope.envelope_blocked([primary], _env) and not os.environ.get("SG_ALLOW_MODEL"):
        sys.exit(f"refused: the first coding model {primary} is not in the router envelope, so the router would run another model. "
                 "Add it to the dispatch node's `allow.models` in the workflow graph, use an envelope of `allow.kinds`, change the coding chain, "
                 "or set SG_ALLOW_MODEL=1 for a deliberate exception.")

co = os.environ.get("SG_CHECKOUT")
if co and pathlib.Path(co).resolve() != cwd and not os.environ.get("SG_ALLOW_OTHER_CWD"):
    sys.exit(f"launch from $SG_CHECKOUT ({co}), not {cwd}: watch.sh, heartbeat.sh and the hub look for runs there. "
             "Set SG_ALLOW_OTHER_CWD=1 to override (the run is then tracked by its absolute path).")

# ADR-0106 rule 4: the engine commits a passing node's target only when the run owns it, a linked worktree that holds the
# run folder. A loop child works on the branch the land reads, so the run this starts for it has that issue worktree as its
# target and the run folder lives inside it (#93). Left on the owner checkout, the target is refused as `owner_checkout` and
# the child's work sits in a worktree outside the run area, so a person had to commit it.
# #580: no run targets the owner's checkout. The engine cuts children from its target HEAD and merges passed children back into
# it, so a checkout target collected every unlanded or rejected merge, and the next run inherited them. A run with no issue
# worktree gets its own linked worktree, cut from origin/$SG_BASE as fetched now; a fetch that fails refuses the start.
FALLBACK = "its own run worktree"   # the target a run gets when no issue worktree is resolved

# #1829: the rule that says which issues a live run holds is one module (liveholds.py), read here and by the dispatchers
# (startability.sh). The start enforced one answer and the frontier read a weaker one, so one issue was sent out every
# cycle and refused every time (ADR-0149: one writer per loop fact).
_lh_spec = importlib.util.spec_from_file_location("liveholds", here / "liveholds.py")
liveholds = importlib.util.module_from_spec(_lh_spec); _lh_spec.loader.exec_module(liveholds)
_issue_named = liveholds.issue_named
_issue_child_request = liveholds.issue_child_request

def _wave_request(txt):
    """The request of a run whose children enter a wave (#375, ADR-0145 rules 9 and 10): a child keeps its branch local, and the
    loop's child pass (`childcc.py`) enters its finished commits into the run's wave branch, which the run's completion lands
    in the base with one push. The dispatch builders still write "push your branch" into every request, so the launch is where
    the sentence is made true; no child branch ever reaches the remote."""
    txt = re.sub(r"\bpush your branch\b", "keep your branch local (never push it)", txt)
    return txt.rstrip("\n") + "\n\nWAVE: your branch stays local. The loop enters your finished commits into the run's wave branch and lands the wave in " \
        + (os.environ.get("SG_BASE") or "the base branch") + " with one push; push no branch of yours.\n"

def _landing_base(co):
    """The commit sha of `origin/<SG_BASE>` (default main), fetched now: the branch the wave is cut from and lands in, so a run
    starts where its work lands (#469), never on a stale local branch. A fetch that fails, or a remote with no such branch,
    refuses the start (#580): an old remote-tracking ref or the local HEAD is never the base."""
    base = os.environ.get("SG_BASE") or "main"
    try: f = subprocess.run(["git", "-C", co, "fetch", "-q", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired: sys.exit(f"refusing to start: the fetch of origin/{base} in {co} timed out; a run never starts from a local branch (#580)")
    if f.returncode: sys.exit(f"refusing to start: cannot fetch origin/{base} in {co}: {f.stderr.strip()}; a run never starts from a local branch (#580)")
    r = subprocess.run(["git", "-C", co, "rev-parse", "--verify", "--quiet", f"origin/{base}^{{commit}}"], capture_output=True, text=True)
    if r.returncode or not r.stdout.strip(): sys.exit(f"refusing to start: origin/{base} names no commit in {co} after the fetch (#580)")
    return r.stdout.strip()

def _salvage_start(co, issue, land):
    """`origin/salvage/<issue>` when a requeue pushed the child's commits there (#2030), the branch holds commits the landing
    branch lacks, and it merges onto the landing branch without conflict (#2044); else None. A conflicting branch is named in a
    status comment, and the start stays on the landing branch."""
    ref = f"salvage/{issue}"; base = os.environ.get("SG_BASE") or "main"
    try: f = subprocess.run(["git", "-C", co, "fetch", "-q", "origin", f"+refs/heads/{ref}:refs/remotes/origin/{ref}"], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired: return None
    if f.returncode: return None
    tip = f"origin/{ref}"
    if subprocess.run(["git", "-C", co, "merge-base", "--is-ancestor", tip, land], capture_output=True).returncode == 0: return None
    m = subprocess.run(["git", "-C", co, "merge-tree", "--write-tree", "--quiet", land, tip], capture_output=True)
    if m.returncode == 0: return tip
    if m.returncode == 1:
        repo = os.environ.get("SG_REPO", ""); cq = state / "cq"; cq.mkdir(parents=True, exist_ok=True)
        leaf = f"{issue}-salvage-{os.getpid()}.md"
        (cq / (f"{repo.replace('/', '__')}#{leaf}" if "/" in repo else leaf)).write_text(
            f"{ref} does not rebase onto {base} without conflict, so the child starts from {base}. The branch stays on origin for a person to rebase.\n")
        print(f"note: {tip} conflicts with {land}: the issue worktree starts from {base}, and the branch is named in a status comment (#2044)", file=sys.stderr)
    return None

def _run_worktree(co, land):
    """A linked worktree of the checkout that this run alone owns, on a new branch squad/run/<key> cut from `land` (#580). The
    key is unique per launch, so two launches of one request at once get two trees. The owner's HEAD, index and files never
    change; the tree sits under the checkout's ignored worktrees/ folder, beside the issue worktrees."""
    err = ""
    for _ in range(3):   # two launches at once can race on the repository's worktree lock: a new key, then try again
        key = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}-{secrets.token_hex(3)}"
        wt = pathlib.Path(co).resolve() / "worktrees" / "run" / key
        wt.parent.mkdir(parents=True, exist_ok=True)
        made = subprocess.run(["git", "-C", co, "worktree", "add", "-q", "-b", f"squad/run/{key}", str(wt), land], capture_output=True, text=True)
        if made.returncode == 0: return wt
        err = made.stderr.strip(); time.sleep(0.2)
    sys.exit(f"refusing to start: cannot make the run's own worktree from {land[:9]} in {co}: {err} (#580)")

def _start_from(wt, br, land):
    """A reused issue worktree starts from the landing branch (#469). A branch behind it moves up by a fast-forward. A branch
    with commits the landing branch lacks (an earlier run's child that never entered a wave) keeps them on
    refs/squad/leftover/<branch>-<sha9>, named on stderr, and starts again from the landing branch: nothing is lost, and nothing
    rides into the next wave unnamed. A tree with uncommitted changes is left alone: False, and the run keeps its fallback."""
    g = lambda *a: subprocess.run(["git", "-C", str(wt), *a], capture_output=True, text=True)
    if g("status", "--porcelain", "--untracked-files=no").stdout.strip():
        print(f"note: {wt} has uncommitted changes: the run gets {FALLBACK}, and a person must look", file=sys.stderr)
        return False
    head = g("rev-parse", "HEAD").stdout.strip()
    extra = g("rev-list", "--count", f"{land}..HEAD").stdout.strip()
    if extra not in ("", "0"):
        ref = f"refs/squad/leftover/{br}-{head[:9]}"
        g("update-ref", ref, head)
        print(f"note: {br} carried {extra} leftover commit(s) not in {land}: kept on {ref}; the run starts from {land}", file=sys.stderr)
    return g("reset", "-q", "--hard", land).returncode == 0

def _issue_worktree(issue, land):
    """The linked worktree the child works in, made when it is missing, else None: the checkout's own fork route only.

    A profile whose fork checkout is another checkout keeps today's target: the child commits in the fork, which is no
    worktree of this run. A path that is no worktree of its own, or that sits on another branch, is left alone with the
    reason printed: the loop must not reset or replace work an earlier child left behind (#65).
    A tree made now on a branch an earlier dispatch left behind starts from the landing branch, as a reused one does (#1591)."""
    co, fork = os.environ.get("SG_CHECKOUT"), os.environ.get("SG_FORK_DIR")
    if not co or not fork or pathlib.Path(co).resolve() != pathlib.Path(fork).resolve(): return None
    br = (os.environ.get("SG_FORK_BRANCH") or "gui/%n").replace("%n", str(issue))
    wt = pathlib.Path(co).resolve() / "worktrees" / "child" / br.replace("/", "-")
    if wt.is_dir():
        cur = subprocess.run(["git", "-C", str(wt), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True)
        if cur.returncode or cur.stdout.strip() != br:
            print(f"note: {wt} is on {cur.stdout.strip() or 'no branch'}, not {br}: the run gets {FALLBACK}, and a person must look", file=sys.stderr)
            return None
        return wt if _start_from(wt, br, land) else None
    wt.parent.mkdir(parents=True, exist_ok=True)
    have = subprocess.run(["git", "-C", co, "rev-parse", "--verify", "--quiet", f"refs/heads/{br}"], capture_output=True).returncode == 0
    if have:   # #1591: a cleanup can take the worktree away and leave its branch at an earlier tip, and a dispatch that made
        # the tree on that tip started the child on a stale base (the base's files and merges were absent). The branch is reused
        # only when it carries no commit the landing branch lacks: then the tree is made on it and moves up as a reused tree
        # does. Otherwise whether it holds work worth keeping cannot be told here, so it is left exactly as it is, its tip is
        # named, and the run starts elsewhere: nothing is deleted, and no unnamed work rides into the wave.
        tip = subprocess.run(["git", "-C", co, "rev-parse", "--verify", "--quiet", f"refs/heads/{br}^{{commit}}"], capture_output=True, text=True).stdout.strip()
        rev = subprocess.run(["git", "-C", co, "rev-list", "--count", f"{land}..refs/heads/{br}"], capture_output=True, text=True)
        if rev.returncode or not tip or rev.stdout.strip() != "0":
            print(f"note: the issue branch {br} is left behind at {(tip or 'an unreadable tip')[:9]} and carries commits not in {land}"
                  f"{'' if rev.returncode == 0 else ' (unreadable)'}: the run does not reuse it, and the branch is left as it is; "
                  f"the run gets {FALLBACK}, and a person must look", file=sys.stderr)
            return None
    made = subprocess.run(["git", "-C", co, "worktree", "add", "-q", str(wt), br] if have else
                          ["git", "-C", co, "worktree", "add", "-q", "-b", br, str(wt), land], capture_output=True, text=True)
    if made.returncode:
        print(f"note: cannot make the issue worktree {wt} on {br}: {made.stderr.strip()}: the run gets {FALLBACK}", file=sys.stderr)
        return None
    return wt if _start_from(wt, br, land) else None

def _named_target(co, path):
    """The tree a fix run names in SG_RUN_TARGET (#654): the child's kept worktree, used as it is (no reset, no new tree), so every
    gate that reads the target reads the child's files and the fix commits stay on its branch. It must be a linked worktree of the
    checkout, on a branch, never the checkout itself; any other path refuses the start, so a fix run never measures a fresh tree."""
    p = pathlib.Path(path)
    if not p.is_dir(): sys.exit(f"refusing to start: SG_RUN_TARGET {path} is no folder (#654)")
    p = p.resolve()
    g = lambda *a: subprocess.run(["git", "-C", str(p), *a], capture_output=True, text=True)
    top, common = g("rev-parse", "--show-toplevel").stdout.strip(), g("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    own = (pathlib.Path(co).resolve() / ".git").resolve()
    if not top or pathlib.Path(top).resolve() != p or not common or pathlib.Path(common).resolve() != own or p == pathlib.Path(co).resolve():
        sys.exit(f"refusing to start: SG_RUN_TARGET {p} is no linked worktree of {co} (#654)")
    if g("symbolic-ref", "--quiet", "HEAD").returncode: sys.exit(f"refusing to start: SG_RUN_TARGET {p} is on no branch (#654)")
    return p

req_text = req_file.read_text()
if _issue_child_request(req_text): req_text = _wave_request(req_text)   # the run cuts a wave below: its children keep their branches local
issues = _issue_named(req_text) if _issue_child_request(req_text) else []   # a run that commits on the branch the land reads
# #865: one issue, one live run (ADR-0150). A start for an issue that a live run of this checkout already works is refused by name, and
# nothing starts; the caller's refusal path frees the request's dispatch marker. The check reads the loop's own state (active-runs.txt
# and each live run's request.txt), never GitHub, which is eventually consistent. A run with a verdict holds nothing.
def _live_holders():   # {issue: run folder name} of each issue a live child run of this checkout works
    return liveholds.live_holders(state / "active-runs.txt", base=pathlib.Path(co or cwd))
_held = _live_holders() if issues else {}
_clash = [(n, _held[n]) for n in issues if n in _held]
if _clash:
    sys.exit("refused: " + "; ".join(f"issue #{n} is already worked by live run {r}" for n, r in _clash)
             + ". One issue, one live run (ADR-0150): this start launches nothing.")
checkout = os.environ.get("SG_CHECKOUT") or str(cwd)
land = _landing_base(checkout)   # before any tree is made: a fetch that fails refuses the start (#580)
named = os.environ.get("SG_RUN_TARGET")
# A fix run repairs a work branch (#1374): its child's commits are on that branch, never in the target, so the final gate
# proves every acceptance command in the child's tree. The engine option records that promise, so a build that names no
# `fix_run` option refuses the round here, by name: an unmarked run proves its gate in the target and reports a false red,
# and branchfixpass.sh would retry the same branch forever without the ADR-0134 stop.
fix_run = bool(named or os.environ.get("SG_FIX_RUN"))
if fix_run and not _names_option("fix_run"):
    sys.exit(f"refused: the squad build at {squad} names no fix_run engine option, so this fix run cannot be marked "
             "and its final gate would prove in the target (#1374). Publish a build that records fix_run first.")
# (#1504, ADR-0150 across runs) A stacked child's request names the branch it was cut from (`CUT FROM: <branch>`, written by
# autofrontier.sh): the issue worktree starts on THAT branch, never the base, so the file one run lands is edited by one line
# of commits. A fix round's request names the work branch in the same line (#2350): its run worktree is cut from that tip, so
# the child commits on the work branch by construction and its head fast-forwards. A branch the checkout does not hold is a
# refusal, named here: the run never starts from the base in its place, since that head is no fast-forward of the branch.
cut = re.search(r"^CUT FROM: *([^\s]+) *$", req_text, re.M)
cut_ref = None; cut_sha = ""
if cut and (len(issues) == 1 or fix_run):
    name = cut.group(1)
    if fix_run:   # a work branch lives on origin, and a local ref of it can be stale: its tip is what the round builds on
        try: subprocess.run(["git", "-C", checkout, "fetch", "-q", "origin", f"+refs/heads/{name}:refs/remotes/origin/{name}"],
                            capture_output=True, timeout=120)
        except subprocess.TimeoutExpired: pass   # no ref resolves below, so the start refuses by name
    refs = [f"refs/remotes/origin/{name}"] if fix_run else [f"refs/heads/{name}"]
    for ref in refs:
        if subprocess.run(["git", "-C", checkout, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True).returncode == 0:
            cut_ref = ref.removeprefix("refs/remotes/origin/").removeprefix("refs/heads/")
            cut_sha = subprocess.run(["git", "-C", checkout, "rev-parse", f"{ref}^{{commit}}"],
                                     capture_output=True, text=True).stdout.strip()
            break
    if not cut_sha:
        sys.exit(f"refusing to start: CUT FROM {name} names no branch of {checkout} (#1504, #2350): the run never starts from the base in its place")
# (#2260 AC6) The record names the tip the child actually starts from: the head of the branch cut here, never the sha another
# reader saw earlier. A STACK TIP line of a stacked child is rewritten to that head, so the record and the cut agree.
if cut_sha and re.fullmatch(r"[0-9a-f]{40,64}", cut_sha):
    req_text = re.sub(r"^STACK TIP: *\S+", f"STACK TIP: {cut_sha}", req_text, count=1, flags=re.M)
salvage = _salvage_start(checkout, issues[0], land) if len(issues) == 1 and not named and not cut_ref else None
target = _named_target(checkout, named) if named else _issue_worktree(issues[0], cut_ref or salvage or land) if len(issues) == 1 else None
if len(issues) > 1 and not named: print(f"note: the request names {issues}: one run has one target, so this run gets {FALLBACK}", file=sys.stderr)
if named:
    print(f"note: the run's target is the kept worktree {target} it fixes: the engine commits it (#654, ADR-0106 rule 4)", file=sys.stderr)
elif target:
    print(f"note: the run's target is the issue worktree {target}: the engine commits it (#93, ADR-0106 rule 4)", file=sys.stderr)
else:
    target = _run_worktree(checkout, cut_sha or land)
    src = f"the CUT FROM branch {cut.group(1)} at {cut_sha[:9]}" if cut_sha else f"origin/{os.environ.get('SG_BASE') or 'main'} at {land[:9]}"
    print(f"note: the run's target is its own worktree {target}, from {src} (#580)", file=sys.stderr)

# workflow graph root check: the graph the run reads is the target's, so the owner's files never change (#580)
wf = os.environ.get("SG_WORKFLOW") or "dynamic"; root = os.environ.get("SG_ROOT_NAME")
gp = target / "docs" / "squad" / "workflows" / f"{wf}.graph.json"
if root and gp.exists():
    doc = json.loads(gp.read_text())
    if doc.get("root") != root:
        old = doc.get("root")
        nodes = [n for n in doc.get("nodes", []) if n.get("name") == old]
        if len(nodes) != 1: sys.exit(f"{gp}: root {old!r} does not name exactly one node; fix the graph by hand")
        doc["root"] = nodes[0]["name"] = root
        gp.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"note: renamed graph root {old!r} -> {root!r} in {gp} (commit it)", file=sys.stderr)
    chk = subprocess.run(["uv", "run", "--project", squad, "python", "-c",
                          "import json,sys;from squad import graph;e=graph.validate(json.load(open(sys.argv[1])));print('\\n'.join(map(str,e or [])));sys.exit(1 if e else 0)", str(gp)],
                         capture_output=True, text=True)
    if chk.returncode: sys.exit(f"invalid graph {gp}:\n{chk.stdout}{chk.stderr}")

runs = (pathlib.Path(checkout) if named else target) / "runs"   # a fix run's folder lives beside every other run, never inside the kept worktree it fixes: that worktree is what a landing or a cleanup deletes, and the record three readers ask for liveness would go with it (#668)
before = set(p.name for p in runs.glob("run-*")) if runs.exists() else set()
log = state / (req_file.stem + ".log")
try: spent = _spent_models(json.loads(_graph_path(os.environ.get("SG_WORKFLOW")).read_text()), os.environ.get("SG_SHARED"))
except (OSError, ValueError): spent = []   # an absent or malformed graph is the squad's own refusal to make, not this launcher's
# The coding chain's hours windows go to the run too, so its own fallback skips a model that the window closes while the run lives (#571).
hours = {e.raw: e.hrs for e in _chain("coding_models") if isinstance(e.hrs, str) and e.hrs}
# The coding chain's order goes to the run as well: the launch picks by it, so an in-run fallback follows the same order
# rather than the graph allow-list order, and the request line and the engine agree on the next model (#617).
order = [m for m in cm if isinstance(m, str) and m]
# A coding entry's declared context window goes to the run too: the engine skips an entry whose window is under the
# prompt it would send (#1470), so a model that cannot take the prompt never spends a call on it.
ctxs = {e.raw: e.ctx for e in _chain("coding_models") if e.ctx}
# A run that cuts a wave keeps its landed children after its verdict (#477, ADR-0106): the wave entry reads each child's
# worktree then, so the run starts with the engine option --keep-children, which it records at its start. The hold is the
# run's wave, never the target's shape. A squad that has no such option refuses the flag, so it goes only to one that names it.
keep = ["--keep-children"] if _issue_child_request(req_text) and _names_option("keep_children") else []
# The fix mark is `SG_RUN_TARGET` (a run on the kept worktree, childfix.sh) or `SG_FIX_RUN` (a round on a pushed branch,
# branchfixpass.sh). It is recorded at run start, so a resume reads it again; the build was probed for it above.
fix = ["--fix-run"] if fix_run else []
proc = subprocess.Popen(["caffeinate", "-dimsu", "uv", "run", "--project", squad, "--extra", "sdk", "squad", "start"] + (["--workflow", os.environ["SG_WORKFLOW"]] if os.environ.get("SG_WORKFLOW") else []) + keep + fix + [
                        "--models", str(here / "models.json"), "--target", str(target)] + (["--runs-dir", str(runs)] if named else []) + (["--spent-models", ",".join(spent)] if spent else [])+ (["--model-hours", ",".join(f"{m}={w}" for m, w in hours.items())] if hours else []) + (["--model-order", ",".join(order)] if order else []) + (["--model-ctx", ",".join(f"{m}={w}" for m, w in ctxs.items())] if ctxs else []) + [req_text],
                        stdout=open(log, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                        cwd=str(target),
                        env={**os.environ, "SQUAD_HEADLESS": "1"})
new = []
deadline = time.monotonic() + 180   # a short step: the folder is read as soon as it appears, never up to 2 s late per launch
while True:
    new = sorted(set(p.name for p in runs.glob("run-*")) - before) if runs.exists() else []
    if new: break
    if proc.poll() is not None:
        tail = "".join(open(log).readlines()[-15:])
        sys.exit(f"squad exited ({proc.returncode}) before creating a run folder; {log}:\n{tail}")
    if time.monotonic() > deadline:
        sys.exit(f"no run folder appeared in 180 s; see {log}")
    time.sleep(0.1)
run = new[-1]
# A workspace removed under the dispatch is a refusal, not a traceback (#1637): the child tree is culled between the cut and
# this write, the folder goes with it, and a crash here left the issue claimed by a marker no pass frees. A refusal frees it
# and returns the card to Todo (#852).
def _gone(): sys.exit(f"launch refused: the run folder {runs / run} is gone; its workspace was removed while the dispatch started, so nothing started")
if not (runs / run).is_dir(): _gone()
(runs / run / "request.txt").write_text(req_text)   # consult.sh reads the request from the run folder; a stacked child's tip is the one cut here (#2260)
# One branch per wave (#375, ADR-0145 rules 8 and 10): a run that dispatches children cuts its wave branch from the base
# as it starts, so every child has a wave to enter and the run's completion has one to land. `wave.sh start` owns the cut
# -- a throwaway clone, never this checkout -- and leaves the marker $SG_STATE/auto/wave-<run> that watch.sh reads when
# the run ends. A cut that fails says so and starts the run anyway: its children then work as they did before, and the
# run ends with no wave to land.
if _issue_child_request(req_text):
    w = subprocess.run(["bash", str(here / "wave.sh"), "start", run], capture_output=True, text=True)
    print("\n".join(l for l in (w.stdout + w.stderr).splitlines() if l.strip()) or f"note: wave.sh start {run} said nothing", file=sys.stderr)
    if w.returncode != 0: print(f"WAVE-ERROR launch: wave.sh start {run} exit {w.returncode}", file=sys.stderr)   # the cut failed: the run starts without a wave (#1349)
run_log = runs / run / "launch.log"
def _copy_launch_log():   # the janitor quotes this file to explain why the run never started; state/<req>.log is the same stream
    try: run_log.write_text(pathlib.Path(log).read_text())
    except OSError:
        try: run_log.write_text("")
        except OSError: pass   # the folder went too: the refusal below names it, and this copy names nothing (#1637)
if not (runs / run).is_dir(): _gone()   # the wave cut takes time, and the cull runs beside it
_copy_launch_log()
if proc.poll() is None:   # launch.py exits at once, so a follower process (a thread would die with it) keeps the copy live until the squad exits, which is exactly when a refused start writes its reason
    subprocess.Popen(["bash", "-c", 'while kill -0 "$1" 2>/dev/null; do cp -f "$2" "$3"; sleep 1; done; cp -f "$2" "$3"', "launchlog", str(proc.pid), str(log), str(run_log)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
# make sure the hub is up so the run's dashboard shows up there on its own (it discovers dashboard servers by itself)
_s = socket.socket(); _s.settimeout(0.5)
try: _s.connect(("127.0.0.1", 7700))
except OSError:
    subprocess.Popen(["caffeinate", "-dimsu", "python3", str(here / "hub.py"), "7700"],
                     stdout=open(state / "hub.log", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
finally: _s.close()
def _active_runs_module():   # #1862: squad/active_runs.py, the list's one writer protocol, from the build this launcher starts
    roots = [r for r in (str(here.parents[2]), os.environ.get("SG_SQUAD"), str(squad), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]
    root = next((r for r in roots if (pathlib.Path(r) / "squad" / "active_runs.py").is_file()), None)
    if root is None:
        sys.exit("refused: no squad/active_runs.py in " + ", ".join(roots) + ": the run's line in active-runs.txt has no "
                 "writer protocol, so a janitor pass could lose it (#1862)")
    spec = importlib.util.spec_from_file_location("active_runs", pathlib.Path(root) / "squad" / "active_runs.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
# The append holds the lock the janitor's rewrite holds, so a run started during a pass keeps its line (#1862). Absolute:
# watch.sh/heartbeat.sh find it from any cwd.
_active_runs_module().append(state / "active-runs.txt", str(runs / run))
print(run)
