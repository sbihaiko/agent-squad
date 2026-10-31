#!/bin/bash
# token-guard.sh -- the token guard: a burn over the cap is a report and one alert, never a stop. Dispatch pauses only on a
# sustained runaway, and resumes by itself. Called by watch.sh at the top of the cycle; also runnable by hand.
#
# The burn is read through tokens.py (the ledger $SG_STATE/token-usage.tsv and the runs' `outcome` events), and only
# lines at or after this guard's own start marker $SG_STATE/token-guard.since count. The marker is written when it is
# absent, so a relight starts a fresh measurement and old lines never count.
#
# Caps (the environment wins, else the profile, else the default):
#   SG_TOKEN_GUARD             on or off, default on. The owner sets it in the hub's Spend & limits theme (#926), which
#                              writes the profile below. Off: this pass writes no report, no alert and no pause, and a pause
#                              that stands is cleared, so dispatch resumes. With it on, nothing else changes.
#   SG_TOKEN_BURN_CAP          tokens per window, default 1500000
#   SG_TOKEN_BURN_WINDOW_H     the window in hours, default 24 (the lookback is bounded by it)
#   SG_TOKEN_BURN_MIN_SECONDS  the rate report waits this many seconds, default 300
#   SG_TOKEN_CACHE_WEIGHT      what one cache-read token counts as, default 0: the cap measures new input and output only (owner)
#                              (#495: a review re-reads its cached context each turn; at full weight one review read as 560k)
#   SG_TOKEN_BURN_RATE_FLOOR_S the span the rate is read over is never shorter than this, default a quarter of the window
#                              (#493: one review is a 185k burst in minutes; over 361 s it read as 44M/day, 12% of the cap)
#   SG_TOKEN_PAUSE_FACTOR      the hourly burn that pauses dispatch, as a multiple of the cap's hourly share, default 3
#   SG_TOKEN_NOTIFY            the notifier of an alert, default /usr/bin/osascript
#
# Two states, each a marker in $SG_STATE. Each start of a state writes one sentence on stdout and one line in alerts.log.
#   token-over   the total since the marker, or its rate over the floor span, is over the cap. A report: the loop goes on.
#                It clears, and the alert re-arms, on the first pass under the cap.
#   token-pause  the burn of the last hour, after one full hour of measurement, is over SG_TOKEN_PAUSE_FACTOR times the
#                cap's hourly share (cap * 3600 / window). autofrontier.sh and autobug.sh start nothing while it is there.
#                It clears on the first pass whose hourly burn is under the cap's hourly share: dispatch resumes by itself.
# exit 1 while dispatch is paused, else 2 while over the cap, else 0. A missing state dir, a missing ledger or a bad line
# is exit 0: the guard never crashes, and a guard that fails pauses nothing.
. "$(dirname "$0")/env.sh" 2>/dev/null || true
: "${SG_TOKEN_BURN_CAP:=1500000}"
: "${SG_TOKEN_BURN_WINDOW_H:=24}"
: "${SG_TOKEN_BURN_MIN_SECONDS:=300}"
: "${SG_TOKEN_CACHE_WEIGHT:=0}"
: "${SG_TOKEN_BURN_RATE_FLOOR_S:=$(( SG_TOKEN_BURN_WINDOW_H * 900 ))}"
: "${SG_TOKEN_PAUSE_FACTOR:=3}"
: "${SG_TOKEN_NOTIFY:=/usr/bin/osascript}"
: "${SG_TOKEN_GUARD:=on}"

here="$(cd "$(dirname "$0")" && pwd)"
state="${SG_STATE:-}"
[ -n "$state" ] && [ -d "$state" ] || exit 0

# The off switch (#926): the owner turns the guard off in the hub, which writes the profile read just above; env.sh sources
# that profile on every pass, so the next pass reads it. Off is a silence, not a stop: the guard writes no report, no alert
# and no pause, and it clears a pause that stands, so dispatch resumes. Exit 0, as a guard that measures nothing. Any other
# value, and an empty one, keeps the guard on.
case "$SG_TOKEN_GUARD" in
  off|0|no|false) rm -f "$state/token-over" "$state/token-pause" 2>/dev/null; exit 0;;
esac

now=$(date +%s)
marker_file="$state/token-guard.since"
if [ ! -f "$marker_file" ]; then printf '%s\n' "$now" > "$marker_file" 2>/dev/null || true; exit 0; fi
marker=$(tr -cd '0-9' < "$marker_file" 2>/dev/null)
if [ -z "$marker" ]; then printf '%s\n' "$now" > "$marker_file" 2>/dev/null || true; exit 0; fi
elapsed=$(( now - marker ))
if [ "$elapsed" -lt 0 ]; then printf '%s\n' "$now" > "$marker_file" 2>/dev/null || true; exit 0; fi
over_file="$state/token-over"; pause_file="$state/token-pause"

# alert <sentence>: one line in alerts.log and one notification
alert() { echo "$(date '+%F %T') $1" >> "$state/alerts.log" 2>/dev/null; "$SG_TOKEN_NOTIFY" -e "display notification \"${1//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1 || true; }
# burn <seconds>: "<total><TAB><top consumer>" over the last <seconds>, plus one second so a line written at the marker counts
burn() {
  local h json; h=$(awk -v s="$1" 'BEGIN{printf "%.6f", (s+1)/3600}')
  if [ -n "${SG_CHECKOUT:-}" ] && [ -d "$SG_CHECKOUT" ]; then
    json=$(python3 -I "$here/tokens.py" burners "$state" --checkout "$SG_CHECKOUT" --hours "$h" --cache-weight "$SG_TOKEN_CACHE_WEIGHT" --json 2>/dev/null) || return 1
  else
    json=$(python3 -I "$here/tokens.py" burners "$state" --hours "$h" --cache-weight "$SG_TOKEN_CACHE_WEIGHT" --json 2>/dev/null) || return 1
  fi
  [ -n "$json" ] || return 1
  printf '%s' "$json" | jq -r '[.total // 0, (.burners[0].consumer // "-")] | @tsv' 2>/dev/null
}
# split <burn>: sets total and top
split() { total=${1%%$'\t'*}; top=${1#*$'\t'}; case "$total" in ''|*[!0-9]*) total=0;; esac; [ -n "$top" ] || top=-; }

# The window: the span since the marker, bounded by SG_TOKEN_BURN_WINDOW_H. No line before the marker is ever read.
window_s=$(( SG_TOKEN_BURN_WINDOW_H * 3600 ))
span=$elapsed
[ "$window_s" -gt 0 ] && [ "$elapsed" -gt "$window_s" ] && span=$window_s
tt=$(burn "$span") || exit 0

# The pause: the burn of the last hour, read only after one full hour of measurement, so a short burst never pauses.
rc=0
if [ "$window_s" -gt 0 ] && [ "$elapsed" -ge 3600 ] && ht=$(burn 3600); then
  split "$ht"; hour=$total; htop=$top
  share=$(( SG_TOKEN_BURN_CAP * 3600 / window_s ))
  line=$(awk -v s="$share" -v f="$SG_TOKEN_PAUSE_FACTOR" 'BEGIN{printf "%d", s*f}')
  if [ -e "$pause_file" ]; then
    if [ "$hour" -lt "$share" ]; then
      rm -f "$pause_file"
      s="Token burn $hour tokens in the last hour is under the cap's hourly share $share, so dispatch resumes."
      printf '%s\n' "$s"; alert "$s"
    else rc=1; fi
  elif [ "$hour" -gt "$line" ]; then
    s="Token burn $hour tokens in the last hour is over ${SG_TOKEN_PAUSE_FACTOR}x the cap's hourly share, $line (top consumer $htop), so dispatch pauses."
    printf '%s\n' "$s" > "$pause_file" 2>/dev/null; printf '%s\n' "$s"; alert "$s"; rc=1
  fi
fi

# The report: over the cap outright, or (once enough time has passed) burning at an over-cap rate. The loop goes on.
split "$tt"; s=
if [ "$total" -gt "$SG_TOKEN_BURN_CAP" ]; then
  s="Token burn $total tokens over the cap $SG_TOKEN_BURN_CAP since the marker (top consumer $top): a report, the loop goes on."
elif [ "$elapsed" -ge "$SG_TOKEN_BURN_MIN_SECONDS" ]; then
  span_r=$elapsed; [ "$SG_TOKEN_BURN_RATE_FLOOR_S" -gt "$elapsed" ] 2>/dev/null && span_r=$SG_TOKEN_BURN_RATE_FLOOR_S
  if [ "$(awk -v t="$total" -v e="$span_r" -v c="$SG_TOKEN_BURN_CAP" 'BEGIN{print (e>0 && t/e*86400 > c) ? 1 : 0}')" = 1 ]; then
    rate=$(awk -v t="$total" -v e="$span_r" 'BEGIN{printf "%d", t/e*86400}')
    s="Token burn $total tokens in ${elapsed}s is a rate of $rate/day over the cap $SG_TOKEN_BURN_CAP (top consumer $top): a report, the loop goes on."
  fi
fi
if [ -n "$s" ]; then
  [ -e "$over_file" ] || { printf '%s\n' "$s" > "$over_file" 2>/dev/null; printf '%s\n' "$s"; alert "$s"; }
  [ "$rc" = 1 ] || rc=2
else
  rm -f "$over_file"
fi
exit $rc
