"""Shared helpers of the dashboard tests: the node runner, the error-pair keys, the page source, and the finished-run loaders."""
import atexit
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_dashboard import CORE_DIR, OUT, core_bundle, engine_data, page_bundle, page_template  # noqa: E402, F401  (CORE_DIR, OUT, page_bundle and page_template are re-exported)

from squad import graph  # noqa: E402

RUNNER = ROOT / "tests" / "dashboard_runner.js"
_NODE: dict = {"proc": None, "fresh": False}


def _spawn():
    return subprocess.Popen(["node", str(RUNNER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)


def _node():
    """The one node process the session reuses. A call that starts its own node pays the binary's cold start -- 89 MB on a
    shared runner -- inside a measured call, and the first call of a session paid it whole (#1534)."""
    p = _NODE["proc"]
    if p is None or p.poll() is not None:
        p = _NODE["proc"] = _spawn()
        _NODE["fresh"] = True
    return p


def start_runner():
    """Start the shared process before the first call, so a suite whose first call sits at its line pays the start in setup,
    which is not a call. No node on this runner means no start; the call that needs node still says so."""
    if shutil.which("node"):
        _node()


def stop_runner():
    """End the shared process and its pipe: a session leaves no node behind."""
    p, _NODE["proc"] = _NODE["proc"], None
    if p is None or p.poll() is not None:
        return
    try:
        p.stdin.close()
    except (BrokenPipeError, ValueError):
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()


atexit.register(stop_runner)


def run_node(ops):
    assert shutil.which("node"), "node is not installed: the dashboard parity guard cannot run, and it never skips"
    req = {"ops": ops}
    for attempt in (0, 1):
        p = _node()
        if _NODE["fresh"]:
            req["engine"], req["core"] = engine_data(), core_bundle()   # the process keeps both for every later call
            _NODE["fresh"] = False
        try:
            p.stdin.write(json.dumps(req) + "\n")
            p.stdin.flush()
            line = p.stdout.readline()
        except (BrokenPipeError, ValueError):
            line = ""
        if line.strip():
            return json.loads(line)
        _NODE["proc"] = None    # the runner ended without an answer: read why, and start one more only once
        if attempt:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            raise AssertionError(p.stderr.read() or "the dashboard runner ended without an answer and said nothing")
    raise AssertionError("the dashboard runner ended without an answer")


# --- the key of one error: (node, rule). A schema error keys on its path (library text differs); any other rule keys on its message ---
SCHEMA_ERR = re.compile(r"^(?:schema: )?([A-Za-z0-9_/<>.\-]+): ")


def pair(node_name, message):
    m = SCHEMA_ERR.match(message)
    return (node_name or "", f"schema@{m.group(1)}" if m else message)


def py_pairs(doc):
    return {pair(e.node, e.message) for e in graph.validate(doc)}


def js_pairs(errs):
    return {pair(e["node"], e["message"]) for e in errs}



RUN22 = ROOT / "tests" / "fixtures" / "run-subject-22"
RUN22_TOKENS = ROOT / "tests" / "fixtures" / "run-subject-22-tokens"  # the same run, with tokens on two nodes


def _run22():
    doc = json.loads((RUN22 / "graph.json").read_text())
    events = run_node([{"op": "record", "text": (RUN22 / "run" / "record.jsonl").read_text()}])[0]["events"]
    return doc, events



def _run22_tokens():
    doc = json.loads((RUN22_TOKENS / "graph.json").read_text())
    events = run_node([{"op": "record", "text": (RUN22_TOKENS / "run" / "record.jsonl").read_text()}])[0]["events"]
    return doc, events



def PAGE_SRC():
    return page_template()



WAVES = ROOT / "tests" / "fixtures" / "run-router-waves"


def synth_run(folder, n, outcome="pass", running=0):
    """Write a run folder (graph.json, run/record.jsonl) whose `route` dispatch spawned `n` children (the run-router-waves graph), for the edge counts no real run holds.

    `outcome` is what each finished child ends with ("pass" or "fail"); the last `running` children have a call in flight and no outcome, and the run has no verdict then.
    """
    folder = Path(folder)
    (folder / "run").mkdir(parents=True, exist_ok=True)
    (folder / "graph.json").write_text((WAVES / "graph.json").read_text())
    start = json.loads((WAVES / "run" / "record.jsonl").read_text().splitlines()[0])
    t0 = 1790881908.0
    out = [start]

    def add(ev):
        out.append({**ev, "pos": len(out) + 1, "ts": t0 + len(out)})

    add({"event": "call_start", "node": "router", "entry": 1, "item": None, "session": "s0", "pid": 1, "limit": 600.0, "started_at": t0})
    add({"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "reasons": [], "spend": 0.1, "turns": 3, "cap": 2, "elapsed": 10.0, "routed": "continue", "model": "opus", "effort": None, "tokens_in": 3000, "tokens_out": 90})
    for i in range(1, n + 1):
        name = f"route.router.{i}"
        prompt = f"Implement the issue in issues/{i % 90 + 10:02d}-synthetic.md."
        add({"event": "spawn_accepted", "request_key": i, "child": name, "parent": "router", "depth": 1, "envelope": "route", "reservation": 2, "parent_entry": 1,
             "definition": {"prompt": prompt, "model": "sonnet", "effort": "medium"}})
        add({"event": "call_start", "node": name, "entry": 1, "item": None, "session": f"s{i}", "pid": 1, "limit": 900.0, "started_at": t0 + len(out)})
        if i > n - running:
            continue
        bad = outcome == "fail"
        add({"event": "outcome", "node": name, "entry": 1, "outcome": outcome, "reasons": ["ac-verify:ac_failed:x"] if bad else [], "spend": 0.2, "turns": 8, "cap": 2, "elapsed": 40.0,
             "model": "sonnet", "effort": "medium", "tokens_in": 200000, "tokens_out": 5000})
    if not running:
        add({"event": "outcome", "node": "route", "entry": 1, "outcome": "pass", "reasons": ["done"], "spend": None, "turns": None, "cap": None, "elapsed": None, "routed": "exit:pass"})
        add({"event": "verdict", "verdict": "completed", "exit_code": 0, "reasons": []})
    (folder / "run" / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in out))
    return folder
