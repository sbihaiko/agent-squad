#!/bin/bash
# usage: hubctl.sh [port]   start the squad hub on 127.0.0.1 if it is not listening (idempotent); prints the URL. Opens no browser.
# State goes to <cwd>/runs/.hub (self-gitignored). Add `runs/.hub/` to .gitignore only if the repo does not already ignore runs/.
port="${1:-7700}"; here="$(cd "$(dirname "$0")" && pwd)"
if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  python3 - "$here/hub.py" "$port" <<'P'
import subprocess, sys
subprocess.Popen(["caffeinate", "-dimsu", "python3", sys.argv[1], sys.argv[2]], stdout=open("/tmp/squad-hub.log", "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
P
  sleep 2
fi
echo "http://127.0.0.1:$port/"
