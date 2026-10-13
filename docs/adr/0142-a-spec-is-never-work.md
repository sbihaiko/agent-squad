# ADR-0142: A spec is never a unit of work

## Status

Accepted.

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
5. **A spec's card sits in the board's spec column, never in the column a startable card waits
   in.** A card's column says who acts next; for a spec that is its owner.

## Consequences

- The dispatch never commits a decision the owner has not made, and a spec waits for him however
  many of its labels look startable.
- A run no longer spends on a spec: the model calls, the branch and the pull request a dispatched
  spec produced are all work the owner did not ask for.
- A repository that wants a different vocabulary — a different label for "not work yet" — adds it
  to one list. The pass needs no change.
- The tickets of a spec dispatch as they always did, and a spec is not a blocker: the pass skips it
  and keeps walking.

## Guard

`contrib/squad-goal/scripts/test-autofrontier-one-run.sh` proves the case the decision names: one
spec, one issue that waits on the owner and one plain ticket in the same walk, and the ticket alone
becomes the child of the run. `tests/test_vendor_skills.py` guards the title prefix of a spec's
child in the skill that cuts the tickets.
