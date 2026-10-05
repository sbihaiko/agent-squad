# ADR-0011 — If it is hand-launched in a chat session, it is not automated

## Status

Accepted, 2026-09-29.

## Context

A critical path that lives inside a live session breaks when the session dies. A reboot or a crash kills the session. Nobody is then listening.

A component with its state in a file or a database, restarted by a supervisor, recovers by itself.

A relay session that promises to report "when the answer arrives" can die first. A revive routine written as prose fails as prose. The order of its steps is not enforced.

## Decision

1. **The critical path never lives inside a conversation.** Run it as a unit that a supervisor restarts on boot. Keep its state in a file or a database, not in a session.
2. **A channel is a channel. It is not an agent.** A one-way relay states where the answer will appear. The reader goes there. No session promises to come back later.
3. **Every invocation leaves a run record.** The record holds the caller, the session id, the cost, the exit code, and what shipped. The record alone must let you reconstruct the outcome.
4. **A revive routine is a script, not prose.** Step order is code. Test it like code. A routine that you cannot execute cannot be trusted.
5. **Check health between stages. Never infer it from a status line.** A step that cannot measure refuses to continue (ADR-0008). There is no blanket force flag. An override carries a recorded reason.
6. **Derive properties. Never assume them.** A count, a path, or a session id that held on the first run is not a constant.
7. **The chain stops before a live write** unless a recorded approval covers it (ADR-0010).

## Consequences

A reboot costs seconds, not hours. Recovery needs no human awake.

Cost: each moving part needs a unit definition, a log, and a restart policy. Keep an inventory of those parts.

A unit that is up is not a loop that works. Track the time since the loop last ran end to end. Exercise it when that time grows.

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
