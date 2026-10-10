# Changelog

## 0.2.1 (local fork, 2026-10-09)

- squad-goal: review speed round (Fable audit, 54 min open-to-merge, 21.8 min of it in the review chain). `gemini-3.8-flash-medium` leaves the review chain (0 of 30 verdicts headless) and `review_timeout_seconds` is 210; `review.sh` kills the reviewer when the PR head moves during the review; `revci.sh` pins the head sha before the review, so a push during it ends as SUPERSEDED instead of reporting the old verdict on the new head; `retryfix.sh` re-arms a PR sha whose autofix run failed or died (max 2 retries, test-covered with a fake state); `watch.sh` logs any step of 5 s or more to `cycle.log`.

- squad-goal: `revci.sh` reports a conflicting head (`mergeable_state` dirty on two polls, 15 s apart) at once, as `CI=none`, instead of waiting 45 min for checks that never start (a conflicting PR gets no CI); autopilot already turns a dirty line into the conflict-only autofix round. Stub-tested.

- squad-goal: review chain puts `claude:claude-opus-5-5` first. Measured (49 tries): `gemini-3.8-flash-high` ran 30 s and returned no VERDICT in headless mode (a tool call was auto-denied, "no output produced"), twice per PR; `grok-4.6` timed out at 300 s in 12 of 19 tries; Opus averaged 77 s with 0 failures. Every review paid about 6 min before Opus answered; Gemini, Grok and GPT stay as fallbacks.
- squad-goal: a run that ends `failed` with `no_dispatch` gets one consult: `consult.sh` asks the coding model (`coding_models[0]`, claude-sonnet-5-5) through the CLI whether the request is valid; GO relaunches it once with the answer appended. `launch.py` keeps the request in the run folder (`request.txt`).
- squad-goal: `autofix.sh` and `forkclose.sh` requests also say "Spawn the child with spend 8"; an autofix run for #1209 was rejected with `spawn_rejected:spend_outside_budget` (router asked for 40).
- squad-goal: `autobug.sh` and `autofrontier.sh` requests say "Spawn each child with spend 8", so the router no longer asks for 40 and gets `spawn_rejected:spend_outside_budget`.
- squad-goal: `autobug.sh` skips an issue that a live run already works (a `spawn_accepted` for it in a run without a verdict), so it never dispatches a duplicate beside a run that is already fixing it.
- squad-goal: `stack.sh` stacks on a stacked PR (chains up to `SG_STACK_DEPTH`, default 3); `forkclose.sh` treats a lock whose pid is gone as stale.
- squad-goal: `autopilot.sh` never merges a PR whose diff is empty (a fix round that undid the change); it raises NEEDS OWNER instead. The fork test command runs every `tests/test_gui_test_*.py`.
- squad-goal: `forkclose.sh` sweeps every fork branch; an issue with a MesenAI PR waits for its merge, then its fork half lands with no close; each fork test first rebuilds `SG_E2E_BUILD_DIR` at the base head (`SG_E2E_BUILD_CMD`).
- squad-goal: `autopilot.sh` no longer retargets a stacked PR whose parent was never merged (an empty jq match printed "null null", read as a merged parent).
- squad-goal: a red fork suite no longer strands the branch: after the revert, `forkclose.sh` re-applies the work on the branch (merge base in, revert the revert) and launches a fix run with the log tail; the fix commit re-arms the close.
- squad-goal: `wtclean.sh` keeps a fork worktree while any live run references it (a final gate cds into it).
- squad-goal: an issue with both a PR and fork work: `forkclose.sh` merges and tests its fork half (Code Complete or Testing) without closing it, and `stack.sh` hands dependents both the PR to stack on and the fork branch to merge.
- squad-goal: the pre-push checklist forbids an invented owner go-ahead: an ADR amendment needs a quote present word for word in the request, else the conflicting change is undone.
- squad-goal: `stack.sh` dispatches on Code Complete (owner's pick 2026-10-10): a Code Complete blocker no longer gates; with a PR it stacks, with fork-only work its unmerged fork branch is named (`READY FORK <br>`) and `autofrontier.sh` tells the child to merge it into its own fork branch first.
- squad-goal: `wtclean.sh` also drops stale `acpr.sh` trees (and `acpr.sh` removes its tree on kill), closed fork-only issue worktrees with their merged branch, `~/wt-*` worktrees of merged PRs. Child worktrees still wait for the run verdict: the land step reads every child workspace (an early per-child cleanup failed a run with land_workspace_missing and was reverted).
- squad-goal forkclose: a merge conflict launches a conflict-fix squad run on the fork branch (base merged in, both sides kept); its new head re-arms the close. The hub labels such runs `merge #N`.
- squad-goal **autofrontier** (`scripts/autofrontier.sh`): the loop dispatches the startable frontier itself as soon as a blocker falls (one squad start, free slots, wave order, profile preamble).
- squad-goal **forkclose** (`scripts/forkclose.sh`): fork-only Code Complete work is merged into the fork base, tested through `test.sh` and closed with evidence, or the merge is reverted (`git revert -m 1`) on a red suite.
- squad-goal `stack.sh`: optimism keys on the blocker's card being in Testing, not on a merged PR that says "Refs #N" (a docs PR citing an issue had unblocked its dependents).
- squad-goal GraphQL diet: card moves are REST (`scripts/board.py`, cached field/item ids), `reconcile.py` reads and moves over REST, the review diff is REST, and the pre-push checklist tells children to open PRs with `gh api --method POST`.
- squad-goal review chain reordered by measured latency: Gemini 3.8 flash high (median 18 s) before Grok 4.6 (median 271 s, 2/6 timeouts); timeout 480 s -> 300 s.
- squad-goal hub: a run that implements issues is labeled `build #N …` (was `code #N …`).
- squad-goal board latency: moves go through `scripts/board.sh`, which flags the hub to refresh within 2 s (was up to 60 s + 15 s page poll, now 5 s); `childcc.py` runs every 30 s.
- squad-goal `scripts/childcc.py`: a child that finishes with outcome pass moves its issue to Code Complete during the run (live runs only, once per child; the issue is read from the child's spawn prompt).
- squad-goal hub: a run whose final gate failed but whose work landed and whose issues were all closed afterwards (verified by hand, evidence on the issue) shows as `verified`, not `failed`.
- squad-goal `scripts/test.sh`: a coordinator test pass (post-merge e2e, fork suite) moves the card to Testing, logs under `$SG_STATE/tests/`, queues the result on the issue, and shows on the hub as `test #N` with its live log as the panel.
- squad-goal hub: a bug-fix run (request says "(bug)" or names a `fix/` branch) is labeled `fix #N`, not `build #N`.
- squad-goal **autobug**: `scripts/autobug.sh`, run by the watch loop every ~6 min, dispatches every open `bug` issue without an open PR, dispatch marker or skip label, all in one squad start within the free slots (one child and PR each, TDD with the RED output in the PR); profile knobs `SG_AUTO_BUGFIX`, `SG_BUG_LABEL`, `SG_BUG_SKIP_LABELS`, `SG_BUG_PROJECT`/`SG_BUG_DOING`.
- squad-goal `stack.sh` is optimistic while testing: a blocker whose PR is already merged into the base no longer gates its dependents while its e2e runs or the issue waits to close.
- squad-goal board flow gains **Code Complete** between In Progress and Testing: an open PR is Code Complete (`revci.sh`, `reconcile.py`); Testing starts at the merge, while the e2e on the merge SHA runs (`autopilot.sh`); the hub orders and colors the new column.
- squad-goal autopilot: PR fields are joined with a unit separator; a tab IFS collapsed an empty labels field, read the base ref as empty and skipped every auto-merge.
- **dashboard**: the popup cycles cards with ‹ › and ←/→; a selection no longer pans the map behind it.
- **dashboard**: a child is labeled `issue #N` from the tracker issue its prompt names, never from
  another numbered file (an ADR named `0272-*.md` used to show as "issue 72").
- **vendor/skills/to-tickets** (re-vendored with `scripts/build_vendor_skills.py`): the `/to-tickets` skill with two additions: every ticket gets a wave (1 + the highest wave of its blockers) and is published with its labels (`ready-for-agent`, one `wave:NN`, a type label when one fits); the personal copy (`~/.agents/skills/to-tickets`) is the vendoring source and wins over the plugin's.
- **contrib/squad-goal**: the autonomous delivery loop that drives tracker issues through agent-squad
  (one `squad start` per frontier, live issue comments and board, independent review, autopilot, hub).
  Synced from the working skill with `scripts/sync-to-fork.sh`; per-repo profiles stay local, and
  `scripts/profiles/example.sh` is the template. Needs `gh`, and `drive-board`'s `board-status.sh` for
  board moves. Highlights:
  - **Autopilot** (`autopilot.sh`, every 30 s from `watch.sh`): reviews each new PR head, then
    `autofix.sh` sends CHANGES, failed CI, conflicts and unresolved review threads back to the squad as
    one fix run (2 rounds; thread- or conflict-only rounds have their own cap of 3); at the cap,
    `autopanel.sh` asks a panel model for one more round or labels the PR `needs-human`; merges on
    APPROVE + green CI + clean + no unresolved threads.
  - **Review** (`review.sh`): walks `review_models` with a per-model timeout (`review_timeout_seconds`),
    logs latency to `review-times.tsv`, and reviews fix rounds on the delta since the last reviewed sha
    with the previous findings (full review on a first pass, a rebase or a big delta).
  - **Optimistic stacking**: `stack.sh` lets an issue start on its only open blocker's PR branch (one level, parent CI not red); the autopilot holds the stacked PR until the parent merges, then retargets it to the base branch and fixes the squash conflict with `rebase --onto`; its review covers only its own diff over the parent.
  - **Done check**: every request asks the child for `ac_commands` (wrapped in `acpr.sh <branch> -- <cmd>` for PR-branch work, since the final gate runs in the main checkout), so `ac-verify` verifies instead of failing with `no_ac_commands`; the hub shows a landed run with nothing verified as `unverified`, in its own color.
  - **Worktrees**: `wtclean.sh` removes finished, pushed child worktrees and leaves the run folder and its dashboard server alone (the hub lists runs from live servers); unpushed ones are kept and reported.
  - **Issue comments**: dispatch, PR opened, run verdict, review/CI result, fix, panel, merge and e2e are
    queued and posted on the issue by `cqflush.sh`.
  - **Hub** (`hub.py`): live run dashboards, board columns with live totals (closed PRs counted from the
    real total), PR open-age pill, auto-selection of a new run, readable run names; repo and board come
    from the profile.
