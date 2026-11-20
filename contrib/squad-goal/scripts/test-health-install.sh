#!/bin/bash
# Regression test of health-install.sh (#2552): the launchd agent it writes starts health.sh, and health.sh starts the
# supervisor, so the plist is the whole environment of every child the supervisor starts. A model gateway the installing
# shell names must reach that environment, or each child call to a gateway model is refused after a machine restart.
# Offline: a throwaway HOME and profile, a stub launchctl. usage: bash test-health-install.sh [scripts dir]
set -u
d=$(cd "${1:-$(dirname "$0")}" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home" "$T/profiles" "$T/state" "$T/co"
printf ': "${SG_REPO:=acme/emu}"\n' > "$T/profiles/emu.sh"
printf '#!/bin/sh\necho "launchctl $*" >> "%s/calls"\n' "$T" > "$T/bin/launchctl"; chmod +x "$T/bin/launchctl"
fails=0; pass=0
ok() { pass=$((pass+1)); echo "ok - $1"; }
no() { fails=$((fails+1)); echo "not ok - $1"; }
install() { (cd "$T/co" && env "$@" PATH="$T/bin:$PATH" HOME="$T/home" SG_PROFILES_DIR="$T/profiles" SG_PROFILE=emu \
  SG_STATE="$T/state" SG_CHECKOUT="$T/co" bash "$d/health-install.sh" >/dev/null 2>&1); }
plist() { cat "$T/home/Library/LaunchAgents/"*.plist 2>/dev/null; }

# 1. The installing shell names a gateway: the plist carries it.
install ANTHROPIC_BASE_URL=http://localhost:8016
if plist | grep -q '<key>ANTHROPIC_BASE_URL</key><string>http://localhost:8016</string>'; then
  ok "the plist carries the gateway the installing shell names"
else no "the plist carries the gateway the installing shell names"; fi
plutil -lint "$T/home/Library/LaunchAgents/"*.plist >/dev/null 2>&1 && ok "the plist is valid" || no "the plist is valid"

# 2. The installing shell names no gateway: the plist names none, and it keeps the profile and the path.
rm -f "$T/home/Library/LaunchAgents/"*.plist
install -u ANTHROPIC_BASE_URL
if plist | grep -q ANTHROPIC_BASE_URL; then no "no gateway in the shell writes none"; else ok "no gateway in the shell writes none"; fi
plist | grep -q '<key>SG_PROFILE</key><string>emu</string>' && ok "the plist keeps the profile" || no "the plist keeps the profile"

echo "$pass ok, $fails failed"
[ "$fails" -eq 0 ]
