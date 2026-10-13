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
