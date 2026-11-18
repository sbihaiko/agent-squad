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
// a width, a point, a mean, a median or a tick is the fold's, never a value this page derives. The Tokens lane's top
// tick stands strictly above every point the fold emits, so no point reaches the lane's edge or its clamp. A column's
// percent hover names its own share of the machine; the machine's memory in gigabytes is the RAM legend entry's hover,
// so no percent reading carries a gigabyte suffix. One line joins the points of the columns it crosses, and a column
// with no point breaks it and is marked no record (#2253); one sample draws a dot, and a mean reads a dash for a line
// that holds no point. Each lane's values and each column's label keep their own box, so nothing overlaps.
const profTripNum=s=>s==null?"&#8212;":profTime(s);
const profTokens=v=>v==null?"&#8212;":String(Math.round(v));
// A count prints in k or M units (#2253): the axis, the lane's mean and the table never print a raw six-digit number.
const profTokUnit=v=>{if(v==null)return "&#8212;";const n=Math.round(v);
 if(n>=1e6)return (n/1e6).toFixed(n>=1e7?0:1).replace(/\.0$/,"")+"M";
 if(n>=1000)return (n/1000).toFixed(n>=1e4?0:1).replace(/\.0$/,"")+"k";
 return String(n)};
const profTripRead=(name,s)=>`${name}: median ${profTripNum(s.median_s)}, p90 ${profTripNum(s.p90_s)} (${s.observations} observation${s.observations===1?"":"s"})`;
const profTripTitle=c=>{const main=profTripRead(c.label,c);
 return c.beside?`${main}; ${esc(c.beside.label||c.beside.stage)} median ${profTripNum(c.beside.median_s)}`:main};
const profTripCell=c=>profTripNum(c.median_s)+(c.beside?` &middot; ${esc(c.beside.label||c.beside.stage)} ${profTripNum(c.beside.median_s)}`:"");
// The chart's layout (#2253): a left gutter holds each lane's three values, the bands run right of it, and a text's
// width is its user-unit font size times its character count, so a label too wide for its column takes a leader line
// instead of a box over its neighbour. Every glyph in the chart is sized in SVG user units, so its box scales with the
// viewBox: the layout holds at 390 px and at 1440 px alike. A CSS caption size would not scale, so the chart sets none.
const PROF_TRIP_W=1000,PROF_TRIP_GUTTER=140,PROF_TRIP_LANE=110,PROF_TRIP_TOP=26,PROF_TRIP_GAP=26;
const PROF_TRIP_FONT=13,PROF_TRIP_CHAR=PROF_TRIP_FONT*0.6,PROF_TRIP_ROW=16;
const profTextW=s=>String(s).length*PROF_TRIP_CHAR;
const profTripPoint=c=>{const l=c||{};return l.points&&l.points.length?l.mean:null};
function profTripChart(t,cols){
 const G=PROF_TRIP_GUTTER,W=PROF_TRIP_W-G,bands=[];let x=G;   // each column opens where the one before it ends
 cols.forEach(c=>{const w=Math.max(0,c.share||0)*W;bands.push({c,x0:x,x1:x+w});x+=w});
 const tokTop=PROF_TRIP_TOP,macTop=tokTop+PROF_TRIP_LANE+PROF_TRIP_GAP,chartBot=macTop+PROF_TRIP_LANE;
 const ticks=Array.isArray(t.tokens_ticks)&&t.tokens_ticks.length===3?t.tokens_ticks:[],top=ticks[2]||0;
 // A point is a share (0 to 1) and an axis tick is a percent (0 to 100): the two scales differ, so no 50% tick lands
 // on the lane's top beside the 100% (#2253).
 const ty=v=>tokTop+PROF_TRIP_LANE*(1-(top>0?Math.min(Math.max(v,0)/top,1):0)),my=v=>macTop+PROF_TRIP_LANE*(1-Math.min(Math.max(v,0),1));
 const myTick=v=>macTop+PROF_TRIP_LANE*(1-Math.min(Math.max(v,0),100)/100);
 const tx=(b,p)=>b.x0+Math.min(Math.max(p,0),1)*(b.x1-b.x0);
 // Each lane's values stand once, in the gutter, so no value is drawn over a lane, a band or a mean (#2253).
 const axis=(values,y0,fmt)=>values.map(v=>`<line class=ptg0 x1="${G}" y1="${y0(v).toFixed(1)}" x2="${PROF_TRIP_W}" y2="${y0(v).toFixed(1)}"></line>`
  +`<text class=ptt x="${G-6}" y="${(y0(v)+4).toFixed(1)}" text-anchor=end>${esc(fmt(v))}</text>`).join("");
 const bandsOf=(y,h)=>bands.map(b=>`<rect class="ptc pts-${esc(b.c.column)}" x="${b.x0.toFixed(1)}" width="${(b.x1-b.x0).toFixed(1)}" y="${y}" height="${h}"></rect>`).join("");
 const bandAll=bandsOf(tokTop,chartBot-tokTop);   // one band per column over both lanes, so a column reads as one block
 // One line joins the points of the columns it crosses (#2253): a column with no point ends the run, and that stretch
 // is marked as no record, never drawn as a zero. The mark is a band over the lane from its top, so it stands off the
 // data scale and can never read as a sample. A point with no neighbour draws a dot.
 const lineOf=(cls,lane,y,unit,val,top,h)=>{const seq=bands.map(b=>{const l=b.c[lane]||{},pts=l.points||[];
   return {b,pts:pts.map(([p,v])=>({x:tx(b,p),y:y(v),v}))}});
  let out="",run=[],crossed=[];
  const flush=()=>{if(run.length===1){const q=run[0];out+=`<circle class="${cls}" cx="${q.x.toFixed(1)}" cy="${q.y.toFixed(1)}" r=2.5><title>${esc(`${crossed[0].c.label}: ${val(q.v)}`)}</title></circle>`}
   else if(run.length>1){out+=`<polyline class="${cls}" points="${run.map(q=>q.x.toFixed(1)+","+q.y.toFixed(1)).join(" ")}"><title>${esc(`${crossed[0].c.label} to ${crossed[crossed.length-1].c.label}: ${unit}`)}</title></polyline>`}
   run=[];crossed=[]};
  for(const s of seq){if(!s.pts.length){flush();continue}run.push(...s.pts);crossed.push(s.b)}
  flush();
  return out+seq.filter(s=>!s.pts.length&&s.b.x1-s.b.x0>0).map(s=>`<rect class="ptgn pts-${esc(s.b.c.column)}" x="${(s.b.x0+1).toFixed(1)}" y="${top}" width="${Math.max(0,s.b.x1-s.b.x0-2).toFixed(1)}" height="${h}"><title>${esc(`${s.b.c.label}: no record on this line`)}</title></rect>`).join("")};
 const drawn=lineOf("ptl-tok","tokens",ty,"tokens a minute",v=>`${profTokens(v)} tokens a minute`,tokTop,PROF_TRIP_LANE)
  +lineOf("ptl-cpu","cpu",my,"CPU, percent of the whole machine",v=>`CPU ${profPct(v)} of the whole machine`,macTop,PROF_TRIP_LANE)
  +lineOf("ptl-ram","ram",my,"RAM, percent of the whole machine",v=>`RAM ${profPct(v)} of the whole machine`,macTop,PROF_TRIP_LANE);
 // Every column prints the mean of each line inside its lane (#2253): the token mean in the Tokens lane, the CPU and
 // RAM means in the Machine lane. The full copy stands at the column's right edge; a column too narrow for it takes a
 // shorter copy, and a row that a text already holds takes the next one, so two texts never share a box. A line with
 // no point in the column reads a dash.
 const laneRows=top=>Array.from({length:6},(_,i)=>top+11+PROF_TRIP_ROW*i);
 const inLane=(()=>{const rows={tok:laneRows(tokTop),mac:laneRows(macTop)},placed={tok:[],mac:[]};
  const put=(k,b,text)=>{const w=profTextW(text),x1=b.x1-3,x0=x1-w;
   let y=rows[k].find(c=>!placed[k].some(p=>Math.abs(p.y-c)<13&&x0<p.x1+1&&p.x0<x1+1));
   if(y==null)y=rows[k][rows[k].length-1];
   placed[k].push({y,x0,x1});
   return `<text class="ptlm pts-${esc(b.c.column)}" x="${x1.toFixed(1)}" y="${y.toFixed(1)}" text-anchor=end>${text}</text>`};
  const fit=(b,full,short)=>(b.x1-b.x0)>=profTextW(full)+6?full:short;
  return bands.map(b=>{const v=c=>profTripPoint(c);
   return put("tok",b,fit(b,`mean ${profTokUnit(v(b.c.tokens))} tok/min`,`${profTokUnit(v(b.c.tokens))}/min`))
    +put("mac",b,fit(b,`CPU ${profPct(v(b.c.cpu))}`,`${profPct(v(b.c.cpu))}`))
    +put("mac",b,fit(b,`RAM ${profPct(v(b.c.ram))}`,`${profPct(v(b.c.ram))}`))}).join("")})();
 // A label too wide for its column takes a leader line and stands on its own row (#2253): no label can then share a
 // box with another, and a column of no width still keeps its name and its median.
 const wide=[],narrow=[];
 bands.forEach(b=>{const txt=`${esc(b.c.label)} ${profTripNum(b.c.median_s)}`;((b.x1-b.x0)>=profTextW(txt.replace(/&(?:#\d+|\w+);/g,"~"))+12?wide:narrow).push({b,txt})});
 const wideHtml=wide.map(({b,txt})=>`<text class="ptlb pts-${esc(b.c.column)}" x="${((b.x0+b.x1)/2).toFixed(1)}" y="${(chartBot+16).toFixed(1)}" text-anchor=middle>${txt}</text>`).join("");
 const narrowHtml=narrow.map(({b,txt},i)=>{const y=chartBot+16+PROF_TRIP_ROW*(i+1),mid=(b.x0+b.x1)/2,right=mid>=(G+PROF_TRIP_W)/2;
  const x=right?Math.max(b.x0-6,0):Math.min(b.x1+6,PROF_TRIP_W),anchor=right?"end":"start";
  const guide=(b.c.median_s||0)>0?`<line class="ptg pts-${esc(b.c.column)}" x1="${mid.toFixed(1)}" y1="${chartBot}" x2="${x.toFixed(1)}" y2="${(y-10).toFixed(1)}"></line>`:"";
  return guide+`<text class="ptlb pts-${esc(b.c.column)}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor=${anchor}>${txt}</text>`}).join("");
 const H=chartBot+16+PROF_TRIP_ROW*narrow.length+6;
 const aria=`Two lanes over the trip's ${bands.length} columns: tokens a minute, and CPU and RAM as shares of the whole machine`;
 return `<svg id=proftc viewBox="0 0 ${PROF_TRIP_W} ${H}" font-size=${PROF_TRIP_FONT} role=img aria-label="${esc(aria)}" preserveAspectRatio=none>`
  +bandAll
  +axis(t.tokens_ticks||[],ty,v=>profTokUnit(v)+" tok/min")+axis(t.machine_ticks||[],myTick,v=>String(v)+"%")
  +`<text class=ptn x="0" y="${(tokTop-10).toFixed(1)}">Tokens</text><text class=ptn x="0" y="${(macTop-10).toFixed(1)}">Machine</text>`
  +drawn+inLane+wideHtml+narrowHtml+`</svg>`}
// The phone's lanes (#2229): the same payload, drawn for a narrow screen. Each column keeps its own width in the lane, so
// a point sits at its position inside the column; a column with no point draws no line there, never a zero. The Tokens
// lane holds one line on its own scale, the Machine lane holds CPU solid and RAM dashed on one 0 to 100% scale. The page
// scales what the payload holds and invents no reading.
const PROF_LANE_W=1000,PROF_TOK_H=64,PROF_MACH_H=96;
const profTripPct=v=>v==null?"&#8212;":(100*v).toFixed(0)+"%";
function profTripShares(cols){const sum=cols.reduce((a,c)=>a+Math.max(0,c.share||0),0)||1;
 return cols.map(c=>({c,w:Math.max(0,c.share||0)/sum*PROF_LANE_W}))}
function profTripBands(parts,h){let x=0,out="";
 for(const {w} of parts){out+=`<rect class=ptlbg x="${x.toFixed(1)}" y="0" width="${w.toFixed(1)}" height="${h}"></rect>`;x+=w}
 return out}
// One line joins the points of adjacent columns, and an empty column breaks it (#2253), so a gap is a gap and never a
// drop to zero. The empty stretch takes a band over the lane from its top, marked no record, so the gap stands off the
// data scale. A run of one point draws a dot, because a polyline of one coordinate draws nothing.
function profTripLine(parts,lane,h,max,cls,title){
 let x=0,out="",run=[];
 const flush=()=>{if(run.length===1){const q=run[0];out+=`<circle class="ptl ${cls}" cx="${q.x}" cy="${q.y}" r="3">${q.t}</circle>`}
  else if(run.length>1)out+=`<polyline class="ptl ${cls}" points="${run.map(q=>q.x+","+q.y).join(" ")}"></polyline>`;run=[]};
 for(const {c,w} of parts){
  const pts=((c[lane]||{}).points)||[];
  if(!pts.length){flush();
   if(w>0)out+=`<rect class="ptl-nr" x="${x.toFixed(1)}" y="0" width="${w.toFixed(1)}" height="${h}"><title>${esc(`${c.label||c.column}: no record`)}</title></rect>`;
   x+=w;continue}
  for(const [p,v] of pts)run.push({x:(x+Math.min(1,Math.max(0,p))*w).toFixed(1),y:(h*(1-Math.min(1,Math.max(0,(v||0)/max)))).toFixed(1),
   t:title?`<title>${esc(title(c,v))}</title>`:""});
  x+=w}
 flush();
 return out}
function profTripLanes(t){
 const cols=t.columns||[],parts=profTripShares(cols);
 const tokMax=Math.max(1,...cols.flatMap(c=>(((c.tokens||{}).points)||[]).map(p=>p[1]||0)));
 const gb=t.mem_gb;
 const tok=`<div class=ptlh>Tokens &middot; tokens/min</div><svg class="ptlane ptlane-tok" viewBox="0 0 ${PROF_LANE_W} ${PROF_TOK_H}" role=img aria-label="${esc(`Tokens per minute over the trip's columns, zero to ${Math.round(tokMax)}`)}" preserveAspectRatio=none>${profTripBands(parts,PROF_TOK_H)}${profTripLine(parts,"tokens",PROF_TOK_H,tokMax,"ptl-tok")}</svg>`;
 const mach=`<div class=ptlh>Machine &middot; percent of the whole machine <span class=ptlkey><i class="ptkey ptkey-cpu"></i>CPU <i class="ptkey ptkey-ram"></i>RAM</span></div>`
  +`<svg class="ptlane ptlane-mach" viewBox="0 0 ${PROF_LANE_W} ${PROF_MACH_H}" role=img aria-label="CPU and RAM as a percent of the whole machine over the trip's columns" preserveAspectRatio=none>`
  +`${profTripBands(parts,PROF_MACH_H)}${profTripLine(parts,"cpu",PROF_MACH_H,1,"ptl-cpu")}${profTripLine(parts,"ram",PROF_MACH_H,1,"ptl-ram",(c,v)=>`RAM ${profTripPct(v).replace("&#8212;","")} of ${gb==null?"the machine":Number(gb).toFixed(0)+" GB"}`)}</svg>`;
 const strip=`<div class=ptstrip>${parts.map(({c,w})=>`<i class="ptseg pts-${esc(c.column)}${c.median_s>0?"":" ptb-none"}" style="flex:${Math.max(0,c.share||0)} 1 0"></i>`).join("")}</div>`;
 const labels=`<div class=ptlabels>${cols.map(c=>`<span class=ptlab><i class="ptk ptk-${esc(c.column)}${c.median_s>0?"":" ptk-none"}" aria-hidden=true></i>${esc(c.label)} ${profTripNum(c.median_s)}</span>`).join("")}</div>`;
 return tok+mach+strip+labels}
function profTripFacts(t){
 const peak=t.peak_load==null?"No load reading":`Peak load ${Number(t.peak_load).toFixed(2)}`;
 return `<p class=tpn>${peak}; saturation at ${t.cores||1} cores; ${t.no_record_minutes||0} minutes with no load record.</p>`}
function profTrip(t,phone){
 if(!t)return "";
 const n=t.deliveries||0,span=profTime(t.window_s||0),head=`<h3>${esc(t.label||"The trip of an issue")}</h3>`;
 if(!t.bar)return `<section id=proftrip>${head}<p class=tpn>${n} deliver${n===1?"y":"ies"} in the last ${span}: fewer than three, so no bar is drawn.</p></section>`;
 const cols=t.columns||[];
 const silent=t.no_record_minutes==null?"the record's missing minutes were not read":`${t.no_record_minutes} minutes carried no load record`;
 const sentence=`Peak load ${t.peak_load==null?"&#8212;":Number(t.peak_load).toFixed(2)} against saturation at ${t.cores||1} cores; ${silent}.`;
 const seg=c=>{const pct=100*Math.max(0,c.share||0),painted=c.median_s>0;   // the fold's own share: the page never derives a second width
  return `<i class="ptb-seg${painted?` pts-${esc(c.column)}`:" ptb-none"}" style="width:${pct.toFixed(2)}%" title="${esc(profTripTitle(c))}"></i>`};
 const bar=`<div class=ptb role=img aria-label="${esc(`${n} deliveries in the last ${span}; the median time of each board column`)}">${cols.map(seg).join("")}</div>`;
 // One header names the Observations count on both tables, taken from the payload, and both print a token count in k
 // or M, so the phone and the desktop never drift (#2253).
 const obs=`Observations (${esc(t.observations_note||"Gating counts gate runs; every other column counts deliveries")})`;
 // The phone draws the lanes in place of the bar, with the peak and the saturation sentence above them.
 if(phone)return `<section id=proftrip>${head}<p class=tpn>${n} deliveries in the last ${span}: the chart folds ${cols.length} columns, one per board column, in the order of the rows below.</p>`
  +profTripFacts(t)+profTripLanes(t)
  +`<table id=proftt aria-label="The trip of an issue, a board column a row"><thead><tr><th>Column</th><th>Median</th><th>p90</th><th>${obs}</th><th>Tokens/issue</th><th>CPU</th><th>RAM</th></tr></thead><tbody>${cols.map(c=>`<tr data-p="trip/${esc(c.column)}"><td class=pn><i class="ptk ptk-${esc(c.column)}${c.median_s>0?"":" ptk-none"}" aria-hidden=true></i><span class=pnm>${esc(c.label)}</span></td><td>${profTripCell(c)}</td><td>${profTripNum(c.p90_s)}</td><td>${c.observations||0}</td><td>${profTokUnit(c.tokens_per_issue)}</td><td>${profTripPct((c.cpu||{}).mean)}</td><td>${profTripPct((c.ram||{}).mean)}</td></tr>`).join("")}</tbody></table></section>`;
 const dash="&#8212;";
 const rows=cols.map(c=>`<tr data-p="trip/${esc(c.column)}"><td class=pn><i class="ptk ptk-${esc(c.column)}${c.median_s>0?"":" ptk-none"}" aria-hidden=true></i><span class=pnm>${esc(c.label)}</span></td>`
  +`<td>${profTokUnit(c.tokens_per_issue)}</td>`
  +`<td>${c.cpu&&c.cpu.mean!=null?profPct(c.cpu.mean):dash}</td><td>${c.ram&&c.ram.mean!=null?profPct(c.ram.mean):dash}</td>`
  +`<td>${profTripCell(c)}</td><td>${profTripNum(c.p90_s)}</td><td>${c.observations||0}</td></tr>`).join("");
 // A RAM field the record never carried is named, so a dash never reads as zero memory use (#2253).
 const ramReason=t.ram_minutes===0?" No RAM reading in this window: the load record has no memory field there.":"";
 // A reading over ten times the core count is a burst of runnable threads, not a saturation reading; the page says so
 // and never prints it as the peak (#2253).
 const badLoad=t.invalid_loads>0?` ${t.invalid_loads} reading${t.invalid_loads===1?"":"s"} above the sane bound ${Number(t.load_bound).toFixed(0)} reported invalid, never the peak.`:"";
 return `<section id=proftrip>${head}<p class=tpn>${n} deliveries in the last ${span}: the bar folds ${cols.length} segments, one per board column, in the order of the rows below.</p>`
  +`<p class=tpn>${sentence}${badLoad}${ramReason}</p>${bar}`
  // The machine's memory in gigabytes is the RAM legend's hover: the percent hovers name a column's own share.
  +`<p class=tpn><span class=ptlg aria-hidden=true><i class=plk-cpu></i>CPU<i class=plk-ram></i><span${t.mem_gb==null?"":` title="the machine holds ${Number(t.mem_gb).toFixed(1)} GB of memory"`}>RAM</span></span> Tokens a minute; CPU and RAM on one scale, 0 to 100% of the machine.</p>`
  +profTripChart(t,cols)
  +`<p class=tpn>CPU and RAM are readings of the whole machine, never of one issue alone.</p>`
  +`<table id=proftt aria-label="The trip of an issue, a board column a row"><thead><tr><th>Column</th><th>Tokens/issue</th><th>Mean CPU</th><th>Mean RAM</th><th>Median</th><th>p90</th><th>${obs}</th></tr></thead><tbody>${rows}</tbody></table></section>`}
function profNote(v){
 if(!v.on)return "CPU sampling is off. On, the hub reads the process table every second and folds the loop of this profile into a hot-spot tree.";
 const at=v.last_reading?new Date(v.last_reading*1000):null,f=v.failed_readings||0;
 return `On: the last ${profSecs(v.window_s||0)} of the process table. `
  +(at?`Last good reading at <time datetime="${at.toISOString()}">${esc(at.toLocaleTimeString())}</time>`+(v.interval_s?`, ${profSecs(v.interval_s)} after the one before. `:". "):"No good reading yet. ")
  +(f?`<b class=lerr>${f} reading${f===1?"":"s"} failed.</b>`:"No reading failed.")
  +(v.coverage==null?"":v.coverage>=1?"Read every process.":`Read ${(100*v.coverage).toFixed(1)}% of the processes.`)
  +" A process that lives between two readings is not counted."}
function profHtml(v,open,phone){
 const head=`<div class=lph><h2>Loop</h2></div>`,trip=profTrip(v.trip,phone);
 // The switch ends the page: the readings stand above it, and the switch that turns them on or off follows them (owner ask).
 const foot=`<div class=lph id=proffoot>${profSw(v)}<span class=lpl aria-hidden=true>CPU sampling</span><span class=lpn>${profNote(v)}</span></div>${!v.write&&v.reason?`<p class=lpr>${esc(v.reason)}</p>`:""}`;
 if(!v.on||!v.tree)return head+trip+foot;   // the file views read files only, so the switch does not gate them
 const rows=[];profRows(v.tree,v.tree.name,1,open,rows);
 return head+trip+`<table id=proft role=treegrid aria-label="Hot spots of the loop"><thead><tr><th title="the node's share of the loop's CPU time in the window, with its children">Hot spot</th><th title="CPU seconds of the node's own processes">Self time</th><th title="CPU seconds of the node with its children; the rows sort by it">Total time</th><th title="the starts the sampler observed under the node, a second over the window; a lower bound, because a process that lives between two readings is not seen">Starts/s (lower bound)</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
  +(v.context?profCtx(v.context,open):"")+foot}
// profiler:end
let profV=null,profErr="",profPhone=false;const profOpen=new Set();   // profPhone: the page the render draws for (the phone drops the load chart, #2229)
function profDraw(){if(!profV)return;const b=document.getElementById("profb"),a=document.activeElement,row=a&&b.contains(a)?a.closest("tr[data-p]"):null,sw=!!a&&a.hasAttribute&&a.hasAttribute("data-prof");
 b.innerHTML=profHtml(profV,profOpen,profPhone)+(profErr?`<p class=lerr role=alert>${esc(profErr)}</p>`:"");
 const t=sw?b.querySelector("[data-prof]"):row?([...b.querySelectorAll("tr[data-p]")].find(r=>r.dataset.p===row.dataset.p)||b).querySelector("button"):null;if(t)t.focus()}   // a redraw replaces the rows: the focus returns to the control that held it
async function profPoll(){try{profV=await (await fetch("/api/profiler?w="+profWin)).json();profDraw()}catch(e){}}
async function profPost(on,client){profErr="";try{const r=await fetch("/api/profiler",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({on,client})}),d=await r.json();if(!r.ok)profErr=d.reason||d.err||"refused"}catch(e){profErr="the hub did not answer"}
 await profPoll()}
function profMount(client){profPhone=client==="phone";document.getElementById("profb").addEventListener("click",e=>{const s=e.target.closest("button[data-prof]");if(s){if(!s.disabled&&profV)profPost(!profV.on,client);return}
 const r=e.target.closest("tr[aria-expanded]");if(!r)return;const p=r.dataset.p;if(profOpen.has(p))profOpen.delete(p);else profOpen.add(p);profDraw()});}
