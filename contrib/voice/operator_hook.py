#!/usr/bin/env python3
"""The operator session's `PreToolUse` hook as a command (#2691).

The CLI runs a hook as a command that reads the event JSON on stdin and prints its answer. This shim reads the event,
loads the decision file the session was started with, and runs the real hook of `squad.slackoperator`
(`pre_tool_use`). The session's controller starts one shim per line, with that line's decision file.

usage: operator_hook.py <decision json file>

The shim fails closed: any error exits 2, which the CLI reads as a block, so a broken shim never lets a call through.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from squad import slackoperator  # noqa: E402

BLOCK = 2   # the CLI's exit code for a blocking hook error


def decision_from(doc):
    """The decision a file names, as `slackoperator.Decision`. A file with no `reply` names a `bot` reply, the path the
    daemon posts from the outbox."""
    doc = doc if isinstance(doc, dict) else {}
    return slackoperator.Decision(
        str(doc.get("reply") or slackoperator.REPLY_BOT),
        str(doc.get("channel") or ""),
        str(doc.get("thread_ts") or ""),
        str(doc.get("text") or ""),
    )


def main(argv=None, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not argv:
            raise ValueError("no decision file")
        decision = decision_from(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
        data = json.load(stdin)
        reply = asyncio.run(slackoperator.pre_tool_use(decision)(data, data.get("tool_use_id"), None))
    except Exception as e:  # noqa: BLE001 - every failure must block the call
        stderr.write(f"operator-hook: blocked: {e}\n")
        return BLOCK
    stdout.write(json.dumps(reply or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
