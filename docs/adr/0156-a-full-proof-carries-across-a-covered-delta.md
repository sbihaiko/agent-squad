# ADR-0156: A full proof carries across a covered delta

## Status

Accepted. Owner decision: "a proof may be carried across a delta, for future use".

## Context

The promotion to stg needs a full proof of the dev tip: the full suite in both modes, run in the promotion gate ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md) rule 3). A full proof costs the time of a full suite on every tip, even when the tip differs from a proven one by a small change to one guard or one document.

The full proof stays the base of every promotion. A cheaper proof must never weaken it, so it is bounded: it covers only a delta the repository has declared, and only on top of a full pass that already exists.

## Decision

1. **A full pass is the base.** P is the sha of the full pass in `$SG_STATE/gate-status-full`. The dev tip is T. A carry applies only when P is an ancestor of T.
2. **The delta is declared, path by path.** The delta is `git diff --name-only P..T`. Every changed path must match a line of `contrib/squad-goal/scripts/delta-guards.txt` (a glob, a TAB, and a command id). A path with no line refuses the carry, and the answer is then the full proof.
3. **The promotion code is never covered.** The map has no line for `promote-*.sh`, `gate.sh`, `serial.sh` or `delta-pass.sh`. A change to any of them always needs the full proof.
4. **The bounds refuse.** A delta over 25 changed paths refuses, and so does a delta with a binary change.
5. **The delta proof runs on a clean tree at T.** It runs ruff, mypy, the fast battery `-m 'not heavy'`, and each distinct guard that the changed paths name. It runs under the lock `full-proof-delta`, in try mode: a held lock exits 73 and runs nothing.
6. **The delta record names what it proved.** `$SG_STATE/gate-status-delta` holds `<epoch> TAB delta TAB <pass|fail> TAB <P> TAB <T> TAB <sha256 of the changed-file list>`. The record is written by a temp file and a rename ([ADR-0030](0030-atomic-writes.md)). A refusal runs nothing and writes no pass record.
7. **The promotion checks the record again.** `promote-stg.sh` accepts a delta only when it says pass, its T is the dev tip, its P carries a full pass, P is an ancestor of the tip, and the changed-file list it proved hashes to the same set as P..T now.
8. **A delta expires with its tip.** A commit on top makes the record stale, and the promotion refuses it, as it refuses a stale full proof.
8a. **The promotion pass runs the delta first.** `promote-pass.sh` tries `delta-pass.sh` before its own full suite, and falls through
   to the full suite on every refusal and on a held `full-proof-delta` lock: the carry is an optimization on the promotion path,
   never a weaker gate, and no other step is allowed to skip the full suite on its own.
9. **Each P carries once.** A P that a delta has already carried refuses a second carry.
10. **The delta does not replace the heavy or serial coverage.** The nightly ([ADR-0153](0153-the-full-suite-runs-nightly-in-both-modes.md)) and the promotion proof remain the instruments of the heavy and serial coverage. A delta answers only for the delta it names.

## Consequences

A small change to a declared guard or document promotes without a full suite run. Any change outside the map, or to the promotion code, costs the same full proof as before.

A carried proof rests on the guards the map names. A defect that only a path outside those guards shows is found by the nightly or by the next full proof, not by the delta.

## Guard

`contrib/squad-goal/scripts/test-promote-delta.sh` guards rules 1 to 4 and 6 to 9. It proves that a covered delta promotes, and that each of these refuses: a path outside the map, a change to the promotion code, a P that is not an ancestor (at the delta and at the promotion), a commit after the record, a red delta, a second carry of one P, more than 25 changed paths, a binary change, a dirty delta tree, and a tampered changed-file hash at the promotion. It also proves that a held `full-proof-delta` lock exits 73 and writes no record (rule 5, the lock). The suite replaces the gate commands with `SG_DELTA_SUITE`, so the assembly of ruff, mypy, the fast battery and the guard commands is not run here. Rule 10 has no guard in this suite: the nightly and the promotion proof keep that coverage. `contrib/squad-goal/scripts/test-promote.sh` guards the full-proof path, which is unchanged.
