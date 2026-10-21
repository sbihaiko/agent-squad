"""One reader maps a child's spawn text to its issues (#555): janitor.py, childcc.py and wave.sh asked the same question
with three regexes, and the router's own words ("GOAL (issue 548, ...)") matched none. Each dispatcher makes every
child carry `Refs #N`, so that line is a link every reader honours."""
import importlib.util
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/child_issues.py"


def _mod():
    spec = importlib.util.spec_from_file_location("child_issues", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROUTER = "Every commit message carries the line `Refs #548` on its own line.\n\nGOAL (issue 548, SPEC 465 child): rename."


def test_the_router_wording_maps_by_its_refs_line():
    m = _mod()
    assert m.first(ROUTER) == "548"
    assert m.named(ROUTER) == {"548"}


def test_the_older_forms_still_map_and_the_hash_form_wins_first():
    m = _mod()
    assert m.first("GitHub issue #1500 and GitHub issue 1487") == "1500"
    assert m.first("FORK-ONLY work for GitHub issue 1487") == "1487"
    assert m.first("FORK-ONLY work for GitHub issues 1491 on the fork") == "1491"
    assert m.first("git worktree add ~/wt-as-1492 -b gui/1492 local/next") == "1492"
    assert m.named("GitHub issues 11, 12 and issue #13") == {"11", "12", "13"}


def test_a_text_that_names_no_issue_maps_to_none():
    m = _mod()
    assert m.first("a child whose prompt names no issue at all") is None
    assert m.named("a child whose prompt names no issue at all") == set()


def test_a_json_encoded_prompt_maps_every_refs_line():
    """#725: a reader that scans a record line or json.dumps of the definition sees "\\nRefs #715", and `\\bRefs` found no
    word boundary after the escaped newline, so a bundled child read as unmapped and the janitor unheld its issues."""
    import json
    m = _mod()
    text = json.dumps({"prompt": "Deliver two issues of SPEC 481 in one slice.\n\n## Issue 715: a seam\nRefs #715\nRefs #717\n"})
    assert m.named(text) == {"715", "717"}
    assert m.first(text) == "715"
