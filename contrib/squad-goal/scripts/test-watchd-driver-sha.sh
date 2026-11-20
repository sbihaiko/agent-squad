#!/bin/bash
# seconds: 2.30
# `watchd.sh start` records the sha the drivers start on (#700): the first field of PUBLISHED in the skill the drivers run
# from goes to $SG_STATE/driver.sha, and `start` removes a stale file when it knows no sha (record_sha in watchd.sh), so
# live-guard.sh can name a publish to another sha as a restart the owner makes. The second start must reach record_sha: a
# start that answers "already running" never calls it and leaves the stale file behind (#2514).
# Offline: a temp skill with a stub env.sh and stub lanes. The test stops the supervisor it started with `stop --force`, and
# the drain waits for a run reader this fixture does not carry, so a draining stop would leave the loop up (#2514).
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME"
# The copy reads the process table through squad.procs (#1508), as the shipped script does: a sandbox refuses the setuid
# table binary, and a copy that fell back to it read its own supervisor as gone. The root comes from git, so a copied or
# relocated contrib tree still points at the repository that holds squad/procs.py.
SG_SQUAD=$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)
[ -n "$SG_SQUAD" ] && [ -f "$SG_SQUAD/squad/procs.py" ] \
  || { echo "FAIL the fixture needs the repository root that holds squad/procs.py (git -C $d rev-parse --show-toplevel)" >&2; exit 1; }
export SG_SQUAD
# watchd.sh sources caps.sh from its own directory, and the fixture copies both side by side: the copy then mirrors the
# shipped layout instead of a path of its own.
/usr/bin/grep -qF '. "$(dirname "$0")/caps.sh"' "$d/watchd.sh" \
  || { echo "FAIL watchd.sh no longer sources caps.sh from its own directory, so the fixture cannot mirror the shipped layout" >&2; exit 1; }
K=$T/skill; S=$K/scripts; st=$T/st; mkdir -p "$S" "$st"
. "$d/driver-tree.sh"; trap 'bash "$S/watchd.sh" stop --force >/dev/null 2>&1; driver_tree_kill "$T"; rm -rf "$T"' EXIT
cp "$d/watchd.sh" "$d/caps.sh" "$S/"; printf 'SG_STATE=%s; SG_PROFILE=\n' "$st" > "$S/env.sh"
printf 'gh_aggregate() { return 1; }\n' > "$S/ghcall.sh"   # offline: the start's loop-bug read has no answer
for f in watch fastloop cqflush; do printf '#!/bin/bash\nsleep 30\n' > "$S/$f.sh"; done
sha=0123456789abcdef0123456789abcdef01234567
printf '%s 2026-10-21T00:00:00Z\n' "$sha" > "$K/PUBLISHED"
bash "$S/watchd.sh" start >/dev/null 2>&1 || ko "watchd did not start"
[ "$(cat "$st/driver.sha" 2>/dev/null)" = "$sha" ] && ok "a start records the PUBLISHED sha in driver.sha" || ko "driver.sha: $(cat "$st/driver.sha" 2>&1)"
# The force stop ends the supervisor; a plain stop only drains since #1662, and this state directory has no run reader, so it
# would wait for ever. A stop that runs past its bound is its own line, never a stale-sha result: the tree is killed and the
# stale check below still runs over a state with no live supervisor.
bash "$S/watchd.sh" stop --force >/dev/null 2>&1
stop_wait=${SG_DRIVER_SHA_STOP_WAIT:-10}; deadline=$(( $(date +%s) + stop_wait ))
while ! bash "$S/watchd.sh" status 2>/dev/null | /usr/bin/grep -q '^watchd not running'; do
  [ "$(date +%s)" -lt "$deadline" ] && { sleep 0.2; continue; }
  ko "the stop did not end the supervisor within ${stop_wait}s"; driver_tree_kill "$T"; break
done
bash "$S/watchd.sh" status 2>/dev/null | /usr/bin/grep -q '^watchd not running' \
  && ok "a stop ends the supervisor the start began" || ko "the supervisor outlived the stop: $(bash "$S/watchd.sh" status 2>&1)"
rm -f "$K/PUBLISHED"; printf 'stale\n' > "$st/driver.sha"
out=$(bash "$S/watchd.sh" start 2>&1) || ko "watchd did not start a second time"
printf '%s\n' "$out" | /usr/bin/grep -q '^watchd started pid=' \
  && ok "a second start reaches record_sha instead of answering already running" || ko "the second start answered: $out"
[ ! -e "$st/driver.sha" ] && ok "a start from a skill with no PUBLISHED and no git keeps no stale driver.sha" || ko "a stale driver.sha survived: $(cat "$st/driver.sha")"
bash "$S/watchd.sh" stop --force >/dev/null 2>&1
[ $fail = 0 ] && echo "test-watchd-driver-sha: all checks green" || { echo "test-watchd-driver-sha: failed"; exit 1; }
