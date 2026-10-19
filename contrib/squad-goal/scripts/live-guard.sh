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
#
# The cycle runs this pass every cycle, and the publish check walks both trees (~2 s here). So the pass keeps a key of
# its input in $SG_STATE and returns at once when the key is unchanged (#568): no check, no publish, no write. The key
# is the tree's HEAD and status, origin/stg after the fetch, and the live copy's own state -- a hand edit of the live
# copy alone still runs the pass. The fetch stays above the skip: only the publish check after it is skipped. The key
# is kept after a pass that resolved every difference, never after one the publish could not land, so a drift the
# publish cannot resolve is named every cycle (ADR-0140). A checkout with no git has no cheap key and is read as before.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
live=${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}
repo=${SG_CHECKOUT:-}/contrib/squad-goal
[ -n "${SG_CHECKOUT:-}" ] && [ -f "$repo/SKILL.md" ] || exit 0                  # this checkout does not ship the skill
[ -d "$live" ] || exit 0                                                       # no live copy on this machine: nothing to keep in step
# The review chain is machine config the publish never writes, and the hub panel edits the live one. Read both lists and
# name a difference every cycle; a missing file or a missing jq says nothing (#542).
rc_live=$(jq -c '.review_models' "$live/scripts/models.json" 2>/dev/null); rc_repo=$(jq -c '.review_models' "$repo/scripts/models.json" 2>/dev/null)
[ -n "$rc_live" ] && [ -n "$rc_repo" ] && [ "$rc_live" != "$rc_repo" ] && echo "LIVE-GUARD the live review chain differs from the repository: live $rc_live, repository $rc_repo"
digest() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }
mode_of() { if stat -f '%Lp' / >/dev/null 2>&1; then xargs -0 stat -f '%Lp %N'; else xargs -0 stat -c '%a %n'; fi; }
live_state() { # every file the live copy holds, by name, mode and content: a hand edit there is an input too (ADR-0140)
  local h; if command -v sha256sum >/dev/null 2>&1; then h=sha256sum; else h="shasum -a 256"; fi
  { ( cd "$live" && find . -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | mode_of ) 2>/dev/null
    ( cd "$live" && find . -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 $h ) 2>/dev/null
  } | digest; }
key_now() { { git -C "$tree" rev-parse HEAD; git -C "$tree" status --porcelain --untracked-files=no
  git -C "$tree" rev-parse "origin/$base"; live_state; } 2>/dev/null | digest; }

# The publish reads the main working tree, and nothing moved that tree to origin: it sat at an old commit while later PRs
# merged, and the live loop ran old scripts. The source is stg and only stg (ADR-0145): a work branch lands in dev, and the
# owner promotes a proven dev tip to stg by hand (promote-pass.sh, #576), so a defect in dev and not in stg never reaches the
# running loop. The tree is
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

kf=$SG_STATE/live-guard.key; key=
if git -C "$SG_CHECKOUT" rev-parse --git-dir >/dev/null 2>&1; then
  key=$(key_now)
  [ -n "$key" ] && [ "$(cat "$kf" 2>/dev/null)" = "$key" ] && exit 0    # nothing the pass reads changed: no check, no publish, no write
fi

diff=$(bash "$here/publish-live.sh" --check "$repo" "$live")
if [ -n "$diff" ]; then
  echo "LIVE-GUARD the live skill differs from the repository copy: $(printf '%s' "$diff" | tr '\n' ' ')"
  out=$(bash "$here/publish-live.sh" "$repo" "$live" 2>&1); rc=$?
  [ -n "${SG_STATE:-}" ] && mkdir -p "$SG_STATE" && touch "$SG_STATE/live-publish.stamp"   # the janitor's rule M reads this change as the publish's own (#659)
  [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/LIVE-GUARD /'
  [ "$rc" = 0 ] || echo "LIVE-GUARD the publish exited $rc"
  left=$(bash "$here/publish-live.sh" --check "$repo" "$live")
  if [ -n "$left" ]; then
    echo "LIVE-GUARD the publish could not resolve: $(printf '%s' "$left" | tr '\n' ' ')"   # named every cycle: the key is not kept
    exit 0
  fi
  echo "LIVE-GUARD the live skill now matches the repository copy"
fi
if [ -n "$key" ] && [ -n "${SG_STATE:-}" ]; then                      # keep the key the next cycle reads
  mkdir -p "$SG_STATE"; next=$(key_now); [ -n "$next" ] && { printf '%s\n' "$next" > "$kf.$$" && mv "$kf.$$" "$kf"; }
fi
exit 0
