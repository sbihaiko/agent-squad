# ADR-0025 — Delegate context-heavy work to subagents; the main session keeps the decision

## Status

Accepted, 2026-09-29.

## Context

Long sessions collect context faster than the work justifies. A full transcript adds tens of thousands of tokens to the window. A broad multi-file sweep adds more. A ruling that needs a deep, slow answer uses attention that the main thread could spend on the next request.

Compaction is the trailing fix. It shrinks a context that already grew, and it discards detail. Subagents are the leading fix. Only the subagent's report enters the main window. The files it read, the sweeps it ran, and the long drafts it wrote stay in its own window.

Without a recorded rule, a session either bloats because it reads everything inline, or under-delegates because it reaches for a subagent too late.

## Decision

1. **Delegate when the work would pull a large or slow load into the main session.** The trigger is what enters the main context, not the size of the work. A multi-file read, a full transcript, a broad sweep, or a deep ruling belongs in a subagent window. The main session receives the conclusion and the load-bearing evidence, never raw dumps.
2. **Choose a subagent that suits the task's capability, risk, latency, and cost.** Run genuinely independent investigations in parallel, not one after another inline. Do not fix an agent taxonomy or model tier here. Capabilities change faster than this record.
3. **A subagent's output is evidence to verify, not a verdict to accept.** Measure its load-bearing claims again in the main session before they become findings, plans, or recorded decisions. A report of success is weak evidence that an external side effect landed. Audit the target state directly.
4. **Delegation never bypasses a gate.** A subagent does not perform an action that ADR-0024 reserves for an explicit grant. It does not touch content that another party owns. It gathers, analyzes, and drafts.
5. **The main session owns the decision and the write.** A subagent proposes and returns. The main session adjudicates, approves, and records.

## Consequences

The main session stays lean across long runs. Deep questions get a full answer without occupying the main thread.

Cost: an asynchronous round trip. A subagent cannot see the main session's context. The prompt must carry the facts it needs, or the answer comes back weaker.

Risks: trusting an unverified claim, which item 3 guards against. Delegating a decision that belongs to the owner, which item 5 guards against.

## Guard

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
