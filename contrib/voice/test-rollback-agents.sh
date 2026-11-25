#!/bin/bash
# usage: test-rollback-agents.sh   Launch-job guard for rollback-agents.sh: the deployment checkout returns to the previous
# proven commit, both agents restart, the state directory is untouched, and a second rollback changes nothing. launchctl is a fake.
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
  # `print` answers 113 for a job launchd does not hold, so the unload wait never fires on this fake (#2954).
  printf '#!/bin/bash\nif [ "${1:-}" = print ]; then exit 113; fi\necho "$*" >> "%s"\n' "$T/launchctl.log" > "$T/bin/launchctl"; chmod +x "$T/bin/launchctl"
  export SG_DEPLOY="$T/deploy" SG_DEPLOY_REMOTE="$T/origin.git" SG_LAUNCH_DIR="$T/LaunchAgents" LAUNCHCTL="$T/bin/launchctl"
  export VOICE_STATE="$T/state" VOICE_OWNER=U0TEST SG_PYTHON="$(command -v python3)"
}
mkfix; trap 'rm -rf "$T"' EXIT
V="$T/LaunchAgents/com.squad.voiced.plist"; O="$T/LaunchAgents/com.squad.operators.plist"
snap() { find "$T/state" -type f -exec cksum {} + 2>/dev/null | sort; }

bash "$here/install-agents.sh" "$A" >/dev/null 2>&1 || fail "install of the first proven commit exited non-zero"
bash "$here/install-agents.sh" "$B" >/dev/null 2>&1 || fail "install of the promoted commit exited non-zero"
printf '{"id":"1"}\n' > "$T/state/other.jsonl"; mkdir -p "$T/state/inboxes"; printf 'line\n' > "$T/state/inboxes/dm.jsonl"
before=$(snap); [ -n "$before" ] || fail "fixture wrote no state to protect"

: > "$T/launchctl.log"
bash "$here/rollback-agents.sh" >/dev/null 2>&1 || fail "rollback exited non-zero"
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$A" ] || fail "rollback did not return the checkout to the previous proven commit"
grep -qF "$V" "$T/launchctl.log" && grep -qF "$O" "$T/launchctl.log" || fail "rollback did not restart both agents"
[ "$(snap)" = "$before" ] || fail "rollback changed the state directory"

cp "$V" "$T/v1"; cp "$O" "$T/o1"
if bash "$here/rollback-agents.sh" >/dev/null 2>&1; then fail "a second rollback with no proven commit left exited zero"; fi
[ "$(git -C "$T/deploy" rev-parse HEAD)" = "$A" ] || fail "a second rollback moved the checkout"
cmp -s "$V" "$T/v1" && cmp -s "$O" "$T/o1" || fail "a second rollback changed a plist"
[ "$(snap)" = "$before" ] || fail "a second rollback changed the state directory"

echo "ok: rollback-agents.sh"
