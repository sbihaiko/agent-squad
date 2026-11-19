#!/bin/bash
# seconds: 0.13
# The comment flusher waits SG_FLUSH_INTERVAL seconds between passes, empty queue or not: 120 unless the profile in force sets it (#329).
# cqflush.sh --interval prints the resolved value and exits, so no test sleeps. Offline: fixture profiles, a gh that always fails, an alarm against a loop.
# usage: bash test-cqflush-interval.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
P=$T/profiles; mkdir -p "$P" "$T/bin"; printf '#!/bin/bash\nexit 1\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
base=': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:='$T'/co}"'
echo "$base" > "$P/alpha.sh"
printf '%s\n: "${SG_FLUSH_INTERVAL:=45}"\n' "$base" > "$P/beta.sh"
export SG_PROFILES_DIR=$P SG_STATE_BASE=$T/state PATH="$T/bin:$PATH"
unset SG_PROFILE SG_FLUSH_INTERVAL
got() { (cd "$T" && env "$@" perl -e 'alarm 5; exec @ARGV' bash "$d/cqflush.sh" --interval >"$T/out" 2>&1; tail -n 1 "$T/out"); }

[ "$(got)" = 120 ] && ok "default interval is 120 s" || ko "default interval: $(got)"
[ "$(got SG_PROFILE=alpha)" = 120 ] && ok "a profile that sets nothing keeps 120 s" || ko "alpha: $(got SG_PROFILE=alpha)"
[ "$(got SG_PROFILE=beta)" = 45 ] && ok "a profile sets the interval" || ko "profile interval: $(got SG_PROFILE=beta)"
[ "$(got SG_PROFILE=alpha SG_FLUSH_INTERVAL=7)" = 7 ] && ok "the environment still wins" || ko "environment interval"
! /usr/bin/grep -q 'sleep 60\|sleep 30' "$d/cqflush.sh" && ok "no fixed 30 s or 60 s sleep is left" || ko "fixed sleep remains"
rm -rf "$T"; exit $fail
