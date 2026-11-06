# ADR-0172: A blocking finding names its repro

## Status

Accepted. Amends [ADR-0151](0151-a-review-streak-escalates.md).

## Context

A reviewer files a defect as Blocking on its own reading. Nothing in the review shows that the defect is present at the
head, so a finding the code does not have can hold a branch and start a fix round. A repro command is the cheapest
check: a line that exits non-zero while the defect is present.

## Decision

1. **A Blocking finding names a repro.** The finding carries one shell command, run at the branch head, that exits
   non-zero while the defect is present.
2. **The loop runs the repro under the signal wall** in the tree that holds the reviewed head: the worktree of a
   `--worktree` review; in a `--branch` review, the checkout when it is at the head, else a throwaway worktree of the
   head. No such tree: no repro runs, and every finding stays Blocking. A repro that exits 0 moves the
   finding to Debt, marked with the head it passed at. A repro that cannot run (timeout, not executable, not found) also
   moves it to Debt, marked as not run. Any other non-zero status keeps the finding Blocking.
3. **A finding with no repro is Debt.** The parser moves it, so no reader sees a Blocking finding without a repro.
4. **A markdown answer has no repro.** Its findings are read as before.
5. **A plan review names no repro.** A plan reads no code, so its prompt asks for none, its answer is parsed with no
   demotion, and no repro runs (ADR-0171).

## Consequences

- A Blocking finding stands only when the loop reproduces it at the head. A review with no reproducible Blocking finding
  is an approval by the existing Debt path (ADR-0151, item 3).
- A reviewer that omits repros turns every Blocking finding into Debt. The prompt asks for repros; the rate is measured
  before any threshold changes.
- The repro command is shown in the review, after the four fields.

## Guard

`contrib/squad-goal/scripts/test-revformat.sh`: a blank repro demotes its finding to Debt, a set repro renders as a fifth
field, a finding with no repro key is refused, and `--repro` keeps a failing repro Blocking and moves a passing or
unrunnable one to Debt with its head. `contrib/squad-goal/scripts/test-review.sh`: a review whose Blocking repro passes is
saved with the finding in Debt, and a review whose every repro passes is an approval; the repro runs in the reviewed tree
(a `--worktree` review), a throwaway worktree of the head, or not at all when no tree holds it. `test-loopbug-review.sh`:
a plan review with a blank repro stays CHANGES, and its prompt asks for no repro.
