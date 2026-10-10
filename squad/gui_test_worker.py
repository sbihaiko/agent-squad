"""Child process of the gui-test supervisor: hosts one adapter and answers JSON-line calls on a pipe."""
import importlib
import json
import os
import sys


def main(spec, kwargs):
    proto = os.fdopen(os.dup(1), "w")
    sys.stdout = sys.stderr  # adapter prints must not corrupt the protocol
    module, _, cls = spec.partition(":")
    adapter = getattr(importlib.import_module(module), cls)(**kwargs)
    for line in sys.stdin:
        msg = json.loads(line)
        try:
            out = {"ok": getattr(adapter, msg["call"])(*msg.get("args", []))}
            if msg["call"] == "launch":
                out["ok"] = None  # the session is the adapter itself
        except Exception as e:  # an adapter error is reported, a crash is not
            out = {"error": f"{type(e).__name__}: {e}"}
        proto.write(json.dumps(out, default=str) + "\n")
        proto.flush()


if __name__ == "__main__":
    main(sys.argv[1], json.loads(sys.argv[2]))
