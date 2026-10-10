"""Ticket #99: every node card's title is a role label derived from what the node is, not its internal name.

Display only: the label never writes the graph and never enters the graph hash. A gate shows its check id, an
action its effect, a wait its owner, an agent its skill (else its own name), and a dispatch child the task it ran (the issue
number, since every child of one dispatch usually runs the same skill and the skill alone would title them all alike). A
node the rule does not cover falls back to its name, so the label is never empty. A loop, an each, and a dispatch carry no
role field of their own and keep their own name, the only token that tells two of them apart. The child fixture is the real
four-wave run; its child `route.router.1` ran issue 1 with the `tdd` skill.
"""
import json

from tests.dashboard_helpers import ROOT, run_node
from tests.helpers import five, node, router

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"


def _label(n):
    return run_node([{"op": "label", "node": n}])[0]


def _children(events):
    return run_node([{"op": "children", "events": events}])[0]


def _run():
    doc = json.loads((FIX / "graph.json").read_text())
    events = run_node([{"op": "record", "text": (FIX / "run" / "record.jsonl").read_text()}])[0]["events"]
    return doc, events


def test_a_gate_card_shows_its_check_id():
    d = router()
    assert _label(node(d, "scope_gate")) == "scope"
    assert _label(node(d, "security_gate")) == "security"
    assert _label(node(d, "final_gate")) == "ac-verify"


def test_an_action_card_shows_its_effect():
    d = five()
    assert _label(node(d, "worktree")) == "worktree_create"
    assert _label(node(d, "merge")) == "merge_land"
    assert _label(node(d, "commit")) == "commit"


def test_a_wait_card_shows_its_owner():
    assert _label(node(five(), "manual_merge")) == "rafael"


def test_an_agent_card_shows_its_skill_and_a_skillless_agent_keeps_its_name():
    d = router()
    assert _label(node(d, "router")) == "ask-matt"           # an agent with a skill shows the skill
    assert _label(node(five(), "scout")) == "scout"          # an agent with no skill falls back to its own name


def test_a_loop_an_each_and_a_dispatch_keep_their_own_name():
    d = five()
    assert _label(node(d, "main")) == "main"                 # a root loop, no role field
    assert _label(node(d, "execute")) == "execute"           # an each, the bound is on the detail line
    assert _label(node(router(), "route")) == "route"        # a dispatch


def test_a_dispatch_child_is_titled_by_the_issue_it_ran():
    doc, events = _run()
    kids = {c["name"]: c for c in _children(events)}
    # The task is the title, not the skill: every child of the router's dispatch runs `tdd`, so the skill alone would
    # make every card read the same. The skill moves to the detail line.
    assert kids["route.router.1"]["label"] == "issue #19"     # the fixture's first child worked issue 19
    assert kids["route.router.1"]["skillLabel"] == "tdd"
    assert kids["route.router.1"]["skill"] == {"name": "tdd", "version": "1"}
    assert kids["route.router.1"]["name"] == "route.router.1"  # the full name stays for the tooltip


def test_a_child_with_no_task_falls_back_to_its_skill_then_its_short_name_never_empty():
    skillonly = _children([{"event": "spawn_accepted", "child": "route.router.8", "envelope": "route", "definition": {"prompt": "do the work", "skill": "tdd"}}])
    assert skillonly[0]["label"] == "tdd" and skillonly[0]["issue"] is None
    plain = _children([{"event": "spawn_accepted", "child": "route.router.9", "envelope": "route", "definition": {"prompt": "do the work"}}])
    assert plain[0]["label"] == "router.9" and plain[0]["skill"] is None
    assert plain[0]["issue"] is None


def test_no_two_cards_in_the_router_graph_share_a_title_and_none_is_empty():
    labels = [_label(n) for n in router()["nodes"]]
    assert all(labels), labels                                   # never empty
    assert len(set(labels)) == len(labels), labels               # no two cards share a title


def test_no_node_of_either_fixture_and_no_degenerate_node_yields_an_empty_label():
    for d in (five(), router()):
        for n in d["nodes"]:
            assert _label(n), n
    for weird in ({}, {"name": ""}, {"kind": "gate"}, {"kind": "agent"}, {"kind": "wait"}, {"kind": "action"}, {"kind": "loop"}):
        assert _label(weird), weird


def test_a_tracker_reference_names_the_issue_and_another_numbered_file_does_not():
    # A GitHub-style prompt: the issue is "#1179"; the ADR file it cites ("0272-...md") is not the issue.
    gh = _children([{"event": "spawn_accepted", "child": "route.router.1", "envelope": "route", "definition": {
        "prompt": "GitHub issue #1179: read docs/adr/gui/0272-the-gui-test-hook.md first", "skill": "tdd"}}])
    assert gh[0]["issue"] == 1179 and gh[0]["label"] == "issue #1179"
    # A numbered file outside issues/ alone names no issue.
    adr = _children([{"event": "spawn_accepted", "child": "route.router.2", "envelope": "route", "definition": {
        "prompt": "read docs/adr/gui/0272-the-gui-test-hook.md", "skill": "tdd"}}])
    assert adr[0]["issue"] is None and adr[0]["label"] == "tdd"
