#!/bin/bash
# `watchd.sh stop` stops the loop for good (#489): the stop takes the supervisor down with it, so the supervisor, the cycle
# and the fast lane all exit, and nothing restarts. A start is a relight: it clears the token guard's markers.
# Offline: a temp state dir, a stub env.sh and a stub watch.sh that stops the daemon from inside a cycle.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); . "$d/driver-tree.sh"
trap 'kill -9 "$sup" "$fl" "$cqpid" $ps_ 2>/dev/null; driver_tree_kill "$T"; rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# A pid answers while it lives, through the signal path: the process table is not this suite's to read (#1508).
alive_pid() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
. "$d/watchd-fixture.sh"   # the process reader a fake env.sh needs (#1514)
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME"
# A stop drains first, and the drain reads the runs through squad/run_liveness.py (ADR-0167), found in SG_SQUAD as a live
# copy finds it (test-watchd-drain.sh). A read with no reader installed is unknown, and unknown holds the drain, so this
# fixture needs the root: without it the stop never lands and every lane outlives it (#2428).
export SG_SQUAD="$(cd "$d/../../.." && pwd)"
# The skill's own caps.sh is absent from the copy, and the `ps` it falls back to is refused inside a child's sandbox
# (#1508), so the fake env.sh names the reader the real caps.sh runs.
reader=$(fake_reader "$SG_SQUAD")
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" "$reader" <<'PY'
import pathlib,sys
f,st,reader=sys.argv[1],sys.argv[2],sys.argv[3]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE=; export SG_STATE SG_PROFILE; %s'%(st,reader),1)
t=t.replace('. "$(dirname "$0")/ghcall.sh"   # the one seam the start\'s loop-bug read goes through (ADR-0146)',
            'gh_aggregate() { return 1; }')
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%f.rsplit("/",1)[0])
p.write_text(t)
PY
# the stub watch.sh: one line to the log, then a stop from inside the cycle, then exit
# the pause lets `start` see the supervisor alive before the stop lands, the way a real cycle does
printf '#!/bin/bash\necho "cycle ran" >> "$SG_STATE/watch.log"\nsleep 8\nbash "%s" stop\nexit 0\n' "$T/watchd.sh" > "$T/watch.sh"
# the real fast lane, beside stub passes: its childcc runs in the background (#590), and a stub childcc.py starts a child of
# its own, as a review does. The stop must end the pass and its child too (#529).
cp "$d/fastloop.sh" "$d/single.sh" "$T/"; { printf 'export SG_STATE=%s; SG_FAST_INTERVAL=60\n' "$SG_STATE"; printf '%s' "$reader"; } > "$T/env.sh"
for f in autofrontier autobug; do printf '#!/bin/bash\nexit 0\n' > "$T/$f.sh"; chmod +x "$T/$f.sh"; done
printf 'import os,subprocess\np=subprocess.Popen(["sleep","30"])\nopen(os.environ["SG_STATE"]+"/pass.pid","w").write("%%d %%d\\n" %% (os.getpid(), p.pid))\np.wait()\n' > "$T/childcc.py"
# The comment flusher is a third lane (#669): the queue is the only place a child learns why the entry gate rejected its work,
# so a loop that runs without it repeats every rejection blind. The stub marks its start and stays up like the real one.
printf '#!/bin/bash\necho $$ > "$SG_STATE/cq.pid"; echo start >> "$SG_STATE/cq.starts"; sleep 30\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/watchd.sh" "$T/cqflush.sh"
bash "$T/watchd.sh" start >/dev/null
sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do [ -s "$SG_STATE/cq.pid" ] && break; sleep 0.2; done
cqpid=$(cat "$SG_STATE/cq.pid" 2>/dev/null)
alive_pid "$cqpid" && ok "the supervisor starts the comment flusher" || ko "no comment flusher ran (pid ${cqpid:-none})"
bash "$T/watchd.sh" start >/dev/null 2>&1   # a second start of a live loop starts no second lane
[ "$(cat "$SG_STATE/cq.starts" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "a second start runs no second flusher" || ko "the flusher started $(cat "$SG_STATE/cq.starts" 2>/dev/null | wc -l | tr -d ' ') times"
# the stop has to land on its own: wait for the supervisor to be gone, never kill it ourselves
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do alive_pid "$sup" || break; sleep 1; done
alive_pid "$sup" && ko "the supervisor is still alive after watch.sh stopped it (pid $sup)" || ok "the supervisor exits when watch.sh stops it"
st=$(bash "$T/watchd.sh" status)
case "$st" in *"not running"*) ok "watchd.sh status answers not running";; *) ko "status after the stop: $st";; esac
# one cycle, never a restart
n=$(/usr/bin/grep -c "cycle ran" "$SG_STATE/watch.log" 2>/dev/null)
[ "$n" = 1 ] && ok "watch.sh ran once and was not restarted" || ko "watch.sh ran $n times"
# the fast lane dies with the supervisor
fl=$(cat "$SG_STATE/fastloop.pid" 2>/dev/null)
{ [ -z "$fl" ] || ! alive_pid "$fl"; } && ok "the fast lane is gone" || ko "the fast lane is still alive (pid $fl)"
{ [ -z "$cqpid" ] || ! alive_pid "$cqpid"; } && ok "the comment flusher stops with the supervisor" || ko "the comment flusher is still alive (pid $cqpid)"
ps_=$(cat "$SG_STATE/pass.pid" 2>/dev/null)
up() { for p in $ps_; do alive_pid "$p" && return 0; done; return 1; }
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -n "$ps_" ] && up || break; sleep 0.2; done
[ -n "$ps_" ] && ! up && ok "the fast lane's background childcc and its child stop with the supervisor" || { ko "the fast lane's pass still runs after the stop (pids ${ps_:-none})"; kill $ps_ 2>/dev/null; }
rm -f "$SG_STATE/pass.pid"
# a relight starts a fresh measurement (#515): the old marker made every start read the old burn again
printf '1\n' > "$SG_STATE/token-guard.since"; echo "Token burn ..." > "$SG_STATE/token-pause"; echo "Token burn ..." > "$SG_STATE/token-over"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
[ "$(cat "$SG_STATE/token-guard.since" 2>/dev/null)" != 1 ] && ok "a start drops the old token-guard marker" || ko "the old token-guard marker survived the start"
[ ! -e "$SG_STATE/token-pause" ] && [ ! -e "$SG_STATE/token-over" ] && ok "a start clears the token-pause and token-over markers" || ko "a token marker survived the start"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do alive_pid "$sup" || break; sleep 1; done
rm -rf "$T"; exit $fail
