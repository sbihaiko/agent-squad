#!/bin/bash
# (#847) The unmetered REST spenders the audit ranked top log every call to api.log, through the door's own log writer:
# the board seam (issue_state.py, the hub board pass and the autobug and autofrontier reads) and reconcile.py's reads.
# Offline: a stub gh answers every call with []. The calls are the lines of $T/calls. Output contracts are not in scope here.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
mkdir -p "$T/bin" "$T/st"
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/calls"
echo '[]'
J
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" SG_STATE="$T/st" SG_REPO=o/r SG_OWNER=o SG_PROJECT=4 GH_GET_TEST=1
export -n GH_GET_TEST
unset SG_GH_PASS

# 1) a board seam read is one line in api.log, with its route
python3 - "$d" "$T/st" <<'PY' >/dev/null 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import issue_state
b = issue_state.BoardBinding("o", 4, "o/r", kind="users", state_dir=sys.argv[2])
b.api("repos/o/r/issues/7")
PY
$G -q "repos/o/r/issues/7" "$T/st/api.log" 2>/dev/null && ok "1 a board seam read is logged to api.log" || ko "1 the board seam read is not in api.log: $(cat "$T/st/api.log" 2>/dev/null)"

# 2) a reconcile read is one line in api.log, through the door. The board read is patched to an empty board, so the
# pass reaches its own REST reads (branches, bug issues) and nothing else.
rm -f "$T/calls" "$T/st/api.log"
python3 - "$d" <<'PY' >"$T/recon.out" 2>&1
import runpy, sys
d = sys.argv[1]; sys.path.insert(0, d)
import issue_state
class _Empty:
    def read_all(self): return {}
issue_state.board_state = lambda **k: _Empty()
sys.argv = [d + "/reconcile.py"]
runpy.run_path(d + "/reconcile.py", run_name="__main__")
PY
$G -q "repos/o/r/branches" "$T/st/api.log" 2>/dev/null && ok "2 a reconcile read is logged to api.log" || ko "2 the reconcile read is not in api.log: $(cat "$T/st/api.log" 2>/dev/null) / $(cat "$T/recon.out")"

rm -rf "$T"; exit $fail
