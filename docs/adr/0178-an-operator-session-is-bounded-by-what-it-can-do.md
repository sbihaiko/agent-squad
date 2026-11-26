# ADR-0178: An operator session is bounded by the calls it can make, never by a filter of message content

## Status

Accepted. This ADR extends [ADR-0154](0154-the-owner-talks-to-the-operator-through-slack.md) and
[ADR-0177](0177-slack-rules-in-the-project.md). It adds the guard block of the committed Slack file, the audit line of
the `voiced` daemon and the tool gate of a project operator session (decision 7). The request line that the gate lets
the answer helper write follows [ADR-0179](0179-a-project-declares-the-command-of-each-action.md).

## Context

The `voiced` daemon turns a Slack message into one inbox line, and the operator session reads the inbox. A sender with a
role in a channel reaches that session. A stranger stays data. The role bound answers who may act. It answers nothing
about how much one sender may send, and nothing about how long one message may be.

A flood of messages becomes a flood of inbox lines and a flood of session wakes. A very long message becomes a very long
context. Both reach the same session that decides what to do, so both widen what the session must read rather than what it
may do.

A filter of the content is the wrong answer. A filter that reads the message to judge it runs inside the session that the
message threatens, and a clever message passes a content filter anyway. The bound belongs at the gate that admits the
message, and it counts and measures, never interprets.

The session's tools are a second surface. A session that may run any command or read any file is bounded by its
prompt alone, and a prompt is data the message can reach. The gate that decides each tool call stands outside the
session, so the session's reach is set by the gate and not by the message.

## Decision

1. **The bound is on admission and on calls, never on message content.** The session is bounded by the calls its tool gate
   allows and by the messages the gate admits. No rule reads a message to decide whether the session may see it. The
   guard counts messages and measures their length; it reads no word of the body. A path rule on a `Read` call bounds
   what the session may do, and it reads no message.
2. **The committed Slack file holds an optional guard block.** The block names `rate`, `max_chars`, `answer_max_chars` and
   `read_wrappers`. A file that names none, and a block that omits a field, take the plugin defaults that
   `squad/slackpolicy.py` holds. `squad slack check` names one defect for each malformed field, so a person repairs the
   key the message names.
3. **The daemon refuses before the inbox write.** `voiced` refuses a message when one sender passes `rate` messages in
   one channel inside the rate window, and when a message body is longer than `max_chars`. The size rule removes each
   `read_wrappers` substring first, so a transport wrapper never counts against the sender. A refused message writes no
   inbox line and wakes no session. A refused clip transcript takes the same size rule.
4. **A refusal reply names its reason class.** A refused sender receives at most one refusal reply per channel inside the
   reply window. The reply names the class, `rate` or `size`, in one sentence. A `listen` channel receives no post.
5. **Every message writes one audit line.** The daemon appends one line per message to `audit.jsonl` in the profile state
   folder: the time, the sender id, the role, the channel, the decision and the reason class. The line holds at most
   eighty characters of the message text, and it removes every URL, key-like value and token before it writes, so no
   secret enters the audit. The audit line is not the seen ledger.
6. **The guard never stops the daemon.** A refused message is one audit line and at most one reply. A refusal of the
   reply itself is a log line. The daemon keeps answering the next message.
7. **A `PreToolUse` hook decides every tool call of a project operator session.** The hook names four tools and denies
   every other tool name with a reason.
   - `Read` passes only for a path inside the project checkout. It denies the secret list: a path with a `.env` or
     `.env.*` component, a path with a `slack.local.yaml` component or one that ends in `.slack.local.yaml`, the owner's
     `~/.ssh`, `~/.config/gh` and `~/.aws`, and every other project checkout.
   - `Monitor` passes only as the exact command `tail -n 0 -F <inbox>`, where the inbox is one of the session's own.
   - `TaskStop` passes.
   - `Bash` passes only for the answer helper, in one of its three forms (an answer, a proposal, or an action request),
     and for each command line the project declares under `guard.read_wrappers`. It denies a command with a shell
     control operator, a substitution or a redirection.
   An allowed call answers with the input the hook judged, so the executed command equals the judged command. Each
   denial writes one audit line with its reason, and a failed write blocks the call. An error in the hook blocks the
   call, so the hook fails closed.
   The gate reads its policy from one file the supervisor writes at the session start, never from a value the model
   can reach.

## Guards

- `tests/test_slack_policy.py` proves the guard block, its defaults and one defect per malformed field.
- `tests/test_voiced_guard.py` proves the rate refusal, the size refusal, the absent inbox line, the one reply per
  channel, the reason class, the audit fields and the redaction.
- `tests/test_operator_gate.py` proves decision 7 case by case, one test per acceptance criterion.
- `tests/test_answer_helper.py` proves the helper writes the request line that the gate allows.
- `contrib/voice/operator_probe.py` proves, with the real CLI, that the executed command equals the judged command.
