# ADR-0107: A resume replays the record from one restart point, with no cache, and checks the workspace first

## Status

Accepted.

## Context

A wait node ends the process, and a later invocation resumes it. Every entry starts a fresh session, and an SDK session is never resumed. The run record is truth (ADR-0019). The `run_start` event logs the graph hash and the engine options.

## Decision

1. **`resume <run-id>` reads the run record and the target.** It refuses a run whose `run_start` graph hash differs from the graph file. It replays the graph walk over the record: it rebuilds each loop's entry count and each node's last outcome from the `outcome` events, and it replays the applied proposals onto the graph (ADR-0103). It reads every `when` again on entry.
2. **The restart point is one position,** or a frontier when a dispatch holds parallel children (ADR-0113). A wait node restarts at that wait. A stopped run restarts at the node after the stop node. Otherwise it is the first node with no exit event in its current entry.
3. **There is no per-node cache key.** Nothing at or after the restart point has an exit event in the current entry, so nothing is skipped. An artifact before the restart point stands because its `outcome` event names it, and the preflight checks that it is present. A missing artifact gives `failed`.
4. **`--from <node name>` is an engine option.** It moves the restart point back to a validated node name. Everything from that node on runs again. The `resume` event logs it.
5. **The preflight checks each workspace that a node about to run will use.** The workspace exists and is in the state its action node's `outcome` event recorded (HEAD, status and diff digest). A missing worktree or a missing git gives `failed`. A moved HEAD, or a change made after the recorded state, gives `conflict`, exit 20. Edits an agent made before the stop are part of the recorded state. The engine never stashes, recreates, repairs or discards a person's change. The person commits or discards, then resumes.
6. **A run may resume when its root loop has no `outcome` event.** That holds after a wait, after a `--stop-after` stop, after a preflight `failed` or `conflict`, and after an abandoned run (ADR-0051). A `conflict` resumes only when the preflight no longer observes it. When the root has an exit event, including one written by a run deadline, the graph ran to its end and a new run starts. A person who wants a redo edits the graph.
7. **A resume keeps the run id.** It appends one `resume` event to the same record: the invoker, the engine options, the restart position, the preflight observation, and the declared default if the wait's deadline had passed. The record holds one `verdict` event per invocation, and the fold takes the last (ADR-0105).

Rejected: a per-node cache key, because it is a second truth beside the record. Rejected: stashing or recreating a workspace, because the engine would guess and drop a person's change. Rejected: a new linked run id, because a run is one execution of one graph version.

## Guard

`tests/test_resume.py`, `tests/test_verdict.py`, `tests/test_adapt.py`.
