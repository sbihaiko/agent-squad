#!/bin/bash
# usage: health-install.sh [uninstall]   Installs/removes the launchd agent that runs health.sh every 2 min (outside watchd, which cannot notice its own death).
# Run from the target checkout (or with SG_PROFILE set). Removal: this script with `uninstall`, or `launchctl bootout gui/$(id -u)/com.squad-goal.health.<profile>`.
# One agent per repository (#20): the label carries the profile, so two supervisors never share or replace one agent.
. "$(dirname "$0")/env.sh"; L=com.squad-goal.health.$SG_PROFILE; P="$HOME/Library/LaunchAgents/$L.plist"; me="$(cd "$(dirname "$0")" && pwd)/health.sh"
if [ "${1:-}" = uninstall ]; then launchctl bootout "gui/$(id -u)/$L" 2>/dev/null; rm -f "$P"; echo "health agent removed"; exit 0; fi
mkdir -p "$(dirname "$P")"
cat > "$P" <<P2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$L</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$me</string></array>
<key>WorkingDirectory</key><string>$SG_CHECKOUT</string>
<key>EnvironmentVariables</key><dict><key>SG_PROFILE</key><string>$SG_PROFILE</string><key>PATH</key><string>$PATH</string></dict>
<key>RunAtLoad</key><true/>
<key>StartInterval</key><integer>120</integer>
<key>StandardOutPath</key><string>$SG_STATE/health.out</string><key>StandardErrorPath</key><string>$SG_STATE/health.out</string>
</dict></plist>
P2
launchctl bootout "gui/$(id -u)/$L" 2>/dev/null; launchctl bootstrap "gui/$(id -u)" "$P" && echo "health agent installed ($P)"
