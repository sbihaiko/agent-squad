#!/bin/bash
# Regression test for the hub footer ceilings: loop_caps() must find the squad package from a hub.py that runs outside the repository
# (the live copy under ~/.claude/skills, where parents[3] is ~/.claude), and a failure to find it must be logged once, never swallowed
# (no network, no server). usage: bash test-hub-caps.sh
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
live="$T/home/.claude/skills/squad-goal/scripts"; mkdir -p "$live" "$T/bare"; cp "$d/hub.py" "$d/panes.js" "$live/"   # hub.py reads panes.js beside itself

# probe <env...> : load the copied hub.py in a fresh process, call loop_caps() twice; stdout is one JSON line, stderr is the hub's log.
cat >"$T/probe.py" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("hub", sys.argv[1]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
a = hub.loop_caps(); hub.loop_caps()
print(json.dumps(None if a is None else {"runs": a["runs"], "children": a["children"]}))
PY
probe() { # <checkout> <sg_squad or ""> : stdout to $T/out, stderr to $T/err
  ( cd "$T/bare" && env -u PYTHONPATH -u SG_SQUAD ${2:+SG_SQUAD="$2"} SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$1" \
      SG_HUB_DIR="$T/hub" SG_STATE="$T/st" HOME="$T/home" python3 "$T/probe.py" "$live/hub.py" >"$T/out" 2>"$T/err" )
}

# 1. the live copy, SG_SQUAD unset, SG_CHECKOUT the repository: the ceilings come from the checkout
probe "$repo" ""
[ "$(cat "$T/out")" = '{"runs": 10, "children": 20}' ] && ok "checkout fallback gives runs 10 and children 20" || ko "checkout fallback: got $(cat "$T/out") ($(head -2 "$T/err" | tr '\n' ' '))"

# 2. SG_SQUAD names a folder with no squad package: it is skipped, the checkout still resolves
probe "$repo" "$T/bare"
[ "$(cat "$T/out")" = '{"runs": 10, "children": 20}' ] && ok "a dead SG_SQUAD falls through to the checkout" || ko "dead SG_SQUAD: got $(cat "$T/out") ($(head -2 "$T/err" | tr '\n' ' '))"

# 3. nothing resolves: no ceiling is guessed, and two calls log exactly one line that names the exception
probe "$T/bare" ""
[ "$(cat "$T/out")" = 'null' ] && ok "no source resolves: no ceiling" || ko "no source: got $(cat "$T/out")"
n=$(/usr/bin/grep -c . "$T/err"); [ "$n" = 1 ] && ok "one log line for two failed calls" || ko "log lines: $n ($(tr '\n' ' ' <"$T/err"))"
/usr/bin/grep -q "ModuleNotFoundError" "$T/err" && ok "the log line names the exception" || ko "the log line never names the exception ($(tr '\n' ' ' <"$T/err"))"

exit $fail
