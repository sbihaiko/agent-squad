# ADR-0180: A full role gets the owner's own session

## Status

Accepted.

## Context

The bounded operator session ([ADR-0154](0154-the-owner-talks-to-the-operator-through-slack.md)) reads each owner message from the project inbox, under a tool set, an MCP bound and an operator brief that the daemon writes. Some messages need the owner's own Claude setup instead: the owner's settings, MCP servers, skills and permission mode. A bounded session cannot offer those.

## Decision

1. **A role names its session mode.** A role in the project's Slack file carries `session: full` or `session: bounded`. A role with no `session` field is bounded. `squad/slackpolicy.py` returns the mode in each decision. Guard: `tests/test_slack_full_session.py`.
2. **Only the owner of the channel's workspace gets a full session, and only in a project channel.** The owner user id of
   each workspace lives in the hub registry, and `voiced` reads it from there and from no other source. A `full` decision
   holds only when the sender that id names owns a `full` role, the revision is active, and the event is a channel message.
   A project channel reads its own project's registry entry. A direct message and a channel that owns no project read
   every configured project's registry, and the first workspace match answers (`Daemon.workspace_owner`).
   A direct message, a guest channel and a channel with no active revision run as `bounded`. Guard:
   `tests/test_slack_full_session.py`, `tests/test_voiced_full_turn.py`, `tests/test_voiced.py`.
3. **A full role admits only the owner or a granted member.** The validator takes the owner id map and the grant list, and
   it names a defect for a `full` role whose member the registry names neither way. A member of a `full` role also names
   a workspace the committed file declares, and a channel, when it names one, that belongs to that workspace; a row that
   fails either check is its own named defect before the owner or grant check. The hub writes no grant, so the owner
   adds one to the registry file. Guard: `tests/test_slack_policy.py`.
4. **The hub writes the owner id.** One hub route writes one owner user id per workspace into the registry, and it
   refuses a value that is not a Slack user id or a workspace that is not a Slack workspace id. Guard:
   `tests/test_hub_slack_owner.py`.
5. **A full message runs one turn of the project's session.** `voiced` runs `claude -p`, with `--resume` after the first turn, in the project checkout. The message goes to the CLI on stdin, never as an argument. The turn carries no tool bound, no MCP bound and no operator brief, so the owner's settings, MCP servers, skills and permission mode apply. The turn drops the operator's config and temporary-folder overrides. Guard: `tests/test_full_turn.py`.
6. **The state folder keeps one session id per project.** The first turn creates the session and records its id. Later turns of that project resume it. Turns of one project run one at a time. Guard: `tests/test_full_turn.py`, `tests/test_voiced_full_turn.py`.
7. **The answer posts after redaction.** `voiced` posts the turn's final text in the owner's thread after the secret redaction of [squad/redact.py](../../squad/redact.py). A failed turn posts one fixed sentence. Guard: `tests/test_voiced_full_turn.py`.
8. **Each turn writes one audit line.** The line holds the time, the sender, the role, the channel, the session id, the exit code, the failure flag and the character count. It holds no message text and no answer text. Guard: `tests/test_voiced_full_turn.py`.
9. **The probe proves a full turn outside the sandbox.** Each call of the operator probe that starts a bounded session runs through the sandbox wrapper. Each call that starts a full turn runs outside it. The probe proves the resume and the stdin message with the real CLI: a word given on stdin in the first turn comes back from the resumed second turn. Guard: `tests/test_operator_probe.py`.
10. **A `!stop` ends the running turn out of band.** `voiced` reads the message `!stop` before it queues a message, and a full role's stop ends its project's running turn at once, with its whole process tree, before the next queued message runs. A bounded session's stop ends nothing, and each stop writes one audit line. Only the requester who started the turn ends it: the registry compares the same identity that started the turn, so a full role handed to anyone else clears nothing. A leading bot mention is dropped, so `@the-bot !stop` stops too. The stop's audit line holds the time, the sender, the role, the channel, the repository, `decision: stop` and `ended: true|false`, and nothing from the message text. Guard: `tests/test_voiced_full_turn.py`, `tests/test_full_turn.py`.

## Consequences

A full turn holds the owner's authority over the project checkout. The sandbox wrapper does not bound it, so a full role is a grant to a session the owner runs, not to a bounded operator. The bounded path keeps its guards.

The session mode lives in the committed Slack file. A change to `session` takes effect when the file's revision goes active.

A caller of `slackpolicy.decide` states the event keys `owner`, `active` and `dm`, and the decision reads all three: an
event that omits `active` runs bounded, and an event that omits `dm` reads as a channel message. The daemon fills the
three keys from the socket event and the hub registry, so no caller infers them from a default.

`Daemon.policy_event` has no caller outside the daemon, and the daemon passes every argument. The removed
`baseline_files` helper has no remaining caller either, so neither one keeps a compatibility shim.
