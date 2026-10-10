#!/bin/bash
# Loop: post $SG_STATE/cq/<issue>-<tag>.md as issue comments, retrying (the API answers 500 at times).
# Only files idle for 5 s are read (a writer may still be filling one). A file that fails 5 times
# moves to <name>.dead and is reported once, so a permanently rejected comment cannot retry forever.
. "$(dirname "$0")/env.sh"
while :; do left=0
  for f in "$SG_STATE"/cq/*.md; do [ -e "$f" ] || continue
    [ $(( $(date +%s) - $(stat -f %m "$f") )) -lt 5 ] && { left=1; continue; }
    n=$(basename "$f" | cut -d- -f1)
    mv "$f" "$f.posting" 2>/dev/null || continue
    if gh api repos/$SG_REPO/issues/$n/comments -F body=@"$f.posting" -q .id >/dev/null 2>&1; then echo "posted $(basename "$f")"; mv "$f.posting" "$f.done"
    else mv "$f.posting" "$f";  c=$(( $(cat "$f.tries" 2>/dev/null || echo 0) + 1 )); echo $c > "$f.tries"; left=1
      [ $c -ge 5 ] && { mv "$f" "$f.dead"; rm -f "$f.tries"; echo "DEAD $(basename "$f") after 5 failures"; }; fi
  done; [ $left = 0 ] && sleep 60 || sleep 30; done
