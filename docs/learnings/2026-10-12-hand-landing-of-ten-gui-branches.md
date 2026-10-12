# Learning: landing ten `gui/*` branches by hand (2026-10-12)

The loop could not land them, so they were merged into the fork's `main` by hand. This
is the record of what that cost and what to do differently; `agent-squad#9` carries the
proposals that follow from it, each with an acceptance criterion.

## The numbers

| | |
|---|---|
| branches landed | 10 — `gui/1872 1879 1881 1882 1883 1888 1889 1891 1892 1893` |
| wall-clock to merge all ten | 12:40 → 12:49 — **9 minutes** |
| wall-clock to make the merged tree green | 13:06 → 13:42 — **53 minutes**, plus the verification runs |
| conflict hunks resolved by hand | **18, in 9 files**, across 5 of the 10 merges |
| files the repair touched | **13 (+148 / −87)** |
| commits the branches were behind `main` | 1 to **122** |

Not one branch was rejected for its content. Every branch was good; every failure came
from the act of merging it. **Merging is cheap; making the merge correct is the cost.**

## Distance is the cost

| branch | `main` was ahead | branch was ahead | files touched on both sides |
|---|---|---|---|
| gui/1872 | 11 | 3 | 0 |
| gui/1882 | 1 | 9 | 0 |
| gui/1883 | 15 | 4 | 0 |
| gui/1891 | 23 | 1 | 2 |
| gui/1892 | 25 | 1 | 1 |
| gui/1879 | 26 | 1 | 1 |
| gui/1893 | 26 | 2 | 1 |
| gui/1881 | **87** | 2 | 4 |
| gui/1888 | **119** | 2 | 2 |
| gui/1889 | **122** | 1 | 4 |

The three deepest produced most of the 18 hunks and **all four** post-merge failure
classes below. The seven at ≤26 behind merged clean and stayed green.

### Rebase beats merging a stale tip, measured

`git rebase --onto <base> <merge-base> <tip>` over the same three commits and the same
three bases:

```
gui/1881 → 1 conflict (SKILL.md)     merged: 8 hunks
gui/1888 → 0 conflicts               merged: 1 hunk
gui/1889 → 0 conflicts               merged: 6 hunks
```

**15 hunks merged; 1 rebased.** A rebase replays the branch's own small commits and
three-way-applies each; a merge compares two trees that diverged 122 commits ago and
conflicts on adjacency. Two costs to handle before adopting it: the rebase rewrites the
branch's commits (the fork branch needs a force-push), and it erases the `Merge <branch>`
subject that `culprit()` falls back to when naming a red base.

## The four ways a merged tree goes red and nothing notices at merge time

1. **A hand resolution silently drops a line, and `bash -n` is happy.** Resolving
   `gui/1893` in `watch.sh` deleted the merged line
   `echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT; trap ... INT TERM`. The script
   still parses. The symptom arrived later: `watch.sh already running pid=` with an empty
   pid, a lock never released, two suites red. **A syntax check cannot see a deleted
   statement, and nothing else looked.**
2. **A reverted merge orphans the spec it brought, and the base stays red.** Reverting
   `gui/1864` and `gui/1867` independently left `test-land-two-repos.sh` in the tree — the
   spec of a feature that no longer existed — so `main` was red with no branch able to fix
   it, and leaving needed the logged `SG_FORK_BASERED_OVERRIDE` grant. Reverting one of two
   interdependent merges is not a revert; it mints a new bug. All three reverts were a bare
   `git revert -m 1` message, so a later reader cannot tell "reverted because red" from
   "reverted because wrong".
3. **A test written against an older epoch of a shared decision.** `gate-suites.sh`'s
   unknown-path class moved `full` → `offline` (#1875) and the python class changed meaning
   (#1881); branches and fixtures written before those moves still assert the old answer.
   Each side was green on its own. The merge is the first tree where each side's tests meet
   the other side's changes.
4. **A fixture missing a stub for a rule that just became universal.** Two suites' fixtures
   had no `shell-suites.sh`, and one stub `env.sh` had no `SG_REPOS`. A rule that arrives
   with a merge invalidates every fixture that did not anticipate it.

None of the four is visible from a branch's own gate, because **the branch's gate never
runs on the merge.**

## Why the tooling cannot see any of it

- `bdiff()` in `forkclose.sh` is `git diff --name-only $(git merge-base $1 $2) $2`: it
  classifies the branch against **its own divergence point**, then `gate-suites.sh` turns
  that stale picture into the suite set that gates the landing.
- Nothing gates the merged tree before it becomes `main`; the conflict resolution that
  produced the landing is not classified at all.
- `git revert -m 1 --no-edit "$m"` reverts a merge and nothing else, so the spec the merge
  brought survives its own feature.
- A red batch discards its own information. The pass printed
  `FORKCLOSE BATCH RED (...) nothing landed, falling back to one branch at a time so the
  culprit is named` and named nothing. What actually failed, in
  `~/.cache/squad-goal/tests/test-1872-20261012-123039.log`, was **five `ruff` UP031 errors
  in one file of one branch (`gui/1879`)** — enough to serialize six branches against
  `max_parallel=10`.

## Checklist for the next hand landing

1. **Rebase each candidate onto the current base first**, then run its gate on the rebased
   tree. Do not merge a stale tip; if the batch must be merged anyway, expect the hunks.
2. **Resolve by union, and audit the deletions.** After every resolution compare the result
   against both parents (`git show :2:<file>`, `:3:<file>`); a line present in neither is a
   fabrication. `bash -n` proves nothing about a deleted statement.
3. **Gate the union of both sides' suite sets.** The resolution can touch a file the
   branch's own diff never named, and both sides' tests now meet in one tree.
4. **When a suite goes red, ask whether its feature is present before debugging the test**:
   `git log -1 --format='%s' -- <test file>`, then check whether that commit's change is
   still in the tree. An orphaned spec looks exactly like a broken one.
5. **A revert of a merge takes the spec with it**, and its message names why.
6. **Clean the branch worktrees only after the verdict**; and remember `wtclean.sh` culls
   loose worktrees under `~`.
7. **Measure before blaming the tests**: `git merge-base`, commits on each side, files
   touched on both sides. Distance predicts the cost better than the diff does.

## Reproduce

```bash
cd ~/VSCodeProjects/agent-squad
git log --merges --format='%h %ad %s' --date=format:'%H:%M' 87e8010..816bd74   # the landing
git diff --stat 0340aeb 816bd74                                                # what the repair touched
git rebase --onto dfbbb9d "$(git merge-base dfbbb9d <gui/1881 tip>)" <gui/1881 tip>
git rebase --onto a7fcfee "$(git merge-base a7fcfee <gui/1888 tip>)" <gui/1888 tip>
git rebase --onto a7fcfee "$(git merge-base a7fcfee <gui/1889 tip>)" <gui/1889 tip>
tail -30 ~/.cache/squad-goal/tests/test-1872-20261012-123039.log               # the red batch
```

## See also

- `agent-squad#9` — the proposals that follow (rebase before gate; batch attribution; a
  resolution audit; a revert that takes its spec; `bdiff()` answering about the landing).
- `agent-squad#3`–`#8` — the GitHub access interface, whose landing pass is the loop's
  heaviest reader and the one that goes quiet when the quota is gone.
- `MesenAI#1885` — one contested ticket must not refuse a whole frontier batch: the same
  principle, at dispatch and at landing.
