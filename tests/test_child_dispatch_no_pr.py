"""A child works in the worktree its run gives it, pushes no branch of its own and opens no pull request.

The dispatch prompts (frontier, bug), the request checklist, the worktree note and the example profile are text the loop
hands a child. None of it asks for a pull request, and none of it tells the child to cut or push a branch of its own: the
run cuts the child's worktree and checks out its branch, the loop enters the child's commits into the run's wave branch,
and the wave lands in dev (ADR-0145 rule 9). The RED output goes to the issue status comment.
"""
import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
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


def test_the_request_and_the_fix_run_name_the_worktrees_own_prepush():
    """#662: the sandbox denies any command naming the live skill copy, so a request that prescribes the loop's own
    prepush path cannot be followed. The child works where it stands, so the relative path is its worktree's copy."""
    for f in ("autofrontier.sh", "autobug.sh"):
        text = read(f)
        assert "bash contrib/squad-goal/scripts/prepush.sh --local" in text, f
        assert "$here/prepush.sh" not in text, f
    fix = read("childfix.sh")
    # #740: the command is the profile's, agent-squad's by default, and a relative script path is anchored at the checkout, which
    # the fix child reaches from its own worktree. It never names the kept worktree as the place to write (#814).
    assert "SG_PREPUSH_CMD-bash contrib/squad-goal/scripts/prepush.sh --local" in fix
    assert "pc=${pc/#bash contrib\\//bash $SG_CHECKOUT/contrib/}" in fix
    assert "Work where you stand" in fix
    assert "$here/prepush.sh" not in fix


def test_the_checklist_names_no_live_script_a_child_cannot_run():
    for line in read("req-checklist.txt").splitlines():
        if "serial.sh gui-suite" in line:
            assert "~/.claude" not in line and "$SG_LIVE_DIR" not in line, line
            assert "bash contrib/squad-goal/scripts/serial.sh gui-suite" in line, line
