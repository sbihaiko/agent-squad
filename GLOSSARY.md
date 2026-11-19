# agent-squad

agent-squad runs a self-adapting orchestration graph on the Claude Agent SDK. The graph is data. A model proposes changes to it at run time. Code validates, applies, and logs them.

## Language

### The graph

**Graph**:
The declared data document that the engine runs and the dashboard loads. It holds the nodes and their references.
_Avoid_: pipeline, workflow, spec, state machine

**Node**:
One unit of the graph. Every node has a name and one of seven kinds: agent, gate, wait, loop, each, action, dispatch.
_Avoid_: phase, step, stage, state, task

**Agent** (node kind):
A node that is one model session with a declared model, tool surface, and budget. It may carry `instructions`, the role text the model reads before its inputs.
_Avoid_: LlmAgent, worker, role

**Gate** (node kind):
A deterministic check node with no model call. Its thresholds never change at run time, and it never carries a `when`. A gate stops or repeats; its fail never routes into another node. A backstop that decides whether a review runs is a fact, not a gate.
_Avoid_: check, guard, review node, backstop

**Wait** (node kind):
A node that holds the run until a person acts, with an owner and a deadline. At a wait the process ends with verdict needs-human and exit code 10; a later invocation resumes at the wait. Past its deadline, the declared default applies on resume.
_Avoid_: needs-human node, suspend, approval step, interrupt, poll

**Loop** (node kind):
A node that repeats its ordered children up to a declared maximum. The maximum may only shrink at run time. The root of a graph is a loop.
_Avoid_: retry, cycle, iteration, back-edge

**Each** (node kind):
A node that runs its ordered children once per item of a declared fact, in `mode` parallel or sequence, at most `max_parallel` at once. Its outcome is the join of the item outcomes under its `join` rule: `all` (fail, then deadline, then needs-human, then pass) or `first_failure`. It carries its own deadline; an item gets the lesser of its own and the remaining time. Parallel items need distinct workspaces.
_Avoid_: fan-out, batch, for-each, parallel node, map, strictBatch, parallelMode

**Action** (node kind):
Deterministic code with declared side effects: a commit, a worktree, a merge landing. No model, no spend. It may carry a `when`.
_Avoid_: script, hook, step, tool node

**Dispatch** (node kind):
A node that lets an agent request children at run time, inside an envelope that the author fixes. It names an entry agent, the envelope, and the `checks` gates that run after every child. A graph with no dispatch node is an authored flow; a root loop that holds one is a dynamic flow.
_Avoid_: router, orchestrator, spawner, planner

**Edge**:
A name reference from one node to another: a loop's ordered children, or a node's next node per outcome. Edges are not separate objects. Control is order-based: a child's `next` takes the reserved words `repeat`, `continue`, `exit:pass`, `exit:fail`, `exit:deadline`, `exit:needs-human`, and a `when` is never bypassed.
_Avoid_: dependency, link, transition, jump

**When**:
One predicate `{fact, op, value}` on a node, over a closed set of facts, that says whether the node runs. A gate never carries one. A compound condition is a derived fact, not a second predicate. A `when` on a loop skips the whole loop, gates included.
_Avoid_: condition, expression, rule, all, any

**Budget**:
The stall cap and the spend cap on a node or a run, each with a minimum and a maximum.
_Avoid_: cap, limit, quota, token floor

**Run spend**:
The money that agent entries have spent in one run, summed from the SDK result of each entry. It is a registered fact. The run block's spend cap minus it is the run remainder, and the engine passes the lesser of a node's cap and the remainder to each entry.
_Avoid_: cost, token count, usage

**Deadline**:
The wall-clock limit on one call or one run. Reaching it is its own outcome, not a failure. Agent, wait, action, and each nodes carry one; a gate and a loop do not. A run default applies where a node declares none. After a deadline, a repeat starts a fresh session.
_Avoid_: timeout, time limit

**Workspace**:
The directory an agent node runs in, named on the node and created by an action node as a git worktree. Two parallel items never share one. A dispatch child that may write gets its own worktree.
_Avoid_: sandbox, checkout, cwd (outside SDK options)

**Session**:
One `query()` call on the SDK: one CLI process, one transcript. Every entry of an agent node starts a new one. A transcript is never run truth.
_Avoid_: conversation, thread, context (as a noun for the call)

**Node runner**:
The code that runs one node kind: `async run(ctx, node, entry, sc)`, which returns the node result. The engine looks it up by kind in one registry. It keeps the scheduling rules (entry count, fast-forward, `when`). A new kind adds one runner.
_Avoid_: executor, handler, step

### Adaptation

**Proposal**:
A change to the graph that the model requests, in a fixed shape. Its states are proposed, validated, applied, or rejected.
_Avoid_: change request, suggestion, move

**Spawn request**:
A request in a dispatching agent's output for one child: a prompt, a skill, and optional narrower settings. Code validates it against the envelope, assigns its id, and logs `spawn_requested`, then `spawn_accepted` or `spawn_rejected`. It is not a proposal kind. A request over any limit is rejected whole.
_Avoid_: delegation, handoff, task, subagent call

**Skill hash**:
The tree hash of one vendored skill, from `vendor/skills/VERSIONS.json`. It is the skill's version. A run records the hash of every skill it can load, and a resume refuses when one differs.
_Avoid_: skill version, skill revision

**Reviewing**:
The board column for an issue whose child's code is done while its review, or a fix round, holds it. It sits between Doing and Batching, and a card in it never goes back to Doing; a requeue sends it to Todo.
_Avoid_: Review, Code Review (older names of the column), review wait (as a mark on Doing)

**Batching**:
The board column for a card whose child is in the wave branch: it waits for its siblings and for the run's end. It sits between Reviewing and Testing. Closed follows when the wave is in dev.
_Avoid_: Landing, Code Complete (older names of the column)

**Testing**:
The board column for a card whose gate or proof waits for a slot or runs for the wave. It sits between Batching and Closed, and a red or unproven attempt is a marker on the card, never a move out of the column. Its count of attempts is the card's Gate Tries number.
_Avoid_: Gating (the older name of the column), pushing (retired), gate wait (as a badge over Batching)

**Landing**:
Code merges a passing write child of a dispatch into the target, in request order, and logs a `land` event. A model never merges. A conflict fails that child with `merge_conflict`; the children that landed before it stay landed.
_Avoid_: merge step, integration child

**Delivered issue**:
An issue of a subject whose held-out acceptance test passes on the final target tree of the run. A landing records which child delivered it; it does not decide whether the issue is delivered.
_Avoid_: landed issue, done issue

**Ledger**:
The one input a dispatch entry agent reads of its children's work: a compact row per finished child with its task, outcome after landing, changed paths, checks, and landed HEAD. It holds no diff, test output or conflict text.
_Avoid_: history, summary, report

**Wave**:
One pass of a dispatch entry in a loop: a fresh router session reads the ledger, requests children, code lands them. The stop word `done` with no spawns ends the waves.
_Avoid_: round, batch, iteration

**Envelope**:
The limits on one dispatch node, fixed by the author: depth, children per entry, parallelism, deadline, join rule, the allowed models, efforts, tools, and pinned skills, the workspace scope, the child budget, and the `checks`. A model never widens it. A child gets the intersection of its parent's limits.
_Avoid_: policy, sandbox, guardrails

**Frontier**:
The children that a resume must run: every accepted child with no `outcome` event in its current entry. A crash between `spawn_accepted` and launch leaves a child in it.
_Avoid_: queue, backlog, pending list

**Replay cursor**:
The dashboard's read position in a run record: one record `pos`, from 1 to the last event. The dashboard folds only events up to the cursor into each card, keyed by node, entry, and item. It shows the record's verdict only at or after the verdict's `pos`. A view setting that changes no graph and no record. At the last event the cursor is pinned: a live run keeps it there until the owner moves it back.
_Avoid_: playback time, scrub position, snapshot

**Unresolved child**:
An accepted child with no `outcome` event at the cursor. The dashboard computes it from the record to inspect a run. It is not the engine's frontier: a `resume` event's recorded `frontier` also names authored nodes and items, and the engine marks a launched child with no outcome as crashed and does not run it again.
_Avoid_: pending child, queued child

**Reread interval**:
The seconds between two reads of `record.jsonl` while the dashboard follows a live run through a browser file handle. An engine setting that `run_start` records (`defaults.engine.dashboard.reread_seconds`). The page has no control for it, and uses one second when a record lacks it. A page served by the run server follows by push instead.
_Avoid_: refresh rate, poll period

**Setup**:
The one-time preparation of a project for the skills and the squad: the issue tracker and labels, `GLOSSARY.md`, `docs/adr/` with the default ADRs, `CLAUDE.md` with a bullet and a guard for each rule ADR, and `AGENTS.md` linked to it. `squad setup` plans, `--apply` adds what is missing, and `--check` reports drift. A rerun changes nothing it already made.
_Avoid_: init, bootstrap

**Watch**:
The dashboard and the monitor that start with a run (`watch.dashboard`, `watch.monitor`, `watch.monitor_seconds`). The monitor reads the run's record on a timer and writes each stuck, abandoned or deadline-missed finding once to `monitor.log` in the run folder. It never calls a model and never changes the run.
_Avoid_: tracking, polling

**Watch step**:
The seconds between two checks of `record.jsonl` while the run server holds a push stream. An engine setting (`defaults.engine.dashboard.watch_seconds`), passed to the server when `squad dashboard` starts it. Each new record event reaches an open page within one step of the write.
_Avoid_: poll interval, tick

**Call in flight**:
A call whose `call_start` has no later outcome. The engine writes a `progress` event for it now and then: the assistant messages so far, the context the newest message read, and the last tool the call used. A call with no such event is not known to be live.
_Avoid_: running call, active call, in-progress node

**Stalled**:
A display state of a run with no verdict and no new record event for longer than the longest call in flight may take, plus a grace (300 seconds when no call is in flight). It is not a gate and not a finding: a quiet gate command can look the same.
_Avoid_: hung, dead, crashed

**Proposal kind**:
One entry in the closed list of changes a proposal may ask for. Six exist: `set_model`, `set_effort`, `narrow_tools`, `shrink_stalls`, `shrink_spend`, `shrink_loop_max`. Each changes one setting on one node and never widens a bound. Skip and repeat are not kinds.
_Avoid_: proposal type, menu, move

**Adapt block**:
The part of a node that lists the proposal kinds it allows, with its model list, effort list, and tool subset. A node with no adapt block is not adaptable. The minimums live on the node's `budget` and `max`.
_Avoid_: policy, permission, override, allowlist

**Adaptation**:
A proposal that code has applied to the running graph.
_Avoid_: mutation, tweak, override

**Adaptation report**:
A read-only view for a person that joins each applied proposal to the target's next entry and its previous entry, and shows the deltas in outcome, spend, turns, and elapsed time. It has no label and no threshold, and no code path reads it. It is never a fact.
_Avoid_: effectiveness index, score, adaptation metric, feedback

**Fact**:
An observation that a `when` or a proposal may read. It carries its instrument, its source, and its timestamp. A derived fact is one that code computes from other facts or from a node's output, such as `tier` or `review_required`. Only code produces a fact, and only a registered fact may be read. A model's self-report, a judge's score, and a cross-run count are not facts yet.
_Avoid_: signal, metric, condition input, compound predicate

**Facts registry**:
The closed list in the graph document that declares each fact: its name, type, instrument, and source kind. A fact outside it is refused.
_Avoid_: fact table, metrics list

**Gates registry**:
The closed list in the graph document of gate check ids and their parameter names. It is served from one source with the engine. The dashboard picks a check id from it and never adds one.
_Avoid_: check table, gate list

**Layout file**:
The sibling file that holds node positions for the dashboard. It is not part of the graph document, and it never changes the graph hash.
_Avoid_: layout field, view state in the graph

**Draft copy**:
The dashboard's recovery copy in browser storage: the graph text, the layout, the file name, a time, and the hash of the last file loaded or saved. It is never written to the graph file or the layout file. A save clears it. The next open of the same name offers Restore or Discard.
_Avoid_: autosave, backup file

**Review sheet**:
The plain-text export of the dashboard's review view: the graph hash, the error count, the declared caps, the diff summary, and the command template. A person pastes it into a ticket or commit message. No code reads it, and it records no approval.
_Avoid_: approval record, sign-off, report

**Command template**:
The run command that the dashboard shows and never starts: the graph file name and the engine options a person sets. It is text, not a resolved path.
_Avoid_: launch button, run config

**Sees list**:
The facts one proposing node may see, declared in its adapt block. A proposal may cite only a fact on this list.
_Avoid_: context, input list, allowlist

### Runs

**Watch**:
The dashboard page and the monitor that start with a run. The page shows the run; the monitor writes each stuck, abandoned or deadline-missed call once to `monitor.log` in the run folder.
_Avoid_: tail, observer

**Engine option**:
An invocation setting that changes how one run executes the same graph: where it stops, its run directory, a lower cap. It is logged in the `run_start` event and changes no node. It may name nodes, and it never overrides a node's model or budget.
_Avoid_: flag, config key, run mode

**Run block**:
The part of the graph document that holds run-level defaults: the run deadline, the `max_parallel` default, and the run spend cap. An engine option may only lower them.
_Avoid_: settings, globals, config

**Run**:
One execution of one graph version, from its first node to its verdict.
_Avoid_: execution, job, session

**Run record**:
The append-only log of events in one run: a JSONL file in the run's directory, one atomic write per event. It is separate from the graph. A node's outcome is derived from its declared artifact and logged here; the SDK result is logged as diagnostic. The verdict module reads this record and the target, nothing else.
_Avoid_: history, journal, trace, status file, transcript

**Resume**:
A later invocation that continues one run from its run record, at one restart point, under the same run id. It re-reads every `when`, skips no node, and checks each workspace first. It never resumes an SDK session.
_Avoid_: retry, restart, replay (as the whole act), continue

**Outcome**:
The result of one node in a run: pass, fail, deadline, or needs-human. A loop's outcome follows its control flow; an `each` joins by worst-of. A reason, such as `deadline` or `loop_max_exhausted`, rides beside the outcome in the record.
_Avoid_: status, result code, state

**Outcomes**:
The one module (`squad/outcomes.py`) that records an outcome: the `outcome` event, `done`, `last`, and for a graph node the `outcome:<name>` fact, the fact refresh and `stop_after`. The engine and the dispatch children both go through it. A child makes no fact, because the registry knows only graph nodes. The defect fallback of the root takes its entry here. A run and a resume use the same rule. Guards: `tests/test_outcomes.py`, and `tests/test_runners.py::test_no_runner_reaches_the_engine_through_ctx_private` for the runners. Not the dispatch Ledger above.
_Avoid_: outcome ledger

**Verdict**:
The single result of a run, owned by one module: completed, needs-human, conflict, failed, stopped, or no-repro. A `dispatch` folds like `each`, and a dispatch entry with no accepted child is `fail`, reason `no_dispatch`. The module folds the run record and the target: a run truncated by `--stop-after` whose nodes all passed is stopped (exit 30), root pass is completed, root fail or deadline is failed, root needs-human is needs-human, and conflict comes from the target. No graph field sets it.
_Avoid_: status, exit status, result

**Work item**:
One unit of work that the decompose node emits for execute nodes. Only the decompose output schema may call it a task.
_Avoid_: task (outside the decompose output), ticket, story

**Subagent**:
A model call inside one agent node. It is never a node and never an edge.
_Avoid_: task, background task, child agent

**Run server**:
The loopback process that `squad dashboard` starts for one run folder. It serves the page and the run paths on `127.0.0.1`, checks the Host header, starts no run, and exits after two idle hours. Its one write is `POST /run/control`: a pause or resume request that goes to the **Control file** and to nothing else. `--file` skips it and prints the `file://` URL.
_Avoid_: dashboard backend, helper

**Control file**:
`control.jsonl` in a run folder: one JSON line per pause or resume request, with no `nodes` for the whole run. It only grows. The engine folds it at each node entry and holds the nodes it names, then records a `paused` event with where the run stands. `squad resume` releases every hold and continues the run.
_Avoid_: stop file, lock

**Side panel**:
The dashboard's inspector for the selected node: its run result, failures, harness, and, in Edit mode, its fields. It starts hidden, overlays the graph, opens on a node press, and closes on a second press, an empty-canvas press, or the `[` key. It never holds a tool button.
_Avoid_: left panel, sidebar, drawer

**Mode**:
The dashboard's View or Edit setting. View reads a run and shows no control that changes a file. Edit shows every control that changes the graph or the layout. A page with a run opens in View; a bare page opens in Edit. Neither mode starts a run.
_Avoid_: tab, editor mode, run mode

**Group card**:
The one dashboard card that stands for three or more children of one dispatch that share a status: done with the same outcome, or running with the same harness. It shows the dispatch name, the child count and outcome, a summary of call time (summed over the children, so it can exceed the run's wall time), cost and tokens, and one square per child. A double-click expands it into one card per child (at no less than 70% zoom, so 100 or more cards stay readable); a double-click on one of those cards folds it back. Selecting it, or any child, lists the children in the side panel.
_Avoid_: child cluster, fan-out card

**Verdict chip**:
The top-bar badge that shows the run's state and exit code, with the failure reason as its tooltip. It shows the record's verdict at the replay cursor and never computes one.
_Avoid_: status badge, result pill

**Project settings file**:
`docs/squad/defaults.yaml` under the target. It holds only the keys the owner changed and overlays the plugin defaults: plugin defaults, project file, `roles`, graph run block, node. It is the one layer that may raise a plugin default. `run_start` records its path and sha256 (`project_settings`), and a resume refuses when it changed. Keys outside its catalog (`squad/project_settings.py`) and credential keys are refused.
_Avoid_: user config, local defaults

### The loop

**Requeue**:
The loop sends an issue whose child did not enter its wave back to Todo, so a later run builds it again. `wave.sh requeue` counts it per issue, one count per run. A new non-RED `Refs #N` commit in the base starts the count again.
_Avoid_: retry, redispatch

**Loop-caused requeue**:
A requeue whose cause is the loop, not the issue's work: a wave that landed without the child, an adapter error, a merge or rebase conflict, a disk stop, a review with no ruling, a head that moved during the review. It counts in its own ledger, `loopqueues-N`, never toward `SG_REQUEUE_CAP`. At `SG_LOOP_REQUEUE_CAP` (6) the issue gets `needs-owner`. A requeue the wave age cap makes counts in neither ledger.
_Avoid_: infra failure, flaky requeue

**Hold**:
A wave entry that refuses a child for now and requeues nothing (`WAVE HOLD`): no review, a wrong base, another sha, a verdict that is not APPROVE, or the gate lock or gate slots held. The child stays left, so the wave waits for it. `holds-RUN-CHILD` counts holds per head; at `SG_HOLD_TRIES` (3) on one head the child is requeued with the hold reason.
_Avoid_: block, skip

**Owner freeze**:
The `frozen:owner` label: the owner acts next on the issue, and its card keeps the column the work reached. Blocked is no column. `needs-owner` is the label a writer adds to ask for it (a loop-caused requeue cap, for one); janitor rule R reads it and writes `frozen:owner`. No dispatch pass takes an issue that carries either. Only the owner removes them.
_Avoid_: Blocked column, needs-human label

**Review lock**:
The lock `review-KEY.lock` that `review.sh` holds for one branch, so two reviews of the same branch never run at once (`SG_REVIEW_LOCK_WAIT`, 3600 s). The second review reuses a verdict the first wrote for the same head and calls no model (verdict reuse). Only a final verdict reaches `review-KEY.md`.
_Avoid_: review queue, review mutex

**Dispatch stop**:
A check that each dispatch pass runs first (`ghcall.sh`). A stop prints one line, writes it to `cycle.log`, and the pass dispatches nothing. The stops: a loop defect (`loop-bug`, ADR-0146); too many children waiting for review; the disk floor, under `SG_MIN_FREE_GB` GiB free (20); bug first, where the frontier pass waits while `bugs-waiting` names a startable bug with no run.
_Avoid_: pause, throttle

### Who holds a card

Three carriers take work, and a card names which one holds it.

**Manual**:
The owner's own work on a card. It moves the card the way any other holder does, and it never enters a slot count, a spend total or a run record.
_Avoid_: hand fix, by hand, human

**HAL9k**:
The operator session's own work on a card — the agent the owner talks to, outside the loop. It claims and releases a card like any other holder and is never mistaken for a run.
_Avoid_: the agent, Claude, operator (as a holder)

**Squad**:
The loop's work on a card: a run the supervisors dispatch. It is the only carrier that holds a slot, spends under a cap and writes a run record.
_Avoid_: the loop (as a holder), robot, bot
