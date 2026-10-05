# ADR-0026 — Commits and pushes are automatic; do not ask

## Status

Amended 2026-10-01 by [ADR-0116](0116-every-implement-works-in-its-own-worktree-and-merges-back-into-main-when-it-finishes.md): an `/implement` run works in its own worktree and merges into `main` when it finishes. "Work directly on `main`" below holds for a change made by hand.

Accepted, 2026-09-29.

## Context

An agent that asks permission before each commit and push wastes time. The owner gives the same answer every time: commit and push.

Another repo is a different case. That repo has its own rules, and those rules change. A local hold on pushes can be correct.

## Decision

After any change in this repo, commit and push without asking. Work directly on `main`. Rafael can still ask for a commit to be amended or undone.

The remote is GitHub mmctech-ai/agent-squad. Push to it after every commit.

In another repo, follow that repo's own rule. If that rule forbids a push, commit locally and stop. If that rule is silent, this ADR applies. A change to that rule is a grant under ADR-0024. Record the grant with its date before you use it.

## Consequences

Turnaround is faster. The remote stays current.

Rafael gives a direct instruction to stop when he wants review first. An agent that holds commits back states why. It names the rule that applies. A reader then does not mistake the hold for a failure.

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
