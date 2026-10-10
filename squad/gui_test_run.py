"""Minimal gui-test/1 runner: drives one adapter through automated steps; manual steps go to a person."""
import hashlib
from pathlib import Path


def failed(results):
    """True when any step failed; a `pending` step (nobody ran it) is not a failure."""
    return any(r["verdict"].startswith("failed") for r in results)


def _screenshot(path):
    data = Path(path).read_bytes()
    return {"path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def run(script, adapter, record=None, prompt=None):
    """Run every step in order; return [{id, verdict, capture}]. Never raises on a wait timeout.

    A `manual` step is put to `prompt(step)`, which returns {"verdict": "passed"|"failed", "screenshot": path}; with no
    prompt, or when it returns None, the step ends `pending`. With no adapter (unavailable) every step is a manual step,
    so the whole script runs as a checklist with the same record.

    `record` takes one replay event per call: gui_step, then gui_capture (only once the wait is met), then gui_verdict.
    """
    results, pos = [], 0

    def emit(event, node, **more):
        nonlocal pos
        if record:
            record({"event": event, "node": node, "pos": pos, **more})
        pos += 1

    for batch in script["batches"]:
        for s in batch["setup"] + batch["steps"] + batch["teardown"]:
            if s["mode"] != "automated" or adapter is None:
                answer = prompt(s) if prompt else None
                emit("gui_step", s["id"], manual=True)
                capture = None
                if answer:
                    capture = _screenshot(answer["screenshot"])
                    emit("gui_capture", s["id"], **capture)
                verdict = answer["verdict"] if answer else "pending"
                emit("gui_verdict", s["id"], verdict=verdict)
                results.append({"id": s["id"], "verdict": verdict, "capture": capture})
                continue
            emit("gui_step", s["id"])
            adapter.inject(s["action"]["name"], s["action"].get("args", {}))
            cond = s["wait"].get("check")
            capture = None
            if adapter.wait(cond, s["wait"].get("timeout_ticks") or s["wait"]["timeout_frames"]) != "met":
                verdict = f"failed (timeout waiting for {cond})"
            else:
                capture = adapter.capture()
                emit("gui_capture", s["id"], **capture)
                if "check" not in s:
                    verdict = "passed"
                else:
                    got = adapter.check(s["check"]["name"], s["check"].get("args", {}))
                    verdict = "failed (unknown id)" if got == "unknown id" else "passed" if got else "failed"
            emit("gui_verdict", s["id"], verdict=verdict)
            results.append({"id": s["id"], "verdict": verdict, "capture": capture})
    return results
