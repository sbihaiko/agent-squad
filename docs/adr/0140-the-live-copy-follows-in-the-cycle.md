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
5. **The pass never fails the cycle.** A drift, and a publish that could not land, are reported and the
   pass exits 0: the cycle's other steps keep their beat, and a source tree the publish cannot resolve
   is a defect of that tree, named every cycle until the tree drops it.

## Consequences

- The live copy is at most one cycle behind the repository copy, and the difference is never silent.
- A tree that still ships a name the repository retired is a difference the publish cannot resolve: the
  check reads it as a name to copy and the publish drops it by name, so the reader names it every cycle
  until the tree drops it. The defect is in the tree, not in the two lists.
- The reader owns no second dialect of the publish: what it reads and what it prints are the publish's
  own lines.

## Guard

`contrib/squad-goal/scripts/test-live-guard.sh` (a drifted live copy is published and the difference is
named; after the pass the publish reports no difference; the machine config and a file no one named are
untouched; a difference the publish cannot land is named; a checkout that ships no copy of the skill
says nothing) and `contrib/squad-goal/scripts/test-watch-cycle.sh` (the cycle runs the pass once per
cycle under its own single-flight lock).
