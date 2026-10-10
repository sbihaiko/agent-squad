---
name: squad-goal
description: Autonomous delivery loop for GitHub issues through agent-squad (per-repo profile) — dispatch the full startable frontier in parallel panels, keep GitHub issues and board live, review independently, recover stalls and model failures, merge into the base branch, run the e2e command, and continue until every issue is Done. Use for "/squad-goal <issues>", or "implement issues #N… with the squad and merge".
---

# Squad goal

Drive a set of GitHub issues (`$SG_REPO`, board = project `$SG_PROJECT`) from ready to Done, with no owner in the loop. Arguments: the issue numbers, or a spec issue whose sub-issues are the work.

The owner may wrap the call in `/goal /squad-goal <issues>` so the loop survives across turns. Either way, **done** = every listed issue closed with evidence, its card in Done, and a green e2e on the final `origin/$SG_BASE`.

## Operating invariants

These rules are invariants of the delivery loop. They override any tendency to wait, batch bookkeeping, keep important state only in local logs, or create unnecessary agent-squad panels.

### 1. GitHub is live state — update immediately

Every meaningful state change, event, decision, failure, recovery, dispatch, review result, CI result, merge, e2e result, blocker change, or dependency change MUST be reflected on the affected GitHub issue immediately.

Do not wait for a run to finish and do not batch issue updates for the end.

For every such event:

1. queue an issue comment through `~/.cache/squad-goal/cq/`;
2. update the project card status when applicable;
3. only then continue orchestration.

The issue timeline is the durable human-readable audit log. Local logs are diagnostic only and MUST NOT be the sole record of an event or decision.

Do not post noisy heartbeats or duplicate messages. Post state transitions, decisions, failures, recoveries, and evidence.

### 2. Optimistic scheduling — never wait unnecessarily

Scheduling is optimistic.

After EVERY event, recompute the complete frontier and immediately dispatch all work that can safely make progress.

A PR being open, under review, waiting for CI, waiting for e2e, or not yet fully validated is NOT by itself a blocker.

Children may start from work that has not merged when the dependency is sufficiently stable to make useful progress. Point them at the dependency branch when necessary and require a rebase onto `origin/$SG_BASE` after the dependency merges.

Split blocked work when a safe independent portion can proceed.

Only a real semantic, file-ownership, ordering, or correctness dependency may prevent dispatch.

Prefer useful speculative work plus later rebase over idle capacity.

CI running, review pending, another child working, or a merge waiting is never a reason to leave unrelated capacity idle.

### 3. Stalls are recoverable

No run, PR, review, CI job, model call, e2e job, or orchestration step may wait indefinitely.

Track last meaningful progress for every active run and PR.

If an active unit stops producing meaningful progress beyond the configured stall timeout:

1. inspect its current state;
2. preserve all recoverable work;
3. post the stall and the recovery action to the affected issue;
4. terminate or abandon only the stuck execution when necessary;
5. resume through a continuation/fix run;
6. recompute the complete frontier immediately.

A stalled child MUST NOT stall unrelated work.

Repeated recovery failures escalate through `autonomy` rather than silently waiting for the owner.

Background machinery: run `scripts/watchd.sh start` once from the target checkout (idempotent; a supervisor restarts watch.sh if it dies; events go to $SG_STATE/watch.log). Arm the Monitor with `scripts/watchtail.sh` only to read that log; the Monitor expiring no longer stops autopilot, heartbeat or the reconciler. Reported verdicts and stalls persist in $SG_STATE/watch-seen.txt, so a restart does not replay them; delete the file to re-announce everything. `watchd.sh stop` ends the supervisor and watch.sh. Never run watch.sh directly under a Monitor while watchd is up: autopilot and the reconciler would run twice.

Requests: build every request with `scripts/mkreq.sh <name> <body-file>`; it appends `req-checklist.txt` (pre-push checks: verbatim decision quote, prose-vs-code grep, AC drift flag, RED first, final-head evidence, ADR verify scripts). Never hand-write `req/*.txt` without the checklist; the body must still start with the `IMPORTANT: your first and only action…` line.

A run verdict of failed whose only reason is ac-verify:no_ac_commands and that has a passing land event prints as "landed (gate: no_ac_commands)": the gate has no AC commands to run; check the PR head sha moved, not the verdict word.

Pre-dispatch: run `scripts/acscan-run.sh <issues>` on every issue before dispatch; any finding goes to the panel before code.

Hub: `scripts/hub.py [port]` (default 7700, loopback, read-only) serves one page that lists every live run dashboard and embeds it (sidebar + grid of running runs). Start it detached once (`Popen(start_new_session=True)`) and open http://127.0.0.1:7700/ once; `launch.py` opens no per-run dashboard tab (`SQUAD_HEADLESS=1`).

### 3b. Circuit breaker on fix rounds

`autopilot.sh` (called by `watch.sh` every cycle) keeps every open `Refs #N` PR moving without the session:
- `AUTOREVIEW`: new head sha with no review -> `revci.sh` (review chain from models.json + CI wait).
- `AUTOFIX`: once review+CI are done on the head sha, any of CHANGES, failed CI, merge conflict (`dirty`) or unresolved review threads (CodeRabbit or people) -> `autofix.sh` launches ONE agent-squad fix run on the PR branch carrying every problem (review findings verbatim, failing check URLs + rerun-if-infra rule, rebase instructions, the thread GraphQL query with reply-then-resolve). One fix per head sha; the fix's push re-arms review. Up to `AP_MAX_ROUNDS` (2) review/CI rounds; threads- or conflict-only rounds have their own cap of 3 and don't spend review rounds.
- `ROUND CAP` -> `autopanel.sh`: an Opus ruling via the claude CLI (read-only, challenger). One more round with no owner question -> `autofix.sh` with the panel's brief (at most `AP_PANEL_MAX`=2 extensions per PR); otherwise label `needs-human`, comment the question on the issue, `NEEDS OWNER` event — that one is yours to ask the owner.
- A failed autofix run (verdict `failed`) or one whose last started call has no live process (`STALL RUN`) is re-armed by `retryfix.sh`: it drops that sha's `fix-`/`queued-` markers and its round, so autopilot dispatches the fix again (max 2 retries per sha; `autofix.sh` records the run in `auto/runof-<pr>-<sha9>`).
- `janitor.sh` (every watch cycle; `JANITOR_DRY=1` only prints, `touch $SG_STATE/auto/nojanitor` turns it off): kills leftover processes whose cwd is inside a finished run folder (3 min after the verdict), kills the dashboard of runs finished for an hour, drops zombie runs (no verdict, idle 20 min, no process, last call dead) from `active-runs.txt`, reports two live runs on one issue (`DUP`), and removes an old `auto/disp-<n>` only when its request failed, the issue is open and no PR names it. It does not clean worktrees (`wtclean.sh` does).
- `bash scripts/test-review.sh [review.sh]` is the regression test of `review.sh` (stubbed gh and claude): a verdict is accepted with rc 0, and a push during the review ends it BLOCKED. Run it after every edit of `review.sh`, and never edit a script a running instance is executing (bash reads it incrementally): stop or wait for the instances first. A reviewer that narrates before the verdict gets its `VERDICT:` line moved to line 1 (revci and autopilot read line 1).
- `bash scripts/test-autofix.sh [autofix.sh]` is the regression test of the `[conflict]` request text (stubbed gh): a PR on `main` rebases onto `origin/main`; a PR stacked on an OPEN parent rebases onto the parent's branch, never onto `main`. A review round's request also states that findings needing a push to the agent-squad fork are out of the run's scope (forkclose.sh lands them after the merge), so a fork-only finding no longer ends the run needs-human.
- janitor rule F (report-only): an open issue named `(#N)` in a fork commit subject older than 30 min, with no open PR naming it, prints `FIXED-NOT-CLOSED #N` once. It never closes (closing needs evidence: comment it, then close). `bash scripts/test-janitor-f.sh` covers it.
- `watch.sh` appends any step that took 5 s or more to `$SG_STATE/cycle.log` (`HH:MM:SS <step> <secs>s`): read it to see where the cycle goes.
- `revci.sh` pins the head sha before the review and reports a conflicting head (`dirty`) at once; `review.sh` kills the reviewer when the PR head moves during the review (SUPERSEDED).
- `AUTOMERGE`: APPROVE + CI green + `mergeable_state` clean + zero unresolved threads -> squash-merge (`SG_AUTO_MERGE=yes`; label `hold`/`needs-human` or an open `Merge after #M` blocks it).
Fix/panel runs are detached; their lines go to `$SG_STATE/auto.log` and are re-emitted on the next pass. Dispatches, panel rulings and owner questions are queued as issue comments (cqflush). Switches: `touch $SG_STATE/auto/off` (all), `$SG_STATE/auto/nofix` (review+merge only). `AF_DRY=1 scripts/autofix.sh <pr> <issue>` prints the request without launching. On `AUTOMERGE` you still owe: e2e on the merge SHA, worktree cleanup, closing the issue. Don't launch a manual fix for a PR the autopilot already queued (`$SG_STATE/auto/queued-<pr>-<sha9>`).

A PR gets at most **2 review→fix rounds** (`AP_MAX_ROUNDS`; rounds 3–4 never converged in the 2026-10-09 data), and an issue at most **3 continuation runs**. On the next `CHANGES` or stall past either limit, stop dispatching fixes for that unit. Then:
1. Post the round history (each verdict's findings, one line each) on the issue.
2. Convene the `autonomy` panel on one question: split the issue, narrow the acceptance criterion, accept the PR with follow-up issues for the open findings, or abandon.
3. Act on the ruling. A `PROVISIONAL` ruling gets a `needs-ratification` label on the issue.

Findings that keep recurring after a fix round usually point at a contradictory requirement, not a weak coder.

### 4. Model/provider failure is recoverable

An unavailable, rate-limited, repeatedly failing, or non-responsive LLM MUST NOT stop the delivery loop.

Model selection is role-based. Each role has an ordered fallback chain configured for the environment.

If the preferred model fails after the configured retry budget:

1. record the model failure on the affected issue;
2. switch automatically to the next allowed model for that role;
3. resume from the latest durable state rather than restarting completed work;
4. record which replacement model continued the work;
5. recompute the frontier so unrelated work continues.

Do not ask the owner merely because a model or provider stopped working.

A replacement model MUST preserve the same role constraints, acceptance criteria, branch ownership, and review independence.

Correctness failures are NOT model-health failures. A reviewer returning `CHANGES`, a test failure caused by the implementation, or a bad patch does not make the model unhealthy.

If no allowed model remains, mark only the affected work Blocked, post the evidence, escalate through `autonomy` where useful, and continue all unrelated work.

Never let the same execution/model instance independently approve its own work when a different reviewer is available.

### 5. Preserve Anthropic weekly quota

Anthropic models are subject to a hard reserve policy.

Before dispatching new model work, and periodically while long-running Anthropic work is active, check the remaining Anthropic weekly usage quota when that information is available.

If the remaining weekly Anthropic quota is **10% or less**:

1. mark Anthropic models unavailable for new squad work for the remainder of the quota window;
2. automatically select the first healthy non-Anthropic model allowed for the same role;
3. do not ask the owner for permission to switch;
4. queue a comment on every affected active issue recording the quota threshold and model switch;
5. preserve the same branch, role, acceptance criteria, constraints, and durable state;
6. recompute the frontier immediately so the quota condition never stalls unrelated work.

The final 10% is a reserve and MUST NOT be intentionally consumed by autonomous squad work.

If an Anthropic-backed child is already running when the threshold is detected, stop assigning it additional work. If it can be safely interrupted without losing useful progress, preserve/push its durable state and continue with a non-Anthropic replacement. If interrupting it would create more risk than allowing the current atomic operation to finish, allow only that operation to reach a safe durable checkpoint, then switch.

Do not switch away from Anthropic merely because quota telemetry is temporarily unavailable. Record telemetry failures diagnostically and continue according to the last reliable quota state. Never invent a quota value.

Quota exhaustion/protection is a resource-routing event, not a model-health failure. Record it as `QUOTA_GUARD`, not `MODEL_FAIL`.

### 6. Events and decisions belong on the issue

The issue is the canonical human-readable history of the work.

At minimum, post:

- squad dispatch and run id;
- dependency/blocker changes;
- autonomous, advisor, and panel decisions;
- PR opened;
- review findings and verdict;
- CI failures and recovery;
- stalled-run detection and recovery;
- model failure and model replacement;
- continuation/fix runs;
- merge and merge SHA;
- e2e results and test counts;
- render-gate results when applicable;
- final acceptance evidence.

For every decision made autonomously, by an advisor, or by a panel, post:

- the question;
- the decision;
- who decided it (`alone`, `advisor`, or `panel`);
- the relevant rationale;
- whether it needs later owner ratification.

### 7. Maximize agent-squad panel utilization

Use the fewest `squad start` panels that safely express the current frontier.

Whenever multiple tasks are startable at the same time, place them in ONE agent-squad panel, with one child and one PR per issue/work slice.

Before creating any new panel:

1. recompute the complete frontier;
2. inspect available capacity;
3. include every compatible startable task that fits;
4. create a separate panel only when branch/base timing, recovery work, or a genuine execution dependency requires it.

Do not launch N independent panels when one panel with N children can perform the same work.

agent-squad cannot add children to a running panel, so launch exactly one new frontier panel containing all currently startable work.

Never delay startable work merely to create a larger batch.

### 8. Merge ready PRs and clean worktrees continuously

A ready PR left open is idle capacity: dependants keep building on a speculative branch, rebases pile up, and conflicts grow. Merge every PR the moment it is ready: review `APPROVE`, CI green, `mergeable_state` clean, and its declared merge order satisfied. Never hold a ready PR to batch merges, to wait for e2e of an earlier merge, or until the end of the goal. At every status check, sweep the open PRs of this goal for any that became ready without an event, and merge them.

After each merge:

1. queue the merge SHA on the issue;
2. start e2e detached on that exact SHA;
3. tell the open PRs that depend on it to rebase onto `origin/$SG_BASE`, through a fix run.

Clean worktrees as soon as their run has ended. Remove every worktree of a run with a verdict (`git worktree remove --force <abs path>`, then `git worktree prune`), and every worktree whose branch has merged. At each status check, sweep `git worktree list` for leftovers from finished runs. Never remove a worktree whose run is still alive, since that ends the run as `land_workspace_missing`. Stale worktrees cost disk, confuse later runs, and hide which work is live.

## Profiles

Per-repo values live in `scripts/profiles/<name>.sh` (repo, owner, board project, checkout, `SG_BASE`, `SG_MERGE_METHOD`, `SG_AUTO_MERGE`, e2e dir/command/pass regex, review and coding rules). `scripts/env.sh` picks the profile from `SG_PROFILE` or from the origin remote, and resolves the agent-squad install from `installed_plugins.json`. Every `$SG_*` name in this document comes from there; run `. scripts/env.sh` first. To support a new repo, copy a profile.

**Root name rule.** `scripts/launch.py` enforces it: before launching it renames `"root"` and the root node's `"name"` together when they differ from `$SG_ROOT_NAME`, validates the graph, and refuses to launch on a validation error (renaming only `"root"` gives `root must name a loop` + `orphan: no parent`). Commit the rename. The rule: The root node of the workflow graph (`"root"` and its node `"name"` in `docs/squad/workflows/<id>.graph.json`) is `$SG_ROOT_NAME`, the project name in snake_case (schema `^[a-z][a-z0-9_]*$`, so no hyphens), never `main`, because the squad dashboard titles its top card with that name. Before every launch, check the chosen workflow: if its root is not `$SG_ROOT_NAME`, rename both fields, validate with `squad.graph.validate` from the installed squad, and note it on the issues. A new profile must set `SG_ROOT_NAME`. Where a repo's own rules (CLAUDE.md) conflict with this document, the repo wins; encode the difference in its profile.

## Start

0. **Model check.** Do not run this loop on a Haiku session model: it hesitates to dispatch under `/autonomy` and loops on confirmations. Ask the owner to switch (`/model opus`) first.
1. Load the `autonomy` skill (Skill tool). Owner questions go to the panel. Carve-outs stay with the owner: money, credentials, access, permanent deletion, external publication.
   **Owner rule (2026-10-09): ask every pending owner decision up front, in one AskUserQuestion, before the first dispatch** (ADR picks, merges that need the owner, carve-outs, where to patch). Then run to the end without asking again.
2. Read every issue body and its "Blocked by" list. Build the ticket graph.
3. Check `git worktree list`, active runs, and open PRs. Another session may already be advancing the same tickets. Reconcile durable state before dispatching duplicates.
4. Start the background machinery once per session, with the Monitor tool where it fits:
   - `nohup ~/.claude/skills/squad-goal/scripts/cqflush.sh > ~/.cache/squad-goal/cqflush.log 2>&1 &`
     Then monitor `tail -n0 -F` on that log.
   - Monitor `~/.claude/skills/squad-goal/scripts/watch.sh`, run from the checkout (monitors expire after 30 min: re-arm on expiry). It emits:
     - `RUN <id> verdict=…`
     - `NEW PR #n …`
     - `REVIEW #pr sha state CI=… VERDICT`
     - `STALL RUN <id> …` (record.jsonl idle past `stall_timeout_minutes`)
     - `STALL REVIEW <pr> …` (no review+CI summary past `review_stall_minutes`; covers a stuck CI too)
     - `HUNG TEST pid=… cpu=…` (`scripts/heartbeat.sh`, called every cycle: a leaf test host — `testhost` or `dotnet exec --runtimeconfig … UI*Tests` — whose CPU time has not moved for `HB_FLAT_MIN`, default 5 min, was killed so the agent's `dotnet test` fails instead of hanging; it judges the executable name only, never a shell whose command line mentions testhost)
     - `BOARD MOVE #n <from> -> <to> (…)` / `BOARD ADD #n …` (`scripts/reconcile.py`, every ~6 min, `RECONCILE=apply`; dry run without it): issue closed -> Done; an open PR with `Refs #N` -> Testing (never downgrades); an open `bug` issue missing from the board -> added (Todo, or Testing when a PR names it). Blocking: an open issue whose `## Blocked by` lists an open `#M` goes Todo -> Blocked, and Blocked -> Todo when all are closed; a section with only free text prints `BOARD NOTE` (write `#N` in the issue body). Facts only, scope = issues >= `RECONCILE_MIN` (default 1102). The coordinator still moves In Progress/Blocked by hand
     - `DEAD RUN: n run(s) without a verdict and no live squad process` (killed by a session restart or crash: relaunch)
   - `MODEL_FAIL`/`MODEL_SWITCH` for review arrive as queued issue comments (cqflush log). `STALL E2E` and `QUOTA_GUARD` are yours to raise: run e2e with `run_in_background` and a timeout, and check quota at each status check.
5. Check the latest reliable Anthropic weekly quota state before the initial dispatch. If remaining quota is 10% or less, exclude Anthropic models from new autonomous squad work.
6. Recompute the complete initial frontier and dispatch it immediately.

## Launching, and staying awake in `/goal` mode

- **Optimistic stacking (owner's go-ahead, 2026-10-09).** Don't wait for a blocker's merge: `scripts/stack.sh <issue>` answers `READY` (all blockers closed: branch from origin/base), `STACK <pr> <branch>` (exactly one open blocker, whose open PR is based on the base branch and has no failing CI: branch from `origin/<branch>`, open the PR with base `<branch>` and "Merge after #<pr>" in the body), or `WAIT <reason>`. Limits: one level (never stack on a stacked PR), one open blocker, parent CI not red. Put every `STACK` issue in the frontier's single `squad start`. The autopilot never merges a PR whose base is not the base branch; when the parent merges it retargets the PR (`RETARGET`) and keeps the parent's last sha in `auto/stack-<pr>`, so a squash conflict is fixed by `git rebase --onto origin/<base> <that sha>`. The review of a stacked PR covers only its own diff over the parent branch and reads surrounding code from that branch. Work that lives in a local fork stacks the same way on the parent's fork branch.
- **Every request carries a DONE CHECK** (`mkreq.sh` appends it): the child ends with `ac_commands`, the checks it ran as evidence, so the final `ac-verify` gate verifies instead of failing with `no_ac_commands`. The gate runs in the main checkout, so work on a PR branch is wrapped as `scripts/acpr.sh <branch> -- <command>` (throwaway worktree at `origin/<branch>`); fork-only work as `cd <fork worktree> && <command>`. The hub shows a run that landed with no command to verify as `unverified` (its own color), never as a plain `landed`.
- **Worktree cleanup is automatic.** `wtclean.sh` (every watch loop) removes a child worktree once its run has a verdict, the tree is clean and HEAD is on a remote branch; a finished worktree with unpushed commits is kept and reported as `WT UNPUSHED` (cherry-pick and push it, then it is cleaned). The same pass also drops `acpr.sh` acceptance trees older than 2 h (a killed gate), fork-only worktrees (`~/wt-as-<n>`, `~/wt-fr-<n>`) once the issue is closed and the branch merged into the fork base (branch deleted with `git branch -d`), and `~/wt-*` PR worktrees once their PR is merged. The repo deletes a PR's head branch on merge (`delete_branch_on_merge`).
- **Ship skill changes with the fork.** After editing this skill, run `scripts/sync-to-fork.sh` (copies it to the fork's `contrib/squad-goal`, refusing any name from the local `$SG_STATE/sync-deny` list) and commit on `local/next`, with a CHANGELOG line.
- **agent-squad is a local fork.** We have no write access to Korck-lab/agent-squad. The repo profile sets `SG_SQUAD` to the fork checkout (branch `local/next` on top of `local/base-0.2.0` = the installed 0.2.0), so runs use the fork. Work on the squad itself (ADR-0272 P1-A: runner, script format, dashboard) is committed there locally — no push, no PR; review the local diff — and the owner sends it upstream as a ZIP from `scripts/export-agent-squad.sh`. Never patch the plugin cache again.
- **No dashboard tabs.** Only the Squad hub (http://127.0.0.1:7700/) is used. `launch.py` sets `SQUAD_HEADLESS=1`, so the run's dashboard server starts for the hub but no tab opens. Never run `/agent-squad:dashboard` or `squad dashboard` without `--no-open`.

- **Launch only through `scripts/launch.py`, from `$SG_CHECKOUT`.** `/agent-squad:work` inside this loop means `launch.py`, never `squad start` in the Bash tool (it dies with the session, as run `2e34b4` did on 2026-10-09). `launch.py` refuses another cwd unless `SG_ALLOW_OTHER_CWD=1`, fails at once with the log tail when `squad` exits before creating the run folder, and writes the run's absolute folder to `active-runs.txt` so `watch.sh`/`heartbeat.sh` track it from anywhere.
- **Never end a turn in `/goal` mode without a live background task.** After every dispatch keep a Monitor on `scripts/watchtail.sh` (persistent, max timeout; re-arm on expiry). With background work alive the Stop hook sends one "background work still running" check-in instead of re-evaluating the goal every turn; `ScheduleWakeup` does not count as background work. Without it the hook blocks each turn end and the session floods the owner with identical wait replies (2026-10-09: 66 hook blocks, 5 forced overrides).
- **When the Stop hook blocks and nothing changed:** answer with one short line at most once, then nothing. Never repeat a status line. Act only on a real event (`RUN … verdict`, `NEW PR`, `REVIEW`, `STALL`, `DEAD RUN`).

## Dispatch: the frontier, in one panel

**Frontier:** every ticket or safely separable work slice that can make useful progress now.

A dependency does not need to be fully merged or validated when useful work can safely proceed.

Cases that count as startable include:

- the blocker is only a doc or ADR already present on an open PR branch: point the child at that branch and tell it to rebase after merge;
- the blocker implementation is stable enough on an open branch for dependent work to proceed speculatively;
- the work has a host-free half: pure functions over primitive inputs, in new files, with their own tests. Split it off and launch it now;
- unrelated portions of an issue can proceed without touching files owned by another child.

**One panel per frontier:** independent/startable issues go in ONE `squad start` whose request lists every issue, one child and one PR each.

A separate run is justified only for:

- a fix or continuation on an existing PR branch;
- work that genuinely must start at a different moment or base;
- a recovery that cannot safely join the current panel.

**Parallelism:** up to 20 children per panel, 10 running at once. Caps live in `docs/squad/workflows/dynamic.graph.json`: `max_parallel` 10, `max_children` 20, `spend_cap` 168. Give each child a disjoint slice. A file that two children would both append to belongs to exactly one of them.

Before every launch, recompute the complete frontier and fill as much available capacity as possible.

**Every coding request must contain these lines:**

- "base on origin/$SG_BASE", unless explicitly starting from a dependency or fix branch;
- "open the PR yourself";
- "Refs #N" (never Closes);
- "GitHub issue #N" (names the dashboard card);
- coding model: the FIRST entry of `coding_models` in `scripts/models.json` (the hub's "squad models" panel edits it; it is the only source of truth, never a model typed in this file or in a repo's CLAUDE.md). Read it at every dispatch: `jq -r '.coding_models[0]' scripts/models.json`; the next entry is the fallback. (2026-10-09: requests hard-coded deepseek while the panel said Sonnet; the panel wins.)
- automatic fallback to the next configured coding model on infrastructure/model failure;
- if Anthropic weekly quota remaining is 10% or less, do not use an Anthropic coding model; select the first healthy configured non-Anthropic alternative;
- the profile's coding rules, verbatim (`$SG_CODING_RULES`);
- accepted ADRs and the repo's CLAUDE.md/AGENTS.md are binding.

For a fix or continuation run, also say:

- "check out origin/<branch>";
- "rebase onto origin/$SG_BASE if behind";
- "push to the same branch";
- "no new PR".

For speculative work based on an unmerged dependency branch, explicitly name the base branch and require rebase onto `origin/$SG_BASE` once the dependency merges. With `SG_MERGE_METHOD=squash` the dependency's own commits never reach the base and a plain `git rebase origin/$SG_BASE` replays them and conflicts (with `merge` a plain rebase or merge is fine). The rebase request must say: `git rebase --onto origin/$SG_BASE <old-dependency-tip> <child-branch>`, where `<old-dependency-tip>` is the dependency head the child last built on (`git merge-base <child-branch> origin/<dependency-branch>` before the dependency branch is deleted). Record that SHA in the dispatch comment.

**Launch:** write the request to `~/.cache/squad-goal/req/<name>.txt`, then run:

`python3 ~/.claude/skills/squad-goal/scripts/launch.py <file>`

from the checkout. It launches detached, records the run in `active-runs.txt`, starts its dashboard server for the hub without opening a tab, and prints the run id.

On dispatch:

1. queue a comment with the run id and relevant dependency/base information on every affected issue;
2. move each card to In Progress;
3. continue immediately to monitoring and frontier recomputation.

## Event loop

Every monitor or watchdog event triggers this loop, in this order:

1. Persist the event and any resulting decision to the affected issue(s).
2. Update the board status if state changed.
3. Perform the required recovery, review, merge, or continuation action.
4. Recompute the ENTIRE ticket frontier.
5. Fill every available squad slot with startable work.
6. Prefer adding compatible work to the same agent-squad panel/run when supported; otherwise launch exactly one new frontier panel containing all currently startable compatible work.
7. Return to monitoring.

Never wait for CI, review, merge, e2e, another child, or a model retry when unrelated or speculatively-safe work can proceed.

## Issue updates

Any meaningful change goes to the issue immediately by writing:

`~/.cache/squad-goal/cq/<issue>-<tag>.md`

`cqflush` posts it with retries.

Move the board card on the same event:

```sh
~/.claude/skills/drive-board/scripts/board-status.sh <owner> <project> "<Status>" <n…>
```

Status options:

- Blocked
- Todo
- In Progress
- Code Complete (PR open: review, CI, fix rounds and merge; `revci.sh` and `reconcile.py` set it)
- Testing (only while a test pass actually runs: the e2e on the merge SHA, which `autopilot.sh` sets on merge, or the fork suite for fork-only work, set by hand)
- Done

A PR opened for an issue moves it to Code Complete; Testing starts at the merge, when the e2e on the merge SHA starts; Done when it passes and the issue closes. A failed e2e sends the card back to In Progress.

The comment queue is a durability mechanism, not permission to postpone creating the update. Queue the update at the moment the event happens.

Autonomous frontier: `scripts/autofrontier.sh` (every ~3 min) dispatches every open `$SG_FRONTIER_LABEL` issue that `stack.sh` calls READY/STACK, has no open PR, no `auto/disp-<n>` marker and no live run working it, all in one squad start within the free slots, with the profile's `SG_FRONTIER_PREAMBLE` as shared rules. `stack.sh` lets a blocker whose card is in Testing or Code Complete pass (owner's pick 2026-10-10: dispatch on Code Complete). A Code Complete blocker with a PR stacks on it; one with fork-only work hands its unmerged fork branch to the child (`READY FORK <br>`), which merges it into its own fork branch first. A PR that merely says "Refs #N" proves nothing.

Fork-only close: `scripts/forkclose.sh` (every loop) takes an open Code Complete issue with no open PR and a fork branch (`SG_FORK_BRANCH`, `%n` = issue) ahead of `SG_FORK_BASE`, merges it (`--no-ff`) in `SG_FORK_DIR`, runs `SG_FORK_TEST_CMD` through `test.sh` (card -> Testing, so its dependents dispatch at once), then closes the issue with the evidence (green) or reverts the merge with `git revert -m 1` and moves the card back to In Progress (red). A merge conflict aborts and is reported. Owner-approved Done rule for fork-only issues (#1180).

GitHub access is REST only in the loop (`board.py` for cards, REST diff in review, REST in reconcile; children open PRs with `gh api --method POST`): the GraphQL quota is shared with every `gh pr`/`gh issue`/`gh project` command and ran out.

Board moves go through `scripts/board.sh` (`$BOARD`), which flags the hub to refresh the board within 2 s instead of its 60 s cycle; `childcc.py` runs every 30 s with the autopilot.

A child that finishes with outcome pass moves its issue to Code Complete at once (`scripts/childcc.py`, every watch loop, live runs only, once per child), with a comment carrying its first reason; the panel may still be running its siblings.

Coordinator test passes (post-merge e2e, a fork suite for fork-only work) go through `scripts/test.sh <issue> [-C <dir>] -- <command>`: it moves the card to Testing, logs to `$SG_STATE/tests/`, queues the result on the issue, and the hub lists it as `test #N` with the live log as its panel. A test run by hand outside it is invisible on the hub.

Automatic bug fixing: `scripts/autobug.sh` (watch.sh, every ~6 min) dispatches every open `bug` issue with no open PR naming it, no `auto/bug-<n>` marker and no skip label (`needs-owner`, `no-auto`, `wontfix`, `question`), all in one squad start within the free run slots: one child and one PR each, TDD with the RED output in the PR, accepted ADRs binding, owner carve-outs returned as fail. It comments the dispatch on each issue and moves the cards (task board, and the bug board when the profile sets `SG_BUG_PROJECT`). The loop never retries: delete `auto/bug-<n>` to dispatch again. `SG_AUTO_BUGFIX=no` turns it off.

Optimistic while testing: a blocker whose PR is already merged into the base counts as done for dispatch (`stack.sh` READY) even while its e2e runs or the issue waits to close; dispatch its dependents at the merge, not at the close. A red e2e goes back to the blocker as a fix run; the dependents keep going.

## Event handling

| Event | Action |
|---|---|
| `NEW PR #n` | Queue the PR-opened event on the issue immediately. Start `nohup scripts/revci.sh <pr> <issue> > ~/.cache/squad-goal/revci-<pr>.out 2>&1 &`. `revci.sh` moves the card to Code Complete; Testing waits for the merge. Review with the preferred independent review model, currently Gemini 3.8 flash high, then Grok 4.6, GPT 6.1 Sol, Opus (models.json; owner, 2026-10-10, by review-times.tsv latency), automatically falling back to the next configured review model on model/provider failure. Wait for CI inside the detached review machinery, not in the orchestration loop. Recompute the frontier immediately. |
| `REVIEW … CHANGES` or CI red | Queue findings on the issue immediately. Read `review-<pr>.md`. Fix real defects through a squad fix run on the PR branch, never by hand. Card back to In Progress. A wrong base means rebase on `origin/$SG_BASE`. Recompute the frontier before launching so compatible fixes/work are grouped when safe. |
| `REVIEW … APPROVE` + CI green + clean | Queue approval/CI evidence. Merge via REST: `gh api -X PUT repos/$SG_REPO/pulls/<pr>/merge -f merge_method=$SG_MERGE_METHOD` (only when `SG_AUTO_MERGE=yes`; otherwise stop at approval and list the PR for the owner). Never merge into a protected branch the repo rules reserve for the owner; promotion between branches stays with the owner. Respect declared merge order, including an ADR before PRs that cite it. Then start e2e detached on the exact merged SHA. Recompute the frontier immediately rather than waiting for e2e. |
| `RUN … verdict` | Queue the verdict if meaningful. Remove that run's worktrees only after the run is no longer alive: `git worktree remove --force <abs path>`, then `git worktree prune`. Drop it from `active-runs.txt`. Recompute the frontier and fill freed slots immediately. |
| `STALL RUN …` | Preserve recoverable work, queue the stall and recovery decision, terminate only the stuck execution if needed, then launch a continuation on the same branch. Do not disturb unrelated children. |
| `STALL REVIEW …` | Queue the stall. Restart/recover the review using the configured review fallback policy. Do not block unrelated merges/work. |
| `STALL CI …` | Queue the stall and available evidence. Retry/recover according to CI policy; if external CI remains unavailable, mark only the affected work Blocked and continue unrelated work. |
| `STALL E2E …` | Queue the stall. Preserve the exact tested SHA, restart the e2e check, and continue unrelated work. |
| `MODEL_FAIL …` | Queue the failure with role/model/error class. Apply the role's retry budget, then automatically switch to the next configured model. Resume from durable state. |
| `MODEL_SWITCH …` | Queue the replacement model and reason. Continue without owner intervention. |
| `QUOTA_GUARD anthropic remaining=…` | If remaining weekly Anthropic quota is 10% or less, queue the quota-protection event, make Anthropic models ineligible for new autonomous work, move affected work to healthy non-Anthropic models at the next safe durable checkpoint, and continue without owner intervention. The protected final 10% MUST NOT be intentionally consumed by squad work. |

After every row above: recompute the complete frontier.

## Run verdicts

- `failed` + `ac-verify:no_ac_commands` is benign; check that the PR or push exists.
- `node_turn_cap` means the run died mid-work. Preserve the worktree, commit recoverable work as WIP if needed, push the branch, queue the recovery on the issue, and launch a continuation run.
- The `unrecognized_model` warning for deepseek is noise only if the requested model actually runs successfully. If execution does not progress, treat it as `MODEL_FAIL` and use the fallback policy.
- Any unknown failure is an event: preserve state, queue evidence, recover if possible, and continue unrelated work.

## Model fallback policy

Model fallback is role-based and lives in `~/.claude/skills/squad-goal/scripts/models.json` (the squad workflow schema rejects unknown fields, so it cannot live in `dynamic.graph.json`). Squad coding ids must also be in that workflow's model allow-list. `review.sh` already walks `review_models` and queues `MODEL_FAIL`/`MODEL_SWITCH` comments. Each model try is killed after `review_timeout_seconds` (default 480; `MODEL_TIMEOUT`, then the next model, no retry), and every try's latency goes to `$SG_STATE/review-times.tsv` (order `review_models` by that data, with the owner's go-ahead). Fix rounds are reviewed incrementally: when the last review asked for CHANGES and the new head descends from the reviewed sha (`review-<pr>.sha`, compare status `ahead`) with a delta of at most `review_delta_max_lines`, the reviewer gets the previous findings plus that delta; a rebase or a first review gets the full diff. The autopilot runs every 30 s and `revci.sh` polls CI every 15 s; for coding, name the next `coding_models` entry in the continuation run's request. `models.json` may carry `efforts.<chain>.<model-id>` (low|medium|high|max; the default is medium, which is stored as an absent key); when dispatching a chain entry, pass that effort in the request/review call if set, otherwise use the default (medium).

Current chains: read `scripts/models.json` (source of truth, edited from the hub); never copy them here.

### Anthropic quota guard

Apply `anthropic_weekly_reserve_percent` as a hard eligibility gate before ordinary model preference/fallback selection. The guard is a file: `touch ~/.cache/squad-goal/quota-guard` turns it on (scripts skip `anthropic_models`), `rm` turns it off. No programmatic reading of the Anthropic weekly quota exists in a headless session, so the guard is event-driven. It turns on when any Anthropic call (squad child, `claude -p` review, the session itself) fails with a usage-limit or weekly-limit error, or when the owner reports the quota at or under the reserve. It turns off when the limit's stated reset time passes. Codex works the same way: a `hit your usage limit` error writes `codex-limited`, and `review.sh` skips every Codex model until that time. Note the orchestrating session itself is Anthropic: when the guard is on, keep its own turns lean (monitor events, short commands) since it cannot switch models.

When the latest reliable Anthropic weekly quota reading is at or below the configured reserve threshold (default: 10%), remove all Anthropic models from the eligible model set for autonomous squad work until the quota window resets or a later reliable reading shows quota above the threshold.

Selection then continues through the configured model list, skipping Anthropic entries and choosing the first healthy non-Anthropic model that satisfies the role.

The quota guard takes precedence over a request that names an Anthropic model as preferred. A preferred Anthropic model is preferred only while it remains quota-eligible.

When quota rises above the reserve threshold after reset or a reliable refresh, Anthropic models may become eligible again automatically.

A model is unhealthy for the current role when it repeatedly fails for infrastructure/provider reasons, is unavailable, is rate-limited beyond the retry budget, or produces no meaningful progress beyond the stall timeout.

Do not classify ordinary coding mistakes, failed acceptance criteria, reviewer `CHANGES`, or test failures caused by the implementation as provider/model-health failures.

If all configured models for a role fail:

1. queue the evidence on the issue;
2. mark only that work Blocked;
3. use `autonomy` to determine whether another allowed execution path exists;
4. continue every unrelated startable task.

## e2e after each merge

Run the profile's e2e command in `$SG_E2E_DIR`, detached on the exact merged `origin/$SG_BASE` SHA (use a separate worktree when `$SG_E2E_DIR` is the live checkout):

```sh
cd "$SG_E2E_DIR" && git fetch -q origin && git checkout -q --detach origin/$SG_BASE && eval "$SG_E2E_CMD"
```

Do not block orchestration while e2e runs.

Require a match of `$SG_E2E_PASS_REGEX` with zero failures; exit 0 alone is not proof.

When the profile defines extra gates for the change (e.g. headless wiring or render gate), run them too.

Post the result on the issue immediately when it arrives.

If e2e fails:

1. queue the failure evidence on the affected issue(s);
2. determine which merged change is responsible;
3. move affected cards back to In Progress or Blocked as appropriate;
4. dispatch a fix through agent-squad, never by hand;
5. keep unrelated work moving.

## Autonomy decisions

Use the `autonomy` skill for owner-level questions that are not carve-outs.

Decisions may be made:

- alone;
- with an advisor;
- by panel.

Every non-trivial decision affecting implementation, acceptance, dependency interpretation, recovery, or merge ordering MUST be queued to the relevant issue immediately with:

- question;
- decision;
- decision source;
- concise rationale;
- `needs-ratification: yes|no`.

Carve-outs remain with the owner:

- money;
- credentials;
- access;
- permanent deletion;
- external publication.

A carve-out may block the affected work, but MUST NOT block unrelated work.

## Close

When every acceptance criterion of an issue holds on `$SG_BASE`:

1. comment with the evidence:
   - PR;
   - merge SHA;
   - test counts;
   - render-gate result when applicable;
   - relevant autonomy decisions;
2. close the issue;
3. move the card to Done;
4. recompute the frontier immediately.

Close the parent spec last.

Visual checks (render versus wireframe) use the `vision_models` chain, Grok 4.6 first (known-image probe passed 2026-10-07). Copy the PNGs with `/usr/bin/git show` (the rtk hook corrupts binaries) into an isolated folder, `cd` there and run `grok -p "Look ONLY at the images here; do not read other files; say CANNOT_SEE_IMAGE if you cannot" -m grok-4.6 --always-approve`; a repo in reach lets Grok answer from source instead of pixels. If Grok fails, fall back to Claude's own vision and say so. A visual failure never blocks unrelated work.

## Final completion check

Before declaring the goal done:

1. fetch `origin/$SG_BASE`;
2. verify every requested issue is closed;
3. verify every requested card is Done;
4. verify no required acceptance criterion is only present on an unmerged branch;
5. verify the final e2e is green on the current final `origin/$SG_BASE` SHA;
6. verify no active run or relevant PR is still carrying required work;
7. verify all significant events and decisions have durable issue comments;
8. verify every `needs-ratification` item is listed for the owner.

If any condition fails, the goal is not done. Recover or continue the loop.

## Final chat report (pt-BR)

Report:

- merged PRs and closed issues;
- final `origin/$SG_BASE` SHA and e2e evidence;
- autonomy decisions (`alone`, `advisor`, `panel`);
- model failures/fallbacks that occurred;
- Anthropic quota-guard activations and substitutions that occurred;
- stalls and recoveries that occurred;
- open `needs-ratification` items;
- anything left undone, and why. An issue with both a MesenAI PR and fork work waits for the PR to merge, then `forkclose.sh` merges its fork half (it sweeps every fork branch, so a closed issue's half still lands) without touching the issue; before each fork test it rebuilds `SG_E2E_BUILD_DIR` at the base head (`SG_E2E_BUILD_CMD`) for the real-app cases.
