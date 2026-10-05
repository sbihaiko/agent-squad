# ADR-0120 — The repository is left clean; no worktree or branch outlives the work it was made for

## Status

Accepted, 2026-10-02. Owner direction: "sempre mantenha o repo clean".

## Context

ADR-0116 sends every `/implement` into its own worktree and merges it back into `main`. Nothing said what happens to the worktree after the merge. On 2026-10-02 the checkout held twelve worktrees under `.claude/worktrees/` and their branches, every one of them fully merged into `main` and holding no commit `main` did not already have. `git worktree list` showed thirteen entries where one was expected.

The cost is not disk. It is a reader who cannot tell an abandoned experiment from work in flight, and a stale checkout that the next agent may mistake for a live one.

## Decision

1. **A unit of work ends with the working tree clean.** Every change is committed and pushed (ADR-0026). `git status --short` prints nothing.
2. **A worktree is removed when the work it was made for lands, and its branch goes with it.** ADR-0116 says the work merges back into `main`; this rule says the scaffolding does not stay. A branch named for a harness-made worktree, with no commit beyond `main`, is leftover, not work in progress.
3. **Scratch files live outside the repository**, under `/tmp` or the run folder. A probe script, a dump, or a decision brief never lands as an untracked file.
4. **A deliberate exception is named, not left.** A branch kept on purpose — a `prototype/<name>` branch carrying a primary source — is the one thing that stays. Keeping it is an act, not an omission.
5. **The reference for "clean" is mechanical**, not a judgement: `git status --short` empty, and `git worktree list` holding the main checkout alone, or only worktrees that hold a commit absent from `main`.

## Considered options

- **Keep the worktrees, for a cheap resume.** Rejected. ADR-0107 already checks the workspace first on a resume and treats a missing one as a stated condition, so a resume does not need a stale directory to survive.
- **Let `git gc` or an automatic prune handle it.** Rejected as a non-answer: nothing prunes a worktree, and pruning is exactly the act this rule requires.

## Consequences

- A resume that named a removed worktree finds it gone. ADR-0107's workspace check already covers that path.
- The twelve worktrees and seventeen branches removed on 2026-10-02 leave `main` and one deliberate `prototype/` branch.
- The rule binds the agent, which is what the owner asked for. It does not police a person mid-work: the guard ignores the checkout this test runs in, so an in-flight worktree is never named.

## Guard

Tests that guard this rule:

- `tests/test_repo_hygiene.py::test_no_finished_worktree_is_left_behind`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
