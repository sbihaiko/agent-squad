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
  # The operators agent reads its projects through the hub seam, so the deploy carries address.sh (#2957).
  mkdir -p "$T/src/contrib/squad-goal/scripts"
  cp "$here/../squad-goal/scripts/address.sh" "$T/src/contrib/squad-goal/scripts/"
  printf '__pycache__/\n*.pyc\n' > "$T/src/.gitignore"   # a pyc another worker writes mid-add must not enter the commit
  git -C "$T/src" init -q -b main
  git -C "$T/src" -c user.name=t -c user.email=t@t add -A
  git -C "$T/src" -c user.name=t -c user.email=t@t commit -q -m proven; A=$(git -C "$T/src" rev-parse HEAD)
  echo promoted >> "$T/src/contrib/voice/OPERATOR.md"
  git -C "$T/src" -c user.name=t -c user.email=t@t commit -qam promoted; B=$(git -C "$T/src" rev-parse HEAD)
  git clone -q --bare "$T/src" "$T/origin.git"
  # `print` answers 113 for a job launchd does not hold, so the unload wait never fires on this fake (#2954).
  printf '#!/bin/bash\nif [ "${1:-}" = print ]; then exit 113; fi\necho "$*" >> "%s"\n' "$T/launchctl.log" > "$T/bin/launchctl"; chmod +x "$T/bin/launchctl"
  # A fake CLI for the operator's `--check`: it lists no live session, so every project reads as one to start (#2957).
  printf '#!/bin/bash\nif [ "$1 $2" = "agents --json" ]; then echo "[]"; exit 0; fi\nexit 2\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
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
# #2957: the folder the plist names is the folder the agent reads, so a project profile there makes operators name its
# session. The check runs with the plist's own EnvironmentVariables, and one session per project must be named.
cat > "$T/profiles/widget.sh" <<P3
: "\${SG_REPO:=acme/widget}"
: "\${SG_CHECKOUT:=$T/src}"
P3
printf '{"acme/widget":{"name":"proj-widget","id":"C0WIDGET01"}}' > "$T/state/channels.json"
"$SG_PYTHON" - "$O" "$T/deploy/contrib/voice/operators.py" "$T/bin" > "$T/check.out" 2> "$T/check.err" <<'PY'
import plistlib, subprocess, sys
doc = plistlib.load(open(sys.argv[1], "rb"))
env = dict(doc["EnvironmentVariables"]); env["PATH"] = sys.argv[3] + ":" + env.get("PATH", "")
sys.exit(subprocess.run([sys.executable, sys.argv[2], "--check"], env=env).returncode)
PY
[ $? = 0 ] || fail "operators --check over the emitted environment exited non-zero: $(tail -2 "$T/check.err")"
grep -q "would start acme-widget" "$T/check.out" || fail "operators named no session for the profile the plist names: $(cat "$T/check.out")"

cp "$V" "$T/v1"; cp "$O" "$T/o1"; pins_before=$(cat "$T/deploy/.git/squad-pins" 2>/dev/null | wc -l)
bash "$here/install-agents.sh" "$B" >/dev/null 2>&1 || fail "a second install exited non-zero"
cmp -s "$V" "$T/v1" && cmp -s "$O" "$T/o1" || fail "a second install changed a plist"
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$B" ] || fail "a second install moved the checkout"
pins_after=$(cat "$T/deploy/.git/squad-pins" 2>/dev/null | wc -l)
[ "$pins_before" = "$pins_after" ] || fail "a second install grew the pin history"
# #2957: with SG_PROFILES_DIR unset the install names the live profiles folder, never the deploy's own example-only one.
env -u SG_PROFILES_DIR bash "$here/install-agents.sh" "$B" >/dev/null 2>&1 || fail "an install without SG_PROFILES_DIR exited non-zero"
# #2957: one constant names the folder. The install asks the daemon for its default, so the emitted value is the
# daemon's LIVE_PROFILES and never a second copy the script carries.
want=$("$SG_PYTHON" "$T/deploy/contrib/voice/voiced.py" --print-profiles-default) \
  || fail "voiced.py --print-profiles-default exited non-zero"
for P in "$V" "$O"; do
  dir=$(key "$P" EnvironmentVariables.SG_PROFILES_DIR) || fail "$(basename "$P") lacks the default SG_PROFILES_DIR"
  [ "$dir" = "$want" ] || fail "default SG_PROFILES_DIR is $dir, not the daemon's LIVE_PROFILES ($want)"
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
