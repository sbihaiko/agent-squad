#!/bin/bash
# usage: test-uninstall-agents.sh   Launch-job guard for uninstall-agents.sh: both agents are booted out and their plists
# removed, a second run is a no-op, and neither the deployment checkout nor the state directory is touched. launchctl is a fake.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }
mkfix() {
  # No detached `git maintenance --auto`: its repack deletes loose objects while the clone below copies them (#2894).
  export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=maintenance.auto GIT_CONFIG_VALUE_0=false GIT_CONFIG_KEY_1=gc.auto GIT_CONFIG_VALUE_1=0
  T=$(mktemp -d); mkdir -p "$T/bin" "$T/src/contrib"
  cp -R "$here" "$T/src/contrib/voice"
  # voiced.py imports the squad package, and a real deploy clones the whole repo: carry its tracked files (#2894).
  (cd "$here/../.." && git ls-files -z squad | xargs -0 tar -cf -) | tar -xf - -C "$T/src"
  printf '__pycache__/\n*.pyc\n' > "$T/src/.gitignore"   # a pyc another worker writes mid-add must not enter the commit
  git -C "$T/src" init -q -b main
  git -C "$T/src" -c user.name=t -c user.email=t@t add -A
  git -C "$T/src" -c user.name=t -c user.email=t@t commit -q -m proven; A=$(git -C "$T/src" rev-parse HEAD)
  git clone -q --bare "$T/src" "$T/origin.git"
  printf '#!/bin/bash\necho "$*" >> "%s"\n' "$T/launchctl.log" > "$T/bin/launchctl"; chmod +x "$T/bin/launchctl"
  export SG_DEPLOY="$T/deploy" SG_DEPLOY_REMOTE="$T/origin.git" SG_LAUNCH_DIR="$T/LaunchAgents" LAUNCHCTL="$T/bin/launchctl"
  export VOICE_STATE="$T/state" VOICE_OWNER=U0TEST SG_PYTHON="$(command -v python3)"
}
mkfix; trap 'rm -rf "$T"' EXIT
V="$T/LaunchAgents/com.squad.voiced.plist"; O="$T/LaunchAgents/com.squad.operators.plist"
snap() { find "$T/state" -type f -exec cksum {} + 2>/dev/null | sort; }

bash "$here/install-agents.sh" "$A" >/dev/null 2>&1 || fail "install exited non-zero"
printf '{"id":"1"}\n' > "$T/state/other.jsonl"
before=$(snap)

: > "$T/launchctl.log"
bash "$here/uninstall-agents.sh" >/dev/null 2>&1 || fail "uninstall exited non-zero"
[ ! -e "$V" ] && [ ! -e "$O" ] || fail "uninstall left a plist in the LaunchAgents folder"
grep -qF "bootout" "$T/launchctl.log" && grep -qF "com.squad.voiced" "$T/launchctl.log" \
  && grep -qF "com.squad.operators" "$T/launchctl.log" || fail "uninstall did not boot out both agents"
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$A" ] || fail "uninstall moved the deployment checkout"
[ "$(snap)" = "$before" ] || fail "uninstall changed the state directory"

bash "$here/uninstall-agents.sh" >/dev/null 2>&1 || fail "a second uninstall exited non-zero"
[ ! -e "$V" ] && [ ! -e "$O" ] || fail "a second uninstall left a plist behind"
[ "$(snap)" = "$before" ] || fail "a second uninstall changed the state directory"

echo "ok: uninstall-agents.sh"
