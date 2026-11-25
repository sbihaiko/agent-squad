#!/bin/bash
# seconds: 4.54
# usage: test-install-agents-bootstrap.sh   Guard for the launchd reload of install-agents.sh (#2954): the script waits
# until launchd has unloaded the old job before it bootstraps the new one, a failed bootstrap exits non-zero and names the
# service, and a finished run leaves com.squad.voiced listed with a pid. launchctl is a stateful fake on PATH.
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
  git -C "$T/src" -c user.name=t -c user.email=t@t commit -q -m proven
  git clone -q --bare "$T/src" "$T/origin.git"
  A=$(git -C "$T/src" rev-parse HEAD)
  # A stateful launchctl: `bootout` leaves the job stopping for FAKE_UNLOAD_TICKS `print` calls, a `print` on a stopping
  # job exits 0, a `print` on a gone job exits 113, and `bootstrap` of a job launchd still holds fails as it did in #2954.
  cat > "$T/bin/launchctl" <<'STUB'
#!/bin/bash
st=${FAKE_STATE:?}
cmd=${1:-}; label=${2##*/}
case "$cmd" in
  bootout)
    if [ "${FAKE_BOOTOUT_STUCK:-0}" = 1 ]; then n=100000; else n=${FAKE_UNLOAD_TICKS:-0}; fi
    printf 'loaded:%s\n' "$n" > "$st/$label" ;;
  print)
    echo "print $label" >> "$st/calls.log"
    s=$(cat "$st/$label" 2>/dev/null) || { echo "Could not find service $label" >&2; exit 113; }
    case "$s" in
      running) ;;
      loaded:*)
        n=${s#loaded:}
        if [ "$n" -le 0 ]; then rm -f "$st/$label"; echo "Could not find service $label" >&2; exit 113; fi
        printf 'loaded:%s\n' "$((n-1))" > "$st/$label" ;;
      *) exit 113 ;;
    esac ;;
  bootstrap)
    label=$(basename "${3:-}" .plist)   # the target is gui/UID, not a job: the label comes from the plist
    if [ -n "$(cat "$st/$label" 2>/dev/null)" ] || [ "${FAKE_BOOTSTRAP_FAIL:-0}" = 1 ]; then
      echo "Bootstrap failed: 5: Input/output error" >&2; exit 5
    fi
    echo running > "$st/$label"; echo "bootstrap $label" >> "$st/calls.log" ;;
  list)
    for f in "$st"/*; do
      [ -f "$f" ] && [ "$(cat "$f")" = running ] || continue
      printf '%s\t0\t%s\n' "${FAKE_PID:-4242}" "$(basename "$f")"
    done ;;
esac
exit 0
STUB
  chmod +x "$T/bin/launchctl"
  export PATH="$T/bin:$PATH"
  export SG_DEPLOY="$T/deploy" SG_DEPLOY_REMOTE="$T/origin.git" SG_LAUNCH_DIR="$T/LaunchAgents"
  export VOICE_STATE="$T/state" VOICE_OWNER=U0TEST SG_PYTHON="$(command -v python3)"
}
mkfix; trap 'rm -rf "$T"' EXIT

# Case 1: launchd holds the old job for three print calls; the script must wait for the unload and end loaded.
export FAKE_STATE="$T/st.wait" FAKE_UNLOAD_TICKS=3; mkdir -p "$FAKE_STATE"
out=$(bash "$here/install-agents.sh" "$A" 2>&1) || fail "install exited non-zero while launchd was still unloading: $(printf '%s' "$out" | tail -3)"
polls=$(grep -c '^print com\.squad\.voiced$' "$FAKE_STATE/calls.log" 2>/dev/null || echo 0)
[ "$polls" -ge 4 ] || fail "install did not wait for the unload: $polls print polls for com.squad.voiced, want at least 4"
listed=$(launchctl list)
printf '%s\n' "$listed" | grep -qE '^[0-9]+[[:space:]]+0[[:space:]]+com\.squad\.voiced$' \
  || fail "launchctl list shows no pid for com.squad.voiced after the install: $(printf '%s' "$listed" | tr '\n' ' ')"
printf '%s\n' "$listed" | grep -qE '^[0-9]+[[:space:]]+0[[:space:]]+com\.squad\.operators$' \
  || fail "launchctl list shows no pid for com.squad.operators after the install"

# Case 2: the bootstrap fails; the script must exit non-zero and name the service.
export FAKE_STATE="$T/st.fail" FAKE_UNLOAD_TICKS=0 FAKE_BOOTSTRAP_FAIL=1; mkdir -p "$FAKE_STATE"
if out=$(bash "$here/install-agents.sh" "$A" 2>&1); then fail "install exited 0 after a failed bootstrap"; fi
printf '%s' "$out" | grep -q 'com\.squad\.voiced' || fail "a failed bootstrap did not name com.squad.voiced: $out"

# Case 3: the job never leaves launchd; the wait is bounded and the script says which service it is.
export FAKE_STATE="$T/st.stuck" FAKE_UNLOAD_TICKS=0 FAKE_BOOTOUT_STUCK=1 SG_BOOTOUT_WAIT_SECONDS=0.01
mkdir -p "$FAKE_STATE"
if out=$(bash "$here/install-agents.sh" "$A" 2>&1); then fail "install exited 0 while the old job never unloaded"; fi
printf '%s' "$out" | grep -q 'com\.squad\.voiced' || fail "a stuck unload did not name com.squad.voiced: $out"
polls=$(grep -c '^print com\.squad\.voiced$' "$FAKE_STATE/calls.log" 2>/dev/null || echo 0)
[ "$polls" -le 45 ] || fail "the unload wait is not bounded: $polls print polls for com.squad.voiced"

echo "ok: install-agents.sh waits for the unload and fails loud"
