#!/bin/bash
# seconds: 0.24
# The process reader never answers "nothing" for a live read (#1647): a `squad.procs` that imports and prints no row is a
# stale or absent module, not an empty process table, and sg_ps falls back to `ps`. Before the fix the empty answer passed
# as success, so `is_watchd` was always false, `watchd start` refused, and the heartbeat marked live runs dead.
# Offline: a copied caps.sh under a throwaway root, a stale and an absent squad.procs, no network.
# usage: bash test-sg-ps-fallback.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
src=$(cd "$d/../../.." && pwd)   # the repository tree: its squad/procs.py is the real one
# read <root> <argv...>: source the copied caps.sh under <root> and run $SG_PS, printing its answer.
read_ps() { local root=$1; shift
  SG_SQUAD=$root SG_PS_FAST=0 bash -c '. "$1/contrib/squad-goal/scripts/caps.sh"; cd /; "$SG_PS" "${@:2}"' _ "$root" "$@" 2>/dev/null; }
# A copied skill whose ../.. is the throwaway root, so _sg_ps_root resolves there, never to the repository.
mkroot() { local root=$1; mkdir -p "$root/contrib/squad-goal/scripts"; cp "$d/caps.sh" "$root/contrib/squad-goal/scripts/caps.sh"; }
# One: a stale module. It imports and answers no row, the shape of a plugin cache older than the live copy.
stale=$T/stale; mkroot "$stale"; mkdir -p "$stale/squad"
: > "$stale/squad/__init__.py"
printf 'def _ps_args(argv):\n    return [], None\n' > "$stale/squad/procs.py"
out=$(read_ps "$stale" -p $$ -ww -o command=)
[ -n "$out" ] && ok "a module that answers no row falls back to ps: the command line is read" || ko "an empty module answer passed as a read"
case "$out" in *bash*|*caps.sh*) true;; *) false;; esac && ok "the fallback answers this shell's own command line" || ko "the fallback answered '$out'"
out=$(read_ps "$stale" -o ppid= -p $$)
[ -n "$out" ] && ok "the fast-shaped read falls back too" || ko "the ppid read answered nothing"
# Two: no module at all. The import fails with a non-zero rc; ps answers the same way.
absent=$T/absent; mkroot "$absent"
out=$(read_ps "$absent" -p $$ -ww -o command=)
[ -n "$out" ] && ok "an absent module falls back to ps" || ko "an absent module answered nothing"
# Three: a dead pid is still an empty answer, and it is not a read.
rc=0; read_ps "$stale" -p 999999 -ww -o command= >/dev/null || rc=$?
[ "$rc" -ne 0 ] && ok "a dead pid still answers no row with a non-zero exit" || ko "a dead pid answered rc 0"
# Four: the real module still answers, so the fallback never runs where the reader works.
out=$(SG_PS_FAST=0 bash -c '. "$1/contrib/squad-goal/scripts/caps.sh"; cd /; "$SG_PS" -p $$ -ww -o command=' _ "$src" 2>/dev/null)
[ -n "$out" ] && ok "the repository's own module answers" || ko "the repository's module answered nothing"
[ $fail = 0 ] && echo "PASS" || echo "TDD RED"
exit $fail
