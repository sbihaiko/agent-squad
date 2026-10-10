#!/bin/bash
# usage: health.sh   (also run by launchd every 2 min, outside watchd: nothing inside the loop can notice the loop itself dying)
# One health pass -> $SG_STATE/health.json. Causes: watchd-down, fastloop-down, watch-stalled (cycle.log silent 6 min), never-started-runs,
# orphan-markers (queued-/runof- whose run never recorded), stuck-prs (janitor-stuck- markers of still-open PRs), loose-worktrees.
# Each cause alerts ONCE (alerts.log + macOS notification) when it appears and re-arms when it clears. HEALTH_DRY=1 prints only; HEALTH_NOTIFY overrides the notifier.
. "$(dirname "$0")/env.sh"; A="$SG_STATE/auto"; mkdir -p "$A"; G=/usr/bin/git; now=$(date +%s)
alive() { [ -s "$1" ] && /bin/ps -p "$(cat "$1")" -o command= 2>/dev/null | grep -q "$2"; }
causes=(); detail=()
add() { causes+=("$1"); detail+=("$2"); }
alive "$SG_STATE/watchd.pid" 'watchd.sh run' || add watchd-down "watchd is not running: no merges, dispatch or cleanup until it is restarted (watchd.sh start)"
alive "$SG_STATE/fastloop.pid" 'fastloop.sh' || add fastloop-down "fastloop (autopilot every 30 s) is not running"
c="$SG_STATE/cycle.log"; [ -e "$c" ] && [ $((now - $(stat -f %m "$c"))) -gt 360 ] && alive "$SG_STATE/watchd.pid" 'watchd.sh run' && add watch-stalled "cycle.log silent for $(( (now - $(stat -f %m "$c")) / 60 )) min while watchd is up"
nv=0; for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do f=$r; [ "${r#/}" = "$r" ] && f="$SG_CHECKOUT/runs/$r"
  [ -e "$f/record.jsonl" ] || { [ -e "$f/request.txt" ] && [ $((now - $(stat -f %m "$f/request.txt"))) -gt 300 ] && nv=$((nv+1)); }; done
[ $nv -gt 0 ] && add never-started-runs "$nv run(s) with no record.jsonl after 5 min (the squad refused to start)"
no=0; for f in "$A"/runof-*; do [ -e "$f" ] || continue; k=${f##*/runof-}; [ -e "$A/queued-$k" ] || continue; d="$SG_CHECKOUT/runs/$(cat "$f")"
  [ -e "$d/record.jsonl" ] || [ ! -d "$d" ] || { [ $((now - $(stat -f %m "$d/request.txt" 2>/dev/null || echo $now))) -gt 600 ] && no=$((no+1)); }; done
[ $no -gt 0 ] && add orphan-markers "$no queued-* marker(s) whose fix run never started"
ns=0; open=" $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[].number' 2>/dev/null | tr '\n' ' ')"
for f in "$A"/janitor-stuck-*; do [ -e "$f" ] || continue; k=${f##*/janitor-stuck-}; case "$open" in *" ${k%%-*} "*) ns=$((ns+1));; esac; done
[ $ns -gt 0 ] && add stuck-prs "$ns open PR(s) flagged PR-STUCK (conflict or red CI with no live fix run)"
nl=$($G -C "$SG_CHECKOUT" worktree list --porcelain 2>/dev/null | awk '/^worktree /{w=$2} /^detached/{print w}' | grep -cE "$HOME/wt-|/acpr\.")
[ "$nl" -gt 0 ] && add loose-worktrees "$nl loose detached worktree(s) (wtclean keeps the dirty or unpushed ones; look at WT LOOSE)"
{ printf '{"ts":%s,"ok":%s,"causes":[' "$now" "$([ ${#causes[@]} = 0 ] && echo true || echo false)"; for i in "${!causes[@]}"; do [ $i = 0 ] || printf ','; printf '"%s"' "${causes[$i]}"; done; printf ']}\n'; } > "$SG_STATE/health.json"
notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
for i in "${!causes[@]}"; do m="$A/health-${causes[$i]}"
  if [ ! -e "$m" ]; then msg="HEALTH ${causes[$i]}: ${detail[$i]}"; echo "$msg"
    [ -n "${HEALTH_DRY:-}" ] || { touch "$m"; echo "$(date '+%F %T') $msg" >> "$SG_STATE/alerts.log"; "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1; }; fi
done
for m in "$A"/health-*; do [ -e "$m" ] || continue; k=${m##*/health-}; case " ${causes[*]} " in *" $k "*) ;; *) [ -n "${HEALTH_DRY:-}" ] || { rm -f "$m"; echo "HEALTH $k cleared"; };; esac; done
exit 0
