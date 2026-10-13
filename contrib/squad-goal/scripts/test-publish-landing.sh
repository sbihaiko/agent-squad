#!/bin/bash
# Regression test for publish-landing.sh (#31): a green landing publishes what it changed -- the live skill follows the fork, and the
# view restarts only when a file it serves changed. Throwaway live skill + fork repo + a stub hubctl.sh, no network, no real hub.
# usage: bash test-publish-landing.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; c() { $G -c user.email=a@b -c user.name=t commit -q "$@"; }; C=contrib/squad-goal
L="$T/live"; F="$T/fork"; export SG_STATE="$T/st" SG_SQUAD_FORK="$F" SG_FORK_BRANCH=main HOME="$T/home" HUBCALLS="$T/hubcalls" HUBFAIL="$T/hubfail"
mkdir -p "$SG_STATE/auto" "$F/$C/scripts/profiles" "$HOME"; : > "$HUBCALLS"
S="$F/$C/scripts"
cp "$d/publish-landing.sh" "$d/refresh-from-fork.sh" "$S/" 2>/dev/null
# the stub records that the view was asked to restart, and whether the landing lock was held while it ran
printf '#!/bin/bash\necho "$* lock=$([ -d "$SG_STATE/auto/forkclose.lock" ] && echo held || echo free)" >> "$HUBCALLS"\n[ -e "$HUBFAIL" ] && exit 1\necho http://127.0.0.1:7700/stub\n' > "$S/hubctl.sh"
echo skill > "$F/$C/SKILL.md"; for f in a.sh hub.py hubmux.py panes.js address.sh; do echo "v1 $f" > "$S/$f"; done; echo 'export X=1' > "$S/profiles/example.sh"
cd "$F"; $G init -q -b main .; $G add -A; c -m base
cp -R "$F/$C" "$L"; echo '{"mine":1}' > "$L/scripts/models.json"
( cd "$L" && find . -type f ! -name models.json | sed 's|^\./||' | sort | while read -r f; do echo "$(shasum -a 256 "$f" | cut -d' ' -f1) $f"; done ) > "$SG_STATE/fork-base"   # what the last sync left: live is not ahead of the fork
green() { h=$($G -C "$F" rev-parse --short=9 HEAD); echo log > "$SG_STATE/auto/basetest-$h"; }   # the base the landing left, recorded green by the pass that landed it
land() { $G -C "$F" add -A; c -m "$1"; [ "${2:-green}" = green ] && green; }
pub() { bash "$L/scripts/publish-landing.sh" 2>&1; }
chk() { bash "$L/scripts/refresh-from-fork.sh" --check 2>&1; }
calls() { wc -l < "$HUBCALLS" | tr -d ' '; }
cp "$S/publish-landing.sh" "$S/refresh-from-fork.sh" "$S/hubctl.sh" "$L/scripts/" 2>/dev/null; green
[ -f "$d/publish-landing.sh" ] && ok "publish-landing.sh exists" || ko "publish-landing.sh is missing"

# a landing that changed nothing the live copy ships: nothing to do, the view is left alone
r=$(pub) && [ "$(calls)" = 0 ] && ok "a landed tree the live copy already holds changes nothing and restarts nothing" || ko "no-op publish (calls $(calls)): $r"

# a view change restarts the view; the check that compares the two names neither afterwards
echo "v2 hub.py" > "$S/hub.py"; echo "v2 a.sh" > "$S/a.sh"; land view
r=$(pub) && ok "publish runs after a green landing that touched a shipped file" || ko "publish failed: $r"
[ "$(cat "$L/scripts/hub.py")" = "v2 hub.py" ] && [ "$(cat "$L/scripts/a.sh")" = "v2 a.sh" ] && ok "the live copy follows the fork" || ko "live copy did not follow the fork"
r2=$(chk) && ok "the check that compares them is clean" || ko "check still names files: $r2"
[ "$(calls)" = 1 ] && grep -q -- '--restart' "$HUBCALLS" && ok "a view change restarts the view" || ko "view not restarted once (calls: $(cat "$HUBCALLS"))"
case "$r" in *http://127.0.0.1:7700/stub*) ok "the restart prints the view's URL";; *) ko "no URL in the output: $r";; esac
grep -q 'lock=held' "$HUBCALLS" && ok "no landing pass can start while the step runs (the landing lock is held)" || ko "landing lock not held during the step: $(cat "$HUBCALLS")"
[ ! -d "$SG_STATE/auto/forkclose.lock" ] && ok "the landing lock is released afterwards" || ko "the landing lock is left behind"

# once per landing, idempotent
r=$(pub) && [ "$(calls)" = 1 ] && ok "a second run changes nothing and does not restart the view again" || ko "second run not idempotent (calls $(calls)): $r"

# a change that touches nothing the view serves leaves the view alone
echo "v3 a.sh" > "$S/a.sh"; land other
r=$(pub) && [ "$(cat "$L/scripts/a.sh")" = "v3 a.sh" ] && ok "a non-view change is published" || ko "non-view change not published: $r"
[ "$(calls)" = 1 ] && ok "a change that touches nothing the view serves leaves it alone" || ko "view restarted for a non-view change: $(cat "$HUBCALLS")"
for f in hubmux.py panes.js address.sh; do
  echo "v4 $f" > "$S/$f"; land "view $f"; n=$(calls); r=$(pub) || ko "publish failed on $f: $r"
  [ "$(calls)" = $((n+1)) ] && ok "$f is served by the view: its change restarts it" || ko "$f changed, the view was not restarted"; done

# a live-only file refuses the refresh by name and changes nothing
n=$(calls); echo mine > "$L/scripts/live-only.sh"; echo "v5 hub.py" > "$S/hub.py"; land fork5
r=$(pub) && ko "publish ran over a live-only file" || case "$r" in *scripts/live-only.sh*) ok "a live-only file refuses the step, by name";; *) ko "refused without naming the file: $r";; esac
[ "$(cat "$L/scripts/hub.py")" = "v2 hub.py" ] && ok "a refusal changes nothing in the live copy" || ko "live hub.py changed under a refusal"
[ "$(cat "$L/scripts/live-only.sh")" = mine ] && [ "$(calls)" = "$n" ] && ok "the live-only file survives and the view is not restarted" || ko "refusal left a mess (calls $(calls) vs $n)"
r=$(pub) && ko "the refusal did not repeat" || case "$r" in *scripts/live-only.sh*) ok "the refusal stands, by name, until the live file is dealt with";; *) ko "second refusal unnamed: $r";; esac
rm -f "$L/scripts/live-only.sh"; r=$(pub) && [ "$(cat "$L/scripts/hub.py")" = "v5 hub.py" ] && [ "$(calls)" = $((n+1)) ] && ok "once the live-only file is gone the landing publishes" || ko "publish after the fix failed (calls $(calls)): $r"

# a landing pass mid-flight: the step waits, changes nothing, and never takes the pass's lock
n=$(calls); echo "v6 a.sh" > "$S/a.sh"; land fork6
sleep 60 & hold=$!; mkdir "$SG_STATE/auto/forkclose.lock"; echo $hold > "$SG_STATE/auto/forkclose.lock/pid"
r=$(pub); rc=$?
[ "$(cat "$L/scripts/a.sh")" = "v3 a.sh" ] && [ "$(calls)" = "$n" ] && ok "mid-flight landing: nothing is published" || ko "published while a landing pass ran: $r"
[ "$(cat "$SG_STATE/auto/forkclose.lock/pid" 2>/dev/null)" = "$hold" ] && ok "the running pass keeps its lock" || ko "the step took over a live landing lock"
case "$r" in *[Ww][Aa][Ii][Tt]*|*mid-flight*) ok "the step says it waits";; *) ko "silent wait: $r";; esac
kill $hold 2>/dev/null; wait $hold 2>/dev/null
r=$(pub) && [ "$(cat "$L/scripts/a.sh")" = "v6 a.sh" ] && ok "a stale landing lock (the pass died) does not block the step" || ko "stale lock blocked the step: $r"

# a base the landing did not record green is not published
echo "v7 a.sh" > "$S/a.sh"; land red notgreen
r=$(pub); [ "$(cat "$L/scripts/a.sh")" = "v6 a.sh" ] && ok "a head with no green gate is not published" || ko "published an ungated head: $r"
green; r=$(pub) && [ "$(cat "$L/scripts/a.sh")" = "v7 a.sh" ] && ok "once the head is recorded green it is published" || ko "green head not published: $r"
# green is a tree: the remote merge of the gated tree (a new sha, the same tree) is the same landing
echo "v8 a.sh" > "$S/a.sh"; $G -C "$F" add -A; c -m v8; green; $G -C "$F" -c user.email=a@b -c user.name=t commit -q --allow-empty -m "remote merge of the gated tree"   # a new sha, the tree the gate saw
r=$(pub) && [ "$(cat "$L/scripts/a.sh")" = "v8 a.sh" ] && ok "a head with the tree of a green base is published" || ko "same-tree head not published: $r"

# a view restart that failed is retried by the next run, though the live copy already follows the fork
n=$(calls); touch "$HUBFAIL"; echo "v9 hub.py" > "$S/hub.py"; land fork9
r=$(pub) && ko "a failed view restart passed" || case "$r" in *"restart failed"*) ok "a failed view restart is reported";; *) ko "failure not reported: $r";; esac
[ "$(cat "$L/scripts/hub.py")" = "v9 hub.py" ] && ok "the live copy was published all the same" || ko "live copy not published"
rm -f "$HUBFAIL"; r=$(pub) && [ "$(calls)" = $((n+2)) ] && ok "the next run retries the restart" || ko "restart not retried (calls $(calls) vs $n): $r"
r=$(pub) && [ "$(calls)" = $((n+2)) ] && ok "and then stops" || ko "restart repeated"

rm -rf "$T"; exit $fail
