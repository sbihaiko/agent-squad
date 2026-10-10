# Changelog

## 0.2.1 (local fork, 2026-10-09)

- **dashboard**: the popup cycles cards with ‹ › and ←/→; a selection no longer pans the map behind it.
- **dashboard**: a child is labeled `issue #N` from the tracker issue its prompt names, never from
  another numbered file (an ADR named `0272-*.md` used to show as "issue 72").
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
  - **Worktrees**: `wtclean.sh` removes finished, pushed child worktrees and leaves the run folder and its dashboard server alone (the hub lists runs from live servers); unpushed ones are kept and reported.
  - **Issue comments**: dispatch, PR opened, run verdict, review/CI result, fix, panel, merge and e2e are
    queued and posted on the issue by `cqflush.sh`.
  - **Hub** (`hub.py`): live run dashboards, board columns with live totals (closed PRs counted from the
    real total), PR open-age pill, auto-selection of a new run, readable run names; repo and board come
    from the profile.
