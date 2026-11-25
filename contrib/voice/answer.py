#!/usr/bin/env python3
"""The one answer path of an operator session (#2949, SPEC 2946).

A project's operator session answers each Slack message through this helper and through nothing else. The helper writes
one outbox line for a pending inbox line of the session's own project, and it refuses any other channel or thread. The
supervisor writes the binding at the session start and names it in `VOICE_ANSWER_BINDING`, so the helper answers only
the channels of that one project, whatever arguments the session passes.

The rule that binds an answer to an inbox line is the Slack policy module's (`slackpolicy.pending_line`), never a second
copy: an answer threads under the question whose `ts` equals its thread, and a channel whose inbox holds no line
answers nothing. The inbox line itself must name a channel of the session's project: a project line names its own
channel, and a DM line names the DM channel the supervisor pinned in the session's record, since the daemon posts to
the channel the outbox line carries. A DM session with no pinned channel answers nothing, so an inbox line anything
wrote never picks the outbox channel. This helper is the only gate on that path; `voiced` posts the line as it reads it.

A message that asks for an action gets two calls under its one line: the read-only answer, then a second call for the
same thread whose text starts with `Proposal:`. The inbox keeps the question, so the second call answers the same
pending line and its proposal line lands beside the answer in the same outbox. No structured field marks a proposal:
the executor that runs an action selects it by the `Proposal:` text prefix.

A retried call for the same thread and the same text writes no second line: a lost Bash tool_result makes the session
call the helper again, and the helper skips the append when its outbox tail already holds that line.

The helper writes no other file and makes no Slack call. It runs under the session's kernel profile, so the kernel
refuses every write beyond the session's own outboxes whatever this helper does. One session per state folder answers
a channel, and every append to an outbox takes an exclusive flock while every inbox read takes a shared one, so two
writers that take the lock never interleave a line. An inbox read takes only the file's bounded tail, so the helper
never loads a whole queue.

usage: answer.py --scope <channel id|dm> [--thread <the inbox line's ts>] --text "<the answer>"
                 [--speech "<a 10 to 30 second summary>"] [--persona <id>]
The DM may omit `--thread`, and then answers the newest pending line of its inbox.
Exit 0: the answer's one outbox line is present, and a retry writes no second one. Exit 2: the call is refused, and
nothing was written. Exit 1: the write failed.

The binding (`VOICE_ANSWER_BINDING`) is a JSON file the supervisor writes: `key`, `state`, `channels`, and `dm` for the
DM session.
"""
import argparse
import fcntl
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
from operators import dm_channel  # noqa: E402  the supervisor's pinned DM record, never an inbox line's own channel

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
    """The JSON lines of one queue file, read from a bounded tail the way `operators.tail_lines` reads one: the helper
    loads a constant amount of memory whatever the inbox's age, and the partial first line of that bounded read is
    dropped.

    The last line may be a half write the daemon is making right now, so it is skipped; a corrupt line before it is a
    broken queue that names its path. An inbox whose pending line is older than the read bound answers nothing, because
    the helper never loads the whole queue.

    The read holds a shared flock on the file, so a writer that takes the matching exclusive lock never meets a line
    half written. The daemon's own inbox append takes no lock, so a torn trailing line is still possible: the helper
    skips that one line and nothing else."""
    out = []
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return out
    except OSError as e:
        raise Refused(f"cannot read the inbox {path}: {e.strerror or e}") from e
    with f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        raw_lines = operators.tail_lines(path)
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

    A second call for an already answered thread is legal: the inbox keeps the question, so the same pending line answers
    again, and the DM's proposal rides beside its own answer. A scope outside the project is refused, and so is a
    thread the channel's inbox never held: an answer may not land in
    another channel or under a stale thread. The inbox line itself must name a channel of the session's project: a
    project line names its own channel, and a DM line names the one DM channel of the binding, since the daemon posts to
    the channel the outbox line carries. The outbox is resolved under the session's state folder, so a tampered binding
    never writes outside it."""
    scope = str(scope or "")
    if scope not in binding["channels"]:
        raise Refused(f"channel {scope!r} is outside this session's project")
    state_dir = os.path.realpath(binding["state"])
    if not os.path.isdir(state_dir):
        raise Refused(f"the session's state folder {state_dir} does not exist")
    outbox = os.path.realpath(operators.outbox_path(binding["state"], scope))
    try:
        inside = os.path.commonpath([state_dir, outbox]) == state_dir
    except ValueError:                       # two paths on different mounts share no common prefix
        inside = False
    if not inside:
        raise Refused(f"the outbox {outbox} is outside the session's state folder {state_dir}")
    inbox = read_lines(operators.inbox_path(binding["state"], scope))
    if thread is None and scope == DM:
        thread = str(inbox[-1].get("ts")) if inbox else None
    question = pending_line(inbox, thread)
    if question is None:
        raise Refused(f"no pending inbox line in {scope} answers thread {thread!r}")
    line_channel = str(question.get("channel") or scope)
    if scope == DM:
        # The DM channel is the one the supervisor pinned at the session start. A binding that names none resolves the
        # pinned record alone, never an inbox line's own channel: a line anything wrote could route the answer to a
        # channel this session's project does not own. With no pinned channel, nothing is verified, so nothing answers.
        own = str(binding.get('dm') or dm_channel(binding['state'], pinned=True))
        if not own:
            raise Refused("the DM session has no pinned DM channel, so it answers nothing")
        if line_channel != own:
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
    with open(outbox, "a+b") as f:
        # One exclusive flock bounds every append to this outbox, so two sessions of one state folder never interleave
        # a line: a writer that a crash left mid-line holds no lock, and the next append starts on a fresh line.
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        for raw in operators.tail_lines(outbox):     # a bounded tail, never the whole outbox
            try:
                held = json.loads(raw)
            except ValueError:
                continue
            if (isinstance(held, dict) and str(held.get("thread_ts")) == record["thread_ts"]
                    and str(held.get("text")) == record["text"]):
                return held                          # a retry of this answer: the line is already there
        f.seek(0, os.SEEK_END)
        lead = ""
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                lead = "\n"                 # a crash's half line stays its own skipped line, never glued to this one
        f.write((lead + json.dumps(record) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
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
