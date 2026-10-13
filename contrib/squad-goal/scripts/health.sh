#!/bin/bash
# usage: health.sh   (also run by launchd every 2 min, outside watchd: nothing inside the loop can notice the loop itself dying)
# One health pass -> $SG_STATE/health.json. Causes: watchd-down, fastloop-down, watch-stalled (cycle.log silent 6 min), never-started-runs,
# orphan-markers (queued-/runof- whose run never recorded), stuck-prs (janitor-stuck- markers of still-open PRs), loose-worktrees,
# runs-board-drift (live runs that disagree with the cards sitting In Progress -- it NAMES both sides, not just the two counts).
# gh-cooldown and gh-miss-rate come from ghspend.py (ADR-0277, the spend is visible): an exhausted cooldown names when it expires and
# which pass owns it, and a sustained snapshot miss rate is named from the call log; both also land in health.json as a short `brief`
# the hub's footer shows. ghspend.py makes no request, so this pass spends nothing from the bucket it reports on.
# Each cause alerts ONCE (alerts.log + macOS notification) when it appears and re-arms when it clears. HEALTH_DRY=1 prints only; HEALTH_NOTIFY overrides the notifier.
. "$(dirname "$0")/env.sh"; A="$SG_STATE/auto"; mkdir -p "$A"; G=/usr/bin/git; now=$(date +%s)
alive() { [ -s "$1" ] && /bin/ps -p "$(cat "$1")" -o command= 2>/dev/null | grep -q "$2"; }
causes=(); detail=(); briefs=()
add() { causes+=("$1"); detail+=("$2"); briefs+=("${3:-}"); }
alive "$SG_STATE/watchd.pid" 'watchd.sh run' || add watchd-down "watchd is not running: no merges, dispatch or cleanup until it is restarted (watchd.sh start)"
alive "$SG_STATE/fastloop.pid" 'fastloop.sh' || add fastloop-down "fastloop (autopilot every 30 s) is not running"
c="$SG_STATE/cycle.log"; [ -e "$c" ] && [ $((now - $(stat -f %m "$c" 2>/dev/null || stat -c %Y "$c" 2>/dev/null || echo 0))) -gt 360 ] && alive "$SG_STATE/watchd.pid" 'watchd.sh run' && add watch-stalled "cycle.log silent for $(( (now - $(stat -f %m "$c" 2>/dev/null || stat -c %Y "$c" 2>/dev/null || echo 0)) / 60 )) min while watchd is up"
nv=0; for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do f=$r; [ "${r#/}" = "$r" ] && f="$SG_CHECKOUT/runs/$r"
  [ -e "$f/record.jsonl" ] || { [ -e "$f/request.txt" ] && [ $((now - $(stat -f %m "$f/request.txt" 2>/dev/null || stat -c %Y "$f/request.txt" 2>/dev/null || echo 0))) -gt 300 ] && nv=$((nv+1)); }; done
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
# the spend: an exhausted cooldown and a sustained miss rate (ghspend.py reads $SG_STATE/gh-cooldown.json and $SG_STATE/api.log)
if [ -f "$(dirname "$0")/ghspend.py" ]; then
  while IFS=$'\t' read -r gc gb gd; do [ -n "$gc" ] && add "$gc" "$gd" "$gb"; done < <(python3 "$(dirname "$0")/ghspend.py" causes "$SG_STATE" 2>/dev/null)
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
  [ -e "$f/request.txt" ] && [ $((now - $(stat -f %m "$f/request.txt" 2>/dev/null || stat -c %Y "$f/request.txt" 2>/dev/null || echo 0))) -le 300 ] && nyoung=$((nyoung+1))
done
slots=$((nlive+nyoung)); nboard=-1; ip=""
if [ -n "${SG_OWNER:-}" ] && [ -n "${SG_PROJECT:-}" ]; then
  sf="$A/status-field-$SG_OWNER-$SG_PROJECT"; fid=""
  [ -e "$sf" ] && [ $((now - $(stat -f %m "$sf" 2>/dev/null || stat -c %Y "$sf" 2>/dev/null || echo 0))) -lt 86400 ] && fid=$(cat "$sf")
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
  cm=""; for c in $cn; do if [ -e "$A/disp-${c#\#}" ] || [ -e "$A/bug-${c#\#}" ]; then cm="$cm $c"; fi; done      # cards with no live run that still hold their dispatch marker: janitor rule Q releases them
  ncn=$(printf '%s\n' $cn | grep -c . || true); nrn=$(printf '%s\n' $rn | grep -c . || true)
  nslots=$(( slots - nboard )); [ $nslots -lt 0 ] && nslots=$((-nslots))
  # Two consecutive passes, so a single one during a merge or a revert is not a cause, and the zero difference costs nothing.
  # A young run may hold an unmatched card either way (its card may not be In Progress yet), so it excuses one mismatch.
  if [ $nslots -gt $nyoung ] || [ "${ncn:-0}" -gt $nyoung ] || [ "${nrn:-0}" -gt 0 ]; then
    sig="$slots $nboard"; prev=""; [ -e "$A/drift-prev" ] && prev=$(cat "$A/drift-prev" 2>/dev/null)
    # once the marker is set the drift keeps the cause, so a persisting drift whose counts move is not reported as cleared
    if [ "$sig" = "$prev" ] || [ -e "$A/health-runs-board-drift" ]; then
      add runs-board-drift "$nlive live run(s), $nboard card(s) In Progress; cards with no live run:${cn:- -}; live runs with no In Progress card:${rn:- -}${cm:+; holding a dispatch marker with no live run (the janitor releases them):$cm}"; fi
    [ -n "${HEALTH_DRY:-}" ] || printf '%s\n' "$sig" > "$A/drift-prev"
  else
    [ -n "${HEALTH_DRY:-}" ] || rm -f "$A/drift-prev"   # a drift that clears before the second pass leaves no stale count behind
  fi
fi
# A failure that repeats unchanged is a defect in the loop, not bad luck (#1858): the same thing is being fed in, or the same
# thing is being read wrong, and nothing inside the loop could see it. Group the runs that ended failed inside the window by the
# machine reason of their verdict (reasons[0] -- the free text around it rewords itself, the machine reason does not) and report
# a group that reaches SG_HEALTH_REPEAT_COUNT. The report is posted ON the issues those runs were on, so the next dispatch reads
# it before it starts (the SQUAD-NOT-STARTABLE line) and the loop stops re-feeding itself the same input.
rr=$(python3 -I - "$SG_CHECKOUT/runs" "$now" "$(( ${SG_HEALTH_REPEAT_MINUTES:-120} * 60 ))" "$(( ${SG_HEALTH_REPEAT_COUNT:-3} ))" <<'PY'
import json, os, sys
runs, now, win, need = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
groups = {}
try: dirs = os.listdir(runs)
except OSError: dirs = []
for d in dirs:
    p = os.path.join(runs, d, "record.jsonl")
    last = None
    try:
        if now - os.stat(p).st_mtime > win: continue
        for l in open(p, errors="ignore"):
            try: o = json.loads(l)
            except Exception: continue
            if o.get("event") == "verdict": last = o
    except OSError: continue
    if not last or last.get("verdict") != "failed": continue
    rs = last.get("reasons") or []
    # the issues the run was on, from the same shape autofrontier reads: the spawn_accepted lines name them
    iss = []
    try:
        for l in open(p, errors="ignore"):
            if '"spawn_accepted"' in l:
                import re
                iss += re.findall(r"GitHub issue #(\d+)|issue #(\d+)", l) and [m for t in re.findall(r"GitHub issue #(\d+)|issue #(\d+)", l) for m in t if m]
    except OSError: pass
    k = (rs[0] if rs else "?")[:60]
    g = groups.setdefault(k, {"n": 0, "iss": [], "why": (rs[1] if len(rs) > 1 else "")})
    g["n"] += 1
    for i in iss:
        if i not in g["iss"]: g["iss"].append(i)
    g["why"] = g["why"] or (rs[1] if len(rs) > 1 else "")
for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
    if g["n"] >= need:
        print("%d\t%s\t%s\t%s" % (g["n"], k, ",".join(g["iss"]) or "-", g["why"][:220].replace("\t", " ")))
PY
)
if [ -n "$rr" ]; then
  nrr=${rr%%$'\t'*}; rest=${rr#*$'\t'}; rkey=${rest%%$'\t'*}; rest=${rest#*$'\t'}; riss=${rest%%$'\t'*}; rwhy=${rest#*$'\t'}
  add runs-repeating-failure "$nrr run(s) ended failed with the same reason '$rkey' in the last ${SG_HEALTH_REPEAT_MINUTES:-120} min: $rwhy"
  # write it where the next dispatch will read it (once per reason and issue: the marker is the memory)
  [ -n "${HEALTH_DRY:-}" ] || for n in ${riss//,/ }; do
    cm="$A/notstartable-$n-$rkey"; [ -e "$cm" ] && continue
    printf 'SQUAD-NOT-STARTABLE: %s run(s) ended failed with the same reason `%s` in the last %s min.\n\n%s\n\nA dispatch on this issue reads this line before it starts and starts nothing while it is here. Remove this comment (or fix the cause) to re-arm it.\n' \
      "$nrr" "$rkey" "${SG_HEALTH_REPEAT_MINUTES:-120}" "$rwhy" > "$A/.cm$$"
    gh api --method POST "repos/$SG_REPO/issues/$n/comments" -F body=@"$A/.cm$$" >/dev/null 2>&1 && touch "$cm"
    rm -f "$A/.cm$$"; done
fi
# Two loops are not twice the loop (#1858): a second watch.sh doubles every GitHub read the loop makes -- which is what the API's
# abuse limit costs -- and a second loop is invisible from inside the first. watchd runs exactly one; anything else is an orphan.
wr=${SG_WATCH_CMD_RE:-'squad-goal/scripts/watch\.sh$'}
# A match on the command line also hits a forked subshell (pipeline, $(...), background group): it inherits its parent's argv (#17).
# A match whose parent is also a match is the same loop. A detached step, `( cmd & )`, outlives its parent: parent pid 1, but it keeps the
# loop's process group, so a match with parent pid 1 that shares a group with another match is a fork too. Two loops started by two
# watchd runs sit in two groups and both count. One ps snapshot gives pid, parent, group and command together; the regex goes in the
# environment, never in awk's argv.
wl=$(/bin/ps -axo pid=,ppid=,pgid=,command= 2>/dev/null | WR="$wr" /usr/bin/awk -v me=$$ '{ pp[$1]=$2; pg[$1]=$3; c=$0; sub(/^ *[0-9]+ +[0-9]+ +[0-9]+ +/, "", c); if ($1 != me && c ~ ENVIRON["WR"]) m[$1]=1 }
  END { for (p in m) { if (pp[p] in m) continue
    if (pp[p] == 1) { f = 0; for (q in m) if (q != p && pg[q] == pg[p]) f = 1; if (f) continue }
    print p } }' | sort -n | tr '\n' ' '); wl=${wl% }
nw=$(printf '%s\n' $wl | grep -c .)
# The pid watchd holds for its watch.sh is the watch.sh child of the pid in watchd.pid; any other counted pid is the stray. Read-only: nothing here kills.
wd=; [ -s "$SG_STATE/watchd.pid" ] && wd=$(cat "$SG_STATE/watchd.pid"); held=
[ -n "$wd" ] && for p in $wl; do [ "$(/bin/ps -p "$p" -o ppid= 2>/dev/null | tr -d ' ')" = "$wd" ] && held="$held $p"; done; held=${held# }
[ "$nw" -gt 1 ] && add loops-duplicated "$nw watch.sh process(es) alive (pids $wl; watchd pid ${wd:-none} holds ${held:-none}): watchd runs exactly one, and a second copy doubles every GitHub read the loop makes (the API answers with its abuse limit)"
{ printf '{"ts":%s,"ok":%s,"runs":%s,"inprogress":%s,"causes":[' "$now" "$([ ${#causes[@]} = 0 ] && echo true || echo false)" "$slots" "$nboard"; for i in "${!causes[@]}"; do [ $i = 0 ] || printf ','; printf '"%s"' "${causes[$i]}"; done; printf '],"briefs":{'; bn=0; for i in "${!causes[@]}"; do [ -n "${briefs[$i]}" ] || continue; [ $bn = 0 ] || printf ','; bn=1; printf '"%s":"%s"' "${causes[$i]}" "${briefs[$i]}"; done; printf '}}\n'; } > "$SG_STATE/health.json"
notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
for i in "${!causes[@]}"; do m="$A/health-${causes[$i]}"
  if [ ! -e "$m" ]; then msg="HEALTH ${causes[$i]}: ${detail[$i]}"; echo "$msg"
    [ -n "${HEALTH_DRY:-}" ] || { touch "$m"; echo "$(date '+%F %T') $msg" >> "$SG_STATE/alerts.log"; "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1; }; fi
done
for m in "$A"/health-*; do [ -e "$m" ] || continue; k=${m##*/health-}; case " ${causes[*]} " in *" $k "*) ;; *) [ -n "${HEALTH_DRY:-}" ] || { rm -f "$m"; echo "HEALTH $k cleared"; };; esac; done
exit 0
