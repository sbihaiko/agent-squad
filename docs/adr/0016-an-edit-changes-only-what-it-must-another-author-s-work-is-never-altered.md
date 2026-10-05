# ADR-0016 — An edit changes only what it must; another author's work is never altered

## Status

Accepted, 2026-09-29.

## Context

Some files have an owner other than the agent that reads them. An agent that finds such a file wrong can fix it in one edit. That fix overwrites the owner's work without a record. The owner and the agent then disagree without knowing it.

A convention that is only described in notes is not a binding rule. Work planned around it can rely on machinery that does not exist.

## Decision

1. **Plain replacement.** Unless the governing rule asks for a tracked correction, an edit replaces wrong with right and leaves nothing behind. The result is the final intended text. It never shows the old value beside the new one.
2. **An edit that keeps the obsolete value is a planning bug.** You can detect it mechanically. Diff the old text against the replacement. If the change is only insertion, the old text survived. Refuse that edit as its own class, next to not-found and no-op. An expansion that keeps every old word may override the refusal with a recorded reason. Treat that as an exception, never a default.
3. **Never alter another author's work without explicit authority.** This covers their text, tickets, state files, and repository. An agent that disagrees with a rule records the open boundary where its readers will see it. It does not edit the rule.
4. **Check ownership before the edit.** Before you write, establish who owns the file. An edit that overlaps another owner is re-scoped. Otherwise, accept the loss deliberately and record it.
5. **Verify with every instrument the change could damage.** A rule edit can be correct as prose and still break the code that reads it. Run the generator and the rule's guard test after every pass.
6. **Verify a convention against the binding rule before you build for it.** A practice described in notes is not a requirement. Building for it can leave a worse artifact than not building.

## Consequences

Each file keeps one owner. No contradictory intermediate state exists. Disagreement becomes a recorded open boundary, not a silent divergence. That also makes it fixable.

Cost: an agent sometimes waits for the owner to correct a rule it can see is wrong. A reader cannot see what changed without version history. Check whether the reader wants the corrected text or the difference.

## Guard

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
