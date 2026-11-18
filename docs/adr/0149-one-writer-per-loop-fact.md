# ADR-0149: One writer per loop fact

## Status

Accepted.

## Context

The loop keeps the facts of an issue in many places: run folders, marker files, counters in janitor, childcc, `wave.sh` and the engine. Each reader derives the state again, and two readers can derive different states for one issue. A requeue cause, a review streak or a freeze can then be lost, counted twice, or read from a run that a rearm already cleared.

## Decision

1. **The per-issue ledger (`issue_ledger.py`) is the single source of an issue's loop facts.** The facts are: dispatch attempts, outcome, requeue cause, review streak and freeze.
2. **Every writer submits an event through one transition function.** Janitor, childcc, `wave.sh` and the engine write no fact any other way. No component derives an issue's state from run folders or markers.
3. **An event has a defined identity and order.** A duplicate event changes nothing. An event out of order is handled by the rule the transition function defines, never by the writer.
4. **A missing or corrupt ledger holds dispatch for that issue.** It never falls back to legacy counters. The hold names the issue and the cause.
   - A ledger is missing when its file is not there and a legacy counter of the issue is (`requeues-<n>`, `loopqueues-<n>` or their `.runs`). The issue has loop facts the ledger does not hold, so it is held. The owner's `migrate` writes the counters into the file once, or `rearm` clears them.
   - An issue with no file and no legacy counter is new. It has no loop fact yet, so it reads as todo and dispatches.
5. **A rearm is a floor.** A run before the rearm never counts toward any fact after it.
6. **Markers migrate in order.** First the meaning of a marker becomes a ledger fact. A marker prefix is deleted only after every reader and writer moved to the ledger and a replay of the ledger gives the same states.

## Consequences

- Janitor rules read the ledger, not run folders or markers.
- Markers shrink to process locks.
- A defect in the ledger holds one issue, not the loop. A wrong fallback cannot hide it.
- During the migration, a marker and its ledger fact live side by side. That cost ends when the replay check passes and the prefix is deleted.

## Guard

`tests/test_issue_ledger.py` and `contrib/squad-goal/scripts/test-issue-ledger.sh` (one transition function, event identity and duplicates, a corrupt or missing ledger holds, a new issue reads todo, a rearm is a floor, the entered, tries, owed, landed, requeue and review facts); `contrib/squad-goal/scripts/test-wave-entry.sh` and `test-wave-land.sh` (`wave.sh` writes its wave events, landing tries, owed closes and the landing fact as ledger facts and no marker); `contrib/squad-goal/scripts/test-branchland.sh` (a branch landing is a `landed` fact); `contrib/squad-goal/scripts/test-childcc.sh` and `test-runreview.sh` (childcc and runreview record and read the `requeue` fact, never the old marker; runreview records the review streak and freezes the issue on it); `contrib/squad-goal/scripts/test-janitor-rearm-floor.sh` and `test-dispatch-hold-wave.sh` (the janitor and the dispatchers read the entry from the ledger); `tests/test_janitor_card_done_base.py` (rule R reads the `landed` fact, never a run's land heads). The engine's own writes do not go through the transition function yet; that part is a draft.
