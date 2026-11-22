# ADR-0154: The owner talks to the operator through Slack

## Status

Accepted.

## Context

The owner talks to the operator session, the agent that works a card outside the loop ([HAL9k](../../GLOSSARY.md)). The conversation needs one channel, and it is Slack. A `voiced` daemon turns each owner Slack message into one line in the project's inbox, and it posts each answer back into the owner's thread (`contrib/voice/OPERATOR.md`). The hub shows the run dashboard, the board, the loop switch and the settings, and it sets the Slack channels. The hub carries no conversation of its own. No session wire protocol exists: the hub serves no session route, and the design notes define none.

## Decision

The rules of each project's Slack use, its two files, its workspace classes, its personas and its roles are [ADR-0177](0177-slack-rules-in-the-project.md). That ADR replaces decisions 3 and 5 of this one.

1. **Slack is the one conversation channel.** The owner and the operator talk in a Slack channel. The `voiced` daemon writes each owner message to the project's inbox, keyed by Slack's channel id, with the transcription of a clip. The session reads the inbox through a Monitor and appends one answer line to the outbox of the same channel and thread. Guard: `tests/test_voiced.py`, `tests/test_voice_setup.py`.
2. **Slack is the wake path in an `owned` workspace, never a poll.** The session never asks an `owned` Slack workspace for messages. A Monitor on the inbox wakes it for each new line, so a channel with nothing new wakes it for nothing. A `guest` workspace is the one place where polling is allowed, because the connector there is the only transport (ADR-0177, decision 10). Guard: `tests/test_voiced.py`.
4. **An answer is text first, then a short spoken summary in the same thread.** The outbox line carries the thread, the full text and the short speech. Without a speech line the daemon speaks the text. Guard: `tests/test_voiced.py`.
6. **The hub serves no session and no terminal.** It serves no route for a live session and none for a terminal, so a message never reaches a session through the hub. The conversation goes through Slack alone. Guard: `tests/test_hub_no_session.py`, `tests/test_hub_no_terminal.py`.

## Consequences

A message the owner sends in Slack reaches the operator as one inbox line, and the answer stays in the owner's thread. The spoken summary follows the text, so a slow TTS never delays the answer.

A channel's setting lives in the project's Slack files (ADR-0177): the committed file holds the rules, and the profile's local file holds a client's ids and the people of each role.
