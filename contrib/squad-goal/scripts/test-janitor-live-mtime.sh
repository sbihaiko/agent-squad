#!/bin/bash
# seconds: 2.68
# Rule M (#659): a file under the live skill directory whose mtime changed while no publish ran raises LIVE-MTIME in alerts.log.
# live-guard.sh stamps $SG_STATE/live-publish.stamp and every publish writes the live PUBLISHED file; a change in a pass that follows either is the publish's own.
# Stubbed gh and board; a temp state dir and a temp live skill. usage: bash test-janitor-live-mtime.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/live/scripts"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
printf 'skill\n' > "$T/live/SKILL.md"; printf '{}\n' > "$T/live/scripts/models.json"; touch -t 202001010000 "$T/live/SKILL.md" "$T/live/scripts/models.json"
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" SG_LIVE_DIR="$T/live" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" >/dev/null 2>&1; }
alerts() { cat "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -c 'LIVE-MTIME'; }
pass env; [ "$(alerts)" = 0 ] && ok "the first pass takes the snapshot and raises nothing" || ko "the first pass alerted"
printf '{"models": "rewritten"}\n' > "$T/live/scripts/models.json"
pass env; [ "$(alerts)" = 1 ] && ok "a changed file with no publish raises LIVE-MTIME" || ko "no alert for the changed file ($(alerts))"
/usr/bin/grep 'LIVE-MTIME' "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -q 'scripts/models.json' && ok "the alert names the file" || ko "the alert does not name the file"
pass env; [ "$(alerts)" = 1 ] && ok "an unchanged live copy raises nothing again" || ko "a pass with no change alerted ($(alerts))"
printf 'skill v2\n' > "$T/live/SKILL.md"; touch "$T/st/live-publish.stamp"
pass env; [ "$(alerts)" = 1 ] && ok "a change a publish made raises nothing" || ko "a publish was read as a stray write ($(alerts))"
# A hand publish (publish-live.sh) writes the live PUBLISHED file and no stamp in the state dir (#727): its change is the
# publish's own too.
sleep 1; printf 'skill v3\n' > "$T/live/SKILL.md"; printf 'abc 2026-01-01T00:00:00Z\n' > "$T/live/PUBLISHED"
pass env; [ "$(alerts)" = 1 ] && ok "a hand publish that writes PUBLISHED raises nothing" || ko "a hand publish was read as a stray write ($(alerts))"
# An import writes __pycache__/<module>.cpython-312.pyc under the live copy, and an editor writes .DS_Store. Neither is a
# change to the loop's code (#661): they raise nothing, whether they come or go.
mkdir -p "$T/live/scripts/__pycache__"
printf 'cache\n' > "$T/live/scripts/__pycache__/models.cpython-312.pyc"; printf 'cache\n' > "$T/live/scripts/models.pyo"; printf 'meta\n' > "$T/live/scripts/.DS_Store"
pass env; [ "$(alerts)" = 1 ] && ok "a bytecode cache, a .pyo and .DS_Store raise nothing" || ko "a bytecode cache raised LIVE-MTIME ($(alerts))"
rm "$T/live/scripts/__pycache__/models.cpython-312.pyc"
pass env; [ "$(alerts)" = 1 ] && ok "a bytecode cache that goes raises nothing" || ko "a removed bytecode cache raised LIVE-MTIME ($(alerts))"
# A snapshot taken before the fix recorded the cache file. Its absence on the next pass is no removal either, so the fix
# raises nothing once over the file already under the live copy (#661).
python3 - "$T/st/live-mtime.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d["files"]["scripts/__pycache__/models.cpython-312.pyc"] = 1.0
json.dump(d, open(sys.argv[1], "w"))
PY
pass env; [ "$(alerts)" = 1 ] && ok "a snapshot that recorded a bytecode cache raises nothing when it is gone" || ko "an old cache entry alerted ($(alerts))"
printf 'print(1)\n' > "$T/live/scripts/real.py"
pass env; [ "$(alerts)" = 2 ] && ok "a real script changing still raises LIVE-MTIME" || ko "a real script change was ignored ($(alerts))"
rm "$T/live/SKILL.md"
pass env; [ "$(alerts)" = 3 ] && ok "a removed file with no publish raises LIVE-MTIME" || ko "no alert for the removed file ($(alerts))"
pass env JANITOR_DRY=1; printf 'x\n' > "$T/live/new.md"
pass env JANITOR_DRY=1; pass env JANITOR_DRY=1; [ "$(alerts)" = 5 ] && ok "a dry pass keeps no snapshot, so it reads the same change again" || ko "dry passes: $(alerts)"
exit $fail
