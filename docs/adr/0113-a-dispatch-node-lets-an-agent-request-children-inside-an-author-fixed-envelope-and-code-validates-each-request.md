# ADR-0113 — A dispatch node lets an agent request children inside an author-fixed envelope; code validates each request

## Status

Accepted, 2026-09-30. Owner direction, then panel decision under autonomy. The owner said the flow must adapt to the problem and not be pre-built. Panel on the request shape: `AGREED 2–1`. Lens A: Codex `gpt-6.1-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the split only. Panel on the design direction (a hybrid: authored envelope plus `dispatch`): `AGREED` 3–0. Amends ADR-0100, ADR-0102, ADR-0103, ADR-0104.

Amended 2026-09-30 (ticket #47): a dispatch child whose tools include a write tool runs in its own git worktree, made from the HEAD of the workspace its template names (the target when that workspace does not exist). A read-only child keeps the named workspace. A worktree path that already exists is refused, never reused.

Amended 2026-10-01 (tickets #87 and #88, first real router run): a read-only child whose named workspace does not exist runs in the target, as a write child's worktree is cut from the target. A spawn request may carry `after`, a list of indexes of earlier requests in the same list. Code turns each index into the child name that the earlier request got, and rejects the whole request with `after_unmet:<index>` when that request was rejected, is not earlier, or does not exist, and with `after_malformed` when the list is not non-negative integers. A child waits, outside the parallel limit, until every child it runs after has an outcome. When one of them did not pass, the child fails without a session, with the reasons `after_failed` and the failed child's name. A read-only child that runs after a child with its own worktree starts in that worktree, so a reviewer sees the work it reviews; its tools stay read-only. The request field `inputs` is accepted but nothing reads it yet.

Amended 2026-10-02 (ticket #101, owner ruling; panel split three ways on run-20261001-234426, so the owner decided). A landing is checked as it happens, and a regression undoes it:

12. **Every landing is checked, and only a regression fails it.** After each `merge_land`, code runs the held-out command of every child that has landed, in request order, and compares each result with the result that command had before this landing. A command that passed before and fails after is a **regression**: the merge is undone, the target returns to the HEAD it held before the merge, and the child folds `fail` with reason `ac_regression:<child of the command that moved>`. A command already failing before the landing is not a regression and fails nothing: this rule asks only what the landing moved, and the final gate is the check on a command that was already red. That also keeps this rule correct on a tree where an issue is unimplemented, a child died, or a held-out file was never reachable. A read-only child, a child that did not pass, and a child that did not merge are never checked. A command with no earlier result to move from — a child's own command at its own landing, and every command at the first landing of a run — is not checked here. The final gate above remains the check on those.
13. **The final gate stays.** It still runs once after the loop over every landed child's `ac_commands`, as rule 4 below says. The per-landing check is the attribution; the gate is the backstop.

Amended 2026-10-04 (owner: "fix", after run-20261004-004606): issue 04 landed with 6 of its 27 held-out tests red. Rule 12 left a child's own command unchecked at its own landing, and the router read only "landed", so it ended the run with no wave left to repair it. The final gate caught it too late.

14. **A child's own held-out command is run at its own landing, and the ledger reports it.** The landing stays, since the repair builds on it, and never undoes the merge (rule 12 still owns undoing, and only for a regression). The `land` event's `effect` carries `held_out`: `pass` or `fail`, absent when the child has no command or did not land. Each ledger row carries the same `held_out`. `scripts/run_router.py` tells the router that `held_out` `fail` means the issue is not done: it requests a repair child, and it does not stop on a landed row alone. The `task` ceiling (rule 10) still bounds the repairs.

Why not check only once at the end: measured on run-20261001-234426, the land of issue 06 turned issue 13's held-out command red, and the gate was the first thing to notice — fifteen lands later. Nothing in the record named the land that broke it, so the router could not be asked to repair it. Why not fail on any red: with a per-issue key, a legitimate land turns an earlier issue's pinned values red, and failing it would block correct work.

Amended 2026-10-01 (ticket #90; panel on #89, owner present, `AGREED`: Lens A Codex `gpt-6-sol`, Lens B Fable 5.1, no dissent). One run can carry many tasks, so the graph grows by waves, and code, not a model, lands the work:

1. **Code lands every passing write child.** After the children of a dispatch entry finish, code merges each passing child that has its own worktree into the target with `merge_land`, one at a time, in request order (the order of `spawn_accepted`), never in completion order. Each landing writes a `land` event: the child, the dispatch, the entry, `pass` or `fail`, reasons, and the effect (`merge_land`, the new target HEAD, and on a conflict the last 400 characters of git's output). A read-only child and a child that did not pass are never landed. A landing is written once: a resume skips a child with a `land` event.
2. **A conflict fails that child, not the wave.** The conflicting merge is aborted, the target is left clean, the child folds as `fail` with reason `merge_conflict` (`land_workspace_missing` when its worktree is gone), and the children that landed before it stay landed. The dispatch folds with the landing result: its outcome is `fail`, and the author's `next` on the dispatch decides what follows.
3. **A router reads a ledger, never a child's artifact.** The prompt of a dispatch entry agent carries one input, `ledger`: one row per finished child of that dispatch, in request order, with the child name, the first 160 characters of its task, the outcome after landing, at most three reasons of at most 120 characters, at most 20 changed paths, the check results, and the landed HEAD (null when it did not land). Diffs, test output and conflict text never enter the ledger or the prompt; they stay in the record and in the child's artifact. The dispatch artifact carries the same ledger.
4. **Waves.** An author wraps the dispatch in a loop and sets the dispatch `next.pass` to `repeat`. Each pass runs a fresh router session on the ledger, and the loop maximum bounds the waves. The final gate runs once after the loop. It takes the `ac_commands` of every child that passed, across all waves. A child of wave N starts from the target HEAD after waves 1 to N−1 landed. Integration inside one wave is checked by the final gate. Corrected 2026-10-02 (ADR-0119): an earlier draft claimed the child's "own suite run already checks it against the earlier waves". It does not. The child runs the project's own suite, not the earlier children's held-out commands, and `squad/subject.py` `_held_command` copies a held-out test in and deletes it again, so no earlier held-out test is present when a later child runs. Measured on run-20261001-234426: the land of issue 06 turned issue 13's held-out command red, and the final gate was the first thing to notice. Rule 12 of the 2026-10-02 amendment closes that gap: every landing is checked as it happens, and a regression undoes it.
5. **A stop word.** An entry artifact with `"spawns": []` and `"done": true` ends the waves: the dispatch records `pass` with reason `done` and routes `exit:pass`, which leaves the loop. Zero spawns without `done` is still `fail`, `no_dispatch` (ADR-0114 rule 7). `done` is ignored when the same artifact also asks for children.
6. **A child's outcome names its model and tokens.** Found in the first real wave run: a dispatch child's `outcome` event carried no `model`, `effort`, `tokens_in` or `tokens_out`, so the dashboard showed a partial token total. A child outcome now carries the same fields as an agent node (ticket #71); tokens stay absent when the adapter does not report them, never zero.
7. **Delivery.** The target holds one `--no-ff` merge commit per landed child. A push out of the target stays the owner's act (ADR-0024).

Not done: a repair child after a conflict is the router's request in the next wave. Nothing integrates a conflict by itself.

Amended 2026-10-01 (ticket #93, found running the full 24-issue subject set with the owner): a wave that lands nothing is a stall; a wave that lands something is progress, and `max` alone could not tell the two apart. On a real run, 16 waves each landed at least one child, yet the run still failed `loop_max_exhausted` once the count reached the author's fixed ceiling.

8. **`stall_after` is a loop field, opt-in, generic to `squad/engine.py`'s one loop executor (ADR-0100), not special-cased to the name `waves`.** `{"count": N, "by": "land"}` tracks consecutive entries of that loop with no `land` event of outcome `pass`. An entry that lands something resets the count to zero. Reaching `N` ends the loop `fail` with reason `waves_stalled`, distinct from `loop_max_exhausted`.
9. **`max` stays the hard backstop, unchanged.** Every entry still counts against it, landing or not; `stall_after` only adds an earlier, progress-aware exit. A loop without `stall_after` behaves exactly as before. `scripts/run_router.py` set `max` to a generous ceiling (`max(20, issues × 3)`) and `stall_after.count` to 3, so a productive run is bounded by real stalls, not by a hand-tuned wave count. (Ticket #98 later replaced the router's ceiling with `max_by: "task"`, rule 11.)

Amended 2026-10-01 (ticket #98, found on the same 24-issue run): a wave that lands a new issue and retries a stuck one resets the stall count, so `stall_after` alone never notices the stuck issue, and `max` still bounded the run by its wave count. The run failed `loop_max_exhausted` after 18 issues landed and zero failures. Owner ruling (2026-10-01): the loop ceiling serves only the same task. A wave that advances to a new task must not spend the ceiling; repeating the same task must.

10. **`max_by: "task"` is a loop field, opt-in, generic to `squad/engine.py`'s one loop executor (ADR-0100), never special-cased to the name `waves`.** With it, `max` is the ceiling of one task's attempts, not of the loop's entries. Each entry adds one attempt to every `task` key its accepted children name; a task that reaches `max` attempts ends the loop `fail` with reason `task_max_exhausted`, distinct from `loop_max_exhausted` and `waves_stalled`. An entry that advances to a task not seen before never spends the ceiling, so progress is never mistaken for being stuck. An entry that names no task key adds one unnamed attempt, so termination still holds. In this mode `loop_max_exhausted` never fires. Absent the field, a loop behaves exactly as before: every entry counts against `max`.
11. **`task` is an optional string on a spawn request.** It names the work item a child advances. Code still assigns the child identity, never a model (rule 4); the task key is the work item, which is content, not identity, so it is not an identity field. `limit_errors` has no rule for it: a task key narrows nothing. `scripts/run_router.py` sets each child's `task` to the issue number it works, and the `waves` loop sets `max_by: "task"` with a small `max` (3), keeping `stall_after` as the no-progress exit.

## Context

ADR-0100 to ADR-0112 describe an authored flow. The author fixes the graph, and a model may only tune six settings (ADR-0103). The owner also wants a flow that no author wrote in advance. One agent runs a router skill. The router names the next skill and prompt for another agent node, and so on. The same engine must run both flows, and the authored guards must hold in both.

## Decision

1. **A seventh node kind: `dispatch`.** An authored graph may hold none, one, or many. A graph with no `dispatch` node is the authored flow of ADR-0100 to ADR-0112, unchanged. A graph whose root loop holds one `dispatch` node is a dynamic flow. The two use one scheduler, one node executor, and one run record.
2. **A dispatch node names an entry agent and an envelope.** The entry agent is an authored agent node. Its output schema carries a `spawns` list. Code lifts the list out, as it lifts proposals (ADR-0103 rule 5). The envelope is the author's and no model widens it:
   - `max_depth`, `max_children` per entry, `max_parallel`, and a `deadline`.
   - `join`: `all` or `first_failure`, as on `each`.
   - `allow`: the model list, effort list, tool ceiling, skill list with a pinned version for each, and the workspace scope.
   - a child budget: the turn and spend minimum and maximum for one child.
   - `checks`: gate node names. Code runs them after every child and before any guarded action. A child cannot omit or edit them.
3. **A spawn request is free-form inside the envelope.** It holds a prompt, a skill name, and optional model, effort, tools, turn cap, spend cap, deadline, and input artifact names. Code checks every value against `allow`. A value may only narrow. The parent's limits bound the child: a child gets the intersection of its parent's tools and the lesser of its parent's remaining spend and time. An input names an artifact already in the run record or a declared file (ADR-0110).
4. **Code assigns identity.** A model never mints an id. The request key is the record position of the `spawn_requested` event. The child name is `<dispatch>.<entry>.<n>`. It carries `parent`, `depth`, and `skill`.
5. **A request that exceeds any limit is rejected whole.** No clamp and no partial apply (ADR-0103 rule 8). A rejection changes no node outcome.
6. **A skill is a setting of the child session, not a node.** The engine starts the child with the prompt `/<skill> <prompt>` and the skill pinned in the allowlist. A skill that a model may not invoke by itself, such as a router, runs only through this prefix. A subagent inside a session stays inside it and is never a node (ADR-0102).
7. **Spend is reserved before launch.** Acceptance records the reservation from the run remainder (ADR-0108). An entry that the run cannot fund is never accepted.
8. **The record gets three new events:** `spawn_requested`, `spawn_accepted`, and `spawn_rejected`. Each holds the request key, the parent, the requested values, the envelope entry it was checked against, and a reason on rejection. An accepted child then writes the same `outcome` events as any node.
9. **A run-only overlay.** A run never edits the graph document. The editor shows the envelope on the dispatch card, and the children as a run-only overlay read from the record (ADR-0111 rule 6).

## Considered options

- **Templates only** (Lens B, dissent on the request shape, `AGREED 2–1`). Verbatim: "(a) template, plus two of (c)'s items moved onto the template, not the request … A named template is the only place the envelope's 'gate a child cannot skip', a `workspace` …, an `adapt.sees` list …, and the child's output artifact can be authored. Under (b) every child shares one ceiling and no card can show a child's effective range, the D13 defect." Rejected 2–1: templates pre-author the child shapes, and the owner asked for a flow that no author wrote in advance. Reopen if an allowlist combination proves unsafe and code cannot state the limit, or if a child shape needs its own output contract. Lens B's point stands in part: `checks` and `workspace` stay author-fixed on the dispatch node, so a request never picks them.
- **Fuller request with an idempotency key, output contract, and pinned skill version** (Lens A). Partly taken: the key is code-assigned (rule 4) and the skill is pinned in `allow` (rule 2). Deferred: a request-authored output contract, since a model must not author its own pass condition. Reopen when a child needs a contract that the entry agent chooses.
- **Dynamic only, no authored graph.** Rejected by all three lenses: the owner still wants gates, waits, and actions.
- **A dynamic flow as a preset seed that the dispatcher may leave.** Rejected: a gate would become skippable (ADR-0100 rule 3).
- **A skill as a node kind.** Rejected by all three lenses: the SDK runs a skill through a session, and a node is one session.

## Consequences

ADR-0100 lists seven kinds. ADR-0103 rule 1 says no proposal adds, removes, or reorders a node. A spawn is a request in the output schema beside proposals. It is not a proposal kind, and it instantiates a child inside a dispatch envelope. ADR-0102 rule 1 covers a spawned child: one fresh session. ADR-0104 registers three facts a dispatching agent may list in `sees`: `depth`, `children_count`, `spawn_rejected_count`. ADR-0114 covers the hash, resume, and verdict. Ticket #15 (control model) is unaffected: a dispatch child has no `next` of its own, and the dispatch node joins the outcomes.

Cost: a free-form request has a wide combination space, and the validator checks each value alone. A rule that couples two values is a later amendment.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_dispatch_with_no_checks`
- `tests/test_graph.py::test_a_spawn_value_outside_allow_is_rejected_whole_with_its_rule`
- `tests/test_graph.py::test_a_model_supplied_identity_in_a_spawn_request_is_refused`
- `tests/test_dispatch.py::test_one_child_runs_as_its_skill_and_writes_the_three_events_and_the_artifact`
- `tests/test_dispatch.py::test_code_assigns_the_request_key_and_child_name_and_a_model_id_is_refused`
- `tests/test_dispatch.py::test_a_request_over_any_limit_is_rejected_whole_with_one_event`
- `tests/test_dispatch.py::test_zero_accepted_children_fold_to_fail_no_dispatch`
- `tests/test_dispatch.py::test_spend_is_reserved_at_acceptance_before_any_launch`
- `tests/test_dispatch.py::test_a_request_the_run_cannot_fund_is_rejected`
- `tests/test_dispatch.py::test_the_checks_gates_run_after_every_child_and_a_failed_check_fails_the_child`
- `tests/test_dispatch.py::test_the_dispatch_artifact_feeds_the_final_gate_with_the_child_commands`
- `tests/test_dispatch.py::test_a_child_may_spawn_a_child_and_depth_and_fan_out_are_enforced`
- `tests/test_dispatch.py::test_a_child_gets_the_intersection_of_its_parents_tools`
- `tests/test_dispatch.py::test_a_grandchild_gets_no_more_than_its_parents_remaining_spend`
- `tests/test_dispatch.py::test_a_grandchild_deadline_is_bounded_by_its_parents_remaining_time`
- `tests/test_dispatch.py::test_max_children_per_entry_is_enforced`
- `tests/test_dispatch.py::test_children_run_in_parallel_up_to_max_parallel`
- `tests/test_dispatch.py::test_parallel_writing_children_run_in_distinct_worktrees_and_a_reader_shares_the_workspace`
- `tests/test_dispatch.py::test_a_request_never_picks_its_workspace_and_each_child_worktree_starts_from_the_target_head`
- `tests/test_dispatch.py::test_a_resume_checks_a_child_worktree_by_its_own_path`
- `tests/test_dispatch.py::test_a_child_whose_worktree_path_exists_is_refused_not_reused`
- `tests/test_dispatch.py::test_a_read_only_child_starts_in_a_directory_that_exists_when_its_named_workspace_was_never_made`
- `tests/test_dispatch.py::test_a_child_with_after_starts_only_when_the_children_it_names_have_passed`
- `tests/test_dispatch.py::test_a_child_whose_after_child_failed_fails_unrun_with_the_reason`
- `tests/test_dispatch.py::test_an_after_that_names_no_earlier_accepted_request_rejects_the_request_whole`
- `tests/test_dispatch.py::test_a_read_only_child_that_runs_after_a_writer_starts_in_the_writers_worktree`
- `tests/test_waves.py::test_a_passing_write_child_is_landed_in_the_target_and_recorded`
- `tests/test_waves.py::test_two_write_children_land_in_request_order_and_the_final_gate_sees_both`
- `tests/test_waves.py::test_a_conflicting_second_writer_fails_with_merge_conflict_and_the_first_stays_landed`
- `tests/test_waves.py::test_a_read_only_child_and_a_failed_child_are_not_landed`
- `tests/test_waves.py::test_a_passed_writer_whose_worktree_is_gone_fails_its_landing_by_name`
- `tests/test_waves.py::test_a_resume_does_not_land_a_child_again`
- `tests/test_waves.py::test_the_router_reads_only_the_ledger_and_the_stop_word_ends_the_waves`
- `tests/test_waves.py::test_the_ledger_shows_a_conflict_and_keeps_the_conflict_text_out_of_the_router`
- `tests/test_waves.py::test_zero_spawns_without_the_stop_word_still_fail_no_dispatch`
- `tests/test_waves.py::test_the_stop_word_is_ignored_when_the_router_also_asks_for_children`
- `tests/test_dispatch.py::test_a_resume_runs_only_the_frontier_and_never_a_passed_sibling`
- `tests/test_waves.py::test_a_dispatch_child_outcome_carries_its_model_effort_and_tokens_when_known`
- `tests/test_waves.py::test_a_task_loop_that_names_a_new_task_each_entry_runs_past_a_small_max`
- `tests/test_waves.py::test_a_task_retried_max_times_exits_fail_task_max_exhausted`
- `tests/test_waves.py::test_a_task_loop_that_names_no_task_still_terminates`
- `tests/test_envelope.py::test_a_task_key_normalizes_and_survives`
- `tests/test_envelope.py::test_a_task_key_is_optional_and_none_when_absent`
- `tests/test_envelope.py::test_a_task_key_carries_no_limit_rule_of_its_own`
- `tests/test_waves.py::test_a_landing_that_breaks_an_earlier_childs_held_out_command_is_undone`
- `tests/test_waves.py::test_a_landing_that_leaves_every_command_where_it_was_stays_landed`

Open: a rule that couples two request values is a later amendment (ADR-0113 cost note); no real SDK adapter runs a skill yet, so the skill prefix is tested on the request only; the request field inputs is accepted and unread.
