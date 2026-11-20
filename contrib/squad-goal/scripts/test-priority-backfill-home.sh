#!/bin/bash
# seconds: 4.19
# usage: bash test-priority-backfill-home.sh   The priority backfill suite runs under a temp HOME with a bare python3 (#2484).
here="$(cd "$(dirname "$0")" && pwd)"
home=$(mktemp -d)
out=$(HOME="$home" SG_GATE_PY=python3 bash "$here/test-priority-backfill.sh" 2>&1); rc=$?
rm -rf "$home"
if [ "$rc" = 0 ] && ! grep -q '^FAIL' <<<"$out"; then
  echo "ok   the priority backfill suite passes under a temp HOME"
  exit 0
fi
echo "FAIL the priority backfill suite under a temp HOME: rc $rc"
tail -n 3 <<<"$out"
exit 1
