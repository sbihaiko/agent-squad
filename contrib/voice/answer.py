#!/usr/bin/env python3
"""The one answer path of an operator session (#2949, SPEC 2946).

A project's operator session answers each Slack message through this helper and through nothing else. The helper writes
one outbox line for a pending inbox line of the session's own project, and it refuses any other channel or thread. The
supervisor writes the binding at the session start and names it in `VOICE_ANSWER_BINDING`, so the helper answers only
the channels of that one project, whatever arguments the session passes.

The rule that binds an answer to an inbox line is the Slack policy module's (`slackpolicy.pending_line`), never a second
copy: an answer threads under the question whose `ts` equals its thread, and a channel whose inbox holds no line
answers nothing. The inbox line itself must name a channel of the session's project: a project line names its own
channel, and a DM line names the binding's one DM channel, since the daemon posts to the channel the outbox line
carries. This helper is the only gate on that path; `voiced` posts the line as it reads it.

The helper writes no other file and makes no Slack call. It runs under the session's kernel profile, so the kernel
refuses every write beyond the session's own outboxes whatever this helper does.

usage: answer.py --scope <channel id|dm> [--thread <the inbox line's ts>] --text "<the answer>"
                 [--speech "<a 10 to 30 second summary>"] [--persona <id>]
The DM may omit `--thread`, and then answers the newest pending line of its inbox.
Exit 0: one outbox line was written. Exit 2: the call is refused, and nothing was written. Exit 1: the write failed.

The binding (`VOICE_ANSWER_BINDING`) is a JSON file the supervisor writes: `key`, `state`, `channels`, and `dm` for the
DM session.
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
    """The JSON object one file holds, or `default` when the file is absent. A file that exists but does not parse is a
    read failure that names its path, never a missing file."""
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
    except FileNotFoundError:
        return default
    except OSError as e:
        raise Refused(f"cannot read {path}: {e.strerror or e}") from e
    except ValueError:
        raise Refused(f"{path} is not valid JSON") from None
    return got if isinstance(got, dict) else default


def read_lines(path):
    """The JSON lines of one queue file. The last line may be a half write a crash left behind, so it is skipped; a
    corrupt line before it is a broken queue that names its path."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            raw_lines = f.read().splitlines()
    except FileNotFoundError:
        return out
    except OSError as e:
        raise Refused(f"cannot read the inbox {path}: {e.strerror or e}") from e
    last = len(raw_lines) - 1
    for number, raw in enumerate(raw_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            if number == last:
                continue
            raise Refused(f"the inbox {path} holds a corrupt line at {number + 1}") from None
        if isinstance(record, dict):
            out.append(record)
    return out


def read_binding(env=None):
    """The session's own project binding: its state folder, its channels, and the DM channel for the DM session. A
    session with no binding answers nothing, so a bare shell call never posts."""
    path = (env if env is not None else os.environ).get(BINDING_ENV) or ""
    if not path:
        raise Refused(f"no session binding: {BINDING_ENV} is unset, so this helper runs outside an operator session")
    binding = read_json(path, {})
    state = str(binding.get("state") or "")
    channels = [str(c) for c in binding.get("channels") or () if c]
    if not state or not channels:
        raise Refused(f"malformed session binding {path}: it names no state folder or no channel")
    return {"key": str(binding.get("key") or ""), "state": state, "channels": channels,
            "dm": str(binding.get("dm") or "")}


def pending_line(inbox, thread):
    """The pending inbox line one answer answers: the policy module's own rule, with no copy here."""
    return slackpolicy.pending_line(inbox, thread)


def answer(binding, scope, thread, text, speech=None, persona=None):
    """Write one outbox line for a pending inbox line of `scope`, a channel of the session's project.

    A scope outside the project is refused, and so is a thread the channel's inbox never held: an answer may not land in
    another channel or under a stale thread. The inbox line itself must name a channel of the session's project: a
    project line names its own channel, and a DM line names the one DM channel of the binding, since the daemon posts to
    the channel the outbox line carries. The outbox is resolved under the session's state folder, so a tampered binding
    never writes outside it."""
    scope = str(scope or "")
    if scope not in binding["channels"]:
        raise Refused(f"channel {scope!r} is outside this session's project")
    state_dir = os.path.realpath(binding["state"])
    outbox = os.path.realpath(operators.outbox_path(binding["state"], scope))
    if os.path.commonpath([state_dir, outbox]) != state_dir:
        raise Refused(f"the outbox {outbox} is outside the session's state folder {state_dir}")
    inbox = read_lines(operators.inbox_path(binding["state"], scope))
    if thread is None and scope == DM:
        thread = str(inbox[-1].get("ts")) if inbox else None
    question = pending_line(inbox, thread)
    if question is None:
        raise Refused(f"no pending inbox line in {scope} answers thread {thread!r}")
    line_channel = str(question.get("channel") or scope)
    if scope == DM:
        own = str(binding.get("dm") or "") or str((inbox[0] if inbox else {}).get("channel") or "")
        if not own or line_channel != own:
            raise Refused(f"the DM line names channel {line_channel!r}, not this session's own DM channel {own!r}")
    elif line_channel != scope:
        raise Refused(f"the line in {scope} names channel {line_channel!r}, outside this session's project")
    record = {"thread_ts": str(question.get("ts")), "text": str(text)}
    if speech:
        record["speech"] = str(speech)
    if persona:
        record["persona"] = str(persona)
    if scope == DM:
        record["channel"] = line_channel
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
