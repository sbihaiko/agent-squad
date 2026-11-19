#!/bin/bash
# The entry and the tip under an unproven outcome (#682, #694, spec 1163, spec 1797, #2149): an obligation admits a
# child, and an unproven wave tip never lands. A card stands in Testing while its wave's tip gate waits or runs (#2295).
# The last cases read the run's dispatch set (#775, #776, #1340).
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# X (#682): a push to the wave that the remote refuses repeats once from the fetch and the rebase on the moved tip, and the
# gates run again on that rebased tree before the second push: what enters is what the gates passed. A sibling enters
# between the gates and the push (the remote's hook moves the wave tip and refuses the push once).
W40=wave/run-40; start2 run-40 >/dev/null
rm -rf "$T/sib"; $G clone -q "$T/origin.git" "$T/sib" 2>/dev/null
(cd "$T/sib" && $G checkout -q -b sib "origin/$W40" && echo s > s40 && $G add s40 && $G -c user.email=a@b -c user.name=t commit -q -m "a sibling (Refs #499)" && $G push -q origin HEAD:refs/heads/sib40)
SIB=$($G -C "$T/sib" rev-parse HEAD)
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do [ "\$ref" = refs/heads/$W40 ] && [ -e "$T/hook40" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook40"; mkdir -p "$T/origin.git/refs/heads/wave"; printf '%s\n' "$SIB" > "$T/origin.git/refs/heads/$W40"; exit 1
H
chmod +x "$T/origin.git/hooks/pre-receive"
child 400 g400 "origin/$W40"; REV_BASE=$W40 rev 400; : > "$T/hook40"; : > "$T/gl40"
r=$(cd "$T/c400" && SG_WAVE_GATES="echo gate >> '$T/gl40'" bash "$T/s/wave.sh" enter run-40 400 2>&1); rc=$?
[ $rc = 0 ] && $O merge-base --is-ancestor "$SIB" $W40 && $O cat-file -e $W40:g400 2>/dev/null && ok "X: a refused entry push rebases and enters" || ko "X: exit $rc: '$r'"
[ "$(wc -l < "$T/gl40" | tr -d ' ')" = 2 ] && ok "X: the gates run again on the rebased tree before the second push" || ko "X: the gates ran $(wc -l < "$T/gl40" | tr -d ' ') times: '$r'"
case "$(wfact 400 run-40)" in "entered "*) ok "X: a child that entered records its entry, no exclusion";; *) ko "X: ledger fact of #400: '$(wfact 400 run-40)'";; esac
# X2: the gates fail on the rebased tree, so the child does not enter, and the cause is recorded for the janitor.
child 401 g401 "origin/$W40"; REV_BASE=$W40 rev 401; rm -f "$T/g401"
rm -rf "$T/sib2"; $G clone -q "$T/origin.git" "$T/sib2" 2>/dev/null
(cd "$T/sib2" && $G checkout -q -b sib "origin/$W40" && echo s > s41 && $G add s41 && $G -c user.email=a@b -c user.name=t commit -q -m "another sibling (Refs #498)")
SIB=$($G -C "$T/sib2" rev-parse HEAD); $G -C "$T/sib2" push -q origin HEAD:refs/heads/sib41
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do [ "\$ref" = refs/heads/$W40 ] && [ -e "$T/hook40" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook40"; mkdir -p "$T/origin.git/refs/heads/wave"; printf '%s\n' "$SIB" > "$T/origin.git/refs/heads/$W40"; exit 1
H
: > "$T/hook40"; before=$SIB
r=$(cd "$T/c401" && SG_WAVE_GATES="[ -e '$T/g401' ] && { echo red after the rebase; exit 1; }; touch '$T/g401'" bash "$T/s/wave.sh" enter run-40 401 2>&1); rc=$?
[ $rc != 0 ] && [ "$($O rev-parse $W40)" = "$before" ] && ok "X2: a rebased tree the gates fail never enters" || ko "X2: exit $rc, tip $($O rev-parse $W40): '$r'"
case "$(wfact 401 run-40)" in "refused the local gates failed: red after the rebase"*) ok "X2: the exclusion cause is recorded";; *) ko "X2: ledger fact '$(wfact 401 run-40)'";; esac
# Y (#682): a hold records its cause too, so the janitor names it when the wave lands without the child.
child 402 g402 "origin/$W40"; REV=none rev 402
r=$(cd "$T/c402" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-40 402 2>&1)
case "$(wfact 402 run-40)" in "held no review of gui/402 yet"*) ok "Y: a review hold records its cause";; *) ko "Y: ledger fact '$(wfact 402 run-40)': '$r'";; esac

# Z (#694): a run whose final gate (ac-verify) failed takes no child into its wave: the child is refused, its cause is recorded,
# and the wave lands empty. A run that passed, or one with no verdict yet, lets its child enter as before.
for rn in 41 42 43; do start2 run-$rn >/dev/null; mkdir -p "$T/co/runs/run-$rn"; done
printf '%s\n' '{"event": "run_start"}' \
  '{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:ac_failed:bash contrib/squad-goal/scripts/fast-gate.sh"]}' > "$T/co/runs/run-41/record.jsonl"
printf '%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-42/record.jsonl"
printf '%s\n' '{"event": "run_start"}' > "$T/co/runs/run-43/record.jsonl"
child 410 g410 origin/wave/run-41; REV_BASE=wave/run-41 rev 410; before=$($O rev-parse wave/run-41)
r=$(cd "$T/c410" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-41 410 2>&1); rc=$?
[ $rc != 0 ] && [ "$($O rev-parse wave/run-41)" = "$before" ] && ok "Z: a child of a run whose final gate failed never enters" || ko "Z: exit $rc, tip moved: '$r'"
case "$(wfact 410 run-41)" in "refused the run's final gate failed"*) ok "Z: the exclusion names the failed final gate";; *) ko "Z: ledger fact '$(wfact 410 run-41)': '$r'";; esac
for rn in 42 43; do n=$((410 + rn - 41))
  child $n g$n origin/wave/run-$rn; REV_BASE=wave/run-$rn rev $n
  r=$(cd "$T/c$n" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-$rn $n 2>&1); rc=$?
  [ $rc = 0 ] && $O cat-file -e wave/run-$rn:g$n 2>/dev/null && ok "Z: a child of run-$rn (passed or live) enters" || ko "Z: run-$rn exit $rc: '$r'"
done


# AA (spec 1163, #1174): an unproven outcome admits with its obligation. At the entry the child enters and the run's entry
# record carries the obligation; at the tip the wave lands on the same word and no child is blamed. A measured red still
# refuses (case C) -- only a word the gate wrote can admit.
start2 run-50 >/dev/null; child 500 g500 "origin/wave/run-50"; REV_BASE=wave/run-50 rev 500; : > "$T/board.log"; rm -rf "$T/st/obligations" "$T/st/proof-card"
r=$(cd "$T/c500" && SG_WAVE_GATES='printf "unproven\tsaturation: the battery crashed twice\tunknown\truff\n" > "$GATE_OUTCOME_FILE"; exit 4' bash "$T/s/wave.sh" enter run-50 500 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e wave/run-50:g500 2>/dev/null && ok "AA: an unproven entry gate admits the child" || ko "AA: entry exit $rc: '$r'"
case "$(wfact 500 run-50)" in "entered "*unproven*obligation*) ok "AA: the entry fact carries the obligation in the run's record";; *) ko "AA: entry fact '$(wfact 500 run-50)'";; esac
obf=$(obl_for "$($G -C "$T/c500" rev-parse HEAD)"); [ -n "$obf" ] && [ "$(cut -f4 "$obf")" = ruff ] && ok "AA: the obligation record names the check that did not run" || ko "AA: obligation '$(cat "$T/st/obligations"/* 2>/dev/null)'"
/usr/bin/grep -qx 'o 3 Batching 500' "$T/board.log" && ok "AA: an unproven entry moves the card to Batching" || ko "AA: card: $(cat "$T/board.log" 2>/dev/null)"
# AD (#1176): the card carries the fact of the unproven entry while the obligation is open, and the entry records which
# obligation the settlement answers.
/usr/bin/grep -qx -- '--proof o 3 500 unproven' "$T/board.log" && ok "AD: the unproven entry puts the fact on the card" || ko "AD: no proof fact: $(cat "$T/board.log" 2>/dev/null)"
[ -n "$(/bin/ls -A "$T/st/proof-card/500" 2>/dev/null)" ] && ok "AD: the issue maps to the obligation the settlement answers" || ko "AD: no proof-card mapping: $(/bin/ls -A "$T/st/proof-card" 2>&1)"
# AF (#1176): the fact stays while the obligation is open. A later entry on a passing gate proves the new tree, not the tree
# the obligation names, so only the settlement answers it; the entry neither clears the card nor the mapping.
(cd "$T/c500" && echo fix > g500fix && $G add g500fix && $G -c user.email=a@b -c user.name=t commit -q -m "fix 500 (Refs #500)") \
  && REV_BASE=wave/run-50 rev 500
: > "$T/board.log"
r=$(cd "$T/c500" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-50 500 2>&1); rc=$?
[ $rc = 0 ] && [ -n "$(/bin/ls -A "$T/st/proof-card/500" 2>/dev/null)" ] && [ -z "$(/usr/bin/grep -- '--proof' "$T/board.log" 2>/dev/null)" ] \
  && ok "AF: a later pass entry keeps the fact while the obligation is open" \
  || ko "AF: rc $rc, board '$(cat "$T/board.log" 2>/dev/null)', mapping '$(/bin/ls -A "$T/st/proof-card/500" 2>&1)'"
# AC: a pass entry carries no obligation: the run's entry record names none, and no obligation record names its tree.
start2 run-52 >/dev/null; child 520 g520 "origin/wave/run-52"; REV_BASE=wave/run-52 rev 520; rm -rf "$T/st/obligations" "$T/st/proof-card"; : > "$T/board.log"
r=$(cd "$T/c520" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-52 520 2>&1); rc=$?
case "$(wfact 520 run-52)" in *obligation*) ko "AC: a pass entry's fact carries an obligation: '$(wfact 520 run-52)'";; *) ok "AC: a pass entry's fact carries no obligation";; esac
[ $rc = 0 ] && [ -z "$(obl_for "$($G -C "$T/c520" rev-parse HEAD)")" ] && ok "AC: a pass entry writes no obligation record" || ko "AC: pass obligation '$(cat "$T/st/obligations"/* 2>/dev/null)'"
[ -z "$(/usr/bin/grep -- '--proof' "$T/board.log" 2>/dev/null)" ] && [ ! -e "$T/st/proof-card/520" ] && ok "AC: a proven entry never carries the fact" || ko "AC: a proven entry carried the fact: $(cat "$T/board.log" 2>/dev/null)"
# AE (#1802, spec 1797): a wave whose tip check is unproven never lands. The entry runs no battery by default, so the tip
# check is the one proof of the wave's children, and an unproven one holds the wave until a pass or a fail. No child is
# blamed, no try is counted, and no card is delivered.
printf '#!/bin/bash\nprintf "unproven\\tsaturation: the battery crashed twice\\tunknown\\truff\\n" > "$GATE_OUTCOME_FILE"\nexit 4\n' > "$T/unproven-tip.sh"
start2 run-51 >/dev/null; child 510 g510 "origin/wave/run-51"; en2 run-51 510 >/dev/null; : > "$T/board.log"; : > "$T/st/gate-history"; rm -rf "$T/st/obligations" "$T/st/proof-card"
r=$(SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' bash '$T/unproven-tip.sh'" SG_WAVE_CHILD_GATES='exit 1' land2 run-51 2>&1); rc=$?
[ $rc != 0 ] && ! $O cat-file -e dev:g510 2>/dev/null && case "$r" in *"WAVE WAIT"*unproven*) true;; *) false;; esac && ok "AE: an unproven tip check holds the wave and says so" || ko "AE: tip exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*|*REQUEUE*) ko "AE: an unproven tip check blamed a child: '$r'";; *) ok "AE: an unproven tip check blames no child";; esac
[ -z "$(tries 510 run-51)" ] && ok "AE: an unproven tip check counts no landing try" || ko "AE: tries $(tries 510 run-51)"
[ -z "$(/usr/bin/grep -- '--proof\|Closed' "$T/board.log" 2>/dev/null)" ] && ok "AE: a held wave delivers no card" || ko "AE: a held wave wrote a card: $(cat "$T/board.log" 2>/dev/null)"
[ "$(awk -F'\t' '$8 == "unproven"' "$T/st/gate-history" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] \
  && ok "AE: the gate record names the tip's unproven outcome" || ko "AE: gate history: '$(cat "$T/st/gate-history" 2>&1)'"

# AG (#2149): an unproven tip past the age cap still never lands, and it frees the run all the same. Before, the rc=5 branch
# returned before the age-cap requeue, so an unproven tip held its issue for ever: the wave never lands, the run never ends,
# and nothing else frees the issue. The capped pass puts each issue the wave holds back in the queue and ends the run.
start2 run-53 >/dev/null; child 530 g530 "origin/wave/run-53"; en2 run-53 530 >/dev/null
printf 'run-53\n' > "$T/st/auto/disp-530"
mkdir -p "$T/co/runs/run-53"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #530 work"}}' > "$T/co/runs/run-53/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-53"
: > "$T/board.log"
r=$(SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' bash '$T/unproven-tip.sh'" SG_WAVE_CHILD_GATES='exit 1' land2 run-53 2>&1); rc=$?
[ $rc != 0 ] && ! $O cat-file -e dev:g530 2>/dev/null && case "$r" in *"WAVE WAIT"*unproven*) true;; *) false;; esac \
  && ok "AG: an unproven tip past the age cap never lands" || ko "AG: tip exit $rc: '$r'"
case "$r" in *"WAVE REQUEUE #530"*"age cap"*) ok "AG: the capped unproven wave puts its issue back in the queue";; \
  *) ko "AG: the issue of the capped unproven wave stayed claimed: '$r'";; esac

# AH (#2295, ADR-0170): a card stands in Testing while its wave's tip gate waits or runs. The landing writes Testing for each
# card of the wave before the gate starts. An unproven or red attempt writes no move. The landing then moves the card to Closed.
# The tip stub writes GATE into the board log, so the log shows the order of the writes and the gate.
printf '#!/bin/bash\necho GATE >> "%s/board.log"\nexit "$(cat "%s/ah-rc")"\n' "$T" "$T" > "$T/ah-tip.sh"
start2 run-55 >/dev/null; child 550 g550 "origin/wave/run-55"; en2 run-55 550 >/dev/null
start2 run-56 >/dev/null; child 560 g560 "origin/wave/run-56"; en2 run-56 560 >/dev/null
: > "$T/board.log"; echo 4 > "$T/ah-rc"
r=$(SG_WAVE_TIP_GATES="bash '$T/ah-tip.sh'" SG_WAVE_CHILD_GATES=true land2 run-55 2>&1); rc=$?
[ "$(cat "$T/board.log")" = "$(printf 'o 3 Testing 550\nGATE')" ] && ok "AH: the card moves to Testing before the tip gate starts" || ko "AH: board log '$(cat "$T/board.log")': '$r'"
[ $rc != 0 ] && ! $O cat-file -e dev:g550 2>/dev/null && ok "AH: the unproven tip holds the wave" || ko "AH: exit $rc: '$r'"
: > "$T/board.log"; echo 0 > "$T/ah-rc"
r=$(SG_WAVE_TIP_GATES="bash '$T/ah-tip.sh'" SG_WAVE_CHILD_GATES=true land2 run-55 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e dev:g550 2>/dev/null && ok "AH: the wave lands on a green tip" || ko "AH: exit $rc: '$r'"
[ "$(/usr/bin/grep -E '^(o 3 [A-Z][a-z]+ 550|GATE)$' "$T/board.log")" = "$(printf 'o 3 Testing 550\nGATE\no 3 Closed 550')" ] \
  && ok "AH: the landing moves the card from Testing to Closed" || ko "AH: board log '$(cat "$T/board.log")': '$r'"
: > "$T/board.log"; echo 1 > "$T/ah-rc"
r=$(SG_WAVE_TIP_GATES="bash '$T/ah-tip.sh'" SG_WAVE_CHILD_GATES=true land2 run-56 2>&1); rc=$?
[ $rc != 0 ] && ! $O cat-file -e dev:g560 2>/dev/null && ok "AH: a red tip does not land" || ko "AH: exit $rc: '$r'"
[ "$(/usr/bin/grep -v '^GATE$' "$T/board.log")" = "o 3 Testing 560" ] && ok "AH: a red attempt writes no move after Testing" || ko "AH: board log '$(cat "$T/board.log")': '$r'"

# The ledger replaced the auto/excluded-<run>-<n> marker (ADR-0149 rule 6): no case leaves one.
[ -z "$(/bin/ls "$T/st/auto" | /usr/bin/grep '^excluded-')" ] && ok "no excluded- marker is written" || ko "excluded markers: $(/bin/ls "$T/st/auto" | /usr/bin/grep '^excluded-' | tr '\n' ' ')"

# S775 (#775): a wave closes only the issues its run was dispatched for. A `Refs #N` outside the run's dispatch set (an engine
# commit that named the first #N of the issue's prose) is reported in one WAVE-NOTE line and a row of wave-outside.tsv, and
# that issue is never closed, never moved, never given a delivery comment.
W14=wave/run-14; start2 run-14 >/dev/null
child 140 g140 "origin/$W14" && (cd "$T/c140" && echo e > e140 && $G add e140 && $G -c user.email=a@b -c user.name=t commit -q -m "squad: route.router.1 passed for #141" -m "Refs #141")
en2 run-14 140 >/dev/null
RD="$T/co/runs/run-14"; mkdir -p "$RD"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #140 work; it is the work #141 waits on"}}' > "$RD/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/cq/"*141* "$T/st/wave-outside.tsv"
r=$(cd "$T/co" && bash "$T/s/wave.sh" land run-14 "$RD" 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -q 'issues/140 -f state=closed' "$T/gh.log" && ok "S775: the dispatched issue closes with the landing" || ko "S775: #140 not closed: rc $rc '$r'"
/usr/bin/grep -q 'issues/141 -f state=closed' "$T/gh.log" && ko "S775: #141, outside the dispatch set, was closed: '$r'" || ok "S775: a Refs outside the dispatch set is not closed"
/usr/bin/grep -q ' 141$' "$T/board.log" && ko "S775: #141's card moved" || ok "S775: the card of an issue outside the dispatch set does not move"
[ -z "$(/bin/ls "$T/st/cq" | /usr/bin/grep '#141-')" ] && ok "S775: no delivery comment is queued for #141" || ko "S775: a comment is queued for #141"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE-NOTE.*#141.*outside.*dispatch')" = 1 ] && ok "S775: the outside Refs is named in one log line" || ko "S775: no single WAVE-NOTE for #141: '$r'"
awk -F '\t' '$2 == "run-14" && $3 == "141"' "$T/st/wave-outside.tsv" 2>/dev/null | /usr/bin/grep -q . && ok "S775: the outside Refs is recorded in wave-outside.tsv" || ko "S775: no wave-outside.tsv row: '$(cat "$T/st/wave-outside.tsv" 2>&1)'"

# S776 (#776): a landing that gives up acts only on the issues its run was dispatched for, as the close does (#775). A
# `Refs #N` outside the dispatch set gets no label, no card move, no comment and no ledger try; it is reported in one
# WAVE-NOTE line and a row of wave-outside.tsv.
W15=wave/run-15; start2 run-15 >/dev/null
child 150 g150 "origin/$W15" && (cd "$T/c150" && echo e > e150 && $G add e150 && $G -c user.email=a@b -c user.name=t commit -q -m "squad: route.router.1 passed for #151" -m "Refs #151")
en2 run-15 150 >/dev/null
RD="$T/co/runs/run-15"; mkdir -p "$RD"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #150 work; it is the work #151 waits on"}}' > "$RD/record.jsonl"
rm -rf "$T/w15"; $G clone -q "$T/origin.git" "$T/w15" 2>/dev/null
(cd "$T/w15" && $G checkout -q dev && echo other > g150 && $G add g150 && $G -c user.email=a@b -c user.name=t commit -q -m "conflicting work" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/cq/"*151* "$T/st/wave-outside.tsv"
r=$(cd "$T/co" && SG_WAVE_LAND_TRIES=1 bash "$T/s/wave.sh" land run-15 "$RD" 2>&1); rc=$?
[ $rc = 1 ] && [ "$(/usr/bin/grep -c '^issue ' "$T/st/auto/kept-fix_150-land-run-15")" = 1 ] && ! /usr/bin/grep -q 'issue 151' "$T/st/auto/kept-fix_150-land-run-15" && ok "S776: the conflicting landing keeps the dispatched issue alone in its marker (#1500)" || ko "S776: #150 marker: rc $rc '$r' '$(cat "$T/st/auto/kept-fix_150-land-run-15" 2>&1)'"
/usr/bin/grep -q 'issues/151/' "$T/gh.log" && ko "S776: #151, outside the dispatch set, was written: '$(cat "$T/gh.log")'" || ok "S776: a Refs outside the dispatch set gets no label"
/usr/bin/grep -q ' 151$' "$T/board.log" && ko "S776: #151's card moved" || ok "S776: the card of an issue outside the dispatch set does not move"
[ -z "$(/bin/ls "$T/st/cq" | /usr/bin/grep '#151-')" ] && ok "S776: no comment is queued for #151" || ko "S776: a comment is queued for #151"
[ -z "$(tries 151 run-15)" ] && ok "S776: no landing try is counted for #151" || ko "S776: tries of #151: $(tries 151 run-15)"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE-NOTE.*#151.*outside.*dispatch')" = 1 ] && ok "S776: the outside Refs is named in one log line" || ko "S776: no single WAVE-NOTE for #151: '$r'"
awk -F '\t' '$2 == "run-15" && $3 == "151"' "$T/st/wave-outside.tsv" 2>/dev/null | /usr/bin/grep -q . && ok "S776: the outside Refs is recorded in wave-outside.tsv" || ko "S776: no wave-outside.tsv row: '$(cat "$T/st/wave-outside.tsv" 2>&1)'"

# S777 (#1340): a landing whose push to the base is refused twice takes the stuck path: its issue goes back to Todo as
# no-landing (board.py refuses any other automated backward move from Code Complete). The origin refuses every push to dev.
W1340=wave/run-1340; start2 run-1340 >/dev/null; child 1340 g1340 "origin/$W1340"; en2 run-1340 1340 >/dev/null
cp "$T/origin.git/hooks/pre-receive" "$T/pre-receive.keep"; printf '#!/bin/bash\nprintf "error: refused by the test remote\\nhint: the operator reads the last line\\nfatal: the test remote refuses every push to dev\\n" >&2\nexit 1\n' > "$T/origin.git/hooks/pre-receive"
: > "$T/gh.log"; : > "$T/board.log"; : > "$T/board-reason.log"; rm -f "$T/st/cq/"*1340*
r=$(SG_WAVE_LAND_TRIES=1 land2 run-1340); rc=$?
cp "$T/pre-receive.keep" "$T/origin.git/hooks/pre-receive"
# The reason quotes the push's own LAST line, as it did before (#1710): the reader that picks it forks no helper, but it still
# picks the last one, so a pasted failure reads the same.
case "$r" in *"WAVE-ERROR the push of wave/run-1340 to dev was refused: error: failed to push some refs to '"*) true;; *) false;; esac && [ $rc = 1 ] && ok "S777: a push refused twice takes the stuck path, quoting the push's own last line" || ko "S777: exit $rc: '$r'"
/usr/bin/grep -qx "no-landing o 3 Todo 1340" "$T/board-reason.log" && ok "S777: the stuck path moves #1340 back to Todo as no-landing (#1340)" || ko "S777: #1340 move reason: '$(cat "$T/board-reason.log")', board '$(cat "$T/board.log")'"

# (#646) The wave tip's check is the base battery over origin/$SG_BASE, and its pytest runs over the selection. The check's command
# is built from the shared definition and run in a clone of the remote, as wave.sh runs it; a stub python logs each call's argv.
# (#1873, #1865) The count follows the caller: wave.sh runs its tip check with SG_GATE_CALLER=tip, so the gate keeps the full
# count; a gate that names no caller reads as a child's own and takes one slot. The fixture names the caller the wave names,
# else it proves a count the landing never runs.
printf '#!/bin/bash\necho "$*" >> "%s/argv.log"\ncase "$*" in *squad.testselect*) cat "%s/select.out" 2>/dev/null;; esac\nexit 0\n' "$T" "$T" > "$T/selpy"; chmod +x "$T/selpy"
tipcmd=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" "$T/selpy" wave-tip 2>/dev/null)
rm -rf "$T/tipclone"; $G clone -q "$T/origin.git" "$T/tipclone" 2>/dev/null
printf 'tests/test_a.py\n' > "$T/select.out"; rm -f "$T/argv.log"
r=$(cd "$T/tipclone" && SG_GATE_CALLER=tip SG_GATE_WORKERS=3 SG_STATE="$T/st" bash -c "$tipcmd" 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 3 -m not heavy tests/test_a.py" "$T/argv.log" && ok "the wave tip check passes the selection to pytest" || ko "tip check argv: rc $rc: '$(cat "$T/argv.log" 2>&1)' $r"
rm -rf "$T/tipclone2"; $G clone -q "$T/origin.git" "$T/tipclone2" 2>/dev/null
printf '#!/bin/bash\necho "$*" >> "%s/argv2.log"\ncase "$*" in *squad.testselect*) cat "%s/select.out" 2>/dev/null;; esac\nexit 0\n' "$T" "$T" > "$T/selpy2"; chmod +x "$T/selpy2"
tipcmd2=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" "$T/selpy2" wave-tip 2>/dev/null)
( cd "$T/tipclone2" && SG_GATE_WORKERS=3 SG_STATE="$T/st" bash -c "$tipcmd2" ) >/dev/null 2>&1
/usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 1 -m not heavy tests/test_a.py" "$T/argv2.log" && ok "a tip check that names no caller reads as a child's and takes one slot (#1865)" || ko "no-caller tip check argv: '$(cat "$T/argv2.log" 2>&1)'"
/usr/bin/grep -q -- "squad.testselect --stage wave-tip --base origin/dev" "$T/argv.log" && ok "the wave tip check selects the delta at the wave-tip stage" || ko "wave-tip stage: '$(cat "$T/argv.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $sel $scope" = "fast pass selected 1 files delta" ] && ok "the wave tip check's status line states the selected file count and the delta scope" || ko "tip check status: '$(cat "$T/st/gate-status" 2>&1)'"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
