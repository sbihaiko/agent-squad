#!/usr/bin/env python3
"""Print the (Jev score, held-out outcome) pairs of the subject runs as Markdown tables (ticket #55).

Run: python3 scripts/jev_shadow_report.py runs [--json pairs.json]
It states n. It sets no threshold and makes no claim about one (ADR-0012, ADR-0018).
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from squad import jev_pairs  # noqa: E402

SCORES = [("spec", "skill_choice", "wanted"), ("spec", "skill_choice", "confidence"), ("actor", "skill_choice", "wanted"),
          ("actor", "skill_choice", "confidence"), ("spec", "content_screen:issue", "p"), ("spec", "content_screen:CLAUDE.md", "p")]


def pick(run, node, use, field):
    kind, _, subj = use.partition(":")
    for d in run["jev"]:
        if d["node"] == node and d["use"] == kind and d["status"] == "ok" and (not subj or (d["subject"] == "CLAUDE.md") == (subj == "CLAUDE.md")):
            return d["result"].get(field)
    return None


def fmt(x):
    return "-" if x is None else f"{x:.2f}"


def main(runs_dir, json_out=None):
    runs = jev_pairs.collect(runs_dir)
    labeled = [r for r in runs if r["held_out"] in ("pass", "fail")]
    calls = [d for r in runs for d in r["jev"]]
    print(f"Runs {len(runs)}, labeled by the held-out test {len(labeled)} (pass {sum(r['held_out'] == 'pass' for r in labeled)}, "
          f"fail {sum(r['held_out'] == 'fail' for r in labeled)}). Jev calls {len(calls)}, "
          f"with no suggestion {sum(d['status'] != 'ok' for d in calls)}.\n")
    print("| Issue | Run | Held-out | Spec: pick | conf | wanted | Actor: pick | conf | wanted | Screen issue p | Screen CLAUDE.md p |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(runs, key=lambda r: (r["issue"], r["run"])):
        sp = next((d for d in r["jev"] if d["node"] == "spec" and d["use"] == "skill_choice" and d["status"] == "ok"), None)
        ac = next((d for d in r["jev"] if d["node"] == "actor" and d["use"] == "skill_choice" and d["status"] == "ok"), None)
        print(f"| {r['issue']:02d} | {r['run'].split('-', 2)[2]} | {r['held_out'] or 'not reached'} | "
              f"{sp['result']['pick'] if sp else '-'} | {fmt(pick(r, 'spec', 'skill_choice', 'confidence'))} | {fmt(pick(r, 'spec', 'skill_choice', 'wanted'))} | "
              f"{ac['result']['pick'] if ac else '-'} | {fmt(pick(r, 'actor', 'skill_choice', 'confidence'))} | {fmt(pick(r, 'actor', 'skill_choice', 'wanted'))} | "
              f"{fmt(pick(r, 'spec', 'content_screen:issue', 'p'))} | {fmt(pick(r, 'spec', 'content_screen:CLAUDE.md', 'p'))} |")
    print("\n| Score | Held-out | n | min | median | max |")
    print("|---|---|---|---|---|---|")
    for node, use, field in SCORES:
        for lab in ("pass", "fail"):
            v = [x for r in labeled if r["held_out"] == lab for x in [pick(r, node, use, field)] if x is not None]
            row = f"| {node} {use} {field} | {lab} | {len(v)} | " + (f"{min(v):.2f} | {statistics.median(v):.2f} | {max(v):.2f} |" if v else "- | - | - |")
            print(row)
    if json_out:
        Path(json_out).write_text(json.dumps(runs, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None)
