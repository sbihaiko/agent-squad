# ADR-0180: A full role gets the owner's own session

## Status

Accepted.

## Context

The bounded operator session ([ADR-0154](0154-the-owner-talks-to-the-operator-through-slack.md)) reads each owner message from the project inbox, under a tool set, an MCP bound and an operator brief that the daemon writes. Some messages need the owner's own Claude setup instead: the owner's settings, MCP servers, skills and permission mode. A bounded session cannot offer those.

## Decision

1. **A role names its session mode.** A role in the project's Slack file carries `session: full` or `session: bounded`. A role with no `session` field is bounded. `squad/slackpolicy.py` returns the mode in each decision. Guard: `tests/test_slack_full_session.py`.
2. **Only the daemon's owner gets a full session.** A message from any other member, a bot or a data event stays bounded, whatever its role says. Guard: `tests/test_slack_full_session.py`.
3. **A full message runs one turn of the project's session.** `voiced` runs `claude -p`, with `--resume` after the first turn, in the project checkout. The message goes to the CLI on stdin, never as an argument. The turn carries no tool bound, no MCP bound and no operator brief, so the owner's settings, MCP servers, skills and permission mode apply. The turn drops the operator's config and temporary-folder overrides. Guard: `tests/test_full_turn.py`.
4. **The state folder keeps one session id per project.** The first turn creates the session and records its id. Later turns of that project resume it. Turns of one project run one at a time. Guard: `tests/test_full_turn.py`, `tests/test_voiced_full_turn.py`.
5. **The answer posts after redaction.** `voiced` posts the turn's final text in the owner's thread after the secret redaction of [squad/redact.py](../../squad/redact.py). A failed turn posts one fixed sentence. Guard: `tests/test_voiced_full_turn.py`.
6. **Each turn writes one audit line.** The line holds the time, the sender, the role, the channel, the session id, the exit code, the failure flag and the character count. It holds no message text and no answer text. Guard: `tests/test_voiced_full_turn.py`.
7. **The probe proves a full turn outside the sandbox.** Each call of the operator probe that starts a bounded session runs through the sandbox wrapper. Each call that starts a full turn runs outside it. The probe proves the resume and the stdin message with the real CLI: a word given on stdin in the first turn comes back from the resumed second turn. Guard: `tests/test_operator_probe.py`.

## Consequences

A full turn holds the owner's authority over the project checkout. The sandbox wrapper does not bound it, so a full role is a grant to a session the owner runs, not to a bounded operator. The bounded path keeps its guards.

The session mode lives in the committed Slack file. A change to `session` takes effect when the file's revision goes active.
