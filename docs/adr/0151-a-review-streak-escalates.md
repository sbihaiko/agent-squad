# ADR-0151: A review streak escalates

## Status

Accepted.

## Context

A child that the reviewer sends back again and again spends money and produces no delivery. The same coder model often repeats the same fix for the same finding. A reviewer that marks a nit as a change request starts fix cycles that add no value. When two components review or fix one child, their verdicts can conflict, and an approval can apply to a commit that nobody reviewed.

## Decision

1. **Three consecutive CHANGES verdicts on one issue send it to needs-human** (`frozen:owner`, ADR-0029) instead of another dispatch.
   The freeze posts its own issue comment, never the edited status comment: an edited comment keeps the time of its first
   post, so the timeline would show it above the fix rounds. The comment names the head the last review read, the streak
   count and the total of reviews of the issue, with the last Blocking findings. One head gets at most one freeze comment,
   so a freeze after a rearm at the same head repeats nothing.
2. **Identical findings twice stop the fix cycle.** The next attempt uses a stronger coder model.
   A fix round that writes no artifact is the same finding unanswered, not a new finding (ADR-0134 reads a round that
   changed nothing by its fingerprint). It is the finding's first repetition, so it moves the coder at once: the round
   after it answers on the next coder of the run's chain, and the unchanged head is not reviewed again. The record keeps
   the child's last turn of that round, so a reader sees why it stopped. The fix round that answers a repeated finding
   names the premise the last fix rested on, and it tests that premise before it attempts a third fix. The repeat is the
   record's own rounds: a requeue cuts a fresh worktree where the last fix's commit is not, so a repeat that only the
   issue ledger holds moves the coder but adds no premise sentence.
   The base rule of ADR-0172 acts first: a Blocking finding whose repro fails at the merge-base and at both heads with the
   same text moves to Debt in the review itself, so it never reaches the identical-findings count or a coder change.
3. **Reviewer severity is calibrated against labeled examples.** A finding that is only Debt or nit never counts as CHANGES.
4. **An approval binds to the reviewed commit.** A new commit after the approval needs a new review.
5. **A live run that reviews its own children owns them.** childcc never reviews or fixes them while their run is live, and
   missing review evidence holds delivery. That hold has no deadline, because the run may still review the head. Once the
   run has its final verdict (ADR-0167) no actor can review that head again, so the hold loses its path to an approval and
   the child falls to the deferral deadline (ADR-0134): it is requeued with the cause named, never reviewed here. The one
   exception is a finished run's child whose tip moved past the head the run reviewed: no run will read that tip, so childcc
   reviews it once per head and enters the child on its own review (#808).
6. **A loop's ac-verify gate that fails identically twice in a row ends the loop needs-human, with the repeated evidence.** Findings read the same once line numbers, shas and whitespace are removed; order is not significant. Only a passing ac-verify gate, a moved head or a changed finding restarts the count. Other gates do not reset it.

## Consequences

- A review loop ends at a known bound, and the owner sees the issue instead of a growing spend.
- A repeated finding gets a different model, not a fourth copy of the same attempt.
- The review streak is a loop fact in the issue ledger (ADR-0149).

## Guard

`tests/test_review_streak.py`: identical findings twice change the coder, different findings do not, a fix round that
wrote no artifact repeats its finding and changes the coder on the first repetition, the record keeps that round's
last turn, a repeated finding's fix round adds the premise sentence to its prompt and a first finding's does not, a
re-dispatched issue's first fix round gets no premise sentence because its fresh worktree cannot show the last fix, and
the dispatch reads the ledger's moves, so a fix round or a re-dispatched issue starts one place down the
chain; a ledger that is held starts no child session, so a corrupt or missing ledger asks the owner instead of starting
the child on the top coder, and a hold reaches the run as needs-human: a dependent of a held issue is held with it and
`first_failure` cancels none of the held issue's siblings, so no other child's failure folds the hold to `failed`.
`contrib/squad-goal/scripts/test-childcc.sh`: a live run that reviewed a child owns it, so the child-completion pass
neither reviews nor fixes that child, and the wave merges only a child whose approval stands at its head; a live run's
unapproved child holds its delivery past every deadline, while a finished run's unapproved child runs the deferral
deadline, is requeued with the cause named at its bound, and a run resumed after its verdict reads as live again. The streak is a ledger fact: `tests/test_issue_ledger.py` and
`contrib/squad-goal/scripts/test-issue-ledger.sh` (three CHANGES verdicts end the issue needs-human, an approval resets
the streak, identical findings twice move the coder, a verdict is one fact per run and round, and a rearm clears it with
its floor) and `contrib/squad-goal/scripts/test-runreview.sh` (the run's review gate writes the verdict, freezes the card
and stops the requeue). `tests/test_ac_repeat.py`: two identical ac-verify failures end the loop needs-human with exactly two actor rounds, across a
resume too; a passing scope gate between the failures does not reset the count. The severity calibration and the
approval binding are a draft until their own tests exist (ADR-0018).
