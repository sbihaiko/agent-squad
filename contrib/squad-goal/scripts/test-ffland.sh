#!/bin/bash
# Regression test for ffland.sh's landing branch (ADR-0145, #1846). usage: bash test-ffland.sh
# Offline: SG_LAND_REMOTE names a path that holds no repository, so no case reaches a network. The report is buffered so the
# first line says TDD RED while a fix is missing, TDD GREEN once it is in.
d=$(dirname "$0"); T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
unset $(compgen -e | /usr/bin/grep '^SG_') 2>/dev/null || true
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
# 1. (#1846) A hand landing that loads no profile names no base. The script used to fall back to `main`, so the push took the
# default branch straight to the work branch's tip, one commit ahead of stg and unprovable. It must refuse instead, name the
# variable, and push nothing.
out=$(env -u SG_BASE SG_REPO=o/r SG_LAND_REMOTE=$T/none bash "$d/ffland.sh" fix/1846-probe 1846 2>"$T/err"); rc=$?
[ $rc = 1 ] && ok "1 a landing with no SG_BASE exits 1" || ko "1 a landing with no SG_BASE exited $rc"
case "$out" in FFLAND-FALLBACK*SG_BASE*) ok "1 the refusal names SG_BASE on its FFLAND-FALLBACK line";; *) ko "1 the refusal does not name SG_BASE: $out";; esac
case "$out" in *fetch*) ko "1 the refusal reached the remote before it read SG_BASE: $out";; *) ok "1 the refusal pushes nothing";; esac
# 2. A landing that names its base still runs: the guard refuses a missing variable, never a landing that carries it.
out=$(SG_BASE=dev SG_REPO=o/r SG_LAND_REMOTE=$T/none bash "$d/ffland.sh" fix/1846-probe 1846 2>/dev/null); rc=$?
[ $rc = 1 ] && case "$out" in *SG_BASE*) ko "2 a landing with SG_BASE set was refused for SG_BASE: $out";; *) ok "2 a landing with SG_BASE set passes the guard";; esac || ko "2 a landing with SG_BASE set exited $rc"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
