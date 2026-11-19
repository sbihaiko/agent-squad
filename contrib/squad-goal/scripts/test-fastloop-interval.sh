#!/bin/bash
# seconds: 8.33
# The fast lane beats every SG_FAST_INTERVAL seconds: 120 unless the profile sets it (#329). The slow cycle keeps its own beat.
# fastloop.sh --interval prints the resolved value and exits, so no test sleeps. Offline: fixture profiles, a gh that always fails,
# and an alarm so a script that ignores --interval cannot loop on.
# usage: bash test-fastloop-interval.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
P=$T/profiles; mkdir -p "$P" "$T/bin"
printf '#!/bin/bash\nexit 1\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
base=': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:='$T'/co}"'
echo "$base" > "$P/alpha.sh"
printf '%s\n: "${SG_FAST_INTERVAL:=45}"\n' "$base" > "$P/beta.sh"
export SG_PROFILES_DIR=$P SG_STATE_BASE=$T/state SG_SQUAD=/dev/null PATH="$T/bin:$PATH"
unset SG_TOOLING_REPO SG_STATE SG_REPO SG_CHECKOUT SG_FAST_INTERVAL
got() { (cd "$T" && env "$@" perl -e 'alarm 5; exec @ARGV' bash "$d/fastloop.sh" --interval >"$T/out" 2>&1; tail -n 1 "$T/out"); }

[ "$(got SG_PROFILE=alpha)" = 120 ] && ok "default interval is 120 s" || ko "default interval: $(got SG_PROFILE=alpha)"
[ "$(got SG_PROFILE=beta)" = 45 ] && ok "a profile sets the interval" || ko "profile interval: $(got SG_PROFILE=beta)"
[ "$(got SG_PROFILE=alpha SG_FAST_INTERVAL=7)" = 7 ] && ok "the environment still wins" || ko "environment interval"
! /usr/bin/grep -q 'sleep 30' "$d/fastloop.sh" && ok "no fixed 30 s sleep is left" || ko "sleep 30 remains"
# (#569) the wait between two beats ends early on a NEW Todo card in the local board cache, and runs to its bound with no event.
# The lane runs for real in a fixture dir: stubbed passes log a line each, and a beat is one childcc line. No network, no model.
F=$T/lane; mkdir -p "$F" "$T/lst"; cp "$d/fastloop.sh" "$d/single.sh" "$d/issue_state.py" "$F/"; BC=$T/lst/board.json; LOG=$T/lane.log
printf 'export SG_STATE=%s SG_STATE_BASE=%s SG_REPO=x/alpha SG_OWNER=x SG_PROJECT=1 SG_BOARD_CACHE=%s\n' "$T/lst" "$T/cache" "$BC" > "$F/env.sh"
printf 'open("%s","a").write("beat\\n")\n' "$LOG" > "$F/childcc.py"
for s in autofrontier autobug; do printf '#!/bin/bash\nexit 0\n' > "$F/$s.sh"; chmod +x "$F/$s.sh"; done
board() { printf '{"repo":"x/alpha","items": [%s]}\n' "$1" > "$BC"; }   # board <items json>: the cache the hub writes
card() { printf '{"n": %s, "r": "x/alpha", "t": "t", "s": "OPEN", "st": "%s", "l": []}' "$1" "$2"; }
beats() { local c; c=$(/usr/bin/grep -c beat "$LOG" 2>/dev/null); echo "${c:-0}"; }
lane() { : > "$LOG"; rm -f "$T/lst/fastloop.pid"; env SG_FAST_INTERVAL=$1 SG_FAST_POLL=0.2 bash "$F/fastloop.sh" >"$T/lane.out" 2>&1 & LP=$!; }
lane_end() { kill "$LP" 2>/dev/null; wait "$LP" 2>/dev/null; pkill -P "$LP" 2>/dev/null; return 0; }
# until_beats <n> <seconds>: polls the beat count up to a deadline, so a loaded machine delays a case and never fails it
until_beats() { local i=0; while [ "$(beats)" -lt "$1" ] && [ $i -lt $(( $2 * 10 )) ]; do /bin/sleep 0.1; i=$((i+1)); done; [ "$(beats)" -ge "$1" ]; }
board "$(card 5 Todo),$(card 6 Done)"
lane 30; until_beats 1 10; s=$SECONDS; board "$(card 5 Todo),$(card 6 Done),$(card 7 Todo)"; until_beats 2 15; el=$((SECONDS - s)); b2=$(beats); lane_end
[ "$b2" -ge 2 ] && [ "$el" -lt 15 ] && ok "a new Todo card in the cache ends the wait early (second beat ${el}s into a 30 s wait)" || ko "new Todo card: beats $b2 after ${el}s"
board "$(card 5 Todo),$(card 6 Done)"
lane 30; until_beats 1 10; s=$SECONDS; board "$(card 5 Todo),$(card 6 Todo)"; until_beats 2 15; el=$((SECONDS - s)); b3=$(beats); lane_end
[ "$b3" -ge 2 ] && [ "$el" -lt 15 ] && ok "a card moved into Todo ends the wait early" || ko "card moved to Todo: beats $b3 after ${el}s"
board "$(card 5 Todo),$(card 6 Done)"
lane 4; until_beats 1 10; s=$SECONDS; board "$(card 5 Todo),$(card 6 Done),$(card 8 'In Progress')"; /bin/sleep 1; b4=$(beats); until_beats 2 15; el=$((SECONDS - s)); b5=$(beats); lane_end
[ "$b4" = 1 ] && [ "$b5" -ge 2 ] && [ "$el" -ge 3 ] && ok "with no new Todo card the wait runs to its bound (second beat ${el}s into a 4 s wait)" || ko "no event: beats $b4 then $b5 after ${el}s"
# a fractional beat still waits: an integer-only bound made the lane spin with no pause between beats
board "$(card 5 Todo)"
lane 0.5; /bin/sleep 2; b6=$(beats); lane_end
[ "$b6" -ge 2 ] && [ "$b6" -le 10 ] && ok "a fractional beat of 0.5 s still waits (beats $b6 in 2 s)" || ko "fractional beat: $b6 beats in 2 s (2 to 10 expected)"
rm -rf "$T"; exit $fail
