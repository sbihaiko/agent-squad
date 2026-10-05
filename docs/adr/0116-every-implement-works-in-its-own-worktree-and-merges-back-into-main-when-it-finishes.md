# ADR-0116 — Every `/implement` works in its own worktree and merges back into main when it finishes

## Status

Accepted, 2026-10-01. Owner instruction (Rafael). Amends [ADR-0026](0026-commits-and-pushes-are-automatic.md): the "work directly on `main`" line holds for a change made by hand, not for an `/implement` run.

## Context

On 2026-10-01, two forks ran `/implement` on #56 in the same working tree. One overwrote the test file of the other. The first fork stopped, copied its files to `/tmp`, and removed them from the repo, so its test file was lost. Agents that run in parallel share one index, one set of untracked files, and one `HEAD`.

## Decision

1. **Each `/implement` run starts in its own git worktree,** on its own branch, cut from the current `origin/main`. A subagent or a fork gets that worktree before its first write (`isolation: worktree`, or `git worktree add`). The primary checkout holds no `/implement` edits.
2. **The run ends with a merge into `main`.** Before it merges, the run fetches `origin/main` and rebases its branch on it, then runs the full suite on the rebased branch. It merges only when the suite is green (ADR-0062). It fast-forwards `main`, pushes, and removes its worktree and its branch.
3. **A rebase conflict is resolved by intent,** by working through the conflict, never by taking one side (upstream removed `/resolving-merge-conflicts` in plugin 1.3.1; no skill replaces it). A rejected push repeats rule 2 from the fetch. A run never force-pushes `main`.
4. **A run that cannot merge leaves its worktree and branch in place,** and says so with the path and the reason. It never deletes work that has not reached `main`.
5. **Two runs never share a worktree.** A ticket has one run at a time. A second run on the same ticket is refused until the first run has merged or been abandoned on the record.
6. **This holds for every agent in the workflow,** including a run under `/autonomy`. A change made by hand outside `/implement` still follows ADR-0026.

## Considered options

- **All runs on `main` in one tree** (ADR-0026 as it stood). Rejected: it lost a test file on 2026-10-01. The tree has one index, and parallel runs overwrite each other.
- **One branch per run and a pull request for each.** Rejected: ADR-0026 makes the owner's approval of each push a cost with no benefit here, and the repository is private. The suite is the check.
- **A merge commit instead of a rebase.** Rejected: it adds a merge commit for each run, and `git log` on `main` then shows the run's steps beside other runs' steps. A rebase keeps one line. Reopen if a rebase rewrites a commit that another run already cites.

## Consequences

A run cannot touch another run's files. Parallel `/implement` runs on different tickets are safe.

Cost: each run builds its own worktree, so a run starts slower and uses more disk. The suite runs once more after the rebase. A run that finishes late can hit a conflict that a single tree would not show.

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018). A candidate guard is a check that an `/implement` run's commits reach `main` from a worktree path, not from the primary checkout.
