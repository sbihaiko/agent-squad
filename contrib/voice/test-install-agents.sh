#!/bin/bash
# usage: test-install-agents.sh   Launch-job guard for install-agents.sh: both agents are named, pinned to a promoted
# commit in a deployment checkout, idempotent, and refused for a commit main does not carry. launchctl is a fake.
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
  echo promoted >> "$T/src/contrib/voice/OPERATOR.md"
  git -C "$T/src" -c user.name=t -c user.email=t@t commit -qam promoted; B=$(git -C "$T/src" rev-parse HEAD)
  git clone -q --bare "$T/src" "$T/origin.git"
  printf '#!/bin/bash\necho "$*" >> "%s"\n' "$T/launchctl.log" > "$T/bin/launchctl"; chmod +x "$T/bin/launchctl"
  mkdir -p "$T/profiles"; : > "$T/profiles/example.sh"
  export SG_DEPLOY="$T/deploy" SG_DEPLOY_REMOTE="$T/origin.git" SG_LAUNCH_DIR="$T/LaunchAgents" LAUNCHCTL="$T/bin/launchctl"
  export VOICE_STATE="$T/state" VOICE_OWNER=U0TEST SG_PYTHON="$(command -v python3)" SG_PROFILES_DIR="$T/profiles"
}
mkfix; trap 'rm -rf "$T"' EXIT
V="$T/LaunchAgents/com.squad.voiced.plist"; O="$T/LaunchAgents/com.squad.operators.plist"
key() { plutil -extract "$2" raw -o - "$1"; }

out=$(bash "$here/install-agents.sh" "$B" 2>&1) || fail "install of the promoted commit exited non-zero: $(printf '%s' "$out" | tail -3)"
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$B" ] || fail "deployment checkout is not pinned to the promoted commit"
[ -f "$V" ] && [ -f "$O" ] || fail "both plists must be written to the LaunchAgents folder"
plutil -lint "$V" "$O" >/dev/null || fail "a plist does not lint"
[ "$(key "$V" Label)" = com.squad.voiced ] && [ "$(key "$O" Label)" = com.squad.operators ] || fail "agent labels are not com.squad.*"
[ "$(key "$V" RunAtLoad)" = true ] && [ "$(key "$V" KeepAlive)" = true ] || fail "voiced plist lacks RunAtLoad or KeepAlive"
[ "$(key "$O" RunAtLoad)" = true ] && [ "$(key "$O" StartInterval)" = 300 ] || fail "operators plist lacks RunAtLoad or StartInterval 300"
case "$(key "$V" ProgramArguments.1)" in "$T/deploy/"*) ;; *) fail "voiced runs outside the deployment checkout";; esac
case "$(key "$O" ProgramArguments.1)" in "$T/deploy/"*) ;; *) fail "operators runs outside the deployment checkout";; esac
grep -qF "$V" "$T/launchctl.log" && grep -qF "$O" "$T/launchctl.log" || fail "install did not load both agents"
# #2957: each agent reads the projects from SG_PROFILES_DIR, so both plists must carry it; the deploy's own profiles
# folder holds only example.sh, and an agent that falls back to it finds no project.
[ "$(key "$V" EnvironmentVariables.SG_PROFILES_DIR)" = "$T/profiles" ] || fail "voiced plist lacks SG_PROFILES_DIR"
[ "$(key "$O" EnvironmentVariables.SG_PROFILES_DIR)" = "$T/profiles" ] || fail "operators plist lacks SG_PROFILES_DIR"

cp "$V" "$T/v1"; cp "$O" "$T/o1"; pins_before=$(cat "$T/deploy/.git/squad-pins" 2>/dev/null | wc -l)
bash "$here/install-agents.sh" "$B" >/dev/null 2>&1 || fail "a second install exited non-zero"
cmp -s "$V" "$T/v1" && cmp -s "$O" "$T/o1" || fail "a second install changed a plist"
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$B" ] || fail "a second install moved the checkout"
pins_after=$(cat "$T/deploy/.git/squad-pins" 2>/dev/null | wc -l)
[ "$pins_before" = "$pins_after" ] || fail "a second install grew the pin history"
# #2957: with SG_PROFILES_DIR unset the install names the live profiles folder, never the deploy's own example-only one.
env -u SG_PROFILES_DIR bash "$here/install-agents.sh" "$B" >/dev/null 2>&1 || fail "an install without SG_PROFILES_DIR exited non-zero"
for P in "$V" "$O"; do
  dir=$(key "$P" EnvironmentVariables.SG_PROFILES_DIR) || fail "$(basename "$P") lacks the default SG_PROFILES_DIR"
  case "$dir" in */squad-goal/scripts/profiles) ;; *) fail "default SG_PROFILES_DIR is $dir, not the live profiles folder";; esac
  case "$dir" in "$T/deploy"/*) fail "default SG_PROFILES_DIR points into the deploy checkout";; esac
done

git -C "$T/src" checkout -q -b side
echo side > "$T/src/side.txt"; git -C "$T/src" add side.txt
git -C "$T/src" -c user.name=t -c user.email=t@t commit -qm side; S=$(git -C "$T/src" rev-parse HEAD)
git -C "$T/src" push -q "$T/origin.git" side
if bash "$here/install-agents.sh" "$S" >/dev/null 2>&1; then fail "install pinned a commit main does not carry"; fi
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$B" ] || fail "a refused install moved the checkout"

echo "ok: install-agents.sh"
