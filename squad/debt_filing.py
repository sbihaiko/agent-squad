"""File each DEBT finding of a saved review as a board task (ADR-0273 Decision 4).

The review is the checklist of `contrib/squad-goal/scripts/revformat.py` (#1777): the section a finding sits in is
its severity, and a bullet reads `- <file>:<line> — <defect> — <concrete failure> — <fix>`, at most two lines.
BLOCKING findings block the merge and are never filed; the acceptance criterion of a filed task is that fix.
A review saved before the checklist (an inline DEBT tag, no sections) is still read the old way.
The filed issue is put on the loop's project board when it is opened, and a finding whose issue is already filed
is carded too if it has none (#1838 -- an issue filed by a run that could not mint a card is repaired here): the frontier and the close passes read
the board, so an issue filed without a card is one nobody works and nobody closes, and every later move for it is a
no-op on stderr. The board is `$SG_OWNER`/`$SG_PROJECT`, the pair every other board move of the loop uses.
GitHub goes through REST only (`gh api`), never GraphQL.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

LABELS = ("debt",)
BOARD_STATUS = "Todo"   # where a filed task waits: the frontier and autobug read that column
FINDING = re.compile(r"^\W*DEBT\W+(?P<loc>\S+?:\d+)\s*[—–:-]+\s*(?P<defect>.*?)\s*(?:Acceptance:\s*(?P<ac>.*))?$",
                     re.IGNORECASE)   # the shape of a review saved before #1777: an inline DEBT tag on the line
SECTION = re.compile(r"^##\s*(Blocking|Debt)\s*$", re.IGNORECASE)
BULLET = re.compile(r"^\s*[-*]\s+(?P<text>\S.*)$")
SEP = re.compile(r"\s+[—–-]\s+")
LOC = re.compile(r"^[^\s:]+:\d+$")
MARK = "debt-key:"


def _sh(args, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True, check=True).stdout


def _bullets(review):
    """(section, text) for every bullet of the checklist, the one extra line joined onto the finding above it."""
    out, section = [], None
    for line in review.splitlines():
        text = line.strip()
        m = SECTION.match(text)
        if m:
            section = m.group(1).capitalize()
            continue
        if not text or text.startswith("#"):
            continue
        found = BULLET.match(line)
        if found and section:
            out.append([section, found["text"]])
        elif section and out:
            out[-1][1] += " " + text
    return out


def parse(review):
    """The DEBT findings of `review` as dicts of loc, defect and acceptance."""
    if not any(SECTION.match(line.strip()) for line in review.splitlines()):
        out = []   # a review saved before the checklist shape: an inline DEBT tag on the line
        for line in review.splitlines():
            m = FINDING.match(line.strip())
            if m:
                out.append({"loc": m["loc"], "defect": m["defect"].rstrip(" ."), "ac": (m["ac"] or "").strip()})
        return out
    out = []
    for section, text in _bullets(review):
        if section != "Debt":
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


def _base(sh, owner, project):
    """The project's REST path, after the owner kind (an org and a user share the endpoint but not the prefix)."""
    kind = "orgs" if json.loads(sh(["gh", "api", f"users/{owner}"]))["type"] == "Organization" else "users"
    return f"/{kind}/{owner}/projectsV2/{project}"


def board_items(sh, owner, project, repo):
    """The numbers of `repo`'s issues already on the board, read the way board.py reads them (REST, paginated)."""
    out = sh(["gh", "api", "--paginate", f"{_base(sh, owner, project)}/items?per_page=100", "--jq",
              '.[]|select(.content.number)|[.content.number,(.content.repository_url//"")]|@json'])
    return {str(n) for n, ru in (json.loads(row) for row in out.splitlines() if row.strip())
            if not repo or ru.endswith("/" + repo)}


def card(sh, repo, owner, project, number, items, status=BOARD_STATUS):
    """Put issue `number` on the board in `status` if it has no card; `items` is the board index, mutated in place.

    The move board.py would do with `--add`. An issue already on the board keeps the Status it has: the reconciler
    owns it from there, not the filer. A board with no `status` option is a configuration this filer cannot invent
    its way out of, so it raises and the loop holds the merge with the reason -- a filed task nobody can see is not
    a filed task.
    """
    if str(number) in items:
        return
    base = _base(sh, owner, project)
    field = next((f for f in json.loads(sh(["gh", "api", f"{base}/fields?per_page=100"])) if f.get("name") == "Status"), None)
    opts = {o["name"]["raw"] if isinstance(o["name"], dict) else o["name"]: o["id"] for o in field["options"]}
    if status not in opts:
        raise RuntimeError(f"the board has no Status option {status!r} (it has: {', '.join(opts)})")
    iid = json.loads(sh(["gh", "api", f"repos/{repo}/issues/{number}"]))["id"]
    item = json.loads(sh(["gh", "api", "--method", "POST", f"{base}/items", "--input", "-"],
                         input=json.dumps({"type": "Issue", "id": iid})))["id"]
    sh(["gh", "api", "--method", "PATCH", f"{base}/items/{item}", "--input", "-"],
       input=json.dumps({"fields": [{"id": field["id"], "value": opts[status]}]}))
    items.add(str(number))


def file_debt(repo, pr, issue, review, head, runner=None, owner="", project="", status=BOARD_STATUS):
    """File one issue per DEBT finding not already filed (on any head); return the new issue URLs.

    `owner` and `project` are the board the new issues are added to; empty means the caller runs no board (a manual
    filing outside the loop), and the issue is then filed without a card, said so on stderr rather than silently.
    """
    sh = runner or _sh
    findings = parse(review)
    if not findings:
        return []
    try:
        sh(["gh", "api", f"repos/{repo}/labels/debt"])
    except subprocess.CalledProcessError:
        sh(["gh", "api", "--method", "POST", f"repos/{repo}/labels", "--input", "-"],
           input=json.dumps({"name": "debt", "color": "d93f0b", "description": "Review debt: a non-blocking finding"}))
    seen = json.loads(sh(["gh", "api", "--paginate", f"repos/{repo}/issues?labels=debt&state=all&per_page=100"]))
    board = board_items(sh, owner, project, repo) if owner and project else set()
    filed = []
    for f in findings:
        # the issue this finding owns: the one already filed for it, or the one opened here -- the card is owed in
        # both cases, because a run that filed it without a card left it invisible (#1838)
        hit = next((i for i in seen if f"{MARK}{key(f)}" in (i.get("body") or "")), None)
        if hit:
            number = hit["number"]
        else:
            body = {"title": f"Debt: {f['defect'][:80]} ({f['loc']})", "body": _body(f, pr, issue, head),
                    "labels": list(LABELS)}
            out = json.loads(sh(["gh", "api", "--method", "POST", f"repos/{repo}/issues", "--input", "-"],
                                input=json.dumps(body)))
            seen.append({"number": out["number"], "body": body["body"]})   # one key, one issue
            filed.append(out["html_url"])
            number = out["number"]
        if owner and project:
            card(sh, repo, owner, project, number, board, status)
        elif not hit:
            print(f"#{number} filed without a card: no board (--owner/--project or SG_OWNER/SG_PROJECT)",
                  file=sys.stderr)
    return filed


def main(argv=None):
    p = argparse.ArgumentParser(description="File a review's DEBT findings as tasks.")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--head", required=True, help="head sha of the reviewed PR")
    p.add_argument("--review", required=True, help="path of the saved review")
    p.add_argument("--owner", default=os.environ.get("SG_OWNER", ""), help="project board owner (default: $SG_OWNER)")
    p.add_argument("--project", default=os.environ.get("SG_PROJECT", ""), help="project board number (default: $SG_PROJECT)")
    p.add_argument("--status", default=BOARD_STATUS, help=f"Status a filed task waits in (default: {BOARD_STATUS})")
    a = p.parse_args(argv)
    with open(a.review, encoding="utf-8") as fh:
        urls = file_debt(a.repo, a.pr, a.issue, fh.read(), a.head,
                         owner=a.owner, project=a.project, status=a.status)
    print("\n".join(urls) if urls else "no new DEBT findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
