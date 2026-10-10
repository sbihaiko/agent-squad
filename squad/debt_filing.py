"""File each DEBT finding of a saved review as a board task (ADR-0273 Decision 4).

A review line reads `- DEBT <file>:<line> — <defect>. Acceptance: <criterion>` (the BLOCKING|DEBT tag of the reviewer).
BLOCKING findings block the merge and are never filed. GitHub goes through REST only (`gh api`), never GraphQL.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys

LABELS = ("debt", "ready-for-agent")
FINDING = re.compile(r"^\W*DEBT\W+(?P<loc>\S+?:\d+)\s*[—–:-]+\s*(?P<defect>.*?)\s*(?:Acceptance:\s*(?P<ac>.*))?$",
                     re.IGNORECASE)
MARK = "debt-key:"


def _sh(args, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True, check=True).stdout


def parse(review):
    """The DEBT findings of `review` as dicts of loc, defect and acceptance."""
    out = []
    for line in review.splitlines():
        m = FINDING.match(line.strip())
        if m:
            out.append({"loc": m["loc"], "defect": m["defect"].rstrip(" ."), "ac": (m["ac"] or "").strip()})
    return out


def key(finding):
    """Stable across heads: the file and the defect, never the line, which moves as the code does."""
    path = finding["loc"].rsplit(":", 1)[0]
    text = f"{path}|{' '.join(finding['defect'].lower().split())}"
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _body(finding, pr, issue, head):
    ac = finding["ac"] or "The defect above is fixed and a test covers it."
    return (f"Origin: PR #{pr} (issue #{issue}), head {head[:12]}\n\n"
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
