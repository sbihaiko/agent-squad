"""A child works in the worktree its run gives it, pushes no branch of its own and opens no pull request.

The dispatch prompts (frontier, bug), the request checklist, the worktree note and the example profile are text the loop
hands a child. None of it asks for a pull request, and none of it tells the child to cut or push a branch of its own: the
run cuts the child's worktree and checks out its branch, the loop enters the child's commits into the run's wave branch,
and the wave lands in dev (ADR-0145 rule 9). The RED output goes to the issue status comment.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
DISPATCH_TEXT = ["autofrontier.sh", "autobug.sh", "req-checklist.txt", "profiles/example.sh"]
ASKS_FOR_A_PR = re.compile(
    r"open(ing)? (the|a|your) PR|open the pull request|PR body|pull request body|\bone PR\b|\bPR each\b|one pull request"
    r"|gh pr create|--method POST repos/\S*/pulls|Merge after|with base \S+ and",
    re.IGNORECASE,
)


def read(name):
    return (SCRIPTS / name).read_text()


def test_no_dispatch_text_asks_a_child_to_open_a_pull_request():
    hits = [(f, m.group(0)) for f in DISPATCH_TEXT for m in ASKS_FOR_A_PR.finditer(read(f))]
    assert hits == []


def test_prompts_give_the_child_the_runs_worktree_and_no_branch_of_its_own():
    """ADR-0145 rule 9: the run cuts the child's worktree and its branch, so the dispatch points the child at the worktree it
    stands in and tells it to push no branch. The retired route's wording -- cut a branch from origin/dev and push it -- steered
    the child into a tree of its own that the wave entry never reads, so every run landed an empty wave (#464)."""
    for f in DISPATCH_TEXT + ["startability.sh"]:
        text = read(f)
        for gone in ("push your branch", "cut your branch from origin", "cut it from origin/$SG_BASE", "one branch each"):
            assert gone not in text, f"{f}: still tells a child to cut or push a branch of its own"
    for f in ("autofrontier.sh", "autobug.sh"):
        text = read(f)
        assert re.search(r"no worktree and no branch of (?:its|your) own", text), f
        assert "the run's wave branch" in text, f
        assert "worktree" in text and "origin/$SG_BASE" not in text.split("wave branch")[0], f


def test_red_output_goes_in_the_issue_status_comment():
    for f in ("req-checklist.txt", "autobug.sh", "profiles/example.sh"):
        text = read(f)
        assert "RED output in the issue status comment" in text, f


def test_profile_and_prepush_range_use_dev():
    assert ': "${SG_BASE:=dev}"' in read("profiles/example.sh")
    assert 'origin/${SG_BASE:-dev}' in read("prepush.sh")
    assert 'origin/${SG_BASE:-main}' not in read("prepush.sh")


def test_no_child_brief_sends_the_child_to_prepush_beside_the_fast_gate():
    """#1761: a child proves its change with the fast gate alone; a prepush run on top of it is a second proof of one tree."""
    for f in ("autofrontier.sh", "autobug.sh", "childfix.sh"):
        text = read(f)
        for phrase in ("Before the child reports done, run", "Before you report done, run", "Before every commit, run"):
            assert phrase not in text, (f, phrase)
        assert "$here/prepush.sh" not in text, f
    assert "Work where you stand" in read("childfix.sh")


def test_the_checklist_names_no_live_script_a_child_cannot_run():
    for line in read("req-checklist.txt").splitlines():
        if "serial.sh gui-suite" in line:
            assert "~/.claude" not in line and "$SG_LIVE_DIR" not in line, line
            assert "bash contrib/squad-goal/scripts/serial.sh gui-suite" in line, line


A_SCRIPTS_PATH = re.compile(r"(?<![\w/.~-])scripts/[A-Za-z0-9_./-]+\.(?:py|sh)")
A_SCRIPTS_DIR = re.compile(r"(?<![\w/.~-])(scripts/(?:[A-Za-z0-9_.-]+/)+)")
A_CHECK_NAME = re.compile(r"(?<![\w/.~-])verify_[A-Za-z0-9_]+\.(?:py|sh)")
CHECKLIST_ITEM = re.compile(r"\((\d+)\)(.*?)(?=\(\d+\)|$)", re.S)
CARRIES_IT = re.compile(r"\b(?:if|when|where) the repository carries\b", re.I)
NOTHING_TO_RUN = re.compile(r"nothing to run", re.I)


def _named_scripts_paths(item):
    """The paths an item names: a full scripts/... path, plus a bare check name that inherits the item's scripts directory."""
    named = A_SCRIPTS_PATH.findall(item)
    directories = A_SCRIPTS_DIR.findall(item)
    if directories:
        named += [directories[0] + n for n in A_CHECK_NAME.findall(item)]
    return list(dict.fromkeys(named))


def test_a_checklist_item_names_a_scripts_path_only_where_the_repository_carries_it():
    """#943: item (6) told every child that touches docs/adr to run scripts/checks/verify_adr_refs.py and its siblings, and
    this repository has no scripts/checks/, so the item was unsatisfiable and a child that amended an ADR ended its run
    failed (run-20261030-193032-a512c3). A path the repository lacks is named only under a condition, and the item says
    plainly what to do when it is absent."""
    text = read("req-checklist.txt")
    offenders = []
    for number, item in CHECKLIST_ITEM.findall(text):
        missing = [p for p in _named_scripts_paths(item) if not (ROOT / p).exists()]
        if missing and not (CARRIES_IT.search(item) and NOTHING_TO_RUN.search(item)):
            offenders.append((number, missing))
    assert offenders == [], (
        "the checklist names a scripts path this repository lacks, with no condition and no fallback: "
        f"{offenders}")
