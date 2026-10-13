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

1. queue an issue comment through `$SG_STATE/cq/` (one state directory per profile), named for its repository: `$(sg_cq_file <issue>-<tag>.md)`;
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

Background machinery: run `scripts/watchd.sh start` once from the target checkout (idempotent; a supervisor restarts watch.sh if it dies; events go to $SG_STATE/watch.log). Arm the Monitor with `scripts/watchtail.sh` only to read that log; the Monitor expiring no longer stops autopilot, heartbeat or the reconciler. Reported verdicts and stalls persist in $SG_STATE/watch-seen.txt, so a restart does not replay them; delete the file to re-announce everything. `watchd.sh stop` ends the supervisor and watch.sh. Never run watch.sh directly under a Monitor while watchd is up: autopilot, the two dispatch passes (autofrontier, autobug) and the reconciler would run twice.

Requests: build every request with `scripts/mkreq.sh <name> <body-file>`; it appends `req-checklist.txt` (pre-push checks: verbatim decision quote, prose-vs-code grep, AC drift flag, RED first, final-head evidence, ADR verify scripts). Put a `PANEL: <id>` line in each sibling's body (or export `SG_PANEL`): `mkreq.sh` then names both requests when it refuses two of one panel that share a file (ADR-0274). `mkreq.sh` refuses any request naming a file already in flight — an open PR that changes it, a request of any panel whose run is still live (no verdict in `runs/<run>/record.jsonl`, the run folder being matched to the request `launch.py` copied into it) or that is still inside the launch window (`SG_SLICE_REQ_TTL`, 30 min), or an unmerged `gui/*` branch in the fork checkout that touches it, which is the only place a fork-only child's slice shows up (#1519) — exiting non-zero and writing no request. One request class is exempt: `autofix.sh`'s `auto-fix-<pr>-<sha9>`, whose PR is in the claim set by definition — a fix run on the PR it repairs is one unit of work, not a second slice (#1769). So the file of another open PR, of another request or of an unmerged fork branch still refuses exactly as before. A request whose run recorded a verdict, and which no live run carries, is history: request files are never deleted, so without that the guard's in-flight set only grew and refused forever against runs that ended days ago (#1529). The dispatchers (`autobug.sh`, `autofrontier.sh`) free the dispatch marker they wrote when `mkreq.sh` refuses (#1529): a dispatch that never happened must not mark the issue done. Export `SG_SLICE_FORCE=1` to dispatch a deliberate overlap anyway: it is the documented way past the guard, and it announces each overlap on stderr so the decision stays visible. Never hand-write `req/*.txt` without the checklist; the body must still start with the `IMPORTANT: your first and only action…` line.

A run verdict of failed whose only reason is ac-verify:no_ac_commands and that has a passing land event prints as "landed (gate: no_ac_commands)": the gate has no AC commands to run; check the PR head sha moved, not the verdict word.

Pre-dispatch: run `scripts/acscan-run.sh <issues>` on every issue before dispatch; any finding goes to the panel before code.

Hub: `scripts/hubctl.sh [--restart] [port]` (default 7700, loopback, read-only) starts `scripts/hubmux.py` when no hub answers on the port, and prints the URL of the cwd's repository. One process serves every profile under `/<profile>`: that repository's `hub.py` page, which lists every live run dashboard and embeds it (sidebar + grid of running runs). `/` lists the known repositories and their live workers; a path no profile owns answers 404 with that list. A port held by anything that does not answer `/api/hub` as the squad hub is refused (#20); `--restart` stops the squad hub on the port first and starts it from the files on disk (`publish-landing.sh`, #31). Open http://127.0.0.1:7700/<profile> once; `launch.py` opens no per-run dashboard tab (`SQUAD_HEADLESS=1`). The left pane's footer strip reads the machine: `CPU 62% · RAM 71% · GPU 27%`, one in-process sample every `LOAD_SAMPLE_SECS` (5 s) served from `/api/load`, with `?` for any source the hub cannot see (a source that raises, or answers with a string, a negative or NaN, is unavailable — never a 0 and never a stale number). GPU is the accelerator's own counter via an unprivileged `ioreg`; CPU and RAM come from Mach's counters inside the hub, so a page view spawns nothing and a saturated machine never makes the hub part of the load. A cell whose own reading is at or above `LOAD_ERR_PCT` (95%) is painted red — that attribute alone, the number that reached it, never the whole line — and red wins over the amber below (#1816); the text, the order and the rounding are unchanged, so the strip reads exactly as it read. While machine CPU stays at or above `LOAD_WARN_PCT` for more than one sample (`LOAD_WARN_SAMPLES`), the run whose process tree owns at least `LOAD_MIN_SHARE_PCT` of that CPU carries a `cpu N%` marker on its card: attribution walks the process tree by **worktree path** (the run folder plus the worktrees its record names), never by process name — every child of every run is the same `claude`, `dotnet` or `python`, so a name would credit whoever started first.

### 3b. Circuit breaker on fix rounds

`autopilot.sh` (called by the fast lane, `fastloop.sh`, every 30 s; by `watch.sh` when the fast lane is down) keeps every open `Refs #N` PR moving without the session:
- `AUTOREVIEW`: new head sha with no review -> `revci.sh` (review chain from models.json + CI wait).
- `AUTOFIX`: once review+CI are done on the head sha, any of CHANGES, failed CI, merge conflict (`dirty`) or unresolved review threads (CodeRabbit or people) -> `autofix.sh` launches ONE agent-squad fix run on the PR branch carrying every problem (review findings verbatim, failing check URLs + rerun-if-infra rule, rebase instructions, the thread GraphQL query with reply-then-resolve). One fix per head sha; the fix's push re-arms review. Up to `AP_MAX_ROUNDS` (2) review/CI rounds; threads- or conflict-only rounds have their own cap of 3 and don't spend review rounds. A request `mkreq.sh` refuses is not a dispatch: `autofix.sh` frees the `fix-`/`queued-<pr>-<sha9>` markers it wrote and returns the round it charged (exit 75), so the next pass retries the head instead of leaving it queued with a BLOCKING verdict and nothing working it (#1770).
- `ROUND CAP` -> `autopanel.sh`: an Opus ruling via the claude CLI (read-only, challenger). One more round with no owner question -> `autofix.sh` with the panel's brief (at most `AP_PANEL_MAX`=2 extensions per PR); otherwise label `needs-human`, comment the question on the issue, `NEEDS OWNER` event — that one is yours to ask the owner.
- A failed autofix run (verdict `failed`) or one whose last started call has no live process (`STALL RUN`) is re-armed by `retryfix.sh`: it drops that sha's `fix-`/`queued-` markers and its round, so autopilot dispatches the fix again (max 2 retries per sha; `autofix.sh` records the run in `auto/runof-<pr>-<sha9>`).
- `janitor.sh` (every watch cycle; `JANITOR_DRY=1` only prints, `touch $SG_STATE/auto/nojanitor` turns it off): kills leftover processes whose cwd is inside a finished run folder (3 min after the verdict), kills the dashboard of runs finished for an hour, drops zombie runs (no verdict, idle 20 min, no process, last call dead) from `active-runs.txt`, reports two live runs on one issue (`DUP`), and removes an old `auto/disp-<n>` only when its request failed, the issue is open and no PR names it. It does not clean worktrees (`wtclean.sh` does).
- A review is a short, topic-grouped checklist (#1777), and `revformat.py` owns that shape: line 1 `VERDICT: APPROVE|CHANGES` (the exact form revci, autopilot and the hub parse), then a `## Blocking` and/or a `## Debt` section, whichever is non-empty, Blocking first — the section a finding sits in IS its severity, and `is_finding` is the one predicate that reads it (the check, the `--summary` line `autopilot.sh` reads and the debt filer all ask it); a fix round (`revformat.py --fix-round`, the delta mode of `review.sh`) may add a `## Resolved` section for the previous findings it answers, whose bullets are answers and not findings, so a fix round whose every Blocking finding is resolved may APPROVE with `blocking=0` and the debt-only path of ADR-0273 sees it — a full review that writes `## Resolved` is a format failure instead; under a section one bullet per finding, at most two lines, `file:line — defect — concrete failure — fix`, findings that share a theme under a `### <theme>` heading, most severe first; no preamble, no restating the diff, no closing summary, and the whole review inside `review_max_lines` of `models.json` (the cap is a config constant, never a number in the prompt). The prompt of both review modes carries that shape, and it is not written in the prompt (#1835): `revformat.py --shape` (with `--fix-round` for the mode that has an answer section) prints it as a template — the smallest answer the check accepts, its verdict and `file:line` in angle brackets — and `review.sh` embeds that printed text verbatim, so a wording the check cannot parse turns `test-revformat.sh` red instead of costing a review round; an answer outside the shape is a `MODEL_FORMAT` failure of that try — the chain moves on and the issue says so, instead of prose passing silently as a review. The verdict is still canonicalized onto line 1 (a bold or lower-case one is rewritten in place); what stood in the way of the shape then fails the check. The fix run's reply to a review is ONE PR comment in the same shape: one bullet per finding, `fixed` or `not fixed` — the one-line reason — the check that proves it, its first line naming the thread it answers, ending with `Refs #N`. `bash scripts/test-revformat.sh [revformat.py]` is its regression test.
- `bash scripts/test-review.sh [review.sh]` is the regression test of `review.sh` (stubbed gh and claude): a verdict is accepted with rc 0, a prose answer is a format failure (BLOCKED, `MODEL_FORMAT` queued), and a push during the review ends it BLOCKED. Run it after every edit of `review.sh`, and never edit a script a running instance is executing (bash reads it incrementally): stop or wait for the instances first.
- `python3 scripts/test-hub-e2e.py` (run it from a checkout: the hub reads that profile) is the e2e of the hub, on a throwaway copy of this skill on its own port, so the live hub and the real `models.json` are never touched. It checks every `/api/*` the page calls, that only our own Origin may POST, and that the models panel a person sees is right: the rows show their `hrs`, an edit wakes Save, Save writes `models.json`, the value survives a reload, a garbage window never enters the field, and nothing in the sidebar overflows its box (that last one caught a row too wide for the 300px column). Needs playwright (python) plus Chrome; it prints SKIP and exits 0 without them.
- `bash scripts/test-autofix.sh [autofix.sh]` is the regression test of the `[conflict]` request text (stubbed gh): a PR on `main` rebases onto `origin/main`; a PR stacked on an OPEN parent rebases onto the parent's branch, never onto `main`. A review round's request also states that findings needing a push to the agent-squad fork are out of the run's scope (forkclose.sh lands them after the merge), so a fork-only finding no longer ends the run needs-human. It also pins the dispatch claim: a request `mkreq.sh` refuses leaves no `auto/queued-<pr>-<sha9>` and no charged round behind, while a dispatched fix keeps both (#1770).
- `wtclean.sh` also cleans the worktrees of a ZOMBIE run (no verdict, `record.jsonl` idle >20 min, no process whose cwd is inside the run folder), under the same clean-tree / HEAD-on-a-remote-branch rules. `bash scripts/test-wtclean.sh` covers zombie, fresh and busy runs.
- janitor rule F (report-only): an open issue named `(#N)` in a fork commit subject older than 30 min, with no open PR naming it, prints `FIXED-NOT-CLOSED #N` once. It never closes (closing needs evidence: comment it, then close). `bash scripts/test-janitor-f.sh` covers it.
- janitor rule I: a live run whose accepted issues are ALL closed is stopped when every one was closed as `not_planned` or is titled `[duplicate…` (`RUN-ON-CLOSED-ISSUES`, process tree and last call killed); closed any other way it is only reported once (a land or fix run may be finishing). `autobug.sh` skips open bug issues titled `[duplicate…`.
- janitor rule J: a run with no `record.jsonl` 5 min after its request (the squad refused to start, e.g. `vendor/skills is not clean`) has its dashboard killed, is dropped from active-runs (`NEVER-STARTED`) and has the dispatch markers of every issue in its request (`auto/bug-<n>` from `autobug.sh`, `auto/disp-<n>` from `autofrontier.sh`) removed, so the issue is dispatched again instead of being stranded behind a marker whose run never existed: it queues one comment (`run X never started, dispatching again (attempt n of 2)`) quoting the last lines of the run's `launch.log` (written by `launch.py`; `state/<req>.log` is the same stream). The set of issues comes from the request's `GitHub issue #N` / `GitHub issue(s) N, M` headers (the same parse `autobug.sh`/`autofrontier.sh` do). After 2 never-started runs for the same issue (counter `auto/neverstart-<n>`) the marker is KEPT, a comment carries the evidence and `HEALTH never-started-retry` goes to `alerts.log`: no third retry, a person must look. `vendor-guard.sh` (watch.sh, under `single.sh`) re-vendors drifted personal skills into the fork and commits them, because a drifted vendor makes every `squad start` refuse. `bash scripts/test-janitor-neverstarted.sh` covers it; `bash scripts/test-launch.sh` covers `launch.log`.
- janitor rule K: after rule J drops a never-started run it calls `retryfix.sh`, which re-arms the PR's `runof-*`/`queued-*` markers (max 2 retries) so autofix redispatches instead of waiting on a stale `queued-<pr>-<s9>`.
- janitor rule Q: a card still `In Progress` behind its `auto/disp-<n>` / `auto/bug-<n>` marker (10 min old) that no run works -- no live run, no run in its launch window, no run that landed, no open or merged PR naming the issue, no `fork-<n>-<sha9>` marker -- goes back to `Todo` (the bug card to `To triage`), its marker is dropped and one comment is queued (`STALE-CARD #N`). Rules E and O read a run's verdict, so a run that ended without one (rule C drops a zombie from `active-runs.txt`) freed nothing and `health.sh` reported `runs-board-drift` for hours; rule Q reads the state, not the run's history. A refused read touches nothing; a definite "not ours" is remembered for 10 min (`janitor-q-seen-<n>`). `health.sh` names the cards that still hold a marker in the `runs-board-drift` cause. `bash scripts/test-janitor-stale-card.sh` and `bash scripts/test-health.sh` cover it.
- janitor rule M (alert): an open, non-draft PR idle for 20 min that conflicts with its base or has red CI and no live run naming it gets `PR-STUCK #pr @ s9: why` (once per head, marker `janitor-stuck-*`), in `alerts.log` and a macOS notification (`JANITOR_NOTIFY`).
- janitor rule O: a run whose record ends with a verdict and holds no `land` pass landed nothing, so the issue must not stay behind its dispatch marker: every issue in its request has `auto/disp-<n>` / `auto/bug-<n>` dropped, so `autofrontier.sh`/`autobug.sh` dispatch it again in the next cycle. The loop-board card is rule R's (one owner, ADR-0029): R derives it from the run's record -- `Todo` after a failed run, `Blocked` after `needs-human` (the owner is the next actor), `Code Complete` after a pass with nothing landed -- and O names that column in its comment, writing none of its own; the bug card goes back to `To triage` on `$SG_BUG_PROJECT`. `failed` is not the only way a run ends without landing (#1847): a run that ended `needs-human` or `pass` with nothing landed left its `auto/disp-<n>` behind, and `autofrontier.sh` skips every issue holding that marker, so the frontier starved on a run that is over -- the cause `health.sh` reports as `frontier-idle`. The bug card is only moved when it still sits in the column the dispatch set (`$SG_BUG_DOING`), so a card another step moved past it is never downgraded; the marker is dropped either way, because nothing must hold a finished run's issue back. A run that landed work, an issue a live run works, and an issue an open or merged PR names keep their marker (`gh search/issues` for `Refs #N`). Once per run (marker `janitor-demoted-<run>`, so a re-dispatch is announced, never silent); the queued comment carries the run id, the verdict and its reasons. The board is written with `board.sh` (REST, no GraphQL quota). `bash scripts/test-janitor-disp-expiry.sh` covers the four states (ended-nothing, landed, live run, open PR) and `bash scripts/test-janitor-failed-dispatch.sh` the card demote.
- `wtclean.sh` rule L: loose detached worktrees in `~/wt-*` or `*/acpr.*` idle 10 min, clean and on a remote branch are removed (`WT CLEAN loose`); dirty or unpushed ones are reported once (`WT LOOSE`). `~/wt-e2e*` and `$SG_E2E_DIR`/`$SG_E2E_BUILD_DIR` are never touched (the fork suite reuses them).
- `health.sh` (rule N): writes `$SG_STATE/health.json` (`ok`, `causes`: watchd-down, fastloop-down, watch-stalled, never-started-runs, orphan-markers, stuck-prs, loose-worktrees) and alerts once per cause (`auto/health-<cause>`, `alerts.log`, `HEALTH_NOTIFY`). `health-install.sh` installs a launchd agent (every 120 s, RunAtLoad); `health-install.sh uninstall` removes it. Two of those causes are about work nobody is on, the failure mode where the loop looks healthy while doing nothing: `fork-parked` (a fork branch ahead of `SG_FORK_BASE` with no process in its worktree) and `frontier-idle` (an open `ready-for-agent` issue with no open PR while no run has started in 30 min).
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
     - `DEAD RUN: n run(s) without a verdict and no live squad process; k marked killed in their record` (killed by a session restart or crash: `scripts/heartbeat.sh` wrote the `failed` verdict, reason `killed/no-process`, into each run's record, so the hub shows the run as ended and the footer stops counting it; relaunch. The janitor does the same for a zombie run with other runs alive)
     - `STALE HEART run=<id> cpu=<n>s last node <node>, killed: heartbeat_stale` (`scripts/heartbeat.sh`, last step of every cycle: a live run whose process tree has not moved a CPU second for `HB_FLAT_MIN` and whose record has taken no new event for `HB_RECORD_MIN` (both default 5 min) is wedged, not idle, and was killed with one line each; its own deadline is a long way off and a loop that stopped looks exactly like a loop with nothing to do. Both signals are required — a long Bash call (a full test suite, a build) writes no record event for minutes while its process works — and a run whose process is gone is the dead-run pass above. The killed verdict comes from `squad.killed` (its one writer) with the reason `killed: heartbeat_stale`; the run's branch and worktree are left exactly as they are. Whole pass: `squad/heartbeat.py`; test: `scripts/test-heartbeat.sh`)
   - `MODEL_FAIL`/`MODEL_SWITCH` for review arrive as queued issue comments (cqflush log). `STALL E2E` and `QUOTA_GUARD` are yours to raise: run e2e with `run_in_background` and a timeout, and check quota at each status check.
5. Check the latest reliable Anthropic weekly quota state before the initial dispatch. If remaining quota is 10% or less, exclude Anthropic models from new autonomous squad work.
6. Recompute the complete initial frontier and dispatch it immediately.

## Launching, and staying awake in `/goal` mode

- **Optimistic stacking (owner's go-ahead, 2026-10-09).** Don't wait for a blocker's merge: `scripts/stack.sh <issue>` answers `READY` (all blockers closed: branch from origin/base), `STACK <pr> <branch>` (exactly one open blocker, whose open PR is based on the base branch and has no failing CI: branch from `origin/<branch>`, open the PR with base `<branch>` and "Merge after #<pr>" in the body), or `WAIT <reason>`. The `## Blocked by` section is read as a bullet list: it ends at the first line that is neither blank nor a bullet, so a trailing prose line that merely names another issue (`Blocks #N: ...`) is information for a human, never an edge (`scripts/test-stack.sh`; the same parse in `forkclose.sh` and `reconcile.py`). Limits: one level (never stack on a stacked PR), one open blocker, parent CI not red. Put every `STACK` issue in the frontier's single `squad start`. The autopilot never merges a PR whose base is not the base branch; when the parent merges it retargets the PR (`RETARGET`) and keeps the parent's last sha in `auto/stack-<pr>`, so a squash conflict is fixed by `git rebase --onto origin/<base> <that sha>`. The review of a stacked PR covers only its own diff over the parent branch and reads surrounding code from that branch. Work that lives in a local fork stacks the same way on the parent's fork branch.
- **Every request carries a DONE CHECK** (`mkreq.sh` appends it): the child ends with `ac_commands`, the checks it ran as evidence, so the final `ac-verify` gate verifies instead of failing with `no_ac_commands`. The gate runs in the main checkout, so work on a PR branch is wrapped as `scripts/acpr.sh <branch> -- <command>` (throwaway worktree at `origin/<branch>`); fork-only work as `cd <fork worktree> && <command>`. Ask for the **narrowest** command that proves the done condition — the test file or suite the change touches, not the project's whole run: `workcmd.py` runs the entries one at a time and caps each one (`TIMEOUT_S`, default 300 s, moved by `SG_AC_TIMEOUT`), so `shell-suites.sh` (159 s, #1785) is never an `ac_command` — run it yourself and declare the named suite. The hub shows a run that landed with no command to verify as `unverified` (its own color), never as a plain `landed`.
- **Worktree cleanup is automatic.** `wtclean.sh` (every watch loop) removes a child worktree once its run has a verdict, the tree is clean and HEAD is on a remote branch; a finished worktree with unpushed commits is kept and reported as `WT UNPUSHED` (cherry-pick and push it, then it is cleaned). The same pass also drops `acpr.sh` acceptance trees older than 2 h (a killed gate), fork-only worktrees (`~/wt-as-<n>`, `~/wt-fr-<n>`) once the issue is closed and the branch merged into the fork base (branch deleted with `git branch -d`), and `~/wt-*` PR worktrees once their PR is merged. The repo deletes a PR's head branch on merge (`delete_branch_on_merge`).
- **Ship skill changes with the fork.** After editing this skill, run `scripts/sync-to-fork.sh` (copies it to the fork's `contrib/squad-goal`, refusing any name from the local `$SG_STATE/sync-deny` list) and commit on `local/next`, with a CHANGELOG line.
- **Refresh the live skill from the fork.** Work children land on `local/next` reaches the running loop only through `scripts/refresh-from-fork.sh`, the counterpart of `sync-to-fork.sh`: it copies the fork's `contrib/squad-goal` over this directory (atomic per file) and never touches `scripts/models.json`, per-repo profiles or `$SG_STATE`. `refresh-from-fork.sh --check` changes nothing and exits 1 naming every shipped file where live and `local/next` disagree. `sync-to-fork.sh` refuses (naming the files) when the fork copy holds work the live copy lacks, and the refresh refuses when live holds edits not yet in the fork; `SG_SYNC_FORCE=1` / `SG_REFRESH_FORCE=1` discard them explicitly. Both track the last synced state in `$SG_STATE/fork-base`.
- **A landing publishes what it changed (#31).** `scripts/publish-landing.sh` runs from the watch cycle after `forkclose.sh` (under `single.sh`) and is the only step that refreshes the live skill from the fork: when the fork head is recorded green (`$SG_STATE/auto/basetest-<sha>` with the head's tree) and `refresh-from-fork.sh --check` names a file, it runs the refresh and then restarts the view (`hubctl.sh --restart`, which prints the URL; an open page reloads on the new `/api/version`) only when a file the view serves changed: `hub.py`, `hubmux.py`, `panes.js`, `address.sh`. It holds forkclose's own lock while it runs and waits, saying so once per head, while a landing pass is mid-flight. It refuses, naming each file and changing nothing, when the live skill holds `SKILL.md` or a `scripts/` file the fork does not ship (bring it over with `sync-to-fork.sh` or remove it): that refusal is what shows drift between the running loop and the repository. A second run finds nothing to do. A failed restart stays pending (`$SG_STATE/auto/publish-view-pending`) and is retried. Test: `scripts/test-publish-landing.sh`.
- **agent-squad is a local fork.** We have no write access to Korck-lab/agent-squad. The repo profile sets `SG_SQUAD` to the fork checkout (branch `local/next` on top of `local/base-0.2.0` = the installed 0.2.0), so runs use the fork. Work on the squad itself (ADR-0272 P1-A: runner, script format, dashboard) is committed there locally — no push, no PR; review the local diff — and the owner sends it upstream as a ZIP from `scripts/export-agent-squad.sh`. Never patch the plugin cache again.
- **No dashboard tabs.** Only the Squad hub (http://127.0.0.1:7700/<profile>) is used. `launch.py` sets `SQUAD_HEADLESS=1`, so the run's dashboard server starts for the hub but no tab opens. Never run `/agent-squad:dashboard` or `squad dashboard` without `--no-open`.

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
- coding model: the FIRST entry of `coding_models` in `scripts/models.json` whose `hours` window contains the current local hour (no window = always; the hub's "squad models" panel edits both, and `models.json` is the only source of truth, never a model typed in this file or in a repo's CLAUDE.md). Read it at every dispatch; the next eligible entry is the fallback. `scripts/launch.py` computes it and refuses a request that names another model, printing which entry it wanted and why (`SG_ALLOW_MODEL=1` overrides; a chain with every entry out of window is an error, never a silent fall-through). (2026-10-09: requests hard-coded deepseek while the panel said Sonnet; the panel wins.) `launch.py` also reads the same switch against the model every node of the graph runs (an alias such as `opus` resolved to its provider id) and refuses a start on a switched-off one (#60).
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

`$SG_STATE/cq/<owner>__<repo>#<issue>-<tag>.md` (`$SG_STATE` is `~/.cache/squad-goal/<profile>`; `sg_cq_file <issue>-<tag>.md` from `address.sh` prints the path for the repository in force)

`cqflush` posts it with retries, to the repository the name carries (a name no profile knows is retired as `.dead`, never posted).

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

Autonomous frontier: `scripts/autofrontier.sh` (every ~3 min) dispatches every open `$SG_FRONTIER_LABEL` issue that `stack.sh` calls READY/STACK, has no open PR, no `auto/disp-<n>` marker and no live run working it, all as the children of one run (one squad start) up to the envelope's limit (the dispatch node's `max_children`, and `run.max_children` less the children live runs hold; the free run slots when the graph names none), the issues past it waiting for the next run, with the profile's `SG_FRONTIER_PREAMBLE` as shared rules. `stack.sh` lets a blocker whose card is in Testing or Code Complete pass (owner's pick 2026-10-10: dispatch on Code Complete). A Code Complete blocker with a PR stacks on it; one with fork-only work hands its unmerged fork branch to the child (`READY FORK <br>`), which merges it into its own fork branch first. A PR that merely says "Refs #N" proves nothing.

Fork-only close: `scripts/forkclose.sh` (every loop) takes an open Code Complete issue with no open PR and a fork branch (`SG_FORK_BRANCH`, `%n` = issue) ahead of `SG_FORK_BASE`, merges it (`--no-ff`) in `SG_FORK_DIR`, runs `SG_FORK_TEST_CMD` through `test.sh` (card -> Testing, so its dependents dispatch at once), then closes the issue with the evidence (green) or reverts the merge with `git revert -m 1` and moves the card back to In Progress (red). A merge conflict aborts and is reported. Nothing in that path may wait on something that can no longer arrive. A card left at In Progress by a fix run that died is re-armed (`FORKCLOSE ORPHAN #N`) when the branch is ahead of the base, no process has a cwd inside its worktree, and its head has been quiet for `SG_FORK_ORPHAN_MINUTES` (20): the card is derived state forkclose itself owns (it writes In Progress on a red and Done on a green), never the gate. `auto/fork-<n>-<head>` expires after `SG_FORK_MARKER_MINUTES` (45) with no new commit (`FORKCLOSE RETRY #N`), because "wait for a real fix commit" is otherwise released only by a run that is already dead. Owner-approved Done rule for fork-only issues (#1180).

Land first, gate after (#85): a landing is two phases. Phase one merges the eligible branch into the base with no suite lock (`FORKCLOSE LANDED #N`), so dependents (`## Blocked by`) are released at once. Phase two gates the landed head under the machine-wide `gate` lock (`serial.sh gate`, the lock `shell-suites.sh` and the pytest gate hold): it never runs beside a child's gate; it waits and says so once, naming the holder serial.sh recorded (`FORKCLOSE WAIT-GATE: waiting for the gate lock (holder <pid>)`), and once more when it has it (`FORKCLOSE GATE-LOCK`). No gate runs before a merge. A green re-gate records the head as a tested base (`basetest-<sha>`); a red one keeps its rule (the failure is named, the issue stays open, the merge is reverted, the fix run arms), except that a base nobody recorded green is first gated on its own, after the revert, so a red base is never blamed on the branch: it parks every candidate (`FORKCLOSE BASE-RED #N`: no merge into it, no revert, no fix run) until its solo retry is green. A dirty path in the shared checkout holds only the merge that would write it; a staged change holds every merge (git refuses a dirty index); the `FORKCLOSE WAIT` line names the paths, five then a count. Test: `scripts/test-forkclose-split.sh`.

GitHub access is REST only in the loop (`board.py` for cards, REST diff in review, REST in reconcile; children open PRs with `gh api --method POST`): the GraphQL quota is shared with every `gh pr`/`gh issue`/`gh project` command and ran out.

Board moves go through `scripts/board.sh` (`$BOARD`), which flags the hub to refresh the board within 2 s instead of its 60 s cycle; `childcc.py` runs every 30 s with the autopilot.

A child that finishes with outcome pass moves its issue to Code Complete at once (`scripts/childcc.py`, every watch loop, once per child), with a comment carrying its first reason; the panel may still be running its siblings. A run that already has its verdict (tiny and fork-only runs pass → verdict in under 1 s, never seen live) is backfilled too, but only when the card is Todo/In Progress/Blocked, never past Code Complete. `scripts/test-childcc.sh` is the regression test (stub gh and board).

Merge stalls: the autopilot prints `HOLD #pr @ sha: <reason>` (once per head and reason) whenever an APPROVE PR does not merge (label hold/needs-human, SG_AUTO_MERGE off, stacked base, `merge after #N` still open, state not clean, threads opened after the cache); janitor rule G reports `READY-NOT-MERGED #pr` for an APPROVE + clean PR unmerged after 5 min and runs one autopilot pass (once per head, never merges itself; again after 15 min if still open). Tests: `scripts/test-autopilot-hold.sh`, `scripts/test-janitor-g.sh`.

Watch lanes: `watchd.sh` runs two loops. `watch.sh` is the 90 s cycle; `fastloop.sh` is the fast lane (childcc, autopilot and both dispatch passes — `autofrontier.sh` and `autobug.sh` — every 30 s), so a slow cycle step never delays a merge (#1239: APPROVE + clean waited ~5 min behind a 339 s forkclose) and a dispatchable issue is picked up in ~30 s instead of on the cycle's beat, up to ~6 min late, which is what `frontier-idle` kept reporting (#1845). `forkclose.sh` runs in the background under `single.sh <name> <cmd>` (non-blocking lock: a second call while the first is alive exits 0), and the fast lane's steps use it too, so no step overlaps its own previous run; the two dispatch passes are called bare because each takes its own `single.sh <stage>` lock inside, so a pass already running is skipped by whichever lane comes second (they go out detached: a pass ends in a `squad start` that runs for minutes and must not hold the 30 s beat). `reconcile.py`, the most expensive step of the cycle (~167 s each), is detached the same way under its own lock and files its stream in `$SG_STATE/reconcile.log`; `wtclean.sh` runs on every third cycle (the trees are almost always clean and ~17 s of every cycle is 10% of the loop — run `scripts/wtclean.sh` for an on-demand pass). Standalone `watch.sh` (no watchd) still runs childcc, autopilot, autofrontier and autobug itself while `fastloop.pid` is not alive. `cycle.log` gets a `CYCLE <n>s` line per watch cycle and `AUTOPILOT-GAP <n>s` when two autopilot starts are more than 120 s apart: if either shows up, a step is starving the loop. Restart watchd after editing `watchd.sh`/`fastloop.sh` (the supervisor itself is not re-read).

The fork gate is the GUI suite AND the squad's own shell suites (#1392, ADR-0273): `forkclose.sh` runs `scripts/fork-gate.sh "$SG_FORK_TEST_CMD"`, which runs the GUI command and then `scripts/shell-suites.sh` (every `test-*.sh` next to it, offline, every failing suite named, exit 1 on any) and is red when either is red. Run `bash scripts/shell-suites.sh` after editing any squad script.

GUI suites never run at once: Mesen's SingleInstance (global mutex) makes a second emulator pass its args to the first and exit 0 ("application exited (0) before the hook came up"), which looks like a code failure. A profile's GUI test command (`SG_FORK_TEST_CMD`) runs through `scripts/serial.sh gui-suite <cmd>`, a per-name lock with stale-pid takeover (`scripts/test-serial.sh`); fix runs inherit it because the forkclose prompt quotes the command. After editing a profile, restart watchd (its env is frozen at start).

Janitor rule H compares board state with the issue's own history (its loop comments; the REST timeline has no project-status events), once per 10 min, report-only: `RED-LOOP #N` after 3+ fork-suite REDs with no green fork close (NEEDS OWNER: the loop is not converging), `STATE-REGRESSION #N` when a card is Todo/Blocked although the history reached Code Complete/Testing, or Done while the issue is open. Test: `scripts/test-janitor-h.sh`.

`forkclose.sh` respects `## Blocked by` between fork halves: an issue whose blocker still has an unlanded fork branch (`gui/<M>` ahead of the base) waits (`FORKCLOSE WAIT #N: the fork half of #M is not in <base> yet`, once per blocker set), because its tests import the blocker's files and merging it early only goes red and burns a fix round. Test: `scripts/test-forkclose-wait.sh`. A branch that is already an ancestor of the base but whose files a sibling's reverted merge took away (0 ahead, tip files missing) is not "landed": forkclose relands it as a merge commit that restores the files (so the close and the `revert -m 1` work as usual), and dependents wait for it. Test: `scripts/test-forkclose-reland.sh`.

Coordinator test passes (post-merge e2e, a fork suite for fork-only work) go through `scripts/test.sh <issue> [-C <dir>] -- <command>`: it moves the card to Testing, logs to `$SG_STATE/tests/`, queues the result on the issue, and the hub lists it as `test #N` with the live log as its panel. A test run by hand outside it is invisible on the hub.

Automatic bug fixing: `scripts/autobug.sh` (watch.sh, every ~6 min) dispatches every open `bug` issue with no open PR naming it, no `auto/bug-<n>` marker and no skip label (`needs-owner`, `no-auto`, `wontfix`, `question`), then open `debt` issues the same way only when no bug is startable, all in one squad start within the free run slots: one child and one PR each, TDD with the RED output in the PR, accepted ADRs binding, owner carve-outs returned as fail. It comments the dispatch on each issue and moves the cards (task board, and the bug board when the profile sets `SG_BUG_PROJECT`). `autofrontier.sh` skips `debt` like it skips bugs. The loop never retries: delete `auto/bug-<n>` to dispatch again. `SG_AUTO_BUGFIX=no` turns it off.

Idle bug hunt: `scripts/autohunt.sh` (watch.sh, every cycle after autobug) starts one read-only hunt when the loop has been idle for `SG_HUNT_IDLE_MINUTES` (default 10): no live squad run, no gate/test holding the `gui-suite` lock or running `forkclose`, nothing startable for autobug or autofrontier, no PR waiting on a review round, `auto/off` absent. One hunt at a time (`hunt_going` while a hunt run is alive). `SG_HUNT_COOLDOWN_MINUTES` (default 60) and `SG_HUNT_PER_DAY` (default 6) count only when a hunt-filed bug (`Filed by autohunt` in the body) has a fix start (`auto/bug-<n>` written by autobug); a hunt that files nothing leaves no cooldown and no day count, so the next idle check may hunt again. `SG_AUTO_HUNT=no` turns it off. `--file` titles every issue it files `[SQUAD] <title>` (the prefix is added unless the title already starts with `[`), which is how a squad-tooling issue reads apart from a product one in the issue list. Logs `HUNT started`, `HUNT filed #N`, `HUNT none`. The child files only real, reproduced defects (label `bug` only, at most 3, never a duplicate); architecture trade-offs go `/adr`. Test: `scripts/test-autohunt.sh`.

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

Model fallback is role-based and lives in `~/.claude/skills/squad-goal/scripts/models.json` (the squad workflow schema rejects unknown fields, so it cannot live in `dynamic.graph.json`). Squad coding ids must also be in that workflow's model allow-list. `review.sh` already walks `review_models` and queues `MODEL_FAIL`/`MODEL_SWITCH` comments. Each model try is killed after `review_timeout_seconds` (default 480; `MODEL_TIMEOUT`, then the next model, no retry), and every try's latency goes to `$SG_STATE/review-times.tsv` (order `review_models` by that data, with the owner's go-ahead). Fix rounds are reviewed incrementally: when the last review asked for CHANGES and the new head descends from the reviewed sha (`review-<pr>.sha`, compare status `ahead`) with a delta of at most `review_delta_max_lines`, the reviewer gets the previous findings plus that delta; a rebase or a first review gets the full diff. The autopilot runs every 30 s and `revci.sh` polls CI every 15 s; for coding, name the next `coding_models` entry in the continuation run's request. `models.json` may carry `efforts.<chain>.<model-id>` (low|medium|high|max; the default is medium, which is stored as an absent key); when dispatching a chain entry, pass that effort in the request/review call if set, otherwise use the default (medium). It may also carry `hours.<chain>.<model-id>`, a local window `"HH-HH"` (start inclusive, end exclusive, wrapping midnight: `22-07`); **the default is all day** — a model with no window always runs, so a window is an opt-in restriction. `review.sh` skips an out-of-window model exactly like an unhealthy one (`MODEL_HOURS` on the issue, no `MODEL_FAIL`) and the chain moves on; `launch.py` applies the same rule to `coding_models`. Both maps are edited per model from the hub's models panel (each row has its own `effort` and `hrs`), and both live beside the chains, never inside the string arrays other scripts `jq`.

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

Visual checks (render versus wireframe) use the `vision_models` chain, Grok 4.6 first (known-image probe passed 2026-10-07). Copy the PNGs with `/usr/bin/git show` (the rtk hook corrupts binaries) into an isolated folder, `cd` there and run `grok -p "Look ONLY at the images here; do not read other files; say CANNOT_SEE_IMAGE if you cannot" -m grok-4.6 --always-approve`; a repo in reach lets Grok answer from source instead of pixels. If Grok fails, fall back to Claude's own vision and say so. The `hours` rule applies here too: a `vision_models` entry whose window excludes the current local hour is skipped exactly like an out-of-window coding or review model, and the chain moves on. No script enforces this one — it is on whoever picks the vision model. A visual failure never blocks unrelated work.

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

Fork-only requests (#1411): ONE rule decides it, `scripts/forkonly.sh` (`sg_fork_only <issue>`): the `fork-only` label, or (fallback) a "Fork-only work" line in the issue body. Every request builder asks it (`autobug.sh`, `autofrontier.sh`, `autofix.sh`, `forkclose.sh`'s conflict and red-suite builders, `mkreq.sh`). A fork-only issue reaches its child with the fork preamble ONLY (`sg_fork_preamble`: worktree from `SG_FORK_BASE`, branch `sg_fork_branch <N>` — the one source, `SG_FORK_BRANCH` with `%n` = the issue, which `autobug.sh`, `autofrontier.sh`, `stack.sh`, `mkreq.sh` and the land `forkclose.sh` all read — commit locally, never push, never open a PR, never ask about one; the local commit is the deliverable): no `origin/<base>`, `fix/<N>`, "open the PR" or `prepush.sh`. A request whose children are all fork-only carries a `FORK-ONLY: all` line, which makes `mkreq.sh` append the fork DONE CHECK and `req-checklist-fork.txt` instead of the PR ones. `mkreq.sh` refuses a fork-only dispatch whose own instructions name a branch `forkclose.sh` would not resolve (the MesenAI habit `fix/<N>-<short-slug>` reaching a fork child is #1831: the commit lands where no close pass looks) and one whose setting cannot name a landing branch at all (no `%n`, or a ref `git` refuses), before the request file exists — so before `launch.py` makes a run folder. Safety net: `scripts/forkrescue.py` (every watch loop) takes a fork-only child that ended needs-human or fail with a commit on a `fix/<N>-x` branch ahead of the base, fast-forwards `gui/<N>` onto it (never a push), moves the card to Code Complete and queues a note on the issue, so a "where does the PR go" question never reaches the owner. Regression test: `bash scripts/test-forkonly.sh`, and `bash scripts/test-forkbranch-source.sh` for the single source of the branch and the refusal above. What the rescue cannot take — a branch whose run is long gone, or two branches answering one issue number — is reported instead of parked: `scripts/forkstrand.py` (every watch loop, after the rescue) names, once per head, a branch ahead of the base whose diff is fork work for an issue whose card is Code Complete/Testing but is not `sg_fork_branch <N>` — the branch, the commit and the ref the land reads — on the issue and in the loop's log, moving nothing; two branches answering one number are refused with both named rather than picked, and `forkrescue.py` refuses that pair too (`bash scripts/test-forkstrand.sh`). **Tickets for squad tooling** (anything under the agent-squad fork, e.g. `/to-tickets` output about squad scripts) are created WITH the `fork-only` label (`gh api --method POST repos/<owner>/<repo>/issues -f 'labels[]=fork-only' ...`); without it the label rule cannot protect the ticket and only the body-line fallback does.
