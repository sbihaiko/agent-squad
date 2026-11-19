#!/bin/bash
# seconds: 0.08
# Guard for #1078: a paragraph break in the panel is one empty line, the line box at the panel's text size, at 10 px and at 22 px.
# usage: bash test-hub-paragraph-gap.sh [path/to/hub-overlay.js]
d=$(cd "$(dirname "$0")" && pwd); js=${1:-$d/hub-overlay.js}
src=$(cat "$js")
gap=$(printf '%s\n' "$src" | grep -o '\.sq-p{margin:0 0 [0-9.]*\(em\|px\)}' | grep -o '[0-9.]*\(em\|px\)' | head -1)
lh=$(printf '%s\n' "$src" | grep -o '#sq-ov{[^}]*font:var(--fs-body)/[0-9.]*' | grep -o '/[0-9.]*$' | tr -d /)
fail=0
if [ -z "$gap" ] || [ -z "$lh" ]; then
  echo "FAIL paragraph gap or line height not found in $js (gap='$gap' line-height='$lh')"; exit 1
fi
for px in 13 21; do
  want=$(awk -v l="$lh" -v s="$px" 'BEGIN{printf "%.2f", l*s}')
  got=$(awk -v g="$gap" -v s="$px" 'BEGIN{ if (g ~ /em$/) { sub(/em$/, "", g); printf "%.2f", g*s } else { sub(/px$/, "", g); printf "%.2f", g } }')
  if [ "$got" = "$want" ]; then
    echo "ok   paragraph gap at ${px}px is one line box (${want}px)"
  else
    echo "FAIL paragraph gap at ${px}px is ${got}px, the line box is ${want}px (gap '$gap', line-height $lh)"; fail=1
  fi
done
exit $fail
