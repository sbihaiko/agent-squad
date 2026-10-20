# ADR-0143: A card only moves forward, and the old options go after the cards

## Status

Accepted. Amended by the owner decision ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md)), verbatim: "three long-lived branches (`dev`, `stg`, `main`); a work branch lands in `dev` by a fast-forward push, with no pull request; the loop promotes `dev` to `stg` when the CI of the `dev` tip is green; the owner promotes `stg` to `main` with one command; the live copy publishes from `stg`; a card is Done when its branch is in `dev`."

## Context

The shipped vocabulary ([ADR-0141](0141-the-board-is-a-shipped-definition.md)) replaces four options
the board carries today: Blocked, In Progress, Done and Open Specs. A board has cards on them. An
option removed under a card loses the value that card carries, and a card moved by guess lands in a
column its work never reached.

## Decision

1. **A card's column only moves forward along the axis.** The furthest the work reached sets the
   column, and a freeze never moves it. A card whose landing fails keeps
   its column and carries a badge; it never moves back. A card is Done when its branch is in `dev`
   ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md)).
   The loop never moves a card into Backlog or Todo on its own; only the owner does. When the owner
   moves a spec's card to Todo, its tickets in Backlog follow it there: the owner's move, not the
   loop's ([ADR-0142](0142-a-spec-is-never-work.md)). An open issue with no card
   gets a column once, when the loop first sees it: Todo for a bug, Backlog for anything else. A card
   that has a column keeps it until work moves it forward.
   **The one backward move:** a run that ends with nothing delivered returns its card from Doing to
   Todo. The dispatch moved the card out of Todo, so the loop undoes its own claim.
   A spec card is derived, not delivered: the fold over its sub-issues is its only writer, no work
   rule names it, and the fold corrects it between Doing and Code Complete. Below Doing it keeps the
   column the owner gave it.
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
`contrib/squad-goal/scripts/test-janitor-floor-startable.sh` and `tests/test_spec_fold.py` guard the
floor and the fold. `contrib/squad-goal/scripts/test-janitor-board-reconcile.sh` guards a spec's
tickets that follow the owner's move of its card to Todo.
