"""The premise and decision check of a ticket before dispatch (#2072, ADR-0155, ADR-0142).

A ticket states its premises as `Premise: <claim> (<path:line>)`. Each one is a fact that reading the cited lines settles
(premise_check.resolve). A ticket cites a decision as `Decision <name>: "<text>"`, and the request carries that text
verbatim. A ticket needs a decision as `Needs decision: <name>`; a needed decision with no cited source holds the ticket.

The check runs once for each content revision of a ticket. Its evidence is kept under the state directory, and a dispatch
of the same revision reads that evidence instead of checking again.

    python3 -m squad.ticket_premise hold <issue.json> --state <dir> --root <repo>      exit 3 when the ticket is held
    python3 -m squad.ticket_premise decisions <issue.json> --state <dir> --root <repo> the cited decisions, one per line
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from squad import premise_check as pc
from squad.record import atomic_write

EX_HOLD = 3
PREMISE = re.compile(r"^Premise: (?P<claim>.+?) \((?P<proof>[^()\s]+)\)\s*$", re.M)
DECISION = re.compile(r'^Decision (?P<name>[^:\n]+): "(?P<text>[^"\n]+)"\s*$', re.M)
NEEDS = re.compile(r"^Needs decision: (?P<name>.+?)\s*$", re.M)


def revision(body):
    return hashlib.sha256(body.encode()).hexdigest()


def check(body, root):
    root = Path(root)
    findings = [{"premise": m["claim"].strip(), "kind": "fact", "proof": m["proof"]} for m in PREMISE.finditer(body)]
    rows = pc.resolve(findings, read=lambda path: (root / path).read_text())
    decisions = {m["name"].strip(): m["text"] for m in DECISION.finditer(body)}
    needs = [m["name"].strip() for m in NEEDS.finditer(body)]
    return {
        "revision": revision(body),
        "rows": [{"premise": r.premise, "state": r.state, "detail": r.detail} for r in rows],
        "decisions": decisions,
        "needs": needs,
        "missing": [name for name in needs if name not in decisions],
    }


def evidence(number, body, state, root):
    path = Path(state) / "ticket-premise" / f"{number}.json"
    if path.exists():
        kept = json.loads(path.read_text())
        if kept.get("revision") == revision(body):
            return kept, True
    ev = check(body, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(ev, indent=1, sort_keys=True))
    return ev, False


def decision_lines(ev):
    return [f'DECISION {name}: "{text}"' for name, text in ev["decisions"].items()]


def hold_line(number, ev):
    return "\n".join(f'HOLD #{number}: missing decision "{name}"' for name in ev["missing"])


def main(argv=None):
    p = argparse.ArgumentParser(prog="squad.ticket_premise")
    p.add_argument("command", choices=("hold", "decisions"))
    p.add_argument("issue", type=Path)
    p.add_argument("--state", required=True, type=Path)
    p.add_argument("--root", type=Path, default=pc.ROOT)
    a = p.parse_args(argv)
    issue = json.loads(a.issue.read_text())
    number = int(issue["number"])
    ev, _ = evidence(number, issue.get("body") or "", a.state, a.root)
    if a.command == "decisions":
        for line in decision_lines(ev):
            print(line)
        return 0
    held = hold_line(number, ev)
    if held:
        print(held)
        return EX_HOLD
    return 0


if __name__ == "__main__":
    sys.exit(main())
