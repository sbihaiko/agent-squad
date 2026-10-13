#!/usr/bin/env python3
"""Launch one squad run fully detached. The run's dashboard server starts (the Squad hub lists it) but no browser tab opens:
SQUAD_HEADLESS=1 (owner, 2026-10-09: "nao abra mais o dashboard, vamos usar apenas o Squad-Hub"). Never open a dashboard tab.
usage: launch.py <request-file>   (run from the target checkout, normally $SG_CHECKOUT)
Prints the run id. nohup+disown dies with the Bash task; a new session survives.
Before launching it checks the workflow graph: a root that is not $SG_ROOT_NAME is renamed (root and node name together) and validated.
While waiting for the run folder it watches the child: if squad exits first, it fails at once with the log tail instead of waiting 3 min."""
import json, os, pathlib, re, socket, subprocess, sys, time

here = pathlib.Path(__file__).resolve().parent
if not os.environ.get("SG_SQUAD"):   # env.sh not sourced (SG_PROFILE alone selects a profile, it does not load it): pick up its SG_* values, else the run silently uses the installed plugin instead of the profile's fork
    out = subprocess.run(["bash", "-c", f'. "{here}/env.sh" >/dev/null && env'], capture_output=True, text=True).stdout
    os.environ.update(dict(l.split("=", 1) for l in out.splitlines() if l.startswith("SG_") and "=" in l))
squad = os.environ.get("SG_SQUAD") or json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))["plugins"]["agent-squad@agent-squad"][0]["installPath"]
state = pathlib.Path(os.environ["SG_STATE"])
state.mkdir(parents=True, exist_ok=True); (state / "cq").mkdir(exist_ok=True)
req_file = pathlib.Path(sys.argv[1]).resolve(); cwd = pathlib.Path.cwd().resolve(); runs = cwd / "runs"

# the coding model comes from models.json (the hub's "squad models" panel), never from memory or a doc
mdoc = json.load(open(here / "models.json"))
def _in_hours(chain, mid):   # mirrors hub.py's in_hours: the model's own window, local hour, "" = always
    w = (mdoc.get("hours") or {}).get(chain, {}).get(mid)
    if not isinstance(w, str) or not re.match(r"^([01]\d|2[0-3])-([01]\d|2[0-3])$", w) or w[:2] == w[3:]: return True
    h = time.localtime().tm_hour; a, b = int(w[:2]), int(w[3:])
    return a <= h < b if a <= b else (h >= a or h < b)
def _may_run(chain, mid):   # mirrors hub.py's may_run: the panel's switch first, then the model's own window
    return not (mdoc.get("off") or {}).get(chain, {}).get(mid) and _in_hours(chain, mid)
cm = mdoc.get("coding_models") or []
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
    for chain, row in (mdoc.get("off") or {}).items():
        for mid, v in (row or {}).items():
            if v and mid in (mdoc.get(chain) or []): out.setdefault(mid.removeprefix("claude:"), chain)
    return out
def _graph_path(workflow):   # mirrors squad/workflows.py resolve: no id, `default` or `dynamic` is the owner's default file, else the dynamic seed
    folder = cwd / "docs" / "squad" / "workflows"; sq = pathlib.Path(squad)
    if workflow in (None, "", "default", "dynamic"):
        return next((p for p in (folder / "default.graph.json", folder / "dynamic.graph.json") if p.is_file()), sq / "docs" / "design" / "router.graph.json")
    mine = folder / f"{workflow}.graph.json"
    return mine if mine.is_file() else sq / "docs" / "workflows" / f"{workflow}.graph.json"
def _run_models(doc):   # [(node, model as the graph names it, first: whether it is the one the node runs first)]: an agent's own model; a dispatch's child default and fallbacks
    for n in doc.get("nodes", []):
        if n.get("kind") == "agent" and n.get("model"): yield n["name"], n["model"], True
        if n.get("kind") == "dispatch":
            for i, m in enumerate((n.get("allow") or {}).get("models") or []): yield n["name"], m, i == 0
off_ids = _off_ids()
if off_ids:
    try: gdoc = json.loads(_graph_path(os.environ.get("SG_WORKFLOW")).read_text())
    except (OSError, ValueError): gdoc = {}   # an absent or malformed graph is the squad's own refusal to make, not this gate's
    hits = [(nm, m, ALIASES.get(m, m), off_ids[ALIASES.get(m, m)], first) for nm, m, first in _run_models(gdoc) if ALIASES.get(m, m) in off_ids]
    said = lambda h: f"node {h[0]!r} runs {h[1]}{'' if h[1] == h[2] else f' -> {h[2]}'}, which the models panel switched off in models.json ({h[3]})"
    for h in hits:
        if not h[4] or os.environ.get("SG_ALLOW_MODEL"): print(f"warning: {said(h)}" + ("" if h[4] else " (a fallback of the dispatch)"), file=sys.stderr)
    if any(h[4] for h in hits) and not os.environ.get("SG_ALLOW_MODEL"):
        sys.exit("refusing to start: " + "; ".join(said(h) for h in hits if h[4]) + ". Switch the model on in the hub's models panel, "
                 "or change the model in the workflow graph, or set SG_ALLOW_MODEL=1 for a deliberate exception.")

co = os.environ.get("SG_CHECKOUT")
if co and pathlib.Path(co).resolve() != cwd and not os.environ.get("SG_ALLOW_OTHER_CWD"):
    sys.exit(f"launch from $SG_CHECKOUT ({co}), not {cwd}: watch.sh, heartbeat.sh and the hub look for runs there. "
             "Set SG_ALLOW_OTHER_CWD=1 to override (the run is then tracked by its absolute path).")

# workflow graph root check
wf = os.environ.get("SG_WORKFLOW") or "dynamic"; root = os.environ.get("SG_ROOT_NAME")
gp = cwd / "docs" / "squad" / "workflows" / f"{wf}.graph.json"
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

# ADR-0106 rule 4: the engine commits a passing node's target only when the run owns it, a linked worktree that holds the
# run folder. A loop child works on the branch the land reads, so the run this starts for it has that issue worktree as its
# target and the run folder lives inside it (#93). Left on the owner checkout, the target is refused as `owner_checkout` and
# the child's work sits in a worktree outside the run area, so a person had to commit it.
FALLBACK = "$SG_CHECKOUT"   # the target a run keeps when no issue worktree is resolved, named in the notes without its path

def _issue_named(txt):
    """The issues a request names: the dispatch header's list first (`GitHub issue(s) N, M`), the plain `issue #N` mention as a fallback."""
    for pat in (r"GitHub issue\(?s?\)? ?#? ?([0-9][0-9, ]*)", r"\bissue #([0-9]+)"):
        m = re.search(pat, txt)
        if m: return [int(x) for x in re.split(r"[^0-9]+", m.group(1)) if x]
    return []

def _issue_child_request(txt):
    """True when the deliverable is a local commit on the branch the land reads: the dispatch builders mark those runs with a
    `CHILD - GitHub issue` block (`autofrontier.sh`, `autobug.sh`) or a `FORK-ONLY` line, which the retired local land route
    wrote and a run record from before its retirement still carries. A fix run on a PR branch names an issue too, and its
    child commits on the PR branch: that run keeps today's target."""
    return bool(re.search(r"^CHILD[^\n]*GitHub issue", txt, re.M) or re.search(r"^FORK-ONLY", txt, re.M))

def _issue_worktree(issue):
    """The linked worktree the child works in, made when it is missing, else None: the checkout's own fork route only.

    A profile whose fork checkout is another checkout keeps today's target: the child commits in the fork, which is no
    worktree of this run. A path that is no worktree of its own, or that sits on another branch, is left alone with the
    reason printed: the loop must not reset or replace work an earlier child left behind (#65)."""
    co, fork = os.environ.get("SG_CHECKOUT"), os.environ.get("SG_FORK_DIR")
    if not co or not fork or pathlib.Path(co).resolve() != pathlib.Path(fork).resolve(): return None
    br = (os.environ.get("SG_FORK_BRANCH") or "gui/%n").replace("%n", str(issue))
    wt = pathlib.Path(co).resolve() / "worktrees" / "child" / br.replace("/", "-")
    if wt.is_dir():
        cur = subprocess.run(["git", "-C", str(wt), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True)
        if cur.returncode or cur.stdout.strip() != br:
            print(f"note: {wt} is on {cur.stdout.strip() or 'no branch'}, not {br}: the run keeps {FALLBACK}, and a person must look", file=sys.stderr)
            return None
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    base = os.environ.get("SG_FORK_BASE") or os.environ.get("SG_BASE") or "main"
    have = subprocess.run(["git", "-C", co, "rev-parse", "--verify", "--quiet", f"refs/heads/{br}"], capture_output=True).returncode == 0
    made = subprocess.run(["git", "-C", co, "worktree", "add", "-q", str(wt), br] if have else
                          ["git", "-C", co, "worktree", "add", "-q", "-b", br, str(wt), base], capture_output=True, text=True)
    if made.returncode:
        print(f"note: cannot make the issue worktree {wt} on {br}: {made.stderr.strip()}: the run keeps {FALLBACK}", file=sys.stderr)
        return None
    return wt

req_text = req_file.read_text()
issues = _issue_named(req_text) if _issue_child_request(req_text) else []   # a run that commits on the branch the land reads
target = _issue_worktree(issues[0]) if len(issues) == 1 else None
if len(issues) > 1: print(f"note: the request names {issues}: one run has one target, so this run keeps {FALLBACK}", file=sys.stderr)
if target:
    print(f"note: the run's target is the issue worktree {target}: the engine commits it (#93, ADR-0106 rule 4)", file=sys.stderr)
runs = (target or cwd) / "runs"
before = set(p.name for p in runs.glob("run-*")) if runs.exists() else set()
log = state / (req_file.stem + ".log")
proc = subprocess.Popen(["caffeinate", "-dimsu", "uv", "run", "--project", squad, "--extra", "sdk", "squad", "start"] + (["--workflow", os.environ["SG_WORKFLOW"]] if os.environ.get("SG_WORKFLOW") else []) + [
                        "--target", str(target or cwd), req_text],
                        stdout=open(log, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                        cwd=str(target) if target else None,
                        env={**os.environ, "SQUAD_HEADLESS": "1"})
new = []
for _ in range(90):
    new = sorted(set(p.name for p in runs.glob("run-*")) - before) if runs.exists() else []
    if new: break
    if proc.poll() is not None:
        tail = "".join(open(log).readlines()[-15:])
        sys.exit(f"squad exited ({proc.returncode}) before creating a run folder; {log}:\n{tail}")
    time.sleep(2)
else:
    sys.exit(f"no run folder appeared in 180 s; see {log}")
run = new[-1]
(runs / run / "request.txt").write_text(req_file.read_text())   # consult.sh reads the request from the run folder
run_log = runs / run / "launch.log"
def _copy_launch_log():   # the janitor quotes this file to explain why the run never started; state/<req>.log is the same stream
    try: run_log.write_text(pathlib.Path(log).read_text())
    except OSError: run_log.write_text("")
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
with open(state / "active-runs.txt", "a") as f: f.write(str(runs / run) + "\n")   # absolute: watch.sh/heartbeat.sh find it from any cwd
print(run)
