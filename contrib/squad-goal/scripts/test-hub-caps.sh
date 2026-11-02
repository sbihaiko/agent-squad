#!/bin/bash
# Regression test for the hub footer ceilings: loop_caps() must find the squad package from a hub.py that runs outside the repository
# (the live copy under ~/.claude/skills, where parents[3] is ~/.claude), and a failure to find it must be logged once, never swallowed
# (no network, no server). usage: bash test-hub-caps.sh
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
live="$T/home/.claude/skills/squad-goal/scripts"; mkdir -p "$live" "$T/bare"; cp "$d/hub.py" "$d/hubstream.py" "$d/hub-stream.js" "$d/web-tokens.css" "$d/chainhealth.py" "$d/provkey.py" "$d/session_send.py" "$d/session_card.py" "$d/sessions.py" "$d/modelreg.py" "$d/registry.json" "$d/router_envelope.py" "$d/agyquota.py" "$d/board-lanes.js" "$d/board-lanes.css" "$d/run-sheet.js" "$d/run-sheet.css" "$d/hub-switcher.js" "$d/board_columns.py" "$d/board_fast.py" "$d/board_history.py" "$d/board_flow_history.py" "$d/board_burndown.py" "$d/board_cfd.py" "$d/board_leadtime.py" "$d/issue_state.py" "$d/issue_ledger.py" "$d/board_lag.py" "$d/ghread.py" "$d/ghthrottle.py" "$d/hub-mobile.html" "$d/hub-mobile-gesture.js" "$d/hub-mobile-move.js" "$d/hub-question-card.js" "$d/hub-mobile-run.js" "$d/hub-mobile-sw.js" "$d/board-filter.js" "$d/board-definition.json" "$d/hub-overlay.js" "$d/vendor-html2canvas-1.4.1.min.js" "$live/"   # hub.py reads chainhealth.py, ghread.py, provkey.py, router_envelope.py, agyquota.py, board_columns.py, board_flow_history.py, board_cfd.py, board_leadtime.py, issue_state.py, board_lag.py, the swimlane and run sheet assets, the pinned board-definition.json and the vendored capture library beside itself

# probe <env...> : load the copied hub.py in a fresh process, call loop_caps() twice; stdout is one JSON line, stderr is the hub's log.
# Case 3 is a proof of the resolution and of nothing else: the gate runs every suite under `uv run`, whose PATH puts this
# checkout's .venv first, and that venv holds `squad` as an editable install. `load_squad` takes a package only from the root it
# asked, so the ambient one answers for no root and case 3 reads no ceiling -- the suite failed here in the full gate at c3c6274
# because the hub took it (#452).
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

# The ceilings the checkout resolves are the fixture's, never a number written here: a project settings file in the checkout
# (docs/squad/defaults.yaml) narrows them, and this suite proves the resolution, not the values (ADR-0018).
want=$(cd "$repo" && env -u PYTHONPATH python3 -c 'import json; from squad import caps; r = caps.resolve("."); print(json.dumps({"runs": r["runs"], "children": r["children"]}))' 2>/dev/null)
[ -n "$want" ] || ko "the checkout resolves no ceilings"

# 1. the live copy, SG_SQUAD unset, SG_CHECKOUT the repository: the ceilings come from the checkout
probe "$repo" ""
[ "$(cat "$T/out")" = "$want" ] && ok "checkout fallback gives the ceilings the checkout resolves ($want)" || ko "checkout fallback: got $(cat "$T/out"), want $want ($(head -2 "$T/err" | tr '\n' ' '))"

# 2. SG_SQUAD names a folder with no squad package: it is skipped, the checkout still resolves
probe "$repo" "$T/bare"
[ "$(cat "$T/out")" = "$want" ] && ok "a dead SG_SQUAD falls through to the checkout" || ko "dead SG_SQUAD: got $(cat "$T/out"), want $want ($(head -2 "$T/err" | tr '\n' ' '))"

# 3. nothing resolves: no ceiling is guessed, and two calls log exactly one line that names the exception
probe "$T/bare" ""
[ "$(cat "$T/out")" = 'null' ] && ok "no source resolves: no ceiling" || ko "no source: got $(cat "$T/out")"
n=$(/usr/bin/grep -c . "$T/err"); [ "$n" = 1 ] && ok "one log line for two failed calls" || ko "log lines: $n ($(tr '\n' ' ' <"$T/err"))"
/usr/bin/grep -q "ModuleNotFoundError" "$T/err" && ok "the log line names the exception" || ko "the log line never names the exception ($(tr '\n' ' ' <"$T/err"))"

# 4. the copy carries what the page needs too: a live hub.py serves its board only with the column table beside it
cat >"$T/board.py" <<'PY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("hub", sys.argv[1]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
m = re.search(r"const COLNAME=(\{.*?\});", hub.PAGE)
print(m.group(1) if m else "(the page names no column)")
PY
( cd "$T/bare" && env -u PYTHONPATH -u SG_SQUAD SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T/bare" \
    SG_HUB_DIR="$T/hub2" SG_STATE="$T/st2" HOME="$T/home" python3 "$T/board.py" "$live/hub.py" >"$T/names" 2>"$T/names.err" )
/usr/bin/grep -q '"unscheduled":"Backlog"' "$T/names" && ok "the live copy names its board columns from the table beside it" \
  || ko "the live copy names no board column: $(cat "$T/names") ($(head -2 "$T/names.err" | tr '\n' ' '))"

exit $fail
