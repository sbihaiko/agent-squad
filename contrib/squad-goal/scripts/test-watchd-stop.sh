#!/bin/bash
# seconds: 6.70
# Guard for #1662: `watchd.sh stop` drains by default. Under a live run it marks the drain and hands off to the detached
# waiter, so the run finishes; with no live work it stops at once. `stop --force` is the hard stop: it does not wait for the
# live run, and it snapshots every live run's trees (refs/wip) before it kills. A drained stop writes no WIP line.
# Offline: a temp state dir, stub lanes that stay up until the loop stops, fixture git trees. No network.
# usage: bash test-watchd-stop.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; sup=; gp=; gp2=
trap 'bash "$T/watchd.sh" halt >/dev/null 2>&1; kill -9 "$sup" "$gp" "$gp2" 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME" "$T/co/runs/r1" "$T/co/runs/r2" "$SG_STATE/locks/gate"
. "$d/caps.sh"   # SG_PS: the process table read through squad.procs, as the shipped script reads it (#1508)
cp "$d/watchd.sh" "$T/watchd.sh"; [ -f "$d/wip_snapshot.py" ] && cp "$d/wip_snapshot.py" "$T/wip_snapshot.py"
python3 - "$T/watchd.sh" "$SG_STATE" "$d" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
t=t.replace('"$(dirname "$0")/caps.sh"','"%s/caps.sh"'%sys.argv[3])
t=t.replace('. "$(dirname "$0")/ghcall.sh"   # the one seam the start\'s loop-bug read goes through (ADR-0146)','gh_aggregate() { return 1; }')
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/fastloop.sh"','"%s/fastloop.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/cqflush.sh"','"%s/cqflush.sh"'%f.rsplit("/",1)[0])
p.write_text(t)
PY
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T/co" SG_HUB_DIR="$T/hub" SG_SHARED="$T/shared" SG_SQUAD="$(cd "$d/../../.." && pwd)"
printf '#!/bin/bash\nsleep 300\n' > "$T/watch.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/fastloop.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh" "$T/watchd.sh"
alive_sup() { [ -n "$sup" ] && kill -0 "$sup" 2>/dev/null; }
wait_down() { for _ in $(seq 1 "$1"); do ! alive_sup && return 0; sleep 0.5; done; return 1; }
# A run's child tree: a committed file, then a modified tracked file and a new untracked file, as a run leaves them.
child() { mkdir -p "$1" && git -C "$1" init -q && printf 'v1\n' > "$1/tracked.txt" && git -C "$1" add -A \
  && git -C "$1" -c user.name=t -c user.email=t@t commit -q -m base && printf 'v2\n' > "$1/tracked.txt" && printf 'new work\n' > "$1/new.txt"; }
child "$T/co/runs/r1/ws/child-1"; child "$T/co/runs/r2/ws/child-2"
printf '{"event": "start"}\n' > "$T/co/runs/r1/record.jsonl"; printf '{"event": "start"}\n' > "$T/co/runs/r2/record.jsonl"
echo "$T/co/runs/r1" > "$SG_STATE/active-runs.txt"

bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && ok "the loop is up for the stop check" || { ko "the loop did not start"; exit 1; }
sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"

# 1. A stop under a live run marks the drain, hands off to the waiter and returns; the run keeps the loop up.
out=$(SG_DRAIN_POLL=1 bash "$T/watchd.sh" stop 2>&1)
case "$out" in *draining*) ok "stop under a live run says it is draining";; *) ko "stop under a live run did not drain: $out";; esac
[ -e "$SG_STATE/auto/drain" ] && [ -e "$SG_STATE/auto/off" ] && ok "stop pauses dispatch and marks the drain" || ko "stop left no drain marker and no pause"
sleep 1.5; alive_sup && ok "the loop stays up while the live run's gate is held" || ko "stop killed the loop under a live run"
kill -0 "$gp" 2>/dev/null && ok "the held gate is not killed by stop" || ko "stop killed the held gate"

# 2. The drained stop: the run ends, the loop stops once the work is done, and no WIP line or ref is written.
printf '{"event": "verdict"}\n' >> "$T/co/runs/r1/record.jsonl"; kill "$gp" 2>/dev/null; gp=
wait_down 20 && ok "the drained stop stops the loop once the run and its gate are done" || ko "the drained stop did not stop the loop"
grep -q 'WIP ' "$SG_STATE/cycle.log" 2>/dev/null && ko "a drained stop wrote a WIP line" || ok "a drained stop writes no WIP line"
[ -z "$(git -C "$T/co/runs/r1/ws/child-1" for-each-ref refs/wip 2>/dev/null)" ] && ok "a drained stop writes no WIP ref" || ko "a drained stop wrote a WIP ref"
[ ! -e "$SG_STATE/auto/drain" ] && ok "the drain marker is gone after the drained stop" || ko "the drain marker outlived the drained stop"

# 3. The hard stop: stop --force snapshots the live run's trees, then kills at once, without waiting for the run.
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
echo "$T/co/runs/r2" > "$SG_STATE/active-runs.txt"; sleep 300 & gp2=$!; echo "$gp2" > "$SG_STATE/locks/gate/pid"
out=$(bash "$T/watchd.sh" stop --force 2>&1)
kill -0 "$gp2" 2>/dev/null && ok "stop --force does not wait for the live run" || ko "stop --force waited for, or killed, the live run's gate"
wait_down 10 && ok "stop --force stops the supervisor" || ko "stop --force left the supervisor up: $out"
grep -q 'WIP saved ws/child-2 refs/wip/r2/ws/child-2' "$SG_STATE/cycle.log" 2>/dev/null && ok "stop --force logs one WIP saved line for the live run's tree" || ko "no WIP saved line from stop --force: $(cat "$SG_STATE/cycle.log" 2>/dev/null)"
ref=refs/wip/r2/ws/child-2
[ "$(git -C "$T/co/runs/r2/ws/child-2" show "$ref:new.txt" 2>/dev/null)" = "new work" ] && [ "$(git -C "$T/co/runs/r2/ws/child-2" show "$ref:tracked.txt" 2>/dev/null)" = "v2" ] \
  && ok "stop --force leaves the tree's work recoverable from its ref" || ko "stop --force did not save the tree's work"
kill "$gp2" 2>/dev/null; gp2=

exit $fail
