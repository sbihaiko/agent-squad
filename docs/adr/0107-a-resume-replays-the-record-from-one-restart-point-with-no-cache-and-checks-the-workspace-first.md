# ADR-0107 — A resume replays the record from one restart point, with no cache, and checks the workspace first

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #17). AGREED on the replay, the same run id, the appended `resume` event, and refusing a changed graph. AGREED 2–1 on no cache key, on a clean workspace, on the verdict of a workspace mismatch, and on which runs may resume. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the splits only.

Amends [ADR-0105](0105-a-loop-s-outcome-follows-control-flow-the-verdict-module-folds-the-record-and-a-deadline-adds-no-exit-code.md) rule 7: one `verdict` event per invocation, not per run.


Amended 2026-09-30 by [ADR-0114](0114-the-hash-covers-the-authored-document-a-resume-replays-a-frontier-and-a-dispatch-folds-like-each.md): a resume replays a frontier when a dispatch holds parallel children (rules 1, 2, and 7 here).

## Context

ADR-0102 ends the process at a wait node and resumes it on a later invocation. It starts a fresh session on every entry and never resumes an SDK session. The run record is truth (ADR-0019). ADR-0106 logs the graph hash and the engine options in `run_start`. dev_squad's resume (T20) used a content-keyed journal and a `resumeEpoch` that cache-busted one node's prompt (`docs/research/dev-squad-adaptivity.md`, section 2.1 and section 7 item 8). Ticket #18 holds the open verdict of a stopped run.

## Decision

1. **`resume <run-id>` reads the run record and the target.** It refuses a run whose `run_start` graph hash differs from the graph file. It replays the graph walk over the record: it rebuilds each loop's entry count and each node's last outcome from the `outcome` events, and it replays the applied proposals onto the graph (ADR-0103 rule 5). It reads every `when` again on entry and never takes one from the old record.
2. **The restart point is one position, or a frontier when a dispatch holds parallel children (ADR-0114 rule 3).** A wait node restarts at that wait. A stopped run restarts at the node after the stop node (ADR-0106 rule 2). Otherwise the restart point is the next node the replayed walk would enter, which is the first node with no exit event in its current entry.
3. **There is no per-node cache key.** Nothing at or after the restart point has an exit event in the current entry, so nothing is skipped. An artifact before the restart point stands because its `outcome` event names it, and the preflight checks that it is present. A missing artifact is a defect and the verdict is `failed`. There is no `resumeEpoch`.
4. **`--from <node name>` is an engine option.** It moves the restart point back to a validated node name. Everything from that node on runs again. The `resume` event logs it.
5. **The preflight checks each workspace that a node about to run will use.** The workspace exists, is on the HEAD that the `outcome` event of its action node recorded, and is in the state that `outcome` event recorded (HEAD, status, and diff digest). A missing worktree or a missing git gives `failed`. A moved HEAD, or a dirty workspace, gives `conflict`, exit 20, because the target holds something the run will not overwrite (ADR-0029 rule 3). The engine never stashes, recreates, repairs, or discards a person's change. The person commits or discards, then resumes.
6. **A run may resume when its root loop has no `outcome` event.** That holds after a wait, after a `--stop-after` stop, after a preflight `failed` or `conflict`, and after an abandoned run (ADR-0051 rule 4). A `conflict` resumes only when the preflight no longer observes it. When the root has an exit event, the graph ran to its end and a new run starts. A person who wants a redo edits the graph.
7. **A resume keeps the run id.** It appends one `resume` event to the same record: the invoker, the engine options, the restart position (a list of frontier positions when a dispatch exists), the preflight observation, and the declared default if the wait's deadline had passed (ADR-0102 rule 6). The record holds one `verdict` event per invocation, and the fold takes the last.

## Considered options

- **A per-node cache key** (Lens A, dissent). Lens A: skip a node with a `pass` outcome only if its key matches: the effective definition after proposals, the input artifact hashes, the fact source versions, and the workspace start state. `--rerun` invalidates the entry and its consumers. Rejected 2–1: the key is a second truth beside the record (ADR-0019 rule 1), and a linear restart has nothing to skip. Reopen if a resume must skip a node that already holds an exit event in its current entry, for example when parallel `each` items partly passed. The cache would then be that item's `outcome` event.
- **A dirty workspace that is unchanged stays valid** (Lens A, dissent). Lens A: record file-state hashes at the pause, and a mismatch stops for a person. Rejected 2–1: uncommitted files are never truth, and file state is git (`docs/research/sdk-per-node-primitives.md`). Reopen if a graph needs non-git scratch files to survive a pause.
- **Every mismatch is needs-human** (Lens C's alternative option). Rejected: needs-human needs an owner and a declared default (ADR-0029 rule 7), and a preflight mismatch has none.
- **Stash a dirty workspace or recreate a missing one.** Rejected: the engine would guess and drop a person's change (ADR-0029).
- **Resume after `failed` never; a repair starts a linked new run** (Lens A, dissent). Rejected 2–1: the record, not the verdict label, says whether the graph ran to its end, and a preflight `failed` or a run deadline leaves the root open. Reopen if a run must resume after its root folded fail without a graph edit. If issue #18 rules a stopped run `completed`, rule 6 needs a stop-specific clause.
- **A new run id that links to the old one.** Rejected: a run is one execution of one graph version with one record.
- **Resume anyway after a graph change.** Rejected: a node has no identity across edits.
- **The replay reads the target's git log to find the position.** Rejected: truth is the record, and the target is read for `conflict` only.
- **`resumeEpoch`.** Rejected: dev_squad's one-node cache-buster in a prompt is the D13 pattern that ADR-0106 retired.

## Consequences

GLOSSARY.md gains `Resume`, and the `Run record` entry names the `resume` event. The map's fog line about persistence across hosts stays: a workspace on another host fails the preflight. Ticket #18 may change rule 6.

Amended 2026-09-30 (ticket #42): a run that hit its run deadline wrote a root `outcome` event, so it ran to its end and a new run starts. Rule 6 no longer lists the run deadline.

Amended 2026-09-30 (ticket #33): rule 5 says "in the recorded state", not "clean". An agent that ran before the stop leaves its own edits in the workspace. A clean check would call that a conflict. A change made after the stop differs from the recorded state and is a conflict.

## Guard

Tests that guard this rule:

- `tests/test_verdict.py::test_a_preflight_failure_and_conflict_on_resume_fold_to_failed_and_conflict`
- `tests/test_resume.py::test_a_resume_with_an_answer_continues_under_the_same_run_id`
- `tests/test_resume.py::test_a_resume_before_the_deadline_with_no_answer_halts_again`
- `tests/test_resume.py::test_a_resume_after_the_deadline_applies_the_declared_default`
- `tests/test_resume.py::test_a_resume_refuses_a_changed_graph_and_writes_no_event`
- `tests/test_resume.py::test_a_resume_refuses_a_run_whose_root_has_an_exit_event`
- `tests/test_resume.py::test_a_missing_artifact_fails_the_resume_at_preflight`
- `tests/test_resume.py::test_from_forces_a_live_run_from_that_node`
- `tests/test_resume.py::test_from_refuses_an_unknown_node`
- `tests/test_resume.py::test_a_resume_with_a_changed_workspace_is_a_conflict_and_runs_nothing`
- `tests/test_resume.py::test_a_resume_with_a_moved_head_is_a_conflict`
- `tests/test_resume.py::test_a_resume_with_a_missing_worktree_fails`
- `tests/test_resume.py::test_a_resume_with_an_untouched_workspace_continues`
- `tests/test_adapt.py::test_a_replayed_proposal_changes_the_effective_node_never_the_graph_or_its_hash`
- `tests/test_adapt.py::test_a_resume_from_a_node_does_not_replay_what_that_node_applied`
- `tests/test_cli.py::test_resume_from_a_node_and_an_answer_are_recorded`
- `tests/test_five_phases.py::test_the_manual_merge_wait_stops_at_exit_10_and_a_resume_finishes_the_run`
- `tests/test_five_phases.py::test_a_wait_left_unanswered_fails_by_its_declared_default_after_the_deadline`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
