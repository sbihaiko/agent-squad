#!/bin/bash
# One heartbeat pass: a test host whose CPU time has not moved for HB_FLAT_MIN minutes (default 5) is hung
# (a headless case at 0% CPU; record.jsonl cannot see it, the agent sits inside one long Bash call).
# Kills it so the agent's `dotnet test` reports a failure, and prints one line per kill. Judges only the leaf
# test host (testhost / `dotnet exec --runtimeconfig ... UI*Tests`), never the idle `dotnet test`/vstest parents.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"
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
done < <($SG_PS -axwwo pid=,time=,command=)
for f in "$st"/*; do [ -e "$f" ] || continue; case "$live" in *" $(basename "$f") "*) ;; *) rm -f "$f";; esac; done
# runs that never reached a verdict while no squad process is alive were killed (session restart, crash): each gets a killed verdict
# in its own record (squad.killed, the one writer), so the hub shows it as ended and the footer stops counting it live.
# A record that moved in the last HB_DEAD_IDLE_MIN minutes (default 1) is a launch window, not a death. SG_RUN_CMD_RE is the squad process pattern.
table=$($SG_PS -axwwo command=) || { echo "HEARTBEAT: the process read failed; no run is marked dead"; exit 0; }   # an unknown table is never an empty one (#1508)
alive=$(printf '%s\n' "$table" | grep -cE "${SG_RUN_CMD_RE:-[s]quad start( |\$)}")
open=0; marked=0; for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do case "$r" in /*) rf=$r;; *) rf=${SG_CHECKOUT:-.}/runs/$r;; esac
  grep -q '"event": *"verdict"' "$rf/record.jsonl" 2>/dev/null && continue
  open=$((open+1))
  [ "$alive" -eq 0 ] && [ -n "$(find "$rf/record.jsonl" -mmin +"${HB_DEAD_IDLE_MIN:-1}" 2>/dev/null)" ] && PYTHONPATH="${SG_SQUAD:-.}${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.killed "$rf" >/dev/null 2>&1 && marked=$((marked+1))
done
[ "$alive" -eq 0 ] && [ "$open" -gt 0 ] && { [ -e "$st/.dead" ] || { echo "DEAD RUN: $open run(s) without a verdict and no live squad process; $marked marked killed in their record"; touch "$st/.dead"; }; } || rm -f "$st/.dead"
exit 0
