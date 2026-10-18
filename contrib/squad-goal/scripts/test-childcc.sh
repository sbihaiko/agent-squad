#!/bin/bash
# Regression test for childcc.py (stub gh + board, no network). usage: bash test-childcc.sh [path/to/childcc.py]
# Live pass moves at once; a finished run is backfilled only when the card is Todo/In Progress/Blocked, never past Code Complete.
src=${1:-$(dirname "$0")/childcc.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/childcc.py"; cp "$(dirname "$src")/issue_state.py" "$(dirname "$src")/child_issues.py" "$(dirname "$src")/ghread.py" "$T/"
printf '#!/bin/bash\necho "$@" >> %s/moves\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
a="$*"
case $a in
  *"graphql"*) cat "$GH_ISSUES_OPEN";;   # the open-issue list the pass reads once for a loop defect (#596)
  *"-q .state"*) echo open;;
  *"POST"*"/sub_issues"*) echo "link $a" >> "$GH_LOG"; cat >/dev/null;;
  *"/sub_issues"*) echo $GH_KIDS | tr ' ' '\n' | head -n "$(cat "$GH_LOG" 2>/dev/null | grep -c '^link')" || true;;   # the sub-issues read: the children the carrier carries so far, one per link written
  *"POST"*"repos/x/y/issues"*) echo "create $(cat)" >> "$GH_LOG"; echo '{"number":900}';;
  *"issues?state=all"*) ;;
  *"repos/x/y/issues/"*) echo '{"id":1}';;
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
printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}' > "$T/issues-open.json"
export GH_LOG=$T/gh.log GH_KIDS="2000 2001" GH_ISSUES_OPEN=$T/issues-open.json; export SG_STATE=$T/st SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 PATH="$T/bin:$PATH" GH_NUMS="2:Todo 3:Testing 4:In_Progress 5101:Todo"
cp "$T/childcc.py" "$T/childcc.py.run"; sed -i.bak "s#^board = .*#board = '$T/board.sh'#" "$T/childcc.py.run"
out=$(cd "$T/co" && python3 "$T/childcc.py.run" 2>&1)
echo "$out" | tail -20 >&2
m=$(cat "$T/moves" 2>/dev/null)
echo "$m" | /usr/bin/grep -q ' 1$' && ok "live pass: moved" || ko "live pass not moved"
echo "$m" | /usr/bin/grep -q ' 2$' && ok "finished run, card Todo: backfilled" || ko "Todo card not backfilled"
echo "$m" | /usr/bin/grep -q ' 4$' && ok "finished run, card In Progress: backfilled" || ko "In Progress card not backfilled"
echo "$m" | /usr/bin/grep -q ' 3$' && ko "card past Code Complete was moved" || ok "finished run, card Testing: left alone"
echo "$m" | /usr/bin/grep -q ' 1487$' && ok "GitHub issue N (no hash): moved" || ko "GitHub issue N (no hash) not moved"
ls "$T/st/cq"/*#1487-cc-*.md >/dev/null 2>&1 && ok "GitHub issue N (no hash): comment queued" || ko "GitHub issue N (no hash): no comment"
echo "$m" | /usr/bin/grep -q ' 1491$' && ok "GitHub issues N: moved" || ko "GitHub issues N not moved"
echo "$m" | /usr/bin/grep -q ' 1492$' && ok "gui/N / wt-as-N: moved" || ko "gui/N / wt-as-N not moved"
echo "$m" | /usr/bin/grep -q ' 1493$' && ok "Refs #N, the dispatchers' own link: moved (#555)" || ko "Refs #N not moved: the router wrote issue N with no hash"
echo "$m" | /usr/bin/grep -q ' 1500$' && ok "hash pattern still wins first" || ko "hash pattern lost first-match"
echo "$out" | /usr/bin/grep -q 'CHILDCC UNMAPPED unmapped c' && ok "unmapped child logged once" || ko "unmapped child not logged: $out"
echo "$out" | /usr/bin/grep -c 'CHILDCC UNMAPPED unmapped c' | /usr/bin/grep -qx 1 && ok "unmapped child logged once only" || ko "unmapped child logged more than once"
grep -c '^create' "$T/gh.log" | grep -qx 1 && ok "two children: one parent created, none for the one-child run" || ko "parent creates: $(cat "$T/gh.log" 2>/dev/null)"
[ "$(grep -c '^link' "$T/gh.log")" = 2 ] && ok "two children: both linked as sub-issues" || ko "links: $(cat "$T/gh.log" 2>/dev/null)"
echo "$out" | /usr/bin/grep -q 'RUN PARENT #900 (two)' && ok "the run's parent is reported" || ko "no parent report: $out"
grep '^create' "$T/gh.log" | grep -q '"labels": \["run-parent", "enhancement"\]' && ok "the parent carries its run and type labels, so no pass dispatches it (#340)" || ko "parent create body: $(grep '^create' "$T/gh.log")"
echo "$out" | /usr/bin/grep -q 'RUN PARENT .*(fin-two)' && ko "a finished run got a parent (#340)" || ok "a finished run gets no parent (#340)"

# A run with a wave (#375): the entry is wave.sh's, stubbed here. A child that its gates refused is tried again only on a new
# HEAD, and a run whose wave landed (wave-<run> gone, waved-<run> kept) never moves a child's card to Code Complete.
cat > "$T/wave.sh" <<'W'
#!/bin/bash
echo "$*" >> "$WAVE_CALLS"
[ -e "$WAVE_FAIL" ] && { echo "WAVE REQUEUE #$3: the local gates failed: x"; exit 1; }
[ "$3" = 5300 ] && { echo "WAVE HOLD #$3: no review yet"; exit 1; }   # a held child: no requeue, so every pass tries it again
[ "$3" = 5600 ] && { echo "WAVE STOP #$3: requeued 3 times, so it is not dispatched again: x"; exit 1; }   # the requeue cap stopped it
echo "WAVE gui/$3 entered (#$3)"
W
export WAVE_CALLS=$T/wave.calls WAVE_FAIL=$T/wave.fail; : > "$WAVE_CALLS"
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
# A review claim left by a pass that died mid-review (#502): the loop stopped, childreview.sh was killed, no review head was
# kept. The claim names the head, so every later pass skipped the review and the entry held the child for good. A claim
# whose pass is gone is no claim; one whose pass is alive still is.
printf '#!/bin/bash\necho "$*" >> "%s/cr.calls"\n' "$T" > "$T/childreview.sh"
run rvw 5102 n; wtree rvw; echo wave/rvw > "$T/st/auto/wave-rvw"; : > "$T/cr.calls"
h=$(/usr/bin/git -C "$T/co/runs/rvw/ws/target-c" rev-parse HEAD)
echo "$h" > "$T/st/auto/rvd-rvw-c"   # the claim as a pass wrote it before it died: the head, and no pass alive behind it
pass rvw >/dev/null
[ "$(grep -c '^rvw 5102' "$T/cr.calls")" = 1 ] && ok "a claim whose pass died is no claim: the child is reviewed again" || ko "a dead pass's claim held the review: $(cat "$T/cr.calls")"
c1=$(grep -c '^rvw 5102' "$T/cr.calls"); sleep 30 & live=$!; echo "$h $live" > "$T/st/auto/rvd-rvw-c"; rm -f "$T/st/auto/cc-rvw-c" "$T/st/auto/requeued-rvw-c"
pass rvw >/dev/null; kill $live 2>/dev/null
[ "$(grep -c '^rvw 5102' "$T/cr.calls")" = "$c1" ] && ok "a claim whose pass is alive holds: the review is not repeated" || ko "a live claim was reviewed again: $(cat "$T/cr.calls")"
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
pass bud >/dev/null; rc=$?
[ "$(ent bud 5200)" = 1 ] && [ "$(ent bud 5201)" = 1 ] && ok "the next pass works the child left" || ko "entries after two passes: $(cat "$WAVE_CALLS")"
[ "$rc" = 0 ] && ok "a pass that leaves no child exits 0" || ko "the last pass exited $rc"
[ -e "$T/st/auto/cc-bud-a" ] && [ -e "$T/st/auto/cc-bud-b" ] && ok "both children got their marker" || ko "markers: $(/bin/ls "$T/st/auto")"
# a held child (5300, no requeue: it is tried on every pass) does not take every pass: the other child gets its turn
run2 fair 5300 5301; : > "$WAVE_CALLS"
pass fair >/dev/null; pass fair >/dev/null; rc=$?
[ "$(ent fair 5300)" = 1 ] && [ "$(ent fair 5301)" = 1 ] && ok "a held child does not take every pass" || ko "entries for a held child and its sibling: $(cat "$WAVE_CALLS")"
[ "$rc" = 0 ] && ok "once each child had its turn, the pass exits 0" || ko "the pass after each child's turn exited $rc"
# a child whose requeue holds it on an unchanged head costs no work: its sibling is worked in the same pass
run2 sup 5400 5401; : > "$WAVE_CALLS"; /usr/bin/git -C "$T/co/runs/sup/ws/target-a" rev-parse HEAD > "$T/st/auto/requeued-sup-a"
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
[ "$(cat "$T/st/auto/requeued-wst-c" 2>/dev/null)" = "$(/usr/bin/git -C "$T/co/runs/wst/ws/target-c" rev-parse HEAD)" ] && [ "$(ent wst 5600)" = 1 ] \
  && ok "a stopped child gets the requeued marker and is not entered again on the same head" || ko "WAVE STOP: marker '$(cat "$T/st/auto/requeued-wst-c" 2>/dev/null)', entries $(cat "$WAVE_CALLS")"
# A loop defect halts delivery too (#596, ADR-0146): a defect in the loop's own machinery is fixed outside the loop, so no
# child enters a wave and no card moves while one carries `loop-bug`. The pass reads the open issues once, names the issue in
# the same line the dispatch passes print, and writes nothing else. Closing the issue resumes the entry with no marker.
run lb 5600 n; wtree lb; echo wave/lb > "$T/st/auto/wave-lb"; echo wave/lb > "$T/st/auto/waved-lb"
: > "$WAVE_CALLS"; rm -f "$T/moves" "$T/st/auto/cc-lb-c" "$T/st/cycle.log"
cat > "$T/issues-open.json" <<'JSON'
{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":596,"title":"The loop cannot land its own fix","body":"","state":"OPEN","labels":{"nodes":[{"name":"loop-bug"}]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}
JSON
out=$(pass lb)
[ "$(ent lb 5600)" = 0 ] && ok "a loop defect enters no child" || ko "a child entered while a loop defect is open: $(cat "$WAVE_CALLS")"
/usr/bin/grep -q ' 5600$' "$T/moves" 2>/dev/null && ko "a loop defect moved a card" || ok "a loop defect moves no card"
echo "$out" | /usr/bin/grep -q "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" && ok "the pass names the same stop line" || ko "no stop line: $out"
/usr/bin/grep -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$T/st/cycle.log" 2>/dev/null && ok "the stop is published to the loop log" || ko "cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ ! -e "$T/st/auto/cc-lb-c" ] && ok "the stopped pass leaves no entry marker" || ko "the stopped pass wrote a marker"
printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}' > "$T/issues-open.json"
out=$(pass lb)
[ "$(ent lb 5600)" = 1 ] && ok "closing the issue resumes the entry with no other action" || ko "entry did not resume: $(cat "$WAVE_CALLS")"

rm -rf "$T"; exit $fail
