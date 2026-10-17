#!/bin/bash
# The token tripwire stops the loop for good (#489). watch.sh runs `watchd.sh stop` when the guard fires, and that stop
# must take the supervisor down with it: the supervisor, the cycle and the fast lane all exit, and nothing restarts.
# Offline: a temp state dir, a stub env.sh and a stub watch.sh that stops the daemon the way the guard path does.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'kill -9 "$sup" 2>/dev/null; rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME"
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/fastloop.sh"','"%s/no-fastloop.sh"'%f.rsplit("/",1)[0])
p.write_text(t)
PY
# the stub watch.sh: one line to the log, then the stop the guard path runs, then exit
# the pause lets `start` see the supervisor alive before the stop lands, the way a real cycle does
printf '#!/bin/bash\necho "cycle ran" >> "$SG_STATE/watch.log"\nsleep 2\nbash "%s" stop\nexit 0\n' "$T/watchd.sh" > "$T/watch.sh"
chmod +x "$T/watch.sh" "$T/watchd.sh"
bash "$T/watchd.sh" start >/dev/null
sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
# the stop has to land on its own: wait for the supervisor to be gone, never kill it ourselves
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do /bin/ps -p "$sup" >/dev/null 2>&1 || break; sleep 1; done
/bin/ps -p "$sup" >/dev/null 2>&1 && ko "the supervisor is still alive after watch.sh stopped it (pid $sup)" || ok "the supervisor exits when watch.sh stops it"
st=$(bash "$T/watchd.sh" status)
case "$st" in *"not running"*) ok "watchd.sh status answers not running";; *) ko "status after the stop: $st";; esac
# one cycle, never a restart
n=$(/usr/bin/grep -c "cycle ran" "$SG_STATE/watch.log" 2>/dev/null)
[ "$n" = 1 ] && ok "watch.sh ran once and was not restarted" || ko "watch.sh ran $n times"
# the fast lane dies with the supervisor
fl=$(cat "$SG_STATE/fastloop.pid" 2>/dev/null)
{ [ -z "$fl" ] || ! /bin/ps -p "$fl" >/dev/null 2>&1; } && ok "the fast lane is gone" || ko "the fast lane is still alive (pid $fl)"
rm -rf "$T"; exit $fail
