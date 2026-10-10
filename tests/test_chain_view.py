"""Guard for #157: a chained subject run has one dashboard page, built from its issue runs (squad/chain_view.py)."""
import json

from squad import chain_view, record


def _issue_run(d, verdict, start=None, end=None):
    r = record.Record(d / "record.jsonl")
    r.append("outcome", node="actor", entry=1, outcome="pass", reasons=[], artifact=None, spend=0.5, turns=7, cap=None, elapsed=3.0, **({"ts": start} if start else {}))
    if verdict:
        r.append("verdict", **({"ts": end} if end else {}), verdict=verdict, exit_code=0, reasons=[], remains=[], folded=[])


def _bench(tmp_path, n=3):
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "issues.json").write_text(json.dumps([{"issue": i, "blocked_by": []} for i in range(1, n + 1)]))
    return bench


def test_the_view_has_a_node_per_issue_a_land_per_passed_issue_and_one_running_call(tmp_path):
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "completed")
    _issue_run(runs / "subject-02-x", "failed")
    _issue_run(runs / "subject-03-x", None)
    subject = tmp_path / "subject"
    (subject / "issues").mkdir(parents=True)
    view = chain_view.from_runs(runs, _bench(tmp_path), subject)
    events = record.read(view / "record.jsonl")
    lands = [e["node"] for e in events if e["event"] == "land"]
    outcomes = {e["node"]: e["outcome"] for e in events if e["event"] == "outcome" and e["node"] != "chain"}
    assert lands == ["route.chain.1"] and outcomes == {"route.chain.1": "pass", "route.chain.2": "fail"}
    assert [e["node"] for e in events if e["event"] == "call_start"] == ["route.chain.3"]
    assert [e["definition"]["task"] for e in events if e["event"] == "spawn_accepted"] == [1, 2, 3]      # a dispatch child per issue: the page groups them
    assert not [e for e in events if e["event"] == "verdict"]
    assert (view / "target").is_symlink()          # the page counts the issue set under target/issues


def test_a_finished_chain_view_ends_with_a_verdict(tmp_path):
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "completed")
    view = chain_view.from_runs(runs, _bench(tmp_path, 1), tmp_path, done=True)
    assert record.read(view / "record.jsonl")[-1]["verdict"] == "completed"


def test_the_chain_graph_has_no_router_skill_and_only_the_held_out_gate(tmp_path):
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "completed")
    view = chain_view.from_runs(runs, _bench(tmp_path, 1), tmp_path)
    nodes = json.loads((view / "graph.json").read_text())["nodes"]
    assert "ask-matt" not in json.dumps(nodes) and {n["name"] for n in nodes if n["kind"] == "gate"} == {"held_out"}


def test_the_cards_follow_the_run_order_and_the_time_is_the_chains_wall_time(tmp_path):
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-03-x", "completed", start=1000.0, end=1100.0)     # a blocker ran first, whatever its number
    _issue_run(runs / "subject-01-x", "completed", start=1100.0, end=1700.0)
    view = chain_view.from_runs(runs, _bench(tmp_path), tmp_path, done=True)
    events = record.read(view / "record.jsonl")
    assert [e["definition"]["task"] for e in events if e["event"] == "spawn_accepted"] == [3, 1, 2]    # started first, then the issue still to come
    assert events[0]["ts"] == 1000.0 and events[-1]["ts"] == 1700.0                                         # the page's Time reads 700 s


def test_the_final_all_tests_result_is_the_held_out_gate_and_a_red_one_fails_the_chain(tmp_path):
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "completed", start=1000.0, end=1100.0)
    bench = _bench(tmp_path, 1)
    ok = record.read(chain_view.from_runs(runs, bench, tmp_path, done=True, final={"ok": True, "text": "24 passed"}) / "record.jsonl")
    assert [e["outcome"] for e in ok if e.get("node") == "held_out"] == ["pass"] and ok[-1]["verdict"] == "completed"
    bad = record.read(chain_view.from_runs(runs, bench, tmp_path, done=True, final={"ok": False, "text": "1 failed"}) / "record.jsonl")
    assert [e["outcome"] for e in bad if e.get("node") == "held_out"] == ["fail"] and bad[-1]["verdict"] == "failed"


def test_a_finished_chain_ends_its_dispatch_and_its_loop_so_the_page_shows_no_unrun_node(tmp_path):
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "completed", start=1000.0, end=1100.0)
    events = record.read(chain_view.from_runs(runs, _bench(tmp_path, 1), tmp_path, done=True, final={"ok": True, "text": ""}) / "record.jsonl")
    assert {e["node"]: e["outcome"] for e in events if e["event"] == "outcome" and e["node"] in ("route", "main", "held_out")} == {"route": "pass", "main": "pass", "held_out": "pass"}


def _verdict(view):
    events = record.read(view / "record.jsonl")
    return [e for e in events if e["event"] == "verdict"][-1], events


def test_a_chain_where_no_issue_landed_is_failed_and_says_how_many_landed_of_how_many(tmp_path):      # #200
    runs = tmp_path / "runs"
    runs.mkdir()
    view = chain_view.from_runs(runs, _bench(tmp_path, 3), tmp_path, done=True, final={"ok": True, "text": ""})      # no issue run was found
    v, events = _verdict(view)
    assert v["verdict"] == "failed" and v["exit_code"] == 1 and "0 of 3 issues landed" in v["reasons"][0]
    assert not [e for e in events if e["event"] == "land"]


def test_a_mixed_chain_counts_failed_skipped_and_exit_zero_issues_the_same_in_verdict_strip_and_cards(tmp_path):      # #200
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "failed")
    _issue_run(runs / "subject-02-x", "completed")
    _issue_run(runs / "subject-03-x", None)      # exit 0 with no verdict in the folder: the chain's exit code wins
    view = chain_view.from_runs(runs, _bench(tmp_path, 4), tmp_path, done=True, status={1: 1, 2: 0, 3: 0, 4: "skipped"})
    v, events = _verdict(view)
    lands = {e["node"] for e in events if e["event"] == "land"}
    passed = {e["node"] for e in events if e["event"] == "outcome" and e["outcome"] == "pass" and e["node"].startswith("route.chain.")}
    assert lands == passed and len(lands) == 2
    assert v["verdict"] == "failed" and v["exit_code"] == 1 and "2 of 4 issues landed" in v["reasons"][0]


def test_a_chain_where_every_issue_landed_is_completed_and_says_so(tmp_path):      # #200
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-01-x", "completed")
    v, _ = _verdict(chain_view.from_runs(runs, _bench(tmp_path, 1), tmp_path, done=True, final={"ok": True, "text": ""}))
    assert v["verdict"] == "completed" and v["exit_code"] == 0 and v["reasons"] == []


def test_a_chain_limited_to_some_issues_counts_only_those(tmp_path):      # #200
    runs = tmp_path / "runs"
    _issue_run(runs / "subject-02-x", "completed")
    v, _ = _verdict(chain_view.from_runs(runs, _bench(tmp_path, 3), tmp_path, done=True, issues=[2]))
    assert v["verdict"] == "completed"
