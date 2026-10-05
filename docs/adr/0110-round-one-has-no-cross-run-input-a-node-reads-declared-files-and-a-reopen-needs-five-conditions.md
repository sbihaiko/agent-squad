# ADR-0110 — Round one has no cross-run input; a node reads declared files; a reopen needs five conditions

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #21). AGREED on all four sub-questions. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. No split, so no third lens.

## Context

dev_squad has cross-run learnings (L3) and guardrails (L4). A learning that recurred twice is promoted to blocking-in-review and can switch on the learning gate. Code injects the top eight advisory items per phase, adds intent priors from the last three runs, and queues correction and divergence learnings from human commits (`docs/research/dev-squad-adaptivity.md`, S12 to S16). Routing runs before the plan exists (D9), and the promotion count carries no instrument. ADR-0104 rule 4 refuses cross-run facts now. ADR-0109 rule 5 defers cross-run comparison to this ticket.

## Decision

1. **Round one has no cross-run input.** It has no cross-run fact, no promotion, no learning gate, and no code that reads a past run's record to change a later run. S12 to S16 are out of scope.
2. **A node may read a declared repo file.** An ADR, a guideline, or a learnings file that a person keeps is an input artifact that the node declares. Code never writes it. This is not a memory rule. It follows from ADR-0102 rule 1, which says context travels only through artifacts, and from ADR-0104 rule 7, which says a file at a target commit is a source. A store that a run writes for later code to read is not a source: no generator names itself (ADR-0008 rule 6).
3. **A run leaves its run record and its artifacts,** for a person and for a resume under the same run id (ADR-0107). No summary file exists to be promoted. A person may read the record and the adaptation report and edit a guideline file by hand (ADR-0010).
4. **A cross-run input reopens only when all five hold:**
   - a generator and instrument that name themselves (ADR-0008 rule 6);
   - a source tied to a commit (ADR-0104 rule 4);
   - a measured spread over repeated runs before any threshold or promotion counts (ADR-0012);
   - a person's recorded approval of each promotion: explicit, scoped, dated (ADR-0010 rule 4);
   - a way to show the effect on the adaptation report without a label (ADR-0109).
5. **A cross-run count never switches on a gate.** It may feed a prompt or a `when` after a reopen, and never a gate (ADR-0104 rule 5).
6. **The ruling lives in the map's Out of scope section,** and the fog line is removed.

## Considered options

- **Port L3 and L4 as graph data with a promotion rule.** Rejected: promotion on a count of two has no instrument and no spread (ADR-0012), and it turns on a gate from a count (ADR-0104 rule 5). The fixture already marks the source of `blocking_learnings` as a gap, and the gate passes vacuously.
- **Only a generator and a source as the reopen condition.** Rejected: it reopens S12's promotion loop with no spread and no approval.
- **A per-run summary file that a person may promote by hand.** Rejected: it is generated state with no generator contract, and it invites the S16 distillation path back. A person can already read the record. Reopen if ticket #22 finds the editor needs a per-run summary as its input. That would be an editor-contract file and not memory.
- **Keep the ruling in Not yet specified.** Rejected: the section is closed, and the design question is now answered.
- **A rule that limits an agent node's inputs to artifacts written in the same run.** Not adopted: a person-kept file is a legitimate declared input.

## Consequences

The map's fog line on cross-run memory becomes one line in Out of scope. ADR-0104 and ADR-0109 stop pointing at fog. Ticket #22 may add a per-run summary as an editor input.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_a_cross_run_fact_is_refused_at_load`
- `tests/test_graph.py::test_refuses_a_fact_sourced_from_the_adaptation_report_or_another_run`
- `tests/test_graph.py::test_refuses_a_node_that_reads_a_file_another_node_writes`
- `tests/test_graph.py::test_accepts_a_declared_read_that_no_node_writes_and_the_fixtures_still_load`
- `tests/test_graph.py::test_reads_and_writes_are_lists_of_nonempty_strings_and_change_the_hash`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
