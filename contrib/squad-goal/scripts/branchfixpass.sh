#!/bin/bash
# usage: branchfixpass.sh   one pass over the pushed work branches: one fix run for each reviewed tip whose review says CHANGES (#401).
#
# branchreviewpass.sh keeps the verdict of each tip in $SG_STATE/review-<key>.{md,sha}. This pass lists the work branches as the
# review pass does (workbranches.sh) and, for each branch whose kept sha is its tip and whose verdict is CHANGES, dispatches one fix
# run on the same branch: the request is branchfix.sh's text, sent through mkreq.sh and launch.py as every pass sends a run.
# The fix run pushes a new tip; the review pass reviews it; an APPROVE lets branchland.sh land it.
# Once per reviewed tip: auto/branchfix-<key>-<sha9> holds the run id. A launch that starts no run keeps no marker, so the next pass
# retries the tip; a round that pushes nothing drops its marker too (#2345), because the next tip is never coming, and so does a
# round that is gone with no verdict and a still record (#2346). The stall rule below bounds those retries.
# Stop (ADR-0134): a round fails when the review of the next tip has the same findings (line numbers, shas and whitespace removed).
# auto/branchfix-fp-<key> keeps the findings of the last dispatched round and auto/branchfix-same-<key> the count of failed rounds in
# a row. At SG_BRANCHFIX_STALLS (default 2) the branch is stalled and its rounds stop: one status line on the issue and the label frozen:owner, once. Changed
# findings restart the count. At most SG_FIX_PASS_MAX fix runs (default 3) start per pass. watch.sh runs this under single.sh.
# A loop defect is fixed outside the loop (ADR-0146, #841): no fix run for a branch whose issue carries `loop-bug` or a `loop-bug:*`
# label, and none at all while LOOP STOP holds or the loop is paused (auto/off, #1789). Each such tip gets one BRANCHFIX SKIP line (auto/branchfix-skip-<key>-<sha9>).
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=branchfix
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/workbranches.sh"
remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; max=${SG_FIX_PASS_MAX:-3}; cap=${SG_BRANCHFIX_STALLS:-2}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
sg_work_branches "$remote" || { echo "BRANCHFIX: $wb_err"; exit 0; }
A="$SG_STATE/auto"; mkdir -p "$A"
# The push back (#768): a fix round's child pushes nothing, as every child, so its commits stay on the run's target branch. Once
# the run has a verdict, this pass pushes that head to the work branch, fast-forward only; auto/branchfix-push-<run> ("<br> <sha>")
# names the round and goes after the push, or when the head is no fast-forward of the branch (said once). The review pass then
# reviews the new tip.
for m in "$A"/branchfix-push-*; do
  [ -f "$m" ] || continue
  run=${m##*/branchfix-push-}; read -r br dsha < "$m"   # dsha: the sha the round was dispatched for, the one its lease is on
  rd=$(/usr/bin/grep "/$run\$" "$SG_STATE/active-runs.txt" 2>/dev/null | tail -1); liv=${rd:+1}; rd=${rd:-$SG_CHECKOUT/runs/$run}
  # (#1960) A kept round's heads are those of every outcome that is not fail: a round whose proof ended unproven (ADR-0164) still
  # made its rebase, and only the work branch takes it; branchland's gate and review still decide. An ordinary round reads pass only.
  kx=$(printf '%s' "$br" | tr / _); kept="$SG_STATE/auto/kept-$kx"; oc=pass; [ -s "$kept" ] && oc=nonfail
  t=$(python3 -c 'import json,sys
s=v=None; h=[]
for l in open(sys.argv[1]):
    try: d=json.loads(l)
    except Exception: continue
    if d.get("event") in ("run_start","resume"): v=None; s=d if d.get("event")=="run_start" else s
    elif d.get("event")=="verdict": v=d
    elif d.get("event")=="outcome" and (d.get("outcome")=="pass" if sys.argv[2]=="pass" else d.get("outcome")!="fail") and (d.get("workspace") or {}).get("head"): h.append(d["workspace"]["head"])
if s and v and s.get("target") and s.get("target_branch"): print(s["target"], s["target_branch"], *reversed(h))' "$rd/record.jsonl" "$oc" 2>/dev/null)
  # (#2346) A round with no run_start and no verdict is read from its record alone. A live round keeps both markers: it may still
  # end with a head, and a run that has just started holds no verdict for a while. A round that is gone (no line in
  # active-runs.txt) and whose record has not moved for SG_BRANCHFIX_DEAD_MIN minutes ended without a verdict, so it pushed
  # nothing and its tip needs a fresh round: its markers go, or the branch freezes with no stop and no alert. A kept branch
  # keeps them, because branchland's round_empty reads the dispatch marker (#1960).
  [ -n "$t" ] || {
    if [ -z "$liv" ] && [ ! -s "$kept" ] && [ -n "$(/usr/bin/find "$rd/record.jsonl" -mmin +${SG_BRANCHFIX_DEAD_MIN:-20} 2>/dev/null)" ]; then
      echo "BRANCHFIX PUSH $br: run $run ended with no verdict, so the tip ${dsha:0:9} is dispatched again"
      rm -f "$m" "$A/branchfix-$kx-${dsha:0:9}"
    fi; continue; }
  read -r tg tb ch <<< "$t"
  # The head (#779): on a work branch the land into the target branch can be a no-op, so that branch stays at the base. The
  # child's own heads (pass outcomes' workspace heads, last first) come first, then the target branch: the first one that is a
  # fast-forward past the work branch is pushed.
  head=$(/usr/bin/git -C "$tg" rev-parse -q --verify "refs/heads/$tb^{commit}" 2>/dev/null) \
    && /usr/bin/git -C "$tg" fetch -q "$remote" "refs/heads/$br" 2>/dev/null || { echo "BRANCHFIX PUSH $br: the head of run $run is not readable; the next pass retries"; continue; }
  old=$(/usr/bin/git -C "$tg" rev-parse FETCH_HEAD)
  # ff reads an outcome's own head only: a run that left no commit names no head of its own, and the target branch it
  # would otherwise name is the base, never a rebase round's work (#1344).
  ff=
  for c in $ch; do
    c=$(/usr/bin/git -C "$tg" rev-parse -q --verify "$c^{commit}" 2>/dev/null) || continue
    [ "$c" = "$old" ] && continue
    [ -n "$ff" ] || ff=$c
  done
  for c in $ch "$head"; do
    c=$(/usr/bin/git -C "$tg" rev-parse -q --verify "$c^{commit}" 2>/dev/null) || continue
    [ "$c" = "$old" ] && continue
    /usr/bin/git -C "$tg" merge-base --is-ancestor "$old" "$c" && { head=$c; break; }
  done
  # A kept rebase round (#1344) rewrites its own branch, so its head is never a fast-forward of the reviewed tip and the
  # ordinary rule would drop it. Its head is the branch's new first pass head, and only when it carries a commit past the ref
  # the round rebased on: a head that adds nothing is no round.
  kre=$(head -1 "$kept" 2>/dev/null)
  [ -s "$kept" ] && [ -n "$ff" ] && [ -n "$kre" ] && [ "$(/usr/bin/git -C "$tg" rev-list --count "$kre..$ff" 2>/dev/null)" != 0 ] && head=$ff
  # (#2345) A round that pushes nothing is no fix round for the tip it was dispatched for. Its once-per-tip marker goes, so the
  # next pass dispatches a fresh round at the same tip: the tip never moves on its own, and a marker that stays freezes the
  # branch with no stop and no alert. The stall rule below bounds those retries. A kept landing round keeps its marker,
  # because branchland.sh reads it (#1960).
  if [ "$old" = "$head" ]; then echo "BRANCHFIX PUSH $br: run $run left no new commit, so no round is pushed"
    [ -s "$kept" ] || rm -f "$A/branchfix-$kx-${dsha:0:9}"
  elif [ -s "$kept" ] && [ -z "$ff" ]; then echo "BRANCHFIX PUSH $br: run $run added no head of its own, so no round is pushed"
  elif [ -s "$kept" ] && ! /usr/bin/git -C "$tg" merge-base --is-ancestor "$old" "$head"; then
    # The lease is on the sha the round was dispatched for, never on what the branch holds now: a writer that pushed after the
    # review is not clobbered, and the round is refused instead (#1344).
    if [ "$old" != "$dsha" ]; then echo "BRANCHFIX PUSH $br: the branch moved past the reviewed sha ${dsha:0:9}; the round of run $run is not pushed"; continue
    elif /usr/bin/git -C "$tg" push -q --force-with-lease="refs/heads/$br:$dsha" "$remote" "$head:refs/heads/$br" 2>/dev/null
    then echo "BRANCHFIX PUSH $br: ${old:0:9} -> ${head:0:9} (rebase round, run $run)"
    else echo "BRANCHFIX PUSH $br: the push of run $run was refused; the next pass retries"; continue; fi
  elif ! /usr/bin/git -C "$tg" merge-base --is-ancestor "$old" "$head"; then
    echo "BRANCHFIX PUSH $br: the head ${head:0:9} of run $run is no fast-forward of the branch tip ${old:0:9}, so it is not pushed"
    [ -s "$kept" ] || rm -f "$A/branchfix-$kx-${dsha:0:9}"
  elif /usr/bin/git -C "$tg" push -q "$remote" "$head:refs/heads/$br" 2>/dev/null; then echo "BRANCHFIX PUSH $br: ${old:0:9} -> ${head:0:9} (run $run)"
  else echo "BRANCHFIX PUSH $br: the push of run $run was refused; the next pass retries"; continue; fi
  rm -f "$m"
done
# An issue that carries a label the frontier skips (SG_FRONTIER_SKIP_LABELS: frozen:owner, needs-ratification, spec, ...) gets no fix
# run. The labels come from the open-issue list sg_work_branches already read, so this costs no call.
held=" $(printf '%s' "$wb_issues" | jq -r --arg s "${SG_FRONTIER_SKIP_LABELS:-}" '($s|split(",")|map(select(.!=""))) as $k
  | .[] | select([.labels[]?.name] | any(. as $l | $k | index($l))) | .number' 2>/dev/null | tr '\n' ' ') "
# The loop-bug labels come from the same open-issue list; the LOOP STOP reader (gh_loop_stop, ghcall.sh) reads that list too and
# logs its line as every pass does. Neither reads GitHub again.
bugs=" $(printf '%s' "$wb_issues" | jq -r '.[] | select([.labels[]?.name] | any(startswith("loop-bug"))) | .number' 2>/dev/null | tr '\n' ' ') "
lsf=$(mktemp); printf '%s' "$wb_issues" > "$lsf"; lstop=$(gh_loop_stop "$lsf"); rm -f "$lsf"
[ -n "$lstop" ] && echo "$lstop"
# The fingerprint of a review's findings and the failed rounds in a row are revformat.py's reading (ADR-0134), so a
# child fix round asks the same rule: a moved line or a new sha is not a change.
# (#2381) A live hand hold (squad.handhold: a person or HAL9k works the card) starts no fix round, so no run commits beside the
# hand work. Only a hold inside its `until` counts: `holds --live` drops a stale one. With hold files present, a read that fails
# holds every round instead of dispatching beside hand work it cannot see.
hh=; hherr=
if ls "$A"/hold-[0-9]* > /dev/null 2>&1; then
  py=${SG_SQUAD:+$SG_SQUAD/.venv/bin/python}; [ -n "$py" ] && [ -x "$py" ] || py=python3
  hh=$(PYTHONPATH="${SG_SQUAD:-$here/../../..}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.handhold holds --live 2>/dev/null) || hherr=1
fi
done=0
while read -r sha br i; do
  [ -n "$br" ] && [ "$done" -lt "$max" ] || continue
  case "$held" in *" $i "*) continue;; esac
  key=$(printf '%s' "$br" | tr '/' '_'); s9=${sha:0:9}; f="$SG_STATE/review-$key.md"
  [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ] || continue
  head -1 "$f" 2>/dev/null | grep -q CHANGES || continue
  # a CHANGES with no Blocking finding (debt only) is an approval: no fix round, as the loop's own passes treat it (revformat.py count)
  python3 "$here/revformat.py" --summary "$f" 2>/dev/null | grep -q '^blocking=0 ' && continue
  [ -e "$A/branchfix-$key-$s9" ] && continue
  if [ -n "$hherr" ]; then
    [ -e "$A/branchfix-skip-hold-$key-unread" ] || { echo "BRANCHFIX SKIP $br: the hand holds could not be read, no fix round starts"; : > "$A/branchfix-skip-hold-$key-unread"; }
    continue
  fi
  rm -f "$A/branchfix-skip-hold-$key-unread"
  hl=$(printf '%s\n' "$hh" | /usr/bin/grep "^#$i ")
  if [ -n "$hl" ]; then   # "#<n> <carrier> since <iso> until <iso>": since survives a renew, so the line is said once per hold
    set -- $hl
    [ -e "$A/branchfix-skip-hold-$key-$4" ] || { echo "BRANCHFIX SKIP $br: issue #$i has a live hand hold ($2 until $6), no fix round starts"; : > "$A/branchfix-skip-hold-$key-$4"; }
    continue
  fi
  why=; case "$bugs" in *" $i "*) why="loop-bug, fixed outside the loop";; esac
  [ -n "$why" ] || [ -z "$lstop" ] || why="LOOP STOP holds, fixed outside the loop"
  # (#1789) A paused loop (auto/off: an owner pause, or a stop or restart drain) starts no run, as autobug and autofrontier;
  # the push back above still runs, so a round that ends under the pause still pushes its head.
  [ -n "$why" ] || [ ! -e "$A/off" ] || why="the loop is paused (auto/off), no run starts"
  if [ -n "$why" ]; then   # a skip dispatches nothing and keeps no fix marker: the tip is dispatched when the label or the stop lifts
    [ -e "$A/branchfix-skip-$key-$s9" ] || { echo "BRANCHFIX SKIP $br: $why"; : > "$A/branchfix-skip-$key-$s9"; }
    continue
  fi
  fp=$(python3 "$here/revformat.py" --fingerprint "$f"); same=$(python3 "$here/revformat.py" --stall "$A" branchfix "$key" "$fp")
  if [ "$same" -ge "$cap" ]; then
    [ "$(cat "$A/branchfix-stop-$key" 2>/dev/null)" = "$fp" ] && continue
    printf 'Status update — branch %s @ %s is stalled: %s fix rounds left the same review findings, so the loop stopped its fix rounds (ADR-0134). The card is frozen for the owner (frozen:owner). A push with other findings restarts the rounds.\n' "$br" "$s9" "$same" > "$(sg_cq_status "$i" "branchfix$$")"
    o=$(gh_send --method POST "repos/$SG_REPO/issues/$i/labels" -f "labels[]=frozen:owner") \
      || { echo "BRANCHFIX STOP $br: the label write failed, the next pass retries: $o"; continue; }
    echo "$fp" > "$A/branchfix-stop-$key"
    echo "BRANCHFIX STOP $br @ $s9 (issue #$i): stalled after $same fix rounds with no change; card frozen for the owner"; continue
  fi
  b=$(mktemp)
  { echo "IMPORTANT: your first and only action is to return the \"spawns\" list in your final output, one entry for the child below; ending the turn without that list fails the run. No tool call makes a child: the run reads the list. The child's spend: $(bash "$here/envelope-spend.sh" 2>/dev/null)."
    bash "$here/branchfix.sh" "$br" "$i"; } > "$b"
  done=$((done+1))
  if ! req=$(bash "$here/mkreq.sh" "auto-branchfix-$key-$s9" "$b"); then rm -f "$b"; echo "BRANCHFIX $br @ $s9 refused by mkreq.sh; the next pass retries" >&2; continue; fi
  rm -f "$b"
  # A refusal is no dispatch (#812): launch.py exits non-zero when it refuses the start, so the pass says launch.py's own last
  # line instead of swallowing it, writes no fix marker and lets the next pass retry the tip.
  lf=$(mktemp)
  # A fix round repairs the work branch, and its child commits on that branch, never in the target: SG_FIX_RUN marks the run so
  # the final gate proves every acceptance command in the child's tree (#1374).
  if ! (cd "$SG_CHECKOUT" && SG_FIX_RUN=1 python3 "$here/launch.py" "$req") >"$lf" 2>&1; then
    echo "BRANCHFIX $br @ $s9 refused by launch.py: $(tail -1 "$lf"); the next pass retries" >&2; rm -f "$lf"; continue
  fi
  run=$(/usr/bin/grep -E '^run-[A-Za-z0-9._-]+$' "$lf" | tail -1); rm -f "$lf"
  [ -n "$run" ] || { echo "BRANCHFIX $br @ $s9: launch.py printed no run id, so no fix run started; the next pass retries" >&2; continue; }
  /usr/bin/git -C "$SG_CHECKOUT" checkout -q -- docs/squad/workflows/dynamic.graph.json 2>/dev/null   # launch.py renames the graph root in place
  # The push marker goes first (#1960): branchland reads a dispatch marker with no push marker as a round that ended.
  echo "$br $sha" > "$A/branchfix-push-$run"; echo "$run" > "$A/branchfix-$key-$s9";python3 "$here/revformat.py" --record-stall "$A" branchfix "$key" "$fp" "$same"; rm -f "$A/branchfix-stop-$key"
  find "$A" -maxdepth 1 -name "branchfix-$key-?????????" ! -name "*-$s9" -delete 2>/dev/null
  echo "BRANCHFIX $br @ $s9 (issue #$i): fix round dispatched ($same failed rounds with no change before it) -> $run"
done <<EOT
$wb_list
EOT
exit 0
