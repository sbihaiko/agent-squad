#!/bin/bash
# usage: install-agents.sh [--restore] <promoted-sha>   Pins the deployment checkout to a promoted commit and loads the
# daemon (com.squad.voiced) and the supervisor (com.squad.operators) launchd agents from it. Idempotent: a second run with
# the same commit rewrites the same plists and restarts the jobs. --restore, used by rollback-agents.sh, pins without
# recording the commit it leaves, so a rollback does not refill the history it pops.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
restore=0
if [ "${1:-}" = --restore ]; then restore=1; shift; fi
sha=${1:?usage: install-agents.sh [--restore] <promoted-sha>}
DEPLOY=${SG_DEPLOY:-$HOME/.local/share/agent-squad/deploy}
REMOTE=${SG_DEPLOY_REMOTE:-$(git -C "$here" remote get-url origin)}
LAUNCH_DIR=${SG_LAUNCH_DIR:-$HOME/Library/LaunchAgents}
LAUNCHCTL=${LAUNCHCTL:-launchctl}
PY=${SG_PYTHON:-$(command -v python3)}
STATE=${VOICE_STATE:-$HOME/.local/state/agent-squad/voiced}
# The folder both agents read their projects from (#2957). It is the live copy's profiles folder: the deploy checkout's
# own profiles folder holds only example.sh, so an agent that fell back to it would find no project.
PROFILES=${SG_PROFILES_DIR:-$HOME/.claude/skills/squad-goal/scripts/profiles}
PINS="$DEPLOY/.git/squad-pins"
CLAUDE_BIN=$(command -v claude) || { echo "install-agents: claude is not on PATH" >&2; exit 1; }

if [ ! -d "$DEPLOY/.git" ]; then git clone -q "$REMOTE" "$DEPLOY"; fi
git -C "$DEPLOY" fetch -q origin
git -C "$DEPLOY" cat-file -e "$sha^{commit}" 2>/dev/null || { echo "install-agents: $sha is not a commit of $REMOTE" >&2; exit 1; }
if [ "$restore" = 0 ]; then
  git -C "$DEPLOY" merge-base --is-ancestor "$sha" origin/main \
    || { echo "install-agents: $sha is not on origin/main, so it is not promoted" >&2; exit 1; }
  if [ -f "$PINS" ]; then
    cur=$(git -C "$DEPLOY" rev-parse HEAD)
    [ "$cur" = "$(git -C "$DEPLOY" rev-parse "$sha^{commit}")" ] || echo "$cur" >> "$PINS"
  else
    : > "$PINS"
  fi
fi
git -C "$DEPLOY" checkout -q --detach "$sha"

mkdir -p "$LAUNCH_DIR"
V="$LAUNCH_DIR/com.squad.voiced.plist"
O="$LAUNCH_DIR/com.squad.operators.plist"
"$PY" "$DEPLOY/contrib/voice/voiced.py" --print-plist --label com.squad.voiced --python "$PY" --profiles "$PROFILES" > "$V.tmp"
mv "$V.tmp" "$V"
cat > "$O.tmp" <<P2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.squad.operators</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$DEPLOY/contrib/voice/operators.py</string></array>
<key>WorkingDirectory</key><string>$DEPLOY</string>
<key>EnvironmentVariables</key><dict>
<key>PATH</key><string>$(dirname "$CLAUDE_BIN"):/usr/bin:/bin:/usr/sbin:/sbin</string>
<key>HOME</key><string>$HOME</string>
<key>VOICE_STATE</key><string>$STATE</string>
<key>VOICE_REPO</key><string>$DEPLOY</string>
<key>SG_PROFILES_DIR</key><string>$PROFILES</string>
</dict>
<key>RunAtLoad</key><true/>
<key>StartInterval</key><integer>300</integer>
<key>StandardOutPath</key><string>$STATE/operators.out</string>
<key>StandardErrorPath</key><string>$STATE/operators.err</string>
</dict></plist>
P2
mv "$O.tmp" "$O"

# launchd unloads a booted-out job asynchronously: a bootstrap sent before the unload ends answers "Bootstrap failed: 5:
# Input/output error" and leaves the job unloaded (#2954). The wait is bounded so a stuck job fails loud, never hangs.
wait_unloaded() {
  i=0
  while "$LAUNCHCTL" print "gui/$(id -u)/$1" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "${SG_BOOTOUT_WAIT_TRIES:-30}" ]; then return 1; fi
    sleep "${SG_BOOTOUT_WAIT_SECONDS:-0.1}"
  done
  return 0
}
for L in com.squad.voiced com.squad.operators; do
  "$LAUNCHCTL" bootout "gui/$(id -u)/$L" 2>/dev/null || true
  wait_unloaded "$L" || { echo "install-agents: launchd still holds $L after bootout" >&2; exit 1; }
  "$LAUNCHCTL" bootstrap "gui/$(id -u)" "$LAUNCH_DIR/$L.plist" \
    || { echo "install-agents: bootstrap of $L failed" >&2; exit 1; }
done
echo "installed com.squad.voiced and com.squad.operators from $(git -C "$DEPLOY" rev-parse --short HEAD)"
