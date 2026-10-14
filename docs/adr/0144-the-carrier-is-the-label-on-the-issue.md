# ADR-0144: This repository's card state is carried by a label on the issue

## Status

Proposed.

## Context

[ADR-0141](0141-the-board-is-a-shipped-definition.md) says the definition names the vocabulary and
not the storage: a repository binds the names to a carrier, and one seam reads it. This repository
binds the state of a card to the Status field of a GitHub Project. A label on the issue is the other
carrier that the vocabulary can use. This ADR picks one for this repository and gives the reason.

The two carriers differ on what it costs to prove a write.

**The Project field.**

- It gives a native view, and an item id cache: the issue to item id map is cached ten minutes and is
  read again on a miss, and the field and option ids are cached a day (`BoardBinding`).
- Its reads are eventually consistent. A card written a moment ago is not always in the next read, so
  a miss re-reads the items with a bounded backoff (`item_id`).
- A write can be answered and not applied, and a write the carrier refuses can still be a state the
  card carries. Neither the answer nor the refusal is proof (`NotCarried`).
- The proof of one write is a read of the whole board. `State.read` is `read_all` for one issue, one
  paginated read of every item, filtered to the repository.
- The write is a retry loop. `board.py` writes again, read back each time, up to a bound with a
  doubling delay, and a card that no read carries is named as a read failure and is never cached as
  written.
- A card must be on the board before it has a state. A card that the board answers "already exists"
  for and no read carries is a `ReadFailure`.

**The label on the issue.**

- The proof of one write is the read of that one issue. The reconciler already makes this read for
  each card (`repos/{REPO}/issues/{n}`), and the issue answers its labels in the same reply.
- A label family is closed: its names are the columns of the vocabulary, so a reader can tell a
  label of the family from any other label. The reconciler is the one writer of the family, as
  [ADR-0141](0141-the-board-is-a-shipped-definition.md) clause 4 asks of a field.
- A label keeps no history. The card is its own watermark, and the reconciler keeps no ledger
  beside it (`reconcile.py`). A label gives it the same watermark and adds none.
- The labels are already in use. The reconciler tags a reverted card with a label, the freeze markers
  of the shipped vocabulary are labels (`FREEZE_LABEL`), and the hub reads the labels of every card
  and filters its board by them.
- A card on no board still has labels, so a label needs no add step. The hub today lists the open issues
  that never reached the board as cards with no state.

## Decision

1. **The state of a card is carried by one label of a closed family on the issue.** One label per
   column of the shipped vocabulary, with a shared prefix that no other label uses. An issue carries
   at most one label of the family. A reader that finds two names the fault and never chooses one.
2. **The reconciler is the one writer of the family.** It derives the column from the facts of the
   issue, as it does today, and it keeps the rule that a card only moves forward
   ([ADR-0143](0143-a-card-only-moves-forward.md)): a move is the removal of the old label and the
   addition of the new one, and the label carried is the watermark.
3. **A write is proven by the issue read that follows it.** The proof is that the issue carries
   exactly one label of the family and that it is the label written. A write that the read does not
   carry is `NotCarried`, named, and never reported as written. The seam keeps this rule for every
   carrier.
4. **The seam stays.** Callers ask `board_state`, and only the binding learns the carrier. A binding
   to the label is a second binding of the same interface, so a name in the vocabulary never changes
   for a caller.
5. **The label carries state only.** The holder of an issue (the Run fields), and the test case
   items, have no issue label to live on. They stay on the Project field, and a later decision moves
   them or keeps them there. The Project stays a carrier for them until then.
6. **The move is made in four steps, and each step rolls back to the step before it:**
   1. **Mirror.** The reconciler writes the label beside the Project Status, and every reader still
      reads the Project. *Rollback:* stop writing the label and remove the family. No reader used it.
   2. **Move the readers.** The seam reads the state from the label. The reconciler still writes
      both. The binding name is recorded at the start of a run, and a resume that finds a different
      name refuses, so no run is in flight when this step lands. *Rollback:* point the seam at the
      Project again. The mirror kept the Project current.
   3. **Stop the old writer.** Nothing writes the Status field. *Rollback:* start the writer again.
      The reconciler derives each column from facts, and a card only moves forward, so the Project
      catches up to the label and not past it.
   4. **Delete the old path.** The Status write, read and cache leave the Project binding. The
      Status options leave the board only after no card carries them
      ([ADR-0143](0143-a-card-only-moves-forward.md) clause 5). *Rollback:* revert the change. The
      options of the board are not touched by this step.

## Consequences

- A write costs one read of one issue to prove, and not one read of the whole board. The wait for a
  read that is behind a write leaves the state path.
- The native Project view stops showing the state at step 3, because no one writes the field. The hub
  draws the board from the same reads, so the owner's view of the board is the hub.
- A move is two calls, a remove and an add, so a read between them can find no label of the family.
  The reader treats this as no state, and the reconciler writes the label on its next pass.
- The label has no history, so a question about when a card moved is answered from the issue events
  and not from the label.
- The Project stays in use for the holder and the test case items, so the item id cache and its
  eventually consistent reads stay for them. The old path that step 4 deletes is the state path only.
- The choice is for the owner. This ADR is Proposed until the owner ratifies it, and the migration
  tickets stay blocked until then.

## Guard

None yet; a draft.
