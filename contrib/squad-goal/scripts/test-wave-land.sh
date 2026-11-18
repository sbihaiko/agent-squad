#!/bin/bash
# The wave's landing pass (#375, ADR-0145 rules 2 and 14): the rebase on the moved base, the one push, the close,
# and the retry paths a refused push, a refused close and a conflicted landing take.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# The wave J lands is the one the entry cases built in the one suite: issues 7, 8, 10, 11, 14 and 24 entered it there.
# The landing suite builds that same wave for itself, so J's cases run over a wave with children to land.
start >/dev/null
for n in 7 8 10 11 14 24; do child "$n" "g$n" && en "$n" >/dev/null; done

# J: the wave lands in dev with one push and one CI run, closes what it carries, and pushes no child branch (#375).
WAVE_ISSUES='7 8 10 11 14 24 30 31'                 # every child the wave carries: a gate failure and a conflict never entered
: > "$T/gh.log"
child 30 g30; en 30 >/dev/null; child 31 g31; r=$(en 31)
[ "$(entered 30)" = yes ] && [ "$(entered 31)" = yes ] && ok "J: the wave of two children holds both" || ko "J: a child of the wave did not enter: '$r'"
/usr/bin/grep -q 'refs/heads/gui/' "$T/pushes.log" && ko "J: a child branch reached the remote: $(/usr/bin/grep 'refs/heads/gui/' "$T/pushes.log")" || ok "J: an entry pushes the wave ref, never a child branch"
# The base moves under the open wave: it is rebased onto before the one push, and only then (ADR-0145 rule 14).
en30head=$($G -C "$T/c30" rev-parse HEAD); en31head=$($G -C "$T/c31" rev-parse HEAD); wtip=$($O rev-parse $W)
devmoved; moved=$($O rev-parse dev)
cp "$T/pushes.log" "$T/pushes.before"; : > "$T/pushes.log"   # from here, the only push left is the landing's own
[ "$($O rev-parse $W)" = "$en31head" ] && [ "$($O rev-parse $W)" = "$wtip" ] && ok "J: the wave tip is the last entrant's head, and the moved dev did not move it" || ko "J: the wave tip is not the last entrant's head"
$O merge-base --is-ancestor "$moved" $W && ko "J: the wave carries the moved dev before the landing" || ok "J: mid-wave the wave does not carry the moved dev (it is not rebased on dev yet)"
$O merge-base --is-ancestor "$en30head" $W && ok "J: the entry of a child is what moves the wave tip" || ko "J: #30's entry is not in the wave"
# (#786) A red base holds the wave's landing: the base is read before the tip's check, so the pass names the base, pushes nothing to
# dev and says it once on alerts.log. The red answer is cleared after the case, so the landing below reads the base afresh.
r=$(cd "$T/co" && RED_BASE=1 bash "$T/s/wave.sh" land run-1 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W: base dev is red at "*) ok "J: a red base holds the wave: the pass names the base and waits";; *) ko "J: a red base did not hold the wave: '$r' (exit $rc)";; esac
[ "$rc" = 1 ] && ok "J: a red base is a wait (exit 1), not a refusal" || ko "J: a red base exited $rc"
[ "$(pushes refs/heads/dev | wc -l | tr -d ' ')" = 0 ] && ok "J: a red base: the wave pushes nothing to dev" || ko "J: a red base pushed to dev: $(pushes refs/heads/dev)"
[ "$(/usr/bin/grep -c 'is red at' "$T/st/alerts.log" 2>/dev/null)" = 1 ] && ok "J: a red base raises one alerts.log line" || ko "J: alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
rm -f "$T/st/auto/basecheck-"*
r=$(land run-1); rc=$?
case "$r" in "WAVE $W "*) ok "J: land names the wave branch and its sha";; *) ko "J: land said nothing: '$r' (exit $rc)";; esac
[ $rc = 0 ] && ok "J: the wave landed (exit 0)" || ko "J: exit $rc: '$r'"
# (#790) The base is read again right after the push: the head the wave landed at has its own answer (unreadable here, the stub has no checks).
[ -e "$T/st/auto/basecheck-unreadable-$($O rev-parse dev)" ] && ok "J: the wave reads the base head it pushed" || ko "J: no read of the pushed head: $(ls "$T/st/auto" 2>/dev/null)"
case "$r" in *refused*) ko "J: a board move that worked is logged as refused: '$r'";; *) ok "J: a board move that worked names no refusal";; esac
# One push to dev, and it is a fast-forward of the dev that moved: the tip lands whole, in one update.
[ "$(pushes refs/heads/dev | wc -l | tr -d ' ')" = 1 ] && ok "J: the wave lands in dev with one push" || ko "J: $(pushes refs/heads/dev | wc -l | tr -d ' ') pushes to dev: $(pushes refs/heads/dev)"
[ "$(pushes refs/heads/dev | cut -d' ' -f1)" = "$moved" ] && ok "J: the one push starts from the dev that moved" || ko "J: the push started from '$(pushes refs/heads/dev | cut -d' ' -f1)', not the moved dev '$moved'"
$O merge-base --is-ancestor "$moved" dev && ok "J: the moved dev is rebased onto before the push" || ko "J: the moved dev is not in the landed dev"
[ "$($O rev-parse dev)" = "$(pushes refs/heads/dev | cut -d' ' -f2)" ] && ok "J: the push is the one that landed (dev is at the pushed sha)" || ko "J: dev is not at the pushed sha"
$O rev-list --merges dev | /usr/bin/grep -q . && ko "J: a merge commit landed" || ok "J: the landing keeps the history linear"
# The rebase on the moved base rewrites the shas, so what proves a child is in dev is its work, not its sha.
$O cat-file -e dev:g30 2>/dev/null && $O cat-file -e dev:g31 2>/dev/null && ok "J: the work of both children of the wave is in dev" || ko "J: a child of the wave is not in dev"
# No child branch is on the remote, now that the whole cycle has run; the wave branch itself goes with the landing.
/usr/bin/grep -q 'refs/heads/gui/' "$T/pushes.log" "$T/pushes.before" && ko "J: a child branch reached the remote" || ok "J: no child branch is pushed to the remote, ever"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ko "J: the landed wave branch survives" || ok "J: the wave branch is deleted once its work is in dev"
[ "$(pushes | awk -v w="refs/heads/$W" '$3!="refs/heads/dev" && $3!=w')" = "" ] && ok "J: the landing writes dev and the wave ref, nothing else" || ko "J: the landing wrote $(pushes)"
# Each issue of the wave is closed through the API, and its one delivery comment is queued, never posted.
for n in $WAVE_ISSUES; do /usr/bin/grep -q "issues/$n -f state=closed" "$T/gh.log" || ko "J: #$n is not closed through the API"; done
for n in $WAVE_ISSUES; do case "$(wfact $n run-1)" in "delivered "*) ;; *) ko "J: #$n has no delivered fact in its ledger: '$(wfact $n run-1)'";; esac; done
# The landing is also the issue's `landed` fact (ADR-0149, #695): rule R reads it, never the run's land heads.
landedfacts() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" landed x/y 2>/dev/null; }
[ "$(landedfacts | cut -f2 | sort -n | tr '\n' ' ')" = "$(printf '%s\n' $WAVE_ISSUES | sort -n | tr '\n' ' ')" ] && ok "J: each issue the wave landed has its landed fact" || ko "J: landed facts: '$(landedfacts)'"
landedfacts | awk -F'\t' '$1!="run-1" {bad=1} END {exit bad}' && ok "J: the landed fact is keyed by the run that landed it" || ko "J: landed facts: '$(landedfacts)'"
[ "$(/usr/bin/grep -c 'state=closed' "$T/gh.log")" = 8 ] && ok "J: each issue of the wave is closed through the API" || ko "J: $(/usr/bin/grep -c 'state=closed' "$T/gh.log") closes for 8 issues"
for n in $WAVE_ISSUES; do ls "$T/st/cq/"x__y#$n-delivery~* >/dev/null 2>&1 || ko "J: no delivery comment is queued for #$n"; done
[ "$(ls "$T/st/cq/"*-delivery~* 2>/dev/null | wc -l | tr -d ' ')" = 8 ] && ok "J: each issue of the wave has its one delivery comment queued" || ko "J: $(ls "$T/st/cq" 2>/dev/null | tr '\n' ' ')"
/usr/bin/grep -qE 'issues/[0-9]+/comments' "$T/gh.log" && ko "J: the pass posted a comment itself" || ok "J: the pass posts no comment itself (the flush does)"
# The shipped vocabulary has no Done column (#468): the landing writes Closed, the column the board carries.
for n in $WAVE_ISSUES; do /usr/bin/grep -qx "o 3 Closed $n" "$T/board.log" || ko "J: the card of #$n does not read Closed"; done
[ "$(/usr/bin/grep -cE '^o 3 Closed ' "$T/board.log")" -ge 7 ] && ok "J: each card of the wave reads Closed" || ko "J: $(/usr/bin/grep -cE '^o 3 Closed ' "$T/board.log") cards read Closed"
/usr/bin/grep -qE '^o 3 Done ' "$T/board.log" && ko "J: the landing still writes Done, which the shipped board does not carry" || ok "J: no Done write on the shipped vocabulary"
# A landing pass with no wave branch to land writes nothing: the second call is refused, and dev does not move.
before=$($O rev-parse dev); r=$(land run-1); rc=$?
[ $rc = 2 ] && case "$r" in *"WAVE-ERROR"*) ok "J: a wave branch that is not there is refused";; *) ko "J: the refusal is silent: '$r'";; esac || ko "J: a second land exited $rc: '$r'"
[ "$($O rev-parse dev)" = "$before" ] && ok "J: a refused landing moves nothing" || ko "J: dev moved on a refused landing"
[ ! -e "$T/st/auto/wave-run-1" ] && ok "J: the landing clears the run's wave marker" || ko "J: the wave marker outlives the landing"

# E2: a push the remote refuses repeats from the fetch (ADR-0145 rule 2). The base moves between the pass's rebase and its
# push -- a concurrent landing, made by the remote's own hook -- so the first push to dev is refused. The pass fetches the
# moved base, rebases on it, and pushes again: the wave lands, and dev carries both the wave's work and the concurrent one.
# start2, en2 and land2 are the setup calls the cases from E2 on run. A failed start cuts no wave branch and a failed entry
# leaves the child out of the wave; either reads as a case that proves nothing if it is swallowed, so both are named with the
# line wave.sh printed. land2's own output and exit code are what each case reads, so it is left as it is.

rm -rf "$T/race"; $G clone -q "$T/origin.git" "$T/race" 2>/dev/null
(cd "$T/race" && $G checkout -q -b raceline dev && echo race > race && $G add race && $G -c user.email=a@b -c user.name=t commit -q -m "a concurrent landing" && $G push -q origin HEAD:raceline)
RACE=$($G -C "$T/race" rev-parse HEAD)
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do printf '%s\t%s\t%s\n' "\$old" "\$new" "\$ref" >> "$T/pushes.log"; [ "\$ref" = refs/heads/dev ] && [ -e "$T/hook-hold" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook-hold"
printf '%s\n' "$RACE" > "$T/origin.git/refs/heads/dev"   # the concurrent landing, between the pass's rebase and its push
exit 1
H
chmod +x "$T/origin.git/hooks/pre-receive"
W2=wave/run-2; start2 run-2 >/dev/null; child 50 g50 "origin/$W2"; en2 run-2 50
: > "$T/hook-hold"; : > "$T/pushes.log"; : > "$T/gh.log"
r=$(land2 run-2); rc=$?
case "$r" in "WAVE $W2 "*) ok "E2: a refused push repeats from the fetch and the wave lands";; *) ko "E2: the refused push did not land: '$r' (exit $rc)";; esac
[ $rc = 0 ] && ok "E2: the landing exits 0" || ko "E2: exit $rc: '$r'"
[ "$($O cat-file -e dev:race 2>/dev/null && echo yes)" = yes ] && ok "E2: the base that moved between the rebase and the push is in dev" || ko "E2: the concurrent landing is not in dev"
[ "$($O cat-file -e dev:g50 2>/dev/null && echo yes)" = yes ] && ok "E2: the wave's work is in dev beside it" || ko "E2: the wave's work is not in dev"
[ "$($O rev-parse dev)" = "$(pushes refs/heads/dev | tail -1 | cut -d' ' -f2)" ] && ok "E2: dev is at the sha the repeat push wrote" || ko "E2: dev is not at the last pushed sha"
$O rev-parse -q --verify "refs/heads/$W2" >/dev/null && ko "E2: the landed wave branch survives" || ok "E2: the wave branch goes with its landing"
[ "$(/usr/bin/grep -c 'issues/50 -f state=closed' "$T/gh.log")" = 1 ] && ok "E2: the issue of the landed wave closes through the API" || ko "E2: $(/usr/bin/grep -c 'issues/50 -f state=closed' "$T/gh.log") closes for #50"

# E3: a close the API refuses queues no delivery comment (#375 debt): the issue stays open, and a comment claiming it
# closed would be false delivery evidence. The close repeats once from the refusal, then the pass names it.
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\nexit 1\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
W3=wave/run-3; start2 run-3 >/dev/null; child 40 g40 "origin/$W3"; en2 run-3 40 >/dev/null
: > "$T/gh.log"; : > "$T/pushes.log"
r=$(land2 run-3); rc=$?
[ "$($O cat-file -e dev:g40 2>/dev/null && echo yes)" = yes ] && ok "E3: the wave still lands when the close is refused" || ko "E3: the wave did not land: '$r' (exit $rc)"
[ "$($O rev-parse dev)" = "$(pushes refs/heads/dev | tail -1 | cut -d' ' -f2)" ] && ok "E3: and dev is at the pushed sha" || ko "E3: dev is not at the pushed sha"
[ "$(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log")" = 2 ] && ok "E3: the refused close is retried once" || ko "E3: $(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log") close attempts for #40"
ls "$T/st/cq/"x__y#40-delivery~* >/dev/null 2>&1 && ko "E3: a delivery comment is queued for an issue that did not close" || ok "E3: no delivery comment is queued for an issue that did not close"
/usr/bin/grep -qx "o 3 Closed 40" "$T/board.log" && ko "E3: the card of an issue that did not close reads Closed" || ok "E3: the card of an issue that did not close keeps its column"
[ -z "$(awk -F'\t' '$4=="40"' "$T/st/landed.tsv" 2>/dev/null)" ] && ok "E3: the ledger carries no row for an issue that did not close" || ko "E3: the ledger claims a delivery that did not happen: '$(awk -F'\t' '$4=="40"' "$T/st/landed.tsv")'"
case "$r" in *"#40"*"did not close"*) ok "E3: the close that failed is named";; *) ko "E3: the failed close is silent: '$r'";; esac
# E3b (#475): an issue the landing could not close stays owed. The landing exits non-zero, so the run is not marked seen, and
# the run's wave marker stays; a later pass tries the close again, and the delivery is recorded once the close succeeds.
[ "$rc" != 0 ] && ok "E3b: a landing that could not close an issue exits non-zero" || ko "E3b: the landing exited 0 with #40 still open"
[ -e "$T/st/auto/wave-run-3" ] && ok "E3b: the run keeps its wave marker, so a later pass lands it again" || ko "E3b: the wave marker is gone: nothing tries the close again"
case "$(wfact 40 run-3)" in "owed "*) [ ! -e "$T/st/auto/wowed-run-3" ] && ok "E3b: the owed close is a fact in the issue's ledger, with no wowed- marker" || ko "E3b: a wowed- marker is written";; *) ko "E3b: ledger fact of #40: '$(wfact 40 run-3)'";; esac
# The stub keeps the labels and the comments a case writes when LABEL_STORE=1 (#2217: the freeze reads back what it wrote),
# and it refuses a label write for LABEL_REFUSE's issue. Without the store every issue read answers the one shape the other
# cases expect, so the store never moves them.
gh_store_stub
: > "$T/gh.log"; : > "$T/board.log"; : > "$T/pushes.log"; dev0=$($O rev-parse dev)
r=$(land2 run-3); rc=$?
[ $rc = 0 ] && ok "E3b: the later pass delivers the owed issue (exit 0)" || ko "E3b: the later pass exited $rc: '$r'"
[ "$(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log")" = 1 ] && ok "E3b: the close is tried again on the later pass" || ko "E3b: $(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log") close attempts on the later pass"
[ "$(ls "$T/st/cq/"x__y#40-delivery~* 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "E3b: the delivery comment is queued once" || ko "E3b: no single delivery comment for #40"
/usr/bin/grep -qx "o 3 Closed 40" "$T/board.log" && ok "E3b: the card moves once the close succeeds" || ko "E3b: the card of #40 did not move"
[ "$(awk -F'\t' '$4=="40"' "$T/st/landed.tsv" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "E3b: the ledger carries one row for #40" || ko "E3b: the ledger row for #40 is not single"
[ "$($O rev-parse dev)" = "$dev0" ] && [ -z "$(cat "$T/pushes.log")" ] && ok "E3b: the later pass pushes nothing" || ko "E3b: the later pass pushed"
[ ! -e "$T/st/auto/wave-run-3" ] && ok "E3b: the wave marker goes once the issue is delivered" || ko "E3b: the wave marker stays after delivery"
case "$(wfact 40 run-3)" in "delivered "*) ok "E3b: the owed fact turns delivered";; *) ko "E3b: ledger fact of #40 after delivery: '$(wfact 40 run-3)'";; esac

# E4 (#869, re-anchored: each commit here names one issue, so the one-issue-per-commit rule expects the same closes): the issues a wave carries are read from the whole message of each commit and named once (#375 review). A `Refs #N`
# that sits in a commit body closes its issue like one in a subject, and an issue two commits (or one commit twice) name is
# closed one time.
W4=wave/run-4; start2 run-4 >/dev/null
rm -rf "$T/c60"; $G clone -q "$T/origin.git" "$T/c60" 2>/dev/null
(cd "$T/c60" && $G checkout -q -b gui/60 "origin/$W4" && echo 60 > g60 && $G add g60 && $G -c user.email=a@b -c user.name=t commit -q -m "work 60" -m "Refs #60"
  echo more > g60b && $G add g60b && $G -c user.email=a@b -c user.name=t commit -q -m "more work" -m "Refs #60"
  echo third > g63 && $G add g63 && $G -c user.email=a@b -c user.name=t commit -q -m "third (Refs #63)" -m "Refs #63")
en2 run-4 60 >/dev/null; : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-4); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g60 2>/dev/null && echo yes)" = yes ] && ok "E4: the wave of a body-only Refs lands" || ko "E4: the wave did not land: '$r' (exit $rc)"
[ "$(/usr/bin/grep -c 'issues/60 -f state=closed' "$T/gh.log")" = 1 ] && ok "E4: an issue named in a commit body is closed, once" || ko "E4: $(/usr/bin/grep -c 'issues/60 -f state=closed' "$T/gh.log") closes for #60"
ls "$T/st/cq/"x__y#60-delivery~* >/dev/null 2>&1 && ok "E4: the issue named in a body has its delivery comment queued" || ko "E4: no delivery comment for #60"
/usr/bin/grep -qx "o 3 Closed 60" "$T/board.log" && ok "E4: the card of the issue named in a body reads Closed" || ko "E4: no Closed card for #60: '$(cat "$T/board.log")'"
[ "$(awk -F'\t' '$4=="60"' "$T/st/landed.tsv" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "E4: the ledger carries one row for an issue two commits name" || ko "E4: the ledger rows for #60: '$(cat "$T/st/landed.tsv" 2>/dev/null)'"
[ "$(/usr/bin/grep -c 'issues/63 -f state=closed' "$T/gh.log")" = 1 ] && ok "E4: an issue a subject and a body both name is closed once" || ko "E4: $(/usr/bin/grep -c 'issues/63 -f state=closed' "$T/gh.log") closes for #63"

# E5: a commit the base already holds is dropped by the rebase, and its issue is neither closed nor logged (#375 review):
# the wave did not deliver it. The issues are read after the rebase, from what is left ahead of the base.
W5=wave/run-5; start2 run-5 >/dev/null; child 61 g61 "origin/$W5"; en2 run-5 61 >/dev/null; child 62 g62 "origin/$W5"; en2 run-5 62 >/dev/null
rm -rf "$T/dup"; $G clone -q "$T/origin.git" "$T/dup" 2>/dev/null
(cd "$T/dup" && $G checkout -q dev && $G fetch -q "$T/c61" gui/61 && GIT_COMMITTER_NAME=other $G -c user.email=a@b -c user.name=t cherry-pick FETCH_HEAD >/dev/null 2>&1 && $G push -q origin HEAD:dev)
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-5); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g62 2>/dev/null && echo yes)" = yes ] && ok "E5: the wave lands what the base did not hold" || ko "E5: the wave did not land: '$r' (exit $rc)"
/usr/bin/grep -q 'issues/61 -f state=closed' "$T/gh.log" && ko "E5: the issue of a commit the base already held is closed" || ok "E5: the issue of a commit the base already held is not closed"
[ -z "$(awk -F'\t' '$4=="61"' "$T/st/landed.tsv" 2>/dev/null)" ] && ok "E5: the ledger carries no row for it" || ko "E5: the ledger claims #61: '$(cat "$T/st/landed.tsv")'"
/usr/bin/grep -q 'issues/62 -f state=closed' "$T/gh.log" && [ -n "$(awk -F'\t' '$4=="62"' "$T/st/landed.tsv")" ] && ok "E5: the issue the wave did deliver is closed and logged" || ko "E5: #62 is not closed and logged"

# J: a wave with nothing ahead of the base landed empty (a run with no dispatch, or every child failed its gates): the landing
# is done, not failed -- the wave ref goes, the marker goes, exit 0 -- or the loop retries it every cycle for good (#375 review).
# `waved-<run>` outlives the landing: the durable record that the run cut a wave, which childcc.py reads.
W6=wave/run-6; start2 run-6 >/dev/null; before=$($O rev-parse dev); : > "$T/pushes.log"
r=$(land2 run-6); rc=$?
[ $rc = 0 ] && ok "J: a wave with nothing ahead of dev lands empty (exit 0)" || ko "J: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W6" >/dev/null && ko "J: the empty wave branch stays on the remote" || ok "J: the empty wave branch is deleted"
[ ! -e "$T/st/auto/wave-run-6" ] && ok "J: the run's wave marker is cleared" || ko "J: the wave marker outlives an empty landing"
[ -e "$T/st/auto/waved-run-6" ] && ok "J: waved-<run> stays, the durable record that the run had a wave" || ko "J: no waved-run-6 record"
[ "$($O rev-parse dev)" = "$before" ] && ok "J: an empty landing moves dev nowhere" || ko "J: dev moved on an empty landing"

# V (#667): a child's fix run holds the wave. A wave with nothing ahead of the base is not landed empty while a
# `ccfix-<run>-<child>` marker names a fix run with no verdict: that run may still put the child in the wave, and an empty
# landing deletes the wave branch, cleans the child's worktree and sends the issue back to the queue while it works.
W7=wave/run-7; start2 run-7 >/dev/null; : > "$T/pushes.log"
mkdir -p "$T/st/fix7"; : > "$T/st/fix7/record.jsonl"
printf '%s\n' "$T/st/fix7" > "$T/st/auto/ccfix-run-7-route.router.1"
r=$(land2 run-7); rc=$?
[ $rc = 1 ] && ok "V: a wave whose child has a fix run with no verdict does not land empty (exit 1)" || ko "V: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "V: the wave branch stays" || ko "V: the wave branch was deleted"
[ -e "$T/st/auto/wave-run-7" ] && ok "V: the run's wave marker stays" || ko "V: the wave marker went"
case "$r" in *"WAVE WAIT"*) ok "V: the pass names the wait";; *) ko "V: not named: '$r'";; esac
# A fix run whose folder is gone is no verdict either: the marker alone holds the wave, and the age cap is what frees it.
rm -rf "$T/st/fix7"
r=$(land2 run-7); rc=$?
[ $rc = 1 ] && ok "V: a fix run whose folder is gone still holds the wave (exit 1)" || ko "V: exit $rc: '$r'"
rm -f "$T/st/auto/ccfix-run-7-route.router.1"
r=$(land2 run-7); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "V: with the marker gone the wave lands empty";; *) ko "V: not empty: '$r'";; esac || ko "V: exit $rc: '$r'"

# N (#449): an empty wave never runs the tip's check at all. A wave with nothing ahead of the base has no child commit for a
# red check to blame, so the check could only fail the landing of a wave that carries nothing -- and four runs went
# needs-human on exactly that (`WAVE CHECK RED` in the loop's own log, at ebb70f8).
W20=wave/run-20; start2 run-20 >/dev/null
r=$(SG_WAVE_TIP_GATES=false land2 run-20); rc=$?
[ $rc = 0 ] && ok "N: an empty wave lands empty though its tip's check fails" || ko "N: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*) ko "N: the tip's check ran on an empty wave: '$r'";; *) ok "N: the tip's check never runs on an empty wave";; esac
$O rev-parse -q --verify "refs/heads/$W20" >/dev/null && ko "N: the empty wave branch stays" || ok "N: the empty wave branch goes with its landing"

# K (#844): a rebase conflict in the landing is terminal for this wave head: no retry on the next cycle. The wave's issues go back
# to the queue through the requeue path, the run's wave marker goes (so the loop stops calling the landing), and the wave branch
# stays on the remote with its commits. A conflict is not a stuck landing: it never reaches the stuck cap (SG_WAVE_LAND_TRIES).
W7=wave/run-7; start2 run-7 >/dev/null; child 70 cf "origin/$W7"; en2 run-7 70 >/dev/null
rm -rf "$T/w3"; $G clone -q "$T/origin.git" "$T/w3" 2>/dev/null
(cd "$T/w3" && $G checkout -q dev && echo other > cf && $G add cf && $G -c user.email=a@b -c user.name=t commit -q -m "conflicting work" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; : > "$T/board.log"; before=$($O rev-parse dev)
r1=$(land2 run-7); rc1=$?
case "$r1" in "WAVE CONFLICT $W7: the rebase on dev conflicts at "*"; #70 kept on fix/70-land-run-7 for one rebase round"*) ok "K: the conflict is logged once, naming the commit and the issue";; *) ko "K: no conflict line: '$r1'";; esac
[ $rc1 = 1 ] && ok "K: the conflicting landing does not land (exit 1)" || ko "K: exit $rc1"
[ -e "$T/st/auto/wave-run-7" ] && ko "K: the wave marker is kept, so the loop retries the landing" || ok "K: the wave marker goes, so the loop stops retrying"
/usr/bin/grep -qx "o 3 Todo 70" "$T/board.log" && ko "K: #70 went back to Todo, so its work is rebuilt (#1500)" || ok "K: #70 stays put: its work is kept for one rebase round (#1500)"
case "$(wfact 70 run-7)" in "refused the rebase on dev conflicts at"*) ok "K: the conflict is a fact in the issue's ledger";; *) ko "K: ledger fact of #70: '$(wfact 70 run-7)'";; esac
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "K: the wave branch keeps the work" || ko "K: the wave branch is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K: dev is not moved by a conflicting landing" || ko "K: dev moved"
/usr/bin/grep -q 'labels\[\]=frozen:owner' "$T/gh.log" && ko "K: a conflict below the cap froze the issue" || ok "K: a conflict below the cap freezes nothing"
case "$r1" in *"WAVE-ERROR"*|*"WAVE NEEDS-HUMAN"*) ko "K: the stuck path was taken: '$r1'";; *) ok "K: the stuck path is not taken";; esac

# KC (#1061, #1500): two waves of one issue, cut on one base, conflict with dev on the same path. Each keeps its own wave head for
# one rebase round: neither requeues the child and neither files a bug, because a kept branch is not a rebuild.
start2 run-k8 >/dev/null; start2 run-k10 >/dev/null
child 71 cf2 origin/dev; en2 run-k8 71 >/dev/null
child 71 cf2 origin/dev; en2 run-k10 71 >/dev/null
rm -rf "$T/w4"; $G clone -q "$T/origin.git" "$T/w4" 2>/dev/null
(cd "$T/w4" && $G checkout -q dev && echo dev > cf2 && $G add cf2 && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cf2" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*71-*
rk8=$(land2 run-k8); rk10=$(land2 run-k10)
case "$rk8$rk10" in *"WAVE REQUEUE #71"*) ko "KC: a landing conflict requeued #71: '$rk8$rk10'";; *) ok "KC: neither conflict requeues #71";; esac
[ -s "$T/st/auto/kept-fix_71-land-run-k8" ] && [ -s "$T/st/auto/kept-fix_71-land-run-k10" ] && ok "KC: each wave keeps its own landing branch" || ko "KC: no kept marker for #71"
/usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ko "KC: a bug was filed for a first round" || ok "KC: no bug for a first round on each wave"

nokeep() { local c; c=$($O -c user.email=a@b -c user.name=t commit-tree "$($O mktree </dev/null)" -m "unrelated" 2>/dev/null) && $O update-ref "refs/heads/fix/$1-land-$2" "$c"; }
# The requeue path stays for a wave that cannot be kept (#1500): the keep push is refused because fix/<n>-land-<run> holds an
# unrelated commit. KC, KR, KD and K3 run on such a wave, so the same-paths rule (#1343) and the requeue cap (#844) keep their guards.
# KB (#1061): two waves of one issue, cut on one base, conflict with dev on the same path. The first conflict re-dispatches the
# child once, with the path named in its request and a rework label on its card; the second identical conflict files one loop
# bug and does not requeue the child again.
start2 run-kb8 >/dev/null; start2 run-kb10 >/dev/null
child 76 cf2b origin/dev; en2 run-kb8 76 >/dev/null
child 76 cf2b origin/dev; en2 run-kb10 76 >/dev/null
rm -rf "$T/w4b"; $G clone -q "$T/origin.git" "$T/w4b" 2>/dev/null
(cd "$T/w4b" && $G checkout -q dev && echo dev > cf2b && $G add cf2b && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cf2b" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*76-*
nokeep 76 run-kb8; nokeep 76 run-kb10; rk8=$(land2 run-kb8); rk10=$(land2 run-kb10)
case "$rk8" in *"WAVE REQUEUE #76"*) ok "KB: the first conflict re-dispatches its child";; *) ko "KB: no requeue of #76: '$rk8'";; esac
cat "$T"/st/cq/*76-* 2>/dev/null | /usr/bin/grep -q cf2b && ok "KB: the child's next request names the conflicting path" || ko "KB: the request names no path: '$(cat "$T"/st/cq/*76-* 2>/dev/null)'"
/usr/bin/grep -q 'issues/76/labels.*labels\[\]=rework' "$T/gh.log" && ok "KB: the card of #76 carries rework" || ko "KB: no rework label for #76"
case "$rk10" in *"WAVE REQUEUE #76"*) ko "KB: the repeated conflict requeued #76 again: '$rk10'";; *) ok "KB: the repeated conflict does not requeue #76";; esac
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && ok "KB: the repeated conflict files one bug" || ko "KB: $(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log") bugs filed"

# KR (#1343): the base moves with every landing, so the conflict key is the conflicting paths, not the base sha. The same path
# conflicting again at a new base is a repeat (one loop bug, no requeue); a different path is a first again (a requeue).
start2 run-kr1 >/dev/null; start2 run-kr2 >/dev/null
child 74 cfr origin/dev; en2 run-kr1 74 >/dev/null
child 74 cfr origin/dev; en2 run-kr2 74 >/dev/null
rm -rf "$T/w5"; $G clone -q "$T/origin.git" "$T/w5" 2>/dev/null
(cd "$T/w5" && $G checkout -q dev && echo dev1 > cfr && $G add cfr && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cfr" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*74-*
nokeep 74 run-kr1; kr1=$(land2 run-kr1)
(cd "$T/w5" && echo dev2 > cfr && $G add cfr && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cfr again" && $G push -q origin HEAD:dev)
nokeep 74 run-kr2; kr2=$(land2 run-kr2)
case "$kr1" in *"WAVE REQUEUE #74"*) ok "KR: the first conflict of #74 re-dispatches it";; *) ko "KR: no requeue of #74: '$kr1'";; esac
case "$kr2" in *"WAVE REQUEUE #74"*) ko "KR: the second conflict on the same path at a new base requeued #74: '$kr2'";; *) ok "KR: the second conflict on the same path at a new base does not requeue #74";; esac
case "$kr2" in *"WAVE-BUG #74"*) ok "KR: the second conflict on the same path files one loop bug";; *) ko "KR: no loop bug for #74: '$kr2'";; esac
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && ok "KR: one bug issue is filed" || ko "KR: $(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log") bugs filed"
/usr/bin/grep 'labels\[\]=bug' "$T/gh.log" | /usr/bin/grep -q 'labels\[\]=frozen:not-startable.*labels\[\]=loop-bug' && ok "KR: the loop bug goes through the one filer, held until its birth Priority reads back (#1978)" || ko "KR: the loop bug skips the filer: $(/usr/bin/grep 'labels\[\]=bug' "$T/gh.log")"
# KR (#1343): the repeat also stops the issue, so no frozen dispatch picks it again: frozen:owner on its card and the stop marker.
/usr/bin/grep -q 'issues/74/labels.*labels\[\]=frozen:owner' "$T/gh.log" && ok "KR: the repeated conflict freezes #74 (frozen:owner)" || ko "KR: no frozen:owner on #74 after the repeat"
ls "$T"/st/cq/*74-wavestop* >/dev/null 2>&1 && ok "KR: the repeated conflict writes the stop marker of #74" || ko "KR: no stop marker for #74 after the repeat"

start2 run-kd1 >/dev/null; start2 run-kd2 >/dev/null
child 75 cfd origin/dev; en2 run-kd1 75 >/dev/null
child 75 cfe origin/dev; en2 run-kd2 75 >/dev/null
rm -rf "$T/w6"; $G clone -q "$T/origin.git" "$T/w6" 2>/dev/null
(cd "$T/w6" && $G checkout -q dev && echo dev1 > cfd && $G add cfd && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cfd" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*75-*
nokeep 75 run-kd1; kd1=$(land2 run-kd1)
(cd "$T/w6" && echo dev1 > cfe && $G add cfe && $G -c user.email=a@b -c user.name=t commit -q -m "dev adds cfe" && $G push -q origin HEAD:dev)
nokeep 75 run-kd2; kd2=$(land2 run-kd2)
case "$kd1" in *"WAVE REQUEUE #75"*) ok "KR: a conflict on one path requeues #75";; *) ko "KR: no requeue of #75 on cfd: '$kd1'";; esac
case "$kd2" in *"WAVE REQUEUE #75"*) ok "KR: a conflict on a different path at a new base is a first again";; *) ko "KR: a different path did not requeue #75: '$kd2'";; esac

# K3 (#844): the requeue cap bounds a conflict that keeps coming back: at SG_LOOP_REQUEUE_CAP the issue goes to the owner, as any
# requeue does (requeue, loop_stop). A second wave of #73 conflicts with dev moved again after its entry.
W44=wave/run-44; start2 run-44 >/dev/null; child 73 cf "origin/$W44"; en2 run-44 73 >/dev/null
(cd "$T/w3" && $G pull -q origin dev && echo other2 > cf && $G add cf && $G -c user.email=a@b -c user.name=t commit -q -m "more conflicting work" && $G push -q origin HEAD:dev)
nokeep 73 run-44; r4=$(SG_LOOP_REQUEUE_CAP=1 land2 run-44); rc4=$?
case "$r4" in *"WAVE NEEDS-HUMAN #73"*) ok "K3: at the requeue cap the conflicting issue goes to the owner";; *) ko "K3: no needs-human line: '$r4'";; esac
[ $rc4 = 1 ] && ok "K3: the capped conflict does not land (exit 1)" || ko "K3: exit $rc4"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
