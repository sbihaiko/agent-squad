# ADR-0151: A review streak escalates

## Status

Accepted.

## Context

A child that the reviewer sends back again and again spends money and produces no delivery. The same coder model often repeats the same fix for the same finding. A reviewer that marks a nit as a change request starts fix cycles that add no value. When two components review or fix one child, their verdicts can conflict, and an approval can apply to a commit that nobody reviewed.

## Decision

1. **Three consecutive CHANGES verdicts on one issue send it to needs-human** (`frozen:owner`, ADR-0029) instead of another dispatch.
2. **Identical findings twice stop the fix cycle.** The next attempt uses a stronger coder model.
   A fix round that writes no artifact is the same finding unanswered, not a new finding (ADR-0134 reads a round that
   changed nothing by its fingerprint). It is the finding's first repetition, so it moves the coder at once: the round
   after it answers on the next coder of the run's chain, and the unchanged head is not reviewed again. The record keeps
   the child's last turn of that round, so a reader sees why it stopped.
3. **Reviewer severity is calibrated against labeled examples.** A finding that is only Debt or nit never counts as CHANGES.
4. **An approval binds to the reviewed commit.** A new commit after the approval needs a new review.
5. **A run that reviews its own children owns them.** childcc never reviews or fixes them. Missing review evidence holds delivery.

## Consequences

- A review loop ends at a known bound, and the owner sees the issue instead of a growing spend.
- A repeated finding gets a different model, not a fourth copy of the same attempt.
- The review streak is a loop fact in the issue ledger (ADR-0149).

## Guard

`tests/test_review_streak.py`: identical findings twice change the coder, different findings do not, a fix round that
wrote no artifact repeats its finding and changes the coder on the first repetition, and the record keeps that round's
last turn. The three-CHANGES freeze, the severity calibration and the approval binding are a draft until their own
tests exist (ADR-0018).
