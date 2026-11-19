#!/bin/bash
# seconds: 0.09
# Guard for #997: the phone sheet steps through the cards the opened card's run holds in its column, and a card with no run
# steps the whole column. The selection rule is the gesture module's, run by node with no DOM; the page is checked for the
# wiring that reads it. The page is driven over the stub DOM by tests/test_hub_mobile_board.py.
# usage: bash test-hub-mobile-sheet.sh [path/to/hub-mobile-gesture.js path/to/hub-mobile.html]
d=$(cd "$(dirname "$0")" && pwd); js=${1:-$d/hub-mobile-gesture.js}; html=${2:-$d/hub-mobile.html}
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the sheet selection is not proved here"; exit 0; fi
T=$(mktemp -d); fail=0
cat > "$T/run.js" <<'JS'
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const box = {}; vm.createContext(box); vm.runInContext(src, box);
const g = (e) => JSON.stringify(vm.runInContext(e, box));
const CARDS = `[{n:1,hr:"run-a"},{n:2},{n:3,hr:"run-a"},{n:4,hr:"run-b"}]`;
const V = (card) => `{col:0,cols:1,card:${card},cards:4,sheet:true}`;
const checks = [
  ["a card with a run selects the cards of that run", `sheetCards(${CARDS}, {n:3,hr:"run-a"}).map(c => c.n)`, "[1,3]"],
  ["a card with no run selects the whole column", `sheetCards(${CARDS}, {n:2}).map(c => c.n)`, "[1,2,3,4]"],
  ["a card with a run of one selects that card alone", `sheetCards(${CARDS}, {n:4,hr:"run-b"}).map(c => c.n)`, "[4]"],
  ["next steps to the run's next card", `sheetStep(${V(0)}, ${CARDS}, {act:"card",step:1}).card`, "2"],
  ["next stops at the run's last card", `sheetStep(${V(2)}, ${CARDS}, {act:"card",step:1}).card`, "2"],
  ["previous steps to the run's first card", `sheetStep(${V(2)}, ${CARDS}, {act:"card",step:-1}).card`, "0"],
  ["previous stops at the run's first card", `sheetStep(${V(0)}, ${CARDS}, {act:"card",step:-1}).card`, "0"],
  ["a card with no run steps the column by one", `sheetStep(${V(1)}, ${CARDS}, {act:"card",step:1}).card`, "2"],
  ["a column step is unchanged by the run rule", `sheetStep({col:0,cols:2,card:2,cards:4,sheet:true}, ${CARDS}, {act:"column",step:1})`, `{"col":1,"cols":2,"card":0,"cards":4,"sheet":true}`],
];
let bad = 0;
for (const [label, expr, want] of checks) {
  const got = g(expr);
  if (got === want) console.log("ok   " + label);
  else { bad++; console.log("FAIL " + label + ": got " + got + ", want " + want); }
}
process.exit(bad ? 1 : 0);
JS
node "$T/run.js" "$js"; [ $? -eq 0 ] || fail=1
rm -rf "$T"
page=$(cat "$html")
for wire in 'b.dataset.hr = x.hr' 'const set = offBoard ? [] : sheetCards(shown, x), at = set.indexOf(x);' 'sheetStep(view, shown, g)' '(at + 1) + " of " + set.length'; do
  case "$page" in *"$wire"*) echo "ok   the page wires: $wire" ;;
  *) echo "FAIL the page does not wire: $wire"; fail=1 ;;
  esac
done
exit $fail
