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
# a fork branch ahead of the base with nobody on it: the fix run that was meant to move it is gone (forkclose re-arms it)
if [ -n "${SG_FORK_DIR:-}" ] && [ -d "$SG_FORK_DIR" ]; then
  cwf=$(/usr/sbin/lsof -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'); npk=0; pk=""
  for b in $($G -C "$SG_FORK_DIR" for-each-ref --format='%(refname:short)' "refs/heads/${SG_FORK_BRANCH//%n/}*" 2>/dev/null); do
    s=${b#${SG_FORK_BRANCH//%n/}}; case "$s" in ''|*[!0-9]*) continue;; esac
    [ "$($G -C "$SG_FORK_DIR" rev-list --count "$SG_FORK_BASE..$b" 2>/dev/null)" -gt 0 ] || continue
    w=$($G -C "$SG_FORK_DIR" worktree list --porcelain | awk -v x="refs/heads/$b" '/^worktree /{p=$2} $0=="branch "x{print p}')
    printf '%s\n' "$cwf" | grep -qF "${w:-/nonexistent}/" && continue
    t=$($G -C "$SG_FORK_DIR" log -1 --format=%ct "$b" 2>/dev/null); [ $(( now - t )) -ge $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] && { npk=$((npk+1)); pk="$pk #$s"; }
  done
  [ $npk -gt 0 ] && add fork-parked "$npk fork branch(es)$pk ahead of $SG_FORK_BASE with no process on them: forkclose re-arms the ones it can, the rest need a fix run"
fi
# ready work nobody is on: no run started in 30 min while a ready-for-agent issue has no open PR
ra=" $(gh issue list --repo $SG_REPO --label ready-for-agent --state open --limit 100 --json number --jq '.[].number' 2>/dev/null | tr '\n' ' ')"
if [ -n "${ra// /}" ]; then
  prn=" $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[]|(.title+" "+(.body//""))' 2>/dev/null | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | tr '\n' ' ')"
  free=""; for n in $ra; do case "$prn" in *" $n "*) ;; *) free="$free $n";; esac; done
  nv2=$(sed 's|.*/runs/||' "$SG_STATE/active-runs.txt" 2>/dev/null | sort -u | while read -r r; do d="$SG_CHECKOUT/runs/$r"; [ -d "$d" ] || continue
    grep -q '"event": *"verdict"' "$d/record.jsonl" 2>/dev/null || { [ -n "$(find "$d/request.txt" -mmin -30 2>/dev/null)" ] && echo x; }; done | wc -l | tr -d ' ')
  [ -n "${free// /}" ] && [ "$nv2" = 0 ] && add frontier-idle "ready-for-agent$free with no open PR and no run started in 30 min: the frontier is starved (autofrontier finds nothing, usually an auto/disp-<n> marker that never expires)"
fi
{ printf '{"ts":%s,"ok":%s,"causes":[' "$now" "$([ ${#causes[@]} = 0 ] && echo true || echo false)"; for i in "${!causes[@]}"; do [ $i = 0 ] || printf ','; printf '"%s"' "${causes[$i]}"; done; printf ']}\n'; } > "$SG_STATE/health.json"
notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
for i in "${!causes[@]}"; do m="$A/health-${causes[$i]}"
  if [ ! -e "$m" ]; then msg="HEALTH ${causes[$i]}: ${detail[$i]}"; echo "$msg"
    [ -n "${HEALTH_DRY:-}" ] || { touch "$m"; echo "$(date '+%F %T') $msg" >> "$SG_STATE/alerts.log"; "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1; }; fi
done
for m in "$A"/health-*; do [ -e "$m" ] || continue; k=${m##*/health-}; case " ${causes[*]} " in *" $k "*) ;; *) [ -n "${HEALTH_DRY:-}" ] || { rm -f "$m"; echo "HEALTH $k cleared"; };; esac; done
exit 0
