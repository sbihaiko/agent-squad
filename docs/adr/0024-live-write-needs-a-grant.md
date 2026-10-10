# ADR-0024: Ready is not authorized: a consequential live write needs an explicit, per-action grant

## Status

Accepted.

## Context

Agents here can push to a remote, deploy, restart a service or send a message. A verified draft and an authorized action are not the same. Reversibility is the wrong boundary: a message can be deleted, but a third party may already have seen it.

## Decision

1. **No consequential live write without a current, explicit grant from the owner for that action.** "Consequential" is any mutation that a third party can observe, such as a message, a push to a remote, a deploy or a production restart. A verified draft is a candidate, never a license.
2. **The grant is per-action.** It covers the named target and change set. A later batch, message or deploy needs a fresh grant.
3. **A standing grant exists only in writing.** It names the channel, the target and the class of action. Anything outside the class falls back to rule 1.
4. **Record a grant given in conversation before you use it,** where the next session reads it.
5. **When in doubt, ask.** Never widen a narrow grant.
6. **Gate only the covered mutation.** Reading, generating, drafting, reviewing and committing inside this repo continue freely.

## Guard

None yet; a draft.
