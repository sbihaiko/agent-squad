#!/bin/bash
# usage: cqflush.sh [--once]   Loop: post <SG_STATE_BASE>/<profile>/cq/<issue>-<tag>.md as issue comments, retrying (the API answers 500 at times).
# The comment's address is its carrier (#20): <owner>__<repo>#<n>-<tag>.md names the repository and the issue, and the comment
# is posted there whatever directory it sits in. The repository comes from the address through repoctx.sh, never from the cwd the
# flusher started in, so one flusher serves every repository. An address no profile knows is set aside as .dead, reported by
# name, and never retried against another repository, the flusher's own included. A legacy <n>-<tag>.md is addressed by the
# directory it sits in: the repository of the profile whose state directory holds it. A queue under a state directory no
# profile owns is set aside as .dead, unposted.
# Only files idle for 5 s are read (a writer may still be filling one). A file that fails 5 times
# moves to <name>.dead and is reported once, so a permanently rejected comment cannot retry forever. --once: one pass, no sleep.
here="$(cd "$(dirname "$0")" && pwd)"; . "$here/repoctx.sh"
base=$(sg_state_base)
# Every queue to read: each profile's own state directory (its SG_STATE override counts), then any other <base>/<name>/cq.
qdirs() { local n st; for n in $(sg_profiles); do st=$(repoctx "$n" 2>/dev/null | sed -n 's/^SG_STATE=//p'); [ -n "$st" ] && echo "$st"/cq/*.md; done
  for st in "$base"/*/cq; do echo "$st"/*.md; done; }
while :; do left=0
  for f in $(qdirs | awk '!s[$0]++'); do [ -e "$f" ] || continue
    [ $(( $(date +%s) - $(stat -f %m "$f") )) -lt 5 ] && { left=1; continue; }
    b=$(basename "$f"); slug=$(basename "$(dirname "$(dirname "$f")")")
    for n in $(sg_profiles); do [ "$(repoctx "$n" 2>/dev/null | sed -n 's/^SG_STATE=//p')" = "$(dirname "$(dirname "$f")")" ] && slug=$n; done
    case "$b" in
      *__*"#"[0-9]*-*) r=${b#*__}; want="${b%%__*}/${r%%#*}"; n=${r#*#}; n=${n%%-*}
        repo=$(repoctx "$want#$n" 2>/dev/null | sed -n 's/^SG_REPO=//p')
        [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $want#$n $b: no profile names the repository $want"; continue; };;
      *) n=${b%%-*}; repo=$(repoctx "$slug" 2>/dev/null | sed -n 's/^SG_REPO=//p')
        [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $slug/$b: no profile named $slug owns this queue"; continue; };;
    esac
    mv "$f" "$f.posting" 2>/dev/null || continue
    if gh api "repos/$repo/issues/$n/comments" -F body=@"$f.posting" -q .id >/dev/null 2>&1; then echo "posted $repo#$n $b"; mv "$f.posting" "$f.done"
    else mv "$f.posting" "$f";  c=$(( $(cat "$f.tries" 2>/dev/null || echo 0) + 1 )); echo $c > "$f.tries"; left=1
      [ $c -ge 5 ] && { mv "$f" "$f.dead"; rm -f "$f.tries"; echo "DEAD $repo#$n $b after 5 failures"; }; fi
  done; [ "${1:-}" = --once ] && exit 0; [ $left = 0 ] && sleep 60 || sleep 30; done
