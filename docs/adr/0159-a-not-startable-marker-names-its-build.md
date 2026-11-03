# ADR-0159: A not-startable marker names the build that wrote it, and another build re-arms it

## Status

Accepted. Owner answer B2, 2026-10-31, after a marker written before a fix kept a ticket out of the frontier.

## Context

When an issue fails the same way three times with no change in its work, the loop writes one `SQUAD-NOT-STARTABLE`
comment, and every dispatcher reads that comment before it starts the issue (`wave.sh`, `health.sh`, `sg_not_startable`
in `startability.sh`, and the list read in `ghread.py`). The marker has no expiry: a loop fix landed, the ticket's
cause was gone, and the marker still held it out. The owner read that as a count of failures from code that no longer
runs.

## Decision

1. **A marker names the build that wrote it.** The line reads `SQUAD-NOT-STARTABLE: build=<sha> ...`, and the sha is the
   checkout the loop publishes from (`sg_loop_build`, `loop_build`).
2. **The same build keeps the stop.** A dispatch reads the marker of its own build as the stop it is.
3. **Another build re-arms it.** A marker written by a different build holds nothing, and the `frozen:not-startable` label
   beside it holds nothing either: a landing may be the fix, and a failure count from before it is not evidence about the code
   that runs now.
4. **Neither an unknown build nor a marker that names none proves anything, so each keeps the stop.** An unreadable
   checkout, and a marker written before this rule, keep the stop they always had. The `frozen:not-startable` label is the
   same stop: it holds an issue unless a marker of another build re-arms it. To re-arm an issue sooner, the owner removes the
   comment and the `frozen:not-startable` label, or runs `issue_ledger.py rearm`.
5. **Both readers hold one rule.** The list read (`ghread.py`) and the per-issue read (`startability.sh`) answer alike,
   because a dispatcher that reads one list and one comment must not see two verdicts.

## Consequences

A landing re-arms every marker at once. An issue that genuinely fails three times per build is dispatched once per build
and then stops again, at the cost of one run per landing; that cost is what the owner chose over a marker that never
expires.

## Guard

`tests/test_not_startable_build.py` guards rules 1 to 5 for both readers, and rule 4 names the label: this build keeps the
stop, another build re-arms, a marker that names none keeps the stop, an unknown build keeps every stop, and a note that is
not the marker is startable. The label beside a marker is guarded by `contrib/squad-goal/scripts/test-autofrontier-one-run.sh`
and `test-autobug.sh`; the re-arm line by `test-health.sh`; the label's removal by `test-cqflush-status.sh`. The wave stop's
writer is guarded by `contrib/squad-goal/scripts/test-wave-requeue-cap.sh` (the queued marker names a build).
