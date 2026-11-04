#!/bin/bash
# usage: branchfixpass.sh   one pass over the pushed work branches: one fix run for each reviewed tip whose review says CHANGES (#401).
#
# branchreviewpass.sh keeps the verdict of each tip in $SG_STATE/review-<key>.{md,sha}. This pass lists the work branches as the
# review pass does (workbranches.sh) and, for each branch whose kept sha is its tip and whose verdict is CHANGES, dispatches one fix
# run on the same branch: the request is branchfix.sh's text, sent through mkreq.sh and launch.py as every pass sends a run.
# The fix run pushes a new tip; the review pass reviews it; an APPROVE lets branchland.sh land it.
# Once per reviewed tip: auto/branchfix-<key>-<sha9> holds the run id. A launch that starts no run keeps no marker, so the next pass
# retries the tip.
# Stop (ADR-0134): a round fails when the review of the next tip has the same findings (line numbers, shas and whitespace removed).
# auto/branchfix-fp-<key> keeps the findings of the last dispatched round and auto/branchfix-same-<key> the count of failed rounds in
# a row. At SG_BRANCHFIX_STALLS (default 3) the rounds stop: one status line on the issue and the label frozen:owner, once. Changed
# findings restart the count. At most SG_FIX_PASS_MAX fix runs (default 3) start per pass. watch.sh runs this under single.sh.
# A loop defect is fixed outside the loop (ADR-0146, #841): no fix run for a branch whose issue carries `loop-bug` or a `loop-bug:*`
# label, and none at all while LOOP STOP holds. Each such tip gets one BRANCHFIX SKIP line (auto/branchfix-skip-<key>-<sha9>).
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=branchfix
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/workbranches.sh"
remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; max=${SG_FIX_PASS_MAX:-3}; cap=${SG_BRANCHFIX_STALLS:-3}
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
  run=${m##*/branchfix-push-}; read -r br _ < "$m"
  rd=$(/usr/bin/grep "/$run\$" "$SG_STATE/active-runs.txt" 2>/dev/null | tail -1); rd=${rd:-$SG_CHECKOUT/runs/$run}
  t=$(python3 -c 'import json,sys
s=v=None; h=[]
for l in open(sys.argv[1]):
    try: d=json.loads(l)
    except Exception: continue
    if d.get("event") in ("run_start","resume"): v=None; s=d if d.get("event")=="run_start" else s
    elif d.get("event")=="verdict": v=d
    elif d.get("event")=="outcome" and d.get("outcome")=="pass" and (d.get("workspace") or {}).get("head"): h.append(d["workspace"]["head"])
if s and v and s.get("target") and s.get("target_branch"): print(s["target"], s["target_branch"], *reversed(h))' "$rd/record.jsonl" 2>/dev/null)
  [ -n "$t" ] || continue
  read -r tg tb ch <<< "$t"
  # The head (#779): on a work branch the land into the target branch can be a no-op, so that branch stays at the base. The
  # child's own heads (pass outcomes' workspace heads, last first) come first, then the target branch: the first one that is a
  # fast-forward past the work branch is pushed.
  head=$(/usr/bin/git -C "$tg" rev-parse -q --verify "refs/heads/$tb^{commit}" 2>/dev/null) \
    && /usr/bin/git -C "$tg" fetch -q "$remote" "refs/heads/$br" 2>/dev/null || { echo "BRANCHFIX PUSH $br: the head of run $run is not readable; the next pass retries"; continue; }
  old=$(/usr/bin/git -C "$tg" rev-parse FETCH_HEAD)
  ff=
  for c in $ch "$head"; do
    c=$(/usr/bin/git -C "$tg" rev-parse -q --verify "$c^{commit}" 2>/dev/null) || continue
    [ "$c" = "$old" ] && continue
    [ -n "$ff" ] || ff=$c
    /usr/bin/git -C "$tg" merge-base --is-ancestor "$old" "$c" && { head=$c; break; }
  done
  # A kept rebase round (#1344) rewrites its own branch, so its head is never a fast-forward of the reviewed tip and the
  # ordinary rule would drop it. The kept marker names the ref the round rebased on; its head is the branch's new first head.
  kept="$SG_STATE/auto/kept-$(printf '%s' "$br" | tr / _)"; [ -s "$kept" ] && [ -n "$ff" ] && head=$ff
  if [ "$old" = "$head" ]; then echo "BRANCHFIX PUSH $br: run $run left no new commit"
  elif [ -s "$kept" ] && ! /usr/bin/git -C "$tg" merge-base --is-ancestor "$old" "$head"; then
    if /usr/bin/git -C "$tg" push -q --force-with-lease="refs/heads/$br:$old" "$remote" "$head:refs/heads/$br" 2>/dev/null
    then echo "BRANCHFIX PUSH $br: ${old:0:9} -> ${head:0:9} (rebase round, run $run)"
    else echo "BRANCHFIX PUSH $br: the push of run $run was refused; the next pass retries"; continue; fi
  elif ! /usr/bin/git -C "$tg" merge-base --is-ancestor "$old" "$head"; then echo "BRANCHFIX PUSH $br: the head of run $run is no fast-forward of the branch, so it is not pushed"
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
  why=; case "$bugs" in *" $i "*) why="loop-bug, fixed outside the loop";; esac
  [ -n "$why" ] || [ -z "$lstop" ] || why="LOOP STOP holds, fixed outside the loop"
  if [ -n "$why" ]; then   # a skip dispatches nothing and keeps no fix marker: the tip is dispatched when the label or the stop lifts
    [ -e "$A/branchfix-skip-$key-$s9" ] || { echo "BRANCHFIX SKIP $br: $why"; : > "$A/branchfix-skip-$key-$s9"; }
    continue
  fi
  fp=$(python3 "$here/revformat.py" --fingerprint "$f"); same=$(python3 "$here/revformat.py" --stall "$A" branchfix "$key" "$fp")
  if [ "$same" -ge "$cap" ]; then
    [ "$(cat "$A/branchfix-stop-$key" 2>/dev/null)" = "$fp" ] && continue
    printf 'Status update — branch %s @ %s: %s fix rounds left the same review findings, so the loop stopped its fix rounds (ADR-0134). The card is frozen for the owner (frozen:owner). A push with other findings restarts the rounds.\n' "$br" "$s9" "$same" > "$(sg_cq_status "$i" "branchfix$$")"
    o=$(gh_send --method POST "repos/$SG_REPO/issues/$i/labels" -f "labels[]=frozen:owner") \
      || { echo "BRANCHFIX STOP $br: the label write failed, the next pass retries: $o"; continue; }
    echo "$fp" > "$A/branchfix-stop-$key"
    echo "BRANCHFIX STOP $br @ $s9 (issue #$i): $same fix rounds with no change; card frozen for the owner"; continue
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
  if ! (cd "$SG_CHECKOUT" && python3 "$here/launch.py" "$req") >"$lf" 2>&1; then
    echo "BRANCHFIX $br @ $s9 refused by launch.py: $(tail -1 "$lf"); the next pass retries" >&2; rm -f "$lf"; continue
  fi
  run=$(/usr/bin/grep -E '^run-[A-Za-z0-9._-]+$' "$lf" | tail -1); rm -f "$lf"
  [ -n "$run" ] || { echo "BRANCHFIX $br @ $s9: launch.py printed no run id, so no fix run started; the next pass retries" >&2; continue; }
  /usr/bin/git -C "$SG_CHECKOUT" checkout -q -- docs/squad/workflows/dynamic.graph.json 2>/dev/null   # launch.py renames the graph root in place
  echo "$run" > "$A/branchfix-$key-$s9"; echo "$br $sha" > "$A/branchfix-push-$run";python3 "$here/revformat.py" --record-stall "$A" branchfix "$key" "$fp" "$same"; rm -f "$A/branchfix-stop-$key"
  find "$A" -maxdepth 1 -name "branchfix-$key-?????????" ! -name "*-$s9" -delete 2>/dev/null
  echo "BRANCHFIX $br @ $s9 (issue #$i): fix round dispatched ($same failed rounds with no change before it) -> $run"
done <<EOT
$wb_list
EOT
exit 0
