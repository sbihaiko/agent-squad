#!/bin/bash
# seconds: 2.30
# `watchd.sh start` records the sha the drivers start on (#700): the first field of PUBLISHED in the skill the drivers run
# from goes to $SG_STATE/driver.sha, so live-guard.sh can name a publish to another sha as a restart the owner makes.
# Offline: a temp skill with a stub env.sh and stub lanes; the test stops the supervisor it started.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME"
K=$T/skill; S=$K/scripts; st=$T/st; mkdir -p "$S" "$st"
. "$d/driver-tree.sh"; trap 'bash "$S/watchd.sh" stop >/dev/null 2>&1; driver_tree_kill "$T"; rm -rf "$T"' EXIT
cp "$d/watchd.sh" "$S/watchd.sh"; printf 'SG_STATE=%s; SG_PROFILE=\n' "$st" > "$S/env.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read has no answer
for f in watch fastloop cqflush; do printf '#!/bin/bash\nsleep 30\n' > "$S/$f.sh"; done
sha=0123456789abcdef0123456789abcdef01234567
printf '%s 2026-10-21T00:00:00Z\n' "$sha" > "$K/PUBLISHED"
bash "$S/watchd.sh" start >/dev/null 2>&1 || ko "watchd did not start"
[ "$(cat "$st/driver.sha" 2>/dev/null)" = "$sha" ] && ok "a start records the PUBLISHED sha in driver.sha" || ko "driver.sha: $(cat "$st/driver.sha" 2>&1)"
bash "$S/watchd.sh" stop >/dev/null 2>&1
rm -f "$K/PUBLISHED"; printf 'stale\n' > "$st/driver.sha"
bash "$S/watchd.sh" start >/dev/null 2>&1 || ko "watchd did not start a second time"
[ ! -e "$st/driver.sha" ] && ok "a start from a skill with no PUBLISHED and no git keeps no stale driver.sha" || ko "a stale driver.sha survived: $(cat "$st/driver.sha")"
bash "$S/watchd.sh" stop >/dev/null 2>&1
[ $fail = 0 ] && echo "test-watchd-driver-sha: all checks green" || { echo "test-watchd-driver-sha: failed"; exit 1; }
