"""Minimal gui-test/1 runner: enough to drive one adapter through automated steps."""


def run(script, adapter, record=None):
    """Run every automated step in order; return [{id, verdict, capture, held}]. Never raises on a wait timeout.

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
            if s["mode"] != "automated":
                continue
            emit("gui_step", s["id"])
            adapter.inject(s["action"]["name"], s["action"].get("args", {}))
            cond = s["wait"].get("check")
            capture = None
            if adapter.wait(cond, s["wait"].get("timeout_ticks") or s["wait"]["timeout_frames"]) != "met":
                verdict = f"failed (timeout waiting for {cond})"
            else:
                capture = adapter.capture()
                emit("gui_capture", s["id"], held=bool(s.get("sensitive")), **capture)
                if "check" not in s:
                    verdict = "passed"
                else:
                    got = adapter.check(s["check"]["name"], s["check"].get("args", {}))
                    verdict = "failed (unknown id)" if got == "unknown id" else "passed" if got else "failed"
            emit("gui_verdict", s["id"], verdict=verdict)
            results.append({"id": s["id"], "verdict": verdict, "capture": capture, "held": bool(s.get("sensitive"))})
    return results
