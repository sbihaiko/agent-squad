# ADR-0145: A work branch lands in `dev` by a fast-forward push, with no pull request

## Status

Accepted (owner). Decision, verbatim: "three long-lived branches (`dev`, `stg`, `main`); a work branch lands in `dev` by a fast-forward push, with no pull request; the loop promotes `dev` to `stg` when the CI of the `dev` tip is green; the owner promotes `stg` to `main` with one command; the live copy publishes from `stg`; a card is Done when its branch is in `dev`."

Amended (owner, after a Fable review). Decision, verbatim: "ADR-0145 gains the wave rules approved by the owner after a Fable review: one branch per wave; a child enters it by a rebase on the wave tip and `--ff-only`, after its local gates pass in its own worktree; a child that fails never enters; the CI runs once per wave, when the wave lands in `dev`; a red wave CI finds its cause by running each child's suite again against the wave tip, the last entrant first; a wave has an age cap; Code Complete means the child is in the wave branch, Done means the wave is in `dev`; the wave branch is rebased on `dev` only before the final push."

## Context

A pull request per work branch put a review step and a CI queue between finished work and the rest of the work. The repository is private and the suite is the check ([ADR-0116](0116-implement-in-a-worktree.md)), so the step added latency and no evidence. One `main` also served three jobs: the line work lands on, the line the live copy publishes from, and the line the owner trusts. A landing that was not yet trusted could still reach the live copy.

## Decision

1. **There are three long-lived branches: `dev`, `stg` and `main`.** Every other branch is a work branch and ends when its work lands.
2. **A work branch lands in `dev` by a fast-forward push, with no pull request.** The run rebases its branch on `origin/dev`, runs its checks, and pushes the tip to `dev`. A rejected push repeats from the fetch. A run never force-pushes a long-lived branch.
3. **The loop promotes `dev` to `stg` when the CI of the `dev` tip is green.** It promotes by a fast-forward of `stg` to that tip. A red or missing CI result promotes nothing, and a later green tip carries the earlier work with it.
4. **The owner promotes `stg` to `main` with one command.** No agent promotes to `main`. The command fast-forwards `main` to the `stg` tip.
5. **The live copy publishes from `stg`.** It never publishes from `dev` or from a work branch ([ADR-0140](0140-the-live-copy-follows-in-the-cycle.md)).
6. **A card is Done when its work is in `dev`.** A pull request no longer exists to carry the landing, so the landing is the tip reaching `dev` ([ADR-0143](0143-a-card-only-moves-forward.md)). For a child in a wave, rule 12 sets the meaning.
7. **CI runs on every push to `dev`, `stg` and `main`,** so each promotion has its own evidence.
8. **A wave has one branch.** A wave is the set of children that land in `dev` together. The wave branch is the only branch that lands in `dev` for them.
9. **A child enters the wave by a rebase on the wave tip and `--ff-only`,** after its local gates pass in its own worktree. A child that fails its gates never enters the wave.
10. **CI runs once per wave, when the wave lands in `dev`.** A child's entry into the wave starts no CI of its own.
11. **A red wave CI finds its cause by running each child's suite again against the wave tip, the last entrant first.** The first suite that fails names the child that broke the wave.
12. **Code Complete means the child is in the wave branch; Done means the wave is in `dev`.**
13. **A wave has an age cap.** A wave that reaches the cap lands as it is; it takes no new child.
14. **The wave branch is rebased on `dev` only before the final push.** Between entries the wave tip moves only by child entries.

Rejected: a pull request per work branch. Rejected: a merge commit on promotion. A fast-forward keeps one line in `git log`.

## Consequences

- A change in `dev` is not yet trusted. Trust grows along the line: green CI moves it to `stg`, and the owner moves it to `main`.
- A defect that reaches `dev` is found by the CI of the `dev` tip, before `stg` carries it.

## Guard

`tests/test_tooling.py` guards rule 7 (the CI triggers in `.github/workflows/ci.yml`). `tests/test_board_facts.py` and `contrib/squad-goal/scripts/test-janitor-board-facts.sh` guard rule 12 (the facts that place a card in Code Complete, Landing, Testing or Doing). Rules 1 to 6, 8 to 11, 13 and 14 have no guard yet; they are a draft ([ADR-0018](0018-failing-gate-is-a-defect.md)).
