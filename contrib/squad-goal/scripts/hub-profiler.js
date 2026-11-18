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
// The trip of an issue (#1769, #2152, #2199, #2228): one stacked bar from the hub's fold, one segment and one row per drawn
// board column, in the board's order, and the caption counts the segments it draws. A column that holds two stages reads
// its larger stage as the median and the p90, and the other stage stands beside it in the same cell with its own median;
// the segment carries the sum of the column's stages, so no measurement the panel draws leaves it. Fewer than three
// deliveries draw no bar. Under the bar one tall chart draws the trip's columns as bands: a Tokens lane with one line in
// tokens a minute, and a Machine lane with a solid CPU line and a dashed RAM line on one scale, 0 to 100% of the machine.
// The Machine Load chart is gone: this chart's own Machine lane replaces it. The page draws only what the payload holds:
// a width, a point, a mean, a median or a tick is the fold's, never a value this page derives.
const profTripNum=s=>s==null?"&#8212;":profTime(s);
const profTokens=v=>v==null?"&#8212;":String(Math.round(v));
const PROF_TRIP_W=1000,PROF_TRIP_LANE=110,PROF_TRIP_TOP=18,PROF_TRIP_GAP=26,PROF_TRIP_LABEL=64;
const PROF_TRIP_WIDE=.08;   // a column at 8% of the chart or more prints the mean of each line; a narrower one carries a guide
const profTripRead=(name,s)=>`${name}: median ${profTripNum(s.median_s)}, p90 ${profTripNum(s.p90_s)} (${s.observations} observation${s.observations===1?"":"s"})`;
const profTripTitle=c=>{const main=profTripRead(c.label,c);
 return c.beside?`${main}; ${esc(c.beside.label||c.beside.stage)} median ${profTripNum(c.beside.median_s)}`:main};
const profTripCell=c=>profTripNum(c.median_s)+(c.beside?` &middot; ${esc(c.beside.label||c.beside.stage)} ${profTripNum(c.beside.median_s)}`:"");
function profTripChart(t,cols){
 const bands=[];let x=0;   // each column's band opens where the one before it ends, its width the fold's own share
 cols.forEach(c=>{const w=Math.max(0,c.share||0)*PROF_TRIP_W;bands.push({c,x0:x,x1:x+w,wide:(c.share||0)>=PROF_TRIP_WIDE});x+=w});
 const tokTop=PROF_TRIP_TOP,macTop=PROF_TRIP_TOP+PROF_TRIP_LANE+PROF_TRIP_GAP,chartBot=macTop+PROF_TRIP_LANE;
 const H=chartBot+PROF_TRIP_LABEL,top=(t.tokens_ticks||[])[2]||0;
 const ty=v=>tokTop+PROF_TRIP_LANE*(1-(top>0?Math.min(Math.max(v,0)/top,1):0)),my=v=>macTop+PROF_TRIP_LANE*(1-Math.min(Math.max(v,0),1));
 const tx=(b,p)=>b.x0+Math.min(Math.max(p,0),1)*(b.x1-b.x0),tick=v=>v>=10?String(Math.round(v)):String(v);
 const axis=(values,y0,unit)=>values.map(v=>`<line class=ptg0 x1="0" y1="${y0(v).toFixed(1)}" x2="${PROF_TRIP_W}" y2="${y0(v).toFixed(1)}"></line>`
  +`<text class=ptt x="${PROF_TRIP_W-4}" y="${(y0(v)-3).toFixed(1)}" text-anchor=end>${esc(tick(v)+unit)}</text>`).join("");
 const bandsOf=(y,h)=>bands.map(b=>`<rect class="ptc pts-${esc(b.c.column)}" x="${b.x0.toFixed(1)}" width="${(b.x1-b.x0).toFixed(1)}" y="${y}" height="${h}"></rect>`).join("");
 const bandAll=bandsOf(tokTop,chartBot-tokTop);   // one band per column, over both lanes, so a column reads as one block
 const lineOf=(cls,pts,y,title)=>!pts||!pts.length?""
  :`<polyline class="${cls} pts-${esc(title.column)}" points="${pts.map(([p,v])=>`${tx(title.band,p).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"><title>${esc(title.text)}</title></polyline>`;
 const gb=t.mem_gb==null?"":`, ${Number(t.mem_gb).toFixed(1)} GB of memory`;
 const drawn=bands.map(b=>{
  const c=b.c,who={column:c.column,band:b};
  const tok=lineOf("ptl-tok",c.tokens&&c.tokens.points,ty,{...who,text:`${c.label}: ${profTokens(c.tokens&&c.tokens.mean)} tokens a minute`});
  const cpu=lineOf("ptl-cpu",c.cpu&&c.cpu.points,my,{...who,text:`${c.label}: CPU ${profPct(c.cpu&&c.cpu.mean)} of the whole machine`});
  const ram=lineOf("ptl-ram",c.ram&&c.ram.points,my,{...who,text:`${c.label}: RAM ${profPct(c.ram&&c.ram.mean)} of the whole machine${gb}`});
  return tok+cpu+ram}).join("");
 const labels=bands.map((b,i)=>{
  const txt=`${esc(b.c.label)} ${profTripNum(b.c.median_s)}`,mid=(b.x0+b.x1)/2;
  if(b.wide){   // a wide column prints the mean of each line under its name
   return `<text class="ptlb pts-${esc(b.c.column)}" x="${(b.x0+2).toFixed(1)}" y="${(chartBot+16).toFixed(1)}">${txt}</text>`
    +`<text class="ptlm pts-${esc(b.c.column)}" x="${(b.x0+2).toFixed(1)}" y="${(chartBot+30).toFixed(1)}">mean ${profTokens(b.c.tokens&&b.c.tokens.mean)} tok/min &middot; CPU ${profPct(b.c.cpu&&b.c.cpu.mean)} &middot; RAM ${profPct(b.c.ram&&b.c.ram.mean)}</text>`}
  const y=chartBot+30+(i%2)*14;   // the narrow labels alternate their row, so two guides never end on one line of text
  const guide=(b.c.median_s||0)>0?`<line class="ptg pts-${esc(b.c.column)}" x1="${mid.toFixed(1)}" y1="${chartBot}" x2="${mid.toFixed(1)}" y2="${(y-10).toFixed(1)}"></line>`:"";
  return guide+`<text class="ptlb pts-${esc(b.c.column)}" x="${mid.toFixed(1)}" y="${y.toFixed(1)}" text-anchor=middle>${txt}</text>`}).join("");
 const aria=`Two lanes over the trip's ${bands.length} columns: tokens a minute, and CPU and RAM as shares of the whole machine`;
 return `<svg id=proftc viewBox="0 0 ${PROF_TRIP_W} ${H}" role=img aria-label="${esc(aria)}" preserveAspectRatio=none>`
  +bandAll
  +axis(t.tokens_ticks||[],ty," tok/min")+axis(t.machine_ticks||[],my,"%")
  +`<text class=ptn x="4" y="${(tokTop-4).toFixed(1)}">Tokens</text><text class=ptn x="4" y="${(macTop-4).toFixed(1)}">Machine</text>`
  +drawn+labels+`</svg>`}
function profTrip(t){
 if(!t)return "";
 const n=t.deliveries||0,span=profTime(t.window_s||0),head=`<h3>${esc(t.label||"The trip of an issue")}</h3>`;
 if(!t.bar)return `<section id=proftrip>${head}<p class=tpn>${n} deliver${n===1?"y":"ies"} in the last ${span}: fewer than three, so no bar is drawn.</p></section>`;
 const cols=t.columns||[];
 const silent=t.no_record_minutes==null?"the record's missing minutes were not read":`${t.no_record_minutes} minutes carried no load record`;
 const sentence=`Peak load ${t.peak_load==null?"&#8212;":Number(t.peak_load).toFixed(2)} against saturation at ${t.cores||1} cores; ${silent}.`;
 const seg=c=>{const pct=100*Math.max(0,c.share||0),painted=c.median_s>0;   // the fold's own share: the page never derives a second width
  return `<i class="ptb-seg${painted?` pts-${esc(c.column)}`:" ptb-none"}" style="width:${pct.toFixed(2)}%" title="${esc(profTripTitle(c))}"></i>`};
 const bar=`<div class=ptb role=img aria-label="${esc(`${n} deliveries in the last ${span}; the median time of each board column`)}">${cols.map(seg).join("")}</div>`;
 const dash="&#8212;";
 const rows=cols.map(c=>`<tr data-p="trip/${esc(c.column)}"><td class=pn><i class="ptk ptk-${esc(c.column)}${c.median_s>0?"":" ptk-none"}" aria-hidden=true></i><span class=pnm>${esc(c.label)}</span></td>`
  +`<td>${c.tokens_per_issue==null?dash:profTokens(c.tokens_per_issue)}</td>`
  +`<td>${c.cpu&&c.cpu.mean!=null?profPct(c.cpu.mean):dash}</td><td>${c.ram&&c.ram.mean!=null?profPct(c.ram.mean):dash}</td>`
  +`<td>${profTripCell(c)}</td><td>${profTripNum(c.p90_s)}</td><td>${c.observations||0}</td></tr>`).join("");
 return `<section id=proftrip>${head}<p class=tpn>${n} deliveries in the last ${span}: the bar folds ${cols.length} segments, one per board column, in the order of the rows below.</p>`
  +`<p class=tpn>${sentence}</p>${bar}`
  +`<p class=tpn><span class=ptlg aria-hidden=true><i class=plk-cpu></i>CPU<i class=plk-ram></i>RAM</span> Tokens a minute; CPU and RAM on one scale, 0 to 100% of the machine.</p>`
  +profTripChart(t,cols)
  +`<p class=tpn>CPU and RAM are readings of the whole machine, never of one issue alone.</p>`
  +`<table id=proftt aria-label="The trip of an issue, a board column a row"><thead><tr><th>Column</th><th>Tokens/issue</th><th>Mean CPU</th><th>Mean RAM</th><th>Median</th><th>p90</th><th>Observations</th></tr></thead><tbody>${rows}</tbody></table></section>`}
function profNote(v){
 if(!v.on)return "CPU sampling is off. On, the hub reads the process table every second and folds the loop of this profile into a hot-spot tree.";
 const at=v.last_reading?new Date(v.last_reading*1000):null,f=v.failed_readings||0;
 return `On: the last ${profSecs(v.window_s||0)} of the process table. `
  +(at?`Last good reading at <time datetime="${at.toISOString()}">${esc(at.toLocaleTimeString())}</time>`+(v.interval_s?`, ${profSecs(v.interval_s)} after the one before. `:". "):"No good reading yet. ")
  +(f?`<b class=lerr>${f} reading${f===1?"":"s"} failed.</b>`:"No reading failed.")
  +(v.coverage==null?"":v.coverage>=1?"Read every process.":`Read ${(100*v.coverage).toFixed(1)}% of the processes.`)
  +" A process that lives between two readings is not counted."}
function profHtml(v,open){
 const head=`<div class=lph><h2>Loop</h2></div>`,trip=profTrip(v.trip);
 // The switch ends the page: the readings stand above it, and the switch that turns them on or off follows them (owner ask).
 const foot=`<div class=lph id=proffoot>${profSw(v)}<span class=lpl aria-hidden=true>CPU sampling</span><span class=lpn>${profNote(v)}</span></div>${!v.write&&v.reason?`<p class=lpr>${esc(v.reason)}</p>`:""}`;
 if(!v.on||!v.tree)return head+trip+foot;   // the file views read files only, so the switch does not gate them
 const rows=[];profRows(v.tree,v.tree.name,1,open,rows);
 return head+trip+`<table id=proft role=treegrid aria-label="Hot spots of the loop"><thead><tr><th title="the node's share of the loop's CPU time in the window, with its children">Hot spot</th><th title="CPU seconds of the node's own processes">Self time</th><th title="CPU seconds of the node with its children; the rows sort by it">Total time</th><th title="the starts the sampler observed under the node, a second over the window; a lower bound, because a process that lives between two readings is not seen">Starts/s (lower bound)</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
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
