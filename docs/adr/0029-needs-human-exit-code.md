# ADR-0029: Needs-human is not success, and gets its own exit code

## Status

Accepted.

## Context

Scripts and SDK runs work unattended, and other tools read their exit codes. An exit code that lies causes two faults. A command can fail to reach its target and still exit 0. A wrapper can treat every non-zero code as "not done" and retry work that already finished. A blocked run is not a defect: a person must act. If it shares the generic failure code, a retry loop follows.

## Decision

1. **One module owns the run verdict** (ADR-0019). The exit code is a pure function of that verdict. No wrapper infers a verdict from a signal.
2. **A timeout is not a verdict.** Before it classifies a run, the wrapper reads the target and checks whether the effect happened. The observed effect decides, not the way the process ended.
3. **Use one code per verdict.** Document them in `--help` and the README:
   - `0`: completed. The work finished. Its external effect is observed at the target.
   - `1`: failed. The script or its inputs are wrong. Investigate it (ADR-0018).
   - `10`: needs-human. Work remains. Only a person can unblock it. Record the reason.
   - `20`: conflict. The target holds something the script will not overwrite. Report it. Never resolve it by guessing.
   - `30`: stopped. The caller asked for the stop and the run is otherwise sound.
   - `40`: no-repro. The bug did not reproduce, so no work is wrong and the premise of the issue is. The caller scripts on it: the janitor counts no failed dispatch for the run and returns the card to the owner, and the dashboard refuses a request on a run that ended so (ADR-0019).
4. **Reserve `0` for a fully completed run.** No partial outcome uses it.
5. **Every non-zero verdict names what remains, by step.** A reader can tell "never ran" from "failed" and see where to resume.
6. **State the resume instruction in the message,** with the command verbatim.
7. **A `10` has an owner and a deadline.** An unanswered question is not a pause. When the deadline expires, the caller applies the declared default and records that it did so.
8. **On a board, a `needs-human` run that landed nothing keeps its card in the column its work reached, and the card carries the owner freeze marker** (`frozen:owner`). The operator reads the board to know what waits for them, and `needs-human` means they are the next actor. A column says where the work is; the marker says who acts next. A separate column for it would move the card backward and lose where the work reached (ADR-0143). One rule owns the column write, the board reconciliation (`janitor.py` rule R): it derives the column from the record of the newest run that holds the issue, and no other rule writes a loop-board card a run names. The same pass writes the marker once, as an issue label; the owner removes it, the pass never does. The freeze reads the verdict time alone: a rearm the owner writes later than the newest run's verdict lifts the freeze for that verdict, so the pass writes no card move and no marker, and it names the lift once per run verdict in the cycle log. A verdict later than the rearm freezes the issue again. The rearm never lifts the stop that keeps a stale work branch from reading as delivered work, and a label already on the issue keeps it. A writer that ends an issue needs-human outside a run verdict, such as the loop-caused requeue cap, adds `needs-owner` and writes no card; rule R turns that label into the marker. The dispatch release (rule O) drops the dispatch marker and names the column R wrote in its comment; it writes none of its own. The card cache follows the pass's own writes, so a later rule reads the column the pass set, never a stale read.

9. **A no-repro run returns its card to the owner the way a needs-human run does.** The run is no failed dispatch, so it counts toward no requeue cap. One comment on the issue names the run, its verdict and its reasons, which are the run's evidence. The card keeps the column its work reached and carries the owner freeze marker (`frozen:owner`); the pass never removes it. A rearm the owner writes later than the run's verdict lifts the freeze for that verdict. Every run surface paints the verdict in a neutral tone through the one run-state table, never the failure red. The Loop page counts the run in its own verdict row, apart from failed.

A new code needs an ADR that names the caller who scripts on it.

## Guard

`tests/test_verdict.py`, `tests/test_resume.py`, `tests/test_cli.py`; for the board column, `contrib/squad-goal/scripts/test-janitor-disp-expiry.sh`, `test-janitor-board-reconcile.sh` and `test-janitor-rearm-unfreeze.sh`.
