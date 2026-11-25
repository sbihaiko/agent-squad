# ADR-0172: A blocking finding names its repro

## Status

Accepted. Amends [ADR-0151](0151-a-review-streak-escalates.md).

## Context

A reviewer files a defect as Blocking on its own reading. Nothing in the review shows that the defect is present at the
head, so a finding the code does not have can hold a branch and start a fix round. A repro command is the cheapest
check: a line that exits non-zero while the defect is present.

## Decision

1. **A Blocking finding names a repro.** The finding carries one shell command, run at the branch head, that exits
   non-zero while the defect is present. The prompt states the polarity: text that must be absent is `! grep -q …`,
   text that must be present is `grep -q …`, and the last command's status decides.
2. **The loop runs the repro under the signal wall and the write wall, in the gate's environment.** The checkout's
   venv comes first on `PATH`, and pytest carries `-p no:deepeval`, so a test the gate passes is not red at startup.
   The signal wall refuses signals to any process
   outside the repro. The write wall refuses every file write except in the tree that holds the reviewed head and in the
   repro's own scratch directory, which is its `TMPDIR` and is removed after the run. The tree is the worktree of a
   `--worktree` review, else a throwaway worktree of the head in a `--branch` review; the checkout is never the repro's
   tree. No such tree: no repro runs, and every finding stays Blocking. A repro that exits 0 moves the finding to Debt,
   marked with the head it passed at. A repro that cannot run (timeout, not executable, not found) also moves it to
   Debt, marked as not run. A repro whose sandbox cannot apply (a session already under a sandbox refuses a nested one)
   proved nothing: the finding stays Blocking and the loop names the refusal. A sandbox executable that cannot start at
   all (a host with no `sandbox-exec`, as a Linux runner) is the same case: the sandbox could not apply, so the finding
   stays Blocking and the loop names the failure, never "not run". Any other non-zero status keeps the finding
   Blocking. A refused write is a failed command, so
   a repro that makes one and then exits non-zero stays Blocking; a repro that swallows the refusal and exits 0 still
   moves its finding to Debt.
   A repro that fails at the head also runs at the base: the merge-base of the head and the base ref, never the base tip,
   in a throwaway worktree of it that is removed after the run; never in the checkout or the reviewed tree. A repro runs
   once at the head in a review. The base runs share one budget per review, and each base run is capped at the budget
   left. A repro that exits 0 at the base keeps its finding Blocking, with a note that the repro passes at the base and fails at the head; the fix-round prompt tells the reviewer that such a finding is resolved when the branch added the fix the repro detects, a repro with inverted polarity. A repro that fails at the base
   too keeps its finding Blocking on its first round, marked with the base sha, because a missing behavior also fails at
   the base. When the previous round's answer holds the same repro, its whitespace normalized, in its Blocking findings,
   and the previous head is a strict ancestor of the head, the repro fails at every tree the branch has shown, so no fix can clear it: the finding moves to Debt, marked with the
   base and both heads. A base run that proves nothing (no merge-base, no base tree, a timeout, a repro that cannot run, a
   kill by a signal, a refused sandbox, a spent budget) keeps the finding Blocking and the loop names the reason.
3. **A finding with no repro is Debt.** The parser moves it, so no reader sees a Blocking finding without a repro.
4. **A markdown answer has no repro, so its Blocking findings are Debt.** Each moves to Debt marked "no repro: markdown
   answer", and a CHANGES left with no Blocking finding is an approval by the existing Debt path. A grok reviewer is
   exempt: its CLI has no schema, so every answer it gives is markdown, and the same exemption holds for the Debt path.
5. **A plan review names no repro.** A plan reads no code, so its prompt asks for none, its answer is parsed with no
   demotion, and no repro runs (ADR-0171).
6. **The loop's own rebase-round review has one writer and names its repro.** A kept rebase or landing branch gets one
   CHANGES review that asks a rebase round. `revformat.py --round-review` writes it for `wave.sh` and `branchland.sh`, in
   the shape the landing pass classifies as changes. Its repro is a trial merge of the head with the ref the round
   rebases on, which exits non-zero while the two conflict.
7. **A kept round is bounded whether it pushes or not.** The push back takes the last head of a kept round whose outcome
   is not fail, so an unproven round (ADR-0164) still reaches its work branch, and only that branch; the landing gate and
   the review still decide what reaches the base. A round whose fix run ended with the branch still at the head it started
   from pushed nothing: the landing pass requeues its issues (Todo, rework), names the wave branch that keeps the work for
   salvage, and removes the marker.

## Consequences

- A Blocking finding stands only when the loop reproduces it at the head. A review with no reproducible Blocking finding
  is an approval by the existing Debt path (ADR-0151, item 3).
- A reviewer that omits repros turns every Blocking finding into Debt. The prompt asks for repros; the rate is measured
  before any threshold changes.
- The repro command is shown in the review, after the four fields.

## Guard

`contrib/squad-goal/scripts/test-revformat.sh`: a blank repro demotes its finding to Debt, a set repro renders as a fifth
field, a finding with no repro key is refused, `--repro` keeps a failing repro Blocking and moves a passing or unrunnable
one to Debt with its head; a repro red at the head runs at the base only then, keeps its finding Blocking with a base
note on its first round, moves it to Debt naming the base and both heads when the previous answer holds the same repro,
keeps it Blocking when reworded, and keeps it Blocking with a named reason when the base run proves nothing; and a repro cannot write outside its tree while it can write inside; a sandbox that cannot apply keeps the finding Blocking and names the refusal, inside a child sandbox and outside one; a sandbox executable that cannot start keeps the finding Blocking and names the failure, told apart from a repro whose own command is not found (127). `test-revformat.sh` also proves a repro runs with the checkout's venv first and pytest under `-p no:deepeval`, and
that `--demote-markdown` moves a markdown answer's Blocking findings to Debt with the reason and keeps the shape.
`contrib/squad-goal/scripts/test-review.sh`:
a markdown CHANGES is saved with its Blocking finding in Debt and as an approval, the prompt states the repro's grep
polarity, a review whose Blocking repro passes is saved with the finding in Debt, and a review whose every repro passes is an
approval; the repro runs in the reviewed tree (a `--worktree` review), a throwaway worktree of the head, or not at all when
no tree holds it, and never in the checkout of a `--branch` review; the base repro runs in a throwaway worktree of the
merge-base, in `--worktree` and `--branch` reviews, which is removed after; a fix round whose only Blocking finding fails
at the base and at both heads is an approval; and a head with no merge-base keeps its finding Blocking and names the reason. `test-loopbug-review.sh`:
a plan review with a blank repro stays CHANGES, and its prompt asks for no repro. `test-revformat.sh` also proves the
round review classifies as changes, its repro fails on a conflict and passes once rebased, and no other script writes it.
`test-branchfix-pass.sh`: an unproven kept round's head is pushed, a fail head never is. `test-branchland.sh`: a kept round
that pushed nothing is requeued with rework and its marker removed; a running or undispatched round is left alone.
