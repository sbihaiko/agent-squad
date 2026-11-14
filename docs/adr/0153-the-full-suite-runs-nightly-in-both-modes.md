# ADR-0153: The full suite runs nightly in both modes, in its own tree and lock

## Status

Accepted.

## Context

Every gate the loop runs is bounded by time. The fast battery that a work-branch tip and a wave entry run deselects `heavy`, runs one mode and runs only the test files its diff reaches, so the calls it skips, the two-mode difference and the files it does not reach go unchecked until the complete CI tier runs on the `stg` tip ([ADR-0173](0173-the-complete-tier-runs-on-the-promotion-request.md)); its result line says which files it ran, or that it ran all. The promotion proof runs the full suite in both modes, but only when the owner starts it, and it holds the landing lock while it runs, so no wave lands meanwhile ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md) rule 3). A declared, covered change on top of a full pass may be carried by a delta proof instead of a new full run ([ADR-0156](0156-a-full-proof-carries-across-a-covered-delta.md)); the nightly is never replaced by it.

A defect that only a heavy call or a serial run shows can therefore sit in the landing branch for days, and the reader of the hub has no reading of it at all.

## Decision

1. **The full suite runs nightly, in both modes, heavy included.** `nightly.sh` runs `pytest -p no:deepeval -n auto` and then `pytest -p no:deepeval -p no:xdist` over the whole suite, with no `-m 'not heavy'`. The watch cycle starts it detached, once a day, under its own single-flight stage; a later cycle the same day runs nothing.
2. **It runs in its own tree.** The suite runs in a plain clone at the state directory, never in the owner's checkout and never in a child's worktree, so no suite it runs can touch the line the loop is working on. It is not a `~/wt-*` worktree, so the worktree cleanup never removes it.
3. **It takes its own lock and never the landing lock.** Its serial name is `nightly`, in try mode, so a second start never queues behind a suite of hours. It never takes the `gate` lock that the landing gate, the wave entry and the promotion wait on, and a nightly in flight never delays a landing; a held landing lock holds the nightly before it starts (rule 7).
4. **One result record is the nightly's only output.** The record is written by a temp file and a rename ([ADR-0030](0030-atomic-writes.md)) and holds the time, the result, the sha, each mode's own result and the failing test ids. The result is a pass only when both modes pass. The two mode logs stay beside it.
5. **The hub draws the record as one fact.** The footer reads the record: the result and its age, in the error tone when the suite is red, with each mode's result, the sha and the failing tests on the hover. A record the hub cannot read is a question mark, never a pass.
6. **A red result alerts once and is a report, never a hold.** The alert takes the path `health.sh` already uses: one line in the alerts file and one notification. The dispatch hold both dispatch passes read says the note once per record and the pass goes on: each landing carries its own local fast gate, and the promotion still needs its full proof, or a delta carry of it ([ADR-0156](0156-a-full-proof-carries-across-a-covered-delta.md)). The hold compares the record's sha with the dev tip: a record whose sha is another commit is red for a base the tip no longer is, so the tip is unproven and not red, and the note names both shas. A missing or unreadable record never holds, and neither does a record whose sha the checkout does not carry: the loop is never blocked on a file it cannot trust.
7. **It holds while a run is live or the gate lock is held.** A full suite beside live runs and their gates saturates the box and bounds every gate; a day with no idle window has no nightly, and the next idle cycle runs it. A run is live by the one reader the drain and the hub use (`squad/run_liveness.py`): a run with no verdict whose engine pid still runs, or, before its first call, whose record moved within the launch window. Only an `idle` answer lets the suite start; a reader that cannot answer holds it. The gate lock holds while its pid lives. A held start writes no day marker.

## Consequences

A defect that only the heavy or serial mode shows now surfaces within a day, whatever the promotion to `main` waits for, and the hub names the tests that failed.

A full suite no longer runs beside a busy loop, so a child's gate keeps its bound. The cost: on a loop that is busy all day, the nightly waits for an idle window and a day can pass with no nightly.

A tip the nightly has not seen is delivered on, unproven, until the next nightly proves it. The risk is bounded to one nightly: a defect that only a heavy call shows can land before the nightly names it, and the red record then holds the next tip.

The nightly spends the machine's time on the order of a full suite once a day. It never adds a gate, so no entry and no landing becomes slower.

## Guard

`contrib/squad-goal/scripts/test-nightly.sh` guards rules 1 to 4, 6 and 7 (both modes in the nightly tree, the record, the alert, the lock names, the day marker, the dispatch hold and the holds for live runs and the gate lock). `contrib/squad-goal/scripts/test-watch-cycle.sh` (case 12) guards rule 1 (the cycle starts the suite detached and does not wait for it). `tests/test_hub_nightly_fact.py` guards rule 5 (the fact, the modes, the sha and the failing tests).
