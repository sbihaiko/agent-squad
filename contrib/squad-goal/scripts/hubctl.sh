#!/bin/bash
# usage: hubctl.sh [--restart] [port]   start the squad hub (hubmux.py: one process, one path per repository) on 127.0.0.1 if no hub
# answers on the port (idempotent); prints the URL of the cwd's repository, http://127.0.0.1:<port>/<profile>. Opens no browser.
# A port is proof of nothing by itself (#20): only an answer naming the squad hub on /api/hub?identity=1 is. That query
# answers from the hub's loaded profiles with no state read, so a loaded machine cannot time it out and make a real hub
# read as foreign (#1202). A port held by anything else is refused by name, and a hub that does not serve the cwd's profile
# (started before the profile existed) is reported.
# --restart (publish-landing.sh, #31) stops the hub on the port first, only when it answers as the squad hub, and names the
# pid it stopped, then starts it from the files now on disk; an open page sees the new /api/version and reloads itself.
# Anything else on the port is refused, never stopped.
# Each repository's hub state goes to <its checkout>/runs/.hub (self-gitignored).
restart=; [ "${1:-}" = "--restart" ] && { restart=1; shift; }
port="${1:-7700}"; here="$(cd "$(dirname "$0")" && pwd)"; url="http://127.0.0.1:$port"
slug=$(bash "$here/address.sh" "${SG_PROFILE:-.}" 2>/dev/null | sed -n 's/^SG_PROFILE=//p')
ask() { curl -s --max-time 3 "$url/api/hub?identity=1"; }
a=$(ask); rc=$?
if [ -n "$restart" ] && [ $rc = 0 ]; then case "$a" in *'"hub": "squad-hub"'*)
  pid=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null)
  kill $pid 2>/dev/null
  [ -n "$pid" ] && echo "hubctl: stopped the squad hub on port $port (pid $pid)" >&2
  for _ in $(seq 20); do sleep 0.5; ask >/dev/null; [ $? = 7 ] && break; done
  a=$(ask); rc=$?; [ $rc = 7 ] || { echo "hubctl: the hub on port $port did not stop" >&2; exit 1; };; esac; fi
if [ $rc = 7 ]; then   # nothing listens: start the hub
  python3 - "$here/hubmux.py" "$port" <<'P'
import shutil, subprocess, sys
pre = ["caffeinate", "-dimsu"] if shutil.which("caffeinate") else []
subprocess.Popen(pre + ["python3", sys.argv[1], sys.argv[2]], stdout=open("/tmp/squad-hub.log", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
P
  for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 1; a=$(ask) && break; done
  hs=$(cut -d' ' -f1 "$here/../PUBLISHED" 2>/dev/null); [ -n "$hs" ] || hs=$(git -C "$here/.." rev-parse HEAD 2>/dev/null)
  [ -n "$hs" ] && [ -n "${SG_STATE:-}" ] && mkdir -p "$SG_STATE" && printf '%s\n' "$hs" > "$SG_STATE/hub.sha.$$" && mv "$SG_STATE/hub.sha.$$" "$SG_STATE/hub.sha"   # the build this hub loaded: live-guard.sh names a later publish of its files
fi
case "$a" in *'"hub": "squad-hub"'*) ;; *) echo "hubctl: port $port is held by something that is not the squad hub; pick another port or stop it" >&2; exit 1;; esac
if [ -n "$slug" ]; then
  case "$a" in *"\"slug\": \"$slug\""*) ;; *) echo "hubctl: the hub on port $port does not serve $slug; restart it to read the new profile" >&2; exit 1;; esac
fi
echo "$url/$slug"
