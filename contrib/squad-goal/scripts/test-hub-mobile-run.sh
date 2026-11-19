#!/bin/bash
# seconds: 0.05
# Guard for #831: pause and resume of the phone's run screen are one client module, and the rules are the whole of it.
# Every request goes to the run's own control route through the hub's /dash/ forward route, with the desktop page's body;
# the screen offers no request for an ended run or a dashboard that does not answer, and the resume it offers after a
# whole-run pause says that the run continues when `squad resume` runs. The module names no loopback address and no
# tailnet host: the forward route is its only address, and the tailnet entry stays disabled until #713.
# The module is run by node with no DOM and no server. When node is not on PATH the run says so and makes no claim.
# usage: bash test-hub-mobile-run.sh [path/to/hub-mobile-run.js]
d=$(cd "$(dirname "$0")" && pwd); js=${1:-$d/hub-mobile-run.js}
if ! command -v node >/dev/null 2>&1; then echo "node not on PATH: the run control rules are not proved here"; exit 0; fi
T=$(mktemp -d); fail=0
cat > "$T/run.js" <<'JS'
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const box = {}; vm.createContext(box); vm.runInContext(src, box);
const g = (e) => JSON.stringify(vm.runInContext(e, box));
const RUN = "run-20261021-032628-72d18f";
const ASKED = "{whole:true,nodes:[],released:[]}";
const OPEN = "{whole:false,nodes:[],released:[]}";
const checks = [
  ["the control goes through the hub's forward route", `runURLs(${JSON.stringify(RUN)}).control`, JSON.stringify(`/dash/${RUN}/run/control`)],
  ["the control state and the info read the same route", `[runURLs(${JSON.stringify(RUN)}).state, runURLs(${JSON.stringify(RUN)}).info]`, JSON.stringify([`/dash/${RUN}/run/control.json`, `/dash/${RUN}/run/info.json`])],
  ["an id is encoded into the route", `runURLs("a/b").control`, JSON.stringify("/dash/a%2Fb/run/control")],
  ["the body is the desktop's, whole run", `controlBody("pause", null)`, JSON.stringify('{"action":"pause"}')],
  ["the body names the nodes when one is asked", `controlBody("resume", ["a"])`, JSON.stringify('{"action":"resume","nodes":["a"]}')],
  ["a live run is offered the pause", `runControlView({ended:false}, ${OPEN}).act`, JSON.stringify("pause")],
  ["a run asked to pause is offered the resume", `runControlView({ended:false}, ${ASKED}).act`, JSON.stringify("resume")],
  ["a held node is a pause the phone may release too", `runControlView({ended:false}, {whole:false,nodes:["a"],released:[]}).act`, JSON.stringify("resume")],
  ["the resume says the run continues when squad resume runs", `runControlView({ended:false}, ${ASKED}).note.includes("squad resume")`, "true"],
  ["an ended run is offered no request", `runControlView({ended:true}, ${OPEN}).act`, "null"],
  ["a dashboard that does not answer is offered no request", `runControlView(null, null).act`, "null"],
  ["the limits name the running call that finishes", "RUN_LIMITS.includes('running call finish')", "true"],
  ["the limits name the resume that does not restart the process", "RUN_LIMITS.includes('does not restart')", "true"],
  ["a pause answer says the running call finishes first", `runControlReply("pause", 200).text.includes("finishes first")`, "true"],
  ["a resume answer never starts a process", `runControlReply("resume", 200).text.includes("squad resume")`, "true"],
  ["an ended run's answer says a resume does not restart it", `[runControlReply("resume", 409).ok, runControlReply("resume", 409).text.includes("does not restart")]`, "[false,true]"],
  ["another refusal names its status", `runControlReply("pause", 403).text`, JSON.stringify("The hub refused the request (403).")],
];
let bad = 0;
for (const [label, expr, want] of checks) {
  const got = g(expr);
  if (got === want) console.log("ok   " + label);
  else { bad++; console.log("FAIL " + label + ": got " + got + ", want " + want); }
}
for (const banned of ["fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import ", "127.0.0.1", "localhost", ".ts.net", "tailnet.host"])
  if (src.includes(banned)) { bad++; console.log("FAIL the module holds " + banned); }
process.exit(bad ? 1 : 0);
JS
node "$T/run.js" "$js"; [ $? -eq 0 ] || fail=1
rm -rf "$T"; exit $fail
