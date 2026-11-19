#!/bin/bash
# seconds: 0.08
# Guard for #1089: a question card is data with a fixed schema, and one pure module maps it to what the page draws. The
# schema refuses an unknown key, an option count outside two to four, and two recommended options on one question. Text
# stays text. Tabs appear only for more than one question. Arrow, Tab, Enter and Escape act through the same names the
# touch controls send, and Submit names every unanswered question. The module is run by node with no DOM and no server.
# When node is not on PATH the run says so and makes no claim.
# usage: bash test-hub-question-card.sh [path/to/hub-question-card.js]
d=$(cd "$(dirname "$0")" && pwd); js=${1:-$d/hub-question-card.js}
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the question card rules are not proved here"; exit 0; fi
T=$(mktemp -d); fail=0
cat > "$T/run.js" <<'JS'
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const box = {}; vm.createContext(box); vm.runInContext(src, box);
const g = (e) => JSON.stringify(vm.runInContext(e, box));
const one = {questions: [{header: "Scope", body: "Which scope?", options: [
  {label: "Narrow", description: "Only the hub"}, {label: "Wide", description: "Every surface", recommended: true}]}]};
const three = {questions: [
  {header: "Scope", body: "Which scope?", options: [{label: "Narrow", description: "a"}, {label: "Wide", description: "b"}]},
  {header: "Depth", body: "How deep?", options: [{label: "Shallow", description: "a"}, {label: "Mid", description: "b"}, {label: "Deep", description: "c"}]},
  {header: "Owner", body: "Who owns it?", options: [{label: "Hub", description: "a"}, {label: "Loop", description: "b"}, {label: "Either", description: "c"}, {label: "Neither", description: "d"}]}]};
const T1 = JSON.stringify(one), T3 = JSON.stringify(three);
const checks = [
  ["a one-question card with two options is valid", `cardCheck(${T1})`, "[]"],
  ["a card with no question is refused", "cardCheck({questions:[]}).length > 0", "true"],
  ["a question with one option is refused", "cardCheck({questions:[{header:'H',body:'b',options:[{label:'a',description:'d'}]}]}).length > 0", "true"],
  ["a question with five options is refused", "cardCheck({questions:[{header:'H',body:'b',options:[1,2,3,4,5].map(i=>({label:'l'+i,description:'d'}))}]}).length > 0", "true"],
  ["a question with four options is valid", "cardCheck({questions:[{header:'H',body:'b',options:[1,2,3,4].map(i=>({label:'l'+i,description:'d'}))}]})", "[]"],
  ["an unknown key is refused: the schema is fixed", "cardCheck({questions:[{header:'H',body:'b',html:'<b>x</b>',options:[{label:'a',description:'d'},{label:'b',description:'d'}]}]}).length > 0", "true"],
  ["two recommended options on one question are refused", "cardCheck({questions:[{header:'H',body:'b',options:[{label:'a',description:'d',recommended:true},{label:'b',description:'d',recommended:true}]}]}).length > 0", "true"],
  ["an invalid card draws no card", "cardView({questions:[]}).ok", "false"],
  ["the label is text, never markup", `cardView(${T1}).options[0].label`, '"Narrow"'],
  ["markup in a label is drawn as the same text", "cardView({questions:[{header:'H',body:'b',options:[{label:'<img src=x onerror=alert(1)>',description:'d'},{label:'b',description:'d'}]}]}).options[0].label", '"<img src=x onerror=alert(1)>"'],
  ["one question shows no tabs", `cardView(${T1}).tabs`, "[]"],
  ["more than one question shows one tab per question", `cardView(${T3}).tabs.map(t => t.header)`, '["Scope","Depth","Owner"]'],
  ["the recommended option is marked", `cardView(${T1}).options[1].recommended`, "true"],
  ["the recommended option carries the badge", `cardView(${T1}).options[1].badge`, '"Recommended"'],
  ["an option that is not recommended has no badge", `cardView(${T1}).options[0].badge`, '""'],
  ["Arrow Down moves the focus to the next option", `cardStep(${T1}, cardStart(${T1}), "down").focus[0]`, "1"],
  ["Arrow Down stops at the last option", `cardStep(${T1}, cardStep(${T1}, cardStart(${T1}), "down"), "down").focus[0]`, "1"],
  ["Arrow Up stops at the first option", `cardStep(${T1}, cardStart(${T1}), "up").focus[0]`, "0"],
  ["Tab moves to the next question", `cardStep(${T3}, cardStart(${T3}), "next").q`, "1"],
  ["Tab stops at the last question", `cardStep(${T3}, cardStep(${T3}, cardStep(${T3}, cardStart(${T3}), "next"), "next"), "next").q`, "2"],
  ["Shift-Tab moves to the previous question", `cardStep(${T3}, cardStep(${T3}, cardStart(${T3}), "next"), "prev").q`, "0"],
  ["Enter selects the focused option", `cardStep(${T1}, cardStep(${T1}, cardStart(${T1}), "down"), "select").answers[0]`, "1"],
  ["Escape cancels the card", `cardStep(${T1}, cardStart(${T1}), "cancel").cancelled`, "true"],
  ["a cancelled card takes no later selection", `cardStep(${T1}, cardStep(${T1}, cardStart(${T1}), "cancel"), "select").answers[0]`, "null"],
  ["the keys map to the actions", `[questionKey({key:"ArrowDown"}), questionKey({key:"Tab"}), questionKey({key:"Tab",shiftKey:true}), questionKey({key:"Enter"}), questionKey({key:"Escape"}), questionKey({key:"a"})]`, '["down","next","prev","select","cancel",null]'],
  ["a touch pick selects the option it names", `cardStep(${T1}, cardStart(${T1}), {act:"pick", index:1}).answers[0]`, "1"],
  ["a single question has the touch controls for its own actions", `cardView(${T1}).controls.map(c => c.act)`, '["up","down","select","cancel","submit"]'],
  ["a card with tabs also has touch controls for the tabs", `cardView(${T3}).controls.map(c => c.act)`, '["up","down","prev","next","select","cancel","submit"]'],
  ["Submit names every unanswered question, none skipped", `cardSubmit(${T3}, cardStart(${T3})).unanswered`, '[{"index":0,"header":"Scope"},{"index":1,"header":"Depth"},{"index":2,"header":"Owner"}]'],
  ["Submit with one unanswered question names only that one", `cardSubmit(${T3}, cardStep(${T3}, cardStep(${T3}, cardStep(${T3}, cardStart(${T3}), {act:"pick",index:0}), "next"), {act:"pick",index:0})).unanswered`, '[{"index":2,"header":"Owner"}]'],
  ["a refused Submit is not ok and says why", `(() => { const r = cardSubmit(${T3}, cardStart(${T3})); return [r.ok, r.why]; })()`, '[false,"unanswered"]'],
  ["a Submit with every question answered returns the answers", `cardSubmit(${T1}, cardStep(${T1}, cardStart(${T1}), {act:"pick",index:1})).answers`, '[{"header":"Scope","label":"Wide"}]'],
  ["a cancelled card is never submitted", `(() => { const r = cardSubmit(${T1}, cardStep(${T1}, cardStart(${T1}), "cancel")); return [r.ok, r.why]; })()`, '[false,"cancelled"]'],
  ["a tab names the question it opens", `cardStep(${T3}, cardStart(${T3}), {act:"goto", index:2}).q`, "2"],
  ["a refused send reopens the card with its picks", `(() => { const s = cardUnsent(cardSubmit(${T1}, cardStep(${T1}, cardStart(${T1}), {act:"pick",index:1})).state); return [s.sent, s.answers[0]]; })()`, "[false,1]"],
];
let bad = 0;
for (const [label, expr, want] of checks) {
  let got;
  try { got = g(expr); } catch (e) { got = "THREW " + e.message; }
  if (got === want) console.log("ok   " + label);
  else { bad++; console.log("FAIL " + label + ": got " + got + ", want " + want); }
}
for (const banned of ["fetch(", "XMLHttpRequest", "WebSocket", "import ", "require(", "document", "window", "innerHTML"])
  if (src.includes(banned)) { bad++; console.log("FAIL the module holds " + banned); }
process.exit(bad ? 1 : 0);
JS
node "$T/run.js" "$js"; [ $? -eq 0 ] || fail=1
rm -rf "$T"; exit $fail
