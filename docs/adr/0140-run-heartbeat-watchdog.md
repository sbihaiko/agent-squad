# ADR-0140: A run whose heart stops is killed, and its record names the reason

## Status

Accepted.

## Context

A call has a deadline, and a monitor reports a call that runs past it (ADR-0051). A run can still wedge between two calls: its process is alive, it spends nothing, and it writes no event. Its own deadline is a long way off, so it holds its slot, its branch and its card while the loop does nothing with it. A loop that has stopped looks exactly like a loop with nothing to do.

The run's record alone cannot decide it. A long Bash call (a full test suite, a build) writes no record event for minutes while its process works, so a watchdog that reads the record alone kills healthy work.

## Decision

1. **A run is killed only when both signals hold.** The CPU time of its process tree has not moved for `HB_FLAT_MIN` minutes (default 5), and its record has taken no new event for `HB_RECORD_MIN` minutes (default 5). One signal is never enough.
2. **The CPU rule is the one already applied to a hung test host.** A tree that has not spent a CPU second for the flat window is not working, whatever its record says.
3. **The record rule reads the run's own clock**, the time of its last event. It is the second vote, not the first: it says the run has nothing to report while the CPU says it is doing nothing.
4. **A run whose process tree is empty is not this rule's work.** The dead-run pass names that case and its own reason.
5. **The record is written before the process dies.** The killed verdict carries the reason `killed: heartbeat_stale`, and `squad.killed` stays the one writer of a killed verdict.
6. **One line per kill**: the run id, the flat CPU time and the last node.
7. **A kill leaves the branch and the worktree exactly as they are.** The run's work is the landing pass's to read, never the watchdog's.
8. **A record that cannot be read is never a kill.** The pass skips it, and the next pass reads it again.
9. **A first pass only stamps.** Flatness is a comparison between two readings, so a run never dies on the pass that first sees it.

## Guard

`tests/test_heartbeat.py`, `contrib/squad-goal/scripts/test-heartbeat.sh`.
