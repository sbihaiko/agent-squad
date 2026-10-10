"""Child process of the gui-test supervisor: hosts one adapter and answers JSON-line calls on a pipe.

`launch` returns *the session* (ADR-0272 item 6), and the five session operations are answered by that object, not by
the adapter: the reference fake returns itself, while the emulator adapter returns a session that owns a different
process. So the worker keeps whatever `launch` handed back and routes `inject`, `check`, `wait`, `capture` and
`teardown` there — the runner's calls do not care which of the two objects answers, which is the point of the seam.
"""
import importlib
import inspect
import json
import os
import sys
from pathlib import Path

SESSION_CALLS = ("inject", "check", "wait", "capture", "teardown")


def _capture(target, out_dir, n):
    """Ask the session for a screenshot, naming the file when it wants one (the emulator's session writes a PNG at a
    path the caller gives; the reference fake invents its own)."""
    if len(inspect.signature(target.capture).parameters) >= 1:
        return target.capture(Path(out_dir) / f"capture-{n}.png")
    return target.capture()


def main(spec, kwargs):
    proto = os.fdopen(os.dup(1), "w")
    sys.stdout = sys.stderr  # adapter prints must not corrupt the protocol
    module, _, cls = spec.partition(":")
    root = os.environ.get("MESENAI_ROOT")
    if root:  # the emulator adapter lives in MesenAI (ADR-0272 item 6, P1-A): scripts/gui_test/<module>.py
        sys.path.insert(0, os.path.join(root, "scripts", "gui_test"))
    adapter = getattr(importlib.import_module(module), cls)(**kwargs)
    out_dir, session, shots = kwargs.get("out_dir", "."), adapter, 0
    for line in sys.stdin:
        msg = json.loads(line)
        call = msg["call"]
        try:
            target = session if call in SESSION_CALLS else adapter
            if call == "capture":
                shots += 1
                out = {"ok": _capture(target, out_dir, shots)}
            else:
                out = {"ok": getattr(target, call)(*msg.get("args", []))}
            if call == "launch":
                session = out["ok"] if out["ok"] is not None else adapter
                out["ok"] = None  # the runner reads the session through later calls, not from this answer
        except Exception as e:  # an adapter error is reported, a crash is not
            out = {"error": f"{type(e).__name__}: {e}"}
        proto.write(json.dumps(out, default=str) + "\n")
        proto.flush()


if __name__ == "__main__":
    main(sys.argv[1], json.loads(sys.argv[2]))
