# ADR-0109 — An adaptation is judged by a read-only report for a person, never by a fact, a gate, or a revert

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #20). AGREED on all five sub-questions. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. No split, so no third lens.

## Context

ADR-0012 says a measurement that becomes a target stops measuring. ADR-0104 says a proxy never triggers a proposal. ADR-0103 lets a model propose settings and puts the applied change in the run record. Someone must be able to read whether a change helped. That reading must not feed the next proposal.

## Decision

1. **The record gets no new event.** The `applied` proposal event holds the proposal id, target, old and new value, and cited facts (ADR-0103 rule 7). The `outcome` event holds the node, entry number, outcome, reasons, artifact, spend, turns, and effective cap (ADR-0105, ADR-0108). The `outcome` event also carries `elapsed` as a diagnostic field, the wall-clock time of the entry. A separate `effect` event would be derived truth written into an append-only record.
2. **A read-only adaptation report joins the events.** It joins them by proposal id and target node name, and pairs the target's next entry with its previous entry by entry number. A person reads it through the editor or a file. A code path never reads it.
3. **The report is never a fact.** It is not a registered fact, not on a `sees` list, not a `when` input, and not a gate input. A proposer never sees whether an earlier adaptation helped. A proposer still sees the raw facts its node declares (ADR-0104 rule 3).
4. **The report shows deltas and no verdict.** For each pair it shows the change in outcome, spend, turns, and elapsed time, with the instrument named. It carries no helped or hurt label and no threshold. It states that it is one observation and not a measurement (ADR-0012). It names the entry numbers and the reasons, because each entry is a fresh session that reads its findings from an artifact, so the second entry is a different task from the first. A missing prior entry, or a missing spend, is shown as unavailable. It never pairs against another node.
5. **Cross-run comparison is out of scope in round one.** It needs repeated runs and a measured spread. It returns only under the reopen condition of ADR-0110.
6. **Nothing reverts by itself.** A revert is a second author path that the record cannot name. A later proposal may set a `set_model` or `set_effort` value back inside its declared list, and it is validated like any proposal. A shrunk cap does not regrow by proposal. A person edits the graph for a wider bound (ADR-0010, ADR-0103 rule 4). If a shrink starves a node, the author's answer is a `next.fail` route, such as a wait, not an engine revert.

## Considered options

- **An `effect` event that code writes after the target's next exit.** Rejected by both lenses: it duplicates fields and hand-writes derived truth into the record (ADR-0019 rule 8, ADR-0008 rule 6). Reopen if a join cannot pair the entries from the record alone. Then write one `effect` event with ids and record positions and no values.
- **A proposer sees the effect through `sees`.** Rejected: a verdict-carrying observation is refused (ADR-0008 rule 2), and a proposer that sees it optimizes against it (ADR-0012). Reopen if a proposer needs a before-and-after pair that its raw facts cannot express. Then register a code-derived delta fact that is `sees`-only and never a trigger.
- **A helped or hurt label by a fixed rule, such as the same outcome at lower spend.** Rejected: the report would become a target, and a threshold on a metric is a claim about the instrument. Reopen if repeated-run spread data shows a delta size at which a label is defensible. It would still be a report label and never a fact.
- **No report in round one.** Rejected: the report is cheap and read-only, and it is the only way a person reads the effect (ADR-0003: someone must read the ledger).
- **Compare the same adaptation across runs of one graph hash.** Rejected: the same hash is necessary and not sufficient, because the target and the request differ per run. dev_squad's promotion on a count of two, with no instrument, is the pattern to avoid.
- **Code reverts an adaptation when the next entry fails.** Rejected: one entry is not a measurement, and a revert against an outcome is a loop against a gate result (ADR-0012 rules 2 and 5).

## Consequences

GLOSSARY.md gains `Adaptation report`. The `outcome` event gains `elapsed`. The map's fog line on judging an adaptation is closed. The editor may show the report, and ticket #22 decides how.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_adaptation_report_is_never_a_registered_source`
- `tests/test_graph.py::test_refuses_a_fact_sourced_from_the_adaptation_report_or_another_run`
- `tests/test_report.py::test_the_report_joins_an_applied_proposal_to_the_previous_and_next_entry_with_deltas`
- `tests/test_report.py::test_the_report_carries_no_label_no_threshold_and_names_its_instrument`
- `tests/test_report.py::test_a_missing_entry_or_a_missing_spend_shows_unavailable_never_zero`
- `tests/test_report.py::test_no_engine_module_imports_the_report`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
