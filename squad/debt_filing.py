"""File each DEBT finding of a saved review as a board task (ADR-0273 Decision 4).

The review is the checklist of `contrib/squad-goal/scripts/revformat.py` (#1777): the section a finding sits in is
its severity, and a bullet reads `- <file>:<line> — <defect> — <concrete failure> — <fix>`, at most two lines.
BLOCKING findings block the merge and are never filed; the acceptance criterion of a filed task is that fix.
The filer asks revformat.py what a finding is (`is_finding`), the same predicate the severity counts and the
`--summary` line `autopilot.sh` reads ask (#1834), so a bullet that answers a previous finding -- under a fix
round's `## Resolved`, or marked `fixed:` where it sits -- is filed by nobody.
A review saved before the checklist (an inline DEBT tag, no sections) is still read the old way.
GitHub goes through REST only (`gh api`), never GraphQL.
"""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

LABELS = ("debt",)
FINDING = re.compile(r"^\W*DEBT\W+(?P<loc>\S+?:\d+)\s*[—–:-]+\s*(?P<defect>.*?)\s*(?:Acceptance:\s*(?P<ac>.*))?$",
                     re.IGNORECASE)   # the shape of a review saved before #1777: an inline DEBT tag on the line
SEP = re.compile(r"\s+[—–-]\s+")
LOC = re.compile(r"^[^\s:]+:\d+$")
MARK = "debt-key:"
_REVFORMAT = {}


def _sh(args, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True, check=True).stdout


def _revformat():
    """revformat.py, the module that reads the shape of a review and tells a finding from a fix round's answer
    (#1777, #1832, #1834). It is loaded from the checkout, next to review.sh: the filer runs as `squad.debt_filing`,
    so the skill's scripts directory is not on the path."""
    if "m" not in _REVFORMAT:
        try:
            _REVFORMAT["m"] = importlib.import_module("revformat")
        except Exception:
            path = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts" / "revformat.py"
            spec = importlib.util.spec_from_file_location("revformat", path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            _REVFORMAT["m"] = m
    return _REVFORMAT["m"]


def parse(review):
    """The DEBT findings of `review` as dicts of loc, defect and acceptance."""
    rv = _revformat()
    if not any(rv.SECTION.match(line.strip()) for line in review.splitlines()):
        out = []   # a review saved before the checklist shape: an inline DEBT tag on the line
        for line in review.splitlines():
            m = FINDING.match(line.strip())
            if m:
                out.append({"loc": m["loc"], "defect": m["defect"].rstrip(" ."), "ac": (m["ac"] or "").strip()})
        return out
    out = []
    for section, text in rv.bullets(review):
        if section != "Debt" or not rv.is_finding(section, text):
            continue
        fields = [f.strip() for f in SEP.split(text)]
        at = next((i for i, f in enumerate(fields[:2]) if LOC.match(f)), 0)
        if len(fields) < at + 2:
            continue
        out.append({"loc": fields[at].rstrip(" .,"), "defect": fields[at + 1].rstrip(" ."),
                    "ac": " ".join(fields[at + 2:]).strip()})
    return out


def key(finding):
    """Stable across heads: the file and the defect, never the line, which moves as the code does."""
    path = finding["loc"].rsplit(":", 1)[0]
    text = f"{path}|{' '.join(finding['defect'].lower().split())}"
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _body(finding, pr, issue, head):
    ac = finding["ac"] or "The defect above is fixed and a test covers it."
    path = finding["loc"].rsplit(":", 1)[0]
    fork = ""
    if path.startswith(("contrib/squad-goal/", "squad/", "tests/")):
        fork = "Fork-only work: the change lands in the agent-squad local fork; no PR against MesenAI.\n\n"
    return (f"Origin: PR #{pr} (issue #{issue}), head {head[:12]}\n\n{fork}"
            f"**Location:** `{finding['loc']}`\n\n**Defect:** {finding['defect']}\n\n"
            f"**Acceptance criterion:** {ac}\n\n<!-- {MARK}{key(finding)} -->\n")


def file_debt(repo, pr, issue, review, head, runner=None):
    """File one issue per DEBT finding not already filed (on any head); return the new issue URLs."""
    sh = runner or _sh
    findings = parse(review)
    if not findings:
        return []
    try:
        sh(["gh", "api", f"repos/{repo}/labels/debt"])
    except subprocess.CalledProcessError:
        sh(["gh", "api", "--method", "POST", f"repos/{repo}/labels", "--input", "-"],
           input=json.dumps({"name": "debt", "color": "d93f0b", "description": "Review debt: a non-blocking finding"}))
    seen = sh(["gh", "api", "--paginate", f"repos/{repo}/issues?labels=debt&state=all&per_page=100"])
    filed = []
    for f in findings:
        if f"{MARK}{key(f)}" in seen:
            continue
        body = {"title": f"Debt: {f['defect'][:80]} ({f['loc']})", "body": _body(f, pr, issue, head),
                "labels": list(LABELS)}
        out = json.loads(sh(["gh", "api", "--method", "POST", f"repos/{repo}/issues", "--input", "-"],
                            input=json.dumps(body)))
        seen += body["body"]   # two findings that collapse to one key file once
        filed.append(out["html_url"])
    return filed


def main(argv=None):
    p = argparse.ArgumentParser(description="File a review's DEBT findings as tasks.")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--head", required=True, help="head sha of the reviewed PR")
    p.add_argument("--review", required=True, help="path of the saved review")
    a = p.parse_args(argv)
    with open(a.review, encoding="utf-8") as fh:
        urls = file_debt(a.repo, a.pr, a.issue, fh.read(), a.head)
    print("\n".join(urls) if urls else "no new DEBT findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
