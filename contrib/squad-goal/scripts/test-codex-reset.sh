#!/bin/bash
# seconds: 0.15
# Regression tests for codex-reset.py --sync (#1525), no network. usage: bash test-codex-reset.sh [path/to/codex-reset.py]
# 1) a marker the cache has outlived is deleted, so a codex model is tried again once the window resets.
# 2) a full window writes its reset into the marker; 3) a marker younger than the cache reading is kept, so --hit still holds.
src=${1:-$(dirname "$0")/codex-reset.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/hub"
export SG_HUB_DIR="$T/hub"; M="$T/codex-limited"
free() { printf '{"gpt":{"asof":1,"plan":"plus","windows":[{"label":"weekly","pct":0,"resets":%s}]}}' "$(( $(date +%s) + 86400 ))" > "$T/hub/hub-quota.json"; touch -t "$(date -v-60S +%Y%m%d%H%M)" "$T/hub/hub-quota.json"; }
# resets=$resets is the one value this suite writes into the cache and then asserts on: recomputing now+86400 at the
# assertion instead makes the check a race against the wall clock, red whenever a second ticks in between (#1792).
full() { resets=$(( $(date +%s) + 86400 )); printf '{"gpt":{"asof":1,"plan":"plus","windows":[{"label":"weekly","pct":100,"resets":%s}]}}' "$resets" > "$T/hub/hub-quota.json"; touch -t "$(date -v-60S +%Y%m%d%H%M)" "$T/hub/hub-quota.json"; }

free; echo "$(( $(date +%s) + 999999 ))" > "$M"; touch -t 202001010000 "$M"
python3 "$src" --sync "$M"; [ -e "$M" ] && ko "a marker the cache has outlived is dropped" || ok "a marker the cache has outlived is dropped"

full; python3 "$src" --sync "$M"
# the marker must hold the reset the cache carries, which is $resets -- the value full() just wrote, not a fresh now+86400.
[ -e "$M" ] && [ "$(cat "$M")" = "$resets" ] && ok "a full window writes its reset into the marker" || ko "writes the reset: marker=$(cat "$M" 2>/dev/null) cache=$resets"

free; echo "$(( $(date +%s) + 3600 ))" > "$M"; touch "$M"
python3 "$src" --sync "$M"; [ -e "$M" ] && ok "a marker younger than the cache reading is kept (--hit still holds)" || ko "a --hit marker is kept"

free; [ -n "$(python3 "$src" --hit)" ] && ok "--hit still answers now+1h on a free cache" || ko "--hit fallback"
rm -rf "$T"; exit $fail
