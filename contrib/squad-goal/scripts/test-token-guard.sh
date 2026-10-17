#!/bin/bash
# The token tripwire (token-guard.sh): the loop stops on the first pass whose burn since the guard's own start marker crosses the cap.
# Offline: no network, a stub env.sh, a temp state dir and a ledger written by hand. usage: bash test-token-guard.sh [token-guard.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/token-guard.sh}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME" "$T/s" "$T/st"
cp "$src" "$T/s/token-guard.sh" 2>/dev/null || { echo "FAIL the guard is not there: $src"; exit 1; }
cp "$d/tokens.py" "$T/s/tokens.py" 2>/dev/null || { echo "FAIL tokens.py is not there: $d/tokens.py"; exit 1; }
: > "$T/s/env.sh"   # the guard sources env.sh; the stub leaves the caller's SG_* (a real profile supplies them)
G="$T/s/token-guard.sh"; L="$T/st/token-usage.tsv"; M="$T/st/token-guard.since"
now() { date +%s; }
iso() { python3 -c 'import datetime,sys;print(datetime.datetime.fromtimestamp(int(sys.argv[1])).isoformat())' "$1"; }   # tokens.py reads the ts as an ISO string
line() { printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(iso "$1")" "$2" "$3" "$4" "$5" "$6" >> "$L"; }
marker() { printf '%s\n' "$(( $(now) - $1 ))" > "$M"; }   # the guard started $1 seconds ago
guard() { SG_STATE="$T/st" "$@" bash "$G" 2>&1; }         # run the guard with the given env assignments

# inside the cap: exit 0, no output
rm -f "$L" "$M"; marker 10; line "$(now)" review:PR#9 claude:opus 100 50 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "under the cap: exit 0, no output" || ko "under cap: rc=$rc out=$o"

# over the cap: exit 1, one sentence naming the total, the cap and the top consumer
rm -f "$L"; marker 10; line "$(now)" review:PR#7 claude:opus 1000000 600000 0
o=$(guard); rc=$?
[ $rc = 1 ] && ok "over the cap: exit 1" || ko "over cap: rc=$rc out=$o"
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
rm -f "$L"; marker 10; line "$(now)" review:R claude:opus 600 400 0
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_CAP=1500 SG_TOKEN_BURN_MIN_SECONDS=2 bash "$G" 2>&1); rc=$?
[ $rc = 1 ] && ok "the rate tripwire fires on a small window with a big burn" || ko "rate fire: rc=$rc out=$o"
case "$o" in *review:R*) ok "the rate sentence names the top consumer";; *) ko "no consumer in rate sentence: $o";; esac

# the rate tripwire waits for SG_TOKEN_BURN_MIN_SECONDS
rm -f "$L"; marker 10; line "$(now)" review:R claude:opus 600 400 0
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_CAP=1500 SG_TOKEN_BURN_MIN_SECONDS=30 bash "$G" 2>&1); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "the rate tripwire waits for SG_TOKEN_BURN_MIN_SECONDS" || ko "min gate: rc=$rc out=$o"

# one review burst inside the cap does not stop the loop (#493): the rate is read over a floor span (a quarter of the
# window), never over the few minutes since the marker. Live: 184963 tokens 361 s in, 12% of the cap, read as 44M/day.
rm -f "$L"; marker 361; line "$(now)" review:BURST claude:opus 150000 34963 0
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "one review burst inside the cap does not stop the loop" || ko "burst: rc=$rc out=$o"
# ... but a burst that would cross the cap over the floor span still fires, and the floor is a setting
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_RATE_FLOOR_S=3600 bash "$G" 2>&1); rc=$?
[ $rc = 1 ] && ok "a burst over the cap across the floor span fires" || ko "floor fire: rc=$rc out=$o"
case "$o" in *184963*1500000*review:BURST*) ok "the rate sentence names total, cap and consumer";; *) ko "rate sentence: $o";; esac

# a cache read is counted at the cache-read price ratio (#495): a repository review re-reads its cached context every
# turn, so counted at full weight one review read as 560k and stopped the loop; at one tenth the same line is 111k.
rm -f "$L"; marker 361; line "$(now)" review:CACHE claude:opus 10000 1000 1000000
o=$(guard); rc=$?
[ $rc = 0 ] && [ -z "$o" ] && ok "a cache read counts at one tenth, so a review inside the cap does not stop the loop" || ko "cache weight: rc=$rc out=$o"
o=$(SG_STATE="$T/st" SG_TOKEN_CACHE_WEIGHT=1 bash "$G" 2>&1); rc=$?
[ $rc = 1 ] && ok "the weight is a setting: at 1 the same line fires" || ko "cache weight 1: rc=$rc out=$o"
case "$o" in *1011000*) ok "the sentence names the weighted total";; *) ko "weighted total missing: $o";; esac

# a missing ledger: exit 0
rm -f "$L"; marker 10
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

# the cap may come from the profile (env.sh) but the environment still wins
printf ': "${SG_TOKEN_BURN_CAP:=500}"\n' > "$T/s/env.sh"
rm -f "$L"; marker 10; line "$(now)" review:P claude:opus 400 200 0
o=$(guard); rc=$?
[ $rc = 1 ] && ok "the cap comes from the profile when the environment is silent" || ko "profile cap: rc=$rc out=$o"
o=$(SG_STATE="$T/st" SG_TOKEN_BURN_CAP=1000 bash "$G" 2>&1); rc=$?
[ $rc = 0 ] && ok "the environment still wins over the profile" || ko "env wins: rc=$rc out=$o"
: > "$T/s/env.sh"

rm -rf "$T"; exit $fail
