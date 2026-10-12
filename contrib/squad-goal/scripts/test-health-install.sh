#!/bin/bash
# The health agent is one launchd label per repository (#20): com.squad-goal.health.<profile>, so two supervisors on one
# machine each have their own agent and installing one never replaces the other. launchctl is a stub that records its calls.
here="$(cd "$(dirname "$0")" && pwd)"; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/profiles" "$T/bin" "$T/home" "$T/co"
for n in alpha beta; do printf ': "${SG_REPO:=x/%s}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=4}"; : "${SG_CHECKOUT:=%s/co}"\n' "$n" "$T" > "$T/profiles/$n.sh"; done
printf '#!/bin/bash\necho "$*" >> "%s/launchctl.log"\n' "$T" > "$T/bin/launchctl"; chmod +x "$T/bin/launchctl"
run() { ( cd "$T/co" && env $(compgen -e | /usr/bin/grep "^SG_" | sed "s/^/-u /") HOME="$T/home" PATH="$T/bin:$PATH" SG_PROFILES_DIR="$T/profiles" SG_STATE_BASE="$T/state" SG_SQUAD=/dev/null SG_PROFILE=$1 bash "$here/health-install.sh" ${2:-} ) 2>&1; }
LA=$T/home/Library/LaunchAgents
run alpha >/dev/null; run beta >/dev/null
[ -f "$LA/com.squad-goal.health.alpha.plist" ] && [ -f "$LA/com.squad-goal.health.beta.plist" ] \
  && ok "each profile installs its own plist" || ko "plists: $(ls "$LA" 2>&1)"
[ ! -e "$LA/com.squad-goal.health.plist" ] && ok "no machine-wide plist is written" || ko "the single machine-wide plist was written"
/usr/bin/grep -q '<key>Label</key><string>com.squad-goal.health.alpha</string>' "$LA/com.squad-goal.health.alpha.plist" \
  && /usr/bin/grep -q '<key>SG_PROFILE</key><string>alpha</string>' "$LA/com.squad-goal.health.alpha.plist" \
  && ok "the label and the profile in the plist are alpha's" || ko "alpha plist: $(cat "$LA/com.squad-goal.health.alpha.plist")"
/usr/bin/grep -q "$T/state/alpha/health.out" "$LA/com.squad-goal.health.alpha.plist" \
  && ok "its log goes to alpha's state directory" || ko "alpha log path"
/usr/bin/grep -q 'bootout gui/[0-9]*/com.squad-goal.health.beta$' "$T/launchctl.log" && ! /usr/bin/grep -q 'bootout gui/[0-9]*/com.squad-goal.health$' "$T/launchctl.log" \
  && ok "installing one profile boots out only its own label" || ko "launchctl calls: $(cat "$T/launchctl.log")"
run alpha uninstall >/dev/null
[ ! -e "$LA/com.squad-goal.health.alpha.plist" ] && [ -f "$LA/com.squad-goal.health.beta.plist" ] \
  && ok "uninstalling alpha leaves beta's agent" || ko "after uninstall: $(ls "$LA")"
rm -rf "$T"; exit $fail
