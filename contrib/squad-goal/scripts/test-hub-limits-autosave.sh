#!/bin/bash
# seconds: 0.58
# Guard for #2433: the Spend & limits theme saves each field on its own, and the field's typed text is the hub's input.
# The page parses a cap with limCapParse before it posts, so 6M, 250M and 1T reach the hub as whole integers and only text
# the parse refuses leaves raw. A field whose value the last write already posted never posts again, so a timer save and
# then a blur write once. A refused value in one field never holds another field's write, and the refused field keeps the
# typed value and its error mark until a successful write or the explicit Discard resets it. A refused Kanban column keeps
# its mark and its value, and a later pause never resends the same body. The saved age of a limits field and of a Kanban
# column moves with the clock, and the one check mark shows only on the field the last write saved.
# The theme's own sources are sliced out of hub.py by marker and run in node with a stub DOM: no browser, no server.
# When node is not on PATH the run says so and exits non-zero, so a missing tool never reads as a pass. usage: bash test-hub-limits-autosave.sh [path/to/hub.py]
# mirrors: hub.py
d=$(cd "$(dirname "$0")" && pwd); hub=${1:-$d/hub.py}
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the limits save rules are not proved here"; exit 1; fi
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
  +'const document={activeElement:null};const RO=process.env.HUB_RO==="1",RO_WHY="read only";'
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

run "two fields edited inside one pause each post once" '{"n":2,"order":["cap","factor"],"cap":"2000","factor":"3"}' '
let got=[];limPauseMs=20;
const send=async b=>{got.push(b);return {diff:[],state:{guard:"on",cap:+b.cap,factor:+b.factor,profile:"/p/t.sh",paused:false,last_change:null}}};
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="2000";STORE["#limf"].value="3";
limQueue("cap",send);limQueue("factor",send);
setTimeout(()=>{const last=got[got.length-1]||{};
  console.log(JSON.stringify({n:got.length,order:got.map(b=>b.factor==="3"?"factor":"cap"),cap:last.cap,factor:last.factor}))},150);'

# --- a refused field never holds another field's write

run "a refused cap does not hold the guard's write" '{"sent":2,"guard":"off","cap":"1000"}' '
let sent=[];limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="1.5";
const send=async b=>{sent.push(b);return b.cap==="1.5"?{err:"token cap must be a positive whole number"}:{diff:[],state:{...limS,guard:"off"}}};
(async()=>{await limWrite("cap",send);STORE["#limg"]={checked:false};await limWrite("guard",send);
  console.log(JSON.stringify({sent:sent.length,guard:sent[1]&&sent[1].guard,cap:sent[1]&&sent[1].cap}))})();'

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

# --- a read-only view writes nothing and arms no timer

HUB_RO=1 run "a read-only view posts no limits write and arms no timer" '{"n":0,"t":0}' '
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="6M";limPauseMs=10;
let n=0;const send=async b=>{n++;return {diff:[],state:{...limS,cap:6000000}}};
limWrite("cap",send).then(()=>{limQueue("cap",send);
  setTimeout(()=>console.log(JSON.stringify({n,t:Object.keys(limTmr).length})),80)})'
unset HUB_RO

HUB_RO=1 run "a read-only view posts no Kanban write" '{"n":0,"skip":true}' '
kanS={columns:[{name:"Doing",max_cards:3,count:1}],file:"p/s.yaml"};kanDraft=kanClone(kanS.columns);
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
let n=0;const send=async b=>{n++;return {diff:[],state:kanS}};
kanWrite("Doing",send).then(d=>console.log(JSON.stringify({n,skip:!!d&&d.skip===true})))'
unset HUB_RO

# --- a field with unsent text keeps its inputs; the mark and the line move in place

run "a focused field keeps the inputs a save of another field would rebuild" '{"kept":true,"status":true}' '
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="7M";STORE["#limc"].id="limc";document.activeElement=STORE["#limc"];
STORE["#lbody"].innerHTML="SENTINEL";
limWrite("factor",async b=>({diff:[],state:{...limS,factor:3}})).then(()=>{
  console.log(JSON.stringify({kept:STORE["#lbody"].innerHTML==="SENTINEL",status:/Saved/.test(STORE["#limstat"].innerHTML)}))})'

run "a field with a pending timer keeps the inputs" '{"kept":true}' '
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#lbody"].innerHTML="SENTINEL";limPauseMs=9000;limQueue("cap",async()=>({}));
limRender();
const kept=STORE["#lbody"].innerHTML==="SENTINEL";
clearTimeout(limTmr["cap"]);delete limTmr["cap"];
console.log(JSON.stringify({kept}))'

# --- a Kanban save posts the saved value of every other column

run "a Kanban save posts the saved value of the other columns" '{"Doing":"3","Reviewing":"5"}' '
kanS={columns:[{name:"Doing",max_cards:3,count:1},{name:"Reviewing",max_cards:5,count:0}],file:"p/s.yaml"};
kanDraft=kanClone(kanS.columns);
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
kanDraft[1].max_cards=9;
let seen=null;kanWrite("Doing",async b=>{seen=b;return {diff:[],state:{columns:kanS.columns}}}).then(()=>{
  console.log(JSON.stringify(seen.columns))})'

# --- a refused Kanban value keeps its mark and its timer, and a later pause never resends it

run "a refused Kanban value is marked and never resent by a later pause" '{"sent":2,"bad":"Doing","mark":true,"timer":0}' '
let sent=[];limPauseMs=20;
kanS={columns:[{name:"Doing",max_cards:9,count:1}],file:"p/s.yaml"};kanDraft=kanClone(kanS.columns);
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
const send=async b=>{sent.push(b.columns?b.columns.Doing:b.cap);
  return (b.columns&&b.columns.Doing==="250")?{err:"card limit must be an integer from 1 to 100"}:{diff:[],state:{...limS,cap:+b.cap}}};
(async()=>{kanDraft[0].max_cards=250;limQueue("kan:Doing",send);
  await new Promise(r=>setTimeout(r,60));
  STORE["#limc"].value="2000";await limWrite("cap",send);
  limQueue("kan:Doing",send);await new Promise(r=>setTimeout(r,60));
  console.log(JSON.stringify({sent:sent.length,bad:kanBad,mark:/class=bad/.test(kanHtml()),timer:Object.keys(limTmr).length}))})();'

# --- the Kanban saved age moves with the clock, like the limits line

run "a Kanban save age moves with the clock" '{"drawn":"4s ago","age":"7s ago"}' '
kanS={columns:[{name:"Doing",max_cards:6,count:5}],file:"p/s.yaml"};kanDraft=kanClone(kanS.columns);
kanWrite("Doing",async()=>({diff:[],state:{columns:[{name:"Doing",max_cards:5,count:5}]}})).then(()=>{
  kanAt=Date.now()-4000;const drawn=(kanHtml().match(/<span id=kanage>([^<]*)<\/span>/)||[])[1];
  kanTick(Date.now()+3000);
  console.log(JSON.stringify({drawn,age:STORE["#kanage"].textContent}))});'

# --- the check mark leaves the row of the field that lost its save

run "the mark of the field that lost its save leaves the row" '{"gone":["cap"]}' '
let gone=[];STORE["#limmk-cap"]={id:"limmk-cap",hidden:false,remove(){gone.push("cap")}};
limOK="factor";limPatch();console.log(JSON.stringify({gone}))'

# --- a missing node fails the guard, so a host without node never reads a pass

nd=$(mktemp -d); ln -s "$(command -v dirname)" "$nd/dirname" 2>/dev/null
guard_out=$(env PATH="$nd" "$(command -v bash)" "$0" "$hub" 2>&1); guard_rc=$?
rm -rf "$nd"
if [ "$guard_rc" -ne 0 ] && printf '%s' "$guard_out" | grep -q "node not on PATH"; then ok "a missing node fails the guard"
else ko "a missing node fails the guard" "exited $guard_rc: $guard_out"; fi

[ $fail = 0 ] && echo "all limits save checks passed" || echo "some limits save checks failed"
exit $fail
