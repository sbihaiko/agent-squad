"""Add guard tests to an ADR in docs/design/guards.json, then rewrite the ADR Guard sections.

Usage: python3 scripts/guard_add.py <adr-number> <open-note or -> <test-id>...
An open note of - clears the open part. A note of = keeps the old one.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G = ROOT / "docs" / "design" / "guards.json"
num, note, *tests = sys.argv[1:]
guards = json.loads(G.read_text())
e = guards.setdefault(num, {"tests": [], "open": ""})
e["tests"] += [t for t in tests if t not in e["tests"]]
if note != "=":
    e["open"] = "" if note == "-" else note
G.write_text(json.dumps(dict(sorted(guards.items())), indent=2) + "\n")
subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_guards.py")], check=True)
