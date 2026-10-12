#!/bin/bash
# usage: hubctl.sh [port]   start the squad hub (hubmux.py: one process, one path per repository) on 127.0.0.1 if no hub
# answers on the port (idempotent); prints the URL of the cwd's repository, http://127.0.0.1:<port>/<profile>. Opens no browser.
# A port is proof of nothing by itself (#20): only an answer naming the squad hub on /api/hub is. A port held by anything
# else is refused by name, and a hub that does not serve the cwd's profile (started before the profile existed) is reported.
# Each repository's hub state goes to <its checkout>/runs/.hub (self-gitignored).
port="${1:-7700}"; here="$(cd "$(dirname "$0")" && pwd)"; url="http://127.0.0.1:$port"
slug=$(bash "$here/repoctx.sh" "${SG_PROFILE:-.}" 2>/dev/null | sed -n 's/^SG_PROFILE=//p')
ask() { curl -s --max-time 3 "$url/api/hub"; }
a=$(ask); rc=$?
if [ $rc = 7 ]; then   # nothing listens: start the hub
  python3 - "$here/hubmux.py" "$port" <<'P'
import shutil, subprocess, sys
pre = ["caffeinate", "-dimsu"] if shutil.which("caffeinate") else []
subprocess.Popen(pre + ["python3", sys.argv[1], sys.argv[2]], stdout=open("/tmp/squad-hub.log", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
P
  for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 1; a=$(ask) && break; done
fi
case "$a" in *'"hub": "squad-hub"'*) ;; *) echo "hubctl: port $port is held by something that is not the squad hub; pick another port or stop it" >&2; exit 1;; esac
if [ -n "$slug" ]; then
  case "$a" in *"\"slug\": \"$slug\""*) ;; *) echo "hubctl: the hub on port $port does not serve $slug; restart it to read the new profile" >&2; exit 1;; esac
fi
echo "$url/$slug"
