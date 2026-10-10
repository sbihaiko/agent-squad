"""A call in flight reads as running, with everything the record knows about it.

Guards the defect the owner found on 2026-10-02: a node that starts never showed the running state on the
dashboard. The child of a dispatch stayed on `started` — a dashed outline and three dashes — for the whole
call, up to sixteen minutes in the real run, then jumped to its result. A card in flight must show that it runs,
for how long, and the last turn count and token count the record has (the last turn is the
evidence that a call is live, not abandoned).
"""
import json

import pytest

from tests.dashboard_helpers import ROOT, run_node

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"


def _run():
    doc = json.loads((FIX / "graph.json").read_text())
    events = run_node([{"op": "record", "text": (FIX / "run" / "record.jsonl").read_text()}])[0]["events"]
    return doc, events


def _started(events, name):
    return next(e for e in events if e["event"] == "call_start" and e["node"] == name)


def _cut(events, name):
    """The record as it stood while `name` was in flight: every event up to and including its call_start."""
    s = _started(events, name)
    return [e for e in events if e["pos"] <= s["pos"]], s


def _kids(events, now=None):
    op = {"op": "children", "events": events}
    if now is not None:
        op["now"] = now
    return run_node([op])[0]


def _child_class(child):
    return run_node([{"op": "childclass", "c": child}])[0]


# ---- the child of a dispatch, in flight
def test_a_child_in_flight_reads_running_with_the_time_since_its_call_started():
    _, events = _run()
    cut, s = _cut(events, "route.router.1")
    live = _child_class(_kids(cut, s["started_at"] + 12)[0])
    assert live == "running"
    assert _kids(cut, s["started_at"] + 12)[0]["running_for"] == pytest.approx(12)


def test_a_child_in_flight_with_no_clock_reads_still_started():
    """The replay cursor reads the record without a clock, so a call in flight keeps its static state there."""
    _, events = _run()
    cut, _ = _cut(events, "route.router.1")
    k = _kids(cut)[0]                                   # no `now`: no live clock
    assert k["outcome"] == "started" and k["running_for"] is None and _child_class(k) == "o-started"


def test_a_child_that_has_not_started_carries_no_state_at_all():
    _, events = _run()
    cut = [e for e in events if e["event"] != "call_start" or e["node"] != "route.router.1"]
    cut = [e for e in cut if e["pos"] <= 6]
    k = _kids(cut, cut[-1]["ts"] + 1)[0]
    assert k["outcome"] is None and k["running_for"] is None and _child_class(k) is None


def test_a_child_that_finished_is_not_running():
    _, events = _run()
    k = _kids(events, events[-1]["ts"] + 600)[0]
    assert k["outcome"] == "pass" and k["running_for"] is None and _child_class(k) == "o-pass"


# ---- what the record says about a call in flight
def test_the_last_progress_event_of_a_call_in_flight_is_what_the_card_shows():
    _, events = _run()
    cut, s = _cut(events, "route.router.1")
    for messages, ctx, tool, at in [(4, 12000, "Read", 20), (7, 31000, "Bash", 42)]:
        cut = [*cut, {"event": "progress", "node": "route.router.1", "entry": s["entry"], "session": s["session"], "messages": messages,
                      "context_tokens": ctx, "tool": tool, "pos": len(cut) + 1.0, "ts": s["started_at"] + at}]
    k = _kids(cut, s["started_at"] + 50)[0]
    assert k["live"] == {"messages": 7, "context_tokens": 31000, "tool": "Bash"}   # the last one wins; the readings never sum
    assert k["running_for"] == pytest.approx(50) and k["turns"] is None and k["tokens_in"] is None


def test_a_progress_event_of_a_finished_call_does_not_mark_it_running():
    _, events = _run()
    cut, s = _cut(events, "route.router.1")
    cut = [*cut, {"event": "progress", "node": "route.router.1", "entry": s["entry"], "session": s["session"], "messages": 4,
                  "context_tokens": 1, "tool": None, "pos": len(cut) + 1.0, "ts": s["started_at"] + 5}]
    done = [e for e in events if e["event"] == "outcome" and e["node"] == "route.router.1"]
    cut = [*cut, *done]
    k = _kids(cut, cut[-1]["ts"] + 600)[0]
    assert k["outcome"] == "pass" and k["live"] is None and _child_class(k) == "o-pass"


def test_a_call_left_open_by_a_finished_run_never_reads_as_running_again():
    """A killed run can end with a call_start and no outcome. After the verdict nothing is in flight."""
    doc, events = _run()
    cut, _ = _cut(events, "route.router.1")
    v = next(e for e in events if e["event"] == "verdict")
    cut = [*cut, {**v, "pos": len(cut) + 1.0}]
    st = run_node([{"op": "stats", "events": cut, "doc": doc, "shown": True, "now": cut[-1]["ts"] + 600}])[0]
    assert st["state"] == v["verdict"] and st["nodes"]["route.router.1"]["running_for"] is None
    assert run_node([{"op": "children", "events": cut, "now": cut[-1]["ts"] + 600}])[0][0]["running_for"] is None


# ---- a graph node, in flight
def test_a_node_in_flight_carries_its_live_figures_and_its_tool():
    doc, events = _run()
    cut, s = _cut(events, "router")
    cut = [*cut, {"event": "progress", "node": "router", "entry": s["entry"], "session": s["session"], "messages": 2,
                  "context_tokens": 900, "tool": "Grep", "pos": len(cut) + 1.0, "ts": s["started_at"] + 9}]
    st = run_node([{"op": "stats", "events": cut, "doc": doc, "shown": True, "now": s["started_at"] + 30}])[0]
    n = st["nodes"]["router"]
    assert n["running_for"] == pytest.approx(30)
    assert n["live"] == {"messages": 2, "context_tokens": 900, "tool": "Grep"}
    done = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True, "now": events[-1]["ts"] + 600}])[0]
    assert done["nodes"]["router"]["live"] is None


# ---- the page paints what the core decides
def test_the_child_card_paints_the_class_the_core_decides_and_shows_its_live_figures():
    from tests.dashboard_helpers import PAGE_SRC
    src = PAGE_SRC()
    assert 'const sc = core.childStateClass(c);' in src                 # the class is the core's decision, never `c.outcome`
    assert '" o-" + oc' not in src
    assert "runningHead(c.running_for, live)" in src and "progressLine(live)" in src


def test_the_running_card_shows_the_live_turns_and_tokens():
    from tests.dashboard_helpers import PAGE_SRC
    src = PAGE_SRC()
    assert "ns.live" in src and "▶ " in src
