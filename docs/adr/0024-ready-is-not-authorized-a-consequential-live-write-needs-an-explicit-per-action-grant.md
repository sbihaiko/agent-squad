# ADR-0024 — Ready is not authorized: a consequential live write needs an explicit, per-action grant

## Status

Accepted, 2026-09-29.

## Context

Agents in this system can reach the outside world. They can push to a remote, deploy, restart a service, or send a message.

A verified draft and an authorized action are not the same. Without a recorded rule, a session reads "the draft is correct" as "send it".

Reversibility is the wrong boundary. A message can be deleted and a commit can be reverted, but a third party may already have seen the effect.

## Decision

1. **No consequential live write without a current, explicit grant from Rafael for that action.** "Consequential" covers any mutation that a third party can observe. Examples are a message, a push to a remote, a deploy, and a restart of a production service. A verified draft is a candidate, never a license.
2. **The grant is temporary and per-action.** It covers the named target and change set. It does not extend forward. A later batch, message, or deploy against the same target needs a fresh grant.
3. **A standing grant exists only in writing.** It names the channel, the target, and the class of action, and it carries a date. Anything outside the written class falls back to item 1. An undeclared habit is not a standing grant.
4. **Record a grant given in conversation before you use it.** A grant that lives only in a chat session dies at the next resume. The next session then re-asks or guesses. Write the grant where the next session reads it.
5. **When in doubt whether a grant covers an action, ask.** Never widen a narrow grant. "Release this batch" does not release the next one.
6. **Gate only the covered mutation.** Reading the target, running generators, drafting, reviewing, and committing inside this repo continue freely. These steps make the granted action fast and safe.
7. **This rule is prospective only.** Actions taken under an earlier arrangement stand.

## Consequences

No unauthorized message, push, or deploy leaves the system. The handoff is clear: the agent drafts, the pipeline verifies, Rafael grants, and the write happens.

Cost: work stalls while Rafael is unavailable. A busy channel needs a written standing grant, or it produces constant questions. That pressure is useful, because writing the standing grant forces its limits to be stated.

Risk: a reader may take this rule as blocking drafting and local work. It does not. Only the live mutation is gated.

## Guard

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
