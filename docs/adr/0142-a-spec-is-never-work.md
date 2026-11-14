# ADR-0142: A spec is never a unit of work

## Status

Accepted. Rewritten in place on the owner decision, verbatim: "ADR-0142's decision that a dispatcher takes an issue
only when its card sits in Todo and it carries the go label becomes the column plus no holder label."

## Context

A spec is an issue, and an issue is what the dispatch pass reads. Nothing in the pass told the two
apart: it took every open issue its blocker gate called startable and made it a child of a run. So
the pass dispatched specs the owner had not started, and a run that implements a spec commits a
decision he has not made. The tickets of a spec are work; the spec is the document that argues for
them, and its owner alone decides when it becomes tickets.

The same holds for an issue that waits on the owner: one asking for a decision, one waiting on an
answer, one closed as not-work. Each is startable by its labels and none is a unit of work.

## Decision

1. **A spec is a document, never a unit of work.** The dispatch pass never makes one a child of a
   run. The owner starts a spec by breaking it into tickets; until he does, it waits.
2. **The pass drops the labels a profile names as not-work.** `SG_FRONTIER_SKIP_LABELS` carries
   them (`spec`, `needs-owner`, `needs-ratification`, `wontfix`, `needs-info`), and the walk reads
   the list rather than a clause in the pass, so a repository changes its own vocabulary in one
   place. The bug label stays its own: `autobug` owns it.
3. **The label is the whole signal.** An issue that carries one is invisible to the pass, with no
   message and no marker. The pass does not ask the owner, does not guess a start, and does not
   comment on the spec.
4. **A child of a spec carries the spec's number in its title**, `[SPEC <n>] <title>`. The prefix is
   a reading aid for a person; the sub-issue link is the fact, and it is the relation the tracker
   carries.
5. **The column Todo is the owner's go signal.** A dispatcher (`autofrontier`, `autobug`) takes an issue when its card
   sits in Todo and the issue carries no holder label (`ready-for-human`, `ready-for-HAL9K`) and no hold. An absent
   triage label authorizes: the pass writes `ready-for-agent` on the issue in the same act, as the record that the loop
   took it. The owner starts an issue by moving its card to Todo. A card with no column is not in Todo. A spec and its
   tickets are created with their card in Backlog; a bug is created in Todo. The loop never moves a card into Backlog or
   Todo on its own; only the owner's move starts that. A spec's card stays in Todo while any of its tickets is in flight,
   and it never follows a ticket into Doing: the spec is a document, and its lane reports how many tickets sit in each
   column. The spec closes when all its tickets close. The column carries no blocking edge — a ticket whose blocker is
   still open waits on the dispatch's reading of that edge, not on a column of its own.
6. **The owner starts a spec by moving its card to Todo, and its tickets follow.** When a spec's card
   sits in Todo, each open ticket of it whose card is in Backlog, or has no column, moves to Todo.
   The move is forward only (ADR-0143): a ticket past Backlog keeps its column. The spec's column is
   still a fold over its tickets: it reads Todo while any ticket is in flight, and the fold writes no
   other column below Doing, so the two never fight. A run, an owner label or a closure outranks the
   follow.
7. **A spec whose tickets are all closed is done, and the loop closes it.** The proof is the
   tracker's count: at least one sub-issue, and every one closed. A spec with no sub-issue proves
   nothing and stays open. The close carries a comment that names the count, and the card follows
   the issue to Done.
8. **A ticket needs no triage label to be taken, and a spec never carries one.** The dispatch takes a ticket from its
   column and writes `ready-for-agent` in Todo. The label waits for no blocker; the repository's `stack.sh` holds a
   blocked ticket. The spec needs no label: its card is the signal.
9. **Only the owner approves a spec's breakdown.** The autonomy panel never approves one unless the
   owner asked for it in the session. With the owner away, the breakdown waits.

## Consequences

- The dispatch never commits a decision the owner has not made, and a spec waits for him however
  many of its labels look startable.
- A run no longer spends on a spec: the model calls, the branch and the pull request a dispatched
  spec produced are all work the owner did not ask for.
- A repository that wants a different vocabulary — a different label for "not work yet" — adds it
  to one list. The pass needs no change.
- The tickets of a spec dispatch once the owner moves the spec's card, or their own cards, to Todo,
  and a spec is not a blocker: the pass skips it and keeps walking.

## Guard

`contrib/squad-goal/scripts/test-autofrontier-one-run.sh` proves the case the decision names: one
spec, one issue that waits on the owner and one plain ticket in the same walk, and the ticket alone
becomes the child of the run. `tests/test_vendor_skills.py` guards the title prefix of a spec's
child in the skill that cuts the tickets. `contrib/squad-goal/scripts/test-janitor-board-reconcile.sh`
proves the column: a spec with no card is added in Backlog, and a child of it with an open
blocker is added in Backlog, each in one write; a spec whose every sub-issue is closed is closed,
and one with an open sub-issue or none stays open; a spec in Todo moves its Backlog tickets to Todo,
and a spec in Backlog moves none. The autofrontier and autobug suites prove the
signal: a card in Todo whose issue carries no holder is dispatched, and an issue that carries a holder, or whose card
is not in Todo, is not.
