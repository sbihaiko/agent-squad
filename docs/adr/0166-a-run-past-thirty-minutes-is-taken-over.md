# A run with no progress for thirty minutes is taken over, never requeued

A run whose record has not moved for thirty minutes is ended by the loop and handed to a person or an agent. Its issues
stay claimed. The loop never puts them back in the queue, so no fresh child starts the same work again.

The bound measures work, never the wall clock since the start. Every node writes a record event as it works: a call, a
message, a gate, an outcome. A run that keeps writing is working, however long it takes. A run that stops writing for the
bound is stuck, and a takeover is its end.

A run waiting for a gate slot writes no progress and is not stuck. It waits for a machine-wide resource it does not
control: the serial gate lock and the gate slots. At gate contention that wait alone can pass the bound. The run's work is
already on its branch, and a takeover would throw an approved change away. The janitor reads the process table: a gate
process alive inside the run folder means the run is waiting its turn or running its gate, so the bound does not apply.

A requeued run throws away its context. The child that ran for half an hour read the issue, the code and its own failed
attempts; a fresh child starts from none of that, and often runs into the same wall. Each requeue spends a run slot and
model spend on work the loop already did once, and the loop goes in circles while the card shows activity. Delivery is the
goal, and a run that cannot finish inside the bound is a delivery problem that a takeover solves faster than a restart.

The cut is hard once the run is stuck. A run whose record has not moved for thirty-one minutes is ended even when its
session is alive. The gate wait is the only condition, and it is the one the run does not own; no other state keeps a run
past the bound for ever.

The claim must be visible. A run that left active-runs with its dispatch marker still set looks like a stale claim, and
the cleanup rules release a stale claim. The takeover marker names the run and its issues, so the owner sees which issues
a hand fix owes, and the loop holds them while the marker stands.

## Decision

1. A run with no verdict whose record has not moved for more than `SG_TAKEOVER_SECONDS` (default 1800), and whose folder
   holds no live gate process, is taken over by the janitor (rule T). A stamped record event is progress; the run start is
   the fallback when no event carries a readable stamp.
2. The janitor writes the killed verdict into the run's record, kills every process inside the run folder and the run's
   dashboard, and drops the run from active-runs.
3. The janitor never frees `auto/disp-<n>` or `auto/bug-<n>` of a taken-over run, so its card stays where the run left it
   and its work branch stays as the child left it.
4. The janitor writes `auto/taken-<run>`, which names the run id and its issues, and appends one `TAKEN-OVER` line to
   `alerts.log`.
5. While `auto/taken-<run>` stands, its issues are held: no cleanup rule puts their cards back in Todo or drops their
   dispatch markers.
6. Whoever takes over finishes the work with a multi-agent workflow, and clears `auto/taken-<run>` when the last issue of
   that run is closed.

## Consequences

- A run that keeps writing progress past thirty minutes is not ended; a run that stops writing for thirty minutes is.
- A run that waits in the gate queue keeps its work: the wait is no run time, and the run resumes when its turn comes.
- A run that never leaves one long call records no event, so it reaches the bound on that call. A takeover starts from the
  run's branch, so the work is not lost.
- Rule C still reaps a zombie run that dies inside the bound, and frees its markers: a run that dies early has little
  context to lose.
- A taken-over issue does not move until the marker is cleared. A marker nobody clears holds its issues for good, and
  `alerts.log` is where the owner finds it.
- A test fixture that builds a live run with an old stamp and no later event is a taken-over run. A suite that tests
  another rule sets `SG_TAKEOVER_SECONDS` high, records a progress event now, or leaves a gate process in the folder.

Guard: `tests/test_janitor_takeover.py`, `tests/test_adr_index.py`.
