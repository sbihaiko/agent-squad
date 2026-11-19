#!/bin/bash
# seconds: 0.62
# (#847) A quota floor: below SG_GH_FLOOR (default 300) core requests left, reconcile.py skips its pass for that cycle with
# one line and makes no call. A reading that is missing or whose hour has rolled over is no floor, so the pass runs.
# (#1648) GraphQL is a pool of its own, and it floors the pass the same way.
# Offline: a stub gh records every call; the board read is patched to an empty board.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
mkdir -p "$T/bin" "$T/st"
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/calls"
echo '[]'
J
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" SG_STATE="$T/st" SG_REPO=o/r SG_OWNER=o SG_PROJECT=4
unset SG_GH_FLOOR RECONCILE
now=$(date +%s)
run() {   # <remaining> <reset>: one reconcile pass against that reading of the core pool
  rm -f "$T/calls" "$T/st/api.log"
  printf '{"core": {"remaining": %s, "limit": 5000, "reset": %s, "at": %s}}' "$1" "$2" "$now" > "$T/st/gh-ratelimit.json"
  python3 - "$d" <<'PY' 2>&1
import runpy, sys
d = sys.argv[1]; sys.path.insert(0, d)
import issue_state
class _Empty:
    def read_all(self): return {}
issue_state.board_state = lambda **k: _Empty()
sys.argv = [d + "/reconcile.py"]
runpy.run_path(d + "/reconcile.py", run_name="__main__")
PY
}

# 1) 120 core requests left, the hour resets in 30 minutes: below the default floor of 300, so the pass skips
out=$(run 120 $((now + 1800)))
$G -qE "^RECONCILE: skipped this pass: core quota 120 is under SG_GH_FLOOR 300" <<<"$out" && ok "1 below the floor the pass says so, once" || ko "1 no skip line: '$out'"
[ "$($G -c "skipped this pass" <<<"$out")" = 1 ] && ok "1 one line" || ko "1 more than one line: '$out'"
[ ! -s "$T/calls" ] && ok "1 below the floor no call is made" || ko "1 calls made below the floor: $(cat "$T/calls")"

# 2) the floor is SG_GH_FLOOR: 120 left under a floor of 100 is no floor, the pass reads
out=$(SG_GH_FLOOR=100 run 120 $((now + 1800)))
[ -s "$T/calls" ] && ok "2 above a lowered floor the pass reads" || ko "2 the pass did not read: '$out'"

# 3) plenty left: the pass reads
out=$(run 4000 $((now + 1800)))
[ -s "$T/calls" ] && ok "3 with quota left the pass reads" || ko "3 the pass did not read: '$out'"

# 4) a reading whose hour has rolled over describes a window that is gone: no floor
out=$(run 10 $((now - 60)))
[ -s "$T/calls" ] && ok "4 a rolled-over reading is no floor" || ko "4 a rolled-over reading held the pass: '$out'"

# 5) GraphQL is a pool of its own (#1648): 3,000 core requests left and 20 GraphQL left is a floor, because the pass
# reads through GraphQL and a refusal there renews the limit the core reading says nothing about.
rm -f "$T/calls" "$T/st/api.log"
printf '{"core": {"remaining": 3000, "limit": 5000, "reset": %s, "at": %s}, "graphql": {"remaining": 20, "limit": 5000, "reset": %s, "at": %s}}' "$((now + 1800))" "$now" "$((now + 1800))" "$now" > "$T/st/gh-ratelimit.json"
out=$(python3 - "$d" <<'PY' 2>&1
import runpy, sys
d = sys.argv[1]; sys.path.insert(0, d)
import issue_state
class _Empty:
    def read_all(self): return {}
issue_state.board_state = lambda **k: _Empty()
sys.argv = [d + "/reconcile.py"]
runpy.run_path(d + "/reconcile.py", run_name="__main__")
PY
)
$G -qE "^RECONCILE: skipped this pass: graphql quota 20 is under SG_GH_FLOOR 300" <<<"$out" && ok "5 the graphql pool floors the pass on its own" || ko "5 no graphql skip line: '$out'"
[ ! -s "$T/calls" ] && ok "5 no call under the graphql floor" || ko "5 calls made under the graphql floor: $(cat "$T/calls")"

rm -rf "$T"; exit $fail
