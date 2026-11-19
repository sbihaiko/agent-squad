#!/bin/bash
# seconds: 4.17
# Guard: the Monitor source reads the loop's log and never starts the loop. SKILL.md arms the Monitor with watchtail.sh "only to
# read that log", and only the owner starts the loop (watchd.sh start). watchtail.sh ran `watchd.sh start` first, so a session
# that re-armed its Monitor restarted a loop the owner had stopped, three times in one evening. With watchd down it says so in
# one line and still tails the log. Stubbed watchd: no loop starts.
# usage: bash test-watchtail.sh [path/to/watchtail.sh]
src=${1:-$(dirname "$0")/watchtail.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; . "$(dirname "$0")/driver-tree.sh"; trap 'driver_tree_kill "$T"; rm -rf "$T"' EXIT
mkdir -p "$T/s" "$T/st"; cp "$src" "$T/s/watchtail.sh"
printf 'SG_STATE=%s\n' "$T/st" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/calls"\n' "$T" > "$T/s/watchd.sh"; chmod +x "$T/s/watchd.sh"
: > "$T/st/watch.log"
run() { bash "$T/s/watchtail.sh" > "$T/out" 2>&1 & p=$!; sleep 1; echo "EVENT one" >> "$T/st/watch.log"; sleep 1; kill $p 2>/dev/null; pkill -P $p 2>/dev/null; wait $p 2>/dev/null; }

# watchd down: no start, one line that says so, and the log is still read
rm -f "$T/st/watchd.pid"; run
[ ! -s "$T/calls" ] && ok "the Monitor source never starts the loop" || ko "watchtail.sh ran watchd.sh: $(cat "$T/calls")"
$G -q 'watchd is not running' "$T/out" && ok "a stopped loop is named in one line" || ko "no line names the stopped loop: $(cat "$T/out")"
$G -q 'EVENT one' "$T/out" && ok "the log is tailed" || ko "the log was not tailed: $(cat "$T/out")"

# watchd up: no line about it
sleep 30 & live=$!; echo "$live" > "$T/st/watchd.pid"; : > "$T/out"; run; kill $live 2>/dev/null
! $G -q 'not running' "$T/out" && $G -q 'EVENT one' "$T/out" && ok "a live loop is tailed with no warning" || ko "live loop: $(cat "$T/out")"
[ ! -s "$T/calls" ] && ok "a live loop is not started either" || ko "watchd.sh was called: $(cat "$T/calls")"
exit "$fail"
