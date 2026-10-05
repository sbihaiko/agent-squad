# ADR-0010 — The agent drafts; the owner's control is approval, not authorship

## Status

Accepted, 2026-09-29.

## Context

Rafael cannot write every output by hand. That is the reason agents exist. A chain that stops before any text is written stops one step short of its purpose.

The opposite failure is just as real. A blanket standing approval removes review from the outputs that need it most.

Both failures concern the same control. The question is never who types the words. It is who accepts the consequence.

## Decision

1. **Drafting is in-method.** The agent writes the text. Producing the words is the job. It is not a boundary to avoid.
2. **A draft is not a delivered output.** It lives in a staging location until approved. Approval and delivery are separate steps. Delivery is last.
3. **Approval is the primary control. It is not bookkeeping.** Rafael may replace any text at any point without justification. Passing a guard test never substitutes for approval.
4. **Standing approval is explicit, scoped, dated, and recorded.** It names a scope and a class of output. "Approve everything" with no scope is not approval. An unrecorded standing approval does not exist.
5. **Remediation is bounded.** Make one fix and one recheck. A second recheck means the content is wrong, not the wording. Escalate instead of tuning.
6. **Never edit another party's material or fields** without explicit authority.
7. **Correct a delivered statement later found wrong.** Correct it in the same place where you delivered it. Report the correction in the same cycle.

## Consequences

Output arrives at machine speed. The review effort lands where it changes an outcome.

Approval fatigue is answered by approving at a grouped level, not item by item. Group by scope or class of output.

Standing approvals accumulate. This is the main risk of this decision. List them, date them, and review them.

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
