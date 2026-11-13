#!/bin/bash
# usage: uninstall-agents.sh   Boots out the daemon and supervisor launchd agents and removes their plists. The deployment
# checkout and the state directory stay where they are.
set -u
LAUNCH_DIR=${SG_LAUNCH_DIR:-$HOME/Library/LaunchAgents}
LAUNCHCTL=${LAUNCHCTL:-launchctl}
for L in com.squad.voiced com.squad.operators; do
  "$LAUNCHCTL" bootout "gui/$(id -u)/$L" 2>/dev/null || true
  rm -f "$LAUNCH_DIR/$L.plist"
  echo "agent $L removed"
done
