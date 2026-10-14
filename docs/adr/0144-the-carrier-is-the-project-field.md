# ADR-0144: This repository's card state is carried by the Project Status field

## Status

Accepted (owner).

## Context

[ADR-0141](0141-the-board-is-a-shipped-definition.md) says the definition names the vocabulary and
not the storage: a repository binds the names to a carrier, and one seam reads it. Two carriers can
hold the state of a card here: the Status field of a GitHub Project, and a label on the issue.
They differ on what it costs to prove a write.

**The Project field.**

- It gives the native board view, and the field and option ids and the issue to item id map are
  cached (`BoardBinding`).
- Its reads are eventually consistent: a card written a moment ago is not always in the next read,
  so a miss re-reads with a bounded backoff.
- A write can be answered and not applied, so the proof of a write is the read that follows it
  (`NotCarried`), and that read covers the board, not one issue.
- A card must be on the board before it has a state.

**The label on the issue.**

- The proof of a write is the read of that one issue, which the reconciler already makes.
- A label needs no add step, and a label family can be closed to the columns of the vocabulary.
- A move is two calls, a remove and an add, so a read between them finds no state.
- The holder of an issue (the Run fields) and the test case items have no issue label to live on.
  They stay on the Project, so the label carrier means two carriers, not one.
- The move costs four staged steps (mirror, move the readers, stop the old writer, delete the old
  path), each with its own writes, while the write budget of the account is the scarce resource.

## Decision

1. **The state of a card stays on the Status field of the Project.** No label carries it.
2. **The seam stays the one door.** Callers ask `board_state`; only the binding knows the carrier,
   so a later change of carrier is a second binding of the same interface.
3. **The cost of proving a write is cut at the writer, not by a change of carrier.** A writer sends
   a board write only when the value differs from the value the card carries, so an unchanged card
   costs no write and no proving read.
4. **The holder and the test case items stay on the Project with the state.** One carrier holds
   everything a card shows.

## Consequences

- The native Project view keeps showing the state, beside the hub.
- The eventually consistent read and its bounded backoff stay on the state path.
- No migration runs, and no label family is created.
- A card on no board still has no state until it is added.

## Guard

None yet; a draft.
