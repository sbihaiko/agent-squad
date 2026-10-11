# ADR-0100: The graph has seven node kinds, a gate is never skipped, and control is order-based

## Status

Accepted.

## Context

The pipeline is code in dev_squad. A tier skip removes the critic on a trivial change. The vocabulary must say which node a skip may remove and which it may not. The editor renders each node kind as one card form.

## Decision

1. **Seven node kinds, closed: agent, gate, wait, loop, each, action, dispatch.** The validator refuses any other kind. A subagent lives inside an agent node. It is never a node and never an edge.
   - **each** runs its ordered children once per item of a declared fact. Its `mode` is parallel or sequence. Its outcome is the join of the item outcomes.
   - **action** is deterministic code with declared side effects. It makes no model call and spends nothing. It may carry a `when`.
   - **dispatch** lets an agent request children (ADR-0113).
2. **A gate is a node kind, not a block on an agent node and not an edge condition.** Its fields are read-only in the editor. Its thresholds never adapt (ADR-0012). A gate stops or repeats. Its fail edge never routes into another node.
3. **A `when` may sit on an agent, wait, loop, each, or action node. It may never sit on a gate.** A tier skip removes the critic agent node. The scope and security gates still run. A `when` on a loop may skip the gates the loop holds, because a gate guards the product of its loop. A loop with a `when` must hold at least one agent, each, or action child.
4. **A `when` holds one predicate** over a declared fact. A compound condition becomes a named, code-derived fact. A backstop, such as `review_required`, is such a fact. The critic's `when` reads it. It is not a gate. `stop_after` is a run option that truncates the run. It is not a node predicate.
5. **Edges are name references.** A loop lists ordered children. There are no edge objects.
6. **Control is order-based.** The root is a loop. A loop runs its ordered children. A `next` takes six reserved words and nothing else: `repeat`, `continue`, `exit:pass`, `exit:fail`, `exit:deadline`, `exit:needs-human`. A `next` never names a node, on any kind.
7. **There are no sibling jumps.** To reach a later sibling, put a `when` on it over a declared fact, including `outcome:<node>`. A `when` is read on every entry and is never bypassed. No node has two entry semantics.
8. **Budget is stalls and spend. Deadline is a separate term** with its own outcome (ADR-0051). The loop maximum is a third term and may only shrink at run time. A stall is a failed attempt at one step (a tool and its command or target) whose failure reads the same as the last one for that step. A changed failure or a success restarts the count, so a call that keeps changing what fails never stalls. Total turns are never capped: spend and the deadline bound a call ([ADR-0134](0134-stall-cap-counts-failures-without-evolution.md)).
9. **A run has a verdict with five values: completed, needs-human, conflict, failed, stopped** (ADR-0029, ADR-0105). **A node has an outcome: pass, fail, deadline, needs-human.** A deadline is a node outcome. It adds no exit code.
10. **The evidence of a failed `ac-verify` command is the output that explains it, inside 1500 characters**, in the gate outcome in the run record. An output that fits the budget is kept whole. A longer one keeps the failing lines found in the part about to be dropped — the `FAILED`/`ERROR` summary lines, the `___ test ___` headers and the `E   ` assertion lines — then the end of the output, and names the size of the dropped part. A bare tail kept the last block a runner prints, which for `pytest -q` is the slowest-durations standings: the assertion that explains the failure was dropped. No agent prompt reads the evidence.
11. **The engine is one scheduler with one runner per node kind.** A new kind adds one runner.

Reopen rule 7 only if a target must run on a jump but not by order under the same facts, and no derived fact can express it.

Rejected: a fail edge from a gate into a node (a gate would not be a stop condition); explicit edge objects (the editor rewrites name references on rename and delete); a jump that bypasses `when`.

## Guard

`tests/test_graph.py`, `tests/test_engine_skeleton.py`, `tests/test_each.py`, `tests/test_actions.py`, `tests/test_spec_shape.py`, `tests/test_gate_evidence.py`, `tests/test_workcmd_evidence_assertion.py`. A change to this rule changes its tests in the same commit (ADR-0018).
