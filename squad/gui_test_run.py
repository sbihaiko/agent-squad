"""Minimal gui-test/1 runner: drives one adapter through automated steps; manual steps go to a person."""
import hashlib
from pathlib import Path

from squad.gui_test_checks import UNKNOWN, evaluate, holds, parse, runner_owned


def failed(results):
    """True when any step failed; a `pending` step (nobody ran it) is not a failure."""
    return any(r["verdict"].startswith("failed") for r in results)


def _screenshot(path):
    data = Path(path).read_bytes()
    return {"path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def wait_for(adapter, cond, timeout, root=None):
    """`met` or `timeout`. A runner-owned predicate (fs.*, log.*) is polled one adapter tick at a time, so the bound is
    ticks, never host time; anything else is the adapter's own wait."""
    if cond is None or not runner_owned(parse(cond)[0]):
        return adapter.wait(cond, timeout)
    for _ in range(timeout + 1):
        if holds(cond, adapter, root):
            return "met"
        getattr(adapter, "advance", lambda: None)()
    return "timeout"


def decide(adapter, check, root=None):
    """The verdict of one objective check; a failure carries expected and observed."""
    name, args = check["name"], check.get("args", {})
    if not runner_owned(name) and not hasattr(adapter, "observe"):
        got = adapter.check(name, args)
        return "failed (unknown id)" if got == UNKNOWN else "passed" if got else "failed"
    r = evaluate(name, args, adapter, root)
    if r["observed"] == UNKNOWN:
        return "failed (unknown id)"
    return "passed" if r["ok"] else f"failed (expected {r['expected']}, observed {r['observed']})"


def goal_route(adapter):
    """Who a `nav.goal` step belongs to on this adapter: `"read"`, `"adapter"` or `"none"` (#1780).

    The hook's `nav.goal` injects nothing: it answers with the state a move is chosen from and the moves it will accept,
    and the Choice stays the runner's (ADR-0271 items 1-2 and 6 A, hook version 2 since #1779). An adapter that answers
    that read declares it under `reads`, so the runner asks it and hands the goal to the Navigator. An adapter that
    lists `nav.goal` among the actions it runs and answers no read drives the goal itself, and the step's own wait and
    check are what decide it, the way every other action of that adapter is decided. An adapter that advertises it in
    neither list has no goal to give: nothing is asked and the step ends `pending`, as a run with no chooser does.
    """
    caps = adapter.capabilities()
    if "nav.goal" in caps.get("reads", ()):
        return "read"
    return "adapter" if "nav.goal" in caps.get("actions", ()) else "none"


def run(script, adapter, record=None, prompt=None, root=None, vision=None, goal=None):
    """Run every step in order; return [{id, verdict, capture, held}]. Never raises on a wait timeout.

    A `manual` step is put to `prompt(step)`, which returns {"verdict": "passed"|"failed", "screenshot": path}; with no
    prompt, or when it returns None, the step ends `pending`. With no adapter (unavailable) every step is a manual step,
    so the whole script runs as a checklist with the same record.

    With a `vision` (squad.gui_test_vision.Vision), an automated step with no `check` takes the model's verdict on its
    capture: `compatible` passes, anything else is `needs review`; a step with a `check` follows the check and is never
    sent. A `nav.goal` step is reached by `goal` (squad.gui_test_goal.Navigator); with none (CI, ADR-0271 Decision 6 A) it
    ends `pending`, and a goal not reached ends `failed (goal not reached ...)`. An adapter that answers the hook's
    `nav.goal` read is asked for it first (#1780), which is what the Navigator chooses among; one that drives the goal
    itself runs the step the way it runs every other action (`goal_route`). Once the vision spend reaches its cap the run stops: every later step ends `skipped (spend cap ...)`.

    `record` takes one replay event per call: gui_step, then gui_capture (only once the wait is met), then gui_verdict.
    """
    results, pos, seen = [], 0, None

    def emit(event, node, **more):
        nonlocal pos
        if record:
            record({"event": event, "node": node, "pos": pos, **more})
        pos += 1

    for batch in script["batches"]:
        for s in batch["setup"] + batch["steps"] + batch["teardown"]:
            if vision and vision.exhausted:
                reason = vision.stop_reason()
                if not any(r["verdict"].startswith("skipped (spend cap") for r in results):
                    emit("gui_stop", s["id"], reason=reason, spent=vision.spent)
                emit("gui_step", s["id"])
                emit("gui_verdict", s["id"], verdict=f"skipped ({reason})")
                results.append({"id": s["id"], "verdict": f"skipped ({reason})", "capture": None, "held": False})
                continue
            if s["mode"] != "automated" or adapter is None:
                answer = prompt(s) if prompt else None
                emit("gui_step", s["id"], manual=True)
                capture = None
                if answer:
                    capture = _screenshot(answer["screenshot"])
                    emit("gui_capture", s["id"], **capture)
                verdict = answer["verdict"] if answer else "pending"
                emit("gui_verdict", s["id"], verdict=verdict)
                results.append({"id": s["id"], "verdict": verdict, "capture": capture, "held": False})
                continue
            action = s.get("action")  # an observe-only step (the validator allows it) injects nothing
            route = goal_route(adapter) if action and action["name"] == "nav.goal" else ""
            emit("gui_step", s["id"], **({"action": action["name"]} if action else {}),
                 **({"check": s["check"]} if "check" in s else {}))
            if route in ("read", "none"):
                # #1780: since #1779 the hook answers `nav.goal` itself - the state the move is chosen from and the
                # moves it will accept (ADR-0271 items 1-2) - so an adapter that answers that read is asked, and the
                # Choice stays the runner's (item 6 A). Asking is a read: it injects nothing, and taking it for a
                # press would leave the step's own wait timing out on a screen nothing moved. An adapter that drives
                # the goal itself is no goal of the runner's: it runs the step below, like every other action.
                if route == "read":
                    adapter.inject("nav.goal", action.get("args", {}))
                missed = "pending" if goal is None else goal.reach(adapter, s, emit, vision)
                if missed:
                    emit("gui_verdict", s["id"], verdict="pending" if goal is None else f"failed ({missed})")
                    results.append({"id": s["id"], "verdict": "pending" if goal is None else f"failed ({missed})", "capture": None, "held": False})
                    continue
            elif action and action["name"] == "nav.sweep":
                missed = "pending" if goal is None else goal.sweep(adapter, s, emit, vision)
                verdict = "pending" if goal is None else f"failed ({missed})" if missed else "passed"
                emit("gui_verdict", s["id"], verdict=verdict)
                results.append({"id": s["id"], "verdict": verdict, "capture": None, "held": False})
                continue
            elif action:
                adapter.inject(action["name"], action.get("args", {}))
            cond = s["wait"].get("check")
            capture = None
            timeout = s["wait"].get("timeout_ticks") or s["wait"]["timeout_frames"]
            if wait_for(adapter, cond, timeout, root) != "met":
                verdict = f"failed (timeout waiting for {cond})"
            else:
                capture = adapter.capture()
                emit("gui_capture", s["id"], held=bool(s.get("sensitive")), **capture)
                if "check" not in s:
                    verdict, seen = vision.review(s, capture) if vision else ("passed", None)
                    if seen:
                        emit("gui_vision", s["id"], **seen)
                else:
                    verdict = decide(adapter, s["check"], root)
            emit("gui_verdict", s["id"], verdict=verdict)
            results.append({"id": s["id"], "verdict": verdict, "capture": capture, "held": bool(s.get("sensitive")),
                            **({"vision": seen} if seen else {})})
            seen = None
    return results
