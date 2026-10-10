# ADR-0116: Every `/implement` works in its own worktree and merges back into main when it finishes

## Status

Accepted.

## Context

Agents that run in parallel share one index, one set of untracked files and one `HEAD`. Two `/implement` runs in one working tree overwrite each other's files. Finished worktrees and branches that stay behind hide which work is live.

## Decision

1. **A change made by hand works on `main`.** Commit and push after every change. Do not ask. A change the owner left uncommitted in the tree is included: the agent commits it and pushes it with its own work, whoever made it and whenever it was made. It first checks that the change holds no secret. It never discards the change and never leaves it pending. The owner can ask for a commit to be amended or undone. In another repo, follow that repo's own rule. If that rule forbids a push, commit locally and stop.
2. **Each `/implement` run starts in its own git worktree,** on its own branch, cut from the current `origin/main`. A subagent or fork gets that worktree before its first write. The primary checkout holds no `/implement` edits.
3. **The run ends with a merge into `main`.** It fetches `origin/main`, rebases its branch, and runs the full suite on the rebased branch. It merges only when the suite is green (ADR-0062). It fast-forwards `main`, pushes, and removes its worktree and its branch.
4. **A rebase conflict is resolved by intent,** never by taking one side. A rejected push repeats rule 3 from the fetch. A run never force-pushes `main`.
5. **A run that cannot merge leaves its worktree and branch in place,** and reports the path and the reason. It never deletes work that has not reached `main`.
6. **Two runs never share a worktree.** A ticket has one run at a time.
7. **The repo is left clean.** A unit of work ends with `git status --short` empty. No worktree or branch outlives the work it was made for. A branch kept on purpose, such as a `prototype/<name>` branch, is named, not left.
8. **Scratch files live outside the repo,** under `/tmp` or the run folder.
9. **This holds for every agent in the workflow,** including a run under `/autonomy`.

Rejected: one branch and a pull request per run. The suite is the check, and the repo is private. Rejected: a merge commit instead of a rebase. A rebase keeps one line in `git log`.

## Guard

`tests/test_repo_hygiene.py` guards rule 7. Rules 1 to 6 have no guard yet; they are a draft.
