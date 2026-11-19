#!/bin/bash
# seconds: 0.04
# Guard for #829: the move rules of the phone's Board tab are one client module, and the rules are the whole of it. A
# release before the threshold does nothing; a card the hub does not mark promotable never stages; the Undo, the app going
# hidden, a profile change and a second move staged over this one all cancel a staged move, and a cancelled move is never
# written, however late the write is asked for. The module names no column: the row's own `pmt` names the destination, so
# this suite names none either.
# The module is run by node with no DOM and no server. When node is not on PATH the run says so and makes no claim.
# usage: bash test-hub-mobile-move.sh [path/to/hub-mobile-move.js]
d=$(cd "$(dirname "$0")" && pwd); js=${1:-$d/hub-mobile-move.js}
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the move rules are not proved here"; exit 0; fi
T=$(mktemp -d); fail=0
cat > "$T/run.js" <<'JS'
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const box = {}; vm.createContext(box); vm.runInContext(src, box);
const g = (e) => JSON.stringify(vm.runInContext(e, box));
const checks = [
  ["a release before the threshold does nothing", "moveDrag({promotable:true,dx:55,dy:2})", "null"],
  ["a release at the threshold stages the move", "moveDrag({promotable:true,dx:56,dy:2})", '{"act":"stage"}'],
  ["a card the hub did not mark promotable never stages", "moveDrag({promotable:false,dx:500,dy:2})", "null"],
  ["a drag left is no move forward", "moveDrag({promotable:true,dx:-500,dy:2})", "null"],
  ["a vertical move is the list scrolling", "moveDrag({promotable:true,dx:10,dy:200})", "null"],
  ["the write waits for the Undo's delay", "moveAct(moveStage(7,'x',1000),'write',1000+UNDO_MS-1).why", '""'],
  ["the write happens at the delay", "moveAct(moveStage(7,'x',1000),'write',1000+UNDO_MS).why", '"written"'],
  ["the staged move carries the row's column", "moveStage(7,'a column the row names',1000).to", '"a column the row names"'],
  ["the Undo cancels the move", "moveAct(moveStage(7,'x',1000),'undo',2000).why", '"undo"'],
  ["a hidden app cancels the move", "moveAct(moveStage(7,'x',1000),'hidden',2000).why", '"hidden"'],
  ["a profile change cancels the move", "moveAct(moveStage(7,'x',1000),'profile',2000).why", '"profile"'],
  ["a second move staged over this one cancels it", "moveAct(moveStage(7,'x',1000),'superseded',2000).why", '"superseded"'],
  ["a superseded move is never written later", "(()=>{const p=moveAct(moveStage(7,'x',1000),'superseded',2000);return moveAct(p,'write',99999).why})()", '"superseded"'],
  ["a cancelled move is never written later", "(()=>{const p=moveAct(moveStage(7,'x',1000),'undo',2000);return moveAct(p,'write',99999).why})()", '"undo"'],
  ["a write before its delay does not fire", "moveAct(moveStage(7,'x',1000),'write',1500).live", "true"],
];
let bad = 0;
for (const [label, expr, want] of checks) {
  const got = g(expr);
  if (got === want) console.log("ok   " + label);
  else { bad++; console.log("FAIL " + label + ": got " + got + ", want " + want); }
}
for (const banned of ["fetch(", "XMLHttpRequest", "WebSocket", "import "])
  if (src.includes(banned)) { bad++; console.log("FAIL the module holds " + banned); }
process.exit(bad ? 1 : 0);
JS
node "$T/run.js" "$js"; [ $? -eq 0 ] || fail=1
rm -rf "$T"; exit $fail
