#!/bin/bash
# seconds: 31.23
# Regression test for childcc.py (stub gh + board, no network). usage: bash test-childcc.sh [path/to/childcc.py]
# Live pass moves at once; a finished run is backfilled only when the card is Todo/In Progress/Blocked, never past Code Complete.
unset SG_BOARD_VOCAB SG_CHILDCC_BUDGET   # the old-vocabulary cases and the one-child passes need them unset; a caller that exports them must not turn the suite red
src=${1:-$(dirname "$0")/childcc.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/childcc.py"; cp "$(dirname "$src")/issue_ledger.py" "$(dirname "$src")/issue_state.py" "$(dirname "$src")/child_issues.py" "$(dirname "$src")/ghread.py" "$(dirname "$src")/drainpolicy.py" "$(dirname "$src")/revformat.py" "$(dirname "$src")/board_columns.py" "$(dirname "$src")/render_owed.py" "$T/"
printf '#!/bin/bash\necho "$@" >> %s/moves\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
a="$*"
case $a in
  *"graphql"*) if [[ $a =~ i([0-9]+):\ issue\(number ]] && [ -e "$GH_BODIES/body-${BASH_REMATCH[1]}" ]; then   # one issue's body, which the render check reads (#2833)
      python3 -c 'import json,sys; print(json.dumps({"data": {"repository": {"i" + sys.argv[1]: {"number": int(sys.argv[1]), "state": "OPEN", "body": open(sys.argv[2]).read()}}}}))' "${BASH_REMATCH[1]}" "$GH_BODIES/body-${BASH_REMATCH[1]}"
    else cat "$GH_ISSUES_OPEN"; fi;;   # the open-issue list the pass reads once for a loop defect (#596)
  *"-q .state"*) [ -e "$GH_STATE_FAIL" ] && { echo "HTTP 403: rate limit" >&2; exit 1; }; echo "${GH_STATE:-open}";;   # a failed read is no answer (#848)
  *"POST"*"/sub_issues"*) echo "link $a $(cat)" >> "$GH_LOG";;   # the body names the child by its id, which is its number here
  *"/sub_issues"*) sed -n 's/^link .*"sub_issue_id": *\([0-9]*\).*/\1/p' "$GH_LOG" 2>/dev/null;;   # the sub-issues read carries each child linked so far: a read that never carried a link cost the backoff, 7 s a pass (#597)
  *"POST"*"repos/x/y/issues"*) echo "create $(cat)" >> "$GH_LOG"; echo '{"number":900}';;
  *"issues?state=all"*) ;;
  *"repos/x/y/issues/"*) [[ $a =~ issues/([0-9]+) ]]; echo "{\"id\":${BASH_REMATCH[1]}}";;   # an issue's id is its number, so a link names its child
  *"/fields?"*) echo 7;;
  *"/items?"*) for kv in $GH_NUMS; do printf '%s\t%s\n' "${kv%%:*}" "${kv#*:}"; done | tr '_' ' ';;   # the one Status read: number, the state the card carries
esac
GH
chmod +x "$T/bin/gh"
run() { # name issue verdict
  mkdir -p "$T/co/runs/$1"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #'$2'"}'; echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'; [ "$3" = v ] && echo '{"event": "verdict"}'; } > "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
# name child definition-json-string outcome
run_def() {
  mkdir -p "$T/co/runs/$1"
  python3 -c 'import json,sys; n,c,d,o=sys.argv[1:5]; open(sys.argv[5],"w").write(json.dumps({"event":"spawn_accepted","child":c,"definition":d})+"\n"+json.dumps({"event":"outcome","node":c,"outcome":o,"reasons":["ok"]})+"\n")' "$1" "$2" "$3" "$4" "$T/co/runs/$1/record.jsonl"
  echo "$1" >> "$T/st/active-runs.txt"
}
run_two() { # name: a run of two children, issues 2000 and 2001, and a run of one child, issue 2002
  mkdir -p "$T/co/runs/$1"; { echo '{"event": "spawn_accepted", "child": "a", "definition": "GitHub issue #2000"}'; echo '{"event": "spawn_accepted", "child": "b", "definition": "GitHub issue #2001"}'; } > "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
run live 1 n; run_two two; run_two fin-two; echo '{"event": "verdict"}' >> "$T/co/runs/fin-two/record.jsonl"; run solo 2002 n; run fin-todo 2 v; run fin-testing 3 v; run fin-prog 4 v
run_def nohash c "FORK-ONLY work for GitHub issue 1487 on the agent-squad local fork." pass
run_def issues c "FORK-ONLY work for GitHub issues 1491 on the fork." pass
run_def branch c "Create the worktree: git worktree add ~/wt-as-1492 -b gui/1492 local/next" pass
run_def hashfirst c "GitHub issue #1500 and GitHub issue 1487" pass
run_def unmapped c "a child whose prompt names no issue at all" pass
run_def refs c "Every commit message carries the line \`Refs #1493\` on its own line. GOAL (issue 1493, SPEC 9 child): fix it." pass
run_def cluster c "CHILD — GitHub issue(s) 5201, 5202, 5203 (work in the worktree the run gives you)." pass
# (#2276) A child maps only to an issue its run was dispatched for: a prompt that cites another issue first never decides it.
req() { printf 'CHILD — GitHub issue(s) %s (bug; one root cause, one branch):\n' "$2" > "$T/co/runs/$1/request.txt"; }
run_def cited c "Fix two bugs. The fix of the red itself is issue #5399. Commit with Refs #5301 and Refs #5302." pass; req cited "5301, 5302"
run_def citedown c "GitHub issue #5311. The body says the old fix is issue #5398." pass; req citedown "5311, 5312"
run_def citednone c "Fix the bug the ticket describes; issue #5397 is its history." pass; req citednone "5321"
printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}' > "$T/issues-open.json"
export SG_SQUAD=$(cd "$(dirname "$src")/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a review takes a review slot (caps.reviews): a throwaway budget, this repository's squad.caps
export GH_BODIES=$T GH_LOG=$T/gh.log GH_ISSUES_OPEN=$T/issues-open.json; export GH_STATE_FAIL=$T/gh.statefail SG_STATE=$T/st SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 PATH="$T/bin:$PATH" GH_NUMS="2:Todo 3:Closed 4:In_Progress 5101:Todo 5104:Code_Complete 5105:Doing 5106:Doing 5107:Doing"
cp "$T/childcc.py" "$T/childcc.py.run"; sed -i.bak "s#^board = .*#board = '$T/board.sh'#" "$T/childcc.py.run"
out=$(cd "$T/co" && python3 "$T/childcc.py.run" 2>&1)
echo "$out" | tail -20 >&2
m=$(cat "$T/moves" 2>/dev/null)
echo "$m" | /usr/bin/grep -q ' 1$' && ok "live pass: moved" || ko "live pass not moved"
echo "$m" | /usr/bin/grep -q ' 2$' && ok "finished run, card Todo: backfilled" || ko "Todo card not backfilled"
echo "$m" | /usr/bin/grep -q ' 4$' && ok "finished run, card In Progress: backfilled" || ko "In Progress card not backfilled"
echo "$m" | /usr/bin/grep -q ' 3$' && ko "card past Code Complete was moved" || ok "finished run, card Closed: left alone"
echo "$m" | /usr/bin/grep -q ' 1487$' && ok "GitHub issue N (no hash): moved" || ko "GitHub issue N (no hash) not moved"
ls "$T/st/cq"/*#1487-cc-*.md >/dev/null 2>&1 && ok "GitHub issue N (no hash): comment queued" || ko "GitHub issue N (no hash): no comment"
echo "$m" | /usr/bin/grep -q ' 1491$' && ok "GitHub issues N: moved" || ko "GitHub issues N not moved"
echo "$m" | /usr/bin/grep -q ' 1492$' && ok "gui/N / wt-as-N: moved" || ko "gui/N / wt-as-N not moved"
for c in 5201 5202 5203; do echo "$m" | /usr/bin/grep -q " $c$" && ok "a cluster child moves #$c" || ko "a cluster child did not move #$c"; done
echo "$m" | /usr/bin/grep -q ' 1493$' && ok "Refs #N, the dispatchers' own link: moved (#555)" || ko "Refs #N not moved: the router wrote issue N with no hash"
echo "$m" | /usr/bin/grep -q ' 1500$' && ok "hash pattern still wins first" || ko "hash pattern lost first-match"
echo "$m" | /usr/bin/grep -q ' 5301$' && ok "a prompt that cites a foreign issue first maps to the run's issue it names (#2276)" || ko "the cited child did not move #5301: $m"
echo "$m" | /usr/bin/grep -q ' 53[9][0-9]$' && ko "a cited foreign issue was moved: $m" || ok "a cited foreign issue moves no card (#2276)"
echo "$out" | /usr/bin/grep -c 'CHILDCC REMAP cited c: its prompt names #5399 first' | /usr/bin/grep -qx 1 && ok "the remap is reported once, with the reference read (#2276)" || ko "the remap line is missing or repeated: $out"
echo "$m" | /usr/bin/grep -q ' 5311$' && ok "a prompt whose first reference is a run issue keeps it (#2276)" || ko "the unchanged path lost #5311: $m"
echo "$out" | /usr/bin/grep -q 'CHILDCC REMAP citedown' && ko "the unchanged path was reported as a remap" || ok "the unchanged path reports no remap (#2276)"
echo "$m" | /usr/bin/grep -q ' 5321$' && ok "a prompt that names no run issue maps to the run's own issue (#2276)" || ko "the child with no run reference did not move #5321: $m"
echo "$out" | /usr/bin/grep -q 'CHILDCC UNMAPPED unmapped c' && ok "unmapped child logged once" || ko "unmapped child not logged: $out"
echo "$out" | /usr/bin/grep -c 'CHILDCC UNMAPPED unmapped c' | /usr/bin/grep -qx 1 && ok "unmapped child logged once only" || ko "unmapped child logged more than once"
# No run parent (#559): a run of two children creates no issue and links no sub-issue; the run's summary rides the janitor's terminal comment.
[ ! -s "$T/gh.log" ] || ! grep -q '^create' "$T/gh.log" && ok "two children: no issue is created" || ko "creates: $(cat "$T/gh.log" 2>/dev/null)"
! grep -q '^link' "$T/gh.log" 2>/dev/null && ok "two children: no child is linked as a sub-issue" || ko "links: $(cat "$T/gh.log" 2>/dev/null)"
echo "$out" | /usr/bin/grep -q 'RUN PARENT' && ko "a run parent is reported: $out" || ok "no pass reports a run parent"

# A run with a wave (#375): the entry is wave.sh's, stubbed here. A child that its gates refused is tried again only on a new
# HEAD, and a run whose wave landed (wave-<run> gone, waved-<run> kept) never moves a child's card to Code Complete.
cat > "$T/wave.sh" <<'W'
#!/bin/bash
echo "$*" >> "$WAVE_CALLS"
[ "$1" = expired ] && { [ -e "$WAVE_CALLS.expired-$2" ]; exit; }   # the age cap: a case marks the run's wave expired (#620)
[ "$1" = requeue ] && { echo "WAVE REQUEUE #$2: $3"; exit 0; }
[ -e "$WAVE_FAIL" ] && { echo "WAVE REQUEUE #$3: the local gates failed: x"; exit 1; }
[ "$3" = 5300 ] && { echo "WAVE HOLD #$3: no review yet"; exit 1; }   # a held child: no requeue, so every pass tries it again
[ "$3" = 5600 ] && { echo "WAVE STOP #$3: requeued 3 times, so it is not dispatched again: x"; exit 1; }   # the requeue cap stopped it
[ "$3" = 5400 ] && { echo "WAVE HOLD #$3: the rebase on wave/kpt conflicts: a.txt; its commits are kept on fix/5400-rebase-kpt for one rebase round, not requeued"; exit 1; }   # a child kept on a rebase round (#1344)
echo "WAVE gui/$3 entered (#$3)"
W
export WAVE_CALLS=$T/wave.calls WAVE_FAIL=$T/wave.fail WAVE_GATE_HOLD=$T/wave.gatehold WAVE_REQUEUE_FAIL=$T/wave.requeuefail WAVE_REQUEUE_ERR=$T/wave.requeueerr WAVE_NEEDS_HUMAN=$T/wave.needshuman; : > "$WAVE_CALLS"
lq() { # issue: the head the run requeued it on, from its ledger (the old auto/requeued- marker, ADR-0149)
  python3 "$T/issue_ledger.py" requeued "x/y#$1" "$2" 2>/dev/null; }
lqset() { python3 "$T/issue_ledger.py" requeue "x/y#$1" "$2" "$3" >/dev/null 2>&1; }
wtree() { # run [child]: the child's worktree, a git repo with one commit
  local d="$T/co/runs/$1/ws/target-${2:-c}"; mkdir -p "$d"; /usr/bin/git -C "$d" init -q . && /usr/bin/git -C "$d" -c user.email=a@b -c user.name=t commit -q --allow-empty -m one; }
pass() { : > "$T/st/active-runs.txt"; echo "$T/co/runs/$1" >> "$T/st/active-runs.txt"; (cd "$T/co" && python3 "$T/childcc.py.run" 2>&1); }
run wv 5100 n; wtree wv; echo wave/wv > "$T/st/auto/wave-wv"; echo wave/wv > "$T/st/auto/waved-wv"; : > "$WAVE_FAIL"
pass wv; pass wv; pass wv
[ "$(grep -c '^enter wv 5100' "$WAVE_CALLS")" = 1 ] && ok "a refused child is not entered again on the same HEAD (gates and requeue run once)" || ko "enter calls for the refused child: $(cat "$WAVE_CALLS")"
/usr/bin/git -C "$T/co/runs/wv/ws/target-c" -c user.email=a@b -c user.name=t commit -q --allow-empty -m two
pass wv >/dev/null
[ "$(grep -c '^enter wv 5100' "$WAVE_CALLS")" = 2 ] && ok "a new commit on the refused child is tried again" || ko "enter calls after a new HEAD: $(cat "$WAVE_CALLS")"
rm -f "$WAVE_FAIL"; /usr/bin/git -C "$T/co/runs/wv/ws/target-c" -c user.email=a@b -c user.name=t commit -q --allow-empty -m three
pass wv >/dev/null
[ "$(grep -c '^enter wv 5100' "$WAVE_CALLS")" = 3 ] && [ -e "$T/st/auto/cc-wv-c" ] && ok "a child that then enters gets its marker" || ko "no entry marker: $(cat "$WAVE_CALLS")"
# the wave landed: wave-<run> is gone, waved-<run> stays, and a child that never entered is not moved to Code Complete
run lnd 5101 v; wtree lnd; echo wave/lnd > "$T/st/auto/waved-lnd"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(pass lnd)
[ ! -s "$WAVE_CALLS" ] && ok "a run whose wave landed enters no child" || ko "enter ran for a landed wave: $(cat "$WAVE_CALLS")"
grep -q ' 5101$' "$T/moves" 2>/dev/null && ko "a child that never entered reads Code Complete after its wave landed" || ok "a child that never entered is never moved to Code Complete after its wave landed"
# A child-request run whose wave cut failed has no wave marker (launch.py starts it anyway). Its child's commit is merged into the
# run's own branch and no entry can follow: the child reads Code Complete with no wave in it (ADR-0145 rule 12). It is named instead.
run cut 5102 n; printf 'CHILD - GitHub issue #5102\n' > "$T/co/runs/cut/request.txt"; rm -f "$T/moves"; : > "$WAVE_CALLS"
out=$(pass cut)
grep -q ' 5102$' "$T/moves" 2>/dev/null && ko "a child of a run with no wave reads Code Complete: $out" || ok "a child of a run with no wave is not moved to Code Complete"
echo "$out" | /usr/bin/grep -q 'WAVE-SALVAGE #5102' && ok "a child of a run with no wave is named on a salvage line" || ko "no salvage line for the child: $out"
# A finished run that cut no wave (#1349): its child's commits reach no remote branch, so they are salvaged: the child's HEAD is
# pushed to fix/<n>-salvage-<run> on its origin, for review and landing, whatever the card says. A child with nothing past
# origin/dev is requeued with the reason. A failed push leaves no marker, so the next pass tries it again.
mk_origin() { # run [ahead]: the run's child worktree gets a bare origin whose dev names its HEAD; one more commit when ahead is 1
  local w="$T/co/runs/$1/ws/target-c" o="$T/origin-$1.git"
  /usr/bin/git init -q --bare "$o"; /usr/bin/git -C "$w" remote add origin "$o"
  /usr/bin/git -C "$w" push -q origin HEAD:refs/heads/dev
  [ "${2:-1}" = 1 ] && /usr/bin/git -C "$w" -c user.email=a@b -c user.name=t commit -q --allow-empty -m "child work"; }
salv_branch() { /usr/bin/git -C "$T/origin-$1.git" rev-parse --verify -q "refs/heads/fix/$2-salvage-$1" >/dev/null && echo yes || echo no; }
for sp in "nw 5104" "dg 5105"; do set -- $sp   # a Code Complete card and a Doing card, both finished with no wave
  run $1 $2 v; mkdir -p "$T/co/runs/$1"; printf 'CHILD — GitHub issue(s) %s (work in the worktree the run gives you)\n' "$2" > "$T/co/runs/$1/request.txt"
  wtree $1; mk_origin $1; : > "$WAVE_CALLS"; rm -f "$T/moves"
  out=$(pass $1)
  sha=$(/usr/bin/git -C "$T/co/runs/$1/ws/target-c" rev-parse HEAD)
  [ "$(salv_branch $1 $2)" = yes ] && [ "$(/usr/bin/git -C "$T/origin-$1.git" rev-parse "refs/heads/fix/$2-salvage-$1")" = "$sha" ] \
    && ok "a finished no-wave child (#$2) is pushed to its salvage branch" || ko "no salvage branch for #$2: $out"
  echo "$out" | /usr/bin/grep -q "CHILD NO-WAVE SALVAGE #$2 ($1 c): pushed ${sha:0:9} to fix/$2-salvage-$1" && ok "the salvage line names the sha and the branch (#$2)" || ko "no salvage line for #$2: $out"
  [ -e "$T/st/auto/cc-$1-c" ] && ok "a salvaged child gets its marker (#$2)" || ko "no marker after the salvage (#$2)"
  [ "$(cat "$T/st/auto/salvaged-fix_$2-salvage-$1" 2>/dev/null)" = "$sha" ] && ok "2571 the salvage push writes the provenance marker with its head (#$2)" || ko "2571 no provenance marker for #$2: '$(ls "$T/st/auto")'"
  [ ! -s "$WAVE_CALLS" ] && ok "a salvaged child is not entered or requeued (#$2)" || ko "wave calls for a salvaged child: $(cat "$WAVE_CALLS")"
  [ -e "$T/moves" ] && grep -q " $2\$" "$T/moves" && ko "a salvaged child moved its card (#$2)" || ok "a salvaged child moves no card (#$2)"
done
# A push that fails is no marker and no requeue: the child is tried again on the next pass (#1349).
run pf 5106 v; mkdir -p "$T/co/runs/pf"; printf 'CHILD — GitHub issue(s) 5106 (work in the worktree the run gives you)\n' > "$T/co/runs/pf/request.txt"
wtree pf; mk_origin pf; /usr/bin/git -C "$T/co/runs/pf/ws/target-c" remote set-url origin "$T/gone-pf.git"; : > "$WAVE_CALLS"
out=$(pass pf)
echo "$out" | /usr/bin/grep -q "CHILD NO-WAVE SALVAGE FAILED #5106" && ok "a failed salvage push is named" || ko "failed push not named: $out"
[ ! -e "$T/st/auto/cc-pf-c" ] && ok "a failed salvage push writes no done marker" || ko "marker after a failed push"
! /usr/bin/grep -q '^requeue 5106' "$WAVE_CALLS" && ok "a failed salvage push is not requeued" || ko "requeued on a failed push: $(cat "$WAVE_CALLS")"
[ ! -e "$T/st/auto/salvaged-fix_5106-salvage-pf" ] && ok "2571 a failed salvage push writes no provenance marker" || ko "2571 a provenance marker with no push"
# A no-wave child with nothing past origin/dev has nothing to salvage: it is requeued with the reason, and the requeue is
# loop-caused (wave.sh loop_caused), so it does not count toward the issue's cap.
run nq 5107 v; mkdir -p "$T/co/runs/nq"; printf 'CHILD — GitHub issue(s) 5107 (work in the worktree the run gives you)\n' > "$T/co/runs/nq/request.txt"
wtree nq; mk_origin nq 0; : > "$WAVE_CALLS"
out=$(pass nq)
echo "$out" | /usr/bin/grep -q 'CHILD NO-WAVE REQUEUE #5107' && ok "a no-wave child with nothing to salvage is requeued" || ko "nothing-to-salvage child not requeued: $out"
grep -q '^requeue 5107 .*cut no wave' "$WAVE_CALLS" && ok "the requeue names the no-wave cause" || ko "no requeue call: $(cat "$WAVE_CALLS")"
[ -e "$T/st/auto/cc-nq-c" ] && ok "a requeued no-wave child gets its marker" || ko "no marker after the requeue"
# A failed read of the issue's state (rate limit, refusal, network) is no answer (#848): childcc wrote the permanent done
# marker on it and said nothing, so the run's wave refused the child on every cycle. The read that fails writes no marker,
# logs the reason, and leaves the child (exit 3) until the read works, when the child enters.
run gf 5900 v; wtree gf; echo wave/gf > "$T/st/auto/wave-gf"; echo wave/gf > "$T/st/auto/waved-gf"; : > "$WAVE_CALLS"; : > "$GH_STATE_FAIL"
out=$(pass gf); rc=$?
[ ! -e "$T/st/auto/cc-gf-c" ] && ok "a failed issue-state read writes no done marker" || ko "marker written on a failed read"
echo "$out" | /usr/bin/grep -q 'CHILDCC SKIP #5900.*could not be read' && ok "the skip is named" || ko "silent skip: $out"
[ "$rc" = 3 ] && ok "the pass leaves the child (exit 3)" || ko "exit $rc"
rm -f "$GH_STATE_FAIL"; pass gf >/dev/null
[ "$(/usr/bin/grep -c '^enter gf 5900' "$WAVE_CALLS")" = 1 ] && [ -e "$T/st/auto/cc-gf-c" ] && ok "the next pass enters the child" || ko "no entry after the read recovers: $(cat "$WAVE_CALLS")"
# A closed issue is a settled skip: its marker is written, and the skip is named.
run clo 5901 v; wtree clo; echo wave/clo > "$T/st/auto/wave-clo"; echo wave/clo > "$T/st/auto/waved-clo"; : > "$WAVE_CALLS"
out=$(GH_STATE=closed pass clo)
[ -e "$T/st/auto/cc-clo-c" ] && ok "a closed issue gets its marker" || ko "no marker for a closed issue"
echo "$out" | /usr/bin/grep -q 'CHILDCC SKIP #5901.*is closed' && ok "the closed skip is named" || ko "closed skip silent: $out"
[ ! -s "$WAVE_CALLS" ] && ok "a closed issue is not entered" || ko "closed issue entered: $(cat "$WAVE_CALLS")"

# A review claim left by a pass that died mid-review (#502): the loop stopped, childreview.sh was killed, no review head was
# kept. The claim names the head, so every later pass skipped the review and the entry held the child for good. A claim
# whose pass is gone is no claim; one whose pass is alive still is.
printf '#!/bin/bash\necho "$* $(git rev-parse HEAD)" >> "%s/cr.calls"\n' "$T" > "$T/childreview.sh"   # each review names the head it read
run rvw 5102 n; wtree rvw; echo wave/rvw > "$T/st/auto/wave-rvw"; : > "$T/cr.calls"
h=$(/usr/bin/git -C "$T/co/runs/rvw/ws/target-c" rev-parse HEAD)
echo "$h" > "$T/st/auto/rvd-rvw-c"   # the claim as a pass wrote it before it died: the head, and no pass alive behind it
pass rvw >/dev/null
[ "$(grep -c '^rvw 5102' "$T/cr.calls")" = 1 ] && ok "a claim whose pass died is no claim: the child is reviewed again" || ko "a dead pass's claim held the review: $(cat "$T/cr.calls")"
c1=$(grep -c '^rvw 5102' "$T/cr.calls"); sleep 30 & live=$!; echo "$h $live" > "$T/st/auto/rvd-rvw-c"; rm -f "$T/st/auto/cc-rvw-c"
pass rvw >/dev/null; kill $live 2>/dev/null
[ "$(grep -c '^rvw 5102' "$T/cr.calls")" = "$c1" ] && ok "a claim whose pass is alive holds: the review is not repeated" || ko "a live claim was reviewed again: $(cat "$T/cr.calls")"
# A review takes a review slot in try mode (#567): a full budget skips the review for this pass, with one line naming the
# ceiling and its holder, and childcc never waits for a slot while it holds its own lock. The child is left for a later pass.
sleep 60 & held=$!; for i in 0 1 2 3; do PYTHONPATH=$SG_SQUAD python3 -m squad.caps take reviews "other-$i" --pid $held >/dev/null; done
run rvf 5103 n; wtree rvf; echo wave/rvf > "$T/st/auto/wave-rvf"; : > "$T/cr.calls"
s0=$(date +%s); out=$(pass rvf); rc=$?; kill $held 2>/dev/null; wait $held 2>/dev/null
[ "$(( $(date +%s) - s0 ))" -lt 20 ] && ok "a full review budget makes childcc skip, never wait" || ko "childcc waited for a review slot"
[ ! -s "$T/cr.calls" ] && ok "a full review budget runs no review this pass" || ko "a review ran with every slot held: $(cat "$T/cr.calls")"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'caps.reviews.*other-0')" = 1 ] && ok "the skip logs one line naming the ceiling and its holder" || ko "skip line: $out"
[ "$rc" = 3 ] && [ ! -e "$T/st/auto/rvd-rvf-c" ] && ok "the skipped child is left for a later pass, with no claim" || ko "exit $rc, claim: $(/bin/ls "$T/st/auto")"
# One pass works one pending child (#590): a review and the gates take 2 to 15 min each, and a pass that worked every child
# held the childcc lock, and every landing behind it, for 14 to 25 min. A pass that leaves a child for later exits 3, never 0,
# so the landing that waits for its children does not land a wave without them. A cursor kept in auto/ rotates the work, so a
# child held on every pass does not take every pass; a child whose requeue holds it on an unchanged head costs no work.
run2() { # name issue-a issue-b: a live run of two finished children, a and b, with a wave and a worktree each
  mkdir -p "$T/co/runs/$1"; { for c in a:$2 b:$3; do echo '{"event": "spawn_accepted", "child": "'${c%%:*}'", "definition": "GitHub issue #'${c#*:}'"}'; done
    for c in a b; do echo '{"event": "outcome", "node": "'$c'", "outcome": "pass", "reasons": ["ok"]}'; done; } > "$T/co/runs/$1/record.jsonl"
  wtree "$1" a; wtree "$1" b; echo "wave/$1" > "$T/st/auto/wave-$1"; echo "wave/$1" > "$T/st/auto/waved-$1"; }
ent() { /usr/bin/grep -c "^enter $1 $2" "$WAVE_CALLS"; }
run2 bud 5200 5201; : > "$WAVE_CALLS"
pass bud >/dev/null; rc=$?
[ "$(( $(ent bud 5200) + $(ent bud 5201) ))" = 1 ] && ok "one pass works one pending child" || ko "entries in one pass: $(cat "$WAVE_CALLS")"
[ "$rc" = 3 ] && ok "a pass that leaves a child for later exits 3" || ko "a pass that left a child exited $rc"
[ "$(cat "$T/st/childcc-waiting" 2>/dev/null)" = 1 ] && ok "the pass writes the count of children that wait for review (#562)" || ko "childcc-waiting after a deferred child: '$(cat "$T/st/childcc-waiting" 2>/dev/null)'"
pass bud >/dev/null; rc=$?
[ "$(ent bud 5200)" = 1 ] && [ "$(ent bud 5201)" = 1 ] && ok "the next pass works the child left" || ko "entries after two passes: $(cat "$WAVE_CALLS")"
[ "$rc" = 0 ] && ok "a pass that leaves no child exits 0" || ko "the last pass exited $rc"
[ "$(cat "$T/st/childcc-waiting" 2>/dev/null)" = 0 ] && ok "a pass that leaves no child writes the count 0 (#562)" || ko "childcc-waiting after no child left: '$(cat "$T/st/childcc-waiting" 2>/dev/null)'"
[ -e "$T/st/auto/cc-bud-a" ] && [ -e "$T/st/auto/cc-bud-b" ] && ok "both children got their marker" || ko "markers: $(/bin/ls "$T/st/auto")"
# a held child (5300, no requeue: it is tried on every pass) does not take every pass: the other child gets its turn
run2 fair 5300 5301; : > "$WAVE_CALLS"
pass fair >/dev/null; pass fair >/dev/null; rc=$?
[ "$(ent fair 5300)" = 1 ] && [ "$(ent fair 5301)" = 1 ] && ok "a held child does not take every pass" || ko "entries for a held child and its sibling: $(cat "$WAVE_CALLS")"
[ "$rc" = 3 ] && ok "a held child keeps the wave from landing: the pass exits 3 (#620)" || ko "a pass with a held child exited $rc: the wave lands without it"
# The hold is bounded (#620): after SG_HOLD_TRIES review holds on the same head (3 by default) the child goes back to the
# queue with that reason, so the wave lands without waiting on a dead review chain for its whole age cap.
pass fair >/dev/null; rc=$?
[ "$rc" = 3 ] && ! /usr/bin/grep -q '^requeue 5300' "$WAVE_CALLS" && ok "a second hold still keeps the wave" || ko "second hold: exit $rc, calls $(cat "$WAVE_CALLS")"
out=$(pass fair); rc=$?
[ "$rc" = 0 ] && /usr/bin/grep -q '^requeue 5300 .*no verdict' "$WAVE_CALLS" && ok "the third hold on one head requeues the child, and the wave may land" || ko "third hold: exit $rc, calls $(cat "$WAVE_CALLS"), out $out"
pass fair >/dev/null; rc=$?
[ "$rc" = 0 ] && [ "$(ent fair 5300)" = 3 ] && ok "a child the hold requeued is not entered again on the same head" || ko "after the hold requeue: exit $rc, calls $(cat "$WAVE_CALLS")"
# A held child of a wave past its age cap holds nothing: the wave lands, and the cap requeues the child (#620, #378).
run2 cap 5300 5301; : > "$WAVE_CALLS"; : > "$WAVE_CALLS.expired-cap"
pass cap >/dev/null; pass cap >/dev/null; rc=$?
[ "$rc" = 0 ] && ok "a held child of an expired wave does not keep it from landing" || ko "expired wave with a held child: exit $rc"
# (#1344) A child kept on a rebase round is no deadline: its commits wait for the round, so it is never requeued or counted, however
# many passes it waits. Its sibling still enters.
run2 kpt 5400 5401; : > "$WAVE_CALLS"
for p in 1 2 3 4 5 6 7 8; do pass kpt >/dev/null; done; rc=$?
! /usr/bin/grep -q '^requeue 5400' "$WAVE_CALLS" && [ ! -e "$T/st/auto/dwait-kpt-a" ] && [ "$(ent kpt 5401)" = 1 ] && ok "a child kept on a rebase round is never requeued or counted" || ko "a kept child was requeued or counted: $(cat "$WAVE_CALLS") $(cat "$T/st/auto/dwait-kpt-a" 2>/dev/null)"
# Its commits stand on a work branch of its own, which the landing passes deliver: it leaves the wave rather than holding it (#1344).
[ "$rc" = 0 ] && ok "a child kept on a rebase round does not hold the wave's landing" || ko "a kept child held the wave: exit $rc"
# a child whose requeue holds it on an unchanged head costs no work: its sibling is worked in the same pass
run2 sup 5400 5401; : > "$WAVE_CALLS"; lqset 5400 sup "$(/usr/bin/git -C "$T/co/runs/sup/ws/target-a" rev-parse HEAD)"
pass sup >/dev/null; rc=$?
[ "$(ent sup 5400)" = 0 ] && [ "$(ent sup 5401)" = 1 ] && [ "$rc" = 0 ] && ok "a requeued child on an unchanged head costs no work" \
  || ko "entries with a requeued child: $(cat "$WAVE_CALLS"), exit $rc"
# A child whose own engine landing failed does not enter its run's wave: its work never reached the run's target, so its
# issue goes back to the queue with the landing's reason and its card does not move to Code Complete (#592).
mkdir -p "$T/co/runs/lfail"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #5500"}'
  echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'
  echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "fail", "reasons": ["merge_conflict"], "effect": {"name": "merge_land"}}'; } > "$T/co/runs/lfail/record.jsonl"
wtree lfail; echo wave/lfail > "$T/st/auto/wave-lfail"; echo wave/lfail > "$T/st/auto/waved-lfail"; : > "$WAVE_CALLS"; rm -f "$T/moves"
pass lfail >/dev/null
[ "$(ent lfail 5500)" = 0 ] && ok "a failed land enters no child" || ko "a child whose landing failed was entered: $(cat "$WAVE_CALLS")"
/usr/bin/grep -q '^requeue 5500 ' "$WAVE_CALLS" && ok "a failed land requeues the issue" || ko "no requeue for a failed land: $(cat "$WAVE_CALLS")"
/usr/bin/grep -q '^requeue 5500 .*merge_conflict' "$WAVE_CALLS" && ok "the requeue carries the landing's reason" || ko "the requeue carries no landing reason: $(cat "$WAVE_CALLS")"
/usr/bin/grep -q ' 5500$' "$T/moves" 2>/dev/null && ko "a child whose landing failed moved to Code Complete" || ok "a child whose landing failed moves no card"
# A child the requeue cap stopped (WAVE STOP) is a requeue too (#594): the same head is never entered again.
run wst 5600 n; wtree wst; echo wave/wst > "$T/st/auto/wave-wst"; echo wave/wst > "$T/st/auto/waved-wst"; : > "$WAVE_CALLS"
pass wst >/dev/null; pass wst >/dev/null
[ "$(lq 5600 wst)" = "$(/usr/bin/git -C "$T/co/runs/wst/ws/target-c" rev-parse HEAD)" ] && [ "$(ent wst 5600)" = 1 ] \
  && ok "a stopped child gets the requeue fact and is not entered again on the same head" || ko "WAVE STOP: fact '$(lq 5600 wst)', entries $(cat "$WAVE_CALLS")"
# A loop defect in the dispatch path halts delivery too (#596, ADR-0146): that defect is fixed outside the loop, so no
# child enters a wave and no card moves while one carries `loop-bug:dispatch`. A bare `loop-bug` halts nothing (B1). The pass reads the open issues once, names the issue in
# the same line the dispatch passes print, and writes nothing else. Closing the issue resumes the entry with no marker.
run lb 5600 n; wtree lb; echo wave/lb > "$T/st/auto/wave-lb"; echo wave/lb > "$T/st/auto/waved-lb"
: > "$WAVE_CALLS"; rm -f "$T/moves" "$T/st/auto/cc-lb-c" "$T/st/cycle.log"
cat > "$T/issues-open.json" <<'JSON'
{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":596,"title":"The loop cannot land its own fix","body":"","state":"OPEN","labels":{"nodes":[{"name":"loop-bug:dispatch"}]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}
JSON
out=$(pass lb)
[ "$(ent lb 5600)" = 0 ] && ok "a loop defect enters no child" || ko "a child entered while a loop defect is open: $(cat "$WAVE_CALLS")"
/usr/bin/grep -q ' 5600$' "$T/moves" 2>/dev/null && ko "a loop defect moved a card" || ok "a loop defect moves no card"
echo "$out" | /usr/bin/grep -q "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" && ok "the pass names the same stop line" || ko "no stop line: $out"
/usr/bin/grep -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$T/st/cycle.log" 2>/dev/null && ok "the stop is published to the loop log" || ko "cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ ! -e "$T/st/auto/cc-lb-c" ] && ok "the stopped pass leaves no entry marker" || ko "the stopped pass wrote a marker"
rc=0; out=$(pass lb) || rc=$?
[ "$rc" = 3 ] && ok "a child left out of its wave under the stop holds the landing (exit 3, #641)" || ko "the stopped pass exited $rc with a child not in its wave: $out"
: > "$T/cr.calls"; out=$(pass lb); [ ! -s "$T/cr.calls" ] && ok "the stopped pass starts no review" || ko "a review started under the stop: $(cat "$T/cr.calls")"
# An entered child is approved and gated (#641, ADR-0146 decision 3): a wave whose children all entered still lands, so the pass
# exits 0 and the landing goes on. A wave past its age cap lands too: the capped landing requeues the child that never entered.
run lbin 5601 n; wtree lbin; echo wave/lbin > "$T/st/auto/wave-lbin"; echo wave/lbin > "$T/st/auto/waved-lbin"; : > "$T/st/auto/cc-lbin-c"
rc=0; out=$(pass lbin) || rc=$?
[ "$rc" = 0 ] && ok "a wave whose children all entered lands under a loop-bug:dispatch (exit 0)" || ko "the stop held an approved wave: exit $rc: $out"
: > "$WAVE_CALLS.expired-lb"; rc=0; out=$(pass lb) || rc=$?; rm -f "$WAVE_CALLS.expired-lb"
[ "$rc" = 0 ] && [ "$(ent lb 5600)" = 0 ] && ok "a wave past its age cap lands under a loop-bug:dispatch, with no entry" || ko "expired wave under the stop: exit $rc, entries $(ent lb 5600)"
# `loop-bug:landing` names the landing path, so it stops the entry as well.
sed 's/"name":"loop-bug:dispatch"/"name":"loop-bug:landing"/' "$T/issues-open.json" > "$T/issues-open.json.l" && mv "$T/issues-open.json.l" "$T/issues-open.json"
rc=0; out=$(pass lb) || rc=$?
[ "$(ent lb 5600)" = 0 ] && echo "$out" | /usr/bin/grep -q "LOOP STOP: #596" && ok "a loop-bug:landing issue stops the entry too" || ko "loop-bug:landing did not stop the entry: $(cat "$WAVE_CALLS")"
# `loop-bug:infra` names the loop's infrastructure, so the same one list stops the entry (ADR-0146).
sed 's/"name":"loop-bug:landing"/"name":"loop-bug:infra"/' "$T/issues-open.json" > "$T/issues-open.json.i" && mv "$T/issues-open.json.i" "$T/issues-open.json"
rc=0; out=$(pass lb) || rc=$?
[ "$(ent lb 5600)" = 0 ] && echo "$out" | /usr/bin/grep -q "LOOP STOP: #596" && ok "a loop-bug:infra issue stops the entry too" || ko "loop-bug:infra did not stop the entry: $(cat "$WAVE_CALLS")"
printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}' > "$T/issues-open.json"
out=$(pass lb)
[ "$(ent lb 5600)" = 1 ] && ok "closing the issue resumes the entry with no other action" || ko "entry did not resume: $(cat "$WAVE_CALLS")"

# Reviewing (#603): with the shipped vocabulary, childcc moves a Doing card to Reviewing when it starts the review of a
# child and when it defers a child that waits for review, through board.sh, once per child. A card past Reviewing (Batching)
# and a card a requeue sent to Todo are never moved; the old vocabulary has no Reviewing and gets no write (#2155).
crv() { cat "$T/moves" 2>/dev/null | /usr/bin/grep -c "^x 4 Reviewing $1\$"; }
run2 crv 5700 5701; : > "$WAVE_CALLS"; : > "$T/cr.calls"; rm -f "$T/moves"
export GH_NUMS="5700:Doing 5701:Doing"
SG_BOARD_VOCAB=shipped pass crv >/dev/null
[ "$(crv 5700)" = 1 ] && /usr/bin/grep -q '^crv 5700' "$T/cr.calls" && ok "the review start moves the child's card to Reviewing" || ko "review start: moves '$(cat "$T/moves" 2>/dev/null)', reviews '$(cat "$T/cr.calls")'"
[ "$(crv 5701)" = 1 ] && ! /usr/bin/grep -q '^crv 5701' "$T/cr.calls" && ok "a child deferred for review moves its card to Reviewing" || ko "defer: moves '$(cat "$T/moves" 2>/dev/null)'"
SG_BOARD_VOCAB=shipped pass crv >/dev/null
[ "$(crv 5700)" = 1 ] && [ "$(crv 5701)" = 1 ] && ok "Reviewing is written once per child" || ko "Reviewing writes after two passes: '$(cat "$T/moves" 2>/dev/null)'"
run2 crl 5710 5711; : > "$WAVE_CALLS"; rm -f "$T/moves"
export GH_NUMS="5710:Code_Complete 5711:Todo"
SG_BOARD_VOCAB=shipped pass crl >/dev/null; SG_BOARD_VOCAB=shipped pass crl >/dev/null
[ "$(crv 5710)" = 0 ] && ok "a card in Batching never goes back to Reviewing" || ko "Batching card moved back: '$(cat "$T/moves")'"
[ "$(crv 5711)" = 0 ] && ok "a card a requeue sent to Todo is not moved to Reviewing" || ko "Todo card moved: '$(cat "$T/moves")'"
run2 cro 5720 5721; : > "$WAVE_CALLS"; rm -f "$T/moves"
export GH_NUMS="5720:In_Progress 5721:In_Progress"
pass cro >/dev/null
! /usr/bin/grep -qE 'Code (Reviewing|Complete)' "$T/moves" 2>/dev/null && ok "the old vocabulary gets no review or landing write" || ko "old vocabulary wrote an old name: '$(cat "$T/moves")'"

# A run reviews its own children (#600, #601): the review gate's verdict is in the child's `land` event. A child its run
# approved enters its wave with no second review call; a child its run held, whose requeued marker exists, is neither
# entered, reviewed nor requeued again; a child of a run with no review in its record (a run started before the gate, or a
# gate that answered no_review) is reviewed here as before. A child approved by its run that its entry still holds (the
# review file is stale or gone) is reviewed here on the next pass, so it is never held for good.
rrun() { # name issue land-outcome land-reason verdict(or "-" for no review in the land event, "none" for no land event)
  mkdir -p "$T/co/runs/$1"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #'$2'"}'
    echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'
    case $5 in none) ;; -) echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "'$3'", "reasons": ['$4'], "effect": {"name": "merge_land"}}';;
      *) echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "'$3'", "reasons": ['$4'], "effect": {"name": "review", "review": {"command": ["runreview.sh"], "elapsed": 1.0, "exit": 0, "verdict": "'$5'", "findings": "x"}}}';;
    esac; } > "$T/co/runs/$1/record.jsonl"
  wtree "$1"; echo "wave/$1" > "$T/st/auto/wave-$1"; echo "wave/$1" > "$T/st/auto/waved-$1"; }
export GH_NUMS="5800:Doing 5801:Doing 5802:Doing 5803:Doing 5300:Doing"
rrun rap 5800 pass '' approve; : > "$WAVE_CALLS"; : > "$T/cr.calls"; rm -f "$T/moves"
pass rap >/dev/null
! /usr/bin/grep -q '^rap ' "$T/cr.calls" && ok "a child its run approved gets no second review call" || ko "an approved child was reviewed again: $(cat "$T/cr.calls")"
[ "$(ent rap 5800)" = 1 ] && [ -e "$T/st/auto/cc-rap-c" ] && ok "a child its run approved enters its wave" || ko "approved child entry: $(cat "$WAVE_CALLS")"
rrun rhd 5801 fail '"review_held"' hold; lqset 5801 rhd "$(/usr/bin/git -C "$T/co/runs/rhd/ws/target-c" rev-parse HEAD)"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
pass rhd >/dev/null; pass rhd >/dev/null
[ "$(ent rhd 5801)" = 0 ] && ok "a child its run held is not entered" || ko "a held child was entered: $(cat "$WAVE_CALLS")"
! /usr/bin/grep -q '^rhd ' "$T/cr.calls" && ok "a child its run held is not reviewed again" || ko "a held child was reviewed: $(cat "$T/cr.calls")"
! /usr/bin/grep -q '^requeue 5801' "$WAVE_CALLS" && ok "a child its run held is not requeued again" || ko "a held child was requeued again: $(cat "$WAVE_CALLS")"
rrun rold 5802 pass '' none; : > "$WAVE_CALLS"; : > "$T/cr.calls"
pass rold >/dev/null
[ "$(/usr/bin/grep -c '^rold 5802' "$T/cr.calls")" = 1 ] && [ "$(ent rold 5802)" = 1 ] && ok "a child of a run with no review in its record is reviewed as before" || ko "old run: reviews '$(cat "$T/cr.calls")', entries '$(cat "$WAVE_CALLS")'"
rrun rnr 5803 pass '' no_review; : > "$WAVE_CALLS"; : > "$T/cr.calls"
pass rnr >/dev/null
[ "$(/usr/bin/grep -c '^rnr 5803' "$T/cr.calls")" = 1 ] && ok "a child whose run gate answered no_review is reviewed as before" || ko "no_review: reviews '$(cat "$T/cr.calls")'"
rrun rst 5300 pass '' approve; : > "$WAVE_CALLS"; : > "$T/cr.calls"
pass rst >/dev/null; pass rst >/dev/null
[ ! -s "$T/cr.calls" ] && [ "$(ent rst 5300)" = 2 ] && [ ! -e "$T/st/auto/apphold-rst-c" ] && ok "an approved child its entry holds is never reviewed here: its approval binds to its head (ADR-0151 rules 4 and 5)" || ko "approved but held: reviews '$(cat "$T/cr.calls")', entries '$(cat "$WAVE_CALLS")'"

# A dispatch child with fix entries (#570) has one passing `outcome` per entry, entry 1, 2, ... of the same child. The pass
# works the child once, at its last entry's head: one review and one entry, never one per entry (#611).
mkdir -p "$T/co/runs/fx"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #5900"}'
  echo '{"event": "outcome", "node": "c", "entry": 1, "outcome": "pass", "reasons": ["one"]}'
  echo '{"event": "review_round", "node": "c", "round": 1, "head": "h1", "verdict": "no_review"}'
  echo '{"event": "outcome", "node": "c", "entry": 2, "outcome": "pass", "reasons": ["two"]}'
  echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land"}}'; } > "$T/co/runs/fx/record.jsonl"   # a live run that reviews its child works it only after its land (#696)
wtree fx; /usr/bin/git -C "$T/co/runs/fx/ws/target-c" -c user.email=a@b -c user.name=t commit -q --allow-empty -m entry2
h2=$(/usr/bin/git -C "$T/co/runs/fx/ws/target-c" rev-parse HEAD); echo wave/fx > "$T/st/auto/wave-fx"; echo wave/fx > "$T/st/auto/waved-fx"
export GH_NUMS="5900:Doing"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
SG_CHILDCC_BUDGET=5 pass fx >/dev/null
[ "$(/usr/bin/grep '^fx ' "$T/cr.calls")" = "fx 5900 $h2" ] && ok "a child with a fix entry is reviewed once, at its last entry's head" || ko "fix entries: reviews '$(cat "$T/cr.calls")', want 'fx 5900 $h2'"
[ "$(/usr/bin/grep '^enter fx ' "$WAVE_CALLS")" = "enter fx 5900" ] && ok "a child with a fix entry enters once" || ko "fix entries: entries '$(cat "$WAVE_CALLS")'"

# The hub shows the pass (#598): auto/childcc-now.json names the pass and each child it holds. Mid-review the child reads
# `review` with the key of its model file, the other child reads `deferred`; when the pass ends no pid stays and the deferred
# child keeps its wait.
printf '#!/bin/bash\ncp "%s/st/auto/childcc-now.json" "%s/mid.json"\n' "$T" "$T" > "$T/childreview.sh"
rrun nwa 6001 pass '' none; rrun nwb 6002 pass '' none; export GH_NUMS="6001:Doing 6002:Doing"
printf '%s\n%s\n' "$T/co/runs/nwa" "$T/co/runs/nwb" > "$T/st/active-runs.txt"; rm -f "$T/st/auto/childcc-now.json"
(cd "$T/co" && python3 "$T/childcc.py.run" >/dev/null 2>&1)
python3 - "$T/mid.json" "$T/st/auto/childcc-now.json" <<'PY' && ok "the pass publishes its children: one in review mid-pass, the other deferred, and no pid once it ends" || ko "childcc-now.json: $(cat "$T/mid.json" "$T/st/auto/childcc-now.json" 2>&1)"
import json, sys
mid, end = (json.load(open(f)) for f in sys.argv[1:])
assert mid["pid"] and mid["t0"], mid
steps = {c["run"]: c["step"] for c in mid["children"]}
assert steps == {"nwa": "review", "nwb": "deferred"}, steps
assert all(c["issue"] in (6001, 6002) and c["child"] == "c" and c["since"] for c in mid["children"]), mid
assert [c["rk"] for c in mid["children"] if c["step"] == "review"][0], mid   # the key of the child branch
assert end["pid"] is None and [(c["run"], c["step"]) for c in end["children"]] == [("nwb", "deferred")], end
PY

# A childcc CHANGES on a child its run did not review starts one fix run in the child's kept worktree, with no requeue (#636).
# The fix run is childfix.sh's, stubbed here: it names the run folder it started. While that run lives the child is held and
# no second fix run starts; a fix run is no child of the issue's wave, so its own child moves no card. An APPROVE on the new
# head enters the wave. Findings that do not change after the fix run requeue the issue once, with them in the reason (ADR-0134).
cat > "$T/childreview.sh" <<'CR'
#!/bin/bash
k=$(git symbolic-ref --quiet --short HEAD | tr / _); cp "$FIX_DIR/verdict-$2" "$SG_STATE/review-$k.md"; git rev-parse HEAD > "$SG_STATE/review-$k.sha"
CR
cat > "$T/wave.sh" <<'W'
#!/bin/bash
[ "$1" = expired ] && exit 1    # the wave's age cap: not reached here, and a read that writes nothing
echo "$*" >> "$WAVE_CALLS"
[ "$1" = requeue ] && { echo "WAVE REQUEUE #$2: $3"; exit 0; }
k=$(git symbolic-ref --quiet --short HEAD | tr / _)
head -1 "$SG_STATE/review-$k.md" | /usr/bin/grep -q 'VERDICT: APPROVE' || { echo "WAVE HOLD #$3: the review of x did not approve it: $(head -1 "$SG_STATE/review-$k.md")"; exit 1; }
echo "WAVE x entered (#$3)"
W
cat > "$T/childfix.sh" <<'CF'
#!/bin/bash
echo "$1 $2 $(pwd -P)" >> "$FIX_DIR/fix.calls"
d=$FIX_DIR/co/runs/fix-$2; mkdir -p "$d"
printf '%s\n' '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #'$2'"}' '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}' > "$d/record.jsonl"
echo "$d"
CF
export FIX_DIR=$T GH_NUMS="6100:Doing 6101:Doing"; rm -f "$T/moves"; : > "$T/fix.calls"
fixpass() { printf '%s\n' "$T/co/runs/$1" "$T/co/runs/fix-$2" > "$T/st/active-runs.txt"; (cd "$T/co" && SG_CHILDCC_BUDGET=5 python3 "$T/childcc.py.run" 2>&1); }
fixed() { /usr/bin/git -C "$T/co/runs/$1/ws/target-c" -c user.email=a@b -c user.name=t commit -q --allow-empty -m fix; echo '{"event": "verdict"}' >> "$T/co/runs/fix-$2/record.jsonl"; }
printf '**VERDICT: CHANGES**\n- [Blocking] a.py:3 the guard is missing\n' > "$T/verdict-6100"
rrun cfx 6100 pass '' none; : > "$WAVE_CALLS"
out=$(fixpass cfx 6100); rc=$?
[ "$(/usr/bin/grep -c '^cfx 6100 ' "$T/fix.calls")" = 1 ] && /usr/bin/grep -q "^cfx 6100 $(cd "$T/co/runs/cfx/ws/target-c" && pwd -P)\$" "$T/fix.calls" \
  && ok "a childcc CHANGES starts one fix run in the child's worktree" || ko "fix runs: '$(cat "$T/fix.calls")', out $out"
! /usr/bin/grep -q '^requeue 6100' "$WAVE_CALLS" && ok "a CHANGES with a fix run does not requeue the issue" || ko "requeued: $(cat "$WAVE_CALLS")"
[ "$rc" = 3 ] && ok "a child in its fix run holds its wave" || ko "a pass with a child in its fix run exited $rc"
out=$(fixpass cfx 6100); rc=$?
[ "$(/usr/bin/grep -c '^cfx 6100 ' "$T/fix.calls")" = 1 ] && ! /usr/bin/grep -q '^requeue 6100' "$WAVE_CALLS" && [ "$rc" = 3 ] \
  && ok "while the fix run lives no second fix run starts and nothing requeues" || ko "live fix run: calls '$(cat "$T/fix.calls")', waves $(cat "$WAVE_CALLS"), exit $rc"
/usr/bin/grep -q ' 6100$' "$T/moves" 2>/dev/null && ko "the fix run's own child moved the card: $(cat "$T/moves")" || ok "the fix run's own child moves no card"
# A fix run's folder that cannot be read is no verdict either (#667): the fix run lived inside the child's worktree, and a
# landing that cleaned that worktree took its record with it. The child stays held -- never released to a wave that would
# land empty -- and the wave's age cap is what frees it.
mv "$T/co/runs/fix-6100" "$T/co/runs/fix-6100.gone"
out=$(fixpass cfx 6100); rc=$?
[ "$rc" = 3 ] && [ "$(/usr/bin/grep -c '^cfx 6100 ' "$T/fix.calls")" = 1 ] && ok "a fix run whose record is unreadable still holds its child" || ko "unreadable fix run: exit $rc, calls '$(cat "$T/fix.calls")', out $out"
! /usr/bin/grep -q '^requeue 6100' "$WAVE_CALLS" && ok "an unreadable fix run requeues nothing" || ko "requeued: $(cat "$WAVE_CALLS")"
mv "$T/co/runs/fix-6100.gone" "$T/co/runs/fix-6100"
printf '**VERDICT: APPROVE**\n' > "$T/verdict-6100"; fixed cfx 6100
out=$(fixpass cfx 6100); rc=$?
/usr/bin/grep -q 'WAVE x entered (#6100)' <<<"$out" && [ -e "$T/st/auto/cc-cfx-c" ] && ! /usr/bin/grep -q '^requeue 6100' "$WAVE_CALLS" \
  && ok "an APPROVE on the fix run's head enters the wave" || ko "after the fix: out $out, waves $(cat "$WAVE_CALLS")"
printf '**VERDICT: CHANGES**\n- [Blocking] b.py:7 the cap is never read\n' > "$T/verdict-6101"
rrun cst 6101 pass '' none; : > "$WAVE_CALLS"
fixpass cst 6101 >/dev/null; printf '**VERDICT: CHANGES**\n- [Blocking] b.py:9 the cap is never read\n' > "$T/verdict-6101"; fixed cst 6101
echo '{"event": "outcome", "node": "c", "entry": 2, "outcome": "fail", "reasons": ["child_ended_early"], "diagnostic": {"ended": {"last": "I stopped after the guard edit"}}}' >> "$T/co/runs/fix-6101/record.jsonl"
out=$(fixpass cst 6101); fixpass cst 6101 >/dev/null
[ "$(/usr/bin/grep -c '^cst 6101 ' "$T/fix.calls")" = 1 ] && ok "findings that do not change start no second fix run" || ko "fix runs: '$(cat "$T/fix.calls")'"
[ "$(/usr/bin/grep -c '^requeue 6101 ' "$WAVE_CALLS")" = 1 ] && /usr/bin/grep -q '^requeue 6101 .*the cap is never read' "$WAVE_CALLS" \
  && ok "findings that do not change requeue the issue once, with the findings in the reason" || ko "requeues: $(cat "$WAVE_CALLS"), out $out"
/usr/bin/grep -q '^requeue 6101 .*its last turn: I stopped after the guard edit' "$WAVE_CALLS" \
  && ok "the requeue reason keeps the fix run's last turn, so a reader sees why it stopped" || ko "no last turn in the reason: $(cat "$WAVE_CALLS")"

# A live run that reviews its own children still owns a child after its pass (#696): it reviews it, fixes it, or lands it,
# and the verdict comes in the child's `land` event. Until that event (or the run's verdict) the child is not pending here:
# no review, no fix run, no requeue, no Code Review move, so no second session edits the worktree the run still fixes.
# The run reviews its children when its dispatch node names a review gate (graph.json), or its record holds a review event.
lrun() { # name issue graph(g: a review gate in graph.json, else none) extra-record-lines... (@HEAD@ is the child's head)
  local n=$1 i=$2 g=$3 h; shift 3; mkdir -p "$T/co/runs/$n"; wtree "$n"; h=$(/usr/bin/git -C "$T/co/runs/$n/ws/target-c" rev-parse HEAD)
  { echo '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c", "entry": 1, "outcome": "pass", "reasons": ["ok"]}'; for l in "$@"; do echo "${l//@HEAD@/$h}"; done; } > "$T/co/runs/$n/record.jsonl"
  [ "$g" = g ] && echo '{"nodes": [{"name": "route", "kind": "dispatch", "review": "review_gate"}]}' > "$T/co/runs/$n/graph.json"
  echo "wave/$n" > "$T/st/auto/wave-$n"; echo "wave/$n" > "$T/st/auto/waved-$n"; }
lpass() { echo "$T/co/runs/$1" > "$T/st/active-runs.txt"; (cd "$T/co" && SG_BOARD_VOCAB=shipped SG_CHILDCC_BUDGET=5 python3 "$T/childcc.py.run" 2>&1); }
untouched() { # name issue out: the pass did not work the child
  ! /usr/bin/grep -q "^$1 $2 " "$T/fix.calls" && ! /usr/bin/grep -q " $2\$\| $2 " "$WAVE_CALLS" && [ ! -e "$T/st/auto/ccfix-$1-c" ] && [ ! -e "$T/st/auto/cc-$1-c" ] \
    && ! /usr/bin/grep -q "CODE REVIEW\|CHILD REVIEW\|CHILD FIX" <<<"$3"; }
printf '**VERDICT: CHANGES**\n- [Blocking] c.py:1 the run still fixes this\n' > "$T/verdict-6200"; printf '**VERDICT: CHANGES**\n- [Blocking] c.py:2 x\n' > "$T/verdict-6201"
export GH_NUMS="6200:Doing 6201:Doing 6202:Doing"; : > "$WAVE_CALLS"; : > "$T/fix.calls"; rm -f "$T/moves"
lrun lv 6200 - '{"event": "review_round", "node": "c", "round": 1, "head": "@HEAD@", "verdict": "fix"}' '{"event": "call_start", "node": "c", "entry": 2}'
out=$(lpass lv)
untouched lv 6200 "$out" && ok "a child its live run reviews and fixes is not worked before its land event" || ko "live run's child worked: fix '$(cat "$T/fix.calls")', waves '$(cat "$WAVE_CALLS")', out $out"
! /usr/bin/grep -q ' 6200$' "$T/moves" 2>/dev/null && ok "a child its live run still owns moves no card" || ko "card moved: $(cat "$T/moves")"
lrun lg 6201 g; out=$(lpass lg)
untouched lg 6201 "$out" && ok "a passed child of a live run whose dispatch names a review gate waits for its land event" || ko "review-gate run's child worked: fix '$(cat "$T/fix.calls")', waves '$(cat "$WAVE_CALLS")', out $out"
echo '{"event": "land", "node": "c", "parent": "route", "entry": 2, "outcome": "pass", "reasons": [], "effect": {"name": "review", "review": {"verdict": "approve"}}}' >> "$T/co/runs/lv/record.jsonl"
rm -f "$T"/st/review-*; out=$(lpass lv)   # the review files of the earlier cases name the same branch
[ "$(/usr/bin/grep -c '^enter lv 6200' "$WAVE_CALLS")" = 1 ] && [ ! -s "$T/fix.calls" ] && ok "after its land event the child is worked as before" || ko "after land: waves '$(cat "$WAVE_CALLS")', fix '$(cat "$T/fix.calls")', out $out"
lrun lf 6202 g; echo '{"event": "verdict"}' >> "$T/co/runs/lf/record.jsonl"; : > "$WAVE_CALLS"; : > "$T/cr.calls"; out=$(lpass lf); rc=$?
untouched lf 6202 "$out" && [ "$rc" = 3 ] && ok "a child of a finished review-gate run with no approval is neither entered nor reviewed: its delivery holds" || ko "finished run: exit $rc, waves '$(cat "$WAVE_CALLS")', out $out"

# One owner per child and per worktree (ADR-0151 rules 4 and 5, #701). A run that reviewed a child owns it: childcc never
# reviews it, starts no fix run for it and never asks the wave to merge it on its own reading. The run's approval binds to
# the head its review read (rule 4), so a child at another head, or one whose run approved no head, holds its delivery
# (rule 5), and only a child whose approval stands enters the wave. The wave stub below carries wave.sh's review evidence:
# the verdict, the base it was read against, and the head it read.
cat > "$T/childreview.sh" <<'CR'
#!/bin/bash
echo "$* $(git rev-parse HEAD)" >> "$FIX_DIR/cr.calls"
CR
cat > "$T/wave.sh" <<'W'
#!/bin/bash
[ "$1" = expired ] && { [ -e "$WAVE_CALLS.expired-$2" ]; exit; }   # the age cap: a case marks the run's wave expired
echo "$*" >> "$WAVE_CALLS"
[ -e "$WAVE_GATE_HOLD" ] && [ "$1" = enter ] && { echo "WAVE HOLD #$3: the gate lock is held by pid 999; the child enters on a later pass"; exit 1; }
[ "$1" = requeue ] && { [ -e "$WAVE_REQUEUE_FAIL" ] && { echo "WAVE STOP #$2: already frozen, so it is not dispatched again: $3"; exit 0; }; [ -e "$WAVE_NEEDS_HUMAN" ] && { echo "WAVE NEEDS-HUMAN #$2: already stopped, so it is not dispatched again: $3"; exit 0; }; [ -e "$WAVE_REQUEUE_ERR" ] && exit 1; echo "WAVE REQUEUE #$2: $3"; exit 0; }
k=$(git symbolic-ref --quiet --short HEAD | tr / _); h=$(git rev-parse HEAD)
[ -s "$SG_STATE/review-$k.md" ] || { echo "WAVE HOLD #$3: no review of $k yet: a child never enters a wave unreviewed"; exit 1; }
[ "$(cat "$SG_STATE/review-$k.base" 2>/dev/null)" = "wave/$2" ] || { echo "WAVE HOLD #$3: the review of $k was read against '$(cat "$SG_STATE/review-$k.base" 2>/dev/null)', not wave/$2"; exit 1; }
[ "$(cat "$SG_STATE/review-$k.sha" 2>/dev/null)" = "$h" ] || { echo "WAVE HOLD #$3: the review of $k read $(cut -c1-9 "$SG_STATE/review-$k.sha" 2>/dev/null), not HEAD ${h:0:9}"; exit 1; }
head -1 "$SG_STATE/review-$k.md" | /usr/bin/grep -q 'VERDICT: APPROVE' || { echo "WAVE HOLD #$3: the review of $k did not approve it: $(head -1 "$SG_STATE/review-$k.md")"; exit 1; }
echo "WAVE x entered (#$3)"
W
orun() { # run issue review-verdict [land]: a finished run whose review gate read child c's one head; prints that head
  local n=$1 i=$2 v=$3 l=${4:-} rd="$T/co/runs/$1" h; wtree "$n"; h=$(/usr/bin/git -C "$rd/ws/target-c" rev-parse HEAD)
  { echo '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'
    echo '{"event": "review_round", "node": "c", "round": 1, "head": "'$h'", "verdict": "'$v'", "offered": false, "review": {"verdict": "'$v'", "findings": "x"}}'
    [ -n "$l" ] && echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "before": "b", "head": "h", "review": {"verdict": "'$v'", "findings": "x"}, "rounds": 1}}'
    echo '{"event": "verdict"}'; } > "$rd/record.jsonl"
  echo "wave/$n" > "$T/st/auto/wave-$n"; echo "wave/$n" > "$T/st/auto/waved-$n"; printf '%s\n' "$h"; }
rfile() { # run head verdict: the review evidence the run's gate wrote for the child's branch, as runreview.sh leaves it
  local k; k=$(/usr/bin/git -C "$T/co/runs/$1/ws/target-c" symbolic-ref --quiet --short HEAD | tr / _)
  printf '**VERDICT: %s**\n- [Blocking] a.py:1 x\n' "$3" > "$T/st/review-$k.md"
  echo "wave/$1" > "$T/st/review-$k.base"; echo "$2" > "$T/st/review-$k.sha"; }
# (a) a child its run reviewed and did not approve is neither reviewed nor fixed here, and its delivery holds.
export GH_NUMS="6300:Doing 6301:Doing"; rm -f "$T"/st/review-*
orun miss 6300 hold >/dev/null; : > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass miss); rc=$?
[ ! -s "$T/cr.calls" ] && ok "a run-owned child its run did not approve is not reviewed by childcc" || ko "childcc reviewed it: $(cat "$T/cr.calls")"
[ ! -s "$T/fix.calls" ] && [ ! -s "$WAVE_CALLS" ] && ok "a run-owned child its run did not approve starts no fix run and enters nothing" || ko "waves '$(cat "$WAVE_CALLS")', fix '$(cat "$T/fix.calls")'"
[ "$rc" = 3 ] && ok "a run-owned child its run did not approve holds its delivery" || ko "held child: exit $rc: $out"
/usr/bin/grep -q ' 6300$' "$T/moves" 2>/dev/null && ko "a held run-owned child moved its card" || ok "a held run-owned child moves no card"
echo "$out" | /usr/bin/grep -q 'RUN-OWNED #6300' && ok "the pass names the run-owned child it holds" || ko "no hold line: $out"
# (b) the approval binds to the reviewed commit: a new head makes it stale, and nothing enters.
h=$(orun stale 6301 approve land); rfile stale "$h" APPROVE
/usr/bin/git -C "$T/co/runs/stale/ws/target-c" -c user.email=a@b -c user.name=t commit -q --allow-empty -m after-approval
: > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass stale); rc=$?; out2=$(lpass stale); rc2=$?
newh=$(/usr/bin/git -C "$T/co/runs/stale/ws/target-c" rev-parse HEAD)
/usr/bin/grep -q "^stale 6301 $newh\$" "$T/cr.calls" && ok "a finished run's child at a moved tip gets childcc's review of that tip (#808)" || ko "tip not reviewed: '$(cat "$T/cr.calls")'"
[ ! -s "$T/fix.calls" ] && ! /usr/bin/grep -q 'entered' <<<"$out" && ok "a stale approval enters nothing and starts no fix run" || ko "waves '$(cat "$WAVE_CALLS")', fix '$(cat "$T/fix.calls")', out $out"
[ "$rc" = 3 ] && [ "$rc2" = 3 ] && ok "a stale approval holds the child's delivery until its tip's review enters" || ko "stale approval: exit $rc, then $rc2: $out $out2"
/usr/bin/grep -q 'Code Complete 6301$' "$T/moves" 2>/dev/null && ko "a child whose approval went stale moved to Code Complete" || ok "a child whose approval went stale is not moved to Code Complete"
echo "$out" | /usr/bin/grep -q 'WAVE HOLD #6301: the review of' && ok "the pass names the review of the moved tip it holds for" || ko "no hold line: $out"
# (b1) #2538: a child at its requeue head prints the TIP line once over two passes, and gets no cc- marker.
export GH_NUMS="6300:Doing 6301:Doing 6302:Doing"
h=$(orun rqh 6302 approve land); rfile rqh "$h" APPROVE
/usr/bin/git -C "$T/co/runs/rqh/ws/target-c" -c user.email=a@b -c user.name=t commit -q --allow-empty -m after-approval
lqset 6302 rqh "$(/usr/bin/git -C "$T/co/runs/rqh/ws/target-c" rev-parse HEAD)"
out=$(lpass rqh; lpass rqh)
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'CHILD REVIEW TIP #6302')" = 1 ] && ok "a child at its requeue head prints the TIP line once over two passes" || ko "TIP lines: $(printf '%s\n' "$out" | /usr/bin/grep -c 'CHILD REVIEW TIP #6302'): $out"
[ ! -e "$T/st/auto/cc-rqh-c" ] && ok "a child at its requeue head gets no cc- marker" || ko "a child at its requeue head got a cc- marker"
# (b2) a run-owned child whose own land failed on missing review evidence (a review deadline, or a review that held it) is
# neither requeued nor worked here: the run's review is the only evidence, and missing evidence holds delivery (ADR-0151
# rule 5). The failed-land requeue is for a failure unrelated to the review, so it reads the approval first.
orun_fail() { # run issue verdict reason: a finished run whose review gate held child c, and whose land failed on it; prints the head
  local n=$1 i=$2 v=$3 r=$4 rd="$T/co/runs/$1" h; wtree "$n"; h=$(/usr/bin/git -C "$rd/ws/target-c" rev-parse HEAD)
  { echo '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'
    echo '{"event": "review_round", "node": "c", "round": 1, "head": "'$h'", "verdict": "'$v'", "offered": false, "review": {"verdict": "'$v'", "findings": "x"}}'
    echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "fail", "reasons": ["'$r'"], "effect": {"name": "review", "before": "b", "head": "'$h'", "review": {"verdict": "'$v'", "findings": "x"}, "rounds": 1}}'
    echo '{"event": "verdict"}'; } > "$rd/record.jsonl"
  echo "wave/$n" > "$T/st/auto/wave-$n"; echo "wave/$n" > "$T/st/auto/waved-$n"; printf '%s\n' "$h"; }
export GH_NUMS="6320:Doing 6321:Doing 6322:Doing"
orun_fail rhl 6320 hold review_held >/dev/null; : > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass rhl); rc=$?
[ ! -s "$T/cr.calls" ] && [ ! -s "$T/fix.calls" ] && ok "a run-owned child its land held on review_held is not reviewed or fixed by childcc" || ko "reviews '$(cat "$T/cr.calls")', fix '$(cat "$T/fix.calls")'"
! /usr/bin/grep -q '^requeue 6320' "$WAVE_CALLS" && ok "a run-owned child its land held on review_held is not requeued by childcc" || ko "childcc requeued it: $(cat "$WAVE_CALLS")"
[ "$rc" = 3 ] && ok "a run-owned child whose land held on review_held holds its delivery" || ko "review_held: exit $rc: $out"
/usr/bin/grep -q ' 6320$' "$T/moves" 2>/dev/null && ko "a run-owned child held on review_held moved its card" || ok "a run-owned child held on review_held moves no card"
orun_fail rdl 6321 deadline review_deadline >/dev/null; : > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass rdl); rc=$?
! /usr/bin/grep -q '^requeue 6321' "$WAVE_CALLS" && [ "$rc" = 3 ] && ok "a run-owned child whose review ran out of time is not requeued: its delivery holds" || ko "review_deadline: exit $rc, waves '$(cat "$WAVE_CALLS")': $out"
# (b3) a failed land for a reason that is not the review still requeues a child whose approval stands on its head: the
# approval is established, and the failure is the landing's own (ADR-0151 rule 5 reads the evidence first, never the reason text).
h=$(orun_fail rok 6322 approve merge_conflict); : > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass rok); rc=$?
/usr/bin/grep -q '^requeue 6322 .*its landing failed: merge_conflict' "$WAVE_CALLS" && ok "a failed land unrelated to the review still requeues a child whose approval stands" || ko "standing approval: waves '$(cat "$WAVE_CALLS")': $out"
[ ! -s "$T/cr.calls" ] && [ ! -s "$T/fix.calls" ] && ok "the run-owned child is never reviewed or fixed here, standing approval or not" || ko "reviews '$(cat "$T/cr.calls")', fix '$(cat "$T/fix.calls")'"
# (c) the wave merges only a child whose approval stands: its run approved it on that head, and its review evidence is there.
mkdir -p "$T/co/runs/pick"; wtree pick a; wtree pick b
ha=$(/usr/bin/git -C "$T/co/runs/pick/ws/target-a" rev-parse HEAD); hb=$(/usr/bin/git -C "$T/co/runs/pick/ws/target-b" rev-parse HEAD)
{ echo '{"event": "spawn_accepted", "child": "a", "envelope": "route", "definition": "GitHub issue #6310"}'
  echo '{"event": "spawn_accepted", "child": "b", "envelope": "route", "definition": "GitHub issue #6311"}'
  echo '{"event": "outcome", "node": "a", "outcome": "pass", "reasons": ["ok"]}'
  echo '{"event": "outcome", "node": "b", "outcome": "pass", "reasons": ["ok"]}'
  echo '{"event": "review_round", "node": "a", "round": 1, "head": "'$ha'", "verdict": "approve", "offered": false, "review": {"verdict": "approve", "findings": "x"}}'
  echo '{"event": "review_round", "node": "b", "round": 1, "head": "'$hb'", "verdict": "hold", "offered": false, "review": {"verdict": "hold", "findings": "y"}}'
  echo '{"event": "land", "node": "a", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "before": "b0", "head": "h0", "review": {"verdict": "approve", "findings": "x"}, "rounds": 1}}'
  echo '{"event": "verdict"}'; } > "$T/co/runs/pick/record.jsonl"
echo wave/pick > "$T/st/auto/wave-pick"; echo wave/pick > "$T/st/auto/waved-pick"
ka=$(/usr/bin/git -C "$T/co/runs/pick/ws/target-a" symbolic-ref --quiet --short HEAD | tr / _)
printf '**VERDICT: APPROVE**\n' > "$T/st/review-$ka.md"; echo wave/pick > "$T/st/review-$ka.base"; echo "$ha" > "$T/st/review-$ka.sha"
: > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass pick); rc=$?
[ "$(/usr/bin/grep -c '^enter pick 6310' "$WAVE_CALLS")" = 1 ] && ok "the wave merges the child its run approved on that head" || ko "approved child not entered: $(cat "$WAVE_CALLS")"
[ "$(/usr/bin/grep -c '^enter pick 6311' "$WAVE_CALLS")" = 0 ] && ok "the wave merges no child its run did not approve" || ko "unapproved child entered: $(cat "$WAVE_CALLS")"
[ ! -s "$T/cr.calls" ] && [ ! -s "$T/fix.calls" ] && ok "neither run-owned child is reviewed or fixed by childcc" || ko "reviews '$(cat "$T/cr.calls")', fix '$(cat "$T/fix.calls")'"
[ "$rc" = 3 ] && ok "the child its run did not approve holds the pass, so the wave waits for it" || ko "pick: exit $rc: $out"

# A run that approved the child's head and then ended before landing it left its approval in the record, and the child
# stands at that head: the approval is the evidence the wave reads, so the child enters. A hold here would never clear,
# because no actor reviews that head again (#2089).
export GH_NUMS="6320:Doing"; rm -f "$T"/st/review-*
happ=$(orun appl 6320 approve); rfile appl "$happ" APPROVE
: > "$T/cr.calls"; : > "$T/fix.calls"; : > "$WAVE_CALLS"; rm -f "$T/moves"
out=$(lpass appl); rc=$?
[ "$(/usr/bin/grep -c '^enter appl 6320' "$WAVE_CALLS")" = 1 ] && ok "a child its run approved and never landed enters on that approval (#2089)" || ko "approved head not entered: $(cat "$WAVE_CALLS") $out"
! /usr/bin/grep -q 'approved no head for it' <<<"$out" && ok "an approved head is never deferred as one the run approved no head for (#2089)" || ko "deferred as unapproved: $out"
[ ! -s "$T/cr.calls" ] && [ ! -s "$T/fix.calls" ] && ok "the approved child is still never reviewed or fixed here" || ko "reviews '$(cat "$T/cr.calls")', fix '$(cat "$T/fix.calls")'"

# A deferral is diagnosable: every pass names each child it leaves and the cause, one line per child (#931). A child a
# finished run never approved can never be approved: no actor reviews that head again, so the hold loses its path to one
# and the deferral deadline owns it (ADR-0151 rule 5). The deadline runs, names the cause every pass, and the issue goes
# back to the queue at its bound; childcc still never reviews or fixes the child.
dpass() { echo "$T/co/runs/$1" > "$T/st/active-runs.txt"; (cd "$T/co" && SG_BOARD_VOCAB=shipped python3 "$T/childcc.py.run" 2>&1); }
frun() { # name issue: a finished run whose one child passed and whose record holds no review (not run-owned)
  local n=$1 i=$2 rd="$T/co/runs/$1"; wtree "$n"
  { echo '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'
    echo '{"event": "verdict"}'; } > "$rd/record.jsonl"
  echo "wave/$n" > "$T/st/auto/wave-$n"; echo "wave/$n" > "$T/st/auto/waved-$n"; }
live() { # name issue started|resumed: a run-owned child whose run is still live, so its last start or resume is not a verdict before it
  local n=$1 i=$2 rd="$T/co/runs/$1" h; wtree "$n"; h=$(/usr/bin/git -C "$rd/ws/target-c" rev-parse HEAD)
  { echo '{"event": "run_start", "run_id": "'$n'"}'
    echo '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'
    echo '{"event": "review_round", "node": "c", "round": 1, "head": "'$h'", "verdict": "hold", "offered": false, "review": {"verdict": "hold", "findings": "x"}}'
    [ "$3" = resumed ] && { echo '{"event": "verdict", "verdict": "needs-human"}'; echo '{"event": "resume", "run_id": "'$n'"}'; }
    true; } > "$rd/record.jsonl"
  echo "wave/$n" > "$T/st/auto/wave-$n"; echo "wave/$n" > "$T/st/auto/waved-$n"; }
export GH_NUMS="6400:Doing"; orun dfr 6400 hold >/dev/null
rm -f "$T/st/alerts.log" "$T"/st/cq/*ccdefer* "$T"/st/auto/dwait-*; : > "$T/dfr.out"; : > "$WAVE_CALLS"
rc=0; for i in 1 2 3 4 5; do lpass dfr >> "$T/dfr.out" || rc=$?; done
[ "$rc" = 3 ] && ok "a finished run's unapproved child holds the wave inside its deadline" || ko "dfr: exit $rc"
[ "$(/usr/bin/grep -c 'CHILDCC DEFERRED dfr c #6400: its run approved no head for it' "$T/dfr.out")" = 5 ] && ok "every pass names the run-owned child and its cause (#931)" || ko "named $(/usr/bin/grep -c 'CHILDCC DEFERRED dfr c' "$T/dfr.out") times: $(cat "$T/dfr.out")"
! /usr/bin/grep -q '^requeue 6400' "$WAVE_CALLS" && ok "a finished run's unapproved child is not requeued before its deadline" || ko "requeued before its deadline: $(cat "$WAVE_CALLS")"
[ -e "$T/st/auto/dwait-dfr-c" ] && ok "a finished run's unapproved child runs the deferral deadline (ADR-0151 rule 5)" || ko "no dwait: the deadline never started"
rc=0; lpass dfr >> "$T/dfr.out" || rc=$?
/usr/bin/grep -q '^requeue 6400' "$WAVE_CALLS" && ok "at the deadline the child's issue goes back to the queue" || ko "no requeue at the deadline: exit $rc, $(cat "$WAVE_CALLS")"
/usr/bin/grep -q 'requeue 6400 .*its run approved no head for it' "$WAVE_CALLS" && ok "the requeue names the cause that held it" || ko "requeue line: $(cat "$WAVE_CALLS")"
[ ! -s "$T/cr.calls" ] && [ ! -s "$T/fix.calls" ] && ok "the deadline never reviews or fixes the child here (ADR-0151 rule 5)" || ko "reviews '$(cat "$T/cr.calls")', fix '$(cat "$T/fix.calls")'"
for i in 1 2 3 4 5 6; do lpass dfr >> "$T/dfr.out" || true; done
[ "$(/usr/bin/grep -c '^requeue 6400' "$WAVE_CALLS")" = 1 ] && ok "a head already sent back is never sent back twice (ADR-0149)" || ko "requeued $(/usr/bin/grep -c '^requeue 6400' "$WAVE_CALLS") times"

# A live run still owns its child: the pass leaves that child to its run, so it is never requeued and never gets a deadline,
# because the run may yet review that head (ADR-0151 rule 5). The pass does not wait for it, so it exits 0.
export GH_NUMS="6450:Doing"; orun lv2 6450 hold >/dev/null; live lv2 6450 started
rm -f "$T"/st/auto/dwait-lv2-*; : > "$WAVE_CALLS"; rc=0
for i in 1 2 3 4 5 6 7 8; do out=$(lpass lv2) || rc=$?; done
[ "$rc" = 0 ] && ! /usr/bin/grep -q '^requeue 6450' "$WAVE_CALLS" && ok "a live run's unapproved child is never requeued, past every deadline" || ko "live run: exit $rc, calls $(cat "$WAVE_CALLS"), out $out"
[ ! -e "$T/st/auto/dwait-lv2-c" ] && ok "a live run's child gets no deferral deadline" || ko "dwait written: $(cat "$T/st/auto/dwait-lv2-c" 2>/dev/null)"
# A record that holds a verdict and then a resume is live again: its earlier verdict no longer reads it as finished (ADR-0167).
export GH_NUMS="6451:Doing"; orun rs 6451 hold >/dev/null; live rs 6451 resumed
rm -f "$T"/st/auto/dwait-rs-*; : > "$WAVE_CALLS"; rc=0
for i in 1 2 3 4 5 6 7 8; do out=$(lpass rs) || rc=$?; done
[ "$rc" = 0 ] && ! /usr/bin/grep -q '^requeue 6451' "$WAVE_CALLS" && [ ! -e "$T/st/auto/dwait-rs-c" ] && ok "a run resumed after its verdict is live again, so its child keeps the unbounded hold" || ko "resumed run: exit $rc, dwait $(cat "$T/st/auto/dwait-rs-c" 2>/dev/null), calls $(cat "$WAVE_CALLS")"

# A machine-wide gate hold clears by itself, so it is never counted toward a deadline and never requeues good work (#931): an
# approved child retries the entry on every pass and enters the one after the lock frees.
export GH_NUMS="6401:Doing"; h=$(orun gate 6401 approve land); rfile gate "$h" APPROVE
: > "$WAVE_CALLS"; rm -f "$T/st/auto/dwait-gate-c" "$T/st/auto/cc-gate-c"; touch "$WAVE_GATE_HOLD"; : > "$T/gate.out"
for i in 1 2 3 4 5 6; do lpass gate >> "$T/gate.out"; done
[ "$(/usr/bin/grep -c '^enter gate 6401' "$WAVE_CALLS")" = 6 ] && ok "a machine gate hold retries the entry on every pass" || ko "entries: $(cat "$WAVE_CALLS")"
! /usr/bin/grep -q '^requeue 6401' "$WAVE_CALLS" && ok "a machine gate hold is never counted toward a deadline: no requeue" || ko "requeued a gate-held child: $(cat "$WAVE_CALLS")"
[ ! -e "$T/st/auto/dwait-gate-c" ] && ok "a machine gate hold writes no deferral counter" || ko "dwait written for a gate hold: $(cat "$T/st/auto/dwait-gate-c" 2>/dev/null)"
/usr/bin/grep -q 'CHILDCC DEFERRED gate c #6401: WAVE HOLD #6401: the gate lock is held' "$T/gate.out" && ok "the deferral names the gate lock that held the child" || ko "no gate-lock cause: $(cat "$T/gate.out")"
rm -f "$WAVE_GATE_HOLD"; out=$(lpass gate); rc=$?
/usr/bin/grep -q 'WAVE x entered (#6401)' <<<"$out" && [ -e "$T/st/auto/cc-gate-c" ] && [ "$rc" = 0 ] && ok "the approved child enters its wave on the pass after the lock frees" || ko "after the lock: exit $rc, out $out"

# A deferral of a cause that does not clear carries a deadline whose counter the janitor never removes (#931, ADR-0134):
# after SG_CHILDCC_DEFER_MAX passes on one unchanged (tip, cause) the issue goes back to the queue with that cause, its head
# is recorded, and the wave lands. The counter is auto/dwait-*, not auto/ccdefer-*, because the janitor drops a finished
# run's ccdefer-* markers every cycle (see test-janitor-ccdefer.sh, which runs both scripts over one state dir).
export GH_NUMS="6402:Doing"; frun dft 6402; rm -f "$T"/st/review-*; : > "$WAVE_CALLS"; : > "$T/cr.calls"
o1=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dft); r1=$?
[ "$r1" = 3 ] && [ -e "$T/st/auto/dwait-dft-c" ] && ok "the first pass opens the deadline counter and does not requeue" || ko "pass 1: exit $r1: $o1"
printf '%s\n' "$o1" | /usr/bin/grep -q 'CHILDCC DEFERRED dft c #6402: WAVE HOLD #6402: no review of' && ok "the deferral names the review hold that kept the child out" || ko "no cause: $o1"
o2=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dft); r2=$?
[ "$r2" = 0 ] && /usr/bin/grep -q '^requeue 6402 .*no review of' "$WAVE_CALLS" && ok "the second unchanged pass requeues the issue with its cause, so the wave lands (#931)" || ko "pass 2: exit $r2, calls $(cat "$WAVE_CALLS"): $o2"
[ ! -e "$T/st/auto/dwait-dft-c" ] && ok "the requeued child's deadline counter goes with it" || ko "dwait survived the requeue: $(cat "$T/st/auto/dwait-dft-c" 2>/dev/null)"
[ "$(lq 6402 dft)" = "$(/usr/bin/git -C "$T/co/runs/dft/ws/target-c" rev-parse HEAD)" ] && ok "the ledger holds the head the child was requeued on" || ko "ledger: '$(lq 6402 dft)'"
# The escalation is the one record a reader has of why an issue went back to the queue: one queued comment naming the run,
# the child, the branch and the sha, and one alerts.log line, written once and only after the requeue stands (#808, #931).
dcq=$(ls "$T"/st/cq/*#6402-ccdefer-* 2>/dev/null)
dh=$(/usr/bin/git -C "$T/co/runs/dft/ws/target-c" rev-parse HEAD); dbr=$(/usr/bin/git -C "$T/co/runs/dft/ws/target-c" symbolic-ref --quiet --short HEAD)
[ "$(printf '%s\n' $dcq | /usr/bin/grep -c .)" = 1 ] && ok "a deferral that requeues queues one escalation comment" || ko "escalation comments: '$dcq'"
[ -n "$dcq" ] && /usr/bin/grep -qF 'dft, child c (issue #6402)' "$dcq" && /usr/bin/grep -qF "sha $dh" "$dcq" && /usr/bin/grep -qF "branch $dbr" "$dcq" && /usr/bin/grep -q 'no review of' "$dcq" && /usr/bin/grep -qF "at branch $dbr" "$dcq" \
  && ok "the escalation names the run, the child, the branch, the sha and the cause" || ko "escalation text: $(cat $dcq 2>/dev/null)"
[ "$(/usr/bin/grep -c 'CHILDCC DEFERRED dft c #6402' "$T/st/alerts.log" 2>/dev/null)" = 1 ] && ok "the escalation writes one alerts.log line" || ko "alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
o3=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dft); r3=$?
[ "$r3" = 0 ] && [ "$(/usr/bin/grep -c '^requeue 6402' "$WAVE_CALLS")" = 1 ] && [ "$(printf '%s\n' $(ls "$T"/st/cq/*#6402-ccdefer-* 2>/dev/null) | /usr/bin/grep -c .)" = 1 ] \
  && ok "the requeued child costs nothing on the next pass and is never requeued or escalated twice" || ko "pass 3: exit $r3, calls $(cat "$WAVE_CALLS"), comments '$(ls "$T"/st/cq/*#6402-ccdefer-* 2>/dev/null)'"

# A live fix run is its own bound (#931, refuted over-reach): the child a fix run still works has no deferral deadline, so a
# healthy fix run older than the deadline is never requeued while it works, and the wave keeps waiting for its verdict.
mkdir -p "$T/co/runs/fixrun-6420"
{ echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #6420"}'
  echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'; } > "$T/co/runs/fixrun-6420/record.jsonl"
export GH_NUMS="6420:Doing"; frun dfw 6420; : > "$WAVE_CALLS"; rm -f "$T/st/auto/dwait-dfw-c" "$T"/st/review-*
printf '%s\n' "$T/co/runs/fixrun-6420" > "$T/st/auto/ccfix-dfw-c"   # the marker a starting fix run leaves, with its record still open
o1=$(SG_CHILDCC_DEFER_MAX=2 dpass dfw); r1=$?
[ "$r1" = 3 ] && printf '%s\n' "$o1" | /usr/bin/grep -q 'CHILD FIX WAIT #6420 (dfw c)' && ok "a child with a live fix run holds its wave" || ko "fix wait: exit $r1: $o1"
o2=$(SG_CHILDCC_DEFER_MAX=2 dpass dfw); r2=$?
[ "$r2" = 3 ] && ! /usr/bin/grep -q '^requeue 6420' "$WAVE_CALLS" && [ ! -e "$T/st/auto/dwait-dfw-c" ] \
  && ok "a live fix run is never counted toward the deadline, and the wave still waits for its verdict" || ko "fix wait pass 2: exit $r2, calls $(cat "$WAVE_CALLS"), dwait '$(cat "$T/st/auto/dwait-dfw-c" 2>/dev/null)'"
echo '{"event": "verdict"}' >> "$T/co/runs/fixrun-6420/record.jsonl"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
o3=$(SG_CHILDCC_DEFER_MAX=2 dpass dfw); r3=$?
[ "$r3" = 3 ] && [ -s "$T/cr.calls" ] && ! /usr/bin/grep -q 'CHILD FIX WAIT' <<<"$o3" && ok "the fix run's own verdict ends the wait, so the child is worked again" || ko "after the fix run: exit $r3, cr '$(cat "$T/cr.calls")', out $o3"

# A terminal stop is no refusal (#931): wave.sh answers a stopped or frozen issue with "WAVE STOP" and exit 0, so the child is
# done here and the wave lands, instead of waiting out its age cap on a pass that exits 3 for ever.
export GH_NUMS="6403:Doing"; frun dfu 6403; rm -f "$T"/st/review-* "$T/st/auto/dwait-dfu-c"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfu >/dev/null
touch "$WAVE_REQUEUE_FAIL"; o4=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfu); r4=$?
[ "$r4" = 0 ] && /usr/bin/grep -q 'CHILD DEFER STOP #6403 (dfu c)' <<<"$o4" && /usr/bin/grep -q 'WAVE STOP' <<<"$o4" \
  && ok "a wave's terminal stop lands the wave instead of holding it" || ko "terminal stop: exit $r4: $o4"
[ -z "$(ls "$T"/st/cq/*#6403-* 2>/dev/null)" ] && [ ! -e "$T/st/auto/dwait-dfu-c" ] && ok "a terminal stop queues no comment and leaves no counter" || ko "terminal stop state: cq '$(ls "$T"/st/cq/*#6403-* 2>/dev/null)', dwait '$(cat "$T/st/auto/dwait-dfu-c" 2>/dev/null)'"
rm -f "$WAVE_REQUEUE_FAIL"
# The other terminal answer of wave.sh is no refusal either: a loop-caused requeue that ran out answers "WAVE NEEDS-HUMAN" with
# exit 0 (wave.sh loop_stop, and the `already stopped` answer), and the child is done here too.
export GH_NUMS="6405:Doing"; frun dfw 6405; rm -f "$T"/st/review-* "$T/st/auto/dwait-dfw-c"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfw >/dev/null
touch "$WAVE_NEEDS_HUMAN"; o4b=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfw); r4b=$?
[ "$r4b" = 0 ] && /usr/bin/grep -q 'WAVE NEEDS-HUMAN' <<<"$o4b" && ok "a needs-human stop lands the wave instead of holding it" || ko "needs-human stop: exit $r4b: $o4b"
[ -z "$(ls "$T"/st/cq/*#6405-* 2>/dev/null)" ] && [ ! -e "$T/st/auto/dwait-dfw-c" ] && ok "a needs-human stop queues no comment and leaves no counter" || ko "needs-human state: cq '$(ls "$T"/st/cq/*#6405-* 2>/dev/null)'"
rm -f "$WAVE_NEEDS_HUMAN"
# A wave that answers neither and exits nonzero is still a refusal: no delivery, no comment, and the child keeps its counter.
export GH_NUMS="6404:Doing"; frun dfv 6404; rm -f "$T"/st/review-* "$T/st/auto/dwait-dfv-c"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfv >/dev/null
touch "$WAVE_REQUEUE_ERR"; o5=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfv); r5=$?
[ "$r5" = 3 ] && /usr/bin/grep -q 'CHILD DEFER REQUEUE #6404 (dfv c) refused' <<<"$o5" && ok "a wave that answers nothing is named as a refused requeue, never reported as success" || ko "refused requeue: exit $r5: $o5"
[ -z "$(ls "$T"/st/cq/*#6404-* 2>/dev/null)" ] && [ -e "$T/st/auto/dwait-dfv-c" ] && ok "a refused requeue queues no comment and keeps the deadline counter" || ko "refused requeue state: cq '$(ls "$T"/st/cq/*#6404-* 2>/dev/null)', dwait '$(cat "$T/st/auto/dwait-dfv-c" 2>/dev/null)'"
rm -f "$WAVE_REQUEUE_ERR"; o6=$(SG_HOLD_TRIES=99 SG_CHILDCC_DEFER_MAX=2 dpass dfv); r6=$?
[ "$r6" = 0 ] && /usr/bin/grep -q '^requeue 6404' "$WAVE_CALLS" && ok "the next pass retries the requeue and the wave lands" || ko "retry: exit $r6, calls $(cat "$WAVE_CALLS")"

# The deadline never reaches a child the pass did not try (#931, refuted over-reach): with SG_CHILDCC_BUDGET=1 a pass works
# one child and the rest wait their turn. A healthy sibling is never requeued because the budget, not a hold, kept it.
mkdir -p "$T/co/runs/dsb"
{ for c in a:6410 b:6411 c:6412; do echo '{"event": "spawn_accepted", "child": "'${c%%:*}'", "envelope": "route", "definition": "GitHub issue #'${c#*:}'"}'; done
  for c in a b c; do echo '{"event": "outcome", "node": "'$c'", "outcome": "pass", "reasons": ["ok"]}'; done; } > "$T/co/runs/dsb/record.jsonl"
rm -f "$T"/st/review-*
for c in a b c; do wtree dsb "$c"; /usr/bin/git -C "$T/co/runs/dsb/ws/target-$c" checkout -q -b "dsb-$c"; hh=$(/usr/bin/git -C "$T/co/runs/dsb/ws/target-$c" rev-parse HEAD)
  echo '{"event": "review_round", "node": "'$c'", "round": 1, "head": "'$hh'", "verdict": "approve", "offered": false, "review": {"verdict": "approve", "findings": "x"}}' >> "$T/co/runs/dsb/record.jsonl"
  echo '{"event": "land", "node": "'$c'", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "review", "review": {"verdict": "approve", "findings": "x"}}}' >> "$T/co/runs/dsb/record.jsonl"
  printf '**VERDICT: APPROVE**\n' > "$T/st/review-dsb-$c.md"; echo wave/dsb > "$T/st/review-dsb-$c.base"; echo "$hh" > "$T/st/review-dsb-$c.sha"; done
echo '{"event": "verdict"}' >> "$T/co/runs/dsb/record.jsonl"; echo wave/dsb > "$T/st/auto/wave-dsb"; echo wave/dsb > "$T/st/auto/waved-dsb"
export GH_NUMS="6410:Doing 6411:Doing 6412:Doing"; : > "$WAVE_CALLS"; rm -f "$T/st/auto/dwait-dsb"* "$T/st/auto/cc-dsb"*
for i in 1 2 3; do SG_CHILDCC_BUDGET=1 SG_CHILDCC_DEFER_MAX=2 dpass dsb >/dev/null; done
[ "$(/usr/bin/grep -c '^enter dsb ' "$WAVE_CALLS")" = 3 ] && ok "one pass per child enters all three healthy siblings" || ko "entries: $(cat "$WAVE_CALLS")"
! /usr/bin/grep -q '^requeue ' "$WAVE_CALLS" && ok "a child the pass budget never reached is never requeued: over-reach is fixed" || ko "requeued a budget-skipped child: $(cat "$WAVE_CALLS")"

# A gate-lock hold with no review run in the pass is no work (#1407, spec #1402): it spends none of the pass budget, keeps the
# cursor off the held child, and ends the pass's entries at once, because the next child meets the same holder. The pass exits
# 3 and the held child is tried first on the next pass. A hold after a review this pass ran is work and spends the budget.
mkdir -p "$T/co/runs/hld"
{ for c in a:6420 b:6421 c:6422; do echo '{"event": "spawn_accepted", "child": "'${c%%:*}'", "envelope": "route", "definition": "GitHub issue #'${c#*:}'"}'; done
  for c in a b c; do echo '{"event": "outcome", "node": "'$c'", "outcome": "pass", "reasons": ["ok"]}'; done; } > "$T/co/runs/hld/record.jsonl"
rm -f "$T"/st/review-*
for c in a b c; do wtree hld "$c"; /usr/bin/git -C "$T/co/runs/hld/ws/target-$c" checkout -q -b "hld-$c"; hh=$(/usr/bin/git -C "$T/co/runs/hld/ws/target-$c" rev-parse HEAD)
  echo '{"event": "review_round", "node": "'$c'", "round": 1, "head": "'$hh'", "verdict": "approve", "offered": false, "review": {"verdict": "approve", "findings": "x"}}' >> "$T/co/runs/hld/record.jsonl"
  echo '{"event": "land", "node": "'$c'", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "review", "review": {"verdict": "approve", "findings": "x"}}}' >> "$T/co/runs/hld/record.jsonl"
  printf '**VERDICT: APPROVE**\n' > "$T/st/review-hld-$c.md"; echo wave/hld > "$T/st/review-hld-$c.base"; echo "$hh" > "$T/st/review-hld-$c.sha"; done
echo '{"event": "verdict"}' >> "$T/co/runs/hld/record.jsonl"; echo wave/hld > "$T/st/auto/wave-hld"; echo wave/hld > "$T/st/auto/waved-hld"
export GH_NUMS="6420:Doing 6421:Doing 6422:Doing 6430:Doing 6431:Doing"; : > "$WAVE_CALLS"; rm -f "$T/st/auto/dwait-hld"* "$T/st/auto/cc-hld"* "$T/st/auto/childcc-cursor" "$T/st/auto/childcc-round"
touch "$WAVE_GATE_HOLD"; hout=$(SG_CHILDCC_BUDGET=2 dpass hld); hrc=$?
[ "$(/usr/bin/grep -c '^enter hld ' "$WAVE_CALLS")" = 1 ] && ok "a gate-lock hold ends the pass's entries: one entry, though the budget allows two" || ko "entries after a lock hold: $(cat "$WAVE_CALLS")"
[ "$hrc" = 3 ] && ok "a gate-lock hold exits with the children-left code" || ko "lock hold exit $hrc: $hout"
[ ! -s "$T/st/auto/childcc-cursor" ] && ok "a gate-lock hold with no review spends no budget and keeps the cursor before the held child" || ko "cursor after a lock hold: '$(cat "$T/st/auto/childcc-cursor" 2>/dev/null)'"
[ ! -e "$T/st/auto/dwait-hld-a" ] && ! /usr/bin/grep -q '^requeue ' "$WAVE_CALLS" && ok "a gate-lock hold counts toward no deferral deadline and requeues nothing" || ko "dwait or requeue after a lock hold: $(cat "$WAVE_CALLS")"
rm -f "$WAVE_GATE_HOLD"; : > "$WAVE_CALLS"; SG_CHILDCC_BUDGET=1 dpass hld >/dev/null
[ "$(/usr/bin/grep '^enter hld ' "$WAVE_CALLS" | head -1)" = "enter hld 6420" ] && ok "the held child is tried first on the next pass" || ko "first entry on the next pass: $(cat "$WAVE_CALLS")"
# A hold after a review this pass ran is work: it spends the budget, and the cursor moves to the child.
rm -f "$T/st/auto/childcc-cursor" "$T/st/auto/childcc-round"; rm -f "$T"/st/review-*; : > "$T/cr.calls"
run2 hlr 6430 6431; : > "$WAVE_CALLS"; touch "$WAVE_GATE_HOLD"; SG_CHILDCC_BUDGET=2 dpass hlr >/dev/null; rm -f "$WAVE_GATE_HOLD"
[ -s "$T/cr.calls" ] && [ "$(cat "$T/st/auto/childcc-cursor" 2>/dev/null)" = "hlr a" ] && ok "a hold after a review the pass ran still spends the budget" || ko "review calls '$(cat "$T/cr.calls")', cursor '$(cat "$T/st/auto/childcc-cursor" 2>/dev/null)'"
[ "$(/usr/bin/grep -c '^enter hlr ' "$WAVE_CALLS")" = 1 ] && ok "a lock hold after a review ends the entries too" || ko "entries: $(cat "$WAVE_CALLS")"
# A child with no change past its base (#1406, #2984): childreview.sh exits 6. The issue ends one no-change fail at once
# (wave.sh fail ... nochange, #2245), with the reason, never a requeue that a later run repeats on the same base, and the head
# is recorded so the next pass does not review it again.
cat > "$T/childreview.sh" <<'CR'
#!/bin/bash
echo "$* $(git rev-parse HEAD)" >> "$FIX_DIR/cr.calls"
k=$(git symbolic-ref --quiet --short HEAD | tr / _); echo "provisional: the fix already exists at this head" > "$SG_STATE/reason-$k"
echo "CHILDREVIEW x: no change past abc, nothing to review (#5950)" >&2; exit 6
CR
export GH_NUMS="5950:Doing"; run nch 5950 n; wtree nch; echo wave/nch > "$T/st/auto/wave-nch"; echo wave/nch > "$T/st/auto/waved-nch"
: > "$WAVE_CALLS"; : > "$T/cr.calls"; out=$(pass nch); rc=$?; pass nch >/dev/null
[ "$(/usr/bin/grep -c '^nch 5950' "$T/cr.calls")" = 1 ] && ok "a no-change child is reviewed once" || ko "no-change reviews: $(cat "$T/cr.calls")"
[ "$(/usr/bin/grep -c '^fail 5950 nch ' "$WAVE_CALLS")" = 1 ] && /usr/bin/grep -q '^fail 5950 nch .*the child made no change.*the fix already exists.* nochange$' "$WAVE_CALLS" && ! /usr/bin/grep -q '^requeue 5950 ' "$WAVE_CALLS" && ok "a no-change child ends one no-change fail with the reason, never a requeue (#2984)" || ko "no-change fail: $(cat "$WAVE_CALLS")"
! /usr/bin/grep -q '^enter nch' "$WAVE_CALLS" && [ "$rc" != 3 ] && ok "a no-change child enters nothing and holds no wave" || ko "no-change: exit $rc, waves $(cat "$WAVE_CALLS")"

# A child whose own gate ended unproven is a finished child all the same (ADR-0164): the work is done and the proof could not
# finish, so its commits enter its wave, and the entry is the place that records the obligation the tree still owes. A child
# whose gate ended `fail` measured a red, and a measured red never enters: it is no finished child here (#1997).
unprun() { # name issue outcome: a finished run whose one child ended `outcome`, with its run's approval at the child's head
  local n=$1 i=$2 o=$3 h k; wtree "$n"; h=$(/usr/bin/git -C "$T/co/runs/$n/ws/target-c" rev-parse HEAD)
  { echo '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c", "outcome": "'$o'", "reasons": ["unproven:c"]}'
    echo '{"event": "review_round", "node": "c", "round": 1, "head": "'$h'", "verdict": "approve", "offered": false, "review": {"verdict": "approve", "findings": "x"}}'
    echo '{"event": "land", "node": "c", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "review", "review": {"verdict": "approve", "findings": "x"}}}'
    echo '{"event": "verdict"}'; } > "$T/co/runs/$n/record.jsonl"
  echo "wave/$n" > "$T/st/auto/wave-$n"; echo "wave/$n" > "$T/st/auto/waved-$n"
  k=$(/usr/bin/git -C "$T/co/runs/$n/ws/target-c" symbolic-ref --quiet --short HEAD | tr / _)
  printf '**VERDICT: APPROVE**\n' > "$T/st/review-$k.md"; echo "wave/$n" > "$T/st/review-$k.base"; echo "$h" > "$T/st/review-$k.sha"; }
export GH_NUMS="5110:Doing 5111:Doing"
unprun unp 5110 unproven; : > "$WAVE_CALLS"; rm -f "$T/moves"; out=$(lpass unp); rc=$?
[ "$(ent unp 5110)" = 1 ] && [ -e "$T/st/auto/cc-unp-c" ] && ok "a child whose gate ended unproven enters its wave" || ko "unproven child not entered: exit $rc, waves '$(cat "$WAVE_CALLS")', out $out"
/usr/bin/grep -q 'WAVE x entered (#5110)' <<<"$out" && ok "the wave admits the unproven child, so its entry stands" || ko "unproven child's entry did not stand: out $out"
unprun flr 5111 fail; : > "$WAVE_CALLS"; rm -f "$T/moves"; out=$(lpass flr); rc=$?
[ "$(ent flr 5111)" = 0 ] && [ ! -e "$T/st/auto/cc-flr-c" ] && ok "a child whose gate ended fail never enters its wave" || ko "failed child entered: waves '$(cat "$WAVE_CALLS")', out $out"
/usr/bin/grep -q ' 5111$' "$T/moves" 2>/dev/null && ko "a failed child moved to Code Complete" || ok "a failed child moves no card"
# A child of an issue whose body holds an "after" image owes a render (#2833): its worktree holds a PNG under
# .scratch/renders/, or it does not enter. The check reads that evidence, never the child's status word, so a child that
# answers `pass` with no render is held too. The refusal is a CHANGES review with one Blocking finding and a repro, which
# starts one fix run; the issue gets one comment per tip that names the missing render, and the card keeps its column.
export GH_NUMS="5110:Doing 5111:Doing 5120:Doing 5121:Doing 5122:Doing 5123:Doing"
printf '## Before\n\n![before](https://x.test/b.png)\n\n## After\n\n![after](https://x.test/a.png)\n' > "$T/body-5120"
cp "$T/body-5120" "$T/body-5121"; cp "$T/body-5120" "$T/body-5123"
printf '## Before\n\n![before](https://x.test/b.png)\n\nThe page shows one row per run.\n' > "$T/body-5122"
unprun aft 5120 unproven; : > "$WAVE_CALLS"; : > "$T/fix.calls"; rm -f "$T/moves"; out=$(lpass aft); rc=$?; out2=$(lpass aft)
[ "$(ent aft 5120)" = 0 ] && [ ! -e "$T/st/auto/cc-aft-c" ] && ok "2833 an after image with no render evidence never enters its wave" || ko "2833 the child with no render entered: waves '$(cat "$WAVE_CALLS")', out $out"
[ "$(/usr/bin/grep -c '^aft 5120 ' "$T/fix.calls")" = 1 ] && ok "2833 the missing render starts one fix run" || ko "2833 fix runs: '$(cat "$T/fix.calls")', out $out $out2"
k=$(/usr/bin/git -C "$T/co/runs/aft/ws/target-c" symbolic-ref --quiet --short HEAD | tr / _); h=$(/usr/bin/git -C "$T/co/runs/aft/ws/target-c" rev-parse HEAD)
[ "$(python3 "$T/revformat.py" --classify "$T/st/review-$k.md" "$T/st/review-$k.sha" "$h")" = changes ] && /usr/bin/grep -q 'repro: .*\.scratch/renders' "$T/st/review-$k.md" && /usr/bin/grep -q 'test-hub-e2e.py' "$T/st/review-$k.md" \
  && ok "2833 the refusal is a CHANGES review with one Blocking finding and a repro" || ko "2833 the review: $(cat "$T/st/review-$k.md" 2>/dev/null)"
[ "$(ls "$T/st/cq" | /usr/bin/grep -c '#5120-render-')" = 1 ] && /usr/bin/grep -q '\.scratch/renders/' "$T"/st/cq/*#5120-render-* && ok "2833 the issue gets one comment per tip that names the missing render" || ko "2833 render comments: $(ls "$T/st/cq")"
/usr/bin/grep -q ' 5120$' "$T/moves" 2>/dev/null && ko "2833 the held child moved its card: $(cat "$T/moves")" || ok "2833 the held child's card keeps its column"
[ "$rc" = 3 ] && ok "2833 the held child holds its wave" || ko "2833 a pass with a held render exited $rc"
unprun aev 5121 unproven; mkdir -p "$T/co/runs/aev/ws/target-c/.scratch/renders"; printf 'png' > "$T/co/runs/aev/ws/target-c/.scratch/renders/page.png"
: > "$WAVE_CALLS"; : > "$T/fix.calls"; out=$(lpass aev)
[ "$(ent aev 5121)" = 1 ] && [ -e "$T/st/auto/cc-aev-c" ] && ! /usr/bin/grep -q '^aev ' "$T/fix.calls" && ok "2833 an after image with a render under .scratch/renders/ enters" || ko "2833 the rendered child did not enter: waves '$(cat "$WAVE_CALLS")', out $out"
unprun anb 5122 unproven; : > "$WAVE_CALLS"; : > "$T/fix.calls"; out=$(lpass anb)
[ "$(ent anb 5122)" = 1 ] && [ -e "$T/st/auto/cc-anb-c" ] && ok "2833 an unproven child of an issue with no after image enters as before" || ko "2833 the before-only child did not enter: waves '$(cat "$WAVE_CALLS")', out $out"
unprun apa 5123 pass; : > "$WAVE_CALLS"; : > "$T/fix.calls"; out=$(lpass apa)
[ "$(ent apa 5123)" = 0 ] && [ ! -e "$T/st/auto/cc-apa-c" ] && [ "$(/usr/bin/grep -c '^apa 5123 ' "$T/fix.calls")" = 1 ] && ok "2833 a child that answers pass with no render never enters" || ko "2833 the pass child with no render: waves '$(cat "$WAVE_CALLS")', fix '$(cat "$T/fix.calls")', out $out"
# A bare `loop-bug` is a full drain (#1980): it stops the child entry as well. This replaces owner answer B1, which let a
# bare loop-bug stop nothing; the contradiction is named for #2041.
cat > "$T/issues-open.json" <<'JSON'
{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":597,"title":"A defect beside the path","body":"","state":"OPEN","labels":{"nodes":[{"name":"loop-bug"}]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}
JSON
out=$(pass lb)
echo "$out" | /usr/bin/grep -q "LOOP STOP: #597" && ok "a bare loop-bug drains the child entry (#1980)" || ko "a bare loop-bug did not stop the pass: $out"

rm -rf "$T"; exit $fail
