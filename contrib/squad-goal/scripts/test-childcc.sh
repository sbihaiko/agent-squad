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
printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}' > "$T/issues-open.json"
export SG_SQUAD=$(cd "$(dirname "$src")/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a review takes a review slot (caps.reviews): a throwaway budget, this repository's squad.caps
export GH_LOG=$T/gh.log GH_ISSUES_OPEN=$T/issues-open.json; export SG_STATE=$T/st SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 PATH="$T/bin:$PATH" GH_NUMS="2:Todo 3:Testing 4:In_Progress 5101:Todo"
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
printf '#!/bin/bash\necho "$* $(git rev-parse HEAD)" >> "%s/cr.calls"\n' "$T" > "$T/childreview.sh"   # each review names the head it read
run rvw 5102 n; wtree rvw; echo wave/rvw > "$T/st/auto/wave-rvw"; : > "$T/cr.calls"
h=$(/usr/bin/git -C "$T/co/runs/rvw/ws/target-c" rev-parse HEAD)
echo "$h" > "$T/st/auto/rvd-rvw-c"   # the claim as a pass wrote it before it died: the head, and no pass alive behind it
pass rvw >/dev/null
[ "$(grep -c '^rvw 5102' "$T/cr.calls")" = 1 ] && ok "a claim whose pass died is no claim: the child is reviewed again" || ko "a dead pass's claim held the review: $(cat "$T/cr.calls")"
c1=$(grep -c '^rvw 5102' "$T/cr.calls"); sleep 30 & live=$!; echo "$h $live" > "$T/st/auto/rvd-rvw-c"; rm -f "$T/st/auto/cc-rvw-c" "$T/st/auto/requeued-rvw-c"
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

# Code Review (#603): with the shipped vocabulary, childcc moves a Doing card to Code Review when it starts the review of a
# child and when it defers a child that waits for review, through board.sh, once per child. A card past Code Review (Code
# Complete) and a card a requeue sent to Todo are never moved; the old vocabulary has no Code Review and gets no write.
crv() { cat "$T/moves" 2>/dev/null | /usr/bin/grep -c "^x 4 Code Review $1\$"; }
run2 crv 5700 5701; : > "$WAVE_CALLS"; : > "$T/cr.calls"; rm -f "$T/moves"
export GH_NUMS="5700:Doing 5701:Doing"
SG_BOARD_VOCAB=shipped pass crv >/dev/null
[ "$(crv 5700)" = 1 ] && /usr/bin/grep -q '^crv 5700' "$T/cr.calls" && ok "the review start moves the child's card to Code Review" || ko "review start: moves '$(cat "$T/moves" 2>/dev/null)', reviews '$(cat "$T/cr.calls")'"
[ "$(crv 5701)" = 1 ] && ! /usr/bin/grep -q '^crv 5701' "$T/cr.calls" && ok "a child deferred for review moves its card to Code Review" || ko "defer: moves '$(cat "$T/moves" 2>/dev/null)'"
SG_BOARD_VOCAB=shipped pass crv >/dev/null
[ "$(crv 5700)" = 1 ] && [ "$(crv 5701)" = 1 ] && ok "Code Review is written once per child" || ko "Code Review writes after two passes: '$(cat "$T/moves" 2>/dev/null)'"
run2 crl 5710 5711; : > "$WAVE_CALLS"; rm -f "$T/moves"
export GH_NUMS="5710:Code_Complete 5711:Todo"
SG_BOARD_VOCAB=shipped pass crl >/dev/null; SG_BOARD_VOCAB=shipped pass crl >/dev/null
[ "$(crv 5710)" = 0 ] && ok "a card in Code Complete never goes back to Code Review" || ko "Code Complete card moved back: '$(cat "$T/moves")'"
[ "$(crv 5711)" = 0 ] && ok "a card a requeue sent to Todo is not moved to Code Review" || ko "Todo card moved: '$(cat "$T/moves")'"
run2 cro 5720 5721; : > "$WAVE_CALLS"; rm -f "$T/moves"
export GH_NUMS="5720:In_Progress 5721:In_Progress"
pass cro >/dev/null
! /usr/bin/grep -q 'Code Review' "$T/moves" 2>/dev/null && ok "the old vocabulary gets no Code Review write" || ko "old vocabulary wrote Code Review: '$(cat "$T/moves")'"

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
rrun rhd 5801 fail '"review_held"' hold; /usr/bin/git -C "$T/co/runs/rhd/ws/target-c" rev-parse HEAD > "$T/st/auto/requeued-rhd-c"; : > "$WAVE_CALLS"; : > "$T/cr.calls"
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
[ "$(/usr/bin/grep -c '^rst 5300' "$T/cr.calls")" = 1 ] && [ "$(ent rst 5300)" = 2 ] && ok "an approved child its entry holds is reviewed here on the next pass" || ko "approved but held: reviews '$(cat "$T/cr.calls")', entries '$(cat "$WAVE_CALLS")'"

# A dispatch child with fix entries (#570) has one passing `outcome` per entry, entry 1, 2, ... of the same child. The pass
# works the child once, at its last entry's head: one review and one entry, never one per entry (#611).
mkdir -p "$T/co/runs/fx"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #5900"}'
  echo '{"event": "outcome", "node": "c", "entry": 1, "outcome": "pass", "reasons": ["one"]}'
  echo '{"event": "review_round", "node": "c", "round": 1, "head": "h1", "verdict": "fix"}'
  echo '{"event": "outcome", "node": "c", "entry": 2, "outcome": "pass", "reasons": ["two"]}'; } > "$T/co/runs/fx/record.jsonl"
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

rm -rf "$T"; exit $fail
