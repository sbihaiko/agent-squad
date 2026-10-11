#!/bin/bash
# usage: health.sh   (also run by launchd every 2 min, outside watchd: nothing inside the loop can notice the loop itself dying)
# One health pass -> $SG_STATE/health.json. Causes: watchd-down, fastloop-down, watch-stalled (cycle.log silent 6 min), never-started-runs,
# orphan-markers (queued-/runof- whose run never recorded), stuck-prs (janitor-stuck- markers of still-open PRs), loose-worktrees,
# runs-board-drift (live runs that disagree with the cards sitting In Progress -- it NAMES both sides, not just the two counts).
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
# The loop against its own board (#1795): a run that dies without its verdict leaves an `In Progress` card with nobody on it while
# the loop still looks busy. Both numbers come from sources the loop already reads -- $SG_STATE/active-runs.txt and the REST board
# (board.py's endpoint: never GraphQL, which the loop spends on real work).
# "Live" is the autofrontier.sh predicate, verbatim: the run has a record.jsonl and no `"event": "verdict"` line. A relative entry
# resolves against $SG_CHECKOUT/runs/ (as never-started-runs above does). The issues a live run is on come from autofrontier's own
# regex over the `spawn_accepted` lines; a run that has spawned nothing yet contributes no name, which is why the card side gets named.
nlive=0; nyoung=0; li=""
for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do f=$r; [ "${r#/}" = "$r" ] && f="$SG_CHECKOUT/runs/$r"
  if [ -e "$f/record.jsonl" ]; then t=$(cat "$f/record.jsonl" 2>/dev/null); case "$t" in *'"event": "verdict"'*) continue;; esac
    nlive=$((nlive+1))
    while IFS= read -r l; do case "$l" in *'"spawn_accepted"'*)
      m=$(printf '%s\n' "$l" | grep -oE 'GitHub issue #[0-9]+' | head -1 || true)
      [ -n "$m" ] || m=$(printf '%s\n' "$l" | grep -oE 'issue #[0-9]+' | head -1 || true)
      [ -n "$m" ] && li="$li ${m##*#}";; esac; done <<< "$t"
    continue
  fi
  # no record yet: inside its 5-min launch window (request.txt mtime) the launcher is still bringing the child up and the card for
  # it is moved to In Progress by autofrontier right after the launch. Same grace never-started-runs grants above; such a run holds
  # a card slot we cannot name yet, so it is counted as a slot and excuses one unmatched card below.
  [ -e "$f/request.txt" ] && [ $((now - $(stat -f %m "$f/request.txt"))) -le 300 ] && nyoung=$((nyoung+1))
done
slots=$((nlive+nyoung)); nboard=-1; ip=""
if [ -n "${SG_OWNER:-}" ] && [ -n "${SG_PROJECT:-}" ]; then
  sf="$A/status-field-$SG_OWNER-$SG_PROJECT"; fid=""
  [ -e "$sf" ] && [ $((now - $(stat -f %m "$sf"))) -lt 86400 ] && fid=$(cat "$sf")
  if [ -z "$fid" ]; then   # the Status field id, the same lookup board.py caches: the items read takes field ids, not names
    fid=$(gh api --paginate "users/$SG_OWNER/projectsV2/$SG_PROJECT/fields?per_page=100" --jq '[.[]|select(.name=="Status")][0].id' 2>/dev/null)
    [ -n "$fid" ] && [ -z "${HEALTH_DRY:-}" ] && printf '%s\n' "$fid" > "$sf"
  fi
  if [ -n "$fid" ]; then
    raw=$(gh api --paginate "users/$SG_OWNER/projectsV2/$SG_PROJECT/items?per_page=100&fields=$fid" --jq '.[]|select(.content.number)|[(.content.number|tostring),((([.fields[]?|select(.id=='"$fid"')|(.value.name|if type=="object" then .raw else . end)][0])//"-"))]|@tsv' 2>/dev/null); brc=$?
    # an unreadable board is not a drift: leave the counts unknown (-1) and let the next readable pass decide
    [ $brc = 0 ] && { nboard=$(printf '%s\n' "$raw" | awk -F'\t' '$2=="In Progress"' | wc -l | tr -d ' ')
      ip="$(printf '%s\n' "$raw" | awk -F'\t' '$2=="In Progress"{printf " #%s",$1}')"; }
  fi
fi
if [ "$nboard" -ge 0 ] 2>/dev/null; then
  cn=""; for c in $ip; do case " $li " in *" ${c#\#} "*) ;; *) cn="$cn $c";; esac; done      # cards with no live run
  rn=""; for n in $li; do case " $ip " in *" #$n "*) ;; *) rn="$rn #$n";; esac; done          # live runs with no In Progress card
  ncn=$(printf '%s\n' $cn | grep -c . || true); nrn=$(printf '%s\n' $rn | grep -c . || true)
  nslots=$(( slots - nboard )); [ $nslots -lt 0 ] && nslots=$((-nslots))
  # Two consecutive passes, so a single one during a merge or a revert is not a cause, and the zero difference costs nothing.
  # A young run may hold an unmatched card either way (its card may not be In Progress yet), so it excuses one mismatch.
  if [ $nslots -gt $nyoung ] || [ "${ncn:-0}" -gt $nyoung ] || [ "${nrn:-0}" -gt 0 ]; then
    sig="$slots $nboard"; prev=""; [ -e "$A/drift-prev" ] && prev=$(cat "$A/drift-prev" 2>/dev/null)
    # once the marker is set the drift keeps the cause, so a persisting drift whose counts move is not reported as cleared
    if [ "$sig" = "$prev" ] || [ -e "$A/health-runs-board-drift" ]; then
      add runs-board-drift "$nlive live run(s), $nboard card(s) In Progress; cards with no live run:${cn:- -}; live runs with no In Progress card:${rn:- -}"; fi
    [ -n "${HEALTH_DRY:-}" ] || printf '%s\n' "$sig" > "$A/drift-prev"
  else
    [ -n "${HEALTH_DRY:-}" ] || rm -f "$A/drift-prev"   # a drift that clears before the second pass leaves no stale count behind
  fi
fi
{ printf '{"ts":%s,"ok":%s,"runs":%s,"inprogress":%s,"causes":[' "$now" "$([ ${#causes[@]} = 0 ] && echo true || echo false)" "$slots" "$nboard"; for i in "${!causes[@]}"; do [ $i = 0 ] || printf ','; printf '"%s"' "${causes[$i]}"; done; printf ']}\n'; } > "$SG_STATE/health.json"
notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
for i in "${!causes[@]}"; do m="$A/health-${causes[$i]}"
  if [ ! -e "$m" ]; then msg="HEALTH ${causes[$i]}: ${detail[$i]}"; echo "$msg"
    [ -n "${HEALTH_DRY:-}" ] || { touch "$m"; echo "$(date '+%F %T') $msg" >> "$SG_STATE/alerts.log"; "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1; }; fi
done
for m in "$A"/health-*; do [ -e "$m" ] || continue; k=${m##*/health-}; case " ${causes[*]} " in *" $k "*) ;; *) [ -n "${HEALTH_DRY:-}" ] || { rm -f "$m"; echo "HEALTH $k cleared"; };; esac; done
exit 0
