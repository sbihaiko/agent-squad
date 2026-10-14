#!/bin/bash
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
rm -rf "$T"; exit $fail
