"""Write each ADR's Guard section from docs/design/guards.json, or check that they agree.

One source: guards.json maps an ADR number to its guard tests and to any open part of the rule.
Usage: python3 scripts/sync_guards.py [--check]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARDS = ROOT / "docs" / "design" / "guards.json"


def render(entry):
    lines = ["Tests that guard this rule:", ""] + [f"- `{t}`" for t in entry["tests"]]
    if entry.get("open"):
        lines += ["", f"Open: {entry['open']}"]
    else:
        lines += ["", "No open part remains. A change to this rule changes its tests in the same commit (ADR-0018)."]
    return "\n".join(lines) + "\n"


def rewrite(text, entry):
    body = render(entry)
    if re.search(r"^## Guard\s*$", text, re.M):
        head = re.split(r"^## Guard\s*$", text, flags=re.M)[0]
        return head + "## Guard\n\n" + body
    return re.sub(r"\n?Guard: [^\n]*\s*$", "\n## Guard\n\n" + body, text.rstrip("\n") + "\n") if re.search(r"\nGuard: ", text) else text + "\n## Guard\n\n" + body


def main(check):
    guards = json.loads(GUARDS.read_text())
    bad = []
    for num, entry in sorted(guards.items()):
        (path,) = (ROOT / "docs" / "adr").glob(f"{num}-*.md")
        old = path.read_text()
        new = rewrite(old, entry)
        if new != old:
            if check:
                bad.append(path.name)
            else:
                path.write_text(new)
    if bad:
        print("Guard sections out of date:", ", ".join(bad))
        sys.exit(1)


if __name__ == "__main__":
    main("--check" in sys.argv)
