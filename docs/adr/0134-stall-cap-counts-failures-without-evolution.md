# ADR-0134: A cap counts failed attempts that do not change, never totals: a call's turns, a loop's entries

## Status

Accepted.

## Context

A cap on total tool-use turns punishes long work that goes well, and it spares a call that retries one failing step. A call stopped at its turn cap loses the whole call (`node_turn_cap`), though most of its turns were progress. The thing to stop is a call that keeps trying to solve something that fails and shows no evolution.

## Decision

1. **No turn cap exists.** The adapter passes the SDK no `max_turns`. Spend ([ADR-0108](0108-run-spend-and-caps.md)) and the deadline ([ADR-0051](0051-call-deadline-and-stuck-agent.md)) bound a whole call.
2. **A stall cap replaces it.** An attempt is one tool call. Its step is the tool plus its command or target (a Bash command, a file path). An attempt that errors counts against its step when the failure reads the same as the last failure of that step. The call stops when one step reaches the cap.
3. **Evolution restarts the count.** A failure that reads differently, a changed count of failures included, restarts the step at one. A success clears the step. A call with a hundred turns and no repeated failure never stalls.
4. **A failure reads by its tail.** Code compares the last 600 characters of the result with whitespace collapsed, and with durations, addresses, ids and temp paths removed. A model's claim of progress is never read.
5. **The setting is `limits.max_stalls`** (default and project file) and `budget.stalls` on a node and on a dispatch envelope, with a minimum and a maximum. A proposal may shrink it (`shrink_stalls`) and never raise it. A spawn request names `stalls`. The default is PROVISIONAL: a first guess, held until a labeled sample of real runs supports a value (ADR-0012, ADR-0018).
6. **A stalled call ends with no result.** The adapter closes the stream at the cap. No `ResultMessage` exists, so the node ends `fail`, reason `node_stall_cap`, with `spend: null`; the grant stays as unmeasured spend ([ADR-0108](0108-run-spend-and-caps.md) rule 2). The `outcome` event keeps `turns` as a measured count when the SDK reports one.
7. **A loop's `max` counts the same way.** An entry that repeats with a failure reading like the last entry's (its reasons, compared as in rule 4) extends the streak. A changed failure restarts it at one. `max` entries alike in a row end the loop `fail`, reason `loop_max_exhausted`. Total entries are never capped. A repeat with no reasons, such as a wave, reads alike each time, so `max` still bounds waves ([ADR-0113](0113-dispatch-envelope.md)). `stall_after` and `max_by: "task"` stay as they are, and `max_by: "task"` never reads the streak. A proposal may still shrink `max`.

Rejected: a progress claim in the artifact, because a model's word about itself is not an engine fact. Rejected: a hard entry ceiling beside `max`, and an opt-in switch per loop, for the same reason. Rejected: keeping a high turn cap as a backstop, because spend and the deadline already bound a call and a second cap would stop good work again.

## Guard

`tests/test_stall.py`, `tests/test_engine_skeleton.py`, `tests/test_sdk_adapter.py`, `tests/test_project_settings.py`, `tests/test_graph.py`, `tests/test_dispatch.py`. A change to this rule changes its tests in the same commit (ADR-0018).
