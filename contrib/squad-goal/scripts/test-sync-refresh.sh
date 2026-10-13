#!/bin/bash
# Regression test for sync-to-fork.sh / refresh-from-fork.sh (throwaway skill dir + fork repo, no network). usage: bash test-sync-refresh.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; c() { $G -c user.email=a@b -c user.name=t commit -q "$@"; }
L="$T/live"; F="$T/fork"; export SG_STATE="$T/st" SG_SQUAD_FORK="$F" SG_FORK_BRANCH=main; mkdir -p "$L/scripts/profiles" "$SG_STATE" "$F/contrib/squad-goal/scripts/profiles"
echo 'zzzz-never' > "$SG_STATE/sync-deny"
cp "$d/sync-to-fork.sh" "$L/scripts/"; [ -f "$d/refresh-from-fork.sh" ] && cp "$d/refresh-from-fork.sh" "$L/scripts/"
echo skill > "$L/SKILL.md"; echo 'echo a' > "$L/scripts/a.sh"; echo '{"mine":1}' > "$L/scripts/models.json"; echo 'export X=1' > "$L/scripts/profiles/example.sh"; echo 'export PRIVATE=1' > "$L/scripts/profiles/mine.sh"
ss() { bash "$L/scripts/sync-to-fork.sh" 2>&1; }; rf() { bash "$L/scripts/refresh-from-fork.sh" "$@" 2>&1; }
r=$(ss) && ok "first sync into an empty fork" || ko "first sync failed: $r"
cd "$F"; $G init -q -b main .; $G add -A; c -m base
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
# generated and backup files are not shipped content: after a refresh, --check is clean even with them on both sides
SG_REFRESH_FORCE=1 rf >/dev/null; mkdir -p "$L/scripts/__pycache__" "$F/contrib/squad-goal/scripts/__pycache__"
echo x > "$L/scripts/__pycache__/a.cpython-314.pyc"; echo y > "$L/scripts/models.json.bak-1"; echo z > "$L/scripts/old.bak"
echo p > "$F/contrib/squad-goal/scripts/__pycache__/b.cpython-314.pyc"; echo q > "$F/contrib/squad-goal/scripts/stale.pyc"; echo r > "$F/contrib/squad-goal/scripts/b.sh.bak-2"; $G add -Af; c -m junk
r=$(rf --check) && ok "check ignores __pycache__, *.pyc, *.bak*" || ko "check counts generated/backup files: $r"
r=$(rf) && [ ! -e "$L/scripts/stale.pyc" ] && [ ! -e "$L/scripts/b.sh.bak-2" ] && [ ! -e "$L/scripts/__pycache__/b.cpython-314.pyc" ] && ok "copy skips generated/backup files" || ko "copy brought generated/backup files: $r"
# files the fork does not ship are out of scope: --check answers "is the live skill current with local/next?", and
# a live-only file (docs/, patches/) is not an answer to that - it is never copied, so it can never converge (#1841)
mkdir -p "$L/docs" "$L/patches"
echo plan > "$L/docs/hub-cost-speed-plan.md"; echo readme > "$L/patches/README.md"
r=$(rf --check) && ok "check ignores files the fork does not ship" || ko "check flags live-only files: $r"
r=$(rf); case "$r" in *"already current"*) ok "refresh reports already current with live-only files present";; *) ko "refresh not idempotent: $r";; esac
[ "$(cat "$L/docs/hub-cost-speed-plan.md")" = plan ] && [ "$(cat "$L/patches/README.md")" = readme ] && ok "live-only files untouched by the refresh" || ko "refresh damaged a live-only file"
r=$(rf --check) && ok "check stays clean after a second refresh" || ko "second check dirty: $r"
# the fork still owns what it ships: a file only the fork carries is named by --check
echo 'echo gate' > "$F/contrib/squad-goal/scripts/new-gate.sh"; $G add -A; c -m newgate
r=$(rf --check) && ko "check passed while the fork ships a file live lacks" || case "$r" in *new-gate.sh*) ok "check still names a fork-only file";; *) ko "fork-only file not named: $r";; esac
# The executable bit is part of what the fork ships. A live file whose BYTES already match is never copied, so before this a
# mode-only divergence survived every refresh: autobug.sh, autofrontier.sh, autohunt.sh and five others sat 644 in the live
# skill while the fork had them 755, and each pass the loop ran by name printed `Permission denied` into watch.log while the
# cycle kept going -- the frontier just read as idle. Bytes alone are not the shipped content.
r=$(rf) >/dev/null; chmod +x "$F/contrib/squad-goal/scripts/new-gate.sh"; $G add -A; c -m exec
chmod -x "$L/scripts/new-gate.sh"
r=$(rf --check) && ko "check passed while the executable bit differs" || case "$r" in *new-gate.sh*) ok "check names a mode-only divergence";; *) ko "mode divergence not named: $r";; esac
r=$(rf) && [ -x "$L/scripts/new-gate.sh" ] && ok "refresh restores the executable bit" || ko "refresh left the script unrunnable: $r"
r=$(rf --check) && ok "check clean once the mode matches" || ko "check dirty after the mode fix: $r"

rm -rf "$T"; exit $fail
