# A gate stage reuses a green verdict for its tree and its suite, and records the reuse

## Context

A run gates the same tree at more than one stage. A dispatch's review command proves a child's tree, and the run's
final gate proves that tree again with the same suite. Each stage ran the suite again, and the run paid the wall time
for a proof it already held.

The same proof is also asked by other callers and other processes: a wave entry, a card-named gate and a landing check
each run the fast gate over the tree a child already proved. A base commit moves at every landing, so a key that holds
it misses most of those asks, though the tree and the delta did not change.

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
4. **The memo spans callers and processes.** A pass is kept under a machine-wide key: the tree object, the digest of
   the uncommitted state, the base ref, the merge-base and the battery text. The base commit is no part of the key, so a
   base that moved with the same merge-base still answers; a changed tree, dirt, merge-base or battery is a new key. The
   base must resolve. The shipped battery reads the base only through the merge-base; a battery that reads the base
   tip directly must put the base commit back in its key before it uses the memo. A run's
   record-bound stage still reads the `gate_result` events of its own run. An approval binds to the tree it proved.
   A caller that would otherwise take a machine lock asks the key first: the wave entry reads the memo before the gate
   lock, so a child whose final gate proved the tree it enters with enters without a second battery and without waiting
   behind another gate.
5. **The reuse is a record event.** A reused stage writes `gate_reuse` with the tree and the stage it reused, its
   artifact carries an empty finding list, and its outcome names `reused_from`. A reader sees the reuse; it is never a
   silent skip.
6. **The landing gate and the nightly keep their own full run.** The memo answers fast-gate callers across processes;
   it never replaces the full suite a landing or a promotion owes. Each reuse is recorded.

## Consequences

A pass won on a tree answers every caller that meets that tree, delta and battery, whatever moved the base. A red or a
missing proof is never kept, so it never answers.

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

`contrib/squad-goal/scripts/test-fast-gate.sh` guards the lock-free probe (`--memo-hit`): it answers a stored pass with
its reuse line and no battery, and it answers no pass for a red, a different tree, a tree whose dirt changed or a memo
turned off. `contrib/squad-goal/scripts/test-wave-conflict.sh` guards the wave entry that reads the probe before the gate lock: a
child whose final gate proved the tree it enters with enters under a held gate lock with no second battery and with the
reuse named in its entry record, while a child that committed after its gate still meets the lock and runs its own
battery.
