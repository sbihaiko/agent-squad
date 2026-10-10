# ADR-0106: A run option is graph data when it changes a node; otherwise it is an engine option recorded at run start

## Status

Accepted.

## Context

dev_squad had 17 config keys. Four changed the shape of the run: `stopAfter`, `parallelMode`, `autoCommit` and `fastPath`. `parallelMode` alone changed fan-out, swapped the merge node for commit and revert nodes, and changed the actor prompt. A flag that hides a topology change is a defect.

## Decision

1. **Sorting rule.** An option is graph data when it changes which nodes exist or a node's declared setting. It is then a field on a node, or a registered fact that a `when` reads. An option is an engine option when it only says how this invocation runs the same graph: where to stop, the run directory, a dry run, a lower run cap. The editor shows graph data on node cards and engine options in a run panel.
2. **`stop_after` is an engine option:** `--stop-after <node name>`. The validator refuses a name that is not a node of the graph. The engine truncates after that node exits. The `run_start` and `verdict` events name the stop node. The verdict is `stopped` (ADR-0105 rule 10).
3. **`parallelMode` is retired.** `each.mode` and `each.max_parallel` on the node are the graph data. A missing git is a preflight defect, never a swap. A graph that needs worktrees declares action nodes, and the preflight refuses a run without git.
4. **`autoCommit` is a node.** The author includes a commit `action` node or leaves it out. The other dev_squad keys follow the same rule: `strictBatch` is `each.join`, retry and revision caps are loop `max`, and models are the `model` of each agent node.
5. **`fastPath: off` is a registered fact.** Code produces `tier_pin` from the invocation argument. Its source is the position of the `run_start` event. The derived `tier` reads it. A model never sets it (ADR-0103).
6. **Run-level defaults are a `run` block in the graph document:** the run deadline default, the `max_parallel` default and the run spend cap. An engine option may override a `run` value only downward.
7. **Every run writes a `run_start` event** with the graph version hash, every engine option and every override. The verdict module reads it.
8. **An engine option never overrides a node's model or budget.** That is the graph author's edit or an applied proposal (ADR-0103).

Rejected: every option as an engine option or every option as graph data, because the editor card would lie either way. Rejected: `fastPath` as an engine override of `tier`, because it bypasses the registry.

## Guard

`tests/test_graph.py`, `tests/test_facts.py`, `tests/test_cli.py`, `tests/test_actions.py`, `tests/test_defaults_wiring.py`.
