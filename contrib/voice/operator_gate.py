#!/usr/bin/env python3
"""The project operator session's `PreToolUse` hook as a command (#2950, #2972).

The CLI runs a hook as a command that reads the event JSON on stdin and prints its answer. This shim reads the event,
loads the policy file the supervisor wrote at the session start, and runs the gate of `squad.operatorgate`. It answers
every tool call: an allowed call carries the exact input the hook judged, and a denied call carries its reason and
appends one audit line to the file the policy names.

A policy may name a `relay` block (#2972). A call the gate denies then goes to the permission relay of the owner's DM,
and the hook waits for the answer. An approval lifts the hook's denial alone: the kernel profile still decides what the
call may read, write and reach, so an approved call that the profile refuses still fails. A denial, a timeout or a
sender with three open requests answers one short reason, which the session gives the member in the thread.

usage: operator_gate.py <policy json file>

The shim fails closed: any error exits 2, which the CLI reads as a block, so a broken policy, a broken event or a bug
never lets a call through.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import permrelay  # noqa: E402  the one approval relay, shared with a full turn (SPEC 2964)

from squad import operatorgate  # noqa: E402

BLOCK = 2   # the CLI's exit code for a blocking hook error


def relayed(block, event, outcome):
    """The denied call after the approval relay answers it: allowed with the judged input, or denied with one reason.

    A request with no relay block keeps the gate's own denial. The relay is the same one a full turn uses, so the
    owner's `approve <hash>` line in the DM answers both."""
    if not block:
        return outcome
    relay = permrelay.Relay(block.get("state") or "", owner=block.get("owner") or "",
                            dm_channel=block.get("dm") or "", workspace=block.get("workspace") or "",
                            timeout=permrelay.TIMEOUT_SECONDS if block.get("timeout") is None
                            else float(block["timeout"]))
    given = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    answer = relay.call(block.get("sender") or "", event.get("tool_name") or "", given,
                        block.get("folder") or "")
    if answer["verdict"] == "allow":
        return operatorgate.Outcome(True, "", given)
    return operatorgate.Outcome(False, answer["reason"], {})


def main(argv=None, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not argv:
            raise ValueError("no policy file")
        doc = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        pol = operatorgate.policy(doc)
        event = json.load(stdin)
        outcome = operatorgate.decide(pol, event)
        if not outcome.allow:
            outcome = relayed(doc.get("relay") if isinstance(doc, dict) else None, event, outcome)
        if not outcome.allow:
            operatorgate.audit(pol.audit, event, outcome.reason)
        stdout.write(json.dumps(operatorgate.hook_output(outcome)))
        return 0
    except Exception as e:  # noqa: BLE001 - every failure must block the call
        stderr.write(f"operator-gate: blocked: {e}\n")
        return BLOCK


if __name__ == "__main__":
    sys.exit(main())
