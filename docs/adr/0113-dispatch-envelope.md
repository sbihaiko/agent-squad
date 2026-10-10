# ADR-0113: A dispatch node lets an agent request children inside an author-fixed envelope; code validates each request

## Status

Accepted.

## Context

An authored flow fixes the graph, and a model may only tune settings (ADR-0103). The owner also wants a flow that no author wrote in advance: one agent runs a router skill, and the router names the next skill and prompt for another agent. One engine must run both flows, and the authored guards must hold in both.

## Decision

### The node and its envelope

1. **`dispatch` is a node kind.** A graph with no `dispatch` node is a plain authored flow. A graph whose root loop holds one `dispatch` node is a dynamic flow. Both use one scheduler, one node executor, and one run record.
2. **A dispatch node names an entry agent and an envelope.** The entry agent is an authored agent node. Its output schema carries a `spawns` list, which code lifts out as it lifts proposals. No model widens the envelope:
   - `max_depth`, `max_children` per entry, `max_parallel`, and a `deadline`.
   - `join`: `all` or `first_failure`, as on `each`.
   - `allow`: the model list, effort list, tool ceiling, skill list with a pinned version for each, and the workspace scope.
   - a child budget: the stall and spend minimum and maximum for one child.
   - `checks`: gate node names. Code runs them after every child and before any guarded action. A child cannot omit or edit them.
3. **A spawn request is free-form inside the envelope.** It holds a prompt, a skill name, and optional model, effort, tools, stall cap, spend cap, deadline, a `task` key, an `after` list, and input artifact names. Code checks every value against `allow`. A value may only narrow. A child gets the intersection of its parent's tools and the lesser of its parent's remaining spend and time. An input names an artifact already in the run record or a declared file (ADR-0109). The request never picks `checks` or the workspace.
4. **Code assigns identity.** A model never mints an id. The request key is the record position of the `spawn_requested` event. The child name is `<dispatch>.<entry>.<n>`. It carries `parent`, `depth`, and `skill`. `task` names the work item a child advances. It is content, not identity, and it narrows nothing.
5. **A request that exceeds any limit is rejected whole.** No clamp and no partial apply. A rejection changes no node outcome.
6. **A skill is a setting of the child session, not a node.** The engine starts the child with the prompt `/<skill> <prompt>` and the skill pinned in the allowlist. A skill that a model may not invoke by itself, such as a router, runs only through this prefix. A subagent inside a session stays inside it and is never a node (ADR-0102).
7. **Spend is reserved before launch.** Acceptance records the reservation from the run remainder (ADR-0108). An entry that the run cannot fund is never accepted.
8. **The record gets three events:** `spawn_requested`, `spawn_accepted`, and `spawn_rejected`. Each holds the request key, the parent, the requested values, the envelope entry it was checked against, and a reason on rejection. An accepted child then writes the same `outcome` events as any node. An outcome names the model, effort, and tokens when known. Tokens are absent when the adapter does not report them, never zero.
9. **A run never edits the graph document.** The editor shows the envelope on the dispatch card and the children as a run-only overlay read from the record (ADR-0111).

### Workspaces and order

10. **A child that can write runs in its own git worktree,** cut from the HEAD of the workspace its template names (the target when that workspace does not exist). A worktree path that already exists is refused, never reused. A read-only child runs in the named workspace, or in the target when that workspace does not exist.
11. **`after` orders children.** It lists indexes of earlier requests in the same list. Code turns each index into the child name of that request. It rejects the whole request with `after_unmet:<index>` when that request was rejected, is not earlier, or does not exist, and with `after_malformed` when the list is not non-negative integers. A child waits, outside the parallel limit, until every child it follows has an outcome. When one did not pass, the child fails without a session, with the reasons `after_failed` and the failed child's name. A read-only child that follows a child with its own worktree starts in that worktree, so a reviewer sees the work it reviews. Its tools stay read-only.

### Landing and waves

12. **Code lands every passing write child.** After the children of an entry finish, code merges each passing child that has its own worktree into the target, one at a time, in request order, never in completion order. Each landing writes a `land` event: the child, the dispatch, the entry, `pass` or `fail`, reasons, and the effect (the new target HEAD, and on a conflict the last 400 characters of git's output). A read-only child and a child that did not pass are never landed. A landing is written once: a resume skips a child with a `land` event. The target holds one `--no-ff` merge commit per landed child. A push out of the target stays the owner's act (ADR-0024).
13. **A conflict fails that child, not the wave.** The merge is aborted and the target stays clean. The child folds `fail` with reason `merge_conflict` (`land_workspace_missing` when its worktree is gone). Children that landed before it stay landed. The author's `next` on the dispatch decides what follows. Nothing integrates a conflict by itself: a repair child is the router's request in the next wave.
14. **Every landing is checked, and only a regression fails it.** After each landing, code runs the held-out command of every child that has landed, in request order, and compares each result with the result that command had before this landing. A command that passed before and fails after is a regression. The merge is undone, the target returns to the HEAD it held before, and the child folds `fail` with reason `ac_regression:<child of the command that moved>`. A command already failing is not a regression. A command with no earlier result to move from is not checked here. A read-only child, a child that did not pass, and a child that did not merge are never checked. Rejected: failing on any red, because a legitimate landing can turn an earlier issue's pinned values red.
15. **A child's own held-out command runs at its own landing, and the ledger reports it.** The landing stays, since the repair builds on it. The `land` effect and each ledger row carry `held_out`: `pass` or `fail`, absent when the child has no command or did not land. A `fail` means the work is not done: the router requests a repair child and never stops on a landed row alone.
16. **A router reads a ledger, never a child's artifact.** The entry agent's prompt carries one input, `ledger`: one row per finished child of that dispatch, in request order, with the child name, the first 160 characters of its task, the outcome after landing, at most three reasons of at most 120 characters, at most 20 changed paths, the check results, `held_out`, and the landed HEAD (null when it did not land). Diffs, test output, and conflict text never enter the ledger or the prompt. The dispatch artifact carries the same ledger.
17. **Waves.** An author wraps the dispatch in a loop and sets the dispatch `next.pass` to `repeat`. Each pass runs a fresh router session on the ledger, and the loop maximum bounds the waves. A child of wave N starts from the target HEAD after waves 1 to N-1 landed. The final gate runs once after the loop over the `ac_commands` of every child that passed. It is the backstop for what rules 14 and 15 do not check.
18. **A stop word.** An entry artifact with `"spawns": []` and `"done": true` ends the waves: the dispatch records `pass` with reason `done` and routes `exit:pass`. Zero spawns without `done` is `fail` with reason `no_dispatch`. `done` is ignored when the same artifact also asks for children.
19. **`stall_after` is an opt-in loop field,** generic to the one loop executor, never special-cased to a loop name. `{"count": N, "by": "land"}` counts consecutive entries with no `land` event of outcome `pass`. An entry that lands something resets the count. Reaching `N` ends the loop `fail` with reason `waves_stalled`. `max` stays the hard backstop. A loop without the field behaves as before.
20. **`max_by: "task"` is an opt-in loop field,** generic in the same way. With it, `max` is the ceiling of one task's attempts, not of the loop's entries. Each entry adds one attempt to every `task` key its accepted children name. A task that reaches `max` is dropped and recorded, and the loop goes on to the other tasks. An entry that names only dropped tasks ends the loop `fail` with reason `task_max_exhausted`. A loop that would end `pass` with a dropped task ends `fail` with `task_exhausted:<task>`. An entry that advances to a new task never spends the ceiling. An entry that names no task key adds one unnamed attempt, so termination holds. In this mode `loop_max_exhausted` never fires.

### Hash, resume, and fold

21. **The graph hash covers the authored document only:** each dispatch node, its envelope, and its `allow` list with pinned skill versions. It never covers a spawned child. Layout stays outside it (ADR-0111). A change to the envelope or an allowed skill version is a new graph version.
22. **The record holds the realized graph.** A child exists in a run because a `spawn_accepted` event says so. It holds the full child definition after validation, the parent, the depth, the reservation, and the record position.
23. **A resume replays a frontier, not one position.** It replays the walk (ADR-0107), then replays `spawn_accepted` events in record order to rebuild every child, the depth, and the fan-out count. The frontier is every accepted child with no exit event in its current entry, plus the restart position of the authored nodes. Each frontier child starts a fresh session. A child that already holds an `outcome` stands on it and is not run again. The `resume` event lists every frontier position.
24. **Two crashes differ.** An accept event with no `call_start`: the child is in the frontier and launches. The engine rechecks the funding of its reservation, and unknown spend is never counted as zero (ADR-0108). A `call_start` with no outcome: the child crashed, is not run again, and the verdict carries `crashed`.
25. **The workspace preflight applies to every frontier child** (ADR-0107). A started child with no exit event is not repaired. The engine never stashes or recreates a workspace.
26. **A dispatch folds like `each`.** It joins the outcomes of its children under its `join` rule. The `checks` gates run per child, and a failed check is that child's `fail`. An entry with zero accepted children folds to `fail` with reason `no_dispatch`, so an empty run never folds to completed. The verdict function is unchanged: the root is still an authored loop (ADR-0105).

Cost: a free-form request has a wide combination space, and the validator checks each value alone. A rule that couples two values is a later change.

Rejected: templates only (they pre-author the child shapes the owner wants unwritten); a request-authored output contract (a model must not author its own pass condition); a dynamic flow as a preset seed the dispatcher may leave (a gate would become skippable, ADR-0100); the graph hash covering children (identity would change during the run).

## Guard

`tests/test_dispatch.py`, `tests/test_waves.py`, `tests/test_graph.py`, `tests/test_envelope.py`, `tests/test_verdict.py`. A change to this rule changes its tests in the same commit (ADR-0018).
