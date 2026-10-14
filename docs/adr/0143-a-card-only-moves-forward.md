# ADR-0143: A card only moves forward, and the old options go after the cards

## Status

Accepted.

## Context

The shipped vocabulary ([ADR-0141](0141-the-board-is-a-shipped-definition.md)) replaces four options
the board carries today: Blocked, In Progress, Done and Open Specs. A board has cards on them. An
option removed under a card loses the value that card carries, and a card moved by guess lands in a
column its work never reached.

## Decision

1. **A card's column only moves forward along the axis.** The furthest the work reached sets the
   column, and a freeze never moves it. A card in Merging whose pull request turns red keeps its
   column and carries a badge; it never moves back.
2. **Two columns are terminal: Closed and Abandoned.** A card that reaches one stays. A closed card
   that still carries an old option lands in Closed, whatever its option said.
3. **A migration plan names every move before any write.** It is a dry run: each card on an old
   option, the old option, the new column and the labels to add, then each option's removal. The
   planner has no write mode; the applier of the definition writes the board.
4. **Each old option has one target, derived from the board vocabulary and never a second table.**
   In Progress becomes Doing and Done becomes Closed. Open Specs becomes Backlog with the spec
   label. Blocked is no column: the card lands in the column its work reached, Backlog when the
   facts name none, with the owner freeze marker over it.
5. **An option is removed only when no card carries it.** Removing one while a card carries it is
   refused, and the refusal names the count per option. A plan lists a removal only for an option
   its moves empty.
6. **A card on a shipped column is never moved by the plan.** Only a card on an old option moves.

## Consequences

- The live board is migrated in two steps a person can read: the plan, then the apply. The plan is
  safe to run at any time because it writes nothing.
- A card never loses its place on the axis, and a terminal card is never reopened by a migration.
- Removing the old options also needs every writer of them to stop writing them; the plan cannot
  see a writer, so that stays a step of the person who applies it.

## Guard

`contrib/squad-goal/scripts/test-board-migrate.sh` and `tests/test_board_migrate.py`.
