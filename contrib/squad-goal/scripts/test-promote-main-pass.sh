#!/bin/bash
# The stg-to-main promotion pass (ADR-0145, ADR-0173): promote-main-pass.sh asks for the complete CI tier on the stg tip, by
# the `promote` label on the one stg -> main pull request, and moves main to the proven sha by a fast-forward. A red tier holds
# main and files one bug; an unproven one holds main and files nothing; an unfinished one holds nothing else. A red's hold
# names the sha it measured, so a new stg tip is never held by a verdict about another sha (#1889).
# Offline: a bare local remote, a stub gh that answers the pull request, the labels, the check runs and the issues, and a
# workflow fixture that names two complete tier jobs. Commit dates are set by hand, so the rule is read against the real clock.
# usage: bash test-promote-main-pass.sh
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done   # hermetic: the live loop exports its own SG_* (#1410)
unset SERIAL_TRY
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git; ST=$T/st
export SG_STATE=$ST SQUAD_CAPS_DIR=$T/caps SG_PROMOTE_REMOTE=$R SG_REPO=x/emu SG_OWNER=x SG_PROJECT=1 GIT_TERMINAL_PROMPT=0
export SG_PROMOTE_MAIN_QUIET_S=3600 SG_PROMOTE_MAIN_MAX_S=14400 SG_PROMOTE_MAIN_FLIGHT_S=3600
mkdir -p "$ST" "$T/bin"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
now=$(date +%s)
at() { echo "@$(( now - $1 )) +0000"; }   # <age in seconds>: a git date that old
$W checkout -q -b main; GIT_COMMITTER_DATE=$(at 90000) $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
# adv <branch> <message> <age>: one commit on the branch, with that committer age.
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; GIT_COMMITTER_DATE=$(at "$3") $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# merged <message> <merge age> <work age>: a promotion as stg gets it, a merge commit whose second parent is a work commit.
merged() { $W fetch -q origin; $W checkout -q -B side origin/stg; GIT_COMMITTER_DATE=$(at "$3") $W commit -q --allow-empty -m "work $1"
  $W checkout -q -B stg origin/stg; GIT_COMMITTER_DATE=$(at "$2") $W merge -q --no-ff -m "$1" side; $W push -q origin stg; }
# realign: main takes the stg tip, so the next case starts with nothing to promote.
realign() { git -C "$R" update-ref refs/heads/main "$(tip stg)"; rm -f "$ST"/promote-main-* "$ST"/auto/promote-main-* "$ST/alerts.log" "$T/pr" "$T/prhead" "$T"/issue-*.state; : > "$T/gh.log"; }
# The workflow fixture: one fast job, the guard, and two complete tier jobs.
printf "jobs:\n  ruff:\n    name: ruff\n  complete-guard:\n    name: complete-guard\n    if: github.head_ref == 'stg' && github.event.label.name == 'promote'\n  full-xdist:\n    name: full-xdist\n    if: github.head_ref == 'stg' && github.event.label.name == 'promote'\n    needs: [complete-guard]\n  gui-e2e:\n    name: gui-e2e\n    if: github.head_ref == 'stg' && github.event.label.name == 'promote'\n    needs: [complete-guard]\n" > "$T/ci.yml"
printf "jobs:\n  ruff:\n    name: ruff\n" > "$T/ci-fast-only.yml"
export SG_CI_WORKFLOW=$T/ci.yml
# iso <epoch>: the "YYYY-MM-DDTHH:MM:SSZ" form GitHub writes for a time, whatever date(1) this host ships.
iso() { date -u -r "$1" +%FT%TZ 2>/dev/null || date -u -d "@$1" +%FT%TZ; }
# checks <sha> <full-xdist> <gui-e2e>: the check runs of one sha. A word is a conclusion, `queued`, `in_progress` or `none`.
# CHK_AGE moves the start back that many seconds: a run of an older event on the same sha.
checks() { local sha=$1 out="" n c i=0 ts; shift
  ts=$(iso $(( $(date +%s) - ${CHK_AGE:-0} )))
  for n in full-xdist gui-e2e; do c=$1; shift; i=$((i + 1))
    case $c in none) ;;
      queued|in_progress) out="$out{\"id\":$i,\"name\":\"$n\",\"status\":\"$c\",\"conclusion\":null,\"started_at\":null,\"created_at\":\"$ts\"},";;
      *) out="$out{\"id\":$i,\"name\":\"$n\",\"status\":\"completed\",\"conclusion\":\"$c\",\"started_at\":\"$ts\",\"created_at\":\"$ts\",\"details_url\":\"https://github.com/x/emu/actions/runs/900$i/job/$i\"},";; esac
  done
  printf '{"check_runs":[{"id":90,"name":"ruff","status":"completed","conclusion":"skipped"},%s]}\n' "${out%,}" | sed 's/,]/]/' > "$T/checks-$sha.json"; }
# The stub GitHub. The stg -> main pull request lives in $T/pr; its head is $T/prhead when a case names one, else the stg tip.
# A new issue takes the next number of $T/issues; its body and labels are kept, and $T/issue-<n>.state answers its state.
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
head() { cat "$T/prhead" 2>/dev/null || git -C "$R" rev-parse refs/heads/stg; }
a="\$*"
case "\$a" in
  *graphql*"issue(number:"*) n=\${a##*issue(number: }; n=\${n%%)*}; printf '{"number":%s,"state":"%s"}\n' "\$n" "\$(cat "$T/issue-\$n.state" 2>/dev/null || echo open)";;
  *graphql*) if [ -e "$T/pr" ]; then printf '{"data":{"repository":{"pullRequests":{"nodes":[{"number":%s,"headRefName":"stg","headRefOid":"%s","baseRefName":"main","isDraft":false,"labels":{"nodes":[]}}],"pageInfo":{"hasNextPage":false}}}}}\n' "\$(cat "$T/pr")" "\$(head)"; else echo '{"data":{"repository":{"pullRequests":{"nodes":[],"pageInfo":{"hasNextPage":false}}}}}'; fi;;
  *"-X POST repos/x/emu/pulls"*) echo 31 > "$T/pr"; printf '%s\n' "\$a" > "$T/pr.opened"; printf '{"number":31,"head":{"sha":"%s"}}\n' "\$(head)";;
  *"-X PUT repos/x/emu/pulls/"*) echo "MERGE" >> "$T/merges"; echo '{"merged":true}';;
  *"-X DELETE repos/x/emu/issues/"*"/labels/"*) if [ -e "$T/label-absent" ]; then echo '{"message":"Label does not exist","status":"404"}'; exit 1; else echo '[]'; fi;;
  *"-X POST repos/x/emu/issues/"*"/labels"*) if [ -e "$T/label-refuse" ]; then echo '{"message":"Validation Failed","status":"422"}'; exit 1; else echo '[{"name":"promote"}]'; fi;;
  *"-X POST repos/x/emu/issues/"*"/comments"*) f=\${a##*body=@}; f=\${f%% *}; cat "\$f" >> "$T/comments"; echo '{"id":1}';;
  *"-X POST repos/x/emu/issues"*) n=\$(( \$(cat "$T/issues" 2>/dev/null || echo 40) + 1 )); echo "\$n" > "$T/issues"
    f=\${a##*body=@}; f=\${f%% *}; cat "\$f" > "$T/issue-\$n.body"; printf '%s\n' "\$a" > "$T/issue-\$n.args"; printf '{"number":%s}\n' "\$n";;
  *"/actions/runs/"*"/jobs"*)
    if [ -e "$T/jobs-quiet" ]; then echo '{"total_count":1,"jobs":[{"status":"completed","conclusion":"failure","steps":[]}]}'
    else echo '{"total_count":1,"jobs":[{"status":"completed","conclusion":"failure","steps":[{"name":"run"}]}]}'; fi;;
  *"/check-runs"*) s=\${a##*commits/}; s=\${s%%/*}; cat "$T/checks-\$s.json" 2>/dev/null || echo '{"check_runs":[]}';;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board"; chmod +x "$T/board"; export SG_BOARD_CMD=$T/board
pass() { bash "$d/promote-main-pass.sh" > "$T/out" 2>&1; }
ghn() { local c; c=$(/usr/bin/grep -c -- "$1" "$T/gh.log" 2>/dev/null); echo "${c:-0}"; }   # <pattern>: the gh calls that match
alerts() { local c; c=$(/usr/bin/grep -c "PROMOTE MAIN" "$ST/alerts.log" 2>/dev/null); echo "${c:-0}"; }
req() { cut -f1 "$ST/promote-main-requested" 2>/dev/null; }
request() { printf '%s\t%s\n' "$1" "$(( $(date +%s) - ${2:-600} ))" > "$ST/promote-main-requested"; }   # <sha> [age]: a request that old
old_hold() { echo "$(( $(date +%s) - 4000 ))" > "$ST/promote-main-hold"; }   # the hold is past the quiet window

# 1) Quiet: the stg tip is older than the quiet window, so the pass opens the pull request, sends the label and records the head.
adv stg quiet 7200; s1=$(tip stg); m0=$(tip main); : > "$T/gh.log"; pass; rc=$?
[ $rc = 0 ] && [ "$(req)" = "$s1" ] && ok "1 a quiet stg tip is requested, and the record is the pull request's head sha" || ko "1 quiet: rc $rc, record '$(req)', out '$(cat "$T/out")'"
/usr/bin/grep -q "head=stg" "$T/pr.opened" 2>/dev/null && /usr/bin/grep -q "base=main" "$T/pr.opened" && ok "1 the pull request is stg -> main" || ko "1 pull request: '$(cat "$T/pr.opened" 2>/dev/null)'"
/usr/bin/grep -q "never merged by hand" "$T/pr.opened" && ok "1 the pull request body says it is never merged by hand" || ko "1 body: '$(cat "$T/pr.opened" 2>/dev/null)'"
[ "$(ghn 'X POST repos/x/emu/issues/31/labels .*labels\[\]=promote')" = 1 ] && ok "1 the request is the promote label" || ko "1 no label: $(cat "$T/gh.log")"
[ "$(tip main)" = "$m0" ] && [ ! -e "$T/merges" ] && ok "1 a request moves nothing and merges nothing" || ko "1 the request moved main or merged"
# 16) A second request on the same pull request removes the label first, so the label event fires again.
rm -f "$ST/promote-main-requested"; : > "$T/gh.log"; pass
[ "$(ghn 'X POST repos/x/emu/pulls')" = 0 ] && ok "16 an open stg -> main pull request is reused" || ko "16 a second pull request was opened"
dl=$(/usr/bin/grep -n -- "-X DELETE repos/x/emu/issues/31/labels/promote" "$T/gh.log" | cut -d: -f1 | head -1)
pl=$(/usr/bin/grep -n -- "-X POST repos/x/emu/issues/31/labels" "$T/gh.log" | cut -d: -f1 | head -1)
[ -n "$dl" ] && [ -n "$pl" ] && [ "$dl" -lt "$pl" ] && [ "$(req)" = "$s1" ] && ok "16 the label is removed, then sent, then the request is recorded" || ko "16 order: delete '$dl', post '$pl', record '$(req)'"
# A label GitHub refuses writes no record; a label that was not there (404) is fine.
rm -f "$ST/promote-main-requested"; : > "$T/label-refuse"; pass; rc=$?
[ -z "$(req)" ] && [ $rc != 0 ] && ok "16 a refused label writes no request" || ko "16 refused label: rc $rc, record '$(req)'"
rm -f "$T/label-refuse"; : > "$T/label-absent"; pass
[ "$(req)" = "$s1" ] && ok "16 a label that was not on the pull request is fine" || ko "16 absent label: record '$(req)', out '$(cat "$T/out")'"
rm -f "$T/label-absent"

# 5) In flight: a run that has not completed, or no run yet inside the grace, holds nothing and changes nothing.
request "$s1" 600; checks "$s1" in_progress success; : > "$T/gh.log"; pass; rc=$?
[ $rc = 0 ] && [ "$(req)" = "$s1" ] && [ "$(tip main)" = "$m0" ] && [ "$(alerts)" = 0 ] && ok "5 a tier in flight changes nothing" || ko "5 in flight: rc $rc, record '$(req)', alerts $(alerts)"
[ "$(ghn 'labels')" = 0 ] && ok "5 a tier in flight is not requested again" || ko "5 in flight asked again: $(cat "$T/gh.log")"
request "$s1" 10; rm -f "$T/checks-$s1.json"; pass
[ "$(req)" = "$s1" ] && [ "$(alerts)" = 0 ] && ok "5 a request with no run yet is in flight inside the grace" || ko "5 grace: record '$(req)', alerts $(alerts)"
# 22) No run after the grace: the label started nothing. The tip is unproven, never red.
request "$s1" 600; pass
[ -z "$(req)" ] && [ "$(alerts)" = 1 ] && [ ! -e "$T/issues" ] && /usr/bin/grep -q "started no run" "$ST/alerts.log" && ok "22 no run after the grace is unproven, with one alert and no bug" || ko "22 never started: record '$(req)', alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"

# 6) Green: main moves to R by a fast-forward, while stg is already ahead of R.
realign; adv stg green-a 7200; ra=$(tip stg); request "$ra" 600; checks "$ra" success success; adv stg green-b 10; sb=$(tip stg); pass; rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$ra" ] && [ "$sb" != "$ra" ] && ok "6 a green complete tier moves main to the requested sha, not to the stg tip" || ko "6 green: rc $rc, main $(tip main), R $ra, out '$(cat "$T/out")'"
[ -z "$(req)" ] && [ ! -e "$T/merges" ] && ok "6 the request is cleared, and the pull request is never merged" || ko "6 record '$(req)' or a merge"
[ "$(git -C "$R" rev-list --merges "$m0..refs/heads/main" | wc -l | tr -d ' ')" = 0 ] && ok "6 main gets no merge commit" || ko "6 main holds a merge commit"

# 7) Red: main stays, one bug is filed with bug and ready-for-agent, and the hold and the red sha are recorded.
realign; adv stg red-a 7200; r1=$(tip stg); m1=$(tip main); request "$r1" 600; checks "$r1" failure success; pass; rc=$?
[ $rc != 0 ] && [ "$(tip main)" = "$m1" ] && ok "7 a red complete tier holds main" || ko "7 red: rc $rc, main $(tip main)"
[ "$(cat "$T/issues" 2>/dev/null)" = 41 ] && [ "$(cat "$ST/promote-main-bug" 2>/dev/null)" = 41 ] && ok "7 the red files one bug and records its number" || ko "7 bug: issues '$(cat "$T/issues" 2>/dev/null)', record '$(cat "$ST/promote-main-bug" 2>/dev/null)', out '$(cat "$T/out")'"
/usr/bin/grep -q "labels\[\]=bug" "$T/issue-41.args" && /usr/bin/grep -q "labels\[\]=ready-for-agent" "$T/issue-41.args" && ! /usr/bin/grep -q "loop-bug" "$T/issue-41.args" \
  && ok "7 the bug carries bug and ready-for-agent, never loop-bug" || ko "7 labels: '$(cat "$T/issue-41.args" 2>/dev/null)'"
/usr/bin/grep -q "a new stg tip is needed" "$T/issue-41.body" && /usr/bin/grep -q "$r1" "$T/issue-41.body" && /usr/bin/grep -q "full-xdist" "$T/issue-41.body" \
  && ok "7 the bug names the sha and the red job, and says a new tip is needed" || ko "7 body: '$(cat "$T/issue-41.body" 2>/dev/null)'"
/usr/bin/grep -q -- "--add x 1 Todo 41" "$T/board.log" 2>/dev/null && ok "7 the bug's card is added through the board door" || ko "7 board: '$(cat "$T/board.log" 2>/dev/null)'"
[ -s "$ST/promote-main-hold" ] && [ "$(cat "$ST/promote-main-red")" = "$r1" ] && [ -z "$(req)" ] && ok "7 the hold time and the red sha are recorded, and the request is cleared" || ko "7 records: hold '$(cat "$ST/promote-main-hold" 2>/dev/null)', red '$(cat "$ST/promote-main-red" 2>/dev/null)', request '$(req)'"
# 8) A red tip is not requested twice, however old the hold is.
old_hold; : > "$T/gh.log"; pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "8 the tip of the last red verdict is not requested again" || ko "8 red tip asked again: $(cat "$T/gh.log")"
# The last red sha is compared with the head the pull request answers: a stg tip that moved under the read is still that head.
adv stg red-moved 7200; echo "$r1" > "$T/prhead"; : > "$T/gh.log"; pass
[ -z "$(req)" ] && [ "$(ghn 'issues/[0-9]*/labels')" = 0 ] && ok "8 a pull request whose head is the last red sha gets no label" || ko "8 head compare: record '$(req)', gh '$(cat "$T/gh.log")'"
rm -f "$T/prhead"
# 9) A hold that names no sha (an unproven or refused-move verdict) re-arms its own wait, on a new tip, past the quiet
# window. A red's hold names its sha, so it is not this hold (#1889, test 26).
date +%s > "$ST/promote-main-hold"; : > "$T/gh.log"; pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "9 inside the quiet window after a verdict the next tip is not requested" || ko "9 re-armed at once: $(cat "$T/gh.log")"
old_hold; pass; r2=$(tip stg)
[ "$(req)" = "$r2" ] && ok "9 past the quiet window the new tip is requested" || ko "9 no request after the window: '$(req)', out '$(cat "$T/out")'"
# 10) The next red comments on the open bug and files no second one.
request "$r2" 600; checks "$r2" success failure; pass
[ "$(cat "$T/issues")" = 41 ] && /usr/bin/grep -q "$r2" "$T/comments" 2>/dev/null && ok "10 a later red comments on the open bug" || ko "10 comment: issues $(cat "$T/issues"), comments '$(cat "$T/comments" 2>/dev/null)'"
[ "$(cat "$ST/promote-main-red")" = "$r2" ] && [ "$(tip main)" = "$m1" ] && ok "10 the red sha follows the last red, and main stays" || ko "10 red record '$(cat "$ST/promote-main-red")'"
# 11) A recorded bug that is closed gives a new bug.
adv stg red-c 7200; r3=$(tip stg); request "$r3" 600; checks "$r3" failure failure; echo closed > "$T/issue-41.state"; pass
[ "$(cat "$T/issues")" = 42 ] && [ "$(cat "$ST/promote-main-bug")" = 42 ] && ok "11 a closed recorded bug gives a new bug" || ko "11 closed bug: issues $(cat "$T/issues"), record $(cat "$ST/promote-main-bug")"
# A failed check whose jobs never ran a step measured nothing (#1629): unproven, never a bug.
adv stg quiet-fail 7200; r4=$(tip stg); request "$r4" 600; checks "$r4" failure success; : > "$T/jobs-quiet"; rm -f "$ST/alerts.log"; pass
[ "$(cat "$T/issues")" = 42 ] && [ "$(alerts)" = 1 ] && [ -z "$(req)" ] && ok "11 a failed check that never ran a step is unproven, and files no bug" || ko "11 quiet fail: issues $(cat "$T/issues"), alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"
rm -f "$T/jobs-quiet"
# A green move after a red clears the bug record, the hold and the red sha.
adv stg healed 7200; r5=$(tip stg); request "$r5" 600; checks "$r5" success success; pass
[ "$(tip main)" = "$r5" ] && [ ! -e "$ST/promote-main-bug" ] && [ ! -e "$ST/promote-main-hold" ] && [ ! -e "$ST/promote-main-red" ] && ok "11 a move clears the bug record, the hold and the red sha" || ko "11 after the move: main $(tip main), $(/bin/ls "$ST" | tr '\n' ' ')"

# 12) Unproven: every conclusion of the one unproven set clears the request, files nothing, writes one alert per tip and
# records the hold. The set is the seam the workflow's guard reads too (promote-pass.sh --ci-unproven-conclusions).
set_=$(bash "$d/promote-pass.sh" --ci-unproven-conclusions | tr '\n' ' ')
[ "$set_" = "cancelled timed_out " ] && ok "12 the unproven set is cancelled and timed_out" || ko "12 unproven set: '$set_'"
for c in $set_; do
  realign; rm -f "$T/issues"; adv stg "unproven-$c" 7200; u=$(tip stg); mu=$(tip main); request "$u" 600; checks "$u" success "$c"; pass
  [ -z "$(req)" ] && [ "$(tip main)" = "$mu" ] && [ ! -e "$T/issues" ] && [ "$(alerts)" = 1 ] && [ -s "$ST/promote-main-hold" ] \
    && ok "12 a $c run is unproven: main stays, no bug, one alert, the hold is recorded" || ko "12 $c: record '$(req)', alerts $(alerts), issues '$(cat "$T/issues" 2>/dev/null)', out '$(cat "$T/out")'"
  /usr/bin/grep -q "gui-e2e ($c)" "$ST/alerts.log" && ok "12 the alert names the job and its $c conclusion" || ko "12 $c alert: '$(cat "$ST/alerts.log" 2>/dev/null)'"
done
# It re-arms only after the quiet window, and then the same tip is asked again: an unproven tip can be proven.
: > "$T/gh.log"; pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "12 inside the quiet window an unproven tip is not requested" || ko "12 unproven re-armed at once"
old_hold; pass
[ "$(req)" = "$u" ] && ok "12 past the quiet window the unproven tip is requested again" || ko "12 unproven tip not asked again: '$(req)', out '$(cat "$T/out")'"
request "$u" 600; pass
[ "$(alerts)" = 1 ] && ok "12 a tip writes its unproven alert once" || ko "12 alerts for one tip: $(alerts)"

# 15) An in-flight request past its bound is unproven, and the alert names the cause: no runner finished the job.
realign; adv stg stuck 7200; q=$(tip stg); request "$q" 3700; checks "$q" queued queued; pass
[ -z "$(req)" ] && [ "$(alerts)" = 1 ] && /usr/bin/grep -q "queued" "$ST/alerts.log" && /usr/bin/grep -q "no runner" "$ST/alerts.log" \
  && ok "15 a request in flight past its bound is unproven, and the alert names the missing runner" || ko "15 past the bound: record '$(req)', alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"

# 2) Neither rule: a fresh tip with a young chain is not requested.
realign; adv stg young-a 3000; adv stg young-b 10; : > "$T/gh.log"; pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "2 a fresh tip on a young chain is not requested" || ko "2 fired early: $(cat "$T/gh.log")"
# 3) Ceiling: the oldest first-parent commit main lacks is past the ceiling, so a stg that keeps moving is still requested.
realign; adv stg ceil-a 18000; adv stg ceil-b 10; pass
[ "$(req)" = "$(tip stg)" ] && ok "3 the ceiling fires on a stg that keeps moving" || ko "3 ceiling: record '$(req)', out '$(cat "$T/out")'"
# 4) A work commit older than the ceiling is not on the first-parent chain, so it fires nothing by itself.
realign; merged promo 10 20000; : > "$T/gh.log"; pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "4 an old work commit behind a fresh merge fires nothing" || ko "4 an old work commit fired: $(cat "$T/gh.log")"

# 14) main is not an ancestor of stg: nothing is requested, and one alert names it per main sha.
realign; adv stg div-stg 7200; adv main div-main 7200; : > "$T/gh.log"; pass; pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && [ "$(alerts)" = 1 ] && /usr/bin/grep -q "not an ancestor" "$ST/alerts.log" \
  && ok "14 a main that is not an ancestor of stg requests nothing, with one alert" || ko "14 diverged: record '$(req)', alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"
# 23) A green tier whose move is refused: one alert, the request is cleared, main stays.
dm=$(tip main); request "$(tip stg)" 600; checks "$(tip stg)" success success; rm -f "$ST/alerts.log" "$ST"/auto/promote-main-*; pass; rc=$?
[ $rc != 0 ] && [ "$(tip main)" = "$dm" ] && [ -z "$(req)" ] && [ "$(alerts)" = 1 ] && /usr/bin/grep -q "did not move" "$ST/alerts.log" \
  && ok "23 a refused move writes one alert and clears the request" || ko "23 refused move: rc $rc, alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"
git -C "$R" update-ref refs/heads/stg "$(tip main)"   # fixture: stg takes main again

# 17) A workflow that names no complete tier job: nothing is requested, nothing is green, one alert.
realign; adv stg noname 7200; nn=$(tip stg); mn=$(tip main); : > "$T/gh.log"
SG_CI_WORKFLOW=$T/ci-fast-only.yml pass
[ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && [ "$(alerts)" = 1 ] && ok "17 an empty complete tier requests nothing, with one alert" || ko "17 empty names: record '$(req)', gh '$(cat "$T/gh.log")', alerts $(alerts)"
request "$nn" 600; SG_CI_WORKFLOW=$T/ci-fast-only.yml pass
[ "$(tip main)" = "$mn" ] && [ "$(req)" = "$nn" ] && ok "17 an empty complete tier is pending, never green" || ko "17 empty names moved main or dropped the request"

# 19) main already holds the stg tip: nothing is read, and a request left behind is cleared.
realign; request "$(tip stg)" 600; : > "$T/gh.log"; pass; rc=$?
[ $rc = 0 ] && [ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "19 a main that holds the stg tip clears the request and reads nothing" || ko "19 record '$(req)', gh '$(cat "$T/gh.log")'"

# 20) A pass killed after it recorded the red sha, and before it cleared the request, answers that red once: the next pass
# reads the recorded sha, clears the request, and writes no second comment on the bug.
realign; adv stg killed-red 7200; kr=$(tip stg); request "$kr" 600; checks "$kr" failure success
printf '77\n' > "$ST/promote-main-bug"; printf 'open\n' > "$T/issue-77.state"; printf '%s\n' "$kr" > "$ST/promote-main-red"; : > "$T/comments"
pass; rc=$?
[ $rc = 0 ] && [ -z "$(req)" ] && [ ! -s "$T/comments" ] && [ "$(cat "$T/issues" 2>/dev/null || echo none)" = none ] \
  && ok "20 a red sha already recorded is not commented on again, and the request is cleared" \
  || ko "20 killed red: rc $rc, request '$(req)', comments '$(cat "$T/comments" 2>/dev/null)'"

# 21) A check run of an older label event on the same sha never answers the current request: the pass reads the runs that
# started after its own request, so a stale red is no evidence, and the tip is unproven instead of filed as a bug. A run
# that started after the request does answer on that same sha.
realign; adv stg stale-run 7200; sr=$(tip stg); mk=$(tip main); n0=$(cat "$T/issues" 2>/dev/null || echo none)
CHK_AGE=1200 checks "$sr" failure success; request "$sr" 600; pass; rc=$?
[ -z "$(req)" ] && [ "$(tip main)" = "$mk" ] && [ "$(cat "$T/issues" 2>/dev/null || echo none)" = "$n0" ] && [ "$(alerts)" = 1 ] \
  && /usr/bin/grep -q "started no run" "$ST/alerts.log" && ok "21 a run of an older label event is no evidence of the current request" \
  || ko "21 stale run: request '$(req)', issues '$(cat "$T/issues" 2>/dev/null || echo none)', alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"
request "$sr" 600; checks "$sr" failure success; pass
[ "$(cat "$ST/promote-main-red" 2>/dev/null)" = "$sr" ] && ok "21 a run that started after the request answers on that same sha" \
  || ko "21 fresh run: red record '$(cat "$ST/promote-main-red" 2>/dev/null)', out '$(cat "$T/out")'"

# 24) The request record holds the head sha GitHub reports for the pull request, never the sha the pass read before it
# opened one: GitHub answers a head that differs from the stg tip the pass read, and the record holds GitHub's answer.
realign; adv stg gh-head 7200; gh_s=$(tip stg); other=$(tip main); [ "$other" != "$gh_s" ] || other=$(git -C "$R" rev-parse refs/heads/dev)
echo "$other" > "$T/prhead"; : > "$T/gh.log"; pass; rc=$?
[ $rc = 0 ] && [ "$(req)" = "$other" ] && ok "24 the record is the head sha GitHub answered, not the sha read before the open" \
  || ko "24 head: rc $rc, record '$(req)', GitHub '$other', stg tip '$gh_s'"
rm -f "$T/prhead"

# 18) SG_PROMOTE=off does nothing: no fetch, no gh call, no line.
adv stg off 7200; rm -rf "$ST/promote-main-tree" "$ST/promote.log"; : > "$T/gh.log"
SG_PROMOTE=off pass; rc=$?
[ $rc = 0 ] && [ ! -s "$T/gh.log" ] && [ ! -e "$ST/promote-main-tree" ] && [ ! -e "$ST/promote.log" ] && [ ! -s "$T/out" ] && ok "18 SG_PROMOTE=off does nothing" || ko "18 off: rc $rc, gh '$(cat "$T/gh.log")', out '$(cat "$T/out")'"
env -u SG_STATE bash "$d/promote-main-pass.sh" >/dev/null 2>&1; rc=$?
[ $rc = 2 ] && ok "18 with SG_STATE unset the pass refuses (exit 2)" || ko "18 SG_STATE unset: rc $rc"

# 25) The quiet window default: with SG_PROMOTE_MAIN_QUIET_S unset, a tip quiet for 700 s sends the label, and one quiet
# for 200 s does not.
passq() { env -u SG_PROMOTE_MAIN_QUIET_S bash "$d/promote-main-pass.sh" > "$T/out" 2>&1; }
realign; adv stg short-quiet 700; s25=$(tip stg); : > "$T/gh.log"; passq; rc=$?
[ $rc = 0 ] && [ "$(req)" = "$s25" ] && /usr/bin/grep -q "X POST repos/x/emu/issues/31/labels .*labels\[\]=promote" "$T/gh.log" \
  && ok "25 a tip quiet 700 s sends the label under the 300 s default window" || ko "25 700 s: rc $rc, record '$(req)', gh '$(cat "$T/gh.log")', out '$(cat "$T/out")'"
realign; adv stg still-moving 200; : > "$T/gh.log"; passq; rc=$?
[ $rc = 0 ] && [ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "25 a tip quiet 200 s is not requested under the 300 s default window" || ko "25 200 s: rc $rc, record '$(req)', gh '$(cat "$T/gh.log")', out '$(cat "$T/out")'"
# 26) A red hold names the sha it measured, never the branch (#1889). A red on A holds main; a new stg tip B is not held
# by a verdict about content no job ran, so the quiet window alone gates the request on B. While stg still holds A, the
# pass waits.
realign; adv stg red-hold-a 7200; a26=$(tip stg); request "$a26" 600; checks "$a26" failure success; passq
[ -s "$ST/promote-main-hold" ] && [ "$(cat "$ST/promote-main-red")" = "$a26" ] \
  && ok "26 the red records the hold and the red sha" || ko "26 red: hold '$(cat "$ST/promote-main-hold" 2>/dev/null)', red '$(cat "$ST/promote-main-red" 2>/dev/null)', out '$(cat "$T/out")'"
# (b) stg still holds the red sha: the pass waits, and asks for no label.
: > "$T/gh.log"; passq; rc=$?
[ $rc = 0 ] && [ -z "$(req)" ] && [ ! -s "$T/gh.log" ] && ok "26 while stg holds the red sha the pass waits" || ko "26 same tip: record '$(req)', gh '$(cat "$T/gh.log")', out '$(cat "$T/out")'"
# (a) stg moves to a new sha inside the re-arm: the complete tier is requested on it, the quiet window alone gating it.
adv stg red-hold-b 700; b26=$(tip stg); : > "$T/gh.log"; passq; rc=$?
[ $rc = 0 ] && [ "$(req)" = "$b26" ] && ok "26 a new tip after a red is requested without a re-arm about the red sha" || ko "26 new tip: record '$(req)', red '$a26', stg '$b26', out '$(cat "$T/out")'"

# No loop script but the two promotion scripts names the main push, and the pass itself pushes nothing.
/usr/bin/grep -nE '^[^#]*\bpush\b' "$d/promote-main-pass.sh" >/dev/null && ko "the pass pushes by itself" || ok "the pass pushes nothing: promote-main.sh moves main"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
