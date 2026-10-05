# ADR-0105 — A loop's outcome follows control flow; the verdict module folds the record; a deadline adds no exit code

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #14). AGREED on the verdict function, `conflict`, the deadline, the fold owner and its log, and the exit codes. AGREED 2–1 on how a loop's outcome is computed and on the shape of one fix and one recheck. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the two splits only.

Amended 2026-09-30 by [ADR-0107](0107-a-resume-replays-the-record-from-one-restart-point-with-no-cache-and-checks-the-workspace-first.md): a resume adds a verdict event.

Amends [ADR-0051](0051-a-call-has-a-deadline-and-a-stuck-agent-is-found-without-a-caller.md) rule 2: a deadline adds no exit code.


Amended 2026-09-30 by [ADR-0114](0114-the-hash-covers-the-authored-document-a-resume-replays-a-frontier-and-a-dispatch-folds-like-each.md): a `dispatch` node folds like `each` (rule 7); a dispatch entry with no accepted child folds to `fail`, reason `no_dispatch`.

## Context

ADR-0100 fixes four node outcomes. ADR-0029 fixes four verdicts and four exit codes. ADR-0019 gives the verdict to one module that reads the run record and the target. ADR-0102 makes the run record truth and joins `each` items by worst-of. dev_squad returned twelve statuses in five places (`docs/research/dev-squad-adaptivity.md`, defect D10). ADR-0051 rule 2 said a deadline should get its own exit code. ADR-0100 rule 6 and ADR-0029 refuse a fifth code, so that sentence is outgrown.

## Decision

1. **A loop's outcome follows control flow.** An entry that reaches the end of its ordered children is `pass`. A child's `next` of `exit:<outcome>` ends the loop with that outcome. A `continue` on a fail means that fail does not count toward the loop. A `repeat` past the loop maximum ends the loop `fail`, with the reason `loop_max_exhausted`. Worst-of belongs to `each` only (ADR-0102 rule 9). An earlier entry's fail does not count once a later entry passes.
2. **A fail that a `continue` drops stays in the record.** The author makes it count with `exit:fail` on that child, or with a later node whose `when` reads `outcome:<node>` (ADR-0104 rule 3).
3. **The verdict is a function of the root's outcome.** Root pass is completed. Root fail is failed. Root needs-human is needs-human. Root deadline is failed, with the reason `deadline`. A root deadline is classified only after the target is read: if the effect is observed, it is completed (ADR-0029 rule 2).
4. **`conflict` is derived from the target.** The verdict module reads the target (unmerged paths, a dirty base) and the run record, and returns `conflict` when the target holds something the run will not overwrite (ADR-0029 rule 3). It is not a node outcome. A graph field cannot set it, because run truth is not graph data (ADR-0019 rule 1).
5. **A deadline folds to fail and keeps its reason.** The exit code is the failed code. An author may route `next.deadline` to `continue`, `repeat` (a fresh session, ADR-0102 rule 4), or `exit:needs-human`. The default is `exit:deadline`.
6. **One verdict module folds; the engine routes.** The engine derives node outcomes and routes on them. Only the verdict module computes the run verdict, as a pure function of the run record and the target.
7. **The run record logs the fold.** Each node exit writes one `outcome` event: node, entry number, outcome, reasons as a list, the artifact it derived from, and the record position. This includes a loop and an `each`. Each invocation ends with one `verdict` event (ADR-0107: a resume adds a later one, and the fold takes the last): the verdict, the exit code, the reasons, what remains by step, the target observation it read, and the ids of the outcome events it folded.
8. **One fix and one recheck is a loop `[actor, gate]` with `max` 2.** Entry 1: the actor works, the gate fails, and `next.fail` repeats. Entry 2: the actor, in a fresh session with the findings read from the artifact, fixes once, and the gate rechecks once. A pass in entry 2 makes the loop pass. A fail in entry 2 exhausts the loop: `fail`, `loop_max_exhausted`. There is no third entry. The gate's first fail stays in the record and is not a verdict marker. An author who wants a person to decide places a wait after the loop, with a `when` that reads the loop's outcome. Else the run is failed and the verdict names what remains.
9. **Exit codes follow the verdict alone:** completed 0, needs-human 10, conflict 20, failed 1, stopped 30 (ADR-0112). No outcome and no reason changes a code.

## Considered options

- **Worst-of over the last entry's children** (Lens A, dissent on the loop rule). Lens A: fold only the children reached in the last entry, with fail, then deadline, then needs-human, then pass. Rejected 2–1: it would make a `continue` on a fail count anyway, and it gives a loop a second fold beside control flow. Reopen if a scenario needs a fail-and-continue child to fail the loop and `exit:fail` cannot express it.
- **A loop passes only if every entry passed.** Rejected: `repeat` is the reason an entry exists, and a later pass must clear an earlier fail.
- **Fix skipped on entry 1 by a `when`** (Lens A, dissent on the shape). Lens A: a loop `[fix, gate]` where entry 1 skips the fix because the product already exists. Rejected 2–1: it needs a `when` to skip a node, and the initial attempt sits outside the loop, so the remediation history scatters. Reopen if an initial draft needs a tool surface or budget that differs from its fix.
- **`conflict` as a fifth node outcome, or an author-mapped `verdict_on` field.** Rejected: outcomes are four, and a graph field would put run truth in graph data.
- **A deadline maps to needs-human by default.** Rejected: needs-human means only a person can unblock the run (ADR-0029), and a deadline is retryable. A graph-level default of `next.deadline: exit:needs-human` stays available through `next`.
- **The engine folds and the verdict module only checks.** Rejected: it recreates dev_squad's five status tables (D10).
- **A deadline gets its own exit code.** Rejected: ADR-0100 rule 6 and ADR-0029 hold four codes.

## Consequences

GLOSSARY.md changes the `Outcome` and `Verdict` entries. ADR-0051 rule 2 is rewritten. Ticket #17 reads the `outcome` and `verdict` events to resume. Ticket #16 inherits the rule that a run option changes no verdict.

## Guard

Tests that guard this rule:

- `tests/test_record.py::test_one_outcome_event_per_node_exit_holds_the_fold_fields`
- `tests/test_verdict.py::test_each_row_of_the_fold_gives_its_verdict_and_code`
- `tests/test_verdict.py::test_false_positive_a_fail_then_a_passing_recheck_is_completed`
- `tests/test_verdict.py::test_a_fail_that_a_continue_drops_does_not_fail_a_stopped_run`
- `tests/test_verdict.py::test_join_order_is_fail_then_deadline_then_needs_human_then_pass`
- `tests/test_engine_skeleton.py::test_actor_gate_loop_fails_once_then_passes_on_the_recheck`
- `tests/test_engine_skeleton.py::test_loop_exhaustion_is_fail_with_the_reason_loop_max_exhausted`
- `tests/test_engine_skeleton.py::test_next_words_continue_drops_a_fail_and_exit_fail_ends_the_loop`
- `tests/test_engine_skeleton.py::test_a_run_writes_one_run_start_and_one_verdict_event`
- `tests/test_deadlines.py::test_a_run_deadline_already_spent_gives_deadline_without_a_call`
- `tests/test_actions.py::test_a_dirty_base_is_a_conflict_before_any_node_runs`
- `tests/test_each.py::test_the_join_is_fail_then_deadline_then_needs_human_then_pass`
- `tests/test_resume.py::test_a_wait_ends_the_process_with_needs_human_and_exit_10`
- `tests/test_dispatch.py::test_the_dispatch_folds_under_its_join_rule`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
