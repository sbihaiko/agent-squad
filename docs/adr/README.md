# Architecture Decision Records

Decisions for agent-squad. A number is never reused. A deleted number was merged or outgrown; git keeps it.

An ADR holds a decision and its reason. It is not a ledger: no dates, counts, statuses, issue numbers, run ids or amendment logs ([ADR-0000](0000-adrs-are-guides.md)).

## Index

| ADR | Decision |
|---|---|
| [0000](0000-adrs-are-guides.md) | ADRs are guides, not locks |
| [0012](0012-gate-is-a-stop-condition.md) | A gate is a stop condition, never an optimization target; verify the instrument first |
| [0018](0018-failing-gate-is-a-defect.md) | A failing gate is a defect to investigate; expectations are re-anchored, never retuned |
| [0019](0019-run-truth-in-one-module.md) | Run truth lives in one module; the exit code is a pure function of one verdict |
| [0024](0024-live-write-needs-a-grant.md) | Ready is not authorized: a consequential live write needs an explicit, per-action grant |
| [0029](0029-needs-human-exit-code.md) | Needs-human is not success, and gets its own exit code |
| [0030](0030-atomic-writes.md) | A write that can be interrupted is written atomically |
| [0051](0051-call-deadline-and-stuck-agent.md) | A call has a deadline, and a stuck agent is found without a caller |
| [0062](0062-red-test-is-fixed.md) | A red test is fixed, whoever broke it |
| [0100](0100-node-kinds-gate-control.md) | The graph has seven node kinds, a gate is never skipped, and control is order-based |
| [0102](0102-node-is-one-fresh-session.md) | A node is one fresh session with a deadline, in a declared workspace; truth is the run record |
| [0103](0103-setting-proposals-and-facts.md) | Six setting proposal kinds over a closed facts registry; a rejected proposal changes nothing |
| [0105](0105-loop-outcome-and-verdict.md) | A loop's outcome follows control flow; the verdict module folds the record; a deadline adds no exit code |
| [0106](0106-run-option-graph-or-engine.md) | A run option is graph data when it changes a node; otherwise it is an engine option recorded at run start |
| [0107](0107-resume-replays-the-record.md) | A resume replays the record from one restart point, with no cache, and checks the workspace first |
| [0108](0108-run-spend-and-caps.md) | Run spend is a measured fact; a cap never starts an entry it cannot fund; the floor is an optional gate |
| [0109](0109-adaptation-report-no-cross-run.md) | An adaptation is judged by a read-only report; round one has no cross-run input |
| [0111](0111-editor-contract.md) | The editor writes what a person authors, reads the record, and shows an adaptation as a run-only overlay |
| [0113](0113-dispatch-envelope.md) | A dispatch node lets an agent request children inside an author-fixed envelope; code validates each request |
| [0116](0116-implement-in-a-worktree.md) | Every `/implement` works in its own worktree and lands in `dev` when it finishes |
| [0117](0117-editor-prepares-never-launches.md) | The editor prepares a run and never launches it; approval is a person's act outside the graph; the page makes no model call |
| [0118](0118-dashboard-server.md) | `squad dashboard` serves one run folder over a loopback server with one write route |
| [0119](0119-subject-answer-key.md) | The subject's answer key asserts what its issue text states, and the 24-issue set can pass together |
| [0124](0124-dashboard-page.md) | The dashboard side panel is an inspector that starts hidden; dispatch children group by status; a corrupt record shows its valid prefix |
| [0129](0129-skill-found-by-name.md) | A borrowed skill is found by name, and a run records the hash of every skill it can load |
| [0133](0133-a-defect-an-agent-finds-is-its-own.md) | A defect an agent finds is its own from that moment, whoever caused it and whenever it began |
| [0134](0134-stall-cap-counts-failures-without-evolution.md) | A cap counts failed attempts with an unchanged failure, never totals: a call's turns, a loop's entries |
| [0136](0136-a-defect-is-fixed-test-first.md) | A defect is fixed test-first, and the red run kept |
| [0137](0137-a-gui-keeps-its-rules-out-of-the-view.md) | A GUI keeps its rules out of the view |
| [0138](0138-a-profile-works-a-list-of-repositories.md) | A profile works a list of repositories, and one seam resolves every address |
| [0139](0139-a-test-without-its-fixture-repo-is-deselected.md) | A test whose fixture repository the runner does not own is deselected there, by name, and never skipped |
| [0140](0140-the-live-copy-follows-in-the-cycle.md) | The live copy follows the repository copy in the cycle, and the difference is read |
| [0141](0141-the-board-is-a-shipped-definition.md) | The board is a shipped definition, applied by one command; the definition names the vocabulary and the repository binds it to a carrier |
| [0142](0142-a-spec-is-never-work.md) | A spec is a document, never a unit of work: the dispatch skips every label a profile names as not-work, and only its tickets are children of a run; the column Todo is the owner's go signal |
| [0143](0143-a-card-only-moves-forward.md) | A card only moves forward along the axis, and the loop moves no card into Backlog or Todo except a run's own claim undone; Closed and Abandoned are terminal, and an old Status option is removed only once the migration plan has emptied it |
| [0144](0144-the-carrier-is-the-project-field.md) | The state of a card stays on the Project Status field; a write is sent only when the value changes |
| [0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md) | A work branch lands in `dev` by a fast-forward push, with no pull request; the owner promotes `dev` to `stg` and `stg` to `main`, and the live copy publishes from `stg`; a wave is one branch, a child enters it by a rebase and `--ff-only`, and CI runs once when the wave lands in `dev` |
| [0146](0146-a-loop-defect-is-fixed-outside-the-loop.md) | A defect in the loop's own machinery is fixed outside the loop: while an open `loop-bug` issue exists, every dispatch pass halts and names the issue, and closing it resumes dispatch with no other action |

## Adding an ADR

Take the next free number. Rewrite an ADR in place when a decision changes; never append an amendment. A rule with no named guard test is a draft ([ADR-0018](0018-failing-gate-is-a-defect.md)). Add one line to `CLAUDE.md` Design rules with the guard.
