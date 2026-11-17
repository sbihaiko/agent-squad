#!/bin/bash
# Writes the labeled gate-load set as one input file, gate-load-set.tsv, whose first two lines name the source command and the write time (#1757).
# usage: gate_load_set_write.sh <state dir> <out dir>
here="$(cd "$(dirname "$0")" && pwd)"
state=${1:?usage: gate_load_set_write.sh <state dir> <out dir>}; out=${2:?usage: gate_load_set_write.sh <state dir> <out dir>}
src="python3 $here/gate_load_set.py $state"
mkdir -p "$out" || exit 1
tmp="$out/gate-load-set.tsv.$$"
{
  printf '# source-command: %s\n' "$src"
  printf '# written: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python3 "$here/gate_load_set.py" "$state"
} > "$tmp" || { rm -f "$tmp"; exit 1; }
mv "$tmp" "$out/gate-load-set.tsv"
