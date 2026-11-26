#!/usr/bin/env python3
"""The project operator session's `PreToolUse` hook as a command (#2950).

The CLI runs a hook as a command that reads the event JSON on stdin and prints its answer. This shim reads the event,
loads the policy file the supervisor wrote at the session start, and runs the gate of `squad.operatorgate`. It answers
every tool call: an allowed call carries the exact input the hook judged, and a denied call carries its reason and
appends one audit line to the file the policy names.

usage: operator_gate.py <policy json file>

The shim fails closed: any error exits 2, which the CLI reads as a block, so a broken policy, a broken event or a bug
never lets a call through.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from squad import operatorgate  # noqa: E402

BLOCK = 2   # the CLI's exit code for a blocking hook error


def main(argv=None, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not argv:
            raise ValueError("no policy file")
        pol = operatorgate.policy(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
        event = json.load(stdin)
        outcome = operatorgate.decide(pol, event)
        if not outcome.allow:
            operatorgate.audit(pol.audit, event, outcome.reason)
        stdout.write(json.dumps(operatorgate.hook_output(outcome)))
        return 0
    except Exception as e:  # noqa: BLE001 - every failure must block the call
        stderr.write(f"operator-gate: blocked: {e}\n")
        return BLOCK


if __name__ == "__main__":
    sys.exit(main())
