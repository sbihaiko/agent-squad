# ADR-0158: A file batch is capped, so one large file never becomes one run

## Status

Accepted. Owner decision, 2026-10-31, after a wave of five tickets over one file died `out_of_budget`.

## Context

The frontier folds every startable ticket that names a common repository path into one child and one run
(`cluster.py`, ADR-0113's envelope). The rule exists so that two children never edit one file and conflict.

The rule has no size bound. Five tickets over one 4,570-line embedded UI became one run with one budget, and that run
ended `out_of_budget` without landing anything: the work fit the file, not the budget. The children that waited on those
tickets then waited for a run that could never finish.

The cap below keeps the conflict rule whole: a capped piece still holds every ticket of its own file slice, and no ticket
of that slice runs beside it.

## Decision

1. **A cluster is capped at two tickets by default.** `CL_MAX` names another cap.
2. **The cap splits, and drops nothing.** A cluster over the cap is cut in ascending ticket-number order into pieces of at
   most the cap; every ticket keeps a child of its own cluster.
3. **A cap that cannot be read, or reads zero or less, is the default.** A malformed setting never empties the frontier.
4. **The split keeps the conflict rule.** Every piece names the same files, so the pieces of one batch never run at once;
   the dispatcher's sibling and in-flight rules see to it.
5. **The cap bounds the run, never the ticket.** A single ticket over one file is one piece and one run, however large; the
   budget is what fails then, and the failure is the ticket's own.

## Consequences

A batch of N tickets over one large file costs ceil(N / cap) runs instead of one run that dies. The wave takes longer in
wall time and finishes.

A run that still dies `out_of_budget` with one ticket is a scope defect in that ticket, not in the packing.

## Guard

`contrib/squad-goal/scripts/test-cluster.sh` guards rules 1 to 3: the default cap splits a five-ticket batch into pieces
of two, a named cap of three splits it in threes, and a cap of zero falls back to the default. Rule 4 is the dispatcher's
own suite (`contrib/squad-goal/scripts/test-cluster.sh` through `autofrontier.sh`, and
`contrib/squad-goal/scripts/test-autofrontier-one-run.sh`). Rule 5 is a draft.
