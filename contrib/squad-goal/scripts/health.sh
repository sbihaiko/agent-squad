#!/bin/bash
# usage: health.sh   (also run by launchd every 2 min, outside watchd: nothing inside the loop can notice the loop itself dying)
# One health pass -> $SG_STATE/health.json. Causes: watchd-down, fastloop-down, watch-stalled (cycle.log silent 6 min), never-started-runs,
# orphan-markers (queued-/runof- whose run never recorded), loose-worktrees,
# runs-board-drift (live runs that disagree with the claimed cards -- In Progress or Doing, whichever the board carries -- it NAMES both sides, not just the two counts).
# gh-cooldown and gh-miss-rate come from ghspend.py (ADR-0277, the spend is visible): an exhausted cooldown names when it expires and
# which pass owns it, and a sustained snapshot miss rate is named from the call log; both also land in health.json as a short `brief`
# the hub's footer shows. ghspend.py makes no request, so this pass spends nothing from the bucket it reports on.
# Each cause alerts ONCE (alerts.log + macOS notification) when it appears and re-arms when it clears. HEALTH_DRY=1 prints only; HEALTH_NOTIFY overrides the notifier.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"; . "$(dirname "$0")/ghcall.sh"; . "$(dirname "$0")/workbranches.sh"; . "$(dirname "$0")/startability.sh"; export SG_GH_PASS=health; export SG_BOARD_SNAPSHOT_S="${SG_BOARD_SNAPSHOT_S:-120}"   # the health check only reports, so it reads the window's snapshot (#1796); every caller that writes leaves this off
A="$SG_STATE/auto"; mkdir -p "$A"; G=/usr/bin/git; now=$(date +%s)
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
alive() { [ -s "$1" ] && $SG_PS -p "$(cat "$1")" -ww -o command= 2>/dev/null | grep -q "$2"; }
# akeys[i] is the alert key of causes[i]: the marker name, which says when an alert has already gone out. It is the cause
# name, except a repeating failure, whose key also names its reason (#1294), so each group alerts once on its own.
causes=(); detail=(); briefs=(); akeys=()
add() { causes+=("$1"); detail+=("$2"); briefs+=("${3:-}"); akeys+=("${4:-$1}"); }
# ADR-0146/ADR-0162: a relight is a start. The pass relights a supervisor it proved dead through the same path `watchd.sh
# start` uses, so every refusal that path carries holds here too (a loop-bug open, orphaned lanes, a broken install); the two
# stops that path would override -- the hub's off switch and a drain in progress -- are read here, before any call. `alive` is
# a process check on the slot, so a pid file that is stale or absent beside a live supervisor never reads as down by itself.
relight() {
  local w
  [ -e "$SG_STATE/auto/owner-stop" ] && { echo "the owner stopped the loop ($SG_STATE/auto/owner-stop)"; return 1; }
  [ -e "$SG_STATE/auto/off" ] && { echo "the loop switch is off ($SG_STATE/auto/off)"; return 1; }
  w=$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null); [ -n "$w" ] && kill -0 "$w" 2>/dev/null && { echo "a drain is in progress (waiter $w)"; return 1; }
  bash "$(dirname "$0")/watchd.sh" start 2>&1
}
# A relight is recorded, and a loop that keeps dying is named: more than three relights inside one hour is `loop-flapping`, so a
# supervisor that dies and is brought back on every pass does not read as a healthy loop between deaths.
relights() { [ -s "$SG_STATE/relights" ] && awk -v n="$now" '$1 > n - 3600' "$SG_STATE/relights" 2>/dev/null | wc -l | tr -d ' ' || echo 0; }
last_relight() { local r; r=$(tail -1 "$SG_STATE/relights" 2>/dev/null); case $r in ''|*[!0-9]*) echo 0 ;; *) echo "$r" ;; esac; }
# #1708, ADR-0146: a loop that drained itself for a loop-bug starts again when no loop-bug is open. The mark the drain left
# (auto/loop-bug-drain) is the only state this watcher acts on, so a loop the owner stopped is never started here. The read
# is the start's own, through the one seam, at most once every SG_LOOPBUG_WATCH_S: an open loop-bug or an unknown read
# refuses that start and the loop stays as it is. The watcher never writes the mark.
if [ -e "$A/loop-bug-drain" ] && [ ! -e "$A/owner-stop" ] && [ -z "${HEALTH_DRY:-}" ] && [ $((now - $(mtime "$SG_STATE/loop-bug-watch"))) -ge "${SG_LOOPBUG_WATCH_S:-300}" ]; then
  : > "$SG_STATE/loop-bug-watch"; lbn=$(cat "$A/loop-bug-drain" 2>/dev/null)
  if lbo=$(bash "$(dirname "$0")/watchd.sh" start --if-bug-drain 2>&1); then
    printf '%s WATCHD START no loop-bug is open, the loop drained for #%s starts again: %s\n' "$(date '+%F %T')" "$lbn" "$(printf '%s' "$lbo" | tail -n 1)" >> "$SG_STATE/watch.log"
  fi
fi
if alive "$SG_STATE/watchd.pid" 'watchd.sh run'; then :
elif ro=$(relight); then
  case "$ro" in *"already running"*) ;;   # a stale or missing pid file beside a live supervisor: it never went down, so nothing is recorded
  *) printf '%s\n' "$now" >> "$SG_STATE/relights"; add watchd-down "relit: $ro"; rln=$(relights); [ "${rln:-0}" -gt 3 ] && add loop-flapping "$rln relights in the last hour: the supervisor keeps dying and the pass brings it back each time" ;;
  esac
else add watchd-down "watchd is not running: $ro"; fi
alive "$SG_STATE/fastloop.pid" 'fastloop.sh' || add fastloop-down "fastloop (autopilot every SG_FAST_INTERVAL s) is not running"
c="$SG_STATE/cycle.log"; [ -e "$c" ] && [ $((now - $(mtime "$c"))) -gt 360 ] && alive "$SG_STATE/watchd.pid" 'watchd.sh run' && add watch-stalled "cycle.log silent for $(( (now - $(mtime "$c")) / 60 )) min while watchd is up"
# #1226: this pass runs while the loop is stopped too, so it frees what a dead run holds (squad/reap.py): the caps reclaim of dead
# leases, and the killed verdict (squad.killed) for a listed run whose own process is proven dead. It keeps the active-runs lines,
# kills nothing, and leaves a live run, a launch and a gate wait alone. The module is the one beside this script, else SG_SQUAD.
rroot=$(cd "$(dirname "$0")/../../.." && pwd); [ -f "$rroot/squad/reap.py" ] || rroot=${SG_SQUAD:-}
while IFS= read -r l; do [ -n "$l" ] || continue; echo "$l"; [ -n "${HEALTH_DRY:-}" ] || echo "$(date '+%F %T') $l" >> "$SG_STATE/alerts.log"
done < <(PYTHONPATH="$rroot${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.reap "$SG_STATE/active-runs.txt" "$SG_CHECKOUT" ${HEALTH_DRY:+--dry} 2>/dev/null)
# #1270: a pass that reads no free run slot names the holder that has had no live lease and no live process for five minutes. A
# count alone leaves the owner guessing which run blocks dispatch, and the reap above leaves a run whose proof of death it
# cannot read. The ceiling is this checkout's (`caps.run_holders`), one line per holder.
slots=$(PYTHONPATH="$rroot${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.reap --slots "$SG_STATE/active-runs.txt" "$SG_CHECKOUT" 2>/dev/null | paste -sd';' - | sed 's/;/; /g')
[ -n "${slots// /}" ] && add no-free-slot "$slots"
nv=0; for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do f=$r; [ "${r#/}" = "$r" ] && f="$SG_CHECKOUT/runs/$r"
  [ -e "$f/record.jsonl" ] || { [ -e "$f/request.txt" ] && [ $((now - $(mtime "$f/request.txt"))) -gt 300 ] && nv=$((nv+1)); }; done
[ $nv -gt 0 ] && add never-started-runs "$nv run(s) with no record.jsonl after 5 min (the squad refused to start)"
no=0; for f in "$A"/runof-*; do [ -e "$f" ] || continue; k=${f##*/runof-}; [ -e "$A/queued-$k" ] || continue; d="$SG_CHECKOUT/runs/$(cat "$f")"
  [ -e "$d/record.jsonl" ] || [ ! -d "$d" ] || { [ $((now - $(stat -f %m "$d/request.txt" 2>/dev/null || echo $now))) -gt 600 ] && no=$((no+1)); }; done
[ $no -gt 0 ] && add orphan-markers "$no queued-* marker(s) whose fix run never started"
nl=$($G -C "$SG_CHECKOUT" worktree list --porcelain 2>/dev/null | awk '/^worktree /{w=$2} /^detached/{print w}' | grep -cE "$HOME/wt-|/acpr\.")
[ "$nl" -gt 0 ] && add loose-worktrees "$nl loose detached worktree(s) (wtclean keeps the dirty or unpushed ones; look at WT LOOSE)"
# a fork branch ahead of the base with nobody on it: the fix run that was meant to move it is gone (forkclose re-arms it)
if [ -n "${SG_FORK_DIR:-}" ] && [ -d "$SG_FORK_DIR" ]; then
  cwf=$("$SG_LSOF" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'); npk=0; pk=""
  for b in $($G -C "$SG_FORK_DIR" for-each-ref --format='%(refname:short)' "refs/heads/${SG_FORK_BRANCH//%n/}*" 2>/dev/null); do
    s=${b#${SG_FORK_BRANCH//%n/}}; case "$s" in ''|*[!0-9]*) continue;; esac
    ahead=$($G -C "$SG_FORK_DIR" rev-list --count "$SG_FORK_BASE..$b" 2>/dev/null); [ "${ahead:-0}" -gt 0 ] || continue
    w=$($G -C "$SG_FORK_DIR" worktree list --porcelain | awk -v x="refs/heads/$b" '/^worktree /{p=$2} $0=="branch "x{print p}')
    printf '%s\n' "$cwf" | grep -qF "${w:-/nonexistent}/" && continue
    t=$($G -C "$SG_FORK_DIR" log -1 --format=%ct "$b" 2>/dev/null); [ $(( now - ${t:-0} )) -ge $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] && { npk=$((npk+1)); pk="$pk #$s"; }
  done
  [ $npk -gt 0 ] && add fork-parked "$npk fork branch(es)$pk ahead of $SG_FORK_BASE with no process on them: forkclose re-arms the ones it can, the rest need a fix run"
fi
# the spend: an exhausted cooldown and a sustained miss rate (ghspend.py reads $SG_STATE/gh-cooldown.json and $SG_STATE/api.log)
if [ -f "$(dirname "$0")/ghspend.py" ]; then
  while IFS=$'\t' read -r gc gb gd; do [ -n "$gc" ] && add "$gc" "$gd" "$gb"; done < <(python3 "$(dirname "$0")/ghspend.py" causes "$SG_STATE" 2>/dev/null)
fi
# Every agent-created issue has one product type. Surface a missing type before the issue can be dispatched or lost.
# #437: every page of the open list (gh_pages), and a defect that is not typed bug is its own cause (a spec is a document, not a defect).
oi=$(gh_pages "repos/$SG_REPO/issues?state=open&per_page=100" 2>/dev/null)
untyped=" $(printf '%s' "$oi" | jq -r '.[]|[.labels[].name] as $l|select(.pull_request|not)|select(($l|index("bug")|not) and ($l|index("enhancement")|not) and ($l|index("spec")|not))|.number' 2>/dev/null | tr '\n' ' ')"
[ -n "${untyped// /}" ] && add untyped-issues "open issue(s) without a type label:$(for n in $untyped; do printf ' #%s' "$n"; done)"
# a defect reads as one in its title or body; typed enhancement (an untyped one is named above) and no bug label.
# "bug lane", "bug pass" and the other board words name a board part, not a defect, so they are cut before the match (#1681)
undef=" $(printf '%s' "$oi" | jq -r '.[]|[.labels[].name] as $l|select(.pull_request|not)|select(($l|index("enhancement")) and ($l|index("bug")|not) and ($l|index("spec")|not))|select((.title+" "+(.body//""))|gsub("\\bbug (lane|pass|board|label|card|project)s?\\b";"";"i")|test("\\b(bug|defect|regression|crash(es|ed)?|broken|fails|failing|wrong|incorrect)\\b";"i"))|.number' 2>/dev/null | tr '\n' ' ')"
[ -n "${undef// /}" ] && add defect-without-bug "open defect(s) that carry no bug label:$(for n in $undef; do printf ' #%s' "$n"; done)"
# Startable work nobody is on: blocking edges decide readiness; labels only select the dispatcher that owns an issue.
# A ready issue with no run or work branch for one pass must remain visible even when it has only a wave label.
# A label the frontier skips is no work for it (#1593): the set is the frontier's own (skiplabels.sh), with its structural
# labels, so a card autofrontier.sh will never start is never named startable here. A label only the bug lane skips
# (no-auto, question) is no hold for the frontier, so that card stays visible: the alert names it when it is starved.
skiplist=$(sg_skip_labels frontier bug,debt,run-parent)
ra=""
while IFS=$'\t' read -r n l; do
  [ -n "$n" ] || continue
  s=0; for k in ${skiplist//,/ }; do case ",$l," in *",$k,"*) s=1;; esac; done
  [ "$s" = 1 ] || ra="$ra $n"
done < <(gh api "repos/$SG_REPO/issues?state=open&per_page=100" -q '.[]|select(.pull_request|not)|[(.number|tostring),([.labels[].name]|join(","))]|join("\t")' 2>/dev/null)
# The readiness facts are read in ONE batched request, 100 aliases per request (#1648): stack.sh read one issue per card,
# and this pass runs every two minutes, so that walk spent more than a thousand GraphQL reads an hour on its own.
ready=""; scache=$(mktemp -d)
rest=$ra
while [ -n "${rest// /}" ]; do
  chunk=$(printf '%s\n' $rest | head -100 | paste -sd, -)
  rest=$(printf '%s\n' $rest | tail -n +101 | paste -sd' ' -)
  [ -n "$chunk" ] || break
  gh_api "repos/$SG_REPO/issues?numbers=$chunk" 2>/dev/null | jq -c '.[]?' 2>/dev/null | while IFS= read -r it; do printf '%s' "$it" > "$scache/$(printf '%s' "$it" | jq -r '.number')"; done
done
for n in $ra; do
  if [ -s "$scache/$n" ]; then SG_STACK_ISSUE="$scache/$n" "$(dirname "$0")/stack.sh" "$n" 2>/dev/null | head -1 | grep -qE '^READY' && ready="$ready $n"
  else "$(dirname "$0")/stack.sh" "$n" 2>/dev/null | head -1 | grep -qE '^READY' && ready="$ready $n"; fi   # a card the batch missed is read as it was before (#1648)
done
rm -rf "$scache"
if [ -n "${ready// /}" ]; then
  brs=" $(sg_work_issues) "
  free=""; for n in $ready; do case "$brs" in *" $n "*) ;; *) free="$free $n";; esac; done
  # A live run is one with a record and no verdict, however old it is (#1338); only a run with no record yet is live for its 30 min.
  nv2=$(sort -u "$SG_STATE/active-runs.txt" 2>/dev/null | while read -r r; do d=$r; [ "${r#/}" = "$r" ] && d="$SG_CHECKOUT/runs/$r"; [ -d "$d" ] || continue   # an absolute entry stands: a run worktree's folder too (#580, #585)
    if [ -e "$d/record.jsonl" ]; then grep -q '"event": *"verdict"' "$d/record.jsonl" 2>/dev/null || echo x
    else [ -n "$(find "$d/request.txt" -mmin -30 2>/dev/null)" ] && echo x; fi; done | wc -l | tr -d ' ')
  if [ -n "${free// /}" ] && [ "$nv2" = 0 ]; then
    # Frontier work is a Todo card, the owner's go signal (#550): a name whose card is not in Todo is dropped. An unreadable board keeps the list.
    if td=$(sg_todo_cards 2>/dev/null); then tds=" $(printf '%s\n' "$td" | tr '\n' ' ')"; keep=""; for n in $free; do case "$tds" in *" $n "*) keep="$keep $n";; esac; done; free=$keep; fi
    [ -n "${free// /}" ] && add frontier-idle "startable$free with no work branch and no run started in 30 min: the frontier is starved (autofrontier finds nothing, usually an auto/disp-<n> marker that never expires)"
  fi
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
  [ -e "$f/request.txt" ] && [ $((now - $(mtime "$f/request.txt"))) -le 300 ] && nyoung=$((nyoung+1))
done
slots=$((nlive+nyoung)); nboard=-1; ip=""; cols=""
# the names a claimed card wears, from the board vocabulary (#1342): the definition's name and the shipped one (Doing here)
cl=$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import board_columns; print(board_columns.claimed_names())' "$(dirname "$0")" 2>/dev/null); [ -n "$cl" ] || cl="In Progress|Doing"
if [ -n "${SG_OWNER:-}" ] && [ -n "${SG_PROJECT:-}" ]; then
  # the state each card carries, asked of the seam (#236): one items read, the Status field id cached by the binding
  raw=$(python3 -I "$(dirname "$0")/issue_state.py" 2>/dev/null); brc=$?
  # an unreadable board is not a drift: leave the counts unknown (-1) and let the next readable pass decide
  [ $brc = 0 ] && { nboard=$(printf '%s\n' "$raw" | awk -F'\t' -v set="$cl" 'BEGIN{n=split(set,a,"|"); for(i=1;i<=n;i++) w[a[i]]=1} ($2 in w)' | wc -l | tr -d ' ')
    ip="$(printf '%s\n' "$raw" | awk -F'\t' -v set="$cl" 'BEGIN{n=split(set,a,"|"); for(i=1;i<=n;i++) w[a[i]]=1} ($2 in w){printf " #%s",$1}')"
    cols="$(printf '%s\n' "$raw" | awk -F'\t' -v set="$cl" 'BEGIN{n=split(set,a,"|"); for(i=1;i<=n;i++) w[a[i]]=1} ($2 in w){print $2}' | sort -u | paste -sd/ -)"
    [ -n "$cols" ] || cols="${cl%%|*}"; }   # the name the board carries; with no claimed card, the definition's name
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
      add runs-board-drift "$nlive live run(s), $nboard card(s) $cols; cards with no live run:${cn:- -}; live runs with no $cols card:${rn:- -}${cm:+; holding a dispatch marker with no live run (the janitor releases them):$cm}"; fi
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
rr=$(python3 -I - "$SG_CHECKOUT" "$now" "$(( ${SG_HEALTH_REPEAT_MINUTES:-120} * 60 ))" "$(( ${SG_HEALTH_REPEAT_COUNT:-3} ))" <<'PY'
import glob, json, os, sys
co, now, win, need = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
groups = {}
def flat(s): return str(s).replace("\t", " ").replace("\n", " ").replace("\r", " ")   # the TSV reads four fields: a tab or newline in free text or in a reason never splits one (#1294)
# a run folder is <checkout>/runs/<run>, or in a run or child worktree (#580, #585)
recs = [r for rel in (("runs",), ("worktrees", "run", "*", "runs"), ("worktrees", "child", "*", "runs"))
        for r in glob.glob(os.path.join(glob.escape(co), *rel, "*", "record.jsonl"))]
for p in recs:
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
    # the key is the machine reason, flattened like the text; an empty reason is "?", so its TSV field is never empty (an empty field collapses under read)
    k = (flat(rs[0]) if rs else "")[:60] or "?"
    g = groups.setdefault(k, {"n": 0, "iss": [], "why": (rs[1] if len(rs) > 1 else "")})
    g["n"] += 1
    for i in iss:
        if i not in g["iss"]: g["iss"].append(i)
    g["why"] = g["why"] or (rs[1] if len(rs) > 1 else "")
for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
    if g["n"] >= need:
        # one line per group: every field is flattened to its own line, so a free text newline never reads as another group
        print("%d\t%s\t%s\t%s" % (g["n"], k, ",".join(g["iss"]) or "-", flat(g["why"])[:220]))
PY
)
if [ -n "$rr" ]; then
  # The helper answers one line per group, and every group at or over the count is its own cause and marks its own issues
  # (#1294). Reading only the first line folded the rest into its free text and left their issues unmarked.
  while IFS=$'\t' read -r nrr rkey riss rwhy; do
    [ -n "$nrr" ] || continue
    # The marker's name is the reason, and the reason is the run's own machine word: `killed/no-process` carries a path
    # separator, so as a file name it names a directory that does not exist, touch fails, the guard below never holds and
    # every pass posts the marker again (#1292). The key is the reason made path-safe; two reasons that encode alike are
    # one marker by design, and the marker line still names the raw reason.
    kf=$(printf '%s' "$rkey" | tr -c 'A-Za-z0-9._-' '-')
    # The alert is per group (#1294): its key is the cause plus the reason, and the checksum keeps two reasons that encode alike
    # apart, so a second group that reaches the count later alerts while the first still persists.
    ak="runs-repeating-failure-$kf-$(printf '%s' "$rkey" | cksum | cut -d' ' -f1)"
    add runs-repeating-failure "$nrr run(s) ended failed with the same reason '$rkey' in the last ${SG_HEALTH_REPEAT_MINUTES:-120} min: $rwhy" "" "$ak"
    # write it where the next dispatch will read it (once per reason and issue: the marker is the memory)
    [ -n "${HEALTH_DRY:-}" ] || for n in ${riss//,/ }; do
      # The marker's name is the reason, and the reason is the run's own machine word: `killed/no-process` carries a path
      # separator, so as a file name it names a directory that does not exist, touch fails, the guard below never holds and
      # every pass posts the marker again (#1292). The key is the reason made path-safe; two reasons that encode alike are
      # one marker by design, and the marker line still names the raw reason.
      kf=$(printf '%s' "$rkey" | tr -c 'A-Za-z0-9._-' '-')
      # a group whose runs named no issue ("-") has no issue to comment on or mark (#1294)
      case "$n" in ''|*[!0-9]*) continue;; esac
      cm="$A/notstartable-$n-$kf"; [ -e "$cm" ] && continue
      printf 'SQUAD-NOT-STARTABLE: build=%s %s run(s) ended failed with the same reason `%s` in the last %s min.\n\n%s\n\nA dispatch on this issue reads this line before it starts and starts nothing while it is here. A later build re-arms it on its own. To re-arm it sooner, remove this comment and the frozen:not-startable label, or fix the cause.\n' \
        "$(sg_loop_build)" "$nrr" "$rkey" "${SG_HEALTH_REPEAT_MINUTES:-120}" "$rwhy" > "$A/.cm$$"
      # The marker is claimed before the post, so a pass interrupted between the two posts nothing twice; a post GitHub
      # refuses drops the claim, so the next pass tries again. An existing path-safe marker keeps its name, so a marker
      # written before this rule still dedupes.
      : > "$cm"
      if gh api --method POST "repos/$SG_REPO/issues/$n/comments" -F body=@"$A/.cm$$" >/dev/null 2>&1; then
        gh api --method POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:not-startable" >/dev/null 2>&1
      else rm -f "$cm"; fi
      rm -f "$A/.cm$$"; done
  done <<< "$rr"
fi
# Two loops are not twice the loop (#1858): a second watch.sh doubles every GitHub read the loop makes -- which is what the API's
# abuse limit costs -- and a second loop is invisible from inside the first. watchd runs exactly one; anything else is an orphan.
wr=${SG_WATCH_CMD_RE:-'squad-goal/scripts/watch\.sh$'}
# A match on the command line also hits a forked subshell (pipeline, $(...), background group): it inherits its parent's argv (#17).
# A match whose parent is also a match is the same loop. A detached step, `( cmd & )`, outlives its parent: parent pid 1, but it keeps the
# loop's process group, so a match with parent pid 1 that shares a group with another match is a fork too. Two loops started by two
# watchd runs sit in two groups and both count. One ps snapshot gives pid, parent, group and command together; the regex goes in the
# environment, never in awk's argv.
wl=$($SG_PS -axwwo pid=,ppid=,pgid=,command= 2>/dev/null | WR="$wr" /usr/bin/awk -v me=$$ '{ pp[$1]=$2; pg[$1]=$3; c=$0; sub(/^ *[0-9]+ +[0-9]+ +[0-9]+ +/, "", c); if ($1 != me && c ~ ENVIRON["WR"]) m[$1]=1 }
  END { for (p in m) { if (pp[p] in m) continue
    if (pp[p] == 1) { f = 0; for (q in m) if (q != p && pg[q] == pg[p]) f = 1; if (f) continue }
    print p } }' | sort -n | tr '\n' ' '); wl=${wl% }
# #1427: the count is this profile's loops, not the machine's. A second profile runs the same watch.sh under its own state
# directory, and counting it made a healthy machine alert -- the false alert that taught the reader to ignore the real one.
# One state directory per profile holds its supervisor, locks and pid files (address.sh), so a candidate is another profile's
# loop only when another state dir positively names it: the lock pidfile its loop holds (`locks/watch/pid`) or the watch.sh
# lane its live supervisor holds (`watchd.pid`). This profile's own lock pidfile, lane and the process's own SG_STATE
# (`/proc` on Linux; macOS hides another process's environment, so the pidfiles and the fail-open count stand for it there)
# name this profile's loops. A candidate no state dir names stays counted: a stray of this profile that holds no pidfile is
# exactly the duplicate this alert exists for, and dropping it would hide the real one.
mestate=${SG_STATE%/}; sbase=$(dirname "$mestate")
# A pidfile names a number or nothing: a malformed or dead pid names no live supervisor, so it is read as absent.
pidval() { local v; v=$(cat "$1" 2>/dev/null); case $v in ''|*[!0-9]*) return 1;; esac; printf '%s\n' "$v"; }
wlockpid=$(pidval "$mestate/locks/watch/pid") || wlockpid=
wd=$(pidval "$mestate/watchd.pid") || wd=; [ -n "$wd" ] && ! kill -0 "$wd" 2>/dev/null && wd=
# what the other state directories beside this one name: their loop's lock pidfile, and the lane child of their live supervisor
fpid=; fwd=
for d in "$sbase"/*; do [ -d "$d" ] && [ "${d%/}" != "$mestate" ] || continue
  x=$(pidval "$d/locks/watch/pid") && fpid="$fpid $x"
  x=$(pidval "$d/watchd.pid") && kill -0 "$x" 2>/dev/null && fwd="$fwd $x"
done
# The environment of one process: SG_STATE, when the platform exposes it (/proc); one record of the NUL-separated list, read
# whole, so a value mid-buffer is never parsed truncated.
pstate() { [ -r "/proc/$1/environ" ] && /usr/bin/awk 'BEGIN{RS="\0"} index($0,"SG_STATE=")==1 { print substr($0,10); exit }' "/proc/$1/environ" 2>/dev/null; }
wkeep=
for p in $wl; do
  pp=$($SG_PS -p "$p" -o ppid= 2>/dev/null | tr -d ' ')
  [ "$p" = "$wlockpid" ] && { wkeep="$wkeep $p"; continue; }
  [ -n "$wd" ] && [ "$pp" = "$wd" ] && { wkeep="$wkeep $p"; continue; }
  s=$(pstate "$p"); [ -n "$s" ] && [ "${s%/}" = "$mestate" ] && { wkeep="$wkeep $p"; continue; }
  case " $fpid " in *" $p "*) continue;; esac
  case " $fwd " in *" $pp "*) continue;; esac
  wkeep="$wkeep $p"
done
wl=${wkeep# }
nw=$(printf '%s\n' $wl | grep -c .)
# The pid watchd holds for its watch.sh is the watch.sh child of the pid in watchd.pid; any other counted pid is the stray. Read-only: nothing here kills.
held=
[ -n "$wd" ] && for p in $wl; do [ "$($SG_PS -p "$p" -o ppid= 2>/dev/null | tr -d ' ')" = "$wd" ] && held="$held $p"; done; held=${held# }
[ "$nw" -gt 1 ] && add loops-duplicated "$nw watch.sh process(es) alive (pids $wl; watchd pid ${wd:-none} holds ${held:-none}): watchd runs exactly one, and a second copy doubles every GitHub read the loop makes (the API answers with its abuse limit)"
# health.json shape (#1294, asserted in test-health.sh): `causes` holds one entry per alert, so a repeating failure with two groups
# over the count is listed twice and the length of the list counts the groups. `briefs` is keyed by cause name, so it carries the
# ghspend causes only; a group's reason and count live in its alert line and its marker, not here.
# A landing lock whose current gate round has run past the round's bounds is stuck, not queued (#1290): a round waits up to
# SERIAL_WAIT_SECONDS (900 s) for the gate lock, then the gate is bounded at the full bound (sg_full_bound), so a live holder whose
# last round began past their sum has outlived both, and a person is told, once. A landing runs several rounds (a red tip, the child
# that broke it leaves, the tip again), so wave.sh marks each round's start in the lock's round file: that mark is the progress, not
# the landing's start. The lock's pid mtime is the instant the holder took it, which is what the alert names; a new holder restarts it.
lkd="$SG_STATE/locks/single-wland"; lhp=$(cat "$lkd/pid" 2>/dev/null)
if [ -n "$lhp" ] && kill -0 "$lhp" 2>/dev/null && [ "$($SG_PS -o stat= -p "$lhp" 2>/dev/null | cut -c1)" != Z ]; then
  lw=${SERIAL_WAIT_SECONDS:-900}; case $lw in ''|*[!0-9]*) lw=900;; esac
  lb=${SG_GATE_BOUND_SECONDS:-900}; case $lb in ''|*[!0-9]*|0) lb=900;; esac
  # (#2379) The landing gate runs the full battery, so its bound is the full bound (sg_full_bound, ADR-0169 rule 9).
  lfb=$(sg_full_bound "$lb" 2>/dev/null); case $lfb in ''|*[!0-9]*|0) ;; *) [ "$lfb" -gt "$lb" ] && lb=$lfb;; esac
  ls=$(mtime "$lkd/pid"); rs=$(mtime "$lkd/round"); [ "$rs" -gt "$ls" ] && lp=$rs || lp=$ls   # the round mark, when a round has begun since the lock was taken
  la=$(( now - lp ))
  [ "$ls" -gt 0 ] && [ "$la" -gt $(( lw + lb )) ] && add landing-held "the landing lock has been held by pid $lhp since $(date -r "$ls" '+%F %T' 2>/dev/null || date -d "@$ls" '+%F %T' 2>/dev/null || printf '%s' "$ls") (no gate round began for ${la}s, past the ${lw}s gate wait plus the ${lb}s gate bound): the landing is stuck, not queued"
fi
# A janitor pass that holds its single-janitor lock past its own bound is stuck, not slow (#1775): the pass deadline lives
# inside janitor.py (#1774) and fires only when the next call would start, so a hang in the shell wrapper, or in Python code
# between calls, held the lock in silence. The bound is the pass deadline plus one call deadline, and a live holder past it
# names its pid and the lock's age. A lock whose holder is dead is the stale lock single.sh reaps, never this cause.
jld="$SG_STATE/locks/single-janitor"; jlp=$(cat "$jld/pid" 2>/dev/null)
if [ -n "$jlp" ] && kill -0 "$jlp" 2>/dev/null && [ "$($SG_PS -o stat= -p "$jlp" 2>/dev/null | cut -c1)" != Z ]; then
  jpd=${SG_JANITOR_DEADLINE:-900}; case $jpd in ''|*[!0-9]*) jpd=900;; esac   # janitor.py's own default and name (#1774)
  jcd=${JANITOR_CALL_TIMEOUT:-60}; case $jcd in ''|*[!0-9]*) jcd=60;; esac    # janitor.py's call deadline (ADR-0051)
  ja=$(( now - $(mtime "$jld/pid") ))
  [ "$jpd" -gt 0 ] && [ "$ja" -gt $(( jpd + jcd )) ] && add janitor-held "the janitor lock has been held by pid $jlp for ${ja}s, past the ${jpd}s pass deadline plus the ${jcd}s call deadline: the pass is stuck outside any call"
fi
{ printf '{"ts":%s,"ok":%s,"runs":%s,"inprogress":%s,"relights":%s,"last_relight":%s,"causes":[' "$now" "$([ ${#causes[@]} = 0 ] && echo true || echo false)" "$slots" "$nboard" "$(relights)" "$(last_relight)"; for i in "${!causes[@]}"; do [ $i = 0 ] || printf ','; printf '"%s"' "${causes[$i]}"; done; printf '],"briefs":{'; bn=0; for i in "${!causes[@]}"; do [ -n "${briefs[$i]}" ] || continue; [ $bn = 0 ] || printf ','; bn=1; printf '"%s":"%s"' "${causes[$i]}" "${briefs[$i]}"; done; printf '}}\n'; } > "$SG_STATE/health.json"
notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
# The marker is written after the pass, not inside it: one cause may arrive as several lines (a repeating failure names one per
# group, #1294), and every one of them must reach the alert readers on the pass it appears. The marker still holds the alert
# key across passes, so an unchanged alert fires once.
for i in "${!causes[@]}"; do m="$A/health-${akeys[$i]}"; [ -e "$m" ] && continue
  msg="HEALTH ${causes[$i]}: ${detail[$i]}"; echo "$msg"
  [ -n "${HEALTH_DRY:-}" ] || { echo "$(date '+%F %T') $msg" >> "$SG_STATE/alerts.log"; "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1; }
done
[ -n "${HEALTH_DRY:-}" ] || for i in "${!causes[@]}"; do touch "$A/health-${akeys[$i]}"; done
for m in "$A"/health-*; do [ -e "$m" ] || continue; k=${m##*/health-}; case " ${akeys[*]} " in *" $k "*) ;; *) [ -n "${HEALTH_DRY:-}" ] || { rm -f "$m"; echo "HEALTH $k cleared"; };; esac; done
exit 0
