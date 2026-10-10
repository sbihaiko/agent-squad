"""One dashboard page for a whole chained subject run.

A chain (`scripts/run_subject_chain.py`) makes one run folder per issue. This module writes a view folder the dashboard
opens as one run: a graph with one node per issue, in run order, and a record built from the issue runs. A passed issue is
an `outcome` pass and a `land` event, so the strip counts it. A failed issue is an outcome fail, a skipped one a
deadline-free fail with the reason `skipped`. A running issue is a `call_start`. The folder is rebuilt after each issue.
The view is a dispatch with a child per issue, so the page groups the issues as it groups a router's children. It is a read-only summary: the issue runs hold the real records.
"""
import json
import os
import time
from pathlib import Path

from squad import record


def _name(n):
    return f"issue_{n:02d}"


def _totals(run):
    """Spend, turns and elapsed seconds of one issue run, from its own record."""
    spend = turns = elapsed = 0.0
    try:
        events = record.read(Path(run) / "record.jsonl")
    except record.RecordError:
        return 0.0, 0, 0.0
    for e in events:
        if e["event"] == "outcome":
            spend += e.get("spend") or 0
            turns += e.get("turns") or 0
            elapsed += e.get("elapsed") or 0
    return round(spend, 4), int(turns), round(elapsed, 1)


def graph_doc():
    """The chain as a dispatch: a `chain` node that proposes one child per issue, no router skill; the held-out gate stands for the check each issue run makes."""
    return {"schema_version": 1, "name": "subject-chain", "description": "A chain: every subject issue runs the five-phase graph in its own run, one after the other, on one growing tree.",
            "run": {"deadline": 86400, "max_parallel": 1, "spend_cap": 1000}, "facts": [], "gates": [{"id": "ac-verify", "params": ["commands_from"]}], "root": "main",
            "nodes": [{"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["route"]},
                      {"name": "route", "kind": "dispatch", "entry": "chain", "max_depth": 1, "max_children": 24, "max_parallel": 1, "deadline": 86400, "join": "all",
                       "allow": {"models": ["haiku", "sonnet", "opus"], "efforts": ["low", "medium", "high"], "tools": ["Read"], "skills": [{"name": "tdd"}], "workspaces": ["worktrees/child"]},
                       "budget": {"stalls": {"min": 1, "max": 5}, "spend": {"min": 0.05, "max": 4}}, "checks": ["held_out"]},
                      {"name": "held_out", "kind": "gate", "check": "ac-verify", "params": {"commands_from": "route"}},
                      {"name": "chain", "kind": "agent", "model": "sonnet", "tools": ["Read"], "budget": {"stalls": {"min": 1, "max": 1}, "spend": {"min": 0.01, "max": 1}},
                       "deadline": 60, "may_spawn": True, "output_key": "spawns", "next": {"fail": "exit:fail"}}]}


def _issue_files(subject):
    return {int(p.name[:2]): p.name for p in sorted((Path(subject) / "issues").glob("[0-9][0-9]-*.md"))}


def write(view, order, results, subject, running=None, done=False, final=None):
    """Rebuild the view folder `view` as a dynamic run: one dispatch with a child per issue, in run order, so the page groups
    them as it groups a router's children. `results` maps an issue to {"status", "run"} (status 0 passed, "skipped", or an exit
    code), `running` is the issue now running, `done` ends the run with a verdict, `final` is the result of all held-out
    tests on the last tree, {"ok": bool, "text": str}: the page shows it as the `held_out` gate."""
    from squad import graph
    view = Path(view)
    view.mkdir(parents=True, exist_ok=True)
    doc = graph_doc()
    gp = view / "graph.json"
    gp.write_text(json.dumps(doc, indent=1))
    g = graph.load(gp)
    target = view / "target"
    if not target.exists() and not target.is_symlink():
        os.symlink(subject, target)     # the page counts the issue set under target/issues
    files = _issue_files(subject)
    rec = view / "record.jsonl"
    rec.unlink(missing_ok=True)
    r = record.Record(rec)
    t0 = min([x["start"] for x in results.values() if x.get("start")] or [None], default=None) or time.time()
    r.append("run_start", ts=t0, run_id="chain-view", graph_name=doc["name"], graph_hash=g.hash, engine_options={}, root=doc["root"], plan=[x["name"] for x in doc["nodes"]])
    r.append("outcome", node="chain", entry=1, outcome="pass", reasons=[], artifact=None, spend=0.0, turns=1, cap=None, elapsed=0.0)
    for i, n in enumerate(order, 1):
        child = f"route.chain.{i}"
        d = {"prompt": f"Issue {n} of the chain: see issues/{files.get(n, f'{n:02d}-issue.md')}.", "task": n, "model": "haiku"}
        r.append("spawn_requested", parent="chain", parent_entry=1, depth=1, proposer_session="chain", requested=d)
        r.append("spawn_accepted", request_key=i, child=child, parent="chain", depth=1, envelope="route", reservation=0.0, parent_entry=1, definition=d)
    for i, n in enumerate(order, 1):
        child, res = f"route.chain.{i}", results.get(n)
        if res is None:
            if n == running:
                r.append("call_start", node=child, entry=1, session=f"issue-{n}", pid=0, limit={}, started_at=r.events[-1]["ts"])
            continue
        spend, turns, elapsed = _totals(res["run"]) if res.get("run") else (0.0, 0, 0.0)
        ok = res["status"] == 0
        reasons = [] if ok else ["skipped: a blocker did not land"] if res["status"] == "skipped" else [f"the issue run exited {res['status']}"]
        end = {"ts": res["end"]} if res.get("end") else {}      # the issue run's own end time, so the page's Time is the chain's wall time
        r.append("outcome", node=child, entry=1, outcome="pass" if ok else "fail", reasons=reasons, artifact=res.get("run"), spend=spend, turns=turns, cap=None, elapsed=elapsed, **end)
        if ok:
            r.append("land", **end, node=child, parent="route", entry=1, outcome="pass", reasons=[], effect={"name": "merge_land", "head": None, "held_out": "pass"})
    landed = sum(1 for n in order if (results.get(n) or {}).get("status") == 0)
    all_landed = landed == len(order)       # an issue with no result (no run found, never started) did not land
    if done:      # the dispatch and the loop ended with the chain: they read "not run" while the chain only looked unfinished
        bad = not all_landed
        for node in ("route", "main"):
            r.append("outcome", node=node, entry=1, outcome="fail" if bad else "pass", reasons=["an issue did not land"] if bad else [], artifact=None, spend=None, turns=None, cap=None, elapsed=0.0)
    if final is not None:
        r.append("outcome", node="held_out", entry=1, outcome="pass" if final["ok"] else "fail", reasons=[] if final["ok"] else ["final_tests_failed"],
                 artifact=None, spend=None, turns=None, cap=None, elapsed=0.0, evidence=None if final["ok"] else {"final_tests_failed": final.get("text", "")[-400:]})
    if done:
        final_bad = final is not None and not final["ok"]
        last = max([x["end"] for x in results.values() if x.get("end")] or [0]) or time.time()
        reasons = ([] if all_landed else [f"{landed} of {len(order)} issue{'' if len(order) == 1 else 's'} landed"]) + (["the final all-tests run failed"] if final_bad else [])
        r.append("verdict", ts=last, verdict="failed" if reasons else "completed", exit_code=1 if reasons else 0, reasons=reasons, remains=[], folded=[])
    return view


def from_runs(runs_dir, bench, subject, view=None, done=False, final=None, issues=None, status=None):
    """Rebuild the view from the issue runs under `runs_dir`: the newest `subject-NN-*` folder of each issue. A folder with a
    `completed` verdict passed, one with another verdict failed, one with no verdict is running. `issues` limits the chain to
    those issues (default: all in issues.json). `status` is the chain's own exit code per issue (0, "skipped" or another code):
    it wins over the folder, so an issue that exited 0 counts as landed. An issue with no run when the chain is done did not land."""
    runs_dir, bench = Path(runs_dir), Path(bench)
    listed = [r["issue"] for r in json.loads((bench / "issues.json").read_text())]
    if issues:
        listed = [n for n in listed if n in set(issues)]
    status = status or {}
    results, running, started = {}, None, {}
    for n in listed:
        folders = sorted(runs_dir.glob(f"subject-{n:02d}-*"))
        if not folders:
            if n in status:
                results[n] = {"status": status[n], "run": None}
            continue
        d = folders[-1]
        try:
            events = record.read(d / "record.jsonl")
        except record.RecordError:
            events = []
        verdicts = [e for e in events if e["event"] == "verdict"]
        started[n] = events[0].get("ts") if events else None
        if n in status:
            results[n] = {"status": status[n], "run": str(d), "start": started[n], "end": verdicts[-1].get("ts") if verdicts else None}
        elif not verdicts:
            running = n
        else:
            results[n] = {"status": 0 if verdicts[-1]["verdict"] == "completed" else 1, "run": str(d), "start": started[n], "end": verdicts[-1].get("ts")}
    # run order: the issues that started, by start time (the chain runs blockers first), then the ones still to come in issues.json order
    order = sorted((n for n in listed if started.get(n)), key=lambda n: started[n]) + [n for n in listed if not started.get(n)]
    return write(view or runs_dir / "chain-view", order, results, subject, running=running, done=done, final=final)


def main(argv=None):
    import argparse
    import time
    p = argparse.ArgumentParser(description="Write the one-page dashboard view of a chained subject run")
    p.add_argument("runs_dir")
    p.add_argument("--bench", default=str(Path(__file__).resolve().parent.parent / "bench" / "subject"))
    p.add_argument("--subject", default=str(Path.home() / "sources" / "subject"))
    p.add_argument("--watch", type=int, default=0, help="rebuild every N seconds until interrupted")
    a = p.parse_args(argv)
    while True:
        print(from_runs(a.runs_dir, a.bench, a.subject), flush=True)
        if not a.watch:
            return 0
        time.sleep(a.watch)


if __name__ == "__main__":
    raise SystemExit(main())
