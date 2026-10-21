# ADR-0151: A review streak escalates

## Status

Accepted.

## Context

A child that the reviewer sends back again and again spends money and produces no delivery. The same coder model often repeats the same fix for the same finding. A reviewer that marks a nit as a change request starts fix cycles that add no value. When two components review or fix one child, their verdicts can conflict, and an approval can apply to a commit that nobody reviewed.

## Decision

1. **Three consecutive CHANGES verdicts on one issue send it to needs-human** (`frozen:owner`, ADR-0029) instead of another dispatch.
2. **Identical findings twice stop the fix cycle.** The next attempt uses a stronger coder model.
3. **Reviewer severity is calibrated against labeled examples.** A finding that is only Debt or nit never counts as CHANGES.
4. **An approval binds to the reviewed commit.** A new commit after the approval needs a new review.
5. **A run that reviews its own children owns them.** childcc never reviews or fixes them. Missing review evidence holds delivery.

## Consequences

- A review loop ends at a known bound, and the owner sees the issue instead of a growing spend.
- A repeated finding gets a different model, not a fourth copy of the same attempt.
- The review streak is a loop fact in the issue ledger (ADR-0149).

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
