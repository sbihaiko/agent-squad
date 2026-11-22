# The operator session

One operator session answers the owner's Slack messages for one project. The `voiced` daemon writes each owner message
to that project's inbox as one JSON line, with the transcription of a clip. The session never asks Slack for messages: a
Claude Code Monitor on the inbox is its only wake path.

## Watch the inbox

Start one Monitor per inbox the session owns:

    tail -n 0 -F "$VOICE_STATE/inboxes/<channel id>.jsonl"

A queue is keyed by Slack's channel id, never by its name, so a channel renamed in Slack keeps its inbox. For the DM,
use `$VOICE_STATE/inboxes/dm.jsonl`. `-n 0` skips the lines already answered, and `-F` keeps following the file
when the daemon reopens it. Each printed line wakes the session at once. A line that arrives while the session is busy
is delivered when the turn ends.

After an upgrade, a Monitor still tailing the old root `$VOICE_STATE/inbox.jsonl` sees no new line, because the
daemon now writes the DM queue to `$VOICE_STATE/inboxes/dm.jsonl`. Restart that Monitor on the new path.

The inbox line carries `id`, `ts`, `user`, `channel`, `text`, `clip`, `language`, `persona` and `role`, the decision
the policy files give the event. `text` is the owner's words, or the transcription of the clip; a line with no text
carries no `text` key. A line from a bot, a stranger or a Slack Connect user never reaches the inbox; it stays data in
`other.jsonl` with the same `language`, `persona` and `role` fields, so a reader tells which caller and channel role
produced it.

## Answer through the outbox

Append one JSON line to the outbox of the same channel:

    "$VOICE_STATE/outboxes/<channel id>.jsonl"

For the DM, use `$VOICE_STATE/outboxes/dm.jsonl`. One line is one answer:

```json
{"thread_ts": "1762500001.000200", "text": "The full answer, with the detail.", "speech": "A short sentence to speak."}
```

- `thread_ts`: the `ts` of the inbox line, so the answer stays in the owner's thread. Optional for the DM.
- `text`: the answer Slack shows as the bot's message, and the detail the owner reads.
- `speech`: the short spoken script. Omit it, and the daemon speaks `text`.
- `channel`: the channel id. Only the DM outbox names it, because a project outbox already names its channel.

The daemon posts the text at once, speaks the first sentence while the text goes out, and speaks the rest as one clip.
It reads every outbox on its own. The session makes no Slack call and waits for no reply.

## No poll

The one-minute poll is retired. No cron entry, no launchd agent and no `watch.py` run is scheduled for the session: the
Monitor alone wakes it, and a line the daemon writes is the only thing that starts a turn.

## Setup

The owner creates the workspace, the app and the tokens. Run `python3 contrib/voice/setup_wizard.py` for each step and
its Keychain entry. Run `contrib/voice/install-agents.sh <promoted-sha>` to pin a deployment checkout to a promoted commit
and load the daemon (`com.squad.voiced`) and the supervisor (`com.squad.operators`) agents from it.
`contrib/voice/rollback-agents.sh` returns to the previous proven commit, and `contrib/voice/uninstall-agents.sh` removes
both agents. None of these commands prints a secret.
