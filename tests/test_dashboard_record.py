"""Run record statistics, following a live run, and the card state."""
import json

import pytest

from squad import graph, report
from tests.dashboard_helpers import PAGE_SRC, ROOT, _run22, _run22_tokens, run_node
from tests.helpers import DESIGN


# --- run record and overlay ---
def _applied_record(graph_hash):
    return [
        {"event": "run_start", "pos": 1, "run_id": "run-0001", "graph_name": "five", "graph_hash": graph_hash, "engine_options": {}, "root": "main", "plan": []},
        {"event": "outcome", "pos": 2, "node": "spec_draft", "entry": 1, "outcome": "fail", "reasons": ["x"], "artifact": "spec", "spend": 1.5, "turns": 10, "cap": None, "elapsed": 30.0},
        {"event": "applied", "pos": 3, "proposal_id": "p1", "kind": "shrink_stalls", "target": "spec_draft", "old": 20, "requested": 15, "range": {"min": 10, "max": 20},
         "facts": [{"name": "run_spend", "value": 1.5, "instrument": "code: x", "source": "record", "timestamp": "t"}], "proposer": "spec_draft"},
        {"event": "outcome", "pos": 4, "node": "spec_draft", "entry": 2, "outcome": "pass", "reasons": [], "artifact": "spec", "spend": 1.25, "turns": 8, "cap": None, "elapsed": 20.0},
    ]


def test_the_overlay_rows_equal_the_engine_report():
    ev = _applied_record("h")
    assert run_node([{"op": "rows", "events": ev}])[0] == json.loads(json.dumps(report.build(ev)))


def test_the_overlay_shows_only_when_the_record_hash_equals_the_graph_hash():
    text = (DESIGN / "five-phases.graph.json").read_text()
    h = graph.canon_hash(json.loads(text))
    match, miss = run_node([{"op": "overlay", "text": text, "events": _applied_record(h)}, {"op": "overlay", "text": text, "events": _applied_record("0" * 64)}])
    assert match["shown"] and len(match["rows"]) == 1 and match["rows"][0]["proposal_id"] == "p1"
    assert not miss["shown"] and "rows" not in miss


def test_a_record_opens_read_only_and_the_shipped_record_matches_the_shipped_graph():
    rec = (DESIGN / "router.record.jsonl").read_text()
    parsed = run_node([{"op": "record", "text": rec}, {"op": "record", "text": "not json"}, {"op": "record", "text": '{"event":"verdict"}'}])
    assert "events" in parsed[0] and "error" in parsed[1] and "error" in parsed[2]
    text = (DESIGN / "router.graph.json").read_text()
    assert run_node([{"op": "overlay", "text": text, "events": parsed[0]["events"]}])[0]["shown"] is True


def test_the_statistics_of_a_real_run_come_from_the_fields_the_record_has():
    doc, events = _run22()
    s = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True}])[0]
    assert s["state"] == "completed" and s["verdict"]["exit_code"] == 0
    assert s["totals"]["spend"] == pytest.approx(0.0414606 + 0.19228799999999996) and s["totals"]["turns"] == 9
    assert s["totals"]["cap"] == 10 and s["totals"]["cap_pct"] == pytest.approx(s["totals"]["spend"] / 10)
    assert s["totals"]["wall"] == pytest.approx(events[-1]["ts"] - events[0]["ts"])
    spec = s["nodes"]["spec"]
    assert (spec["outcome"], spec["routed"], spec["turns"], spec["entries"]) == ("pass", "continue", 3, 1)
    assert spec["elapsed"] == pytest.approx(7.667778458009707) and spec["spend"] == pytest.approx(0.0414606) and spec["cap_pct"] == pytest.approx(0.0414606 / 2.0)


def test_a_field_the_record_lacks_is_null_and_never_zero():
    doc, events = _run22()
    nodes = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True}])[0]["nodes"]
    assert nodes["scope_gate"]["spend"] is None and nodes["scope_gate"]["turns"] is None and nodes["scope_gate"]["elapsed"] is None and nodes["scope_gate"]["cap_pct"] is None
    assert nodes["worktree"]["spend"] is None and nodes["worktree"]["elapsed"] == pytest.approx(0.13002345801214688)
    router = json.loads((DESIGN / "router.graph.json").read_text())
    rev = run_node([{"op": "record", "text": (DESIGN / "router.record.jsonl").read_text()}])[0]["events"]
    t = run_node([{"op": "stats", "events": rev, "doc": router, "shown": True}])[0]["totals"]
    assert t["spend"] is None and t["turns"] is None and t["wall"] is None and t["cap_pct"] is None and t["cap"] == router["run"]["spend_cap"]


def test_a_node_with_two_entries_sums_its_cost_and_shows_the_last_outcome_and_the_worst_cap_use():
    doc, events = _run22()
    ev = [events[0]] + _applied_record("x")[1:2] + _applied_record("x")[3:]
    ev[1]["cap"], ev[2]["cap"] = 2.0, 2.0
    n = run_node([{"op": "stats", "events": ev, "doc": doc, "shown": True}])[0]["nodes"]["spec_draft"]
    assert n["entries"] == 2 and n["outcome"] == "pass" and n["spend"] == pytest.approx(2.75) and n["turns"] == 18 and n["elapsed"] == pytest.approx(50.0)
    assert n["cap_pct"] == pytest.approx(0.75)


def test_a_record_without_a_verdict_has_no_verdict_yet_and_a_started_call_has_no_outcome():
    doc, events = _run22()
    open_ev = [e for e in events if e["event"] != "verdict" and not (e["event"] == "outcome" and e["node"] == "actor")]
    s = run_node([{"op": "stats", "events": open_ev, "doc": doc, "shown": True}])[0]
    assert s["state"] == "no verdict yet" and s["verdict"] is None
    assert s["nodes"]["actor"]["outcome"] is None and s["nodes"]["actor"]["started"] is True


def test_the_harness_is_the_graph_setting_the_outcome_value_and_the_engine_options_of_the_run():
    doc, events = _run22()
    s = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True}])[0]
    h = s["nodes"]["spec"]["harness"]
    assert h["configured"] == {"model": "claude-sonnet-5-5", "effort": "medium", "tools": ["Read", "Grep", "Glob"]} and h["used"] == {"model": None, "effort": None}
    assert s["nodes"]["worktree"]["harness"] is None  # an action node has no harness
    assert s["engine"]["permission_mode"] == "bypassPermissions" and "WebFetch" in s["engine"]["disallowed_tools"] and s["engine"]["max_output_tokens"] == 32000
    new = json.loads(json.dumps(events))
    next(e for e in new if e["event"] == "outcome" and e["node"] == "spec").update({"model": "claude-haiku-4-5", "effort": "low"})
    used = run_node([{"op": "stats", "events": new, "doc": doc, "shown": True}])[0]["nodes"]["spec"]["harness"]["used"]
    assert used == {"model": "claude-haiku-4-5", "effort": "low"}


def test_a_graph_hash_mismatch_keeps_the_timeline_and_the_verdict_only():
    doc, events = _run22()
    s = run_node([{"op": "stats", "events": events, "doc": doc, "shown": False}])[0]
    assert set(s) == {"run_id", "graph_name", "state", "verdict", "timeline"}
    assert s["state"] == "completed" and s["verdict"]["verdict"] == "completed"
    assert [t["node"] for t in s["timeline"] if t["event"] == "outcome"][:3] == ["spec", "worktree", "actor"]
    assert s["timeline"][0]["at"] == 0 and all(set(t) == {"pos", "at", "event", "node", "entry", "outcome"} for t in s["timeline"])


# --- following a live run ---
def _live(events, now):
    return run_node([{"op": "live", "events": events, "now": now}])[0]


def test_the_reread_interval_comes_from_run_start_and_an_older_record_uses_one_second():
    _, events = _run22()
    def delay(ev):
        return run_node([{"op": "delay", "events": ev}])[0]
    assert "dashboard" not in events[0]["defaults"]["engine"] and delay(events) == 1      # a record made before #80
    for value, want in [(5, 5), (0.5, 0.5), (0, 1), (-2, 1), ("fast", 1), (None, 1), (True, 1)]:
        ev = json.loads(json.dumps(events))
        ev[0]["defaults"]["engine"]["dashboard"] = {"reread_seconds": value}
        assert delay(ev) == want, value
    bare = json.loads(json.dumps(events))
    del bare[0]["defaults"]
    assert delay(bare) == 1


def test_the_reread_interval_an_engine_run_records_is_the_one_the_dashboard_reads(tmp_path):
    from squad import cli, defaults, record
    from tests.test_cli import two_agents, write
    slow = tmp_path / "slow.yaml"
    slow.write_text(defaults.PATH.read_text().replace("reread_seconds: 1 ", "reread_seconds: 4 "))
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--defaults", str(slow)]) == 0
    assert run_node([{"op": "delay", "events": record.read(tmp_path / "r" / "record.jsonl")}])[0] == 4


@pytest.mark.heavy
def test_a_growing_record_runs_then_completes_and_its_totals_never_shrink():
    doc, events = _run22()
    last_spend, states = 0, []
    for k in range(1, len(events) + 1):
        now = events[k - 1]["ts"] + 1
        s = run_node([{"op": "stats", "events": events[:k], "doc": doc, "shown": True, "now": now}])[0]
        states.append(s["state"])
        spend = s["totals"]["spend"] or 0
        assert spend >= last_spend and len(s["timeline"]) == k
        last_spend = spend
    assert states[:-1] == ["running"] * (len(events) - 1) and states[-1] == "completed"


def test_the_node_in_progress_shows_the_time_since_its_call_started():
    doc, events = _run22()
    started = events[4]["started_at"]
    assert events[4]["event"] == "call_start" and events[4]["node"] == "actor"
    live = _live(events[:5], started + 12)
    assert live["state"] == "running"
    assert live["in_progress"] == [{"node": "actor", "entry": 1, "item": None, "since": pytest.approx(12)}]
    assert live["since_last"] == pytest.approx(started + 12 - events[4]["ts"])
    s = run_node([{"op": "stats", "events": events[:5], "doc": doc, "shown": True, "now": started + 12}])[0]
    assert s["nodes"]["actor"]["running_for"] == pytest.approx(12) and s["nodes"]["spec"]["running_for"] is None
    assert s["live"]["in_progress"][0]["node"] == "actor"
    assert s["totals"]["wall"] == pytest.approx(started + 12 - events[0]["ts"])                  # a run with no verdict has run until now
    done = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True, "now": events[-1]["ts"] + 500}])[0]
    assert done["totals"]["wall"] == pytest.approx(events[-1]["ts"] - events[0]["ts"])             # a finished run keeps its own end
    assert _live(events, started + 9999)["in_progress"] == []                      # a finished call is not in progress


def test_a_run_with_no_growth_for_the_stated_period_shows_as_stalled():
    _, events = _run22()
    mid, last = events[:2], events[1]["ts"]                                           # the spec call is in flight, limit 900 s
    assert _live(mid, last + 900 + 59)["state"] == "running"
    assert _live(mid, last + 900 + 61)["state"] == "stalled" and _live(mid, last)["stall_after"] == 960
    idle = events[:4]                                                                 # no call in flight: 300 s plus the 60 s grace
    assert _live(idle, events[3]["ts"] + 359)["state"] == "running"
    assert _live(idle, events[3]["ts"] + 361)["state"] == "stalled" and _live(idle, events[3]["ts"])["stall_after"] == 360


def test_a_call_with_no_deadline_never_makes_the_run_stalled():
    _, events = _run22()
    ev = json.loads(json.dumps(events[:2]))
    ev[1]["limit"] = None                                                             # the engine records no limit for an infinite deadline
    live = _live(ev, ev[1]["ts"] + 10 ** 6)
    assert live["state"] == "running" and live["stall_after"] is None


def test_a_finished_run_is_never_stalled_and_a_verdict_other_than_completed_keeps_its_name():
    _, events = _run22()
    assert _live(events, events[-1]["ts"] + 10 ** 6)["state"] == "completed"
    failed = json.loads(json.dumps(events))
    failed[-1]["verdict"] = "failed"
    assert _live(failed, failed[-1]["ts"] + 1)["state"] == "failed"


def test_without_a_clock_the_statistics_keep_the_snapshot_state_and_the_hash_mismatch_view_still_shows_live_state():
    doc, events = _run22()
    s = run_node([{"op": "stats", "events": events[:5], "doc": doc, "shown": True}])[0]
    assert s["state"] == "no verdict yet" and "live" not in s
    m = run_node([{"op": "stats", "events": events[:5], "doc": doc, "shown": False, "now": events[4]["ts"] + 1}])[0]
    assert m["state"] == "running" and set(m) == {"run_id", "graph_name", "state", "verdict", "timeline", "live"}


def test_a_reread_that_catches_a_half_written_last_line_drops_it_and_keeps_the_complete_lines():
    _, events = _run22()
    lines = [json.dumps(e) for e in events[:3]]
    whole = run_node([{"op": "parse_live", "text": "\n".join(lines) + "\n"}])[0]
    cut = run_node([{"op": "parse_live", "text": "\n".join(lines) + '\n{"event": "outc'}])[0]
    assert len(whole["events"]) == 3 and cut == whole
    mid = run_node([{"op": "parse_live", "text": lines[0] + "\nnot json\n" + lines[1] + "\n"}])[0]
    assert len(mid["events"]) == 1 and mid["bad"]["line"] == 2    # a bad complete line ends the record there, and is named
    assert "error" in run_node([{"op": "parse_live", "text": "not json\n" + lines[0] + "\n"}])[0]   # no valid prefix: still an error
    assert "error" in run_node([{"op": "parse_live", "text": '{"event": "run_st'}])[0]                              # nothing complete yet


@pytest.mark.heavy
def test_a_missing_value_shows_a_dash_and_a_value_shows_its_unit():
    f = lambda kind, value: run_node([{"op": "fmt", "kind": kind, "value": value}])[0]  # noqa: E731
    assert [f(k, None) for k in ("seconds", "usd", "pct", "count")] == ["\u2014"] * 4
    assert f("seconds", 7.667) == "7.7s" and f("seconds", 0.13) == "0.13s" and f("seconds", 125) == "2m 05s" and f("seconds", 0) == "0s"
    assert f("usd", 0.0414606) == "$0.041" and f("usd", 0) == "$0.000" and f("usd", 12.3456) == "$12.35"
    assert f("pct", 0.0207) == "2%" and f("pct", 0.004) == "<1%" and f("pct", 0) == "0%" and f("count", 3) == "3"


def test_tokens_per_second_is_tokens_out_over_elapsed_and_a_record_without_tokens_gives_null_never_zero():
    doc, events = _run22_tokens()
    s = run_node([{"op": "stats", "events": events, "doc": doc, "shown": True}])[0]
    spec = s["nodes"]["spec"]
    assert (spec["tokens_in"], spec["tokens_out"]) == (1200, 400) and spec["tps"] == pytest.approx(400 / 7.667778458009707)
    assert s["nodes"]["actor"]["tps"] == pytest.approx(6000 / 25.13335620801081)
    for name in ("worktree", "scope_gate", "merge"):  # no tokens in the record for these nodes
        n = s["nodes"][name]
        assert n["tokens_in"] is None and n["tokens_out"] is None and n["tps"] is None
    t = s["totals"]  # the total rate counts only the calls that have tokens: tokens out over their elapsed time
    assert (t["tokens_in"], t["tokens_out"]) == (31200, 6400)
    assert t["tps"] == pytest.approx(6400 / (7.667778458009707 + 25.13335620801081))
    doc0, events0 = _run22()  # the old record has no tokens anywhere
    old = run_node([{"op": "stats", "events": events0, "doc": doc0, "shown": True}])[0]
    assert all(n["tokens_in"] is None and n["tokens_out"] is None and n["tps"] is None for n in old["nodes"].values())
    assert old["totals"]["tokens_in"] is None and old["totals"]["tokens_out"] is None and old["totals"]["tps"] is None


def test_a_call_with_zero_elapsed_gives_no_rate_and_two_entries_sum_their_tokens():
    doc, events = _run22_tokens()
    ev = json.loads(json.dumps(events))
    next(e for e in ev if e["event"] == "outcome" and e["node"] == "spec")["elapsed"] = 0
    n = run_node([{"op": "stats", "events": ev, "doc": doc, "shown": True}])[0]["nodes"]["spec"]
    assert n["tps"] is None and n["tokens_out"] == 400  # tokens stay; a rate over no time is unknown, not infinite
    two = [events[0]] + _applied_record("x")[1:2] + _applied_record("x")[3:]
    for e, (i, o) in zip(two[1:3], ((100, 50), (200, 150)), strict=False):
        e.update(tokens_in=i, tokens_out=o)
    m = run_node([{"op": "stats", "events": two, "doc": doc, "shown": True}])[0]["nodes"]["spec_draft"]
    assert (m["tokens_in"], m["tokens_out"]) == (300, 200)


@pytest.mark.heavy
def test_tokens_and_the_rate_show_a_dash_when_missing_and_a_short_unit_when_present():
    f = lambda kind, value: run_node([{"op": "fmt", "kind": kind, "value": value}])[0]  # noqa: E731
    assert f("tokens", None) == "\u2014" and f("rate", None) == "\u2014"
    assert f("tokens", 0) == "0" and f("tokens", 950) == "950" and f("tokens", 1200) == "1.2k" and f("tokens", 31200) == "31k" and f("tokens", 9999) == "10k" and f("tokens", 1250000) == "1.3M"
    assert f("rate", 52.2) == "52 tk/s" and f("rate", 3.14) == "3.1 tk/s" and f("rate", 0) == "0 tk/s"


# --- the card's state, the top bar, and the issue a child ran ---
RUN_ISSUES = ROOT / "tests" / "fixtures" / "run-router-issues"


def _state(ns):
    return run_node([{"op": "state", "ns": ns}])[0]


def _state_class(ns):
    return run_node([{"op": "stateclass", "ns": ns}])[0]


@pytest.mark.heavy
def test_a_nodes_card_state_resolves_for_every_state_and_a_node_that_never_ran_is_not_grey():
    assert _state({"outcome": "pass"}) == "passed"
    assert _state({"outcome": "fail"}) == "failed"
    assert _state({"outcome": "deadline"}) == "deadline"
    assert _state({"outcome": "needs-human"}) == "needs-human"
    assert _state({"started": True, "outcome": None}) == "started"
    assert _state({"not_run": True}) == "not-run"
    assert _state({"running_for": 3.0, "outcome": "pass"}) == "running"       # running now wins over an earlier outcome
    assert _state(None) is None                                              # not in the record: no state to show
    # and the class each state paints, matching the legend's own classes
    assert [_state_class({"outcome": "pass"}), _state_class({"outcome": "fail"})] == ["o-pass", "o-fail"]
    assert [_state_class({"outcome": "deadline"}), _state_class({"outcome": "needs-human"})] == ["o-deadline", "o-needs-human"]
    assert [_state_class({"started": True, "outcome": None}), _state_class({"not_run": True})] == ["o-started", "o-not-run"]
    assert _state_class({"running_for": 1.0}) == "running" and _state_class(None) is None


def test_the_top_bar_counts_the_landed_issues_of_a_multi_issue_run_over_the_issue_set():
    events = run_node([{"op": "record", "text": (RUN_ISSUES / "run" / "record.jsonl").read_text()}])[0]["events"]
    total = len(list((RUN_ISSUES / "target" / "issues").glob("*.md")))       # the run folder holds the issue set
    p = run_node([{"op": "issues", "events": events, "total": total}])[0]
    assert total == 24 and (p["done"], p["total"], p["text"]) == (18, 24, "18 of 24 issues")
    cells = run_node([{"op": "issuecells", "events": events, "total": total}])[0]
    assert [c["id"] for c in cells] == ["issues"] and cells[0]["text"] == "18 of 24 issues" and "land" in cells[0]["title"]
    # no issue set (a run folder without target/issues): no bar, and a plain landed count that never says "of"
    assert run_node([{"op": "issues", "events": events, "total": None}])[0] == {"done": 18, "total": None, "text": "18 landed"}
    assert run_node([{"op": "issuecells", "events": events, "total": None}])[0] == []


def test_the_top_bar_counts_a_one_issue_run_by_its_passing_merge_once_it_completes():       # #154
    merge = {"event": "outcome", "node": "merge", "outcome": "pass", "effect": {"name": "merge_land", "head": "abc"}}
    running = run_node([{"op": "issues", "events": [merge], "total": 24}])[0]
    done = run_node([{"op": "issues", "events": [merge, {"event": "verdict", "verdict": "completed"}], "total": 24}])[0]
    failed = run_node([{"op": "issues", "events": [merge, {"event": "verdict", "verdict": "failed"}], "total": 24}])[0]
    assert (running["done"], running["total"]) == (0, 1) and (failed["done"], failed["total"]) == (0, 1)
    assert (done["done"], done["total"], done["text"]) == (1, 1, "1 of 1 issue")      # the folder's 24 issue files are the subject, not the run's set


def test_the_top_bar_of_a_chained_run_counts_the_issues_its_start_tree_held():       # #157
    merge = {"event": "outcome", "node": "merge", "outcome": "pass", "effect": {"name": "merge_land", "head": "abc"}}
    p = run_node([{"op": "issues", "events": [merge, {"event": "verdict", "verdict": "completed"}], "total": 24, "held": 5}])[0]
    assert (p["done"], p["total"], p["text"]) == (6, 24, "6 of 24 issues")
    start = run_node([{"op": "issues", "events": [], "total": 24, "held": 5}])[0]       # the run has not merged yet: the held issues still count
    assert (start["done"], start["total"]) == (5, 24)


def test_the_page_shows_the_top_bar_not_run_state_and_child_issue_from_the_core():
    src = PAGE_SRC()
    assert 'id="progress"' in src and "core.progressCells(core.issueProgress(" in src     # the top bar, from the core
    assert "core.stateClass(ns)" in src and ".card.o-not-run rect" in src                  # a never-run node is not grey
    assert "esc(c.label" in src and "c.skillLabel" in src   # a child card is titled by its task, its role on the line under
    assert src.index('id="progress"') < src.index('id="mapbox"')                          # the bar sits above the map


def test_a_record_corrupt_mid_file_keeps_its_valid_prefix_and_names_the_bad_line_ADR_0126():
    lines = (DESIGN / "router.record.jsonl").read_text().splitlines()
    cut = "\n".join(lines[:4] + ["{not json"] + lines[4:]) + "\n"
    r, nonobj, first_bad, empty = run_node([{"op": "record", "text": cut}, {"op": "record", "text": "\n".join(lines[:3] + ["[1]"]) + "\n"},
                                            {"op": "record", "text": "{no\n" + "\n".join(lines)}, {"op": "record", "text": ""}])
    assert [e["event"] for e in r["events"]] == [json.loads(x)["event"] for x in lines[:4]]   # the prefix, never a skip past the bad line
    assert r["bad"]["line"] == 5 and r["bad"]["shown"] == 4 and r["bad"]["message"]
    assert len(nonobj["events"]) == 3 and nonobj["bad"]["line"] == 4 and "JSON object" in nonobj["bad"]["message"]
    assert "events" not in first_bad and "line 1 is not JSON" in first_bad["error"]          # no valid prefix: refused, as before
    assert "run_start" in empty["error"]


def test_a_gate_the_engine_ran_is_never_read_as_not_run():       # #731
    """The record now holds a `gate_start` and an outcome for every gate a dispatch runs. A gate the record names is a
    node with its outcome, never "not run: the record has no call for this node, and no dispatch checked it"."""
    doc = json.loads((DESIGN / "router.graph.json").read_text())
    ev = [{"event": "run_start", "pos": 1, "run_id": "r", "graph_name": doc["name"], "graph_hash": "h", "engine_options": {},
           "root": "main", "plan": []},
          {"event": "gate_start", "pos": 2, "node": "review_gate", "entry": 1, "item": None, "pid": 1, "limit": None, "started_at": 1000.0},
          {"event": "outcome", "pos": 3, "node": "review_gate", "entry": 1, "outcome": "pass", "reasons": [], "artifact": None,
           "spend": None, "turns": None, "cap": None, "elapsed": None},
          {"event": "verdict", "pos": 4, "verdict": "completed", "exit_code": 0, "reasons": [], "remains": [], "folded": []}]
    s = run_node([{"op": "stats", "events": ev, "doc": doc, "shown": True}])[0]
    g = s["nodes"]["review_gate"]
    assert g["outcome"] == "pass" and g["running_for"] is None and not g.get("not_run")
    assert run_node([{"op": "state", "ns": g}])[0] == "passed"
    assert s["nodes"]["scope_gate"]["not_run"] is True      # a gate the record never names is still reported as not run
