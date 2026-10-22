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
wrote no artifact repeats its finding and changes the coder on the first repetition, the record keeps that round's
last turn, and the dispatch reads the ledger's moves, so a fix round or a re-dispatched issue starts one place down the
chain; a ledger that is held starts no child session, so a corrupt or missing ledger asks the owner instead of starting
the child on the top coder, and a hold reaches the run as needs-human: a dependent of a held issue is held with it and
`first_failure` cancels none of the held issue's siblings, so no other child's failure folds the hold to `failed`.
`contrib/squad-goal/scripts/test-childcc.sh`: a run that reviewed a child owns it, so the child-completion pass neither
reviews nor fixes that child, a child whose approval is missing or stale holds its delivery, and the wave merges only a
child whose approval stands at its head. The streak is a ledger fact: `tests/test_issue_ledger.py` and
`contrib/squad-goal/scripts/test-issue-ledger.sh` (three CHANGES verdicts end the issue needs-human, an approval resets
the streak, identical findings twice move the coder, a verdict is one fact per run and round, and a rearm clears it with
its floor) and `contrib/squad-goal/scripts/test-runreview.sh` (the run's review gate writes the verdict, freezes the card
and stops the requeue). The severity calibration and the approval binding are a draft until their own tests exist
(ADR-0018).
