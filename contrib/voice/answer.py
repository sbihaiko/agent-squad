#!/usr/bin/env python3
"""The one answer path of an operator session (#2949, SPEC 2946).

A project's operator session answers each Slack message through this helper and through nothing else. The helper writes
one outbox line for a pending inbox line of the session's own project, and it refuses any other channel or thread. The
supervisor writes the binding at the session start and names it in `VOICE_ANSWER_BINDING`, so the helper answers only
the channels of that one project, whatever arguments the session passes.

The rule that binds an answer to an inbox line is the Slack policy module's (`slackpolicy.pending_line`), never a second
copy: an answer threads under the question whose `ts` equals its thread, and a channel whose inbox holds no line
answers nothing. `voiced` checks the same rule again before it posts, so the daemon is the backstop and this helper is
the first wall.

The helper writes no other file and makes no Slack call. It runs under the session's kernel profile, so the kernel
refuses every write beyond the session's own outboxes whatever this helper does.

usage: answer.py --scope <channel id|dm> --thread <the inbox line's ts> --text "<the answer>"
                 [--speech "<a 10 to 30 second summary>"] [--persona <id>]
Exit 0: one outbox line was written. Exit 2: the call is refused, and nothing was written. Exit 1: the write failed.

The binding (`VOICE_ANSWER_BINDING`) is a JSON file the supervisor writes: `key`, `state` and `channels`.
"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True     # the kernel refuses a write outside the outboxes, so no cache file may be tried
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (HERE, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)
import operators  # noqa: E402  the one naming rule of an inbox and an outbox
from squad import slackpolicy  # noqa: E402  the one rule that binds an answer to an inbox line (ADR-0177)

BINDING_ENV = "VOICE_ANSWER_BINDING"
DM = operators.DM


class Refused(Exception):
    """The call answers no pending line of the session's project, so it writes nothing."""


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return default
    return got if isinstance(got, dict) else default


def read_lines(path):
    """The JSON lines of one queue file, skipping a half line a crash left behind."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        pass
    return out


def read_binding(env=None):
    """The session's own project binding: its state folder and its channels. A session with no binding answers
    nothing, so a bare shell call never posts."""
    path = (env if env is not None else os.environ).get(BINDING_ENV) or ""
    if not path:
        raise Refused(f"no session binding: {BINDING_ENV} is unset, so this helper runs outside an operator session")
    binding = read_json(path, {})
    state = str(binding.get("state") or "")
    channels = [str(c) for c in binding.get("channels") or () if c]
    if not state or not channels:
        raise Refused(f"malformed session binding {path}: it names no state folder or no channel")
    return {"key": str(binding.get("key") or ""), "state": state, "channels": channels}


def pending_line(inbox, thread):
    """The pending inbox line one answer answers: the policy module's own rule, with no copy here."""
    return slackpolicy.pending_line(inbox, thread)


def answer(binding, scope, thread, text, speech=None, persona=None):
    """Write one outbox line for a pending inbox line of `scope`, a channel of the session's project.

    A scope outside the project is refused, and so is a thread the channel's inbox never held: an answer may not land in
    another channel or under a stale thread. The DM outbox line names its channel, because the daemon reads the channel
    from the line alone."""
    scope = str(scope or "")
    if scope not in binding["channels"]:
        raise Refused(f"channel {scope!r} is outside this session's project")
    question = pending_line(read_lines(operators.inbox_path(binding["state"], scope)), thread)
    if question is None:
        raise Refused(f"no pending inbox line in {scope} answers thread {thread!r}")
    record = {"thread_ts": str(question.get("ts")), "text": str(text)}
    if speech:
        record["speech"] = str(speech)
    if persona:
        record["persona"] = str(persona)
    if scope == DM:
        record["channel"] = str(question.get("channel") or scope)
    outbox = operators.outbox_path(binding["state"], scope)
    os.makedirs(os.path.dirname(outbox), exist_ok=True)
    with open(outbox, "a", encoding="utf-8") as f:      # one short append: one line lands whole or not at all
        f.write(json.dumps(record) + "\n")
        f.flush()
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description="Answer one pending inbox line of this session's project.")
    ap.add_argument("--scope", required=True, help="the inbox line's channel id, or dm")
    ap.add_argument("--thread", default=None, help="the inbox line's ts; the DM may omit it")
    ap.add_argument("--text", required=True, help="the answer Slack shows")
    ap.add_argument("--speech", default=None, help="a 10 to 30 second spoken summary")
    ap.add_argument("--persona", default=None, help="the channel persona id the answer posts under")
    args = ap.parse_args(argv)
    if not args.text.strip():
        print("answer: refused: the answer text is empty", file=sys.stderr)
        return 2
    try:
        record = answer(read_binding(), args.scope, args.thread, args.text, args.speech, args.persona)
    except Refused as e:
        print(f"answer: refused: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"answer: the write failed: {e.strerror or e}", file=sys.stderr)
        return 1
    print(f"answer: wrote one line to the {args.scope} outbox under thread {record['thread_ts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
