# Architecture Decision Records

Decisions for agent-squad. Numbers 0000 to 0062 are stable, so cross-references hold. Gaps are ADRs not installed.

## Index

| ADR | Decision |
|---|---|
| [0000](0000-adrs-are-guides.md) | ADRs are guides, not locks |
| [0001](0001-us-english-and-concise-ste100.md) | US English and Concise STE100 for all project output |
| [0003](0003-nothing-is-dropped-silently-three-buckets-at-the-tool-judgment-boundary.md) | Nothing is dropped silently: three buckets at the tool/judgment boundary |
| [0008](0008-one-observation-contract-between-the-deterministic-layer-and-the-model-layer.md) | One observation contract between the deterministic layer and the model layer |
| [0010](0010-the-agent-drafts-the-owner-s-control-is-approval-not-authorship.md) | The agent drafts; the owner's control is approval, not authorship |
| [0011](0011-if-it-is-hand-launched-in-a-chat-session-it-is-not-automated.md) | If it is hand-launched in a chat session, it is not automated |
| [0012](0012-a-gate-is-a-stop-condition-never-an-optimization-target-verify-the-instrument-first.md) | A gate is a stop condition, never an optimization target; verify the instrument first |
| [0016](0016-an-edit-changes-only-what-it-must-another-author-s-work-is-never-altered.md) | An edit changes only what it must; another author's work is never altered |
| [0018](0018-a-failing-gate-is-a-defect-to-investigate-expectations-are-re-anchored-never-retuned.md) | A failing gate is a defect to investigate; expectations are re-anchored, never retuned |
| [0019](0019-run-truth-lives-in-one-module-the-exit-code-is-a-pure-function-of-one-verdict.md) | Run truth lives in one module; the exit code is a pure function of one verdict |
| [0024](0024-ready-is-not-authorized-a-consequential-live-write-needs-an-explicit-per-action-grant.md) | Ready is not authorized: a consequential live write needs an explicit, per-action grant |
| [0025](0025-delegate-context-heavy-work-to-subagents-the-main-session-keeps-the-decision.md) | Delegate context-heavy work to subagents; the main session keeps the decision |
| [0026](0026-commits-and-pushes-are-automatic.md) | Commits and pushes are automatic; do not ask |
| [0028](0028-project-state-is-read-from-the-target-never-from-a-hand-written-file.md) | Project state is read from the target, never from a hand-written file |
| [0029](0029-needs-human-is-not-success-and-gets-its-own-exit-code.md) | Needs-human is not success, and gets its own exit code |
| [0030](0030-a-write-that-can-be-interrupted-is-written-atomically.md) | A write that can be interrupted is written atomically |
| [0051](0051-a-call-has-a-deadline-and-a-stuck-agent-is-found-without-a-caller.md) | A call has a deadline, and a stuck agent is found without a caller |
| [0062](0062-a-red-test-is-fixed-whoever-broke-it.md) | A red test is fixed, whoever broke it |
| [0100](0100-the-graph-has-four-node-kinds-and-a-gate-node-is-never-skipped.md) | The graph has four node kinds, and a gate node is never skipped (amended by 0101 and 0113: seven kinds; ticket #127: one runner per kind) |
| [0101](0101-two-more-node-kinds-each-and-action-a-backstop-is-a-fact-and-a-when-holds-one-predicate.md) | Two more node kinds, `each` and `action`; a backstop is a fact; a `when` holds one predicate |
| [0102](0102-a-node-is-one-fresh-session-with-a-deadline-in-a-declared-workspace-and-truth-is-the-run-record.md) | A node is one fresh session with a deadline, in a declared workspace; truth is the run record (amended: one runner per kind, ticket #127) |
| [0103](0103-six-setting-proposal-kinds-a-node-declares-its-own-range-and-a-rejected-proposal-changes-nothing.md) | Six setting proposal kinds; a node declares its own range; a rejected proposal changes nothing |
| [0104](0104-facts-come-from-a-closed-registry-a-proposer-sees-only-its-declared-facts-and-a-proxy-never-triggers.md) | Facts come from a closed registry; a proposer sees only its declared facts; a proxy never triggers |
| [0105](0105-a-loop-s-outcome-follows-control-flow-the-verdict-module-folds-the-record-and-a-deadline-adds-no-exit-code.md) | A loop's outcome follows control flow; the verdict module folds the record; a deadline adds no exit code |
| [0106](0106-a-run-option-is-graph-data-when-it-changes-a-node-otherwise-an-engine-option-recorded-at-run-start.md) | A run option is graph data when it changes a node; otherwise it is an engine option recorded at run start |
| [0107](0107-a-resume-replays-the-record-from-one-restart-point-with-no-cache-and-checks-the-workspace-first.md) | A resume replays the record from one restart point, with no cache, and checks the workspace first |
| [0108](0108-run-spend-is-a-measured-fact-a-cap-never-starts-an-entry-it-cannot-fund-and-the-floor-is-an-optional-gate.md) | Run spend is a measured fact; a cap never starts an entry it cannot fund; the floor is an optional gate |
| [0109](0109-an-adaptation-is-judged-by-a-read-only-report-for-a-person-never-by-a-fact-a-gate-or-a-revert.md) | An adaptation is judged by a read-only report for a person, never by a fact, a gate, or a revert |
| [0110](0110-round-one-has-no-cross-run-input-a-node-reads-declared-files-and-a-reopen-needs-five-conditions.md) | Round one has no cross-run input; a node reads declared files; a reopen needs five conditions |
| [0111](0111-the-editor-writes-what-a-person-authors-reads-the-record-and-shows-an-adaptation-as-a-run-only-overlay.md) | The editor writes what a person authors, reads the record, and shows an adaptation as a run-only overlay |
| [0112](0112-a-run-stopped-on-request-has-its-own-verdict-stopped-and-exit-code-30.md) | A run stopped on request has its own verdict, `stopped`, and exit code 30 |
| [0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md) | A dispatch node lets an agent request children inside an author-fixed envelope; code validates each request |
| [0114](0114-the-hash-covers-the-authored-document-a-resume-replays-a-frontier-and-a-dispatch-folds-like-each.md) | The hash covers the authored document; a resume replays a frontier; a dispatch folds like each |
| [0115](0115-control-is-order-based-and-a-next-takes-six-reserved-words-with-no-sibling-jumps.md) | Control is order-based; a `next` takes six reserved words; there are no sibling jumps |
| [0116](0116-every-implement-works-in-its-own-worktree-and-merges-back-into-main-when-it-finishes.md) | Every `/implement` works in its own worktree and merges back into main when it finishes |
| [0117](0117-the-editor-prepares-a-run-and-never-launches-it-approval-is-a-person-s-act-outside-the-graph-and-the-page-makes-no-model-call.md) | The editor prepares a run and never launches it; approval is a person's act outside the graph; the page makes no model call |
| [0118](0118-squad-dashboard-serves-one-run-folder-over-a-read-only-loopback-server.md) | `squad dashboard` serves one run folder over a read-only loopback server |
| [0119](0119-the-subject-answer-key-asserts-what-its-issue-text-states.md) | The subject's answer key asserts what its issue text states; the 24-issue set can pass together |
| [0120](0120-the-repository-is-left-clean-no-worktree-or-branch-outlives-the-work-it-was-made-for.md) | The repository is left clean; no worktree or branch outlives the work it was made for |
| [0121](0121-a-call-in-flight-records-where-it-has-got-to.md) | A call in flight records where it has got to, and a child card reads running like a node card |
| [0122](0122-the-dashboard-follows-a-live-run-by-push-and-re-reads-only-where-no-server-exists.md) | The dashboard page follows a live run by push, and re-reads only where no server exists |
| [0123](0123-one-test-call-over-twenty-seconds-fails-the-suite-unless-it-carries-a-reason.md) | One test call over 20 seconds fails the suite, unless it carries a reason |
| [0124](0124-the-dashboard-side-panel-is-an-inspector-that-starts-hidden-and-tools-live-in-the-top-bar-and-on-the-canvas.md) | The dashboard side panel is an inspector that starts hidden; tools live in the top bar and on the canvas |
| [0125](0125-the-dashboard-groups-the-children-of-one-dispatch-by-status-into-group-cards.md) | The dashboard groups the children of one dispatch by status into group cards |
| [0126](0126-a-corrupt-record-shows-its-valid-prefix-under-a-banner-an-expanded-group-stays-readable-a-square-is-a-keyboard-stop.md) | A corrupt record shows its valid prefix under a banner; an expanded group stays readable; a square is a keyboard stop |
| [0127](0127-a-plain-squad-run-writes-the-graph-it-ran-into-its-run-folder.md) | A plain `squad run` writes the graph it ran into its run folder |
| [0128](0128-the-dashboard-may-pause-and-resume-a-launched-run-through-a-control-file-and-the-run-server-gains-one-write-route.md) | The dashboard may pause and resume a launched run through a control file, and the run server gains one write route |
| [0129](0129-a-borrowed-skill-is-found-by-name-at-run-time-never-by-a-path.md) | A borrowed skill is found by name at run time, never by a path |
| [0130](0130-a-run-records-the-hash-of-every-skill-it-can-load-and-a-resume-refuses-on-drift.md) | A run records the hash of every skill it can load, and a resume refuses on drift |

## Adding an ADR

New ADRs start at 0100. A rule without a named guard test is a draft, per ADR-0018. Copy the format of any file here.
