#!/bin/bash
# token-guard.sh -- the token tripwire: stop the loop on the FIRST pass whose burn crosses the cap, no second attempt.
# Called by watch.sh at the top of the cycle; also runnable by hand.
#
# The burn is read through tokens.py (the ledger $SG_STATE/token-usage.tsv and the runs' `outcome` events), and only
# lines at or after this guard's own start marker $SG_STATE/token-guard.since count. The marker is written when it is
# absent, so a relight starts a fresh measurement and old lines never stop the loop.
#
# Caps (the environment wins, else the profile, else the default):
#   SG_TOKEN_BURN_CAP          tokens per window, default 1500000
#   SG_TOKEN_BURN_WINDOW_H     the window in hours, default 24 (the lookback is bounded by it)
#   SG_TOKEN_BURN_MIN_SECONDS  the rate tripwire waits this many seconds, default 300
#   SG_TOKEN_CACHE_WEIGHT      what one cache-read token counts as, default 0.1, the provider's cache-read price ratio
#                              (#495: a review re-reads its cached context each turn; at full weight one review read as 560k)
#   SG_TOKEN_BURN_RATE_FLOOR_S the span the rate is read over is never shorter than this, default a quarter of the window
#                              (#493: one review is a 185k burst in minutes; over 361 s it read as 44M/day, 12% of the cap)
#
# exit 1 and one sentence on stdout when either holds -- the total since the marker is over the cap, or the rate
# (total / max(elapsed_seconds, floor) * 86400) is over the cap once SG_TOKEN_BURN_MIN_SECONDS have passed. exit 0 with no output
# when the burn is inside the cap, and on a missing state dir, a missing ledger or a bad line: the guard never crashes.
. "$(dirname "$0")/env.sh" 2>/dev/null || true
: "${SG_TOKEN_BURN_CAP:=1500000}"
: "${SG_TOKEN_BURN_WINDOW_H:=24}"
: "${SG_TOKEN_BURN_MIN_SECONDS:=300}"
: "${SG_TOKEN_CACHE_WEIGHT:=0.1}"
: "${SG_TOKEN_BURN_RATE_FLOOR_S:=$(( SG_TOKEN_BURN_WINDOW_H * 900 ))}"

here="$(cd "$(dirname "$0")" && pwd)"
state="${SG_STATE:-}"
[ -n "$state" ] && [ -d "$state" ] || exit 0

now=$(date +%s)
marker_file="$state/token-guard.since"
if [ ! -f "$marker_file" ]; then printf '%s\n' "$now" > "$marker_file" 2>/dev/null || true; exit 0; fi
marker=$(tr -cd '0-9' < "$marker_file" 2>/dev/null)
if [ -z "$marker" ]; then printf '%s\n' "$now" > "$marker_file" 2>/dev/null || true; exit 0; fi
elapsed=$(( now - marker ))
if [ "$elapsed" -lt 0 ]; then printf '%s\n' "$now" > "$marker_file" 2>/dev/null || true; exit 0; fi

# Encode the marker into the burners window: the span since the marker, bounded by SG_TOKEN_BURN_WINDOW_H, plus one
# second so a line written at the marker itself still counts. No earlier line is ever read.
window_s=$(( SG_TOKEN_BURN_WINDOW_H * 3600 ))
span=$elapsed
[ "$window_s" -gt 0 ] && [ "$elapsed" -gt "$window_s" ] && span=$window_s
hours=$(awk -v s="$span" 'BEGIN{printf "%.6f", (s+1)/3600}')

if [ -n "${SG_CHECKOUT:-}" ] && [ -d "$SG_CHECKOUT" ]; then
  json=$(python3 -I "$here/tokens.py" burners "$state" --checkout "$SG_CHECKOUT" --hours "$hours" --cache-weight "$SG_TOKEN_CACHE_WEIGHT" --json 2>/dev/null) || exit 0
else
  json=$(python3 -I "$here/tokens.py" burners "$state" --hours "$hours" --cache-weight "$SG_TOKEN_CACHE_WEIGHT" --json 2>/dev/null) || exit 0
fi
[ -n "$json" ] || exit 0
tot_top=$(printf '%s' "$json" | jq -r '[.total // 0, (.burners[0].consumer // "-")] | @tsv' 2>/dev/null) || exit 0
total=${tot_top%%$'\t'*}
top=${tot_top#*$'\t'}
case "$total" in ''|*[!0-9]*) total=0;; esac
[ -n "$top" ] || top=-

# Over the cap outright, or (once enough time has passed) burning at an over-cap rate.
if [ "$total" -gt "$SG_TOKEN_BURN_CAP" ]; then
  printf 'Token burn %s tokens over the cap %s since the marker (top consumer %s), so the loop stops.\n' "$total" "$SG_TOKEN_BURN_CAP" "$top"
  exit 1
fi
if [ "$elapsed" -ge "$SG_TOKEN_BURN_MIN_SECONDS" ]; then
  span_r=$elapsed; [ "$SG_TOKEN_BURN_RATE_FLOOR_S" -gt "$elapsed" ] 2>/dev/null && span_r=$SG_TOKEN_BURN_RATE_FLOOR_S
  if [ "$(awk -v t="$total" -v e="$span_r" -v c="$SG_TOKEN_BURN_CAP" 'BEGIN{print (e>0 && t/e*86400 > c) ? 1 : 0}')" = 1 ]; then
    rate=$(awk -v t="$total" -v e="$span_r" 'BEGIN{printf "%d", t/e*86400}')
    printf 'Token burn %s tokens in %ss is a rate of %s/day over the cap %s (top consumer %s), so the loop stops.\n' "$total" "$elapsed" "$rate" "$SG_TOKEN_BURN_CAP" "$top"
    exit 1
  fi
fi
exit 0
