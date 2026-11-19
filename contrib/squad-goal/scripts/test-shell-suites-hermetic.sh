#!/bin/bash
# Regression test for #1410 cause 2: shell-suites.sh clears every inherited SG_* variable before each suite, so the live loop's env cannot reach an assertion. usage: bash test-shell-suites-hermetic.sh [path/to/shell-suites.sh]
src=${1:-$(dirname "$0")/shell-suites.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$src" "$T/s/shell-suites.sh"; cp "$(dirname "$src")/caps.sh" "$T/s/caps.sh"   # the scrub it sources (#2306)
printf '#!/bin/bash\nenv | /usr/bin/grep -q "^SG_" && { echo "leaked: $(env | /usr/bin/grep ^SG_ | tr "\\n" " ")"; exit 1; }; exit 0\n' > "$T/s/test-probe.sh"
o=$(SG_STATE=/live/state SG_FORK_DIR=/live/fork SG_PROFILE=live bash "$T/s/shell-suites.sh" 2>&1) && ok "suites start without inherited SG_*" || ko "SG_* reached a suite: $o"
rm -rf "$T"; exit $fail
