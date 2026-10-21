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
5. **A rearm is a floor.** A run before the rearm never counts toward any fact after it.
6. **Markers migrate in order.** First the meaning of a marker becomes a ledger fact. A marker prefix is deleted only after every reader and writer moved to the ledger and a replay of the ledger gives the same states.

## Consequences

- Janitor rules read the ledger, not run folders or markers.
- Markers shrink to process locks.
- A defect in the ledger holds one issue, not the loop. A wrong fallback cannot hide it.
- During the migration, a marker and its ledger fact live side by side. That cost ends when the replay check passes and the prefix is deleted.

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
