#!/bin/bash
# Regression test for sync-to-fork.sh / refresh-from-fork.sh (throwaway skill dir + fork repo, no network). usage: bash test-sync-refresh.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; c() { $G -c user.email=a@b -c user.name=t commit -q "$@"; }
L="$T/live"; F="$T/fork"; export SG_STATE="$T/st" SG_SQUAD_FORK="$F" SG_FORK_BRANCH=local/next; mkdir -p "$L/scripts/profiles" "$SG_STATE" "$F/contrib/squad-goal/scripts/profiles"
echo 'zzzz-never' > "$SG_STATE/sync-deny"
cp "$d/sync-to-fork.sh" "$L/scripts/"; [ -f "$d/refresh-from-fork.sh" ] && cp "$d/refresh-from-fork.sh" "$L/scripts/"
echo skill > "$L/SKILL.md"; echo 'echo a' > "$L/scripts/a.sh"; echo '{"mine":1}' > "$L/scripts/models.json"; echo 'export X=1' > "$L/scripts/profiles/example.sh"; echo 'export PRIVATE=1' > "$L/scripts/profiles/mine.sh"
ss() { bash "$L/scripts/sync-to-fork.sh" 2>&1; }; rf() { bash "$L/scripts/refresh-from-fork.sh" "$@" 2>&1; }
r=$(ss) && ok "first sync into an empty fork" || ko "first sync failed: $r"
cd "$F"; $G init -q -b local/next .; $G add -A; c -m base
echo 'echo b' > "$L/scripts/b.sh"; r=$(ss) && ok "live-only edits sync" || ko "live-ahead sync refused: $r"
$G add -A; c -m live
# fork-ahead: a child lands a new file and an edit on local/next
echo 'echo fork' > "$F/contrib/squad-goal/scripts/fork-gate.sh"; echo 'echo a2' > "$F/contrib/squad-goal/scripts/a.sh"; $G add -A; c -m child
r=$(ss) && ko "sync erased fork-ahead work" || case "$r" in *fork-gate.sh*a.sh*|*a.sh*fork-gate.sh*) ok "fork-ahead files block the sync, named";; *) ko "refused without naming files: $r";; esac
[ -f "$F/contrib/squad-goal/scripts/fork-gate.sh" ] && ok "fork-ahead file still there" || ko "fork-ahead file deleted"
r=$(rf --check) && ko "check passed while live lags the fork" || case "$r" in *fork-gate.sh*) ok "check names the divergent file";; *) ko "check silent: $r";; esac
r=$(rf) && ok "refresh runs" || ko "refresh failed: $r"
[ "$(cat "$L/scripts/fork-gate.sh" 2>/dev/null)" = "echo fork" ] && [ "$(cat "$L/scripts/a.sh")" = "echo a2" ] && ok "refresh brought the files over" || ko "refresh did not bring files over"
[ "$(cat "$L/scripts/models.json")" = '{"mine":1}' ] && [ -f "$L/scripts/profiles/mine.sh" ] && ok "local config preserved (models.json, private profile)" || ko "local config overwritten"
[ -f "$SG_STATE/sync-deny" ] && ok "sync-deny untouched" || ko "sync-deny gone"
r=$(rf --check) && ok "check passes once refreshed" || ko "check fails after refresh: $r"
r=$(ss) && ok "sync works again after the refresh" || ko "sync refused after refresh: $r"
echo 'echo c' > "$F/contrib/squad-goal/scripts/c.sh"; r=$(SG_SYNC_FORCE=1 ss) && [ ! -f "$F/contrib/squad-goal/scripts/c.sh" ] && ok "explicit override syncs over fork-ahead work" || ko "override failed: $r"
# refresh must not erase live-only edits either
echo 'echo c2' > "$F/contrib/squad-goal/scripts/c.sh"; $G add -A; c -m c; echo 'echo live-edit' > "$L/scripts/a.sh"
r=$(rf) && ko "refresh erased a live edit" || { [ "$(cat "$L/scripts/a.sh")" = "echo live-edit" ] && ok "refresh refuses over a live-only edit" || ko "live edit lost"; }
rm -rf "$T"; exit $fail
