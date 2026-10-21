# ADR-0140: The live copy follows the repository copy in the cycle, and the difference is read

## Status

Accepted.

## Context

The squad-goal loop and the owner's info bar run the live skill under `~/.claude/skills/squad-goal`, not
the `contrib/squad-goal` tree the repository ships. `publish-live.sh` copies the names the repository
ships into that skill as an explicit file list and drops the names the repository retired, so the live
copy holds its own machine config (`scripts/models.json`, the per-repo profiles under `scripts/profiles/`,
the loop's own state directory).

Nothing in the cycle ran it. The live copy followed the repository by hand, so the repository could move
under the running copy with no line to say so: the repository dropped a block from the hub footer on
purpose, and the bar the owner reads kept printing it. The publish itself was in order; what it had no
reader for was its own drift.

## Decision

1. **The cycle reads the difference.** `live-guard.sh` asks `publish-live.sh --check` for one line per
   difference between the repository copy and the live copy, publishes it, and reads again. The watch
   cycle runs it every cycle under `single.sh liveguard`, beside the pass that re-vendors the personal
   skills.
2. **A pass that publishes prints the difference it read.** The one line per `copy`/`drop` is named in
   the cycle's stream, and a difference the publish left behind is named as a fact the cycle cannot
   leave unread. A publish that reports nothing is never read as evidence that the two copies agree.
3. **The publish stays an explicit file list, never a mirror.** The reader adds no list of its own and
   builds no path: the names the repository ships cross over, the names it retired are dropped by name,
   a file no one named is never touched, and the live copy's machine config is never one of the names.
4. **The source is the checkout the profile works in.** `live-guard.sh` reads
   `$SG_CHECKOUT/contrib/squad-goal` when that tree carries the skill (its own `SKILL.md`). A profile
   whose checkout is another repository ships no copy of this skill, and the pass says nothing. The
   destination is the publish's own default (`$SG_LIVE_DIR`, else `~/.claude/skills/squad-goal`).
5. **A publish records the tested sha.** The tested sha is the source HEAD the publish copies from.
   The publish writes `PUBLISHED` (`<sha> <utc>`) at the top of the live skill, atomically, and only when
   every copy landed. `--check` names `publish <sha>` while the file is missing or names another sha.
   The pass prints `LIVE-GUARD published <sha>` after a publish.
6. **A restart is the owner's.** `watchd.sh start` records the sha the drivers start on in
   `$SG_STATE/driver.sha`. A later publish to another sha is named once per publish as a pending restart.
   The pass never starts or stops a process.
7. **Live `models.json` is owner config, read and never compared.** The pass does not compare it with
   the repository copy each cycle. It names a change of its digest once as a hand edit, then stays silent
   until the digest changes. A stray `.bak-*` copy in the live `scripts/` is listed once as clutter and
   is never removed.
8. **The pass never fails the cycle.** A drift, and a publish that could not land, are reported and the
   pass exits 0: the cycle's other steps keep their beat, and a source tree the publish cannot resolve
   is a defect of that tree, named every cycle until the tree drops it.
9. **A publish closes the issues its commits name.** The `loop-bug` stop reads the issue state and
   nothing else (ADR-0149), so the step that makes a fix live is the step that closes it. A commit the
   publish makes live that carries a `Closes #n` trailer closes `#n`, with one comment naming the sha
   and the published time, and the stop lifts on the next pass. The commits are the previously published
   sha (exclusive) to the source HEAD; the HEAD alone when `PUBLISHED` is missing or names a commit the
   source does not hold. A `TDD RED` commit names its issue and carries no closing trailer, so it closes
   nothing (ADR-0136). `--check` closes nothing, and a close that fails is named, never failing the
   publish. The ledger keeps its local record; the write to GitHub is the issue close alone, and no
   other pass writes that fact.

## Consequences

- The live copy is at most one cycle behind the repository copy, and the difference is never silent.
- A tree that still ships a name the repository retired is a difference the publish cannot resolve: the
  check reads it as a name to copy and the publish drops it by name, so the reader names it every cycle
  until the tree drops it. The defect is in the tree, not in the two lists.
- The live copy names the sha it holds, and the drivers name the sha they run, so a gap between them is
  one line, not a guess.
- A `loop-bug` stop that a landed fix closed stays lifted: the issue closes when the fix becomes live,
  not when it lands, and no owner hand closes it in between.
- A note that does not change is not printed again, so two cycles with no change print nothing.
- The reader owns no second dialect of the publish: what it reads and what it prints are the publish's
  own lines.

## Guard

`contrib/squad-goal/scripts/test-live-guard.sh` (a drifted live copy is published and the difference is
named; after the pass the publish reports no difference; the machine config and a file no one named are
untouched; a difference the publish cannot land is named; a checkout that ships no copy of the skill
says nothing; a publish names its sha and a pending restart once; a hand edit to live `models.json` and
live clutter are named once; a publish closes the issue its `Closes #n` fix names, once, and a `TDD RED`
commit closes nothing), `contrib/squad-goal/scripts/test-publish-live.sh` (`PUBLISHED` and its
`--check` line; a linked worktree writes none; the close names the sha and the published time, closes
each issue once, and `--check` closes nothing), `contrib/squad-goal/scripts/test-watchd-driver-sha.sh`
(a start records `driver.sha`) and `contrib/squad-goal/scripts/test-watch-cycle.sh` (the cycle runs the pass once per
cycle under its own single-flight lock).
