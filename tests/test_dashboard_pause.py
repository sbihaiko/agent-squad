"""Ticket #107: the dashboard shows where a pause leaves the run, as the record holds it (ADR-0128, ADR-0117 rule 10)."""
import re

from tests.dashboard_helpers import PAGE_SRC, run_node

START = {"event": "run_start", "run_id": "run-x", "pos": 1}
PAUSED = {"event": "paused", "node": "b", "scope": "nodes", "held": ["b"], "completed": ["a"], "pos": 5}
STOPPED = {"event": "verdict", "verdict": "stopped", "reasons": ["paused:b"], "pos": 6}


def view(*events):
    return run_node([{"op": "pause", "events": list(events)}])[0]


def test_a_run_with_no_verdict_and_no_pause_is_live():
    assert view(START)["state"] == "live"


def test_a_run_that_ended_on_a_pause_shows_the_engines_held_and_completed_lists_and_the_resume_command():
    v = view(START, PAUSED, STOPPED)
    assert (v["state"], v["node"], v["held"], v["completed"], v["resume"]) == ("paused", "b", ["b"], ["a"], "squad resume run-x")


def test_a_pause_with_no_verdict_yet_already_shows_where_the_run_stands():
    assert view(START, PAUSED)["state"] == "paused"


def test_a_resume_after_a_pause_makes_the_run_live_again():
    assert view(START, PAUSED, STOPPED, {"event": "resume", "pos": 7})["state"] == "live"


def test_a_finished_run_and_a_stop_after_run_are_ended_and_take_no_request():
    done = {"event": "verdict", "verdict": "completed", "reasons": [], "pos": 6}
    stop = {"event": "verdict", "verdict": "stopped", "reasons": ["stopped_after:a"], "pos": 6}
    assert view(START, done)["state"] == "ended" and view(START, stop)["state"] == "ended"


def test_a_request_body_names_the_nodes_or_the_whole_run():
    body = lambda action, nodes: run_node([{"op": "controlbody", "action": action, "nodes": nodes}])[0]   # noqa: E731
    assert body("pause", ["a", "b"]) == '{"action":"pause","nodes":["a","b"]}'
    assert body("resume", None) == '{"action":"resume"}' and body("pause", []) == '{"action":"pause"}'


def test_the_page_posts_only_to_the_control_path_and_never_launches_a_run():
    src = PAGE_SRC()
    posts = re.findall(r'method:\s*"POST"', src)
    assert len(posts) == 1 and 'fetch("run/control"' in src
    assert "spawn" not in src.lower() or "spawn_" in src.lower()      # the only `spawn` the page knows is the record's spawn_* events
