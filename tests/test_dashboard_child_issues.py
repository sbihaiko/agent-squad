"""A child that works a cluster shows every issue it works (#954): the label named only the first number, so a child
holding three issues read as one and its card set looked complete when it was not."""
from tests.dashboard_helpers import run_node


def _spawn(prompt, task="tdd"):
    return {"event": "spawn_accepted", "child": "route.router.1", "envelope": "route", "parent": "router", "depth": 1,
            "definition": {"prompt": prompt, "task": task, "skill": {"name": "tdd", "version": "1"}, "model": "m", "effort": "medium", "after": []}}


def _label(prompt):
    return run_node([{"op": "children", "events": [_spawn(prompt)]}])[0][0]["label"]


def test_a_child_of_a_cluster_names_every_issue_it_works():
    assert _label("CHILD — GitHub issue(s) 897, 908, 910 (work in the worktree the run gives you)") == "issues #897, #908, #910"


def test_a_child_of_one_issue_keeps_the_singular_label():
    assert _label("One child for GitHub issue #1500, and a note on issue #1487") == "issue #1500"


def test_a_prompt_with_no_issue_still_falls_back_to_its_skill():
    assert _label("a child whose prompt names no issue at all") == "tdd"
