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
state = pathlib.Path(os.environ["SG_STATE"])   # exported by env.sh: one state directory per repository (#20)
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

before = set(p.name for p in runs.glob("run-*")) if runs.exists() else set()
log = state / (req_file.stem + ".log")
proc = subprocess.Popen(["caffeinate", "-dimsu", "uv", "run", "--project", squad, "--extra", "sdk", "squad", "start"] + (["--workflow", os.environ["SG_WORKFLOW"]] if os.environ.get("SG_WORKFLOW") else []) + [
                        "--target", str(cwd), req_file.read_text()],
                        stdout=open(log, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
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
