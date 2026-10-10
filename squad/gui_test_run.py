"""Minimal gui-test/1 runner: drives one adapter through automated steps; manual steps go to a person."""
import hashlib
from pathlib import Path


def failed(results):
    """True when any step failed; a `pending` step (nobody ran it) is not a failure."""
    return any(r["verdict"].startswith("failed") for r in results)


def _screenshot(path):
    data = Path(path).read_bytes()
    return {"path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def run(script, adapter, record=None, prompt=None, vision=None):
    """Run every step in order; return [{id, verdict, capture, held}]. Never raises on a wait timeout.

    A `manual` step is put to `prompt(step)`, which returns {"verdict": "passed"|"failed", "screenshot": path}; with no
    prompt, or when it returns None, the step ends `pending`. With no adapter (unavailable) every step is a manual step,
    so the whole script runs as a checklist with the same record.

    With a `vision` (squad.gui_test_vision.Vision), an automated step with no `check` takes the model's verdict on its
    capture: `compatible` passes, anything else is `needs review`; a step with a `check` follows the check and is never
    sent. Once the vision spend reaches its cap the run stops: every later step ends `skipped (spend cap ...)`.

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
            emit("gui_step", s["id"])
            if "action" in s:
                adapter.inject(s["action"]["name"], s["action"].get("args", {}))
            cond = s["wait"].get("check")
            capture = None
            if adapter.wait(cond, s["wait"].get("timeout_ticks") or s["wait"]["timeout_frames"]) != "met":
                verdict = f"failed (timeout waiting for {cond})"
            else:
                capture = adapter.capture()
                emit("gui_capture", s["id"], held=bool(s.get("sensitive")), **capture)
                if "check" not in s:
                    verdict, seen = vision.review(s, capture) if vision else ("passed", None)
                    if seen:
                        emit("gui_vision", s["id"], **seen)
                else:
                    got = adapter.check(s["check"]["name"], s["check"].get("args", {}))
                    verdict = "failed (unknown id)" if got == "unknown id" else "passed" if got else "failed"
            emit("gui_verdict", s["id"], verdict=verdict)
            results.append({"id": s["id"], "verdict": verdict, "capture": capture, "held": bool(s.get("sensitive")),
                            **({"vision": seen} if seen else {})})
            seen = None
    return results
