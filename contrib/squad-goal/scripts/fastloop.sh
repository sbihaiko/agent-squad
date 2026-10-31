#!/bin/bash
# The fast lane of the watch daemon: every SG_FAST_INTERVAL seconds (120 by default, a profile setting) childcc (a passed child moves its card) and the two
# dispatch passes, autofrontier and autobug, so a dispatchable issue is picked up within one beat instead of on the slow cycle's beat (~6 min late,
# what frontier-idle kept reporting, #1845).
# Why a lane of its own: watch.sh runs them after forkclose, whose fork suite can take minutes behind the GUI suite lock; a dispatch then waits for the whole cycle.
# Each step runs under single.sh, so it never overlaps its own previous run. Pid in $SG_STATE/fastloop.pid.
# childcc runs in the background too (#590): one pass reviews and gates a child for 2 to 15 min, and in the foreground it held
# every dispatch and the pid refresh of the beat behind it. It stays in the supervisor's process group, so a watchd stop ends it.
# The wait between beats ends early on a new Todo card in the local board cache (#569): the owner's go signal reaches a dispatch
# within SG_FAST_POLL seconds (2 by default), not at the end of the beat. It reads that file only, never GitHub.
# The cache is the hub's one board (SG_PROJECT): the bug project's own Todo column (SG_BUG_*) is not in it, so a bug filed there waits for the beat.
# --interval prints the resolved beat and exits (tests). SG_FASTLOOP_ONCE=1 runs one iteration and exits (tests).
. "$(dirname "$0")/env.sh"; [ "${1:-}" = --interval ] && { echo "$SG_FAST_INTERVAL"; exit 0; }; here="$(dirname "$0")"; P="$SG_STATE/fastloop.pid"
# todos: the numbers of Todo cards in the fresh hub board cache; nothing when it is missing, stale or unreadable.
todos() { python3 "${here:-$(dirname "$0")}/issue_state.py" --hub-todos 2>/dev/null; }
# A lane removes the pid file only when the file names it: the lane of a later start may own it by then (#527).
mine() { [ "$(cat "$P" 2>/dev/null)" = "$$" ] && rm -f "$P"; }
# load_sample: one line per beat in $SG_STATE/load.log, the UTC time then the 1, 5 and 15 minute load averages; nothing when unreadable.
load_sample() {
  local a b c l; l=$(sysctl -n vm.loadavg 2>/dev/null | tr -d '{}'); [ -n "$l" ] || l=$(cat /proc/loadavg 2>/dev/null)
  read -r a b c _ <<<"$l"; [ -n "$a" ] && printf '%s LOAD %s %s %s\n' "$(date -u '+%F %T')" "$a" "$b" "$c" >> "$SG_STATE/load.log"
}
[ -n "${SG_FASTLOOP_ONCE:-}" ] || { echo $$ > "$P"; trap 'mine; exit 0' TERM INT; }; cc=
while :; do
  t0=$(todos)   # the cards in Todo at the start of the beat: a card that arrives while the passes run still ends the wait at once
  # A lane whose supervisor died exits: else it runs beside the lane of the next watchd start, two of each pass a beat (#527).
  [ -n "${SG_FASTLOOP_ONCE:-}" ] || { [ "$PPID" != 1 ] && [ "$(ps -o ppid= -p $$ | tr -d ' ')" = "$PPID" ]; } || { mine; exit 0; }
  [ -n "${SG_FASTLOOP_ONCE:-}" ] || echo $$ > "$P"   # every beat: watch.sh reads this file to know the lane is up, and a missing one sent the cycle to run childcc inline (#513)
  load_sample
  # $cc is the pass this lane started: a later beat starts none while it lives, and the childcc lock still bars every other pass.
  { [ -n "$cc" ] && kill -0 "$cc" 2>/dev/null; } || { bash "$here/single.sh" childcc python3 "$here/childcc.py" 2>&1 & cc=$!; }
  # Both go out detached: a pass ends in a squad start that runs for minutes, and it must not hold the beat. They are called bare, as watch.sh calls them,
  # because each takes its own single.sh <stage> lock inside — so a pass already running is skipped here, and never doubled against the cycle's standalone path.
  # The bug pass goes first (#616): it writes the bugs it left waiting, and the frontier pass after it reads that count.
  ( { "$here/autobug.sh" 2>&1; "$here/autofrontier.sh" 2>&1; } & ) 2>/dev/null
  [ -n "${SG_FASTLOOP_ONCE:-}" ] && { wait "$cc"; exit 0; }   # a test's one iteration ends with its pass
  # The wait is SG_FAST_INTERVAL seconds at most (a fraction is fine), polled; a card in Todo that the beat's start did not see ends it.
  # It counts polls, never SECONDS: an integer test on a fractional beat errored and the lane spun with no pause. The remainder is one last short sleep.
  p=${SG_FAST_POLL:-2}; num='^[0-9]+([.][0-9]+)?$'; [[ $p =~ $num ]] && [ "$p" != 0 ] || p=2; iv=$SG_FAST_INTERVAL; [[ $iv =~ $num ]] || iv=120
  n=$(awk -v i="$iv" -v p="$p" 'BEGIN{print int(i/p)}'); r=$(awk -v i="$iv" -v p="$p" -v n="$n" 'BEGIN{print i-n*p}')
  for ((k = 0; k < n; k++)); do sleep "$p"; [ -z "$(comm -13 <(echo "$t0") <(todos))" ] || continue 2; done
  [ "$r" = 0 ] || sleep "$r"; done
