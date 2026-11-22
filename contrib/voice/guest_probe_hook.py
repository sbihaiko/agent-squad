#!/usr/bin/env python3
"""The command hook that runs a guest session's own `PreToolUse` hook inside the real CLI (#2716).

The probe cannot hand an in-process callback to a `claude -p` call, so this shim reads the CLI's hook input on stdin,
runs the real hook of the role (`squad.slackpoller.pre_read_tool_use` for the poller, `squad.slackwriter.
pre_write_tool_use` for the writer), and prints its answer. The shim fails closed: any error exits 2, which the CLI reads
as a block, so a broken shim never lets a call through.

usage: guest_probe_hook.py poller
       guest_probe_hook.py writer '<draft json: tool, channel, thread_ts, text, name>'
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from squad import slackpoller, slackwriter  # noqa: E402

BLOCK = 2   # the CLI's exit code for a blocking hook error


def hook_for(argv):
    """The real hook of the role the arguments name."""
    role = argv[0] if argv else ""
    if role == "poller":
        return slackpoller.pre_read_tool_use()
    if role == "writer" and len(argv) == 2:
        return slackwriter.pre_write_tool_use(slackwriter.Draft(**json.loads(argv[1])))
    raise ValueError(f"no guest hook for the arguments {argv!r}")


def main(argv=None, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    argv = sys.argv[1:] if argv is None else argv
    try:
        data = json.load(stdin)
        reply = asyncio.run(hook_for(argv)(data, data.get("tool_use_id"), None))
    except Exception as e:  # noqa: BLE001 - every failure must block the call
        stderr.write(f"guest-probe-hook: blocked: {e}\n")
        return BLOCK
    stdout.write(json.dumps(reply or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
