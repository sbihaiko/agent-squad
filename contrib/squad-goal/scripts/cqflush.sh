#!/bin/bash
# usage: cqflush.sh [--once]   Loop: post <SG_STATE_BASE>/<profile>/cq/<owner>__<repo>#<issue>-<tag>.md as issue comments, retrying (the API answers 500 at times).
# A comment carries its own address (#20): the name says the repository and the issue, and the comment is posted there whatever
# directory it sits in -- the queue directory gives isolation, never the write target. The repository is resolved from that
# address through the seam (address.sh), never from the cwd or the profile the flusher started in, so one flusher serves every
# repository. An address no profile knows, or a name that is no address, is set aside as .dead, reported by name, never retried
# and never posted anywhere. A legacy <n>-<tag>.md has no address: it is the repository of the profile whose state directory
# holds it, and a queue under a state directory no profile owns is set aside as .dead, unposted.
# Only files idle for 5 s are read (a writer may still be filling one). A file that fails 5 times
# moves to <name>.dead and is reported once, so a permanently rejected comment cannot retry forever. --once: one pass, no sleep.
here="$(cd "$(dirname "$0")" && pwd)"; . "$here/address.sh"
base=${SG_STATE_BASE:-$HOME/.cache/squad-goal}
while :; do left=0
  for f in "$base"/*/cq/*.md; do [ -e "$f" ] || continue
    [ $(( $(date +%s) - $(stat -f %m "$f") )) -lt 5 ] && { left=1; continue; }
    b=$(basename "$f"); slug=$(basename "$(dirname "$(dirname "$f")")")
    case "$b" in
      *__*"#"*) r=${b#*__}; want="${b%%__*}/${r%%#*}"; n=${r#*#}; n=${n%%-*}
        out=$(repoctx "$want#$n" 2>&1); repo=$(printf '%s\n' "$out" | sed -n 's/^SG_REPO=//p')
        [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $want#$n $b: $(printf '%s' "$out" | head -n 1 | sed 's/^repoctx: //')"; continue; };;
      [0-9]*-*) n=${b%%-*}; repo=$(repoctx "$slug" 2>/dev/null | sed -n 's/^SG_REPO=//p')
        [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $slug/$b: no profile named $slug owns this queue"; continue; };;
      *) mv "$f" "$f.dead"; echo "DEAD $slug/$b: not named <owner>__<repo>#<n>-<tag>.md"; continue;;
    esac
    mv "$f" "$f.posting" 2>/dev/null || continue
    if gh api "repos/$repo/issues/$n/comments" -F body=@"$f.posting" -q .id >/dev/null 2>&1; then echo "posted $repo#$n $b"; mv "$f.posting" "$f.done"
    else mv "$f.posting" "$f";  c=$(( $(cat "$f.tries" 2>/dev/null || echo 0) + 1 )); echo $c > "$f.tries"; left=1
      [ $c -ge 5 ] && { mv "$f" "$f.dead"; rm -f "$f.tries"; echo "DEAD $repo#$n $b after 5 failures"; }; fi
  done; [ "${1:-}" = --once ] && exit 0; [ $left = 0 ] && sleep 60 || sleep 30; done
