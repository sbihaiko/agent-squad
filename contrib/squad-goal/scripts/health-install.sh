#!/bin/bash
# usage: health-install.sh [uninstall]   Installs/removes the launchd agent that runs health.sh every 2 min (outside watchd, which cannot notice its own death).
# One agent per repository, so each supervisor is watched and removing one leaves the others.
. "$(dirname "$0")/env.sh"; L=$(sg_health_label "$SG_PROFILE"); P="$HOME/Library/LaunchAgents/$L.plist"; me="$(cd "$(dirname "$0")" && pwd)/health.sh"
if [ "${1:-}" = uninstall ]; then launchctl bootout "gui/$(id -u)/$L" 2>/dev/null; rm -f "$P"; echo "health agent $L removed"; exit 0; fi
mkdir -p "$(dirname "$P")"
# The plist is the whole environment of health.sh, of the supervisor it starts and of every child that supervisor starts
# (#2552). A model gateway the installing shell names goes in, or each child call to a gateway model is refused after a
# machine restart. No default is written: with the variable unset, the CLI reaches its own endpoint.
x() { local v=${1//&/&amp;}; v=${v//</&lt;}; printf '%s' "${v//>/&gt;}"; }
gw=; [ -n "${ANTHROPIC_BASE_URL:-}" ] && gw="<key>ANTHROPIC_BASE_URL</key><string>$(x "$ANTHROPIC_BASE_URL")</string>"
cat > "$P" <<P2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$L</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$me</string></array>
<key>WorkingDirectory</key><string>$(x "$SG_CHECKOUT")</string>
<key>EnvironmentVariables</key><dict><key>SG_PROFILE</key><string>$(x "$SG_PROFILE")</string><key>PATH</key><string>$(x "$PATH")</string>$gw</dict>
<key>RunAtLoad</key><true/>
<key>StartInterval</key><integer>120</integer>
<key>StandardOutPath</key><string>$SG_STATE/health.out</string><key>StandardErrorPath</key><string>$SG_STATE/health.out</string>
</dict></plist>
P2
if [ -n "$gw" ]; then echo "health agent: the plist carries the model gateway $ANTHROPIC_BASE_URL"
else echo "health agent: no ANTHROPIC_BASE_URL in this shell, so the plist names no model gateway"; fi
plutil -lint "$P" >/dev/null || { echo "health agent: $P is not a valid plist; nothing is loaded" >&2; exit 1; }
launchctl bootout "gui/$(id -u)/$L" 2>/dev/null; launchctl bootstrap "gui/$(id -u)" "$P" && echo "health agent installed ($P)"
