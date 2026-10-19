# ADR-0148: Bug first

## Status

Accepted.

## Context

A known defect that waits behind feature work keeps its cost. Later work builds on the broken path, and each new run can meet the same defect again. A feature run that lands while a startable bug waits adds risk to a base that is already wrong.

The loop has two dispatch passes. `autobug.sh` dispatches open `bug` issues. `autofrontier.sh` dispatches the feature frontier. Both pull from the same run slots and the same spend caps.

## Decision

1. **Zero bugs is the policy.** A bug is filed at once, as an issue with the `bug` label, and its card goes to Todo at once. `reconcile.py` adds an open `bug` issue that is not on the board, in Todo, or in Code Complete when a work branch names it.
2. **A bug is dispatched before a feature.** Each fast-lane beat (`fastloop.sh`) and each slow cycle (`watch.sh`) runs `autobug.sh` first, and `autofrontier.sh` only after it ends.
3. **A waiting bug holds the frontier.** `autobug.sh` writes the count of startable bugs it left with no run to `$SG_STATE/bugs-waiting`. While that count is over 0 and fresh (30 min), `autofrontier.sh` dispatches nothing (`gh_bug_first` in `ghcall.sh`). The pass prints `BUG FIRST: <n> startable bug(s) wait for a run; no feature run this pass` and writes it to `cycle.log`.
4. **A missing, unreadable or stale count holds nothing.** A dead `autobug.sh` must not stop feature work forever.
5. **`debt` comes after bugs.** `autobug.sh` reads `debt` issues only when no bug is startable. `autofrontier.sh` skips `bug` and `debt` issues.
6. **A skip label still skips.** A bug with `needs-owner`, `no-auto`, `wontfix` or `question` is not startable, so it does not hold the frontier. A `loop-bug` halts both passes (ADR-0146).

## Consequences

- Feature work waits while a startable bug has no run. That wait is the cost of a clean base.
- A bug that cannot get a run, for spend or slots, holds the frontier while `autobug.sh` keeps writing a count over 0. The hold ends when the bugs get runs, or 30 min after the last count.
- `SG_AUTO_BUGFIX=no` turns `autobug.sh` off. Then no count is written, and the frontier is not held.

## Guard

`contrib/squad-goal/scripts/test-autobug.sh` (a pass writes the startable bugs it leaves with no run to `bugs-waiting`) and `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (a waiting bug halts the frontier pass, names the count and writes `cycle.log`; a zero, missing or stale count holds nothing). The order of the two passes in `fastloop.sh` and `watch.sh`, and the Todo card for a new bug, have no guard yet; that part is a draft.
