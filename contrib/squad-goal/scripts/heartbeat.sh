#!/bin/bash
# One heartbeat pass: a test host whose CPU time has not moved for HB_FLAT_MIN minutes (default 5) is hung
# (a headless case at 0% CPU; record.jsonl cannot see it, the agent sits inside one long Bash call).
# Kills it so the agent's `dotnet test` reports a failure, and prints one line per kill. Judges only the leaf
# test host (testhost / `dotnet exec --runtimeconfig ... UI*Tests`), never the idle `dotnet test`/vstest parents.
. "$(dirname "$0")/env.sh"
flat=${HB_FLAT_MIN:-5}; st="$SG_STATE/hb"; mkdir -p "$st"; now=$(date +%s); live=" "
while read -r pid t cmd; do
  set -- $cmd; exe=${1##*/}   # judge the executable only: a shell whose command line merely mentions testhost is not one
  case "$exe" in testhost*) ;; dotnet) [ "$2" = exec ] && [ "$3" = --runtimeconfig ] && case "$cmd" in *UI*Tests*) true;; *) false;; esac || continue;; *) continue;; esac
  s=$(echo "$t" | awk '{n=split($1,a,/[-:]/); s=a[n]; if(n>1)s+=a[n-1]*60; if(n>2)s+=a[n-2]*3600; if(n>3)s+=a[n-3]*86400; printf "%d",s}')
  live="$live$pid "; f="$st/$pid"
  if [ -e "$f" ] && read -r last since < "$f" && [ "$s" -le $((last+1)) ]; then
    if [ $((now-since)) -ge $((flat*60)) ]; then
      d="${SG_HUB_DIR:-${SG_CHECKOUT:-.}/runs/.hub}"; mkdir -p "$d"; echo "$(date +%s) $pid" >> "$d/hub-kills.log"  # counted by the hub
echo "HUNG TEST pid=$pid cpu=${s}s flat for ${flat}m, killed: $(echo "$cmd" | cut -c1-110)"; kill "$pid" 2>/dev/null; rm -f "$f"
    fi
  else echo "$s $now" > "$f"; fi
done < <(/bin/ps -axo pid=,time=,command=)
for f in "$st"/*; do [ -e "$f" ] || continue; case "$live" in *" $(basename "$f") "*) ;; *) rm -f "$f";; esac; done
# runs that never reached a verdict while no squad process is alive were killed (session restart, crash)
alive=$(/bin/ps -axo command= | grep -c "[s]quad start --target")
open=0; for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do case "$r" in /*) rf=$r;; *) rf=runs/$r;; esac; grep -q '"event": *"verdict"' "$rf/record.jsonl" 2>/dev/null || open=$((open+1)); done
[ "$alive" -eq 0 ] && [ "$open" -gt 0 ] && { [ -e "$st/.dead" ] || { echo "DEAD RUN: $open run(s) without a verdict and no live squad process"; touch "$st/.dead"; }; } || rm -f "$st/.dead"
exit 0
