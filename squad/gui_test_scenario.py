"""Run every case of a `gui-test/1` scenario, batch by batch, on the squad's GUI test infrastructure (MesenAI #1286).

The run command (`scripts/gui_test_run_script.py`) runs one script against one adapter, one launch per batch, one
verdict per step. A whole scenario does not fit that shape: a batch whose cases are all `manual` needs an adapter that
only launches, while a batch with automated cases needs a target whose screens answer - so "every case of the scenario
was run" is a per-batch question, answered here in one call, with one row per case of the whole script in script order.

A batch the roster cannot serve is neither dropped nor silently green: its cases are `not executed` with the reason,
the same way the run command refuses a script the adapter does not advertise rather than skipping it (ADR-0272 item 6).

`blocked_by` is the issue plan's own edge (`to_test_scenario._setup_edges`, the "Blocked by" column of the scenario's
case issues): the case a step waits for is the batch's last setup step, and everything else in the batch waits for that
step too. A row carries it so a run of one case can be read against the cases that had to run first.
"""
from pathlib import Path

from squad.gui_test_run import run as run_one
from squad.to_test_scenario import _setup_edges, script_steps

NOT_EXECUTED = "not executed"


def cases(script):
    """One row per case of the script, in run order: id, batch, part, mode, blocked_by."""
    steps = script_steps(script)
    edges = _setup_edges(steps)
    return [{"id": s["id"], "batch": s["batch"], "part": s["part"], "mode": s["mode"],
             "blocked_by": edges.get(s["id"])} for s in steps]


def kind(verdict):
    """The group a verdict belongs to: `failed` for anything that failed, else the verdict itself."""
    if verdict.startswith("failed"):
        return "failed"
    return verdict if verdict in ("passed", "pending", NOT_EXECUTED) else NOT_EXECUTED


def summary_of(rows):
    counts = {"passed": 0, "failed": 0, "pending": 0, NOT_EXECUTED: 0}
    for r in rows:
        counts[kind(r["verdict"])] += 1
    return {**counts, "cases": len(rows), "run": counts["passed"] + counts["failed"]}


def outcome(rows, batches):
    """`(status, reasons)`: what makes a scenario run red - a failed case, a batch no adapter served, or no case run.

    A `pending` case never makes it red (nobody ran it yet, and the run command says the same); a batch that could not
    run does, because this module exists to say every listed case *can* be run (#1286), and a run with nothing behind
    it is the green-but-blind result the ADR names as the trap.
    """
    counts = summary_of(rows)
    reasons = []
    if counts["run"] == 0:
        reasons.append("no case executed")
    for b in batches:
        if b["verdict"] == NOT_EXECUTED:
            reasons.append(f"batch {b['id']} was not executed: {b['reason']}")
    failed = [r["id"] for r in rows if kind(r["verdict"]) == "failed"]
    if failed:
        reasons.append(f"{len(failed)} case(s) failed: " + ", ".join(failed))
    return ("failed" if reasons else "passed"), reasons


def run_scenario(script, adapters, out_root=None, prompt=None, record=None):
    """Run every case of the whole scenario; return {"rows", "batches", "summary", "status", "reasons"}.

    `adapters` maps a batch id to the adapter factory that serves it, called with the batch's own output directory. A
    batch missing from it leaves its cases `not executed` and names the batch as the reason - a roster that forgot a
    batch is visible in the rows, never a green run over nothing. A `manual` case is put to `prompt` (a person) and
    ends `pending` without one, so a run nobody watched says so instead of claiming a verdict.
    """
    by_id = {c["id"]: dict(c) for c in cases(script)}
    rows, batches = [], []
    for batch in script["batches"]:
        batch_id, adapter = batch["id"], adapters.get(batch["id"])
        if adapter is None:
            verdicts = {s["id"]: f"{NOT_EXECUTED} (no adapter for batch {batch_id})"
                        for part in ("setup", "steps", "teardown") for s in batch.get(part) or []}
            batches.append({"id": batch_id, "verdict": NOT_EXECUTED,
                            "reason": f"the roster has no adapter for batch {batch_id}"})
        else:
            out_dir = Path(out_root or ".") / batch_id
            out_dir.mkdir(parents=True, exist_ok=True)  # the batch's own output directory, as the run command gives it
            session = adapter(out_dir)
            session.launch(script.get("fixtures") or {})
            got = run_one({**script, "batches": [batch]}, session, record=record, prompt=prompt, root=out_root)
            verdicts = {r["id"]: r["verdict"] for r in got}
            batches.append({"id": batch_id, "verdict": "passed" if not any(v.startswith("failed") for v in verdicts.values())
                            else "failed"})
        for part in ("setup", "steps", "teardown"):
            for s in batch.get(part) or []:
                rows.append({**by_id[s["id"]],
                             "verdict": verdicts.get(s["id"], f"{NOT_EXECUTED} (the adapter reported nothing for it)")})
    status, reasons = outcome(rows, batches)
    return {"rows": rows, "batches": batches, "summary": summary_of(rows), "status": status, "reasons": reasons}
