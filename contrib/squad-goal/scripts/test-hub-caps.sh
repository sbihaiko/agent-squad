#!/bin/bash
# seconds: 3.33
# Regression test for the hub footer ceilings: loop_caps() must find the squad package from a hub.py that runs outside the repository
# (the live copy under ~/.claude/skills, where parents[3] is ~/.claude), and a failure to find it must be logged once, never swallowed
# (no network, no server). usage: bash test-hub-caps.sh
# mirrors: hub.py hub_theme_core.py hub_theme_settings_limits.py hub_theme_settings_communication.py hub_theme_now.py hub_theme_settings_loop.py hub_theme_profiler.py hub_theme_trip.py hub_theme_runs.py hub_theme_stats.py hub_theme_board.py hub_theme_prs.py hub_theme_flow.py hub_theme_board_build.py hub_theme_promote.py hub_theme_page.py hub_theme_settings_subscriptions.py hub_theme_settings_models.py hub_theme_settings_board.py hub_theme_settings_access.py hub_theme_settings_personas.py procscan.py hubstream.py hub-stream.js web-tokens.css chainhealth.py grokmark.py provkey.py hub_profiler.py hub-profiler.js hub-profiler.css modelreg.py registry.json router_envelope.py agyquota.py board-lanes.js board-lanes.css run-sheet.js run-sheet.css hub-switcher.js board_columns.py board_fast.py board_history.py board_flow_history.py board_cfd.py board_leadtime.py issue_state.py issue_ledger.py board_lag.py ghread.py ghthrottle.py hub-mobile.html hub-mobile-search.js hub-mobile-gesture.js hub-mobile-chart.js hub-mobile-loop.js hub-mobile-link.js hub-mobile-run.js hub-mobile-usage.js hub-mobile-specs.js hub-mobile-sw.js board-filter.js board-definition.json run-states.json models_chains.py steprec.py specstall.py revformat.py ci_state.py board_priority.py board_wait.py priority_backfill.py priority_rank.py siblings.py
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
live="$T/home/.claude/skills/squad-goal/scripts"; mkdir -p "$live" "$T/bare"; for f in $(sed -n 's/^# mirrors: //p' "$0" | head -n 1); do cp "$d/$f" "$live/"; done   # the copy is the list the mirrors line states

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

# 5. the copy is the list the `# mirrors:` line states, and that list holds every local module hub.py imports or loads by path (#1108, #2011): the gate
# reads the same line to pick this suite whenever a listed file changes, so a new import that the list misses fails here.
imported=$(python3 - "$d/hub.py" "$d" <<'PY'
import ast, os, sys
src, d = sys.argv[1], sys.argv[2]
mods = set()
for n in ast.walk(ast.parse(open(src).read())):
    if isinstance(n, ast.Import): mods |= {a.name.split(".")[0] for a in n.names}
    elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0: mods.add(n.module.split(".")[0])
    elif isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "spec_from_file_location":   # a module loaded by its file path (#2011)
        mods |= {c.value[:-3] for a in n.args for c in ast.walk(a) if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.endswith(".py")}
print(" ".join(sorted(m + ".py" for m in mods if os.path.isfile(os.path.join(d, m + ".py")))))
PY
)
mirrored=$(sed -n 's/^# mirrors: //p' "$0" | head -n 1 | tr ' ' '\n' | sort | tr '\n' ' ')
for f in $imported; do case " $mirrored" in *" $f "*) ;; *) ko "hub.py imports $f, which the mirror statement does not name";; esac; done
[ -n "$mirrored" ] && ok "the mirror statement names every local module hub.py imports"

exit $fail
