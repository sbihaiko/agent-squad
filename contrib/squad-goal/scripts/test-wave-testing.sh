#!/bin/bash
# seconds: 17.78
# The Testing card limit holds a new wave out of its gate (#2439, SPEC 2427, ADR-0170): with Testing at its card limit a ready
# wave does not enter its gate and the log names the limit; under its limit it enters; with no limit it enters; and a wave
# already in its gate -- one of its own cards reads Testing -- is never stopped by the limit. A batch enters its gate only when
# every run of it may enter, and a broken limit read is a defect with its own exit, never a full column.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"
mkdir -p "$T/co/docs/squad"
limit() { printf 'columns:\n  Testing:\n    max_cards: %s\n' "$1" > "$T/co/docs/squad/defaults.yaml"; }
rows() { python3 - "$T/st/hub-items.json" "$@" <<'EOF'
import json, sys, time
items = []
for row in sys.argv[2:]:
    n, state = row.split("\t")
    items.append({"n": int(n), "st": state, "r": "x/y"})
json.dump({"t": time.time(), "repo": "x/y", "items": items}, open(sys.argv[1], "w"))
EOF
}

# T2439-0: the seam the wave reads. The shipped board read answers "<issue>\t<column>" rows from the hub cache, and the
# shipped count reads those rows; a change to either shape either holds every wave or admits every wave, so the pair is pinned
# here over the real scripts, never a stub of them.
rows '9001	Testing' '9002	Testing' '9003	Todo'
out=$(SG_REPO=x/y SG_BOARD_CACHE="$T/st/hub-items.json" python3 "$T/s/issue_state.py" 2>/dev/null)
[ "$(printf '%s\n' "$out" | awk -F'\t' '$2 == "Testing"' | /usr/bin/grep -c .)" = 2 ] && ok "T2439-0: the board read answers issue<TAB>column rows" || ko "T2439-0: the board read answered '$out'"
printf '%s\n' "$out" > "$T/board-rows"
tn=$( (. "$T/s/columnlimit.sh"; sg_column_cards Testing "$T/board-rows") )
[ "$tn" = 2 ] && ok "T2439-0: the shipped count reads the Testing rows of that board" || ko "T2439-0: the count answered '$tn'"

# T2439-1: Testing at its card limit. Two other cards stand in Testing and the limit is two, so a ready wave does not enter
# its gate: the tip's check never runs, nothing lands, and the line names the limit.
W50=wave/run-50; start2 run-50 >/dev/null; child 5001 g5001 "origin/$W50"; en2 run-50 5001 >/dev/null
limit 2; rows '9001	Testing' '9002	Testing'; rm -f "$T/st/tip.log"
r=$(SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-50 2>&1); rc=$?
[ $rc != 0 ] && ok "T2439-1: a wave held by the Testing limit does not land" || ko "T2439-1: exit $rc: '$r'"
[ ! -e "$T/st/tip.log" ] && ok "T2439-1: the held wave never enters its gate" || ko "T2439-1: the tip's check ran: '$(cat "$T/st/tip.log" 2>/dev/null)'"
case "$r" in *"Testing column holds 2 card(s) of its limit 2"*) ok "T2439-1: the log names the Testing limit" ;; *) ko "T2439-1: the limit is not named: '$r'";; esac
$O cat-file -e dev:g5001 2>/dev/null && ko "T2439-1: the held wave landed" || ok "T2439-1: the held wave pushes nothing to dev"

# T2439-2: Testing under its limit. One other card stands in Testing under a limit of two, so the ready wave enters its gate
# and lands, and its card moves to Testing.
W51=wave/run-51; start2 run-51 >/dev/null; child 5002 g5002 "origin/$W51"; en2 run-51 5002 >/dev/null
limit 2; rows '9001	Testing'; rm -f "$T/st/tip.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-51 2>&1); rc=$?
[ $rc = 0 ] && ok "T2439-2: a wave under the Testing limit lands" || ko "T2439-2: exit $rc: '$r'"
[ -e "$T/st/tip.log" ] && ok "T2439-2: the wave under the limit enters its gate" || ko "T2439-2: the tip's check never ran: '$r'"
$O cat-file -e dev:g5002 2>/dev/null && ok "T2439-2: the wave enters its gate and lands" || ko "T2439-2: the child is not in dev"
/usr/bin/grep -q 'Testing 5002' "$T/board.log" && ok "T2439-2: the card of the gated wave stands in Testing" || ko "T2439-2: no Testing write: '$(cat "$T/board.log")'"

# T2439-3: No Testing limit. Five cards stand in Testing and the project file sets no limit, so the ready wave enters its
# gate and lands.
W52=wave/run-52; start2 run-52 >/dev/null; child 5003 g5003 "origin/$W52"; en2 run-52 5003 >/dev/null
limit none; rows '9001	Testing' '9002	Testing' '9003	Testing' '9004	Testing' '9005	Testing'; rm -f "$T/st/tip.log"
r=$(SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-52 2>&1); rc=$?
[ $rc = 0 ] && ok "T2439-3: with no Testing limit a ready wave lands" || ko "T2439-3: exit $rc: '$r'"
$O cat-file -e dev:g5003 2>/dev/null && ok "T2439-3: with no Testing limit a ready wave enters its gate" || ko "T2439-3: the child is not in dev: '$r'"

# T2439-4: A wave already in its gate is never stopped by the limit. Its own card already reads Testing and another card does
# too under a limit of one, so the wave continues its gate and lands.
W53=wave/run-53; start2 run-53 >/dev/null; child 5004 g5004 "origin/$W53"; en2 run-53 5004 >/dev/null
limit 1; rows '5004	Testing' '9001	Testing'; rm -f "$T/st/tip.log"
r=$(SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-53 2>&1); rc=$?
[ $rc = 0 ] && ok "T2439-4: a wave already in its gate lands" || ko "T2439-4: exit $rc: '$r'"
[ -e "$T/st/tip.log" ] && ok "T2439-4: the limit never stops a wave already in its gate" || ko "T2439-4: the tip's check never ran: '$r'"
$O cat-file -e dev:g5004 2>/dev/null && ok "T2439-4: the wave already in its gate is not pushed back" || ko "T2439-4: the child is not in dev"

# T2439-5: a batch never carries a new wave past the limit. Two waves land together: one is already in its gate -- its own
# card 5401 reads Testing -- and one is new. Testing holds one card and its limit is one, so only the continued wave may
# enter. The batch splits, the continued wave lands, and the new wave waits for the limit.
W54=wave/run-54; start2 run-54 >/dev/null; child 5401 g5401 "origin/$W54"; en2 run-54 5401 >/dev/null
W55=wave/run-55; start2 run-55 >/dev/null; child 5501 g5501 "origin/$W55"; en2 run-55 5501 >/dev/null
limit 1; rows '5401	Testing'; rm -f "$T/st/tip.log"
mkdir -p "$T/co/runs/run-54" "$T/co/runs/run-55"
printf '%s\n' "$T/co/runs/run-54" > "$T/st/auto/wland-run-54.queued"
r=$( (cd "$T/co" && SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' bash "$T/s/wave.sh" land run-55 "$T/co/runs/run-55" 2>&1) ); rc=$?
[ $rc != 0 ] && ok "T2439-5: the pass ends with the new wave held" || ko "T2439-5: exit $rc: '$r'"
$O cat-file -e dev:g5401 2>/dev/null && ok "T2439-5: the wave already in its gate lands" || ko "T2439-5: the continued wave did not land: '$r'"
$O cat-file -e dev:g5501 2>/dev/null && ko "T2439-5: the new wave entered the batch gate" || ok "T2439-5: the new wave never enters the batch gate"
case "$r" in *"the Testing column holds 1 card(s) of its limit 1"*) ok "T2439-5: the log names the limit for the new wave" ;; *) ko "T2439-5: the limit is not named: '$r'";; esac

# T2439-6: a broken board-column read is a defect, never a full column. The limit read fails, so the pass stops with exit 2
# and one WAVE DEFECT line, and the wave enters no gate: a broken seam must never look like backpressure on Testing.
W56=wave/run-56; start2 run-56 >/dev/null; child 5601 g5601 "origin/$W56"; en2 run-56 5601 >/dev/null
limit 2; rows '9001	Testing'; rm -f "$T/st/tip.log"
r=$(SG_SQUAD="$T/nothing" SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-56 2>&1); rc=$?
[ $rc = 2 ] && ok "T2439-6: a broken limit read exits 2" || ko "T2439-6: exit $rc: '$r'"
case "$r" in *"WAVE DEFECT"*) ok "T2439-6: the line names a defect, never a full column" ;; *) ko "T2439-6: no defect line: '$r'";; esac
[ ! -e "$T/st/tip.log" ] && ok "T2439-6: the wave enters no gate on a broken read" || ko "T2439-6: the tip's check ran"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
