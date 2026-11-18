---
name: squad-goal
description: Autonomous delivery loop for GitHub issues through agent-squad (per-repo profile) — dispatch the full startable frontier in parallel panels, keep GitHub issues and board live, review independently, recover stalls and model failures, merge into the base branch, run the e2e command, and continue until every issue is closed, its card is Closed, its work is promoted into `stg` and the live copy is published. Use for "/squad-goal <issues>", or "implement issues #N… with the squad and land".
---

# Squad goal

Drive a set of GitHub issues (`$SG_REPO`, board = project `$SG_PROJECT`) from ready to Closed, with no owner in the loop. Arguments: the issue numbers, or a spec issue whose sub-issues are the work.

The owner may wrap the call in `/goal /squad-goal <issues>` so the loop survives across turns. Either way, **done** = every listed issue closed with evidence, its card in Closed, its work in `origin/$SG_BASE`, that base promoted into `stg` through one pull request, `stg` moved into `main` by the loop on a green complete CI tier, the live copy published from a tree on `stg`, and a green e2e on the final `origin/$SG_BASE`. A landing is not a delivery and a promotion is not a publish: each is its own step, and the goal holds until the last one.

**A defect in the loop's own machinery is never dispatched to the loop** (ADR-0146). §9 holds the whole procedure, from the
finding to the restart. When the task is to monitor the loop while it runs (`/goal /autonomy` with a monitoring mandate),
§9 and the 20-minute check of §10 are the working set, and `contrib/squad-goal/docs/loop-monitor.md` states the same
mandate in one page.

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

A branch pushed but not yet landed, under review, waiting for CI, waiting for e2e, or not yet fully validated is NOT by itself a blocker.

Children may start from work that has not merged when the dependency is sufficiently stable to make useful progress. Point them at the dependency branch when necessary and require a rebase onto `origin/$SG_BASE` after the dependency merges.

Split blocked work when a safe independent portion can proceed.

Only a real semantic, file-ownership, ordering, or correctness dependency may prevent dispatch.

Prefer useful speculative work plus later rebase over idle capacity.

CI running, review pending, another child working, or a merge waiting is never a reason to leave unrelated capacity idle.

### 3. Stalls are recoverable

No run, no branch landing, no review, no CI job, no model call, no e2e job and no orchestration step may wait indefinitely.

Track last meaningful progress for every active run and work branch.

If an active unit stops producing meaningful progress beyond the configured stall timeout:

1. inspect its current state;
2. preserve all recoverable work;
3. post the stall and the recovery action to the affected issue;
4. terminate or abandon only the stuck execution when necessary;
5. resume through a continuation/fix run;
6. recompute the complete frontier immediately.

A stalled child MUST NOT stall unrelated work.

Repeated recovery failures escalate through `autonomy` rather than silently waiting for the owner.

A `loop-bug` issue stays open until its fix is live, and the stop reads the issue state alone (ADR-0149). The step that makes a fix live closes it: `live-guard.sh` publishes the repository copy over the running skill, and the publish closes every issue named by a `Closes #n` trailer in the commits it publishes, with one comment naming the sha and the published time (#732). A `TDD RED` commit names its issue in the subject and carries no closing trailer, so it closes nothing (ADR-0136). Do not close the loop-bug issue by hand after a landing: between the landing and the publish the loop still runs the old copy (ADR-0140). The whole procedure, from the finding to the restart, is §9.

Background machinery: run `scripts/watchd.sh start` once from the target checkout (idempotent; a supervisor restarts watch.sh if it dies; events go to $SG_STATE/watch.log). Arm the Monitor with `scripts/watchtail.sh` only to read that log; the Monitor expiring no longer stops the branch passes, heartbeat or the reconciler. Reported verdicts and stalls persist in $SG_STATE/watch-seen.txt, so a restart does not replay them; delete the file to re-announce everything. `watchd.sh stop` ends the supervisor and watch.sh. After a plugin update, run `scripts/watchd.sh reload` from each profile's target checkout instead of stop and start: a stop kills live runs and gates, a reload keeps them. A publish that must reach the live copy restarts it with `scripts/watchd.sh restart`, which drains the live runs first (ADR-0162). Never run watch.sh directly under a Monitor while watchd is up: the branch passes, the two dispatch passes (autofrontier, autobug) and the reconciler would run twice.

Requests: build every request with `scripts/mkreq.sh <name> <body-file>`; it appends `req-checklist.txt` (pre-push checks: verbatim decision quote, prose-vs-code grep, AC drift flag, RED first, final-head evidence, ADR verify scripts). Put a `PANEL: <id>` line in each sibling's body (or export `SG_PANEL`): `mkreq.sh` then names both requests when it refuses two of one panel that share a file (ADR-0274). `mkreq.sh` refuses any request naming a file already in flight — an open work branch that changes it, a request of any panel whose run is still live (no verdict in `runs/<run>/record.jsonl`, the run folder being matched to the request `launch.py` copied into it) or that is still inside the launch window (`SG_SLICE_REQ_TTL`, 30 min), or an unmerged `gui/*` branch in the fork checkout that touches it, which is the only place a fork-only child's slice shows up (#1519) — exiting non-zero and writing no request. So the file of another work branch, of another request or of an unmerged fork branch still refuses exactly as before. A request whose run recorded a verdict, and which no live run carries, is history: request files are never deleted, so without that the guard's in-flight set only grew and refused forever against runs that ended days ago (#1529). The dispatchers (`autobug.sh`, `autofrontier.sh`) free the dispatch marker they wrote when `mkreq.sh` refuses (#1529): a dispatch that never happened must not mark the issue done. Export `SG_SLICE_FORCE=1` to dispatch a deliberate overlap anyway: it is the documented way past the guard, and it announces each overlap on stderr so the decision stays visible. Never hand-write `req/*.txt` without the checklist; the body must still start with the `IMPORTANT: your first and only action…` line.

A run verdict of failed whose only reason is ac-verify:no_ac_commands and that has a passing land event prints as "landed (gate: no_ac_commands)": the gate has no AC commands to run; check the branch head sha moved, not the verdict word.

Pre-dispatch: run `scripts/acscan-run.sh <issues>` on every issue before dispatch; any finding goes to the panel before code.

Hub: `scripts/hubctl.sh [--restart] [port]` (default 7700, loopback, read-only) starts `scripts/hubmux.py` when no hub answers on the port, and prints the URL of the cwd's repository. One process serves every profile under `/<profile>`: that repository's `hub.py` page, which lists every live run dashboard and embeds it (sidebar + grid of running runs). `/` lists the known repositories and their live workers; a path no profile owns answers 404 with that list. A port held by anything that does not answer `/api/hub` as the squad hub is refused (#20); `--restart` stops the squad hub on the port first and starts it from the files on disk (`publish-landing.sh`, #31). Open http://127.0.0.1:7700/<profile> once; `launch.py` opens no per-run dashboard tab (`SQUAD_HEADLESS=1`). The left pane's footer strip reads the machine: `CPU 62% · RAM 71% · GPU 27%`, one in-process sample every `LOAD_SAMPLE_SECS` (5 s) served from `/api/load`, with `?` for any source the hub cannot see (a source that raises, or answers with a string, a negative or NaN, is unavailable — never a 0 and never a stale number). GPU is the accelerator's own counter via an unprivileged `ioreg`; CPU and RAM come from Mach's counters inside the hub, so a page view spawns nothing and a saturated machine never makes the hub part of the load. A cell whose own reading is at or above `LOAD_ERR_PCT` (95%) is painted red — that attribute alone, the number that reached it, never the whole line — and red wins over the amber below (#1816); the text, the order and the rounding are unchanged, so the strip reads exactly as it read. While machine CPU stays at or above `LOAD_WARN_PCT` for more than one sample (`LOAD_WARN_SAMPLES`), the run whose process tree owns at least `LOAD_MIN_SHARE_PCT` of that CPU carries a `cpu N%` marker on its card: attribution walks the process tree by **worktree path** (the run folder plus the worktrees its record names), never by process name — every child of every run is the same `claude`, `dotnet` or `python`, so a name would credit whoever started first.

### 3b. The branch passes: review, fix, land

The pull-request path was removed (#361, ADR-0145): no loop script creates, reads or merges a pull request. The work branch is the unit of work, and three passes keep it moving. Each names its verdicts in one line.

`watch.sh` runs `branchreviewpass.sh`, `branchfixpass.sh` and `branchland.sh` every cycle, each under `single.sh` so two passes never overlap; `SG_LAND_PAUSED` set in the environment skips all three. Together they keep every pushed work branch moving without the session:
- `REVIEW`: `branchreviewpass.sh` reviews each work-branch tip that has no kept review, through `branchreview.sh` (the review chain from `models.json`). The verdict and the sha it read are kept under the branch key (`$SG_STATE/review-<branch with / as _>.{md,sha}`). At most `SG_REVIEW_PASS_MAX` (3) branches a pass, `SG_REVIEW_PARALLEL` (3) at once; `SG_REVIEW_TRIES` failed tries at one tip are a stop that expires after `SG_REVIEW_STOP_TTL` (1800 s).
- `FIX`: `branchfixpass.sh` dispatches ONE agent-squad fix run for each reviewed tip whose review says `CHANGES` with at least one Blocking finding. The request is `branchfix.sh`'s text, through `mkreq.sh` and `launch.py`: check out the branch, commit on top, push to the same branch. Once per reviewed tip (`auto/branchfix-<key>-<sha9>` holds the run id); a launch that starts no run keeps no marker, so the next pass retries the tip. At most `SG_FIX_PASS_MAX` (3) fix runs a pass.
- `LAND`: `branchland.sh` lands a branch whose review approved its tip and whose checks are green (success, skipped or neutral; or, with zero check runs while Actions is off, the local gate at the tip rebased onto the landing branch). The review is classified by the one parser (`revformat.py --classify`, #1376) as stale, malformed, changes or approves; the pass reads no first-line substring and no `--summary` count, so a review outside the accepted shape is malformed, holds its own branch and names the shape, and a corrected file at the same tip lands on the next pass. `ffland.sh` does the landing: rebase the branch on the base, then fast-forward push the tip straight to `$SG_LAND_BRANCH` (default `dev`), and delete the branch. The issue is then closed through the API, one delivery comment is queued and the card moves to Done; each landing adds a row to `$SG_STATE/landed.tsv`. A red, running or missing check is one `BRANCHLAND WAIT <branch>: <why>`; a push a ruleset refuses, a rebase that conflicts or a branch with nothing ahead is one `BRANCHLAND REFUSE <branch>: <why>`, kept as a marker so it is said once per head and reason.
- `STOP`: a fix round fails when the review of the next tip carries the same findings (line numbers, shas and whitespace removed). `auto/branchfix-fp-<key>` keeps the last round's findings and `auto/branchfix-same-<key>` the count of failed rounds in a row; at `SG_BRANCHFIX_STALLS` (default 2) the branch is stalled and its rounds stop, with one status line on the issue and the label `frozen:owner`, once. Changed findings restart the count (ADR-0134). `revformat.py` reads both (`--fingerprint`, `--stall`, `--record-stall`), so a child fix round asks the same rule.
- `janitor.sh` (every watch cycle; `JANITOR_DRY=1` only prints, `touch $SG_STATE/auto/nojanitor` turns it off): kills leftover processes whose cwd is inside a finished run folder (3 min after the verdict), kills the dashboard of runs finished for an hour, drops zombie runs (no verdict, idle 20 min, no process, last call dead) from `active-runs.txt`, reports two live runs on one issue (`DUP`), and removes an old `auto/disp-<n>` only when its request failed, the issue is open and no work branch names it. It does not clean worktrees (`wtclean.sh` does).
- A review is a short, topic-grouped checklist (#1777), and `revformat.py` owns that shape: line 1 `VERDICT: APPROVE|CHANGES` (the exact form the branch passes and the hub parse), then a `## Blocking` and/or a `## Debt` section, whichever is non-empty, Blocking first — the section a finding sits in IS its severity, and `is_finding` is the one predicate that reads it (the check, the `--summary` line `branchfixpass.sh` reads and the debt filer all ask it); a fix round (`revformat.py --fix-round`, the delta mode of `review.sh`) may add a `## Resolved` section for the previous findings it answers, whose bullets are answers and not findings, so a fix round whose every Blocking finding is resolved may APPROVE with `blocking=0` and the debt-only path of ADR-0273 sees it — a full review that writes `## Resolved` is a format failure instead; under a section one bullet per finding, at most two lines, `file:line — defect — concrete failure — fix` (the location may also be `path:symbol`, a file alone, a whole-change word such as `Repo-wide`, or an acceptance criterion such as `AC1`; an empty section may hold a wrapped marker such as `(none)`), findings that share a theme under a `### <theme>` heading, most severe first; no preamble, no restating the diff, no closing summary, and the whole review inside `review_max_lines` of `models.json` (the cap is a config constant, never a number in the prompt). The prompt of both review modes carries that shape, and it is not written in the prompt (#1835): `revformat.py --shape` (with `--fix-round` for the mode that has an answer section) prints it as a template — the smallest answer the check accepts, its verdict and `file:line` in angle brackets — and `review.sh` embeds that printed text verbatim, so a wording the check cannot parse turns `test-revformat.sh` red instead of costing a review round; an answer outside the shape is a `MODEL_FORMAT` failure of that try, never prose passing silently as a review: with more than one reachable model the same model gets one repair try with the complaint, then the chain moves on and the issue says so; with one reachable model the failure spends a retry of that model. The verdict is still canonicalized onto line 1 (a bold or lower-case one is rewritten in place); what stood in the way of the shape then fails the check. The fix run's reply to a review is ONE issue comment in the same shape: one bullet per finding, `fixed` or `not fixed` — the one-line reason — the check that proves it, ending with `Refs #N`. `bash scripts/test-revformat.sh [revformat.py]` is its regression test.
- `bash scripts/test-review.sh [review.sh]` is the regression test of `review.sh` (stubbed gh and claude): a verdict is accepted with rc 0, a prose answer is a format failure (BLOCKED, `MODEL_FORMAT` queued), and a push during the review ends it BLOCKED. Run it after every edit of `review.sh`, and never edit a script a running instance is executing (bash reads it incrementally): stop or wait for the instances first.
- `python3 scripts/test-hub-e2e.py` (run it from a checkout: the hub reads that profile) is the e2e of the hub, on a throwaway copy of this skill on its own port, so the live hub and the real `models.json` are never touched. It checks every `/api/*` the page calls, that only our own Origin may POST, and that the models panel a person sees is right: the rows show their `hrs`, an edit wakes Save, Save writes `models.json`, the value survives a reload, a garbage window never enters the field, and nothing in the sidebar overflows its box (that last one caught a row too wide for the 300px column). Needs playwright (python) plus Chrome; it prints SKIP and exits 0 without them.
- `bash scripts/test-branchfix-pass.sh [scripts dir]` is the regression test of the fix pass (#401, stubbed gh, `mkreq.sh` and `launch.py`): one fix run per reviewed CHANGES tip, once per reviewed tip, at most `SG_FIX_PASS_MAX` a pass, and the ADR-0134 stop when the findings do not change. `bash scripts/test-branchreview-pass.sh` covers the review pass, `bash scripts/test-branchland.sh` the landing pass, and `bash scripts/test-revformat-stall.sh` the findings fingerprint and the count the fix pass asks `revformat.py`.
- `wtclean.sh` also cleans the worktrees of a ZOMBIE run (no verdict, `record.jsonl` idle >20 min, no process whose cwd is inside the run folder), under the same clean-tree / HEAD-on-a-remote-branch rules. `bash scripts/test-wtclean.sh` covers zombie, fresh and busy runs.
- janitor rule F (report-only): an open issue named `(#N)` in a fork commit subject older than 30 min, with no open work branch naming it, prints `FIXED-NOT-CLOSED #N` once. It never closes (closing needs evidence: comment it, then close). `bash scripts/test-janitor-f.sh` covers it.
- janitor rule I: a live run whose accepted issues are ALL closed is stopped when every one was closed as `not_planned` or is titled `[duplicate…` (`RUN-ON-CLOSED-ISSUES`, process tree and last call killed); it is also stopped when every one closed after the run started and before the run's last progress and the run holds no landing for it (no land in its record or the issue ledger, no branch of its children naming the issue), and the killed verdict names the reason (`closed_without_landing`); closed any other way it is only reported once (a land or fix run may be finishing). `bash scripts/test-janitor-i.sh` covers it. `autobug.sh` skips open bug issues titled `[duplicate…`.
- janitor rule J: a run with no `record.jsonl` 5 min after its request (the squad refused to start, e.g. `vendor/skills is not clean`) has its dashboard killed, is dropped from active-runs (`NEVER-STARTED`) and has the dispatch markers of every issue in its request (`auto/bug-<n>` from `autobug.sh`, `auto/disp-<n>` from `autofrontier.sh`) removed, so the issue is dispatched again instead of being stranded behind a marker whose run never existed: it queues one comment (`run X never started, dispatching again (attempt n of 2)`) quoting the last lines of the run's `launch.log` (written by `launch.py`; `state/<req>.log` is the same stream). The set of issues comes from the request's `GitHub issue #N` / `GitHub issue(s) N, M` headers (the same parse `autobug.sh`/`autofrontier.sh` do). After 2 never-started runs for the same issue (counter `auto/neverstart-<n>`) the marker is KEPT, a comment carries the evidence and `HEALTH never-started-retry` goes to `alerts.log`: no third retry, a person must look. `vendor-guard.sh` (watch.sh, under `single.sh`) re-vendors drifted personal skills into the fork and commits them, because a drifted vendor makes every `squad start` refuse. `bash scripts/test-janitor-neverstarted.sh` covers it; `bash scripts/test-launch.sh` covers `launch.log`.
- janitor rule K: a `disp-<n>` marker whose request log says `Nothing ran` (10 min old, no live run on the issue) is dropped like rule E does for `verdict failed`.
- janitor rule Q: a card still `Doing` behind its `auto/disp-<n>` / `auto/bug-<n>` marker (10 min old) that no run works -- no live run, no run in its launch window, no run that landed, no open work branch naming the issue, no `fork-<n>-<sha9>` marker -- goes back to `Todo` (the bug card to `To triage`), its marker is dropped and one comment is queued (`STALE-CARD #N`). Rules E and O read a run's verdict, so a run that ended without one (rule C drops a zombie from `active-runs.txt`) freed nothing and `health.sh` reported `runs-board-drift` for hours; rule Q reads the state, not the run's history. A refused read touches nothing; a definite "not ours" is remembered for 10 min (`janitor-q-seen-<n>`). `health.sh` names the cards that still hold a marker in the `runs-board-drift` cause. `bash scripts/test-janitor-stale-card.sh` and `bash scripts/test-health.sh` cover it.
- janitor rule O: a run whose record ends with a verdict and holds no `land` pass landed nothing, so the issue must not stay behind its dispatch marker: every issue in its request has `auto/disp-<n>` / `auto/bug-<n>` dropped, so `autofrontier.sh`/`autobug.sh` dispatch it again in the next cycle. The loop-board card is rule R's (one owner, ADR-0029): R derives it from the run's record -- `Todo` after a failed run, `Blocked` after `needs-human` (the owner is the next actor), `Code Complete` after a pass with nothing landed -- and O names that column in its comment, writing none of its own; the bug card goes back to `To triage` on `$SG_BUG_PROJECT`. `failed` is not the only way a run ends without landing (#1847): a run that ended `needs-human` or `pass` with nothing landed left its `auto/disp-<n>` behind, and `autofrontier.sh` skips every issue holding that marker, so the frontier starved on a run that is over -- the cause `health.sh` reports as `frontier-idle`. The bug card is only moved when it still sits in the column the dispatch set (`$SG_BUG_DOING`), so a card another step moved past it is never downgraded; the marker is dropped either way, because nothing must hold a finished run's issue back. A run that landed work, an issue a live run works, and an issue an open work branch names keep their marker. Once per run (marker `janitor-demoted-<run>`, so a re-dispatch is announced, never silent); the queued comment carries the run id, the verdict and its reasons. The board is written with `board.sh` (REST, no GraphQL quota). `bash scripts/test-janitor-disp-expiry.sh` covers the four states (ended-nothing, landed, live run, open work branch) and `bash scripts/test-janitor-failed-dispatch.sh` the card demote.
- A child session never merges anything: the child request says the loop lands, and the `ghcall.sh` door refuses a merge where `SG_CHILD_SESSION` is set (`CHILD-MERGE-REFUSED`, the call environment's own marker). `tests/test_gh_door_child_merge.py` covers it.
- `wtclean.sh` rule L: loose detached worktrees in `~/wt-*` or `*/acpr.*` idle 10 min, clean and on a remote branch are removed (`WT CLEAN loose`); dirty or unpushed ones are reported once (`WT LOOSE`). `~/wt-e2e*` and `$SG_E2E_DIR`/`$SG_E2E_BUILD_DIR` are never touched (the fork suite reuses them).
- `health.sh` (rule N): writes `$SG_STATE/health.json` (`ok`, `causes`: watchd-down, fastloop-down, watch-stalled, never-started-runs, orphan-markers, loose-worktrees) and alerts once per cause (`auto/health-<cause>`, `alerts.log`, `HEALTH_NOTIFY`). `health-install.sh` installs a launchd agent (every 120 s, RunAtLoad); `health-install.sh uninstall` removes it. Two of those causes are about work nobody is on, the failure mode where the loop looks healthy while doing nothing: `fork-parked` (a fork branch ahead of `SG_FORK_BASE` with no process in its worktree) and `frontier-idle` (an open `ready-for-agent` issue with no open work branch while no run has started in 30 min).
- `watch.sh` appends any step that took 5 s or more to `$SG_STATE/cycle.log` (`HH:MM:SS <step> <secs>s`): read it to see where the cycle goes.
- `branchreview.sh` keeps the verdict with the sha it read, and `branchland.sh` lands only the tip that review approved; `review.sh` kills the reviewer when the branch head moves during the review and publishes `VERDICT: BLOCKED (... head moved during the review)`. Two reviews of one branch never run at once: `review.sh` holds `review-KEY.lock` (waits up to `SG_REVIEW_LOCK_WAIT`, 3600 s), the second reuses a verdict the first wrote for the same head, each try writes private files, and only a final verdict reaches `review-KEY.md`, by a rename.
Fix runs are launched detached (`launch.py`); dispatches, panel rulings and owner questions are queued as issue comments (cqflush). Five switches, and they are not one switch: `touch $SG_STATE/auto/off` stops only the dispatch passes (autobug, autofrontier, autohunt) and the branch fix rounds (branchfixpass, #1789); `SG_LAND_PAUSED=1` in the environment skips the three branch passes; `touch $SG_STATE/auto/nojanitor` turns the janitor off; `scripts/watchd.sh stop` ends the supervisor, the 90 s cycle and the fast lane; and an open issue labelled `loop-bug`, bare or prefixed, drains the loop by itself: live runs and gates finish, the loop ends, and it starts again when no loop-bug is open (ADR-0146; marks `auto/loop-bug-drain` and, for an owner stop, `auto/owner-stop`). The owner saying "stop the loop" means `watchd.sh stop`; `auto/off` alone leaves the live runs spending and the cycle printing. On a landing you still owe: e2e on the landed SHA, worktree cleanup, closing the issue. Never launch a manual fix for a tip the fix pass already dispatched (`$SG_STATE/auto/branchfix-<key>-<sha9>`).

A work branch gets at most **`SG_BRANCHFIX_STALLS` (2) fix rounds that do not change the findings** (ADR-0134), three `CHANGES` verdicts on one issue go to `needs-human` (ADR-0151 rule 1), and a requeue is bounded by `SG_REQUEUE_CAP` and `SG_LOOP_REQUEUE_CAP`, 6 each (`wave.sh`). At the fix-round limit the fix pass stops dispatching for that unit. Then:
1. Post the round history (each verdict's findings, one line each) on the issue.
2. Convene the `autonomy` panel on one question: split the issue, narrow the acceptance criterion, accept the branch with follow-up issues for the open findings, or abandon.
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
- branch pushed;
- review findings and verdict;
- CI failures and recovery;
- stalled-run detection and recovery;
- model failure and model replacement;
- continuation/fix runs;
- landing and the landed SHA;
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

Use the fewest runs that safely express the current frontier.

Whenever multiple tasks are startable at the same time, place them in ONE agent-squad panel, with one child and one work branch per issue/work slice.

Before creating any new panel:

1. recompute the complete frontier;
2. inspect available capacity;
3. include every compatible startable task that fits;
4. create a separate panel only when branch/base timing, recovery work, or a genuine execution dependency requires it.

Do not launch N independent panels when one panel with N children can perform the same work.

A run cannot take children after it starts, so launch exactly one new run holding all currently startable work.

Never delay startable work merely to create a larger batch.

### 8. Land ready branches and clean worktrees continuously

A ready work branch left pushed is idle capacity: dependants keep building on a speculative branch, rebases pile up, and conflicts grow. Land every branch the moment it is ready: the review `APPROVE` on its tip and every check green. Never hold a ready branch to batch landings, to wait for the e2e of an earlier landing, or until the end of the goal. At every status check, sweep the pushed work branches of this goal for any that became ready without an event, and land them (`branchland.sh` does this each watch cycle).

After each landing:

1. queue the landed SHA on the issue;
2. start e2e detached on that exact SHA;
3. tell the branches that depend on it to rebase onto `origin/$SG_BASE`, through a fix run;
4. when the last row of `$SG_STATE/landed.tsv` is older than 900 s, promote: run `promote-pass.sh` from the checkout with `SG_CHECKOUT` set, which runs the full suite in both modes in `$SG_STATE/promote-tree` and then hands the base to `promote-stg.sh` — one `dev`→`stg` pull request, opened and merged by the script. No script pushes `stg` itself, and `promote-stg.sh` refuses when the proven sha is not the `dev` tip: read the one reason in `$SG_STATE/promote.log` when nothing moved. The loop promotes `stg` into `main` too: every cycle `promote-main-pass.sh` asks for the complete CI tier on the quiet `stg` tip (the `promote` label on the `stg`→`main` pull request), reads the answer, and fast-forwards `main` to the green sha through `promote-main.sh <sha>`. A red files one `bug` and holds `main` until a new `stg` tip; an unproven tier is an alert (`PROMOTE MAIN` in `alerts.log`), never a bug (ADR-0173);
5. after a promotion, publish the live copy from a tree on `stg` only: fast-forward `$SG_STATE/stg-tree` to `origin/stg`, then `publish-live.sh <stg-tree>/contrib/squad-goal`. Restart the drivers with `watchd.sh restart`, which drains the live runs first (ADR-0162), and `hubctl.sh --restart`. A fix in `dev` is not live until this step, and `live-guard.sh` publishes nothing from a tree that is not on `stg` (ADR-0140).
6. after each publish, run the installation check on every fix the publish carried, and write the result on the fix's issue:
   - compare the start time of each long-lived process (`ps -o lstart=` of the hub and of the drivers) with the publish time. A process that started before the publish runs the old code: restart it;
   - give each fix one result and name the fact you read. `works`: a log line, a record or a count after the publish shows the new behaviour. `no effect`: the fix is installed and its fact is absent. `broke`: the fix caused a red. `not measured`: its case did not occur yet;
   - read the throughput measures against the reading before the publish: landings per hour, runs by verdict, spend, and the share of run time in gate and suite calls;
   - file a bug at once for `no effect` and for `broke`. Read a `not measured` fix again at the next status check (§10);
   - rank the fixes that are not live by the share of run time or of rework each one attacks, and land and publish the largest first. A fix of one failure does not move the throughput; a fix of the largest share does.

A bug you find yourself is filed at once with the `bug` label and its card in Todo (ADR-0148), before any other step, and the loop dispatches bugs ahead of features. A bug you fix by hand moves straight to Doing (else `autobug.sh` dispatches it a second time), takes the fast gate rather than the full one, lands in `dev`, and is promoted and published before the next feature dispatch. A `loop-bug` is not dispatched at all: §9.

Clean worktrees as soon as their run has ended. Remove every worktree of a run with a verdict (`git worktree remove --force <abs path>`, then `git worktree prune`), and every worktree whose branch has landed. At each status check, sweep `git worktree list` for leftovers from finished runs. Never remove a worktree whose run is still alive, since that ends the run as `land_workspace_missing`. Stale worktrees cost disk, confuse later runs, and hide which work is live.

### 9. A loop defect is fixed outside the loop

A defect in the loop's own machinery — dispatch, childcc, wave, landing, the review chain, the janitor, the hub, the live copy — is never dispatched to the loop (ADR-0146). The steps, in order:

1. **File it.** One issue with the evidence it rests on: the run id, the log lines, the markers, the timestamps. Label `loop-bug` and the one label that names the defect: `loop-bug:dispatch` for the dispatch path, `loop-bug:landing` for the landing path (the only label that stops landings), `loop-bug:infra` for the loop's infrastructure — the wave lifecycle, a gate, the engine. Any of them, and a plain `loop-bug`, drains the loop at the next pass. Card to Doing. File it when the evidence names a defect, not when a pass is merely slow.
2. **Adversarial review, before the first edit.** Spawn a fresh Fable session (Agent tool, model `fable`, stance challenger) with the issue, the evidence, the premise the fix rests on and the change you propose. It must name what else explains the evidence and which test separates the two. Record the ruling on the issue. A fix the review refutes is not started; the refutation, with its evidence, becomes the issue's closing record.
3. **The loop stops itself.** The label drains it: `watchd.sh status` reads `off until the loop-bug #N closes` once the live work ended. Never run `watchd.sh stop` or `drain` for it: that is the owner's stop, and the loop then stays off after the fix (`stop --force` is the hard stop and snapshots the live work first, #1662). A run that already finished and whose children entered its wave may be landed first through the loop's own door, `bash scripts/single.sh wland bash scripts/wave.sh land <run>`, never by calling `wave.sh land` directly: a hand call overlaps a live holder and proves nothing about the loop's own path (#1290). So the stop does not strand approved work.
4. **Fix it test-first, in a worktree.** The RED commit first, then the fix (ADR-0136). The fast gate, not the full one, started as `SG_GATE_WORKERS=<n> SG_GATE_REASON="<why>" bash scripts/hand-gate.sh`: the count and the reason are declared where the gate starts, and the gate refuses without them. A delta of two files takes two workers at most. Land in `dev`; the commit carries `Closes #n`.
5. **Promote and publish** (§8 steps 4 and 5). The publish closes the issue; never close it by hand.
6. **The loop restarts itself.** The health agent starts it within five minutes of the last loop-bug closing, and the audit names the issue. Confirm that the first cycle prints no `LOOP STOP` and no `DISPATCH HOLD` for that issue. A loop the owner stopped stays off; start it with `scripts/watchd.sh start`. The start itself refuses while any open issue carries `loop-bug` or a label that begins with `loop-bug:`, and names the issue: the publish of step 5 is what closes it, so a refusal here means the fix is not live yet.

Guard: `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (intake stops on the label and on nothing else), `test-watchd-drain.sh` and `test-health.sh` (the label drains the loop and the close restarts it), `test-childcc.sh` (a child does not enter during a stop), `test-publish-live.sh` (a fix is live only after the publish, and the publish closes the issue), `test-watchd-start.sh` (a start is refused and names the issue while any loop-bug is open), `tests/test_autonomy_fragment.py` (the owner-only list the carve-outs read). The Fable adversarial, the stop order and the trap rule are a draft: no script checks them yet.

**A trap is not a slow pass.** A trap is a line the loop prints unchanged for three cycles while no fact moves: the same `WATCH: <run> waits`, `BRANCHLAND WAIT`, `DISPATCH HOLD` or `LOOP STOP` naming the same subject, a `CCDEFER` or `dwait` counter that only grows, or a run whose `record.jsonl` mtime never moves. On a trap: file it (step 1), stop the loop (step 3), unwind what holds it — the marker in `auto/`, the lock in `$SG_STATE/locks`, the lease in `~/.cache/agent-squad/caps` — and requeue the issue with `wave.sh requeue <issue> <why>`. Name every marker you delete on the issue; never delete one silently, and never delete a counter another script measures.

### 10. The status check, every 20 minutes

The loop runs on events; the check runs on a timer. Read these in order and act on the first that fails. A check with no numbers is not a check.

1. **Parallelism.** Live runs against the ceiling: `python3 -m squad.caps`, whose one JSON line names the ceiling and its occupancy (its `occupancy.runs` is what is live now, its `runs` is the cap). The cap comes from the config the project resolves, since the project file overlays the plugin default. A finished run still listed in `active-runs.txt` is waiting for its wave, not working: count it, read its wave age, and past `SG_WAVE_MAX_AGE_SECONDS` (7200) it lands on a later pass. A run's accepted children never count as run slots.
2. **Delivery speed.** Rows added to `$SG_STATE/landed.tsv` in the last hour, and the age of the oldest `WATCH: <run> waits` or `BRANCHLAND WAIT` line in `watch.log`. Zero landings in an hour with live runs is a stall, whatever the log says about progress.
3. **Runs against cards in Doing.** Every live run has a Doing card, and every startable Doing card has one live run or one open work branch. `janitor.py` reports the drift as `runs-board-drift` and two runs on one issue as `DUP`. A card with neither goes back to Todo; a run with no card is an orphan.
4. **Tokens.** `python3 scripts/tokens.py` over the last hour against the cap's hourly share (`token-guard.sh`). A burn over the cap is a report and an alert, never a stop; dispatch pauses only while the last hour burns over 3x the cap and resumes by itself under it.
5. **Stalls and traps.** `STALL RUN`, a `CYCLE <n>s` over 300 s in `cycle.log`, a `gh-cooldown.json` not yet expired, and any line that repeats unchanged for three checks (§9).
6. **Worktrees.** `git worktree list | wc -l`, then `scripts/wtclean.sh`; it salvages the unpushed work of an ended run into `refs/salvage/wt/<tree path>` and removes the tree, so act on every `WT SALVAGED` line by landing that work, and on every `WT KEPT unpushed` line by hand. Remove only what the check proves is unowned: another session's tree is not yours to delete.
7. **The board.** The Doing column matches the live runs and open work branches. While `watchd` is down no pass writes the board, so its moves are yours (see "Issue updates").
8. **The nightly.** `nightly.sh` runs the full suite in both modes in its own tree and lock. A red record holds delivery and names the `dev` tip; a proof in flight or `SG_NIGHTLY_OFF` holds it and spends neither the day's marker. Clear a red record by fixing the tip, never by deleting the marker.

Post one status comment when a number changed, and nothing when none did.

Guard: `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (a live run's accepted children never fill the run slots; #948 is its case), `tests/test_caps.py` and `tests/test_wtclean.py` where they exist, `test-janitor-*.sh` for the drift causes. Items 2, 4 and 8 are a draft: their numbers are read by hand.

## Profiles

Per-repo values live in `scripts/profiles/<name>.sh` (repo, owner, board project, checkout, `SG_BASE`, `SG_MERGE_METHOD`, `SG_AUTO_MERGE`, e2e dir/command/pass regex, review and coding rules). `scripts/env.sh` picks the profile from `SG_PROFILE` or from the origin remote, and resolves the agent-squad install from `installed_plugins.json`. Every `$SG_*` name in this document comes from there; run `. scripts/env.sh` first. To support a new repo, copy a profile.

**Root name rule.** `scripts/launch.py` enforces it: before launching it renames `"root"` and the root node's `"name"` together when they differ from `$SG_ROOT_NAME`, validates the graph, and refuses to launch on a validation error (renaming only `"root"` gives `root must name a loop` + `orphan: no parent`). Commit the rename. The rule: The root node of the workflow graph (`"root"` and its node `"name"` in `docs/squad/workflows/<id>.graph.json`) is `$SG_ROOT_NAME`, the project name in snake_case (schema `^[a-z][a-z0-9_]*$`, so no hyphens), never `main`, because the squad dashboard titles its top card with that name. Before every launch, check the chosen workflow: if its root is not `$SG_ROOT_NAME`, rename both fields, validate with `squad.graph.validate` from the installed squad, and note it on the issues. A new profile must set `SG_ROOT_NAME`. Where a repo's own rules (CLAUDE.md) conflict with this document, the repo wins; encode the difference in its profile.

## Start

0. **Model check.** Do not run this loop on a Haiku session model: it hesitates to dispatch under `/autonomy` and loops on confirmations. Ask the owner to switch (`/model opus`) first.
1. Load the `autonomy` skill (Skill tool). Owner questions go to the panel. Carve-outs stay with the owner: money, credentials, access, permanent deletion, external publication.
   **Owner rule (2026-10-09): ask every pending owner decision up front, in one AskUserQuestion, before the first dispatch** (ADR picks, merges that need the owner, carve-outs, where to patch). Then run to the end without asking again.
2. Read every issue body and its "Blocked by" list. Build the ticket graph.
3. Check `git worktree list`, active runs, and open work branches. Another session may already be advancing the same tickets. Reconcile durable state before dispatching duplicates.
4. Start the background machinery once per session, with the Monitor tool where it fits:
   - `nohup ~/.claude/skills/squad-goal/scripts/cqflush.sh > $SG_STATE/cqflush.log 2>&1 &`
     Then monitor `tail -n0 -F` on that log.
   - `scripts/watchd.sh start` once (idempotent), then Monitor `scripts/watchtail.sh`, which reads `watch.log`; the Monitor needs no re-arming. Never run `watch.sh` under a Monitor while `watchd` is up: the passes and both dispatchers would run twice. It emits:
     - `RUN <id> verdict=…`
     - `STALL RUN <id> …` (record.jsonl idle past `stall_timeout_minutes`)
     - `HUNG TEST pid=… cpu=…` (`scripts/heartbeat.sh`, called every cycle: a leaf test host — `testhost` or `dotnet exec --runtimeconfig … UI*Tests` — whose CPU time has not moved for `HB_FLAT_MIN`, default 5 min, was killed so the agent's `dotnet test` fails instead of hanging; it judges the executable name only, never a shell whose command line mentions testhost)
     - `BOARD MOVE #n <from> -> <to> (…)` / `BOARD ADD #n …` (`scripts/reconcile.py`, every ~6 min, `RECONCILE=apply`; dry run without it): issue closed -> Closed; an open issue whose work branch holds commits `$SG_LAND_BRANCH` lacks -> Code Complete (never downgrades); an open `bug` issue missing from the board -> added (Todo, or Code Complete when a work branch names it). Blocking: an open issue whose `## Blocked by` lists an open `#M` goes Todo -> Blocked, and Blocked -> Todo when all are closed; a section with only free text prints `BOARD NOTE` (write `#N` in the issue body). Facts only, scope = issues >= `RECONCILE_MIN` (default 1102). The coordinator still moves Doing/Blocked by hand
     - `DEAD RUN: n run(s) without a verdict and no live squad process; k marked killed in their record` (killed by a session restart or crash: `scripts/heartbeat.sh` wrote the `failed` verdict, reason `killed/no-process`, into each run's record, so the hub shows the run as ended and the footer stops counting it; relaunch. The janitor does the same for a zombie run with other runs alive)
   - `MODEL_FAIL`/`MODEL_SWITCH` for review arrive as queued issue comments (cqflush log). `STALL E2E` and `QUOTA_GUARD` are yours to raise: run e2e with `run_in_background` and a timeout, and check quota at each status check.
5. Check the latest reliable Anthropic weekly quota state before the initial dispatch. If remaining quota is 10% or less, exclude Anthropic models from new autonomous squad work.
6. Recompute the complete initial frontier and dispatch it immediately.

## Launching, and staying awake in `/goal` mode

- **A blocker is a dispatch edge, never a stack.** Work is built on the landing branch. `scripts/stack.sh <issue>` reads the issue's native blocked-by relation and answers `READY` (every blocker is closed) or `WAIT <reason>` (an open blocker whose work is not in `$SG_BASE` yet, a blocker whose state is not readable, or a blocker sitting in Code Complete whose branch has not landed). Optimistic branch stacking returns with the wave rules of ADR-0145 (#372). The `## Blocked by` section of the body is a person-facing mirror: a stale edge is named once (`STACK NOTE`) and never gates the decision. The frontier dispatches a READY issue and leaves a WAIT one to a later pass.
- **BASE CI: a red check on the base head holds the landings.** `basecheck.sh` reads the check runs of the base head before each landing, right after each landing push, and once per watch cycle (`basecheckpass.sh`, #790). A red completed check is reported once per head: one `alerts.log` line, and a queued comment on each issue whose `landed.tsv` row names that head (`branchland.sh` and `wave.sh` both write such a row, so a red head reaches the issues that landed at it). `branchland.sh` and `wave.sh` wait until the head is green. A red answer is read again after `SG_BASE_CI_RECHECK` seconds (default 600); a green one is kept once its newest check is `SG_BASE_CI_GRACE` seconds old (default 900), and is read again after `SG_BASE_CI_OK_TTL` seconds (default 600). An unreadable read is one `alerts.log` line per head and does not block. Pending or missing checks do not block. `SG_BASE_CI_GATE=0` turns it off (#786).
- **Every request carries a DONE CHECK** (`mkreq.sh` appends it): the child ends with `ac_commands`, the checks it ran as evidence, so the final `ac-verify` gate verifies instead of failing with `no_ac_commands`. The gate runs in the main checkout, so work on a work branch is wrapped as `scripts/acpr.sh <branch> -- <command>` (throwaway worktree at `origin/<branch>`); fork-only work as `cd <fork worktree> && <command>`. Ask for the **narrowest** command that proves the done condition — the test file or suite the change touches, not the project's whole run: `workcmd.py` runs the entries one at a time and caps each one (`TIMEOUT_S`, default 300 s, moved by `SG_AC_TIMEOUT`), so `shell-suites.sh` (159 s, #1785) is never an `ac_command` — run it yourself and declare the named suite. The hub shows a run that landed with no command to verify as `unverified` (its own color), never as a plain `landed`.
- **Worktree cleanup is automatic.** `wtclean.sh` (every watch loop) removes a child worktree once its run has a verdict, the tree is clean and HEAD is on a remote branch; a finished worktree with unpushed commits is kept and reported as `WT UNPUSHED` with its salvage push (run it, then the tree is cleaned). The loop itself never pushes it (#361, #771): it only flags it. Janitor rule O keeps the tree as `auto/salvage-<n>`, and the next dispatch of the issue names the branch and sha in a `SALVAGE:` line, so the new run can reuse the work. Rule Q drops a dispatch marker that stands behind a card already in Todo with no run and no work branch, so a failed run that left active-runs unreleased does not keep its issue claimed. A work branch must be named `fix/<n>-…` or `gui/<n>-…`: the branch passes and the frontier read no other name, so a child branch pushed under its own name is invisible and the frontier dispatches its issue again. The same pass also drops `acpr.sh` acceptance trees older than 2 h (a killed gate), fork-only worktrees (`~/wt-as-<n>`, `~/wt-fr-<n>`) once the issue is closed and the branch merged into the fork base (branch deleted with `git branch -d`), and `~/wt-*` worktrees once their branch is an ancestor of `origin/$SG_LAND_BRANCH`.
- **No dashboard tabs.** Only the Squad hub (http://127.0.0.1:7700/<profile>) is used. `launch.py` sets `SQUAD_HEADLESS=1`, so the run's dashboard server starts for the hub but no tab opens. Never run `/agent-squad:dashboard` or `squad dashboard` without `--no-open`.

- **Launch only through `scripts/launch.py`, from `$SG_CHECKOUT`.** `/agent-squad:work` inside this loop means `launch.py`, never `squad start` in the Bash tool (it dies with the session, as run `2e34b4` did on 2026-10-09). `launch.py` refuses another cwd unless `SG_ALLOW_OTHER_CWD=1`, fails at once with the log tail when `squad` exits before creating the run folder, and writes the run's absolute folder to `active-runs.txt` so `watch.sh`/`heartbeat.sh` track it from anywhere. A request that is a child of one issue (a `CHILD - GitHub issue` block, or a fork-only request) starts its run inside that issue's worktree (`$SG_CHECKOUT/worktrees/child/<branch>`, made here when it is missing, on the branch `sg_fork_branch` names), with the run folder inside it: the target is then a linked worktree the run owns, so the engine commits a passing node's work there and the branch tip is the run's final state (ADR-0106 rule 4). Every other request keeps `$SG_CHECKOUT` as its target and says why on stderr: one with no issue, one naming several, a fix run on a work branch whose child commits there, and a profile whose fork checkout is another checkout, whose child commits in the fork.
- **Never end a turn in `/goal` mode without a live background task.** After every dispatch keep a Monitor on `scripts/watchtail.sh` (persistent, max timeout; re-arm on expiry). With background work alive the Stop hook sends one "background work still running" check-in instead of re-evaluating the goal every turn; `ScheduleWakeup` does not count as background work. Without it the hook blocks each turn end and the session floods the owner with identical wait replies (2026-10-09: 66 hook blocks, 5 forced overrides).
- **When the Stop hook blocks and nothing changed:** answer with one short line at most once, then nothing. Never repeat a status line. Act only on a real event (`RUN … verdict`, `BRANCHREVIEW …`, `BRANCHLAND …`, `STALL`, `DEAD RUN`).

## Dispatch: the frontier, in one panel

**Frontier:** every ticket or safely separable work slice that can make useful progress now.

A dependency does not need to be fully merged or validated when useful work can safely proceed.

Cases that count as startable include:

- the blocker is only a doc or ADR already present on a pushed work branch: point the child at that branch and tell it to rebase after the branch lands;
- the blocker implementation is stable enough on an open branch for dependent work to proceed speculatively;
- the work has a host-free half: pure functions over primitive inputs, in new files, with their own tests. Split it off and launch it now;
- unrelated portions of an issue can proceed without touching files owned by another child.

**One run per frontier:** independent/startable issues go in ONE request to `launch.py` that lists every issue, one child and one work branch each.

A separate run is justified only for:

- a fix or continuation on an existing work branch;
- work that genuinely must start at a different moment or base;
- a recovery that cannot safely join the current panel.

**Parallelism:** the machine ceiling and a run's envelope are two different numbers, and both are read from disk, never remembered. `caps.runs` in the config the project resolves (the plugin default in `docs/squad/defaults.yaml`, overlaid by the project file) bounds the whole machine. `docs/design/router.graph.json` holds a run's own envelope: `run.max_parallel` 2, the dispatch node's `max_children` 4, `spend_cap` 40. Give each child a disjoint slice. A file that two children would both append to belongs to exactly one of them.

Before every launch, recompute the complete frontier and fill as much available capacity as possible.

**Every coding request must contain these lines:**

- "base on origin/$SG_BASE", unless explicitly starting from a dependency or fix branch;
- "push nothing: the loop enters your commits into the run's wave branch and lands the wave" (`launch.py` rewrites a line that says to push your branch);
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
- "no new branch; push the same one".

For speculative work based on an unmerged dependency branch, explicitly name the base branch and require rebase onto `origin/$SG_BASE` once the dependency merges. The loop lands by rebase and fast-forward (`ffland.sh`), so no squash ever happens and a plain `git rebase origin/$SG_BASE` replays nothing twice. The rebase request must say: `git rebase --onto origin/$SG_BASE <old-dependency-tip> <child-branch>`, where `<old-dependency-tip>` is the dependency head the child last built on (`git merge-base <child-branch> origin/<dependency-branch>` before the dependency branch is deleted). Record that SHA in the dispatch comment.

**Launch:** write the request to `$SG_STATE/req/<name>.txt`, then run:

`python3 ~/.claude/skills/squad-goal/scripts/launch.py <file>`

from the checkout. It launches detached, records the run in `active-runs.txt`, starts its dashboard server for the hub without opening a tab, and prints the run id.

On dispatch:

1. queue a comment with the run id and relevant dependency/base information on every affected issue;
2. move each card to Doing;
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

`$SG_STATE/cq/<owner>__<repo>#<issue>-<tag>.md` (`$SG_STATE` is `$SG_STATE/<profile>`; `sg_cq_file <issue>-<tag>.md` from `address.sh` prints the path for the repository in force)

`cqflush` posts it with retries, to the repository the name carries (a name no profile knows is retired as `.dead`, never posted).

A write the loop cannot prove is a defect, not a silence (#117). When a write to the tracker is refused, the pass that hit the refusal keeps its log line and queues ONE comment on the issue that write was about: `scripts/refusal.sh <issue> <REFUSAL-NAME> "<GitHub's error>" [<what the pass was doing>]` writes it into this same queue, so the issue says why it did not move whatever happens to that pass. The refusal names are a closed set in code, readable in one place: `scripts/refusal-names.sh` holds them (`sg_refusal_names`, one name per line) and the one predicate over it (`sg_refusal_named <name>`), and every helper that names a refusal -- this one, `autorefuse.sh`, and a pass's write door -- refuses a name the set does not hold, naming it on stderr, instead of queueing, filing or logging it under a guessed name (#142). Test: `scripts/test-refusal-names.sh`. The comment carries the refusal's name and GitHub's own error, quoted, and its first line names the refusal and the issue, so the issue is greppable beside the cycle log; the same refusal on the same issue is one entry, never one per cycle. The bug issue such a refusal files is the task, and this comment is the place.

Move the board card on the same event:

```sh
scripts/board.sh <owner> <project> "<Status>" <n…>
```

The columns are the shipped set (`SG_BOARD_VOCAB=shipped`): Backlog, Todo, Doing, Code Review, Code Complete, Gating, Closed, Abandoned. Gating holds a card whose gate or proof waits for a slot or runs for the wave; a red or unproven attempt is a marker, never a move, and the card's Gate Tries number counts the attempts. `Done` is not one of them, and `Blocked` is not a column either: a freeze is a badge over the column the work reached (ADR-0143 rule 4).

- `childcc.py` moves Doing → Code Review when a child passes.
- `janitor.py` rule R is the one producer of the derived columns, from the run's record: Todo after a failed run, Blocked after `needs-human`, Code Complete after a pass with nothing landed.
- Code Complete means the child's work is in its run's wave branch. Closed means the wave is in dev (ADR-0145 rule 12). The push, the approval and the dev CI are facts the run reports, never columns.
- A card only moves forward (ADR-0143).

While `watchd` is down nothing writes the board: you do, with the same `board.sh`, in the same vocabulary. A `loop-bug` card moves like any other: Doing when the fix starts, Code Complete when its commit enters the wave, Closed when it is in `dev` (ADR-0145 rule 12). Its ISSUE stays open until the publish closes it (ADR-0149); the card and the issue are two clocks, not one.

The comment queue is a durability mechanism, not permission to postpone creating the update. Queue the update at the moment the event happens.

Autonomous frontier: `scripts/autofrontier.sh` (fast lane, after `autobug.sh`) first runs the stops of `ghcall.sh` (`gh_loop_stop`, `gh_dispatch_hold`, `gh_disk_stop` under `SG_MIN_FREE_GB` GiB free, 20 by default, and `gh_bug_first` while `bugs-waiting` names a startable bug with no run); a stop prints one line and dispatches nothing. It then dispatches every open `$SG_FRONTIER_LABEL` issue that `stack.sh` calls READY, has no open work branch naming it, no `auto/disp-<n>` marker and no live run working it, dealt over the free run slots as one run per slot (one squad start each), each run taking at most its envelope (the dispatch node's `max_children`, and `run.max_children` less the children live runs hold), the issues past the slots waiting for the next pass, with the profile's `SG_FRONTIER_PREAMBLE` as shared rules. `stack.sh` waits on every open blocker, and on a blocker in Code Complete whose branch has not landed.

GitHub access is REST only in the loop (`board.py` for cards, REST diff in review, REST in reconcile): the GraphQL quota is shared with every `gh` subcommand and ran out.

Board moves go through `scripts/board.sh` (`$BOARD`), which flags the hub to refresh the board within 2 s instead of its full-read cycle (10 min; the open cards refresh every 15 s); `childcc.py` runs every beat of the fast lane.

A child that finishes with outcome pass moves its issue to Code Complete at once (`scripts/childcc.py`, every watch loop, once per child), with a comment carrying its first reason; the panel may still be running its siblings. A run that already has its verdict (tiny and fork-only runs pass → verdict in under 1 s, never seen live) is backfilled too, but only when the card is Todo/In Progress/Blocked, never past Code Complete. `scripts/test-childcc.sh` is the regression test (stub gh and board).

Watch lanes: `watchd.sh` runs two loops. `watch.sh` is the 90 s cycle; `fastloop.sh` is the fast lane (childcc and both dispatch passes — `autobug.sh`, then `autofrontier.sh` — every `SG_FAST_INTERVAL` s, 120 by default), so a slow cycle step never delays a landing (#1239: APPROVE + clean waited ~5 min behind a 339 s forkclose) and a dispatchable issue is picked up within one beat instead of on the cycle's beat, up to ~6 min late, which is what `frontier-idle` kept reporting (#1845). A cycle step runs in the background under `single.sh <name> <cmd>` (non-blocking lock: a second call while the first is alive exits 76), and the fast lane's steps use it too, so no step overlaps its own previous run; the two dispatch passes are called bare because each takes its own `single.sh <stage>` lock inside, so a pass already running is skipped by whichever lane comes second (they go out detached: a pass ends in a `squad start` that runs for minutes and must not hold the beat). `reconcile.py`, the most expensive step of the cycle (~167 s each), is detached the same way under its own lock and files its stream in `$SG_STATE/reconcile.log`; `wtclean.sh` runs on every third cycle (the trees are almost always clean and ~17 s of every cycle is 10% of the loop — run `scripts/wtclean.sh` for an on-demand pass). Standalone `watch.sh` (no watchd) still runs childcc, autofrontier and autobug itself while `fastloop.pid` is not alive. `cycle.log` gets a `CYCLE <n>s` line per watch cycle: a long one says a step is starving the loop. Restart watchd after editing `watchd.sh`/`fastloop.sh` (the supervisor itself is not re-read). A new plugin install or publish reaches the lanes with `watchd.sh reload` (SIGHUP, #873): the lanes end their pass, re-read the install path, and start again; live runs keep running.

After editing any squad script, run `bash scripts/shell-suites.sh`: every `test-*.sh` beside it, offline, every failing suite named, exit 1 on any.

GUI suites never run at once: Mesen's SingleInstance (global mutex) makes a second emulator pass its args to the first and exit 0 ("application exited (0) before the hook came up"), which looks like a code failure. A profile's GUI test command (`SG_FORK_TEST_CMD`) runs through `scripts/serial.sh gui-suite <cmd>`, a per-name lock with stale-pid takeover (`scripts/test-serial.sh`); fix runs inherit it because the forkclose prompt quotes the command. After editing a profile, restart watchd (its env is frozen at start).

Janitor rule H compares board state with the issue's own history (its loop comments; the REST timeline has no project-status events), once per 10 min, report-only: `RED-LOOP #N` after 3+ fork-suite REDs with no green fork close (NEEDS OWNER: the loop is not converging), `STATE-REGRESSION #N` when a card is Todo/Blocked although the history reached Code Complete, or Done while the issue is open. Test: `scripts/test-janitor-h.sh`.

Coordinator test passes (post-merge e2e, a fork suite for fork-only work) go through `scripts/test.sh <issue> [-C <dir>] -- <command>`: it moves no card, logs to `$SG_STATE/tests/`, queues the result on the issue, and the hub lists it as `test #N` with the live log as its panel. A test run by hand outside it is invisible on the hub.

Automatic bug fixing: `scripts/autobug.sh` (fast lane, before `autofrontier.sh`; the same stops except `gh_bug_first`) dispatches every open `bug` issue with no open work branch naming it, no `auto/bug-<n>` marker and no skip label (`needs-owner`, `no-auto`, `wontfix`, `question`), then open `debt` issues the same way only when no bug is startable, all in one squad start within the free run slots: one child and one branch each, TDD with the RED output in the issue status comment, accepted ADRs binding, owner carve-outs returned as fail. It comments the dispatch on each issue and moves the cards (task board, and the bug board when the profile sets `SG_BUG_PROJECT`). `autofrontier.sh` skips `debt` like it skips bugs. The loop never retries: delete `auto/bug-<n>` to dispatch again. It writes the count of startable bugs it left with no run to `bugs-waiting`, which holds the frontier pass (bug first). `SG_AUTO_BUGFIX=no` turns it off.

Idle bug hunt: `scripts/autohunt.sh` (watch.sh, every cycle after autobug) starts one read-only hunt when the loop has been idle for `SG_HUNT_IDLE_MINUTES` (default 10): no live squad run, no gate/test holding the `gui-suite` lock or running `forkclose`, nothing startable for autobug or autofrontier, no work branch waiting on a review, `auto/off` absent. One hunt at a time (`hunt_going` while a hunt run is alive). `SG_HUNT_COOLDOWN_MINUTES` (default 60) and `SG_HUNT_PER_DAY` (default 6) count only when a hunt-filed bug (`Filed by autohunt` in the body) has a fix start (`auto/bug-<n>` written by autobug); a hunt that files nothing leaves no cooldown and no day count, so the next idle check may hunt again. `SG_AUTO_HUNT=no` turns it off. `--file` titles every issue it files `[SQUAD] <title>` (the prefix is added unless the title already starts with `[`), which is how a squad-tooling issue reads apart from a product one in the issue list. Logs `HUNT started`, `HUNT filed #N`, `HUNT none`. The child files only real, reproduced defects (label `bug` only, at most `SG_FILE_CEILING`, never a duplicate); architecture trade-offs go `/adr`. One defect storm, one bounded spend (#140, SPEC #117): `--file` files at most `SG_FILE_CEILING` new issues (default 3, declared once in the filer, and the hunt request asks a child for that same number) for ONE pass -- `SG_FILE_PASS` names the pass, else the hunt that is running -- so a storm of defects cannot starve a landing. The filing goes through the one door (`gh_send`), so its write is one line of `api.log` under the pass that spends it (ADR-0277). The pass is keyed by the CAUSE, never the occurrence: a fingerprint it already filed opens no second issue (`HUNT dup #N <fp>`), a fingerprint it cannot file for the ceiling is named with the fingerprint it would have filed (`HUNT hold <fp> <name>`), and a filing GitHub refused opens no issue, spends no line of the ceiling and is named (`HUNT refused <fp> <name>`). A caller that knows the cause names it with `--file <title> <body-file> --fingerprint <fp>`. A filing that names no pass is a pass of one, never held. Tests: `scripts/test-autohunt.sh`, `scripts/test-defect-ceiling.sh`.

Refused writes: `scripts/autorefuse.sh --refused <operation> <address> <error> [--exit <code>] [--context <run-or-card>]` files ONE bug for one refused-write CAUSE, never one per occurrence. The operation is a refusal NAME, and the names are the closed set of `scripts/refusal-names.sh`: a name the set does not hold is refused here, by name (`REFUSE not-named`), and nothing is filed under it -- an unknown name has no shape, so the filer guesses none (#142). The fingerprint is the operation, the address and the error's own first line; the run id, the timestamp and the cycle number are outside it on purpose. The index is `$SG_STATE/auto/refusal-<fingerprint>`, one line `<issue> <state>`: a fingerprint whose issue is open files nothing, one whose issue is closed files again (a defect that came back is a defect), and the file survives a restart, so a restart never re-files every open defect. The state is read live through `ghcall.sh`, and a state the loop cannot read files nothing (a refusal is not an answer, ADR-0277). The issue is opened by `autohunt.sh --file`, so it carries the `bug` label, the startable label, the `[SQUAD]` prefix and the `Filed by autorefuse` line. Logs `REFUSE filed|open|refile|unreadable|not-filed #<n> refusal-<fingerprint> <operation> <address>: <the error's first line>`. The pass that hits the refusal is what calls it (#145); no step of the cycle calls it yet. Test: `scripts/test-autorefuse.sh`.

Optimistic while testing: a blocker whose branch has landed counts as done for dispatch (`stack.sh` READY) even while its e2e runs or the issue waits to close; dispatch its dependents at the landing, not at the close. A red e2e goes back to the blocker as a fix run; the dependents keep going.

## Event handling

| Event | Action |
|---|---|
| `BRANCHREVIEW <branch> <verdict>` | Queue the review on the issue immediately. The review pass reads the tip of every pushed work branch; `branchreview.sh` writes the verdict under the branch key. Review with the first entry of `chains.review` in `scripts/models.json` whose `hrs` window holds the current moment, otherwise the next entry: read the file, never this paragraph. Fall back to the next configured entry on a model or provider failure. The review machinery runs detached under `watch.sh`; the orchestration loop waits for nothing. Recompute the frontier immediately. |
| `BRANCHREVIEW … CHANGES` or CI red | Queue findings on the issue immediately. Read `review-<branch with / as _>.md`. Fix real defects through a squad fix run on the work branch (the fix pass dispatches one), never by hand — except a defect in the loop itself, which is fixed outside it (§9). Card back to In Progress. A wrong base means rebase on `origin/$SG_BASE`. Recompute the frontier before launching so compatible fixes/work are grouped when safe. |
| `BRANCHLAND <sha>` | Queue the landing evidence: the branch, the landed SHA and the checks. `branchland.sh` fast-forward pushes the rebased tip to `$SG_LAND_BRANCH` and deletes the branch, then closes the issue through the API. Never push over a branch the repo rules reserve for the owner. Promotion is not the owner's step any more: §8 promotes `dev` into `stg` by one pull request, and `stg` into `main` by a fast-forward on a green complete CI tier. Respect declared landing order, including an ADR before the branches that cite it. Then start e2e detached on the exact landed SHA. Recompute the frontier immediately rather than waiting for e2e. |
| `<PASS>: the open-issue list is not readable … cooldown` | Read `gh-cooldown.json`, post one comment, then make no GitHub call until its `until`; re-run that pass by hand afterwards. The pass is not broken and the landing is not lost — GitHub is rate-limiting. |
| `RUN … verdict` | Queue the verdict if meaningful. Remove that run's worktrees only after the run is no longer alive: `git worktree remove --force <abs path>`, then `git worktree prune`. Do **not** drop it from `active-runs.txt`: a finished run stays listed until its wave lands, and a hand drop orphans the wave (`startability.sh`, `watch.sh`). The janitor drops it once the wave landed or closed. Recompute the frontier and fill freed slots immediately. |
| `STALL RUN …` | Preserve recoverable work, queue the stall and recovery decision, terminate only the stuck execution if needed, then launch a continuation on the same branch. Do not disturb unrelated children. |
| `STALL REVIEW …` | Queue the stall. Restart/recover the review using the configured review fallback policy. Do not block unrelated merges/work. |
| `STALL CI …` | Queue the stall and available evidence. Retry/recover according to CI policy; if external CI remains unavailable, mark only the affected work Blocked and continue unrelated work. |
| `STALL E2E …` | Queue the stall. Preserve the exact tested SHA, restart the e2e check, and continue unrelated work. |
| `MODEL_FAIL …` | Queue the failure with role/model/error class. Apply the role's retry budget, then automatically switch to the next configured model. Resume from durable state. |
| `MODEL_SWITCH …` | Queue the replacement model and reason. Continue without owner intervention. |
| `QUOTA_GUARD anthropic remaining=…` | If remaining weekly Anthropic quota is 10% or less, queue the quota-protection event, make Anthropic models ineligible for new autonomous work, move affected work to healthy non-Anthropic models at the next safe durable checkpoint, and continue without owner intervention. The protected final 10% MUST NOT be intentionally consumed by squad work. |

After every row above: recompute the complete frontier.

## Run verdicts

- `failed` + `ac-verify:no_ac_commands` is benign; check that the branch was pushed.
- `node_turn_cap` means the run died mid-work. Preserve the worktree, commit recoverable work as WIP if needed, push the branch, queue the recovery on the issue, and launch a continuation run.
- The `unrecognized_model` warning for deepseek is noise only if the requested model actually runs successfully. If execution does not progress, treat it as `MODEL_FAIL` and use the fallback policy.
- Any unknown failure is an event: preserve state, queue evidence, recover if possible, and continue unrelated work.

## Model fallback policy

Model fallback is role-based and lives in `~/.claude/skills/squad-goal/scripts/models.json` (the squad workflow schema rejects unknown fields, so it cannot live in `dynamic.graph.json`). Squad coding ids must also be in that workflow's model allow-list. `review.sh` already walks `review_models` and queues `MODEL_FAIL`/`MODEL_SWITCH` comments. Each model try is killed after `review_timeout_seconds` (default 480; `MODEL_TIMEOUT`, then the next model, no retry), and every try's latency goes to `$SG_STATE/review-times.tsv` (order `review_models` by that data, with the owner's go-ahead). Fix rounds are reviewed incrementally: when the last review asked for CHANGES and the new head descends from the reviewed sha (`review-<key>.sha`, compare status `ahead`) with a delta of at most `review_delta_max_lines`, the reviewer gets the previous findings plus that delta; a rebase or a first review gets the full diff. The review pass runs every watch cycle; for coding, name the next `coding_models` entry in the continuation run's request. `models.json` is v2: each chain holds entries `{"id", "raw", "route", "effort", "hrs", "on"}` under `chains.<kind>`. An entry's `effort` is low|medium|high|max, and the default is medium, which is stored as an absent key; when dispatching a chain entry, pass that effort in the request/review call if set, otherwise use the default (medium). An entry's `hrs` is a local window `"HH-HH"` (start inclusive, end exclusive, wrapping midnight: `22-07`), or clauses joined by `;`, each with optional days, ranges joined by `,` and an optional `UTC` suffix (DeepSeek off-peak: `mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC`); `modelreg.in_window` owns the grammar, and the `--tsv` column 8 says whether the window holds now; **the default is all day** — a model with no window always runs, so a window is an opt-in restriction. `review.sh` skips an out-of-window model exactly like an unhealthy one (`MODEL_HOURS` on the issue, no `MODEL_FAIL`) and the chain moves on; `launch.py` applies the same rule to `coding_models`. An entry's `on: false` takes it out of the chain without removing it. Every setting is edited per model from the hub's models panel (each row has its own `effort`, `hrs` and switch), so a reader writes the entry, never a top-level map. The top-level `efforts`/`hours`/`off` maps are the v1 shape: a hub Save migrates the file and drops them, so a value written there is ignored.

Current chains: read `scripts/models.json` (source of truth, edited from the hub); never copy them here.

### Anthropic quota guard

Apply `anthropic_weekly_reserve_percent` as a hard eligibility gate before ordinary model preference/fallback selection. The guard is a file: `touch $SG_STATE/quota-guard` turns it on (scripts skip `anthropic_models`), `rm` turns it off. No programmatic reading of the Anthropic weekly quota exists in a headless session, so the guard is event-driven. It turns on when any Anthropic call (squad child, `claude -p` review, the session itself) fails with a usage-limit or weekly-limit error, or when the owner reports the quota at or under the reserve. It turns off when the limit's stated reset time passes. Codex works the same way: a `hit your usage limit` error writes `codex-limited`, and `review.sh` skips every Codex model until that time. Note the orchestrating session itself is Anthropic: when the guard is on, keep its own turns lean (monitor events, short commands) since it cannot switch models.

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
3. keep each affected card in the column its work reached and put a badge on it: a card never moves back (ADR-0143 rule 1), and the one backward move in the vocabulary is Doing to Todo for a run that delivered nothing;
4. dispatch a fix through agent-squad, never by hand — except a defect in the loop itself, fixed outside it (§9);
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
   - the work branch and the landed SHA;
   - merge SHA;
   - test counts;
   - render-gate result when applicable;
   - relevant autonomy decisions;
2. close the issue;
3. move the card to Closed;
4. recompute the frontier immediately.

Close the parent spec last.

Visual checks (render versus wireframe) use the `vision_models` chain, Grok 4.6 first (known-image probe passed 2026-10-07). Copy the PNGs with `/usr/bin/git show` (the rtk hook corrupts binaries) into an isolated folder, `cd` there and run `grok -p "Look ONLY at the images here; do not read other files; say CANNOT_SEE_IMAGE if you cannot" -m grok-4.6 --always-approve`; a repo in reach lets Grok answer from source instead of pixels. If Grok fails, fall back to Claude's own vision and say so. The `hours` rule applies here too: a `vision_models` entry whose window excludes the current local hour is skipped exactly like an out-of-window coding or review model, and the chain moves on. No script enforces this one — it is on whoever picks the vision model. A visual failure never blocks unrelated work.

## Final completion check

Before declaring the goal done:

1. fetch `origin/$SG_BASE`;
2. verify every requested issue is closed;
3. verify every requested card is Closed;
4. verify no required acceptance criterion is only present on an unmerged branch;
5. verify the final e2e is green on the current final `origin/$SG_BASE` SHA;
6. verify no active run or unlanded work branch is still carrying required work;
7. verify `origin/stg` holds the final `dev`, and the live copy was published from a tree on `stg`;
8. verify all significant events and decisions have durable issue comments;
9. verify every `needs-ratification` item is listed for the owner.

If any condition fails, the goal is not done. Recover or continue the loop.

## Final chat report (pt-BR)

Report:

- landed branches and closed issues;
- final `origin/$SG_BASE` SHA and e2e evidence;
- autonomy decisions (`alone`, `advisor`, `panel`);
- model failures/fallbacks that occurred;
- Anthropic quota-guard activations and substitutions that occurred;
- stalls and recoveries that occurred;
- open `needs-ratification` items;
- anything left undone, and why;
- the promotion and the publish: the `stg` sha, the file count, and the time the live copy was published.