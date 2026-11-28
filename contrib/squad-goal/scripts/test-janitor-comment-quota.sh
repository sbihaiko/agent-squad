#!/bin/bash
# seconds: 1.36
# Rule H reads comments through the metered door and yields once below the quota floor.
# (#3066) SG_SQUAD names the tree this suite sits in, and each pass runs from the fixture folder: the janitor reads squad/
# from SG_SQUAD first and from the caller's cwd only as a fallback. The daily pass clears every SG_* setting and runs from a
# cwd that holds no squad/, so the fallback reached /squad/procs.py and the pass died at import with a traceback.
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"; repo="$(cd "$here/../../.." && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/issue_ledger.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "$*" >> "$T/gh.calls"
case "$*" in
  *issues/2201/comments*) echo '[{"created_at":"2026-01-01T00:00:00Z","body":"the fork suite failed after merging"},{"created_at":"2026-01-02T00:00:00Z","body":"the fork suite failed after merging"},{"created_at":"2026-01-03T00:00:00Z","body":"the fork suite failed after merging"}]';;
  *issues/2202/comments*) echo '[{"created_at":"2026-01-04T00:00:00Z","body":"Card -> Code Complete"}]';;
  *projectsV2*) echo '';;
  *graphql*) echo '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *issues?state=open*) echo '[]';;
esac
J
sed -i '' "s|\$T|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" T="$T"
printf '{"items":[{"n":2201,"st":"Doing","s":"open"},{"n":2202,"st":"Todo","s":"open"}]}\n' > "$T/st/hub-board.json"
run() { ( cd "$T" && SG_SQUAD="$repo" JANITOR_DRY=1 SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_HUB_DIR="$T/st" SG_GH_PASS=janitor python3 -I "$T/s/janitor.py" 2>&1 ); }
: > "$T/st/gh-ratelimit.json"
out=$(run); log="$T/st/api.log"
/usr/bin/grep -q 'issues/2201/comments' "$T/gh.calls" && /usr/bin/grep -q 'REST /\{0,1\}repos/x/y/issues/2201/comments' "$log" \
  && ok "comment read is metered in api.log" || ko "comment read missing or unmetered: $(cat "$T/gh.calls" 2>/dev/null) $(cat "$log" 2>/dev/null)"
printf '%s\n' "$out" | /usr/bin/grep -q 'RED-LOOP #2201: 3 fork-suite REDs' \
  && ok "nonempty fork failures trigger RED-LOOP" || ko "fork history was not parsed: $out"
printf '%s\n' "$out" | /usr/bin/grep -q 'STATE-REGRESSION #2202: card is Todo but its history reached Code Complete' \
  && ok "prior completion triggers STATE-REGRESSION" || ko "completion history was not parsed: $out"
# The floor uses the last core quota observation; an unexpired low reading suppresses the whole comment pass.
python3 - "$T/st/gh-ratelimit.json" <<'PY'
import json, sys, time
with open(sys.argv[1], "w") as f: json.dump({"core": {"remaining": 2, "reset": time.time() + 3600}}, f)
PY
: > "$T/gh.calls"; : > "$log"; out=$(SG_GH_FLOOR=300 run)
[ -z "$(/usr/bin/grep 'issues/2201/comments' "$T/gh.calls")" ] && [ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'JANITOR skipped comment pass')" = 1 ] \
  && ok "low quota skips comment reads with one line" || ko "floor did not skip exactly once: $out; $(cat "$T/gh.calls")"
[ "$fail" = 0 ] && echo PASS || { echo FAILED; exit 1; }
