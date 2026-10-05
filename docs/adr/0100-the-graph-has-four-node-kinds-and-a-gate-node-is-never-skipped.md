# ADR-0100 — The graph has four node kinds, and a gate node is never skipped

## Status

Accepted, 2026-09-29. Panel decision under autonomy (issue #7): AGREED on the node kinds and the gate rule; AGREED 2–1 on the deadline term, the proposal kind term, and the verdict values.

Amended 2026-09-29 by [ADR-0101](0101-two-more-node-kinds-each-and-action-a-backstop-is-a-fact-and-a-when-holds-one-predicate.md): the kinds are six (`each` and `action` join). In rule 3, the backstop (T5) is a code-derived fact that the critic's `when` reads, not a gate node. A `when` on a loop may skip the loop's gate children.


Amended 2026-09-30 by [ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md): the kinds are seven (`dispatch` joins). A graph with no `dispatch` node is unchanged.

Amended 2026-10-01 (ticket #91): a failed `ac-verify` command keeps the last 1500 characters of its output as evidence. The gate outcome in the run record carries it in an `evidence` field, keyed by reason. The gate artifact does not, so no agent prompt reads it. The dispatch ledger does not carry it either (ADR-0113 rule 3). The failed command named the test file; the evidence names the assert.

Amended 2026-10-02 (ticket #127): the engine is one scheduler with one node runner per kind (`squad/runners`, `NodeRunner.run(ctx, node, entry, sc) -> Res`). A new kind adds one runner to the registry. The kinds and rules here are unchanged.

## Context

dev_squad's pipeline is code. It skips the critic on a trivial tier (T1) and then re-enters it from a backstop (T5). The map (issue #2) rules that gate thresholds never adapt, and the editor must render each node kind as one card form. The vocabulary has to say which node a tier skip may remove, and which it may not.

## Decision

1. **Seven node kinds, closed: agent, gate, wait, loop, each, action, dispatch.** The validator refuses any other kind. A subagent is inside an agent node. It is never a node and never an edge.
2. **A gate is a node kind, not a block on an agent node and not an edge condition.** Its fields are read-only in the editor. Its thresholds never adapt (ADR-0012).
3. **A `when` predicate may sit on an agent, a wait, or a loop. It may never sit on a gate.** A tier skip removes the critic agent node. The scope and security checks that guarded it are gate nodes, and they still run. The dev_squad backstop (T5) is itself a gate node.
4. **Edges are name references on nodes.** A loop lists ordered children. A node maps each outcome to the next node's name. There are no edge objects.
5. **Budget is turns and spend. Deadline is a separate term** with its own outcome (ADR-0051). The loop maximum is a third term and may only shrink at run time.
6. **A run has a verdict with five values: completed, needs-human, conflict, failed, stopped** (ADR-0029, ADR-0112). **A node has an outcome: pass, fail, deadline, needs-human.** A deadline is a node outcome. It adds no exit code.

## Considered options

- Two kinds (agent, wait) with gate and loop as properties. Rejected: a gate that must run cannot be a subagent or a block the actor may skip (SDK note §4.4), and the editor needs one form per kind.
- Gate as an edge condition. Rejected: the editor cannot draw or edit edges, and a read-only block needs a node to sit on.
- Explicit edge objects. Rejected: the editor rewrites name references on rename and delete. Edge objects earn their place only when an edge carries its own settings, which none does.
- Deadline inside budget (Lens B). Rejected 2–1: the deadline has its own outcome, and mixing it with turns and spend hides that. Lens B's dissent: the editor sums one numeric block, and a separate term costs it a second.

## Consequences

GLOSSARY.md holds these terms. Ticket #8 decides the proposal kinds, #9 the fact set, #10 node execution, and #11 tests the vocabulary against the five phases as a fixture.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_fifth_kind_and_an_eighth_kind`
- `tests/test_graph.py::test_refuses_a_when_on_a_gate`
- `tests/test_spec_shape.py::test_a_spec_with_no_ac_commands_fails_the_gate_not_ac_verify_and_the_spec_runs_again`
- `tests/test_spec_shape.py::test_ac_commands_beside_spec_fails_with_a_finding_that_names_it`
- `tests/test_spec_shape.py::test_a_good_spec_still_completes_a_subject_run`
- `tests/test_gate_evidence.py::test_a_failed_acceptance_command_keeps_the_tail_of_its_output`
- `tests/test_gate_evidence.py::test_the_final_gate_outcome_records_the_evidence_and_the_artifact_stays_small`
- `tests/test_gate_evidence.py::test_a_passing_gate_outcome_has_no_evidence_field`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
