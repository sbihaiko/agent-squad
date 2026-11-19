#!/bin/bash
# seconds: 0.97
# A childcc pass never holds the fast lane's beat (#590): one pass reviews and gates a child for 2 to 15 min, and in the
# foreground it held every dispatch and the pid refresh behind it. The pass runs in the background; a later beat starts no
# second pass while it lives.
# Offline: a stub childcc.py that blocks, the real single.sh, stub dispatch passes, a 0.2 s beat, and an alarm so a hung
# loop cannot run on.
# usage: bash test-fastloop-childcc.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S" "$T/st"; cp "$d/fastloop.sh" "$d/single.sh" "$S/"
printf 'export SG_STATE=%s; SG_FAST_INTERVAL=0.2\n' "$T/st" > "$S/env.sh"
for f in autofrontier autobug; do printf '#!/bin/bash\necho %s >> "%s/beats"\n' "$f" "$T" > "$S/$f.sh"; chmod +x "$S/$f.sh"; done
# the blocked pass: it logs its start and its pid, then waits 30 s, as a review that hangs does
printf 'import os,time\nopen("%s/cc","a").write("%%d\\n" %% os.getpid())\ntime.sleep(30)\n' "$T" > "$S/childcc.py"
unset "${!SG_@}"; : > "$T/beats"; : > "$T/cc"
perl -e 'alarm 20; exec @ARGV' bash "$S/fastloop.sh" >/dev/null 2>&1 & lp=$!
P=$T/st/fastloop.pid
for _ in $(seq 300); do [ "$(/usr/bin/grep -c autofrontier "$T/beats")" -ge 3 ] && break; sleep 0.01; done
[ "$(/usr/bin/grep -c autofrontier "$T/beats")" -ge 3 ] && ok "a blocked childcc does not block the dispatch passes" \
  || ko "dispatch beats while childcc blocks: $(/usr/bin/grep -c autofrontier "$T/beats")"
rm -f "$P"
for _ in $(seq 200); do [ -s "$P" ] && break; sleep 0.01; done
[ -s "$P" ] && ok "a blocked childcc does not block the pid refresh" || ko "the pid file stayed gone while childcc blocks"
[ "$(wc -l < "$T/cc" | tr -d ' ')" = 1 ] && ok "a later beat starts no second childcc" || ko "childcc passes started: $(wc -l < "$T/cc" | tr -d ' ')"
kill "$lp" 2>/dev/null; wait "$lp" 2>/dev/null
for p in $(cat "$T/cc"); do kill "$p" 2>/dev/null; done
rm -rf "$T"; exit $fail
