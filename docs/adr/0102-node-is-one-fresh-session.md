# ADR-0102: A node is one fresh session with a deadline, in a declared workspace; truth is the run record

## Status

Accepted.

## Context

ADR-0100 fixes the vocabulary. This record fixes how the engine runs one node on `claude-agent-sdk`. Facts it rests on:

- A node is one `query()` call: one CLI subprocess, one session. The SDK has no wall-clock deadline. An `anyio.fail_after` scope around `query()` stops the CLI child, with about 5 s of teardown.
- Nothing documents a session resume after a hang.
- The session store mirrors transcripts only, best-effort. File state needs git.
- A background subagent yields a second `ResultMessage` whose `origin.kind` is `task-notification`.
- The SDK concurrency limit caps subagents inside one session, not concurrent `query()` subprocesses.

## Decision

1. **Every entry of an agent node starts a new session.** A `repeat` starts a fresh session too. Context between nodes travels only through artifacts that code reads and puts in the prompt. The transcript is never truth (ADR-0019). A node that spawns a background subagent keeps the result whose `origin` is empty. A child spawned by a dispatch node follows the same rules (ADR-0113).
2. **Workspace isolation is graph data.** An action node creates a worktree as a declared side effect. An agent node declares `workspace: <name>`, which becomes its `cwd`. Parallel `each` items need distinct workspaces, and the validator refuses parallel items on one workspace. A missing git is a preflight defect, never a silent downgrade to sequence (ADR-0018). A node that passed commits that workspace itself, so the branch tip is the run's final state (ADR-0106). A node that declares no workspace works in the target; when the target is a worktree the run owns, the same commit applies to it.
3. **A deadline sits on agent, wait, action, and `each`.** A gate and a loop carry none: they make no call. A run-level default applies and a node may override it. The validator refuses a node with no effective deadline (ADR-0051). The run-level deadline may be null, meaning none. The per-call deadline is then the only cap.
4. **The engine enforces the deadline with `anyio.fail_after` around `query()`.** The teardown budget is an engine constant of about 5 s. The `deadline` outcome is appended to the run record before the engine waits on the close. After a `deadline`, a `repeat` starts fresh. No session resume follows a hang.
5. **An `each` item's effective deadline is the lesser of its own and the `each` node's remaining time.**
6. **At a wait node the process ends.** The run record marks the wait node. The verdict is needs-human, exit code 10. The message names the owner, the deadline, and the resume command. A later invocation resumes at the wait node. If the deadline has passed on resume, the declared default applies and the record says so (ADR-0029).
7. **An agent node may yield `needs-human`.** Its default `next` is `exit:needs-human`. The author places a wait node after it when the run should hold. The engine never inserts a node the graph does not declare.
8. **Run truth is one append-only run record per run**: a JSONL file in the run directory, one atomic write per event (ADR-0030). Code derives a node's outcome from its declared artifact: output schema, files, or commit. The `ResultMessage` is diagnostic only. The verdict module reads the run record and the target only (ADR-0019).
9. **An `each` declares `join: all | first_failure`, default `all`.** Under `all`, every item runs to its end. The join is `fail` if any item failed, else `deadline`, else `needs-human`, else `pass`. Under `first_failure`, the first non-pass item cancels its siblings and the join takes that outcome.
10. **`mode: parallel | sequence` and `max_parallel` are fields on the `each` node.** `max_parallel` defaults from a run setting. The engine must bound concurrent `query()` subprocesses, because the SDK does not.
11. **One scheduler runs each node through the runner of its kind.** The agent runner owns the one fresh session and its deadline.

Rejected: resuming the prior session on a `repeat` or after a deadline (a resumed session can land in the wrong state); an implicit worktree per node (a side effect the editor cannot show); an in-process poll at a wait node (ADR-0051 finds a stuck run from records); the transcript or git as the run record.

## Guard

`tests/test_graph.py`, `tests/test_record.py`, `tests/test_engine_skeleton.py`, `tests/test_deadlines.py`, `tests/test_each.py`, `tests/test_actions.py`, `tests/test_node_commit.py`. A change to this rule changes its tests in the same commit (ADR-0018).
