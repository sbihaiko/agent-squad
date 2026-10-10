# ADR-0109: An adaptation is judged by a read-only report; round one has no cross-run input

## Status

Accepted

## Context

A measurement that becomes a target stops measuring (ADR-0012). A person must be able to read whether an applied setting change helped. That reading must not feed the next proposal. dev_squad also keeps cross-run learnings promoted by a count of two, with no instrument.

## Decision

### The adaptation report

1. **The record gets no new event.** The `applied` proposal event and the `outcome` event (which carries `elapsed`) hold what a report needs (ADR-0103, ADR-0105). A separate `effect` event would be derived truth in an append-only record.
2. **A read-only report joins the events** by proposal id and target node, and pairs the target's next entry with its previous entry. A person reads it through the editor or a file. No code path reads it.
3. **The report is never a fact.** It is not a registered fact, not on a `sees` list, and not a `when` or gate input. A proposer never sees whether an earlier adaptation helped.
4. **The report shows deltas and no verdict.** It shows the change in outcome, spend, turns, and elapsed time, names the instrument and the entry numbers, and says it is one observation. It carries no helped or hurt label and no threshold. A missing entry or spend shows as unavailable, never zero. It never pairs against another node.
5. **Nothing reverts by itself.** A later proposal may set a model or effort back inside its declared list. A shrunk cap does not regrow by proposal. A person edits the graph for a wider bound. If a shrink starves a node, the author routes `next.fail`, such as a wait.

### No cross-run input in round one

6. **Round one has no cross-run input:** no cross-run fact, no promotion, no learning gate, and no code that reads a past run's record to change a later run.
7. **A node may read a declared repo file** that a person keeps (an ADR, a guideline, a learnings file). Code never writes it. Context travels only through artifacts (ADR-0102). A store that a run writes for later code to read is not a source.
8. **A run leaves its record and artifacts** for a person and for a resume under the same run id (ADR-0107). No summary file exists to be promoted.
9. **A cross-run input reopens only when all five hold:**
   - a generator and instrument that name themselves;
   - a source tied to a commit;
   - a measured spread over repeated runs before any threshold or promotion counts;
   - a person's recorded, scoped approval of each promotion;
   - a way to show the effect on the report without a label.
10. **A cross-run count never switches on a gate.** After a reopen it may feed a prompt or a `when`.

Rejected: an `effect` event; a proposer that sees the effect; a label by a fixed rule; a revert by code on one failed entry; comparing runs of one graph hash (same hash, different target and request).

## Guard

`tests/test_graph.py`, `tests/test_report.py`. A change to this rule changes its tests in the same commit (ADR-0018).
