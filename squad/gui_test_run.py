"""Minimal gui-test/1 runner: enough to drive one adapter through automated steps."""


def run(script, adapter):
    """Run every automated step in order; return [{id, verdict}]. Never raises on a wait timeout."""
    results = []
    for batch in script["batches"]:
        for s in batch["setup"] + batch["steps"] + batch["teardown"]:
            if s["mode"] != "automated":
                continue
            adapter.inject(s["action"]["name"], s["action"].get("args", {}))
            cond = s["wait"].get("check")
            if adapter.wait(cond, s["wait"].get("timeout_ticks") or s["wait"]["timeout_frames"]) != "met":
                verdict = f"failed (timeout waiting for {cond})"
            elif "check" not in s:
                verdict = "passed"
            else:
                got = adapter.check(s["check"]["name"], s["check"].get("args", {}))
                verdict = "failed (unknown id)" if got == "unknown id" else "passed" if got else "failed"
            results.append({"id": s["id"], "verdict": verdict})
    return results
