# ADR-0102 — A node is one fresh session with a deadline, in a declared workspace; truth is the run record

## Status

Accepted, 2026-09-29. Panel decision under autonomy (issue #10). AGREED on fourteen sub-questions. AGREED 2–1 on which kinds carry a deadline and on the `each` join field. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the two splits only.


Amended 2026-09-30 by [ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md): a spawned child is one fresh session with the same rules as any agent node.

Amended 2026-09-30 (owner ruling, ticket #46): the run block's deadline may be null, meaning no run deadline. The per-call deadline is then the only cap. The owner's words outrank rule 3 (ADR-0000).

Amended 2026-10-02 (ticket #127): "one node" runs through the runner of its kind (`squad/runners`, `NodeRunner.run(ctx, node, entry, sc) -> Res`). The agent runner calls `squad/session.py` for the one fresh session and its deadline. The engine keeps one scheduler, not one executor. Rules 1 to 10 are unchanged.

## Context

ADR-0100 and ADR-0101 fix the vocabulary. This record fixes how the engine runs one node on `claude-agent-sdk` 0.2.161. The facts it rests on are in `docs/research/sdk-node-probes.md` and the per-node primitives note on branch `research/sdk-per-node-primitives`:

- A node is one `query()` call: one CLI subprocess, one session. The SDK has no wall-clock deadline. A probe showed that an `anyio.fail_after` scope around `query()` stops the CLI child, with about 5 s of teardown.
- `resume=<session_id>` continues a session after a turn or spend cap. Nothing documents a resume after a hang.
- The session store mirrors transcripts only, best-effort, and cannot combine with file checkpointing. File state needs git. A store resume must use a matching `cwd`.
- A background subagent yields a second `ResultMessage` whose `origin.kind` is `task-notification`.
- The SDK concurrency limit caps subagents inside one session, not concurrent `query()` subprocesses.

dev_squad ran fan-out with one knob for many effects (`parallelMode`), a silent downgrade to sequence when git was missing, a `strictBatch` run option, a prompt-only timeout, and twelve status strings with no exit code.

## Decision

1. **Every entry of an agent node starts a new session.** A `repeat` starts a fresh session too. Context between nodes travels only through artifacts that code reads and puts in the prompt. The session transcript is never truth (ADR-0019 rule 4). A node that spawns a background subagent keeps the result whose `origin` is empty as the node result.
2. **Workspace isolation is graph data.** An action node creates a worktree as a declared side effect. An agent node declares `workspace: <name>`, which becomes its `cwd`. Parallel `each` items need distinct workspaces; the validator refuses parallel items on one workspace. A missing git is a preflight defect, never a silent downgrade to sequence (ADR-0018).
3. **A deadline sits on agent, wait, action, and `each`.** A gate and a loop carry none: they make no call, and a loop's time is bounded by its children and the run. A run-level default applies; a node may override it; the validator refuses a node with no effective deadline (ADR-0051 rule 1).
4. **The engine enforces the deadline with `anyio.fail_after` around `query()`.** The teardown is budgeted as an engine constant, measured at about 5 s. The outcome `deadline` is appended to the run record before the engine waits on the close. After a `deadline` a `repeat` starts fresh; no session resume follows a hang.
5. **An `each` carries its own deadline.** An item's effective deadline is the lesser of its declared deadline and the `each` node's remaining time.
6. **At a wait node the process ends.** The run record marks the wait node. The verdict is needs-human, exit code 10. The message names the owner, the deadline, and the resume command. A later invocation resumes at the wait node. If the deadline has passed on resume, the declared default applies and the record says so (ADR-0029 rules 6 and 7, ADR-0011).
7. **An agent node may yield `needs-human`.** Its default `next` is `exit:needs-human`. The graph author places a wait node after it when the run should hold instead. The engine never inserts a node the graph does not declare.
8. **Run truth is one append-only run record per run**: a JSONL file in the run's directory, one atomic write per event (ADR-0030). Code derives a node's outcome from the node's declared artifact: its output schema, files, or commit. The `ResultMessage` is logged as diagnostic. The verdict module reads the run record and the target only (ADR-0019, ADR-0028).
9. **An `each` declares `join: all | first_failure`, default `all`.** Under `all` every item runs to its end and the join is `fail` if any item failed, else `deadline`, else `needs-human`, else `pass`. Under `first_failure` the first non-pass item cancels its siblings through the same cancel scope and the join takes that outcome.
10. **`mode: parallel | sequence` and `max_parallel` are fields on the `each` node.** `max_parallel` defaults from a run setting. The SDK's subagent cap does not bound concurrent `query()` subprocesses, so the engine must.

## Considered options

- Resume the prior session on a `repeat` with the critic's findings appended. Rejected: a resumed session can land in the wrong state, and a fresh prompt built from artifacts keeps mid-graph resume possible.
- Implicit worktree per agent node, or isolation as a run option. Rejected: a side effect the editor cannot show is dev_squad's D13 defect again.
- A deadline on every kind, gate and loop included (Lens A, dissent on rule 3): a deterministic check or a loop can also stall. Rejected 2–1: a gate and a loop make no call, and ADR-0051 binds the deadline to a call. Reopen if a loop is shown to spin without a child consuming time.
- Resume the same session after a deadline. Rejected: the session may have stopped inside a tool effect, and no documented resume protocol reconciles effects first.
- An in-process poll at a wait node. Rejected: it holds a process for days, and ADR-0051 rule 3 finds a stuck run from records, not from a live caller.
- The session transcript, or git, as the run record. Rejected: the store is best-effort and holds no files; git forces a commit on every node, wait nodes included.
- One fixed join with no per-node choice (Lens A, dissent on rule 9): join all items, so the record reports every outcome. Rejected 2–1: dev_squad's `strictBatch` shows both policies are wanted, and a field keeps the policy as graph data. Reopen if ticket #16 rules strictness a run option.
- `parallelMode` as a run option applied to every `each`. Rejected: the validator needs the mode per node to check workspaces.

## Consequences

GLOSSARY.md gains `Session` and `Workspace`, and the `Each`, `Wait`, `Deadline`, and `Run record` entries change. The run record must be replayable to a wait node; the resume ticket graduated from the map's fog carries that. Ticket #14 folds the outcomes this record produces. Ticket #16 inherits three rulings: isolation, `mode`, and `join` are graph data.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_deadline_on_a_gate_and_on_a_loop`
- `tests/test_graph.py::test_refuses_a_node_with_no_effective_deadline`
- `tests/test_graph.py::test_refuses_parallel_items_sharing_a_workspace`
- `tests/test_graph.py::test_accepts_a_sequence_each_on_one_workspace`
- `tests/test_record.py::test_every_event_kind_keeps_its_contract_fields`
- `tests/test_record.py::test_the_reader_rejects_an_event_that_lacks_a_required_field`
- `tests/test_record.py::test_the_reader_rejects_a_position_gap_an_unknown_kind_and_a_bad_outcome`
- `tests/test_engine_skeleton.py::test_every_entry_starts_a_fresh_session_and_the_artifact_decides_the_outcome`
- `tests/test_engine_skeleton.py::test_artifacts_are_files_named_by_the_outcome_event_and_nothing_else_carries_context`
- `tests/test_engine_skeleton.py::test_an_agent_that_yields_needs_human_ends_the_run_needs_human`
- `tests/test_deadlines.py::test_a_deadline_gives_the_outcome_deadline_with_no_extra_exit_code`
- `tests/test_deadlines.py::test_a_subagent_stays_inside_its_session_and_is_never_a_node`
- `tests/test_deadlines.py::test_the_deadline_event_is_written_before_the_teardown_ends`
- `tests/test_deadlines.py::test_a_fake_child_is_stopped_at_the_deadline_and_leaves_no_orphan`
- `tests/test_deadlines.py::test_a_node_deadline_overrides_the_run_default`
- `tests/test_actions.py::test_an_action_carries_a_deadline_and_a_timeout_is_the_deadline_outcome`
- `tests/test_five_phases.py::test_a_large_input_plans_and_runs_each_item_in_its_own_worktree`
- `tests/test_each.py::test_an_item_gets_the_lesser_of_its_deadline_and_the_remaining_each_time`
- `tests/test_dispatch.py::test_a_deadline_child_folds_the_dispatch_as_deadline_not_a_new_exit_code`
- `tests/test_deadlines.py::test_a_null_run_deadline_is_valid_and_a_node_deadline_still_stops_its_call`
- `tests/test_deadlines.py::test_a_null_run_deadline_with_no_node_deadline_runs_to_its_end_and_an_option_may_set_one`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
