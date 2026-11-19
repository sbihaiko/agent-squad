#!/bin/bash
# seconds: 2.43
# The token guard (token-guard.sh): a burn over the cap is a report line and one alert, never a stop. Dispatch pauses only
# while the burn over the last hour stays over 3x the cap's hourly share, and it resumes by itself once that burn falls under the cap.
# The off switch (#926): the profile the guard reads names SG_TOKEN_GUARD=off, and the pass writes nothing and pauses
# nothing. The profile is sourced on every pass, so a changed cap, factor or switch is read on the guard's next pass.
# Offline: no network, a stub env.sh, a temp state dir and a ledger written by hand. usage: bash test-token-guard.sh [token-guard.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/token-guard.sh}; T=$(mktemp -d); . "$d/driver-tree.sh"; trap 'driver_tree_kill "$T"; rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home" SG_TOKEN_NOTIFY=true; mkdir -p "$HOME" "$T/s" "$T/st"
cp "$src" "$T/s/token-guard.sh" 2>/dev/null || { echo "FAIL the guard is not there: $src"; exit 1; }
cp "$d/tokens.py" "$T/s/tokens.py" 2>/dev/null || { echo "FAIL tokens.py is not there: $d/tokens.py"; exit 1; }
: > "$T/s/env.sh"   # the guard sources env.sh; the stub leaves the caller's SG_* (a real profile supplies them)
G="$T/s/token-guard.sh"; L="$T/st/token-usage.tsv"; M="$T/st/token-guard.since"; P="$T/st/token-pause"; O="$T/st/token-over"; AL="$T/st/alerts.log"
now() { date +%s; }
iso() { python3 -c 'import datetime,sys;print(datetime.datetime.fromtimestamp(int(sys.argv[1])).isoformat())' "$1"; }   # tokens.py reads the ts as an ISO string
line() { printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(iso "$1")" "$2" "$3" "$4" "$5" "$6" >> "$L"; }
marker() { printf '%s\n' "$(( $(now) - $1 ))" > "$M"; }   # the guard started $1 seconds ago
guard() { SG_STATE="$T/st" "$@" bash "$G" 2>&1; }         # run the guard with the given env assignments

# inside the cap: exit 0, no output
rm -f "$L" "$M"; marker 10; line "$(now)" review:PR#9 claude:opus 100 50 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "under the cap: exit 0, no output" || ko "under cap: rc=$rc out=$o"

# over the cap: a report, never a stop. exit 2, one sentence naming the total, the cap and the top consumer, one alert,
# and no pause marker. The next pass over the cap says nothing and alerts no more; a pass under the cap re-arms the alert.
rm -f "$L" "$AL" "$O" "$P"; marker 10; line "$(now)" review:PR#7 claude:opus 1000000 600000 0
o=$(guard); rc=$?
[ $rc = 2 ] && ok "over the cap: exit 2, a report" || ko "over cap: rc=$rc out=$o"
case "$o" in *"goes on"*) ok "the sentence says the loop goes on";; *) ko "no 'goes on' in: $o";; esac
[ ! -e "$P" ] && ok "over the cap does not pause dispatch" || ko "over the cap wrote the pause marker"
[ "$(/usr/bin/grep -c 'Token burn' "$AL" 2>/dev/null)" = 1 ] && ok "over the cap writes one alert" || ko "alerts: $(cat "$AL" 2>/dev/null)"
o2=$(guard); rc2=$?
[ $rc2 = 2 ] && [ -z "$o2" ] && [ "$(/usr/bin/grep -c 'Token burn' "$AL")" = 1 ] && ok "a second pass over the cap is quiet and alerts no more" || ko "second pass: rc=$rc2 out=$o2"
rm -f "$L"; o3=$(guard); rc3=$?
[ $rc3 = 0 ] && [ ! -e "$O" ] && ok "a pass under the cap re-arms the alert" || ko "re-arm: rc=$rc3 out=$o3"
rm -f "$L" "$AL"; marker 10; line "$(now)" review:PR#7 claude:opus 1000000 600000 0; o=$(guard)
case "$o" in *1600000*) ok "the sentence names the measured total";; *) ko "no total in: $o";; esac
case "$o" in *1500000*) ok "the sentence names the cap";; *) ko "no cap in: $o";; esac
case "$o" in *review:PR#7*) ok "the sentence names the top consumer";; *) ko "no top consumer in: $o";; esac

# a line older than the marker never counts
rm -f "$L" "$M"; marker 8; line "$(( $(now) - 5000 ))" review:OLD claude:opus 2000000 0 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "a line older than the marker is not counted" || ko "old line: rc=$rc out=$o"

# a relight (no marker) starts a fresh measurement and writes the marker
rm -f "$M"; line "$(now)" review:NEW claude:opus 2000000 0 0
o=$(guard); rc=$?
[ $rc = 0 ] && ok "a relight (no marker) starts fresh: exit 0" || ko "relight: rc=$rc out=$o"
[ -f "$M" ] && ok "the relight writes the marker" || ko "no marker was written"

# the rate tripwire fires on a small elapsed window with a big burn (the total itself stays inside the cap)
rm -f "$L" "$O"; marker 10; line "$(now)" review:R claude:opus 600 400 0
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_CAP=1500 SG_TOKEN_BURN_MIN_SECONDS=2 bash "$G" 2>&1); rc=$?
[ $rc = 2 ] && ok "the rate report fires on a small window with a big burn" || ko "rate fire: rc=$rc out=$o"
case "$o" in *review:R*) ok "the rate sentence names the top consumer";; *) ko "no consumer in rate sentence: $o";; esac

# the rate tripwire waits for SG_TOKEN_BURN_MIN_SECONDS
rm -f "$L" "$O"; marker 10; line "$(now)" review:R claude:opus 600 400 0
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_CAP=1500 SG_TOKEN_BURN_MIN_SECONDS=30 bash "$G" 2>&1); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "the rate tripwire waits for SG_TOKEN_BURN_MIN_SECONDS" || ko "min gate: rc=$rc out=$o"

# one review burst inside the cap is no report (#493): the rate is read over a floor span (a quarter of the
# window), never over the few minutes since the marker. Live: 184963 tokens 361 s in, 12% of the cap, read as 44M/day.
rm -f "$L" "$O" "$P"; marker 361; line "$(now)" review:BURST claude:opus 150000 34963 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "one review burst inside the cap is no report and no pause" || ko "burst: rc=$rc out=$o"
# ... but a burst that would cross the cap over the floor span still fires, and the floor is a setting
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_RATE_FLOOR_S=3600 bash "$G" 2>&1); rc=$?
[ $rc = 2 ] && ok "a burst over the cap across the floor span is reported" || ko "floor fire: rc=$rc out=$o"
case "$o" in *184963*1500000*review:BURST*) ok "the rate sentence names total, cap and consumer";; *) ko "rate sentence: $o";; esac

# a cache read is counted at the cache-read price ratio (#495): a repository review re-reads its cached context every
# turn, so counted at full weight one review read as 560k and stopped the loop; at one tenth the same line is 111k.
rm -f "$L" "$O"; marker 361; line "$(now)" review:CACHE claude:opus 10000 1000 1000000
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "a cache read counts at one tenth, so a review inside the cap does not stop the loop" || ko "cache weight: rc=$rc out=$o"
o=$(SG_STATE="$T/st" SG_TOKEN_CACHE_WEIGHT=1 bash "$G" 2>&1); rc=$?
[ $rc = 2 ] && ok "the weight is a setting: at 1 the same line is reported" || ko "cache weight 1: rc=$rc out=$o"
case "$o" in *1011000*) ok "the sentence names the weighted total";; *) ko "weighted total missing: $o";; esac

# owner ruling: a cache read is not counted by default; the cap measures new input and output only. Ten million cached
# tokens in one review do not stop the loop.
rm -f "$L" "$O"; marker 361; line "$(now)" review:CACHE0 claude:opus 10000 1000 10000000
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "a cache read is not counted by default" || ko "cache read counted: rc=$rc out=$o"

# The pause: dispatch pauses only when the burn over the last hour stays over 3x the cap's hourly share (the default cap
# 1500000 a day is 62500 an hour, so 3x is 187500), measured over that hour. The guard writes token-pause, which the
# dispatch passes read, says so once and alerts once. It resumes by itself when the hourly burn falls under the cap's share.
rm -f "$L" "$AL" "$O" "$P"; marker 3700; line "$(now)" review:RUN claude:opus 150000 50000 0
o=$(guard); rc=$?
[ $rc = 1 ] && [ -e "$P" ] && ok "an hour over 3x the cap pauses dispatch: exit 1 and token-pause" || ko "pause: rc=$rc out=$o"
case "$o" in *200000*187500*review:RUN*pauses*) ok "the pause sentence names the hourly burn, the 3x line and the consumer";; *) ko "pause sentence: $o";; esac
[ "$(/usr/bin/grep -c 'pauses' "$AL" 2>/dev/null)" = 1 ] && ok "the pause writes one alert" || ko "pause alert: $(cat "$AL" 2>/dev/null)"
# a short burst before an hour of measurement is no pause
rm -f "$L" "$P"; marker 600; line "$(now)" review:BURST claude:opus 150000 50000 0
o=$(guard); rc=$?
[ ! -e "$P" ] && [ $rc != 1 ] && ok "a burst before one hour of measurement does not pause" || ko "early pause: rc=$rc out=$o"
# a burn older than the hour is not in the hourly measure
rm -f "$L" "$P" "$O"; marker 7200; line "$(( $(now) - 5000 ))" review:OLDHOUR claude:opus 200000 0 0
o=$(guard); rc=$?
[ ! -e "$P" ] && [ $rc = 0 ] && ok "a burn outside the last hour does not pause" || ko "old hour: rc=$rc out=$o"
# paused, an hourly burn between the cap's share and 3x keeps the pause, quietly
rm -f "$L" "$AL"; printf 'paused\n' > "$P"; marker 3700; line "$(now)" review:MID claude:opus 100000 0 0
o=$(guard); rc=$?
[ $rc = 1 ] && [ -e "$P" ] && [ -z "$o" ] && ok "a paused loop stays paused while the hourly burn is over the cap" || ko "hold: rc=$rc out=$o"
# paused, an hourly burn under the cap's share resumes dispatch by itself
rm -f "$L"; line "$(now)" review:LOW claude:opus 1000 0 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ ! -e "$P" ] && ok "the hourly burn under the cap resumes dispatch" || ko "resume: rc=$rc out=$o"
case "$o" in *resumes*) ok "the resume says so";; *) ko "resume sentence: $o";; esac
# the dispatch passes read the pause marker and start nothing while it is there
for f in autofrontier autobug; do /usr/bin/grep -q 'token-pause.*exit 0' "$d/$f.sh" && ok "$f.sh starts nothing while token-pause is there" || ko "$f.sh does not read token-pause"; done
# the cycle logs the guard's sentence and goes on: it never stops the loop on a burn
w=$(/usr/bin/grep -n 'token-guard.sh' "$d/watch.sh" | head -1 | cut -d: -f1)
sed -n "${w:-1},$(( ${w:-1} + 6 ))p" "$d/watch.sh" | /usr/bin/grep -q 'watchd.sh" stop' && ko "watch.sh still stops the loop on a burn" || ok "watch.sh does not stop the loop on a burn"

# a missing ledger: exit 0
rm -f "$L" "$O" "$P"; marker 10
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "a missing ledger: exit 0" || ko "no ledger: rc=$rc out=$o"

# a bad ledger line does not crash the guard
rm -f "$L"; marker 10; printf 'garbage with no tabs\n' >> "$L"; line "$(now)" review:OK claude:opus 100 0 0
o=$(guard); rc=$?
[ $rc = 0 ] && ok "a bad ledger line does not crash the guard" || ko "bad line: rc=$rc out=$o"

# an empty state dir (no marker yet): exit 0
rm -rf "$T/empty"; mkdir -p "$T/empty"
o=$(SG_STATE="$T/empty" bash "$G" 2>&1); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "an empty state dir: exit 0" || ko "empty dir: rc=$rc out=$o"

# a missing state dir: exit 0
o=$(SG_STATE="$T/nosuch" bash "$G" 2>&1); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "a missing state dir: exit 0" || ko "no dir: rc=$rc out=$o"

# The off switch (#926): the owner turns the guard off in the hub, which writes the profile the guard reads on its next
# pass. Off: no report, no alert, no pause, and a pause that stands clears on this pass, so dispatch resumes.
printf 'SG_TOKEN_GUARD=off\n' > "$T/s/env.sh"
rm -f "$L" "$AL" "$O" "$P"; marker 3700; line "$(now)" review:OFF claude:opus 2000000 500000 0; printf 'paused\n' > "$P"
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "the guard off: exit 0 and no report" || ko "off: rc=$rc out=$o"
[ ! -e "$P" ] && ok "the guard off clears a standing pause on its next pass" || ko "off left the pause marker"
[ ! -e "$O" ] && ok "the guard off writes no over-cap marker" || ko "off wrote token-over"
[ ! -e "$AL" ] && ok "the guard off writes no alert" || ko "off wrote an alert: $(cat "$AL" 2>/dev/null)"
# the same burn with the guard on again is a pause: off was the only cause
printf 'SG_TOKEN_GUARD=on\n' > "$T/s/env.sh"
o=$(guard); rc=$?
[ $rc = 1 ] && [ -e "$P" ] && ok "the guard on again pauses the same burn" || ko "on again: rc=$rc out=$o"

# A changed cap takes effect on the guard's next pass, with no restart: same ledger, same marker, another profile.
printf 'SG_TOKEN_BURN_CAP=1000\n' > "$T/s/env.sh"
rm -f "$L" "$AL" "$O" "$P"; marker 10; line "$(now)" review:PCAP claude:opus 400 200 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "the cap the profile names holds a burn under it" || ko "profile cap: rc=$rc out=$o"
printf 'SG_TOKEN_BURN_CAP=500\n' > "$T/s/env.sh"
o=$(guard); rc=$?
[ $rc = 2 ] && ok "a changed cap takes effect on the next pass" || ko "changed cap: rc=$rc out=$o"
case "$o" in *500*) ok "the report names the changed cap";; *) ko "changed cap not named: $o";; esac

# A changed pause factor takes effect on the guard's next pass too, read from the profile.
printf 'SG_TOKEN_BURN_CAP=6000000\nSG_TOKEN_PAUSE_FACTOR=5\n' > "$T/s/env.sh"   # share 250000/h, so 5x is 1250000
rm -f "$L" "$AL" "$O" "$P"; marker 3700; line "$(now)" review:PFACT claude:opus 300000 0 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ ! -e "$P" ] && ok "the profile's pause factor holds an hour under it" || ko "profile factor: rc=$rc out=$o"
printf 'SG_TOKEN_BURN_CAP=6000000\nSG_TOKEN_PAUSE_FACTOR=1\n' > "$T/s/env.sh"   # 1x is 250000, and the hour is 300000
o=$(guard); rc=$?
[ $rc = 1 ] && [ -e "$P" ] && ok "a changed pause factor takes effect on the next pass" || ko "changed factor: rc=$rc out=$o"
: > "$T/s/env.sh"

# the cap may come from the profile (env.sh) but the environment still wins
printf ': "${SG_TOKEN_BURN_CAP:=500}"\n' > "$T/s/env.sh"
rm -f "$L" "$O"; marker 10; line "$(now)" review:P claude:opus 400 200 0
o=$(guard); rc=$?
[ $rc = 2 ] && ok "the cap comes from the profile when the environment is silent" || ko "profile cap: rc=$rc out=$o"
rm -f "$O"; o=$(SG_STATE="$T/st" SG_TOKEN_BURN_CAP=1000 bash "$G" 2>&1); rc=$?
[ $rc = 0 ] && ok "the environment still wins over the profile" || ko "env wins: rc=$rc out=$o"
: > "$T/s/env.sh"

rm -rf "$T"; exit $fail
