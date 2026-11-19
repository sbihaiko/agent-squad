#!/bin/bash
# seconds: 1.82
# The live loop lands finished work through branchland.sh. It has no local landing path.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
fails=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAILED: $1"; fails=$((fails + 1)); }

retired=("fork""close.sh" "fork""-gate.sh" "fork""only.sh" "sync-to-""fork.sh" "refresh-from-""fork.sh")
for name in "${retired[@]}"; do
  [ ! -e "$here/$name" ] && ok "$name is retired" || bad "$name remains in the scripts directory"
done

for file in "$here"/*; do
  [ -f "$file" ] || continue
  case "$(basename "$file")" in test-*|*.txt) continue;; esac
  for name in "${retired[@]}"; do
    grep -Fq "$name" "$file" && bad "$(basename "$file") names a retired local path"
  done
done

[ $fails -eq 0 ] && echo "test-retire-fork-only: all checks green" || { echo "test-retire-fork-only: $fails failed"; exit 1; }
