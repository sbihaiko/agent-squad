#!/bin/bash
# seconds: 0.41
# The dispatch writer of the labeled gate-load set (#1757): one input file, a header that names its source and write time.
# Fixtures in a temp folder only: never the loop state.
# usage: bash test-gate-load-set-write.sh [gate_load_set_write.sh]
here="$(cd "$(dirname "$0")" && pwd)"; src=${1:-$here/gate_load_set_write.sh}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/state" "$T/out"; : > "$T/state/gate-history"

bash "$src" "$T/state" "$T/out" >"$T/log.txt" 2>&1 && ok "the writer exits 0" || ko "the writer failed: $(cat "$T/log.txt")"
f="$T/out/gate-load-set.tsv"
[ -f "$f" ] && ok "the writer names one input file, gate-load-set.tsv" || ko "no gate-load-set.tsv in the output folder"

sed -n 1p "$f" 2>/dev/null | grep -q '^# source-command: .*gate_load_set\.py .*'"$T"'/state$' \
  && ok "the first header line names the source command" || ko "source line: $(sed -n 1p "$f" 2>/dev/null)"
sed -n 2p "$f" 2>/dev/null | grep -Eq '^# written: [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' \
  && ok "the second header line carries the write time" || ko "written line: $(sed -n 2p "$f" 2>/dev/null)"
[ "$(sed -n 3p "$f" 2>/dev/null)" = "$(printf 'finish\tsha\tkind\tscope\toutcome\tload_start\tload_finish\trerun')" ] \
  && ok "the third line is the row header" || ko "row header: $(sed -n 3p "$f" 2>/dev/null)"

python3 "$here/gate_load_report.py" "$f" 2>&1 | grep -q '^source: .*gate_load_set\.py' \
  && ok "the report reads the written file" || ko "the report did not read the file"

rm -rf "$T"
exit $fail
