#!/bin/bash
# usage: live-guard.sh   Keeps the live skill ($HOME/.claude/skills/squad-goal) in step with the repository copy this
# checkout ships, and names the difference it read. The watch cycle runs it every cycle (single.sh liveguard).
#
# The live copy is what the loop and the owner's info bar run, and it followed the repository by hand: the repository
# dropped the `health:` and `last progress` blocks from the hub footer, and the bar kept printing them for as long as
# nobody published. So the drift is read, never assumed: this pass asks publish-live.sh --check for one `copy`/`drop`
# line per difference, prints what it read, publishes it, and reads again. A difference still there after the publish is
# a fact the cycle cannot leave unread, and it is named as one.
#
# The rule of the publish is untouched: an explicit FILE LIST, never a mirror. Only the names the repository ships cross
# over, the names it retired are dropped by name, and the live copy's machine config (scripts/models.json, the per-repo
# profiles under scripts/profiles/, $SG_STATE) is never one of the names -- so this pass never builds a path of its own
# and never removes a file no one named.
#
# Source: the checkout this profile works in, when it carries the skill ($SG_CHECKOUT/contrib/squad-goal, named by its
# own SKILL.md). A profile whose checkout is another repository ships no copy of this skill and the pass says nothing.
# Destination: publish-live.sh's own default ($SG_LIVE_DIR, else $HOME/.claude/skills/squad-goal).
# Exit 0 always: a drift, and a publish that could not land, are reported, never raised as a failure of the cycle.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
live=${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}
repo=${SG_CHECKOUT:-}/contrib/squad-goal
[ -n "${SG_CHECKOUT:-}" ] && [ -f "$repo/SKILL.md" ] || exit 0                  # this checkout does not ship the skill
[ -d "$live" ] || exit 0                                                       # no live copy on this machine: nothing to keep in step

# The publish reads the main working tree, and nothing moved that tree to origin: it sat at an old commit while later PRs
# merged, and the live loop ran old scripts. The source is stg and only stg (ADR-0145): a work branch lands in dev, the loop
# promotes dev to stg on a green CI, so a defect in dev and not in stg never reaches the running loop. The tree is
# fast-forwarded first (git only, no GitHub call), ff-only, from origin/stg, and only when it is clean and on stg; the publish runs only from a tree at origin/stg. A tree on
# any other branch is not a source: it is named and nothing is published. A dirty tree (a file the publish copies; machine config
# is not dirt), a diverged tree and a failed fetch are named, nothing is published, and the tree is never touched. The base here is stg, never $SG_BASE: that one names where work branches start.
if git -C "$SG_CHECKOUT" rev-parse --git-dir >/dev/null 2>&1; then
  base=stg; tree=$SG_CHECKOUT; cur=$(git -C "$tree" branch --show-current)
  if [ "$cur" != "$base" ]; then echo "LIVE-GUARD main tree $tree not fast-forwarded: it is on ${cur:-a detached head}, not $base; nothing is published"; exit 0
  elif [ -n "$(git -C "$tree" status --porcelain --untracked-files=no -- contrib/squad-goal ':!contrib/squad-goal/scripts/models.json' ':!contrib/squad-goal/scripts/profiles')" ]; then echo "LIVE-GUARD main tree $tree not fast-forwarded: a shipped file is dirty; nothing is published"; exit 0
  elif ! GIT_TERMINAL_PROMPT=0 git -C "$tree" fetch --quiet origin "$base" >/dev/null 2>&1; then echo "LIVE-GUARD main tree $tree not fast-forwarded: git fetch origin $base failed; nothing is published"; exit 0
  else
    from=$(git -C "$tree" rev-parse --short HEAD)
    if git -C "$tree" merge --ff-only --quiet "origin/$base" >/dev/null 2>&1; then
      to=$(git -C "$tree" rev-parse --short HEAD)
      [ "$from" = "$to" ] || echo "LIVE-GUARD main tree $tree fast-forwarded $base $from..$to"
    else echo "LIVE-GUARD main tree $tree not fast-forwarded: $base has diverged from origin/$base (not a fast-forward); nothing is published"; exit 0; fi
  fi
fi

diff=$(bash "$here/publish-live.sh" --check "$repo" "$live")
[ -z "$diff" ] && exit 0                                    # the two copies agree: nothing read, nothing to say
echo "LIVE-GUARD the live skill differs from the repository copy: $(printf '%s' "$diff" | tr '\n' ' ')"
out=$(bash "$here/publish-live.sh" "$repo" "$live" 2>&1); rc=$?
[ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/LIVE-GUARD /'
[ "$rc" = 0 ] || echo "LIVE-GUARD the publish exited $rc"
left=$(bash "$here/publish-live.sh" --check "$repo" "$live")
[ -z "$left" ] && { echo "LIVE-GUARD the live skill now matches the repository copy"; exit 0; }
echo "LIVE-GUARD the publish could not resolve: $(printf '%s' "$left" | tr '\n' ' ')"
exit 0
