#!/bin/bash
# Regression test for the portable lsof command contract. usage: bash test-lsof.sh [scripts-directory]
scripts=${1:-$(dirname "$0")}; fail=0
ok() { printf 'ok   %s\n' "$1"; }
ko() { printf 'FAIL %s\n' "$1"; fail=1; }

for source in janitor.py hub.py; do
  if grep -qE 'sh\("/usr/(sbin|bin)/lsof' "$scripts/$source"; then
    ko "$source invokes lsof through a platform path"
  else
    ok "$source has no platform-specific lsof invocation"
  fi
done

for source in wtclean.sh health.sh; do
  if grep -qE '\$\(/usr/(sbin|bin)/lsof' "$scripts/$source"; then
    ko "$source invokes lsof through a platform path"
  else
    ok "$source has no platform-specific lsof invocation"
  fi
done

if grep -q 'SG_LSOF' "$scripts/env.sh"; then
  ok "env.sh defines the shared lsof command"
else
  ko "env.sh does not define the shared lsof command"
fi

exit "$fail"
