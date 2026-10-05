"""Shared helpers of the dashboard tests: the node runner, the error-pair keys, the page source, and the finished-run loaders (#118)."""
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


def run_node(ops):
    assert shutil.which("node"), "node is not installed: the dashboard parity guard cannot run, and it never skips (#61 decision 4)"
    p = subprocess.run(["node", str(ROOT / "tests" / "dashboard_runner.js")], input=json.dumps({"engine": engine_data(), "core": core_bundle(), "ops": ops}),
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


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
RUN22_TOKENS = ROOT / "tests" / "fixtures" / "run-subject-22-tokens"  # the same run, with tokens on two nodes (ticket #82)


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
