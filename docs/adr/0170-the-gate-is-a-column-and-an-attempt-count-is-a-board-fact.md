# ADR-0170: The gate is a column, and an attempt count is a board fact

## Status

Accepted.

## Context

A card whose child is in the wave branch holds Batching until its work is in dev. The gate sits between those two states:
a proof waits for a slot, the suite runs, and a red or unproven attempt sends the work back to a fix
round. The board had no column for that wait, so a card behind a queued proof and a card whose proof is
running read the same, and the owner could not see the queue.

The attempt is also a fact the board did not carry. Reading a red attempt as a backward move would
undo a column the work reached, and the definition's own rule is that the furthest column stands.

The old vocabulary carried Pushing for this stretch. The definition retired it, but the live board still
held the option, and an option removed under a card loses the value that card carries.

## Decision

1. **A gate is a column, not a badge over Batching.** The definition names Testing between Batching and
   Closed, with its own colour that no other status wears, the actor the gate, and the
   fact that a gate or proof waits for a slot or runs for the wave. Every surface takes the order from
   the definition: the lane order, the legend, the phone's served board and the cumulative flow bands.
2. **An attempt is a marker, not a move.** A red or unproven attempt keeps the card in Testing; the card
   leaves the column only forward, to Closed, and the definition's rules block says so. A fix round
   that starts new work moves the card backward only through the rules ADR-0143 already names.
3. **The attempt count is a board fact.** The definition names one number field, Gate Tries, and the
   applier creates it as a number in the same step as the column. A card with no recorded attempt
   reads as zero, so the count is one number per card and never a per-attempt ledger the board would
   have to grow.
4. **The definition, the pinned copy and the pin hash move in one step.** The hash refuses a pinned
   copy edited by hand, and `board_pin.py` reports a published definition that still lacks the column.
   A change to the definition that lands in one of the three alone is named as drift, never adopted.
5. **A retired option is removed only when no card carries it.** Pushing has no column to take its
   cards, so the migration never moves a card on it and refuses the removal by name while a card still
   carries it. The removal is never silent.

## Consequences

- The owner reads the gate queue as a column and the attempts as a number, with no second axis.
- A card never leaves a column because a proof was red: the proof is a marker on the card that waits.
- The applier creates a typed field, not only a select, so the definition carries a count beside its
  vocabulary without a second writer.
- A published definition that lags the pinned copy is reported by the pin command rather than adopted,
  and the published copy itself moves by the operator's publish, never by this repository's suite.
- Testing took the pink the ring had reserved for a user column, so the ring's third slot moved to amber
  (`--chart-series-4`). A user column that lands in that slot wears a different colour than it did
  before; this line names the recolour as a decision, and a guard refuses a ring colour that equals a
  named column colour, so a future column colour cannot collide with one silently.

## Guard

`tests/test_board_pin.py` holds the column's order, fact, actor and colour, the rules line, the number
field, and the one-step pin. `tests/test_board_columns.py` holds the lane order, the legend, the phone
board and the flow bands. `tests/test_board_define.py` and `contrib/squad-goal/scripts/test-board-define.sh`
hold the applier's number field. `tests/test_board_migrate.py` and
`contrib/squad-goal/scripts/test-board-migrate.sh` hold a retired option's refusal. `tests/test_board_facts.py`
holds a ticket in Testing as a ticket in flight, and the page's in-flight list as the facts' own list.
`tests/test_hub_board_blockers.py` holds the column colours and refuses a ring colour that a named column
also wears.
