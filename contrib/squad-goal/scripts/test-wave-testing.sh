#!/bin/bash
# seconds: 17.78
# The Testing card limit holds a new wave out of its gate (#2439, SPEC 2427, ADR-0170): with Testing at its card limit a ready
# wave does not enter its gate and the log names the limit; under its limit it enters; with no limit it enters; and a wave
# already in its gate -- one of its own cards reads Testing -- is never stopped by the limit.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"
mkdir -p "$T/co/docs/squad"
limit() { printf 'columns:\n  Testing:\n    max_cards: %s\n' "$1" > "$T/co/docs/squad/defaults.yaml"; }
rows() { printf '%s\n' "$@" > "$T/board-rows"; }

# T2439-1: Testing at its card limit. Two other cards stand in Testing and the limit is two, so a ready wave does not enter
# its gate: the tip's check never runs, nothing lands, and the line names the limit.
W50=wave/run-50; start2 run-50 >/dev/null; child 5001 g5001 "origin/$W50"; en2 run-50 5001 >/dev/null
limit 2; rows '9001	Testing' '9002	Testing'; rm -f "$T/st/tip.log"
r=$(SG_BOARD_ROWS="$T/board-rows" SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-50 2>&1); rc=$?
[ $rc != 0 ] && ok "T2439-1: a wave held by the Testing limit does not land" || ko "T2439-1: exit $rc: '$r'"
[ ! -e "$T/st/tip.log" ] && ok "T2439-1: the held wave never enters its gate" || ko "T2439-1: the tip's check ran: '$(cat "$T/st/tip.log" 2>/dev/null)'"
case "$r" in *"Testing column holds 2 card(s) of its limit 2"*) ok "T2439-1: the log names the Testing limit" ;; *) ko "T2439-1: the limit is not named: '$r'";; esac
$O cat-file -e dev:g5001 2>/dev/null && ko "T2439-1: the held wave landed" || ok "T2439-1: the held wave pushes nothing to dev"

# T2439-2: Testing under its limit. One other card stands in Testing under a limit of two, so the ready wave enters its gate
# and lands, and its card moves to Testing.
W51=wave/run-51; start2 run-51 >/dev/null; child 5002 g5002 "origin/$W51"; en2 run-51 5002 >/dev/null
limit 2; rows '9001	Testing'; rm -f "$T/st/tip.log"; : > "$T/board.log"
r=$(SG_BOARD_ROWS="$T/board-rows" SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-51 2>&1); rc=$?
[ $rc = 0 ] && ok "T2439-2: a wave under the Testing limit lands" || ko "T2439-2: exit $rc: '$r'"
[ -e "$T/st/tip.log" ] && ok "T2439-2: the wave under the limit enters its gate" || ko "T2439-2: the tip's check never ran: '$r'"
$O cat-file -e dev:g5002 2>/dev/null && ok "T2439-2: the wave enters its gate and lands" || ko "T2439-2: the child is not in dev"
/usr/bin/grep -q 'Testing 5002' "$T/board.log" && ok "T2439-2: the card of the gated wave stands in Testing" || ko "T2439-2: no Testing write: '$(cat "$T/board.log")'"

# T2439-3: No Testing limit. Five cards stand in Testing and the project file sets no limit, so the ready wave enters its
# gate and lands.
W52=wave/run-52; start2 run-52 >/dev/null; child 5003 g5003 "origin/$W52"; en2 run-52 5003 >/dev/null
limit none; rows '9001	Testing' '9002	Testing' '9003	Testing' '9004	Testing' '9005	Testing'; rm -f "$T/st/tip.log"
r=$(SG_BOARD_ROWS="$T/board-rows" SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-52 2>&1); rc=$?
[ $rc = 0 ] && ok "T2439-3: with no Testing limit a ready wave lands" || ko "T2439-3: exit $rc: '$r'"
$O cat-file -e dev:g5003 2>/dev/null && ok "T2439-3: with no Testing limit a ready wave enters its gate" || ko "T2439-3: the child is not in dev: '$r'"

# T2439-4: A wave already in its gate is never stopped by the limit. Its own card already reads Testing and another card does
# too under a limit of one, so the wave continues its gate and lands.
W53=wave/run-53; start2 run-53 >/dev/null; child 5004 g5004 "origin/$W53"; en2 run-53 5004 >/dev/null
limit 1; rows '5004	Testing' '9001	Testing'; rm -f "$T/st/tip.log"
r=$(SG_BOARD_ROWS="$T/board-rows" SG_WAVE_TIP_GATES='echo ran >> "$SG_STATE/tip.log"; true' land2 run-53 2>&1); rc=$?
[ $rc = 0 ] && ok "T2439-4: a wave already in its gate lands" || ko "T2439-4: exit $rc: '$r'"
[ -e "$T/st/tip.log" ] && ok "T2439-4: the limit never stops a wave already in its gate" || ko "T2439-4: the tip's check never ran: '$r'"
$O cat-file -e dev:g5004 2>/dev/null && ok "T2439-4: the wave already in its gate is not pushed back" || ko "T2439-4: the child is not in dev"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
