#!/bin/bash
# seconds: 2.58
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
# 3. (#2260 AC3) A child cut on its blocker's approved tip lands on top of the blocker once the blocker landed first. The
# landing rebases the child onto origin/$SG_BASE, and the blocker's commits, already in the base, do not ride a second time.
G=/usr/bin/git; R=$T/r; $G init -q -b dev "$R"; $G -C "$R" config user.email t@t; $G -C "$R" config user.name t
echo a > "$R/a"; $G -C "$R" add a; $G -C "$R" commit -qm base
$G init -q --bare "$T/remote.git"; $G -C "$R" remote add origin "$T/remote.git"; $G -C "$R" push -q origin dev
$G -C "$R" checkout -q -b gui/1; echo b > "$R/b"; $G -C "$R" add b; $G -C "$R" commit -qm "blocker (Refs #1)"; $G -C "$R" push -q origin gui/1
$G -C "$R" checkout -q -b gui/2; echo c > "$R/c"; $G -C "$R" add c; $G -C "$R" commit -qm "child (Refs #2)"; $G -C "$R" push -q origin gui/2
out=$(SG_BASE=dev SG_REPO=o/r SG_LAND_STATUS=off SG_LAND_REMOTE="$T/remote.git" bash "$d/ffland.sh" gui/1 1 2>/dev/null)
case "$out" in FFLAND\ *) ok "3 the blocker lands in dev";; *) ko "3 the blocker did not land: $out";; esac
out=$(SG_BASE=dev SG_REPO=o/r SG_LAND_STATUS=off SG_LAND_REMOTE="$T/remote.git" bash "$d/ffland.sh" gui/2 2 2>/dev/null)
case "$out" in FFLAND\ *) ok "3 the stacked child lands after its blocker";; *) ko "3 the stacked child did not land: $out";; esac
got=$($G -C "$T/remote.git" log --format=%s dev | tr '\n' '|')
[ "$got" = "child (Refs #2)|blocker (Refs #1)|base|" ] && ok "3 the child stands once on top of the blocker's landing" || ko "3 dev history: $got"
$G -C "$T/remote.git" show dev:b >/dev/null 2>&1 && $G -C "$T/remote.git" show dev:c >/dev/null 2>&1 && ok "3 the base holds the work of both branches" || ko "3 a change of one branch is missing from dev"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
