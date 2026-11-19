#!/bin/bash
# seconds: 1.58
# Guard for #2433: the Spend & limits theme saves each field on its own, and the field's typed text is the hub's input.
# The page parses a cap with limCapParse before it posts, so 6M, 250M and 1T reach the hub as whole integers and only text
# the parse refuses leaves raw. A field whose value the last write already posted never posts again, so a timer save and
# then a blur write once. A refused value in one field never holds another field's write, and the refused field keeps the
# typed value and its error mark until a successful write or the explicit Discard resets it.
# The theme's own sources are sliced out of hub.py by marker and run in node with a stub DOM: no browser, no server.
# When node is not on PATH the run says so and makes no claim. usage: bash test-hub-limits-autosave.sh [path/to/hub.py]
# mirrors: hub.py
d=$(cd "$(dirname "$0")" && pwd); hub=${1:-$d/hub.py}
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the limits save rules are not proved here"; exit 0; fi
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1: $2"; fail=1; }

# The preamble slices the theme out of hub.py by marker and runs it with a stub DOM: esc, the Settings writers, the limits
# block, then the case body, all in one script so the body reads the theme's names as the page does. A field the page has
# not drawn carries no value; a case that types one sets `value` itself.
cat > "$T/pre.js" <<'JS'
const fs=require("fs"), vm=require("vm");
const src=fs.readFileSync(process.argv[2],"utf8"), body=fs.readFileSync(process.argv[3],"utf8");
const esc=src.match(/^const esc=.*$/m)[0];
const sett=src.slice(src.indexOf("// SETT{"), src.indexOf("// }SETT")+"// }SETT".length);
const lim=src.slice(src.indexOf("let limS=null"), src.indexOf("async function limLoad()"));
for(const fn of ["function limRender(","function limWrite(","function limQueue("]){ if(!lim.includes(fn)){ console.error("the slice lost "+fn); process.exit(2); } }
const stub='const STORE=new Proxy({},{get:(o,s)=>o[s]||(o[s]={innerHTML:"",textContent:"",className:"",value:undefined})});'
  +'const $=s=>STORE[s];const setBar=()=>{};function setChip(){}';
vm.runInThisContext([stub,esc,sett,lim,body].join("\n"));
JS

# run <name> <expected stdout> <body>: the theme's sources and the body, whose one JSON line is the result
run() {
  local name=$1 want=$2 body=$3 got rc
  printf '%s\n' "$body" > "$T/body.js"
  got=$(node "$T/pre.js" "$hub" "$T/body.js" 2>&1); rc=$?
  if [ $rc -ne 0 ]; then ko "$name" "node exited $rc: $got"; return; fi
  if [ "$got" = "$want" ]; then ok "$name"; else ko "$name" "got $got, want $want"; fi
}

# --- the cap a reader types reaches the hub as a whole integer

run "a cap typed with a unit posts the parsed integer" '{"sent":"6000000"}' '
let seen=null;limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="6M";
limWrite("cap",async b=>{seen=b;return {diff:[],state:{...limS,cap:6000000}}}).then(()=>{
  console.log(JSON.stringify({sent:seen.cap}))});'

run "a cap typed as a whole number posts that number" '{"sent":"250000000"}' '
let seen=null;limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="250M";
limWrite("cap",async b=>{seen=b;return {diff:[],state:{...limS,cap:250000000}}}).then(()=>{
  console.log(JSON.stringify({sent:seen.cap}))});'

run "text the parse refuses leaves raw, so the hub can refuse it" '{"sent":"lots"}' '
let seen=null;limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="lots";
limWrite("cap",async b=>{seen=b;return {err:"token cap must be a positive whole number"}}).then(()=>{
  console.log(JSON.stringify({sent:seen.cap}))});'

# --- one value posts once: a timer save and then a blur write send one request

run "a field whose value the last write posted never posts again" '{"n":1}' '
let n=0;limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="6M";
const send=async b=>{n++;return {diff:[],state:{...limS,cap:6000000}}};
(async()=>{await limWrite("cap",send);await limWrite("cap",send);
  console.log(JSON.stringify({n}))})();'

run "an edit that changes the value posts again" '{"n":2}' '
let n=0;limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
const send=async b=>{n++;return {diff:[],state:{...limS,cap:+b.cap}}};
(async()=>{STORE["#limc"].value="6M";await limWrite("cap",send);
  STORE["#limc"].value="7M";await limWrite("cap",send);
  console.log(JSON.stringify({n}))})();'

# --- a refused field never holds another field's write

run "a refused cap does not hold the guard's write" '{"sent":2,"guard":"off"}' '
let sent=[];limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="1.5";
const send=async b=>{sent.push(b);return b.cap==="1.5"?{err:"token cap must be a positive whole number"}:{diff:[],state:{...limS,guard:"off"}}};
(async()=>{await limWrite("cap",send);STORE["#limg"].checked=false;await limWrite("guard",send);
  console.log(JSON.stringify({sent:sent.length,guard:sent[1]&&sent[1].guard}))})();'

# --- a refused field keeps the typed value and its error mark

run "a refused cap keeps the typed value and the error mark" '{"value":true,"invalid":true}' '
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="1.5";
limWrite("cap",async b=>({err:"token cap must be a positive whole number"})).then(()=>{
  const h=STORE["#lbody"].innerHTML;
  console.log(JSON.stringify({value:h.includes("value=\"1.5\""),invalid:h.includes("aria-invalid=true")}))});'

run "a successful write resets the field to the saved value" '{"value":true,"invalid":false}' '
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="6M";
limWrite("cap",async b=>({diff:[],state:{...limS,cap:6000000}})).then(()=>{
  const h=STORE["#lbody"].innerHTML;
  console.log(JSON.stringify({value:h.includes("value=\"6M\""),invalid:h.includes("aria-invalid=true")}))});'

[ $fail = 0 ] && echo "all limits save checks passed" || echo "some limits save checks failed"
exit $fail
