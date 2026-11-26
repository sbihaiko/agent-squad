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
| [0135](0135-failed-run-evidence-lives-on-a-branch.md) | A failed gui-test run's screenshots live on a dedicated evidence branch |
| [0136](0136-a-defect-is-fixed-test-first.md) | A defect is fixed test-first, and the red run kept |
| [0137](0137-a-gui-keeps-its-rules-out-of-the-view.md) | A GUI keeps its rules out of the view |
| [0138](0138-a-profile-works-a-list-of-repositories.md) | A profile works a list of repositories, and one seam resolves every address |
| [0139](0139-a-test-without-its-fixture-repo-is-deselected.md) | A test whose fixture repository the runner does not own is deselected there, by name, and never skipped |
| [0140](0140-the-live-copy-follows-in-the-cycle.md) | The live copy follows the repository copy in the cycle, and the difference is read |
| [0141](0141-the-board-is-a-shipped-definition.md) | The board is a shipped definition, applied by one command; the definition names the vocabulary and the repository binds it to a carrier |
| [0142](0142-a-spec-is-never-work.md) | A spec is a document, never a unit of work: the dispatch skips every label a profile names as not-work, and only its tickets are children of a run; the column Todo is the owner's go signal |
| [0143](0143-a-card-only-moves-forward.md) | A card only moves forward along the axis, and the loop moves no card into Backlog or Todo except a run's own claim undone; Closed and Abandoned are terminal, and an old Status option is removed only once the migration plan has emptied it |
| [0144](0144-the-carrier-is-the-project-field.md) | The state of a card stays on the Project Status field; a write is sent only when the value changes |
| [0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md) | A work branch lands in `dev` by a fast-forward push, with no pull request; `dev` reaches `stg` by one pull request, the loop moves `main` to a `stg` sha whose complete tier is green, and the live copy publishes from `stg`; a wave is one branch, a child enters it by a rebase and `--ff-only`, and CI runs once on the `dev` → `stg` pull request |
| [0146](0146-a-loop-defect-is-fixed-outside-the-loop.md) | A defect in the loop's own machinery is fixed outside the loop: while an open `loop-bug` issue exists, no new dispatch, autobug or review starts and the pass names the issue, an approved and gated wave still lands unless the defect names the landing path, and closing the issue resumes dispatch with no other action |
| [0147](0147-one-model-registry.md) | One model registry names every model; one resolver maps a raw spelling to a route; a chain is a list of registry ids |
| [0148](0148-bug-first.md) | Zero bugs: a bug is filed at once with its card in Todo, and the loop dispatches bugs before features; `autobug.sh` runs before `autofrontier.sh`, and a fresh count of startable bugs with no run holds the frontier pass |
| [0149](0149-one-writer-per-loop-fact.md) | The per-issue ledger is the single source of an issue's loop facts; every writer submits events through one transition function; a missing or corrupt ledger holds dispatch for that issue; a rearm is a floor; a marker prefix is deleted only after its readers and writers moved and replay is verified |
| [0150](0150-a-run-never-holds-conflicting-siblings.md) | A run never holds two children of one spec, nor two issues whose bodies name the same repository-relative file; a deterministic check before dispatch enforces it, and conflict handling at landing stays; the file check crosses runs, so the bug lane holds a bug whose file a live run edits |
| [0151](0151-a-review-streak-escalates.md) | Three consecutive CHANGES verdicts send an issue to needs-human; identical findings twice stop the fix cycle and the next attempt uses a stronger coder; Debt or nit findings never count as CHANGES; an approval binds to the reviewed commit; a run that reviews its own children owns them |
| [0152](0152-reach-the-web-surfaces-over-the-tailnet.md) | The web surfaces keep their loopback bind; the hub is the one tailnet entry, through one owner-set `tailscale serve`; a tailnet request needs an allowed login, and a write also the tailnet origin; a write that acts on the machine also needs its own owner grant, off by default; the hub serves no session route and no terminal route; no credential is stored |
| [0153](0153-the-full-suite-runs-nightly-in-both-modes.md) | The full suite runs nightly in both modes, heavy included, in its own tree and its own lock, never the landing lock; one atomic result record carries the failing tests to the hub, a red result alerts, and a red result holds delivery until one green run |
| [0154](0154-the-owner-talks-to-the-operator-through-slack.md) | Slack is the one conversation channel: the `voiced` daemon turns each owner message into one inbox line and posts each answer into the owner's thread, and the session reads the inbox through a Monitor; the hub sets the channels and serves no session route and no terminal route; no session wire protocol exists |
| [0155](0155-a-premise-is-checked-never-assumed.md) | A premise is checked, never assumed: one round proves what reading proves, sends what only running proves to a probe, and asks the owner at most five questions, each with a recommended answer that "yes" accepts; a question that depends on an open question waits a round, the check is one comment on the spec rewritten in place, and a model with no tools never reviews. |


| [0158](0158-a-file-batch-is-capped.md) | A file batch is capped at two tickets by default: the cap splits a cluster in ascending number order and drops no ticket, so one large file never becomes one run that dies out_of_budget |
| [0159](0159-a-not-startable-marker-names-its-build.md) | A not-startable marker names the build that wrote it: the same build keeps the stop, another build re-arms it, and a marker that names none keeps the stop |
| [0160](0160-a-model-omitted-is-investigated-and-recovered.md) | A model omitted from a chain is investigated and recovered: the omission names its cause, the lane is probed before the window ends, and only a real answer clears the streak |
| [0161](0161-a-chain-entry-shows-its-state-as-a-tag.md) | A chain entry shows its live state as a tag: one read joins the configuration and the state, so an omitted model keeps its place and names its cause |
| [0162](0162-the-hub-writes-the-loop-switch-and-drains-by-default.md) | The hub writes the loop switch through one route: drain is the default and waits for live runs and gates, stop-now confirms what it kills, every change is audited, and a tailnet write needs `tailnet.loop_write` |

| [0163](0163-a-proof-is-bounded-at-fifteen-minutes.md) | A proof is bounded at fifteen minutes and holds no dispatch: past the deadline the suite is stopped and the tip waits for a retry, a named reserve lifts past its bound, and the CI tiers and the nightly carry what the local proof does not reach |

| [0164](0164-delivery-outranks-the-proof.md) | Delivery outranks the proof: an unfinished proof never holds a landing or a promotion, a failed one always does, and the record names which of the three a promotion was |
| [0165](0165-a-dispatch-waits-for-its-card-write.md) | A dispatch's card write is an event held pending until it lands; no run starts while it is pending, and recovery writes it before anything launches |
| [0166](0166-a-run-past-thirty-minutes-is-taken-over.md) | A run with no progress for thirty minutes is taken over, never requeued: a gate-queue wait is no run time, and the loop ends a stuck run and keeps its issues claimed behind a `taken-<run>` marker until a hand fix closes them |
| [0167](0167-a-finished-run-frees-its-run-slot.md) | A finished run frees its run slot: the loop counts every live lease and every listed run whose last invocation has no verdict, a verdict before a later start is history, and the ceiling never widens |
| [0168](0168-a-gate-stage-reuses-a-green-verdict.md) | A gate stage reuses a green verdict for its tree and its suite: the suite is what the stage runs, never the id of its check; the tree is its commit and a digest of its uncommitted state, read from `porcelain -z`; the fast-gate memo spans callers and processes and holds no base commit; and the reuse is a recorded `gate_reuse` event, never a silent skip |
| [0169](0169-a-gate-runs-the-test-scope-its-stage-names.md) | A gate runs the test scope its stage names: an intermediate stage runs the delta the change reaches, the landing gate, the promotion proof and the nightly run the full battery, an unknown stage is full, a delta that cannot cover the change falls back to the full battery with its reason, and the record states the scope |

| [0170](0170-the-gate-is-a-column-and-an-attempt-count-is-a-board-fact.md) | The gate is a column, Testing, between Batching and Closed, and its attempt count is one number field: a red or unproven attempt is a marker, never a move, the definition, the pinned copy and the hash move in one step, and a retired option goes only when no card carries it |
| [0171](0171-a-loop-defect-is-reviewed-by-fable-or-astra.md) | A loop defect is reviewed by Fable or Astra before it is built: its chain starts with Fable, then Astra, and the review chain follows; a `loop-bug` label selects it; autobug builds a loop-bug only after an approving plan review of its current body |
| [0172](0172-a-blocking-finding-names-its-repro.md) | A Blocking finding names a repro run at the head: a repro that exits 0 or cannot run moves the finding to Debt, a finding with no repro is Debt, and a markdown answer has none (amends 0151) |
| [0173](0173-the-complete-tier-runs-on-the-promotion-request.md) | The complete tier runs on the promotion request: the fast tier runs on the dev-to-stg pull request and moves `stg`, the complete tier runs on the stg-to-main pull request on the `promote` label through a guard that starts it once per tip, and the loop moves `main` by a fast-forward to the sha whose complete tier is green; a red files one bug, an unproven tier is an alert |
| [0174](0174-the-landings-gate-is-first-in-the-queue.md) | The landing's gate is first in the queue: a child's queued fast gate stands aside for it, a try-mode gate answers 73 at once, and a priority marker whose pid is gone or that is past `SERIAL_PRIORITY_MAX` names nobody |
| [0175](0175-a-timeout-a-suite-explains-is-unproven.md) | A timeout that a suite of the same tree explains is unproven: the runner names its held gate slots so the suite takes none twice, the cap clock never stops, and a history row carries the tree sha |
| [0176](0176-two-landings-run-at-once-and-git-arbitrates-the-push.md) | Two landings run at once and git arbitrates the push: `single.sh` takes one of `SG_SINGLE_SLOTS` slots, a landing passes two, and the refused push repeats from its fetch |
| [0177](0177-slack-rules-in-the-project.md) | A project commits `slack.yaml` and the profile keeps `slack.local.yaml`: one pure module at `squad/slackpolicy.py` validates both and names each failing key, `squad slack check` reports them, a token, an unsafe persona name, a foreign voice, an over-long tone, a foreign language, a guest id, `prod` and a `secret` key are refused, and a commit guard refuses a local file |
| [0178](0178-an-operator-session-is-bounded-by-what-it-can-do.md) | An operator session is bounded by the calls it can make, never by a filter of message content: the committed Slack file holds an optional guard block, `voiced` refuses a flooding or oversized message before the inbox write and names the reason class, every message writes one redacted audit line, and a `PreToolUse` hook decides every tool call of a project session and fails closed |
| [0179](0179-a-project-declares-the-command-of-each-action.md) | A project declares the command of each action under `project.actions`; the session writes one request line, and the executor runs the declared command outside the session, never a command built from a message |

## Adding an ADR

Take the next free number. Rewrite an ADR in place when a decision changes; never append an amendment. A rule with no named guard test is a draft ([ADR-0018](0018-failing-gate-is-a-defect.md)). Add one line to `CLAUDE.md` Design rules with the guard.
