"""A child is cut from origin/dev, pushes its branch and opens no pull request.

The dispatch prompts (frontier, bug), the request checklist and the example profile are text the loop hands a child. None
of it asks for a pull request; the RED output goes to the issue status comment, and the profile and the prepush range
name dev as the base.
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


def test_prompts_say_branch_from_origin_dev_push_and_open_no_pull_request():
    for f in ("autofrontier.sh", "autobug.sh"):
        text = read(f)
        assert "push your branch" in text, f
        assert "open no pull request" in text, f


def test_red_output_goes_in_the_issue_status_comment():
    for f in ("req-checklist.txt", "autobug.sh", "profiles/example.sh"):
        text = read(f)
        assert "RED output in the issue status comment" in text, f


def test_profile_and_prepush_range_use_dev():
    assert ': "${SG_BASE:=dev}"' in read("profiles/example.sh")
    assert 'origin/${SG_BASE:-dev}' in read("prepush.sh")
    assert 'origin/${SG_BASE:-main}' not in read("prepush.sh")
