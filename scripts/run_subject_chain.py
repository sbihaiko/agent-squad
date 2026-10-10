# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk==0.2.161", "anyio", "pyyaml", "jsonschema"]
# ///
"""Run every subject issue, one after the other, on ONE growing tree: the fixed five-phase graph, chained.

Run: uv run scripts/run_subject_chain.py [--runs-dir DIR] [--issues 1,2,3]
Issues run blockers first. Each run clones the final target of the run before it (`run_subject.py --subject <tree> --holds ...`),
so run N sees the work of runs 1..N-1. A failed issue leaves the tree as it was and its dependents are skipped.
At the end, every held-out acceptance test runs on the last good tree. A harness, not a gate.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from squad import chain_view  # noqa: E402


def order(rows):
    """Issue numbers, blockers first, ties by number."""
    by, out = {r["issue"]: r for r in rows}, []
    while len(out) < len(by):
        ready = sorted(n for n, r in by.items() if n not in out and all(b in out for b in r["blocked_by"]))
        if not ready:
            raise SystemExit("blocked_by has a cycle")
        out.append(ready[0])
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", default=str(Path.home() / "sources" / "subject"))
    p.add_argument("--bench", default=str(ROOT / "bench" / "subject"))
    p.add_argument("--runs-dir", default=str(ROOT / "runs" / f"chain-{time.strftime('%Y%m%d-%H%M%S')}"))
    p.add_argument("--issues", default="")
    a = p.parse_args()
    rows = json.loads((Path(a.bench) / "issues.json").read_text())
    want = {int(x) for x in a.issues.split(",") if x} or {r["issue"] for r in rows}
    tree, held, status = a.subject, [], {}
    by = {r["issue"]: r for r in rows}
    chain_view.from_runs(a.runs_dir, a.bench, a.subject) if Path(a.runs_dir).exists() else None
    for n in (n for n in order(rows) if n in want):
        if any(status.get(b) != 0 for b in by[n]["blocked_by"] if b in want):
            status[n] = "skipped"
            print(f"issue {n}: skipped (a blocker did not land)", flush=True)
            continue
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_subject.py"), str(n), "--subject", tree, "--holds", ",".join(map(str, held)),
                            "--bench", a.bench, "--runs-dir", a.runs_dir], capture_output=True, text=True)
        status[n] = r.returncode
        chain_view.from_runs(a.runs_dir, a.bench, a.subject, issues=sorted(want), status=status)      # one page for the whole chain
        (Path(a.runs_dir) / f"chain-{n:02d}.log").parent.mkdir(parents=True, exist_ok=True)
        (Path(a.runs_dir) / f"chain-{n:02d}.log").write_text(r.stdout + r.stderr)
        print(f"issue {n}: exit {r.returncode}", flush=True)
        if r.returncode == 0:
            m = re.search(r"^record: (.*)/record\.jsonl", r.stdout, re.M)
            tree = str(Path(m.group(1)) / "target")
            held.append(n)
    print("TREE", tree, flush=True)
    print("STATUS", json.dumps(status), flush=True)
    sh = [f"cp {Path(a.bench).resolve() / by[n]['acceptance_test']} tests/api/test_zz_final_{n:02d}.py" for n in held]
    from squad import workcmd  # the project's environment, not the orchestrator's venv (which holds no pytest): the same policy as a gate
    final = workcmd.run(f"rm -rf /tmp/chain-final && git clone -q {tree} /tmp/chain-final && cd /tmp/chain-final && " + " && ".join(sh + ["python3 -m pytest -q -p no:xdist --no-header"]), ".")
    print("FINAL (all held-out tests on the last good tree)", final.ok, final.evidence, flush=True)
    chain_view.from_runs(a.runs_dir, a.bench, a.subject, done=True, issues=sorted(want), status=status, final={"ok": final.ok, "text": final.evidence or ""})      # the page ends with the all-tests result
    return 0 if all(v == 0 for v in status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
