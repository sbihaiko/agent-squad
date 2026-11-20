#!/bin/bash
# seconds: 2.08
# Regression test for wtclean.sh's run-record archive (#2588): a child tree holds run folders, and removing the tree took
# every record.jsonl with it, so the hub's trip bar forgot each delivery whose tree was cleaned. A removal now copies each
# run folder's record.jsonl and request.txt to $SG_STATE/run-archive/<run>/ first; a copy that fails keeps the tree under one
# WT KEPT line, and an archive folder older than SG_RUN_ARCHIVE_DAYS goes. usage: bash test-wtclean-archive.sh [path/to/wtclean.sh]
here=$(cd "$(dirname "$0")" && pwd -P); src=${1:-$here/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export PYTEST_DEBUG_TEMPROOT="$T/pytmp"   # hermetic: no pass below may prune the real pytest temp trees (#1581)
mkdir -p "$T/s" "$T/st"; cp "$src" "$T/s/wtclean.sh"
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x SG_BASE=main\n' "$T/co" "$T/st" > "$T/s/env.sh"
export SG_LSOF=${SG_LSOF:-$(PATH="$PATH:/usr/sbin:/usr/bin" command -v lsof 2>/dev/null)}   # wtclean.sh reads every live cwd through it
G=/usr/bin/git; $G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
echo runs/ >> .git/info/exclude   # a run folder inside a child tree is ignored, so the tree reads clean (as it does in the loop)
mkdir -p worktrees/child
# child <tree> <run>: a landed child tree that holds one ended run folder, with the record and the request a run writes
child() { $G worktree add -q --detach "worktrees/child/$1" HEAD; mkdir -p "worktrees/child/$1/runs/$2"
  printf '%s\n' '{"event": "run_start", "ts": 1.0}' '{"event": "verdict", "verdict": "completed", "ts": 2.0}' > "worktrees/child/$1/runs/$2/record.jsonl"
  echo 'Refs #7' > "worktrees/child/$1/runs/$2/request.txt"; }
child gui-7 run-20261101-010101-aaaaaa
rec=$(cat worktrees/child/gui-7/runs/run-20261101-010101-aaaaaa/record.jsonl)
out=$(bash "$T/s/wtclean.sh" 2>&1)
A="$T/st/run-archive/run-20261101-010101-aaaaaa"
[ ! -d worktrees/child/gui-7 ] && ok "A1: the landed child tree goes" || ko "A1: the child tree stayed: $out"
[ -f "$A/record.jsonl" ] && [ "$(cat "$A/record.jsonl")" = "$rec" ] && ok "A1: the run record lands in the archive before the tree goes" || ko "A1: no archived record, or another record: $(ls "$T/st/run-archive" 2>&1)"
[ "$(cat "$A/request.txt" 2>/dev/null)" = "Refs #7" ] && ok "A1: the run request lands with the record" || ko "A1: no archived request"
# A2: a copy that fails keeps the tree, and one WT KEPT line names the reason
rm -rf "$T/st/run-archive"; : > "$T/st/run-archive"   # the archive root is a file: mkdir -p fails
child gui-8 run-20261101-010202-bbbbbb
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d worktrees/child/gui-8 ] && ok "A2: a tree whose record did not reach the archive stays" || ko "A2: the tree went with its record unarchived: $out"
[ "$(printf '%s\n' "$out" | grep -c 'WT KEPT.*gui-8')" = 1 ] && case "$out" in *"gui-8:"*"run record"*) ok "A2: one WT KEPT line names the archive failure";; *) ko "A2: the WT KEPT line has no reason: $out";; esac || ko "A2: not one WT KEPT line for gui-8: $out"
# A3: an archive folder older than SG_RUN_ARCHIVE_DAYS goes, a fresh one stays
rm -f "$T/st/run-archive"; mkdir -p "$T/st/run-archive/run-20200101-010101-old" "$T/st/run-archive/run-20261101-010101-new"
touch -t 202001010000 "$T/st/run-archive/run-20200101-010101-old"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d "$T/st/run-archive/run-20200101-010101-old" ] && ok "A3: an archive older than SG_RUN_ARCHIVE_DAYS goes" || ko "A3: the old archive folder stayed: $out"
[ -d "$T/st/run-archive/run-20261101-010101-new" ] && ok "A3: a fresh archive folder stays" || ko "A3: the fresh archive folder went: $out"
touch -t 202001010000 "$T/st/run-archive/run-20261101-010101-new"
env SG_RUN_ARCHIVE_DAYS=36500 bash "$T/s/wtclean.sh" >/dev/null 2>&1
[ -d "$T/st/run-archive/run-20261101-010101-new" ] && ok "A3: SG_RUN_ARCHIVE_DAYS moves the line" || ko "A3: a raised day count pruned the folder anyway"
rm -rf "$T"; exit $fail
