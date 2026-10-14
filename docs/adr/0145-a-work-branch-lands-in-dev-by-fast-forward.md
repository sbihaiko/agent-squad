# ADR-0145: A work branch lands in `dev` by a fast-forward push, with no pull request

## Status

Accepted (owner). Decision, verbatim: "three long-lived branches (`dev`, `stg`, `main`); a work branch lands in `dev` by a fast-forward push, with no pull request; the loop promotes `dev` to `stg` when the CI of the `dev` tip is green; the owner promotes `stg` to `main` with one command; the live copy publishes from `stg`; a card is Done when its branch is in `dev`."

## Context

A pull request per work branch put a review step and a CI queue between finished work and the rest of the work. The repository is private and the suite is the check ([ADR-0116](0116-implement-in-a-worktree.md)), so the step added latency and no evidence. One `main` also served three jobs: the line work lands on, the line the live copy publishes from, and the line the owner trusts. A landing that was not yet trusted could still reach the live copy.

## Decision

1. **There are three long-lived branches: `dev`, `stg` and `main`.** Every other branch is a work branch and ends when its work lands.
2. **A work branch lands in `dev` by a fast-forward push, with no pull request.** The run rebases its branch on `origin/dev`, runs its checks, and pushes the tip to `dev`. A rejected push repeats from the fetch. A run never force-pushes a long-lived branch.
3. **The loop promotes `dev` to `stg` when the CI of the `dev` tip is green.** It promotes by a fast-forward of `stg` to that tip. A red or missing CI result promotes nothing, and a later green tip carries the earlier work with it.
4. **The owner promotes `stg` to `main` with one command.** No agent promotes to `main`. The command fast-forwards `main` to the `stg` tip.
5. **The live copy publishes from `stg`.** It never publishes from `dev` or from a work branch ([ADR-0140](0140-the-live-copy-follows-in-the-cycle.md)).
6. **A card is Done when its branch is in `dev`.** A pull request no longer exists to carry the landing, so the landing is the branch tip reaching `dev` ([ADR-0143](0143-a-card-only-moves-forward.md)).
7. **CI runs on every push to `dev`, `stg` and `main`,** so each promotion has its own evidence.

Rejected: a pull request per work branch. Rejected: a merge commit on promotion. A fast-forward keeps one line in `git log`.

## Consequences

- A change in `dev` is not yet trusted. Trust grows along the line: green CI moves it to `stg`, and the owner moves it to `main`.
- A defect that reaches `dev` is found by the CI of the `dev` tip, before `stg` carries it.

## Guard

`tests/test_tooling.py` guards rule 7 (the CI triggers in `.github/workflows/ci.yml`). Rules 1 to 6 have no guard yet; they are a draft ([ADR-0018](0018-failing-gate-is-a-defect.md)).
