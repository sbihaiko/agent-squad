# A gate stage reuses a green verdict for its tree and its suite, and records the reuse

## Context

A run gates the same tree at more than one stage. A dispatch's review command proves a child's tree, and the run's
final gate proves that tree again with the same suite. Each stage ran the suite again, and the run paid the wall time
for a proof it already held.

The reuse must never weaken the gate. A stage must never read another run's verdict, a red verdict, or a verdict for a
different tree. A silent skip would hide what happened, so the reuse must be on the record.

## Decision

1. **The suite is what a stage runs, never the id of its check.** An `ac-verify` stage runs each command from its named
   artifact through `/bin/sh -c`; a `review-command` stage runs its argv as it stands. Two stages of one run that meet
   the same command list run one suite, whatever checks name them. A check that runs no shell command has no suite and
   is never memoed.
2. **The tree is its commit and a digest of its uncommitted state.** The digest reads the porcelain status stream in
   its `-z` form, so a path with a space, a tab or a non-ASCII character reads as the file it is, and an untracked
   path counts by its contents. A dirty path the key cannot read, such as an untracked directory git does not expand
   or a file it may not open, is no key at all: the stage runs its check. A pass over uncommitted work never answers
   for the commit.
3. **Only a pass is reused.** A red verdict is recorded and never answers for a later stage. A review that reports no
   review is not an approval, so it is not kept.
4. **The memo is the run's own record.** A stage reads the `gate_result` events of its own run and no other run's. An
   approval binds to the tree it proved.
5. **The reuse is a record event.** A reused stage writes `gate_reuse` with the tree and the stage it reused, its
   artifact carries an empty finding list, and its outcome names `reused_from`. A reader sees the reuse; it is never a
   silent skip.
6. **The landing gate and the nightly keep their own full run.** The memo covers a run's gate stages only.

## Consequences

A run whose review command and final gate run one suite runs it once. A stage that meets a dirty tree the key cannot
read runs its own suite, so the saving is lost, never the proof.

A review command can write facts beyond its verdict, such as an issue ledger event or a status comment. A reused
approval does not write those facts a second time: the stage that first passed wrote them. A later stage with a
different tree or a different command runs the review again.

## Guard

`tests/test_gate_reuse.py`. It proves that a gate stage reuses a green verdict for the same tree and suite and records
`gate_reuse`; that a dispatch's review stage and the run's final gate run one suite and the second reuses the first;
that an untracked name with a space counts by its contents, so two contents give two keys; that a dirty tree the key
cannot read skips the reuse; and that a red verdict, a different suite, a different tree and another run's verdict are
never reused.
