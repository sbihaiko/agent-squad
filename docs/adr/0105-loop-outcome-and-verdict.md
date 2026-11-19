# ADR-0105: A loop's outcome follows control flow; the verdict module folds the record; a deadline adds no exit code

## Status

Accepted.

## Context

The run needs one fold from node outcomes to a run verdict and an exit code. Several folds in several places gave dev_squad twelve statuses in five places. [ADR-0100](0100-node-kinds-gate-control.md) fixes four node outcomes. ADR-0029 fixes the verdicts and exit codes. ADR-0019 gives the verdict to one module.

## Decision

1. **A loop's outcome follows control flow.** An entry that reaches the end of its ordered children is `pass`. A child's `next` of `exit:<outcome>` ends the loop with that outcome. A `continue` on a fail means that fail does not count toward the loop. `max` entries in a row that repeat with the same failure end the loop `fail`, reason `loop_max_exhausted`; an entry whose failure reads differently restarts the count ([ADR-0134](0134-stall-cap-counts-failures-without-evolution.md)). Worst-of belongs to `each` only. A later pass clears an earlier fail.
2. **A fail that a `continue` drops stays in the record.** The author makes it count with `exit:fail` on that child, or with a later node whose `when` reads `outcome:<node>`.
3. **The verdict is a function of the root's outcome.** Root pass is completed. Root fail is failed. Root needs-human is needs-human. Root deadline is failed, reason `deadline`, but only after the target is read: if the effect is observed, the run is completed (ADR-0029 rule 2).
4. **`conflict` is derived from the target.** The verdict module reads the target (unmerged paths, a dirty base) and the run record. It returns `conflict` when the target holds something the run will not overwrite. It is not a node outcome. A graph field cannot set it.
5. **A deadline folds to fail and keeps its reason.** It adds no exit code. An author may route `next.deadline` to `continue`, `repeat` (a fresh session), or `exit:needs-human`. The default is `exit:deadline`.
6. **One verdict module folds; the engine routes.** The engine derives node outcomes and routes on them. Only the verdict module computes the run verdict, as a pure function of the run record and the target.
7. **The run record logs the fold.** Each node exit writes one `outcome` event: node, entry number, outcome, reasons as a list, the artifact it derived from, and the record position. Each invocation ends with one `verdict` event: the verdict, the exit code, the reasons, what remains by step, the target observation, and the ids of the outcome events it folded. A resume adds a later `verdict` event, and the fold takes the last. A `dispatch` folds like `each`. A dispatch entry with no accepted child folds to `fail`, reason `no_dispatch`.
8. **One fix and one recheck is a loop `[actor, gate]` with `max` 2.** Entry 1: the actor works, the gate fails, `next.fail` repeats. Entry 2: the actor, in a fresh session with the findings read from the artifact, fixes once, and the gate rechecks once. A pass makes the loop pass. Two alike fails exhaust the loop: `fail`, `loop_max_exhausted`. A second fail that reads differently from the first is evolution and earns a third entry. The gate's first fail stays in the record. An author who wants a person to decide places a wait after the loop, with a `when` that reads the loop's outcome.
9. **Exit codes follow the verdict alone:** completed 0, failed 1, needs-human 10, conflict 20, stopped 30, no-repro 40. No outcome and no reason changes a code.
10. **A run stopped on request is `stopped`, exit 30,** reason `stopped_after:<node>`. A truncated run folds first as usual: a failed node gives `failed`, a conflict at the target gives `conflict`, a wait that holds gives `needs-human`. Only an otherwise sound run gives `stopped`. `stopped` is not success: exit 0 stays reserved for a fully completed run. The message names what remains by step and the resume command. A stopped run resumes (ADR-0107).
11. **A call in flight records progress.** The adapter writes at most one `progress` event per call per `progress.seconds` (a setting; `null` writes none). The event carries the newest reading of the call (messages so far, context size, last tool), never a sum. An unreported figure is null, never zero. The dashboard reads a child card in flight with the same rule as a node card.
12. **A run whose process is gone gets a `failed` verdict, reason `killed/no-process`, or `killed/signal:<NAME>` when a signal ended it.** An engine appends a `signal` event (the signal, its pid, parent pid and process group, and the time) before a SIGTERM, SIGHUP or SIGINT ends it, so the verdict names that signal. The reaper keeps `reap-evidence.json` beside the record: the time it found the run gone, and the live kill commands and parentless temp-folder scripts of that moment. A run that is killed (the operator, a restart, a crash) never reaches its own fold, so its record would end with no verdict, and a reader would see a stalled run for ever. `squad.verdict.killed` builds that verdict, and `squad.killed` is the one writer: it appends the verdict to the record with the record's own atomic append (ADR-0030), and writes nothing when the record already ends in a verdict. The heartbeat and the janitor call it when no process of the run is left. A reader that finds a run with no verdict, past the stall limit and with no process of its own, does not count it as live.

13. **A reason a model writes about itself is a claim, never a budget fact.** The claim map reads the reason as a
    prefix, so `out_of_budget` and `out_of_budget: <the child's words>` are one claim, `child_said_out_of_budget` with
    the child's words kept after it. A run read the child's sentence as its own budget verdict and ended with $39 of a
    $40 cap unspent (#960). The engine's own cap reasons (`CAP_REASON`) stay apart from every claim.

Rejected: a deadline with its own exit code, because the codes are a contract (ADR-0029). Rejected: `conflict` as a graph field, because run truth is not graph data.

## Guard

`tests/test_verdict.py`, `tests/test_record.py`, `tests/test_engine_skeleton.py`, `tests/test_dashboard_live_call.py`, `tests/test_sdk_adapter.py`, `tests/test_killed.py`, `contrib/squad-goal/scripts/test-heartbeat.sh`, `contrib/squad-goal/scripts/test-janitor-killed.sh`, `contrib/squad-goal/scripts/test-hub-killed.sh`. A change to this rule changes its tests in the same commit (ADR-0018).
