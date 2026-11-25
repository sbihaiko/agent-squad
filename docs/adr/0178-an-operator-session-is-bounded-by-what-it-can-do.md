# ADR-0178: An operator session is bounded by what it can do, never by a filter of what it reads

## Status

Accepted. This ADR extends [ADR-0154](0154-the-owner-talks-to-the-operator-through-slack.md) and
[ADR-0177](0177-slack-rules-in-the-project.md). It adds the guard block of the committed Slack file and the audit line of
the `voiced` daemon.

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

## Decision

1. **The bound is on admission, never on meaning.** The session is bounded by the actions its roles grant and by the
   messages the gate admits. No rule reads a message to decide whether the session may see it. The guard counts messages
   and measures their length; it reads no word of the body.
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

## Guards

- `tests/test_slack_policy.py` proves the guard block, its defaults and one defect per malformed field.
- `tests/test_voiced_guard.py` proves the rate refusal, the size refusal, the absent inbox line, the one reply per
  channel, the reason class, the audit fields and the redaction.
