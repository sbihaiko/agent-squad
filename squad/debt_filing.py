"""File each DEBT finding of a saved review as a board task (ADR-0273 Decision 4).

The review is the checklist of `contrib/squad-goal/scripts/revformat.py` (#1777): the section a finding sits in is
its severity, and a bullet reads `- <file>:<line> — <defect> — <concrete failure> — <fix>`, at most two lines.
BLOCKING findings block the merge and are never filed; the acceptance criterion of a filed task is that fix.
The filer asks revformat.py what a finding is (`is_finding`), the same predicate the severity counts and the
`--summary` line `branchfixpass.sh` reads ask (#1834), so a bullet that answers a previous finding -- under a fix
round's `## Resolved`, or marked `fixed:` where it sits -- is filed by nobody.
A review saved before the checklist (an inline DEBT tag, no sections) is still read the old way.
The filed issue is put on the loop's project board when it is opened, and a finding whose issue is already filed
is carded too if it has none (#1838 -- an issue filed by a run that could not mint a card is repaired here): the frontier and the close passes read
the board, so an issue filed without a card is one nobody works and nobody closes, and every later move for it is a
no-op on stderr. The board is `$SG_OWNER`/`$SG_PROJECT`, the pair every other board move of the loop uses.
GitHub goes through REST only (`gh api`), never GraphQL. The card is asked of the seam that reads and writes the state
an issue carries (`issue_state.py`, #236); the filer holds no board route of its own.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import types

LABELS = ("debt", "enhancement")   # the type label keeps the issue in autobug's debt tier; `bug` would lift it into the bug tier (#427)
BOARD_STATUS = "Todo"   # where a filed task waits: the frontier and autobug read that column
FINDING = re.compile(r"^\W*DEBT\W+(?P<loc>\S+?:\d+)\s*[—–:-]+\s*(?P<defect>.*?)\s*(?:Acceptance:\s*(?P<ac>.*))?$",
                     re.IGNORECASE)   # the shape of a review saved before #1777: an inline DEBT tag on the line
SEP = re.compile(r"\s+[—–-]\s+")
LOC = re.compile(r"^[^\s:]+:\d+$")
MARK = "debt-key:"
_REVFORMAT: dict[str, types.ModuleType] = {}


def _sh(args, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True, check=True).stdout


def _revformat():
    """revformat.py, the module that reads the shape of a review and tells a finding from a fix round's answer
    (#1777, #1832, #1834). It is loaded from the checkout, next to review.sh: the filer runs as `squad.debt_filing`,
    so the skill's scripts directory is not on the path."""
    if "m" not in _REVFORMAT:
        try:
            _REVFORMAT["m"] = importlib.import_module("revformat")
        except ImportError:
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


class _Done:
    """The answer shape the seam's runner returns: the binding reads returncode, stdout and stderr."""

    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def board_state(sh, owner, project, repo):
    """The seam that reads and writes the state an issue carries (#236), over the loop's board. `issue_state.py` is
    loaded from the checkout beside revformat.py, and every call it makes goes through `sh`: the filer holds no board
    route of its own. The seam's caches sit in `$SG_STATE`, beside board.py's."""
    if "issue_state" not in _REVFORMAT:
        path = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
        spec = importlib.util.spec_from_file_location("issue_state", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _REVFORMAT["issue_state"] = m

    def run(args, input=None, capture_output=True, text=True, timeout=None):
        try:
            return _Done(0, sh(list(args), input=input))
        except subprocess.CalledProcessError as e:
            return _Done(e.returncode or 1, "", str(e.stderr or e))
    state_dir = os.environ.get("SG_STATE") or tempfile.mkdtemp(prefix="debt-filing-")
    return _REVFORMAT["issue_state"].board_state(owner=owner, project=project, repo=repo, kind=None, run=run,
                                                 state_dir=state_dir)


def board_items(state):
    """The numbers of the repository's issues already on the board, in one read of the seam."""
    return set(state.read_all())


def card(state, number, items, status=BOARD_STATUS):
    """Put issue `number` on the board in `status` if it has no card; `items` is the board index, mutated in place.

    The move board.py would do with `--add`, asked of the seam: the add, then a Status write the read that follows
    proves. An issue already on the board keeps the Status it has: the reconciler owns it from there, not the filer.
    A board with no `status` option is a configuration this filer cannot invent its way out of, so it raises and the
    loop holds the merge with the reason -- a filed task nobody can see is not a filed task. A write no read carries
    raises the seam's `NotCarried`, and the card is never counted as made.
    """
    if str(number) in items:
        return
    opts = state.binding.status_field()["opt"]
    if status not in opts:
        raise RuntimeError(f"the board has no Status option {status!r} (it has: {', '.join(opts)})")
    if not state.binding.item_id(number, add_missing=True):
        raise RuntimeError(f"#{number} was not added to the board")
    for k in range(state.binding.attempts):   # the items route is eventually consistent (#96): written again, then read back, as board.py does
        if k and state.binding.backoff:
            time.sleep(state.binding.backoff * (2 ** (k - 1)))
        try:
            state.write(number, status)
            break
        except RuntimeError:
            if k == state.binding.attempts - 1:
                raise
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
    state = board_state(sh, owner, project, repo) if owner and project else None
    board = board_items(state) if state else set()
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
            card(state, number, board, status)
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
