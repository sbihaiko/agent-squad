#!/bin/bash
# Guard for #923: `watchd.sh drain` pauses dispatch and stops the loop only once no live run and no held gate remain.
# A live run is an active-runs line whose record.jsonl has no verdict; a held gate is a locks/gate/pid naming a live pid.
# Offline: a temp state dir, stub cycle and stub lanes that stay up until the loop stops. No network.
# usage: bash test-watchd-drain.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; gp=; sup=
trap 'kill -9 "$sup" "$gp" 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME" "$T/runs/r1" "$SG_STATE/locks/gate"
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/fastloop.sh"','"%s/fastloop.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/cqflush.sh"','"%s/cqflush.sh"'%f.rsplit("/",1)[0])
p.write_text(t)
PY
printf '#!/bin/bash\nsleep 300\n' > "$T/watch.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/fastloop.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh" "$T/watchd.sh"
alive_sup() { [ -n "$sup" ] && kill -0 "$sup" 2>/dev/null; }
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && ok "the loop is up for the drain check" || { ko "the loop did not start"; exit 1; }
printf '{"event": "start"}\n' > "$T/runs/r1/record.jsonl"; echo "$T/runs/r1" > "$SG_STATE/active-runs.txt"
sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"
out=$(SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain 2>&1)
case "$out" in *draining*) ok "drain says it is draining";; *) ko "drain did not say draining: $out";; esac
[ -e "$SG_STATE/auto/off" ] && [ -e "$SG_STATE/auto/drain" ] && ok "drain pauses dispatch and marks the drain" || ko "drain left no pause and no drain marker"
sleep 4
alive_sup && ok "a live run and a held gate keep the loop up while draining" || ko "drain stopped the loop with work still live"
printf '{"event": "verdict"}\n' >> "$T/runs/r1/record.jsonl"; sleep 4
alive_sup && ok "the loop stays up while the gate is still held" || ko "drain stopped the loop when only the gate was held"
kill "$gp" 2>/dev/null; gp=
for _ in $(seq 1 20); do ! alive_sup && [ ! -e "$SG_STATE/auto/drain" ] && break; sleep 1; done
if alive_sup; then ko "drain did not stop the loop once the run and the gate were done"; else ok "drain stops the loop once the run and the gate are done"; fi
[ ! -e "$SG_STATE/auto/drain" ] && ok "the drain marker is gone after the stop" || ko "the drain marker outlived the stop"
[ "$(bash "$T/watchd.sh" drain 2>&1)" = "watchd not running" ] && ok "drain on a stopped loop says so" || ko "drain on a stopped loop did not say not running"
# A drain's pause is the drain's own: a start from the terminal after a hub drain dispatches again, so the strip never reads
# "on" while nothing dispatches. A pause the owner set before the drain is the owner's, and a start keeps it.
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && [ ! -e "$SG_STATE/auto/off" ] && ok "a terminal start after a drain lifts the drain's pause" || ko "a terminal start after a drain left auto/off: the loop reads on and dispatches nothing"
SG_DRAIN_POLL=1 bash "$T/watchd.sh" stop >/dev/null
: > "$SG_STATE/auto/off"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null
for _ in $(seq 1 20); do ! alive_sup && break; sleep 1; done
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
[ -e "$SG_STATE/auto/off" ] && ok "a pause the owner set before the drain survives the next start" || ko "a start removed the owner's own pause"
bash "$T/watchd.sh" stop >/dev/null
[ "$fail" = 0 ] && echo "watchd drain: all ok" || echo "watchd drain: FAILED"
exit "$fail"
