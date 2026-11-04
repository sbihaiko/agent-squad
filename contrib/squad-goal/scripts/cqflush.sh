#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: cqflush.sh [--once]   Loop: post each repository's queued comments as issue comments, retrying (the API answers 500 at times).
# A comment carries its own address (#20): the name says the repository and the issue, and the comment is posted there whatever
# directory it sits in -- the queue directory gives isolation, never the write target. The repository is resolved from that
# address through the seam (address.sh), never from the cwd or the profile the flusher started in, so one flusher serves every
# repository. An address no profile knows, or a name that is no address, is set aside as .dead, reported by name, never retried
# and never posted anywhere. A legacy <n>-<tag>.md has no address: it is the repository of the profile whose state directory
# holds it, and a queue under a state directory no profile owns is set aside as .dead, unposted.
# Only files idle for 5 s are read (a writer may still be filling one). A file that fails 5 times
# moves to <name>.dead and is reported once, so a permanently rejected comment cannot retry forever. --once: one pass, no sleep.
# A comment named <n>-status~<tag>.md or <n>-delivery~<tag>.md is the one loop comment of that kind on its issue (#328): the body gets a hidden
# marker on its first line, the comment that holds the marker is edited (PATCH) when its body differs, nothing is written when it is the
# same, and a comment is posted only when none holds the marker. Any other name is posted as a new comment. Sets $1.did to what it did.
# A comment named <n>-unstart~<tag>.md is a removal, never a post (#673, issue_ledger.py rearm; #1297): every comment of the issue
# whose body starts SQUAD-NOT-STARTABLE: is deleted, and so is the frozen:not-startable label the marker wrote. The label goes first:
# a refused removal leaves the comments in place and the file queued for a retry (#1297). A 404 on the label is an issue that never
# carried it. No other comment goes. The body of the queued file is not read.
# A comment named <n>-terminal-closed-<pid>.md is the janitor's Closed comment (#777): the issue state is read again first. An issue
# open again drops it (.dropped, a DROPPED line); an unread state keeps it queued for a retry.
cq_unstart() { local repo=$1 n=$2 file=$3 pg page ids=() id k=0 lab=frozen:not-startable lo lrc lw=absent
  # The label goes first (#1297): a label the API refuses (403, a secondary limit) returns here with every comment still in
  # place, so the marker, the evidence, stays and the file is queued again for a retry. A 404 is an issue that never carried
  # the label, the same outcome, not a failure. Removed first, a label alone holds nothing once its marker is gone.
  lo=$(gh_send --method DELETE "repos/$repo/issues/$n/labels/$lab" 2>&1); lrc=$?
  case "$lrc" in
    0) lw=removed;;
    2) case "$lo" in *"GitHub refused: Not Found"*|*"GitHub refused: Label does not exist"*) ;; *) return 1;; esac;;
    *) return 1;;
  esac
  for pg in 1 2 3 4 5 6 7 8 9 10; do
    page=$(gh_api "repos/$repo/issues/$n/comments?per_page=100&page=$pg") || return 1
    while read -r id; do [ -n "$id" ] && ids+=("$id"); done < <(printf '%s' "$page" | jq -r '.[]|select((.body//"")|startswith("SQUAD-NOT-STARTABLE:"))|.id' 2>/dev/null)
    [ "$(printf '%s' "$page" | jq 'length' 2>/dev/null)" = 100 ] || break
  done
  for id in ${ids[@]+"${ids[@]}"}; do gh_send --method DELETE "repos/$repo/issues/comments/$id" >/dev/null 2>&1 || return 1; k=$((k + 1)); done
  # The claim files health.sh wrote with the marker are its memory that the marker was posted (#1301): the marker is gone now,
  # so the claims of this issue go too, and a later failure with the same reason is marked again. The claims live in the
  # auto/ of the state directory that owns the queue file, so one flusher serves every profile: the file the loop hands in
  # always sits at <state>/cq/<name>.posting, and a file at any other depth is a layout defect, refused by name, never rm'd
  # at a path the dirname stack guessed. Only a removal that reached this line clears them: a refused label or comment
  # returned above, the marker is still in place, and its memory stays. Another issue's claims and the health alert's own
  # files are no match for this issue's prefix.
  local qdir auto
  qdir=$(dirname "$file"); [ "$(basename "$qdir")" = cq ] || return 1
  auto=$(dirname "$qdir")/auto
  [ -d "$auto" ] && rm -f "$auto/notstartable-$n-"*
  echo "removed $k comment(s), label $lab $lw" > "$file.did"; }
cq_send() { local repo=$1 n=$2 b=$3 file=$4 t mk cid
  t=${b#*#}; t=${t#*-}; case "$t" in status~*) mk=status;; delivery~*) mk=delivery;; unstart~*) cq_unstart "$repo" "$n" "$file"; return;; esac
  if [ -z "${mk:-}" ]; then gh_send "repos/$repo/issues/$n/comments" -F body=@"$file" >/dev/null 2>&1 && echo posted > "$file.did"; return; fi
  mk="<!-- sg-loop:$mk -->"; { echo "$mk"; cat "$file"; } > "$file.marked"
  for pg in 1 2 3 4 5 6 7 8 9 10; do   # the comments of the issue, a page at a time, until the marker is found or the list ends
    page=$(gh_api "repos/$repo/issues/$n/comments?per_page=100&page=$pg") || return 1
    cid=$(printf '%s' "$page" | jq -r --arg m "$mk" '[.[]|select((.body//"")|contains($m))][0].id // empty' 2>/dev/null)
    [ -n "$cid" ] || [ "$(printf '%s' "$page" | jq 'length' 2>/dev/null)" = 100 ] || break; [ -z "$cid" ] || break
  done
  if [ -z "$cid" ]; then gh_send "repos/$repo/issues/$n/comments" -F body=@"$file.marked" >/dev/null 2>&1 && echo posted > "$file.did"; rc=$?
  elif [ "$(gh_api "repos/$repo/issues/comments/$cid" -q .body 2>/dev/null)" = "$(cat "$file.marked")" ]; then echo "unchanged" > "$file.did"; rc=0
  else gh_send --method PATCH "repos/$repo/issues/comments/$cid" -F body=@"$file.marked" >/dev/null 2>&1 && echo edited > "$file.did"; rc=$?; fi
  rm -f "$file.marked"; return $rc; }
here="$(cd "$(dirname "$0")" && pwd)"; . "$here/address.sh"
export SG_GH_PASS=cqflush; . "$here/ghcall.sh"   # every comment read and write goes through the door (ADR-0277)
base=$(sg_state_base)
# Seconds between two passes, empty queue or not: SG_FLUSH_INTERVAL from the environment, else the profile in force, else 120. --interval prints it and exits (tests).
iv=${SG_FLUSH_INTERVAL:-$(sg_profile_setting "${SG_PROFILE:-}" SG_FLUSH_INTERVAL 120)}; [ "${1:-}" = --interval ] && { echo "$iv"; exit 0; }
stop=; sp=; trap 'stop=1; kill "$sp" 2>/dev/null' TERM   # a reload (#873) ends the flusher after the post in flight, never inside it, and wakes an idle wait
orphaned() { [ "$(/bin/ps -o ppid= -p $$ | tr -d ' ')" = 1 ]; }   # the supervisor is gone (#959): the flusher posts nothing more
[ "${1:-}" = --once ] || echo $$ > "$SG_STATE/cqflush.pid"   # the lane's pid under its own state directory, which watchd reads to name an orphan (#1313)
while :; do left=0; { [ -n "$stop" ] || orphaned; } && exit 0
  for f in "$base"/*/cq/*.md; do [ -e "$f" ] || continue; { [ -n "$stop" ] || orphaned; } && exit 0
    [ $(( $(date +%s) - $(mtime "$f") )) -lt 5 ] && { left=1; continue; }
    b=$(basename "$f"); slug=$(basename "$(dirname "$(dirname "$f")")")
    case "$b" in
      *__*"#"*) r=${b#*__}; want="${b%%__*}/${r%%#*}"; n=${r#*#}; n=${n%%-*}
        out=$(repoctx "$want#$n" 2>&1); repo=$(printf '%s\n' "$out" | sed -n 's/^SG_REPO=//p')
        [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $want#$n $b: $(printf '%s' "$out" | head -n 1 | sed 's/^repoctx: //')"; continue; };;
      [0-9]*-*) n=${b%%-*}; repo=$(repoctx "$slug" 2>/dev/null | sed -n 's/^SG_REPO=//p')
        [ -n "$repo" ] || { mv "$f" "$f.dead"; echo "DEAD $slug/$b: no profile named $slug owns this queue"; continue; };;
      *) mv "$f" "$f.dead"; echo "DEAD $slug/$b: not named <owner>__<repo>#<n>-<tag>.md"; continue;;
    esac
    case "$b" in *-terminal-closed-*)
      is=$(gh_api "repos/$repo/issues/$n" -q .state 2>/dev/null) || { left=1; echo "HOLD $repo#$n $b: the issue state could not be read"; continue; }
      [ "$is" = open ] && { mv "$f" "$f.dropped"; echo "DROPPED $repo#$n $b: the issue is open again; no Closed comment is posted"; continue; };;
    esac
    mv "$f" "$f.posting" 2>/dev/null || continue
    if cq_send "$repo" "$n" "$b" "$f.posting"; then echo "$(cat "$f.posting.did") $repo#$n $b"; rm -f "$f.posting.did"; mv "$f.posting" "$f.done"
    else mv "$f.posting" "$f";  c=$(( $(cat "$f.tries" 2>/dev/null || echo 0) + 1 )); echo $c > "$f.tries"; left=1
      [ $c -ge 5 ] && { mv "$f" "$f.dead"; rm -f "$f.tries"; echo "DEAD $repo#$n $b after 5 failures"; }; fi
  done; [ "${1:-}" = --once ] && exit 0; sleep "$iv" & sp=$!; wait "$sp"; done
