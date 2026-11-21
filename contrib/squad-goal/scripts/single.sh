#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: single.sh <name> <command...>   Runs the command unless a previous run under the same name is still alive; then it exits 76 at once.
# 76 is the one "the lock is held" code, separate from the command's own exit (#1290): a caller tells a wait from a failure by the
# code alone, and reads the holder's pid and the lock's instant from $L/pid to say which fact it waits on.
# Why: a slow step (forkclose waits for the GUI suite lock) must not hold the rest of the watch cycle, and a step started in the background
# must not overlap its own previous run. Lock = directory with the holder's pid, stale when the pid is gone (as serial.sh, which waits instead).
# The state directory the locks live in: the caller's SG_STATE, else the seam's own base (address.sh) -- a path spelled here
# would be a second dialect of the state rule the seam owns (test_squad_goal_state_split).
if [ -z "${SG_STATE:-}" ]; then . "$(dirname "$0")/address.sh"; SG_STATE=$(sg_state_base); fi
. "$(dirname "$0")/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"   # SG_PS (#1508)
st=$SG_STATE; n=$1; shift; L="$st/locks/single-$n"; mkdir -p "$st/locks"
# A landing does not start under a drain that committed its stop (#1711): the drain's last busy read would miss it, and the
# stop takes it with the group. 76 is the wait code watch.sh already draws for a held landing lock.
case $n in wland) drain_stopping && { echo "single: no landing starts while a drain stops the loop (#1711)" >&2; exit 76; };; esac
# SG_SINGLE_WAIT=<polls>: a caller that must not skip its step in silence waits up to that many one-second polls for the holder
# (#471), and still exits 76 when the lock stays held; the caller reads the holder from $L/pid to say why.
w=${SG_SINGLE_WAIT:-0}
HELD=76   # the one "the lock is held" code (#1290): never a command's own exit
# A zombie holder is dead: its parent has not reaped it, and kill -0 still answers (#1057).
alive() { kill -0 "$1" 2>/dev/null && [ "$($SG_PS -o stat= -p "$1" 2>/dev/null | cut -c1)" != Z ]; }
# (#2607) A name takes SG_SINGLE_SLOTS slots, one by default: slot one keeps the lock's own name and a later slot carries its
# number. A landing passes two (SG_LAND_PARALLEL, wave.sh): two waves rebase, check and push at once, and git refuses the push
# that is not a fast-forward, so the base's move is the race git settles.
slots=${SG_SINGLE_SLOTS:-1}; case $slots in ''|*[!0-9]*|0) slots=1;; esac
slotof() { SL="$st/locks/single-$n"; [ "$1" = 1 ] || SL="$SL-$1"; }
freeslot() { local i; for ((i=1;i<=slots;i++)); do slotof "$i"; [ -d "$SL" ] || return 0; done; return 1; }
while [ "$w" -gt 0 ] && ! freeslot; do sleep 1; w=$((w-1)); done
taken=""
for ((i=1;i<=slots;i++)); do
  slotof "$i"
  if mkdir "$SL" 2>/dev/null; then taken=$i; break; fi
  h=$(cat "$SL/pid" 2>/dev/null)
  if [ -n "$h" ] && ! alive "$h"; then echo "single: reaping stale lock $n: holder pid $h is dead, lock age $(( $(date +%s) - $(mtime "$SL/pid") ))s" >&2; rm -rf "$SL"; mkdir "$SL" 2>/dev/null && { taken=$i; break; }   # stale: take over
  elif [ -z "$h" ] && [ $(( $(date +%s) - $(mtime "$SL") )) -gt 10 ]; then rm -rf "$SL"; mkdir "$SL" 2>/dev/null && { taken=$i; break; }   # no pid after 10 s: owner died between mkdir and write
  fi
done
[ -n "$taken" ] || exit $HELD
L=$SL
# The release is the holder's only: a lock taken over meanwhile names another pid, and removing it would start a pass beside that holder (#531).
echo $$ > "$L/pid"; trap '[ "$(cat "$L/pid" 2>/dev/null)" = "$$" ] && rm -rf "$L"' EXIT INT TERM
"$@"
