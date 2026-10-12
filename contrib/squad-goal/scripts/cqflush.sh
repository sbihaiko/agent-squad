#!/bin/bash
# usage: cqflush.sh [--once]   Loop: post <SG_STATE_BASE>/<profile>/cq/<issue>-<tag>.md as issue comments, retrying (the API answers 500 at times).
# A comment's address is where it sits: the profile whose state directory holds it, and the issue number its name starts
# with. The repository comes from that address through the seam (address.sh), never from the cwd the flusher started in, so one
# flusher serves every repository (#20). A queue under a state directory no profile owns is set aside as .dead, unposted.
# Only files idle for 5 s are read (a writer may still be filling one). A file that fails 5 times
# moves to <name>.dead and is reported once, so a permanently rejected comment cannot retry forever. --once: one pass, no sleep.
here="$(cd "$(dirname "$0")" && pwd)"; . "$here/address.sh"
base=${SG_STATE_BASE:-$HOME/.cache/squad-goal}
while :; do left=0
  for f in "$base"/*/cq/*.md; do [ -e "$f" ] || continue
    [ $(( $(date +%s) - $(stat -f %m "$f") )) -lt 5 ] && { left=1; continue; }
    n=$(basename "$f" | cut -d- -f1); slug=$(basename "$(dirname "$(dirname "$f")")")
    repo=$(repoctx "$slug" 2>/dev/null | sed -n 's/^SG_REPO=//p')
    [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $slug/$(basename "$f"): no profile named $slug owns this queue"; continue; }
    mv "$f" "$f.posting" 2>/dev/null || continue
    if gh api "repos/$repo/issues/$n/comments" -F body=@"$f.posting" -q .id >/dev/null 2>&1; then echo "posted $repo#$n $(basename "$f")"; mv "$f.posting" "$f.done"
    else mv "$f.posting" "$f";  c=$(( $(cat "$f.tries" 2>/dev/null || echo 0) + 1 )); echo $c > "$f.tries"; left=1
      [ $c -ge 5 ] && { mv "$f" "$f.dead"; rm -f "$f.tries"; echo "DEAD $repo#$n $(basename "$f") after 5 failures"; }; fi
  done; [ "${1:-}" = --once ] && exit 0; [ $left = 0 ] && sleep 60 || sleep 30; done
