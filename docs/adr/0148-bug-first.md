# ADR-0148: Bug first

## Status

Accepted.

## Context

A known defect that waits behind feature work keeps its cost. Later work builds on the broken path, and each new run can meet the same defect again. A feature run that lands while a startable bug waits adds risk to a base that is already wrong.

The loop has two dispatch passes. `autobug.sh` dispatches open `bug` issues. `autofrontier.sh` dispatches the feature frontier. Both pull from the same run slots and the same spend caps.

## Decision

1. **Zero bugs is the policy.** A bug is filed at once, as an issue with the `bug` label, and its card goes to Todo at once. `reconcile.py` adds an open `bug` issue that is not on the board, in Todo, or in Code Complete when a work branch names it. A bug is startable by design, so its card is Todo before its triage label is set, in the same filing; the Backlog-then-label order of the feature and spec publish does not apply to it. The Todo card limit holds the Backlog-to-Todo move only: a card that leaves Backlog for Todo waits while Todo is full, and a bug's card is added in Todo with no card before, so the limit never holds the bug filing.
2. **A bug is dispatched before a feature.** Each fast-lane beat (`fastloop.sh`) and each slow cycle (`watch.sh`) runs `autobug.sh` first, and `autofrontier.sh` only after it ends. Bug first never overrides a column bound: a full Doing column starts no bug, because the pass reads the column limit through `sg_board_limit_effective Doing` (#2423). A batch takes only the bugs whose cards fit the free cards, and each Doing card counts once, a hand-held one included.
3. **A waiting bug holds the frontier.** `autobug.sh` writes the count of startable bugs it left with no run to `$SG_STATE/bugs-waiting`. While that count is over 0 and fresh (30 min), `autofrontier.sh` dispatches nothing (`gh_bug_first` in `ghcall.sh`). The pass prints `BUG FIRST: <n> startable bug(s) wait for a run; no feature run this pass` and writes it to `cycle.log`.
4. **A missing, unreadable or stale count holds nothing.** A dead `autobug.sh` must not stop feature work forever.
5. **`debt` comes after bugs.** `autobug.sh` reads `debt` issues only when no bug is startable. `autofrontier.sh` skips `bug` and `debt` issues.
6. **A skip label still skips.** A bug with `needs-owner`, `no-auto`, `wontfix` or `question` is not startable, so it does not hold the frontier. A `loop-bug` halts both passes (ADR-0146).

## Consequences

- Feature work waits while a startable bug has no run. That wait is the cost of a clean base.
- A bug that cannot get a run, for spend or slots, holds the frontier while `autobug.sh` keeps writing a count over 0. The hold ends when the bugs get runs, or 30 min after the last count.
- `SG_AUTO_BUGFIX=no` turns `autobug.sh` off. Then no count is written, and the frontier is not held.

## Guard

`contrib/squad-goal/scripts/test-autobug.sh` (a pass writes the startable bugs it leaves with no run to `bugs-waiting`) and `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (a waiting bug halts the frontier pass, names the count and writes `cycle.log`; a zero, missing or stale count holds nothing). The bug's label set and its card's Todo column are pinned by test 21 of `contrib/squad-goal/scripts/test-autohunt.sh`. The Todo card limit's stop is pinned by `contrib/squad-goal/scripts/test-board-todo-limit.sh`. The order of the two passes in `fastloop.sh` and `watch.sh` has no guard yet; that part is a draft.
