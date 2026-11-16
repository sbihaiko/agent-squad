# ADR-0141: The board is a shipped definition, applied by one command

## Status

Accepted.

## Context

The loop reads a board, and the board's vocabulary — the columns a card passes through, the kinds
an item carries, the markers that hold a card, the ways a terminal item ends — was decided in
prose. It lived in the private repository, in the text of the specifications that argued for it,
and nowhere a repository could take it from.

The cost of that is not hypothetical. The owner's own board carries a column no rule defines: it
was written by hand into the field, and the page that draws the board did not know the column
existed. A board whose vocabulary is a person's memory is a board that drifts, and the drift is
invisible until a reader sees a card in a column that reads wrong.

A repository that has agent-squad installed wants the same board. It cannot read the private
repository the loop ships from, and it should not have to rebuild the vocabulary from prose.

## Decision

1. **The board definition ships as one artifact, in its own public repository.** It carries the
   vocabulary as data — the columns with the fact that sets each one and who acts next, the kinds,
   the two freeze markers, the outcomes, the views — and the command that applies it. It is
   published under MIT so a repository that runs the loop can take it without access to the
   private one.
2. **One command applies it, and the command is idempotent.** The same run against an applied
   board changes nothing, and a status option keeps the identifier it had, so no card loses the
   value it carries. A run can be asked what it would change and change nothing.
3. **The applier refuses to empty a column.** A status option that cards still carry is refused,
   with the counts named, unless the caller forces it by name after the cards have moved. A
   vocabulary change is never allowed to be silent.
4. **The definition names the vocabulary; it does not name the storage.** A repository binds the
   names to the carrier it works in, and one seam reads the carrier, so no reader learns which one
   is in use. Where a repository chooses a field of a board as the carrier, that field is written
   by one reconciler from the item's own state and is never a second source of the same fact.
5. **A freeze is a marker, never a column**, and the two reasons a card can be held stay apart:
   the owner is the next actor, or another item gates this one. Collapsing them loses the one
   signal that says who the bottleneck is.
6. **The published repository is the one place the vocabulary is written for others.** A
   specification in the private repository decides what the loop does and points at the definition;
   it does not restate the columns, and the definitions are not kept in two places that can
   disagree.
7. **No repository-specific value ships in the definition.** No secret, no credential, no machine
   configuration, no name of a repository or a person. The definition is data a stranger can apply
   unchanged.
8. **The definition declares its version, and each column states its fact in one closed grammar: a
   fact name, `all`, `any` and `not`.** One rule evaluates the expression, so the hub read and the
   GitHub Project writer reach a column the same way. A reader that meets a version or an expression
   it does not know keeps the card's last written column and names the unknown; it never guesses a
   column and never fails silently. A file with no version reads as the current version.

## Consequences

- A repository adopting the loop applies one command and has the board the loop's own repository
  reads, instead of a board assembled by hand from prose.
- The applier is the only writer of the board's options, so a difference between a board and its
  definition is reported as what a run would change, not discovered later by a reader.
- The vocabulary has one home, and a specification that needs a change to it changes the
  definition. Two homes is how the same fact comes to be two facts.
- A copy of the definition that a person edits by hand is a board the next apply undoes. That is
  the intended behaviour: the definition is the source and the board is its instance.
- The applier runs outside this repository, so this repository's suite cannot guard it. Until a
  guard exists in the published repository and names itself here, this decision is a draft.

## Guard

`tests/test_board_definition_grammar.py` proves the grammar, the version and the unknown cases;
`tests/test_board_pin.py` proves the pinned copy. The applier runs outside this repository, so the
apply side stays a draft until the published repository guards it.
