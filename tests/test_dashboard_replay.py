"""The replay cursor, unresolved children, and the hash mismatch."""


from tests.dashboard_helpers import _run22, run_node


# ---- the replay cursor, unresolved children, and the hash mismatch ----
def _replay(events, cursor):
    return run_node([{"op": "replay", "events": events, "cursor": cursor}])[0]


def _move(events, cursor, move, to=None):
    return run_node([{"op": "move", "events": events, "cursor": cursor, "move": move, "to": to}])[0]


def test_the_cursor_folds_only_the_events_up_to_its_pos_into_each_card():
    doc, events = _run22()
    r = _replay(events, 5)
    assert r["cursor"] == 5 and r["first"] == 1 and r["last"] == 14 and r["at_end"] is False
    assert [e["pos"] for e in r["events"]] == [1, 2, 3, 4, 5]
    s = run_node([{"op": "stats", "events": r["events"], "doc": doc, "shown": True}])[0]
    assert s["nodes"]["spec"]["outcome"] == "pass" and s["nodes"]["actor"]["started"] is True and s["nodes"]["actor"]["outcome"] is None
    assert "verify" not in s["nodes"] and s["totals"]["turns"] == 3                  # only the spec outcome (3 turns) is in the cut


def test_the_verdict_shows_only_at_or_after_its_own_pos():
    doc, events = _run22()
    verdict_pos = events[-1]["pos"]
    before, at = _replay(events, verdict_pos - 1), _replay(events, verdict_pos)
    def stat(r):
        return run_node([{"op": "stats", "events": r["events"], "doc": doc, "shown": True}])[0]
    assert stat(before)["verdict"] is None and stat(before)["state"] == "no verdict yet"
    assert stat(at)["verdict"]["verdict"] == "completed" and stat(at)["state"] == "completed" and at["at_end"] is True


def test_a_cursor_outside_the_record_or_between_two_positions_lands_on_a_real_event():
    _, events = _run22()
    assert _replay(events, None)["cursor"] == 14 and _replay(events, 999)["cursor"] == 14
    assert _replay(events, 0)["cursor"] == 1 and _replay(events, -4)["cursor"] == 1
    assert _replay(events, 5.5)["cursor"] == 5                                         # a position between two events lands on the one before
    gap = [{**e, "pos": e["pos"] * 10} for e in events]                              # positions need not be dense
    assert _replay(gap, 55)["cursor"] == 50 and _replay(gap, 140)["at_end"] is True


def test_a_step_moves_one_event_and_a_step_to_the_end_pins_the_cursor():
    _, events = _run22()
    assert _move(events, 5, "forward") == 6 and _move(events, 5, "back") == 4
    assert _move(events, 1, "back") == 1                                               # no step before the first event
    assert _move(events, 13, "forward") is None and _move(events, 14, "forward") is None   # the end is "pinned": null
    assert _move(events, 5, "start") == 1 and _move(events, 5, "end") is None
    assert _move(events, None, "back") == 13                                           # pinned means at the end, so back goes to the one before
    assert _move(events, 5, "to", 9) == 9 and _move(events, 5, "to", 99) is None


def test_an_accepted_child_with_no_outcome_at_the_cursor_is_unresolved():
    def at(pos, **kw): return {"pos": pos, "ts": 100.0 + pos, **kw}
    events = [at(1, event="run_start", run_id="r"), at(2, event="spawn_accepted", child="route.router.1", parent="route", parent_entry=1, depth=1),
              at(3, event="spawn_accepted", child="route.router.2", parent="route", parent_entry=1, depth=1),
              at(4, event="outcome", node="route.router.1", entry=1, outcome="pass"),
              at(5, event="outcome", node="route", entry=1, outcome="pass"),                 # the parent finished: not a child
              at(6, event="verdict", verdict="completed")]
    def names(cursor):
        return [c["child"] for c in _replay(events, cursor)["unresolved"]]
    assert names(1) == [] and names(2) == ["route.router.1"] and names(3) == ["route.router.1", "route.router.2"]
    assert names(4) == ["route.router.2"] and names(6) == ["route.router.2"]               # a child that never ended stays listed to the end
    assert run_node([{"op": "unresolved", "events": events, "cursor": 3}])[0][1] == {"child": "route.router.2", "parent": "route", "parent_entry": 1, "depth": 1, "pos": 3}
    _, run22 = _run22()
    assert _replay(run22, 14)["unresolved"] == []                                          # a run with no spawned child has none


def test_a_hash_mismatch_keeps_the_timeline_and_the_verdict_to_the_cursor():
    doc, events = _run22()
    r = _replay(events, 6)
    m = run_node([{"op": "stats", "events": r["events"], "doc": doc, "shown": False}])[0]
    assert set(m) == {"run_id", "graph_name", "state", "verdict", "timeline"} and [t["pos"] for t in m["timeline"]] == [1, 2, 3, 4, 5, 6]
    end = run_node([{"op": "stats", "events": _replay(events, 14)["events"], "doc": doc, "shown": False}])[0]
    assert end["verdict"]["verdict"] == "completed" and len(end["timeline"]) == 14


def test_a_large_record_opens_with_no_index_and_the_cursor_cuts_it_by_position():
    n = 40000
    events = [{"pos": 1, "ts": 1.0, "event": "run_start", "run_id": "big"}] + [{"pos": i, "ts": float(i), "event": "outcome", "node": f"n{i}", "entry": 1, "outcome": "pass"} for i in range(2, n + 1)]
    r = _replay(events, 25000)
    assert r["cursor"] == 25000 and len(r["events"]) == 25000 and r["events"][-1]["pos"] == 25000
    assert set(r) == {"cursor", "first", "last", "at_end", "events", "unresolved"}          # the answer holds the cut and nothing else: no index
    assert _move(events, 25000, "forward") == 25001 and _move(events, n - 1, "forward") is None
