// The Loop view (#1743, #1754): one render function for the desktop and the phone, inlined into both pages.
// profiler:start
// The Loop tab (#1743): /api/profiler is the one read and the one write. The hub folds the tree and sorts it; the page
// draws it and keeps only which rows are open. The root is open until it is closed; every other node opens on a press.
// The context row, every process outside the loop, is its own table below the tree.
const profDue=(tab,hidden,v)=>tab==="prof"&&!hidden&&!!v&&v.on;
const profFileDue=(tab,hidden,v)=>tab==="prof"&&!hidden&&!!v&&!v.on;   // the file views, the trip bar among them: no sampler drives them, so a slower cadence reads them (#1769)
const profSecs=s=>s>=100?s.toFixed(0)+" s":s>=1?s.toFixed(2)+" s":(s*1000).toFixed(0)+" ms";
const profRate=r=>r>=10?r.toFixed(0)+"/s":r>=1?r.toFixed(1)+"/s":r.toFixed(2)+"/s";
const profSw=v=>`<button type=button role=switch aria-label="CPU sampling" aria-checked="${!!v.on}" data-prof${v.write?"":" disabled"} class=psw${v.reason?` title="${esc(v.reason)}"`:""}><i aria-hidden="true"></i></button>`;
const profTw=(name,kids,isOpen)=>kids?`<button type=button class=ptw aria-label="${isOpen?"Close":"Open"} ${esc(name)}">${isOpen?"&#9662;":"&#9656;"}</button>`:`<span class=ptw></span>`;
function profRows(n,path,level,open,out){const kids=n.children||[],isOpen=kids.length>0&&(level===1)!==open.has(path),pct=(100*(n.share||0)).toFixed(1)+"%";
 out.push(`<tr data-p="${esc(path)}"${kids.length?` aria-expanded=${isOpen}`:""} aria-level=${level} class="pk-${esc(n.kind)}"><td class=pn style="padding-left:${(level-1)*18+6}px">`
  +profTw(n.name,kids.length,isOpen)+`<span class=pbw><i class=pbar style="width:${pct}"></i></span><b>${pct}</b><span class=pnm>${esc(n.name)}</span></td>`
  +`<td>${profSecs(n.self_s||0)}</td><td>${profSecs(n.total_s||0)}</td><td>${profRate(n.total_starts_s||0)}</td></tr>`);
 if(isOpen)for(const k of kids)profRows(k,path+"/"+k.name,level+1,open,out)}
function profCtx(c,open){const kids=c.children||[],isOpen=kids.length>0&&open.has("context");
 const row=(p,level,name,n,tw)=>`<tr data-p="${esc(p)}"${tw?` aria-expanded=${isOpen}`:""} aria-level=${level} class="pk-other"><td class=pn style="padding-left:${(level-1)*18+6}px">${tw?profTw(name,1,isOpen):`<span class=ptw></span>`}<span class=pnm>${esc(name)}</span></td><td>${profSecs(n.total_s||0)}</td><td>${n.events||0}</td></tr>`;
 return `<table id=profc role=treegrid aria-label="Every process outside the loop"><thead><tr><th title="every process that is not the loop of this profile, by application: a loaded machine shows here, a costly loop in the tree">Context</th><th title="CPU seconds in the window">Total time</th><th title="processes first seen in the window">Starts</th></tr></thead><tbody>`
  +row("context",1,c.name,c,kids.length>0)+(isOpen?kids.map(k=>row("context/"+k.name,2,k.name,k,false)).join(""):"")+`</tbody></table>`}
const profStamp=t=>new Date(t*1000).toLocaleString();   // the load line's hover stamps (the owner dropped the loop state panel)
let profWin=86400;   // the profiler read's window: the page draws no picker for it, so it reads the hub's 24 hour default
const profTime=s=>s>=3600?(s/3600).toFixed(1)+" h":s>=60?(s/60).toFixed(1)+" min":s>=1?s.toFixed(1)+" s":Math.round(s*1000)+" ms";
const profPct=v=>v==null?"&#8212;":(100*v).toFixed(1)+"%";
// The machine load (#1768): under the strip, on the strip's own window and time axis, the line of the machine's
// one-minute load average from the hub's minute record. A minute the record does not cover is a gap, never a zero, and
// the core count is a dashed reference line: above it the machine holds more work than its cores can run at once.
const PROF_LOAD_W=1000,PROF_LOAD_H=120;
function profLoad(v){
 if(!v)return "";
 const start=v.start,end=v.end,span=(end-start)||1;   // the window the hub folded on: the page never re-projects it
 const px=at=>(PROF_LOAD_W*(at-start)/span).toFixed(1),top=Math.max(v.peak||0,v.cores||1)||1,py=load=>(PROF_LOAD_H*(1-Math.max(0,Math.min(load,top))/top)).toFixed(1);
 const gaps=(v.gaps||[]).map(g=>{const a=Math.max(g.start,start),b=Math.min(g.end,end);
  return b>a?`<rect class=plg x="${px(a)}" y="0" width="${(PROF_LOAD_W*(b-a)/span).toFixed(1)}" height="${PROF_LOAD_H}" title="${esc(String(g.reason||"no load record"))}"></rect>`:""}).join("");
 const segs=(v.segments||[]).map(seg=>seg.length>1?`<polyline class=pll points="${seg.map(p=>px(p.at)+","+py(p.load)).join(" ")}"></polyline>`
  :seg.length?`<circle class=pld cx="${px(seg[0].at)}" cy="${py(seg[0].load)}" r="2.5" title="${esc("load "+Number(seg[0].load).toFixed(2)+" at "+profStamp(seg[0].at))}"></circle>`:"").join("");
 const cores=v.cores||1,cy=py(cores),core=`<line class=plc x1="0" y1="${cy}" x2="${PROF_LOAD_W}" y2="${cy}"><title>Saturation: the machine's ${cores} cores</title></line>`
  +`<text class=plct x="${PROF_LOAD_W-6}" y="${Math.max(11,(Number(cy)-5)).toFixed(1)}" text-anchor=end>saturation &middot; ${cores} cores</text>`;   // the owner asked for one horizontal line that names saturation
 const gap=(v.gap_s||0)>0?`, ${profTime(v.gap_s)} with no load record, drawn as a gap`:"";
 const aria=`Machine load over the last ${profTime(span)}: peak ${Number(v.peak||0).toFixed(2)} against a saturation of ${cores} cores${gap}`;
 return `<section id=profload><div class=lsh><h3>${esc(v.label||"Machine load")}</h3></div>`
  +`<svg viewBox="0 0 ${PROF_LOAD_W} ${PROF_LOAD_H}" role=img aria-label="${esc(aria)}" preserveAspectRatio=none>${gaps}${segs}${core}</svg>`
  +`<p class=lpn>Peak ${Number(v.peak||0).toFixed(2)} over the window; the dashed line is saturation at ${cores} cores${gap}.</p></section>`}
// The trip of an issue (#1769, #2152): one stacked bar from the hub's fold, one segment and one row per board column, in
// the board's order. A column that holds two stages reads its larger stage as the median and the p90, and the other
// stage stands beside it in the same cell with its own median; the segment carries the sum of the column's stages, so no
// measurement leaves the panel. Fewer than three deliveries draw no bar.
const profTripNum=s=>s==null?"&#8212;":profTime(s);
// The wall-time view of the loop's steps and gates was removed at the owner's request: the panel read as noise over the
// trip bar and the hot spots, and the API still carries the data for a later view.
const profLoadNum=x=>x==null?"&#8212;":x.toFixed(2);
const profTripRead=(name,s)=>`${name}: median ${profTripNum(s.median_s)}, p90 ${profTripNum(s.p90_s)} (${s.observations} observation${s.observations===1?"":"s"})`;
const profTripTitle=c=>{const main=profTripRead(c.label,c);
 return c.beside?`${main}; ${c.beside.stage} median ${profTripNum(c.beside.median_s)}`:main};
const profTripCell=c=>profTripNum(c.median_s)+(c.beside?` &middot; ${esc(c.beside.stage)} ${profTripNum(c.beside.median_s)}`:"");
function profTrip(t){
 if(!t)return "";
 const n=t.deliveries||0,span=profTime(t.window_s||0),head=`<h3>${esc(t.label||"The trip of an issue")}</h3>`;
 if(!t.bar)return `<section id=proftrip>${head}<p class=tpn>${n} deliver${n===1?"y":"ies"} in the last ${span}: fewer than three, so no bar is drawn.</p></section>`;
 const cols=t.columns||[];
 const seg=c=>{const pct=100*Math.max(0,c.share||0),painted=c.median_s>0;   // the fold's own share: the page never derives a second width
  return `<i class="ptb-seg${painted?` pts-${esc(c.column)}`:" ptb-none"}" style="width:${pct.toFixed(2)}%" title="${esc(profTripTitle(c))}"></i>`};
 const bar=`<div class=ptb role=img aria-label="${esc(`${n} deliveries in the last ${span}; the median time of each board column`)}">${cols.map(seg).join("")}</div>`;
 const rows=cols.map(c=>`<tr data-p="trip/${esc(c.column)}"><td class=pn><i class="ptk ptk-${esc(c.column)}${c.median_s>0?"":" ptk-none"}" aria-hidden=true></i><span class=pnm>${esc(c.label)}</span></td><td>${profTripCell(c)}</td><td>${profTripNum(c.p90_s)}</td><td>${c.observations||0}</td></tr>`).join("");
 return `<section id=proftrip>${head}<p class=tpn>${n} deliveries in the last ${span}: the bar folds six segments, one per board column, in the order of the rows below.</p>${bar}`
  +`<table id=proftt aria-label="The trip of an issue, a board column a row"><thead><tr><th>Column</th><th>Median</th><th>p90</th><th>Observations</th></tr></thead><tbody>${rows}</tbody></table></section>`}
function profNote(v){
 if(!v.on)return "CPU sampling is off. On, the hub reads the process table every second and folds the loop of this profile into a hot-spot tree.";
 const at=v.last_reading?new Date(v.last_reading*1000):null,f=v.failed_readings||0;
 return `On: the last ${profSecs(v.window_s||0)} of the process table. `
  +(at?`Last good reading at <time datetime="${at.toISOString()}">${esc(at.toLocaleTimeString())}</time>`+(v.interval_s?`, ${profSecs(v.interval_s)} after the one before. `:". "):"No good reading yet. ")
  +(f?`<b class=lerr>${f} reading${f===1?"":"s"} failed.</b>`:"No reading failed.")
  +(v.coverage==null?"":v.coverage>=1?"Read every process.":`Read ${(100*v.coverage).toFixed(1)}% of the processes.`)
  +" A process that lives between two readings is not counted."}
function profHtml(v,open){
 const head=`<div class=lph><h2>Loop</h2></div>`,load=profLoad(v.load),trip=profTrip(v.trip);
 // The switch ends the page: the readings stand above it, and the switch that turns them on or off follows them (owner ask).
 const foot=`<div class=lph id=proffoot>${profSw(v)}<span class=lpl aria-hidden=true>CPU sampling</span><span class=lpn>${profNote(v)}</span></div>${!v.write&&v.reason?`<p class=lpr>${esc(v.reason)}</p>`:""}`;
 if(!v.on||!v.tree)return head+load+trip+foot;   // the file views read files only, so the switch does not gate them
 const rows=[];profRows(v.tree,v.tree.name,1,open,rows);
 return head+load+trip+`<table id=proft role=treegrid aria-label="Hot spots of the loop"><thead><tr><th title="the node's share of the loop's CPU time in the window, with its children">Hot spot</th><th title="CPU seconds of the node's own processes">Self time</th><th title="CPU seconds of the node with its children; the rows sort by it">Total time</th><th title="the starts the sampler observed under the node, a second over the window; a lower bound, because a process that lives between two readings is not seen">Starts/s (lower bound)</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
  +(v.context?profCtx(v.context,open):"")+foot}
// profiler:end
let profV=null,profErr="";const profOpen=new Set();
function profDraw(){if(!profV)return;const b=document.getElementById("profb"),a=document.activeElement,row=a&&b.contains(a)?a.closest("tr[data-p]"):null,sw=!!a&&a.hasAttribute&&a.hasAttribute("data-prof");
 b.innerHTML=profHtml(profV,profOpen)+(profErr?`<p class=lerr role=alert>${esc(profErr)}</p>`:"");
 const t=sw?b.querySelector("[data-prof]"):row?([...b.querySelectorAll("tr[data-p]")].find(r=>r.dataset.p===row.dataset.p)||b).querySelector("button"):null;if(t)t.focus()}   // a redraw replaces the rows: the focus returns to the control that held it
async function profPoll(){try{profV=await (await fetch("/api/profiler?w="+profWin)).json();profDraw()}catch(e){}}
async function profPost(on,client){profErr="";try{const r=await fetch("/api/profiler",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({on,client})}),d=await r.json();if(!r.ok)profErr=d.reason||d.err||"refused"}catch(e){profErr="the hub did not answer"}
 await profPoll()}
function profMount(client){document.getElementById("profb").addEventListener("click",e=>{const s=e.target.closest("button[data-prof]");if(s){if(!s.disabled&&profV)profPost(!profV.on,client);return}
 const r=e.target.closest("tr[aria-expanded]");if(!r)return;const p=r.dataset.p;if(profOpen.has(p))profOpen.delete(p);else profOpen.add(p);profDraw()});}
