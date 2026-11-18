// The run sheet (#984): a run opens in a side sheet, as the approved mock draws it (#889, element #rk). This text is the
// sheet's model and its body. It is pure: it reads the run row the hub serves (/api/runs, /api/ended) and the run's record
// (record.jsonl, through the /dash/ proxy), and draws a string. The hub inlines it into the page; the tests run it in node.
const rkEsc=t=>String(t==null?"":t).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rkUsd=v=>"$"+(+v||0).toFixed(2);
const rkTok=v=>v>=1e6?(v/1e6).toFixed(1)+" M":v>=1e3?Math.round(v/1e3)+" k":String(v||0);
const rkMin=m=>{m=Math.round(+m||0);return m<60?m+" min":Math.floor(m/60)+" h "+String(m%60).padStart(2,"0")+" min"};
const rkClock=ts=>{if(!ts)return"";const d=new Date(ts*1000);return String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0")};
const rkIsGate=(n,gates)=>gates.has(n)||/_gate$/.test(n);
// The level of one event: an error stops something, a warn asks for a look, a gate is a gate at work, the rest is info.
function rkLevel(e,gates){const k=e.event||"",n=e.node||"";
  if(k==="outcome"&&e.outcome&&e.outcome!=="pass")return"error";
  if(k==="verdict"&&e.verdict!=="completed")return"error";
  if(/error|fail|refus/.test(k))return"error";
  if(/deny|skip|stall|timeout|kill|wedge/.test(k))return"warn";
  if(k==="gate_start"||k==="review_round"||k==="land"||(k==="outcome"&&rkIsGate(n,gates)))return"gate";
  return"info"}
// One line of text per event: the event, then the few facts the record carries for it.
function rkText(e){const k=e.event||"",why=(e.reasons||[])[0]||e.reason||"";
  const add=(a,b)=>b?a+" · "+b:a;
  if(k==="run_start")return add("run start",e.graph_name&&"graph "+e.graph_name);
  if(k==="call_start")return add("call start",e.entry!=null&&"entry "+e.entry);
  if(k==="gate_start")return"gate start";
  if(k==="progress")return["progress",e.messages!=null&&e.messages+" messages",e.context_tokens&&rkTok(e.context_tokens)+" context",e.tool].filter(Boolean).join(" · ");
  if(k==="outcome")return add(add("outcome",e.outcome),why);
  if(k==="review_round")return add("review round "+(e.round||""),e.verdict);
  if(k==="review_progress")return add("review running",e.elapsed&&Math.round(e.elapsed)+" s");
  if(k==="spawn_requested")return"child requested";
  if(k==="spawn_accepted")return add("child accepted",e.child);
  if(k==="land")return add("land",e.outcome);
  if(k==="verdict")return add("verdict",e.verdict);
  if(k==="compaction")return add("context compaction",e.trigger);
  return add(k||"event",why)}
// The wall time split of a run, as squad/run_time.py reads the record: seconds of suite, gate wait, model and idle.
function rkSplit(rec){const ts=rec.map(e=>+e.ts).filter(Number.isFinite),wall=ts.length?Math.max(...ts)-Math.min(...ts):0;
  const gates=new Set(rec.filter(e=>e.event==="gate_start").map(e=>e.node)),calls=new Set(rec.filter(e=>e.event==="call_start").map(e=>e.node));
  const bash={suite:0,gate:0};let agent=0,stage=0;
  rec.forEach(e=>{if(e.event==="progress"&&e.phase==="end"&&["suite","gate"].includes(e.command_class))bash[e.command_class]+=+e.elapsed||0;
    else if(e.event==="outcome"&&gates.has(e.node))stage+=+e.elapsed||0;
    else if(e.event==="outcome"&&calls.has(e.node))agent+=+e.elapsed||0});
  const suite=bash.suite+stage,model=Math.max(0,agent-bash.suite-bash.gate),idle=Math.max(0,wall-suite-bash.gate-model);
  return{wall,suite,gate_wait:bash.gate,model,idle}}
// The gate stages that ran more than once on one tree and suite, most seconds first, as squad/run_time.py reads them.
function rkRepeats(rec){const started=new Map(),groups=new Map();
  rec.forEach(e=>{const at=[e.node,e.entry,e.item].join("|");
    if(e.event==="gate_start")started.set(at,e);
    else if(e.event==="gate_result"){const b=started.get(at);started.delete(at);let s=0;
      if(b){const t0=b.started_at!=null?b.started_at:b.ts;s=Math.max(0,(+e.ts||0)-(+t0||0))}
      const k=[e.suite,e.sha,e.dirt].join("|"),g=groups.get(k)||{suite:e.suite,sha:e.sha,dirt:e.dirt,count:0,seconds:0};
      g.count++;g.seconds+=s;groups.set(k,g)}});
  return[...groups.values()].filter(g=>g.count>1).sort((a,b)=>b.seconds-a.seconds)}
// row: the run row the hub serves. rec: the record's events, in order. Returns the sheet's model.
function rkModel(row,rec){rec=(rec||[]).filter(e=>e&&typeof e==="object");
  const live=row.rank===0||!!row.running,gates=new Set(rec.filter(e=>e.event==="gate_start"&&e.node).map(e=>e.node));
  const start=rec.find(e=>e.event==="run_start")||{},order=[...(start.plan||[])];
  rec.forEach(e=>{const n=e.node;if(n&&!order.includes(n))order.push(n)});
  const st={},model={};
  rec.forEach(e=>{const n=e.node;if(!n)return;
    if(e.event==="call_start"||e.event==="gate_start")st[n]=st[n]||"started";
    if(e.event==="outcome"){st[n]=e.outcome==="pass"?"done":"fail";if(e.model&&e.entry!=null)model[n+"#"+e.entry]=e.model}});
  const nodes=order.map(n=>({n,gate:rkIsGate(n,gates),st:st[n]==="started"?(live?"run":"pend"):(st[n]||"pend")}));
  const per={};
  rec.filter(e=>e.event==="outcome"&&e.model).forEach(e=>{const r=per[e.model]=per[e.model]||{m:e.model,calls:0,tin:0,tout:0,usd:0,nodes:[]};
    r.calls++;r.tin+=+e.tokens_in||0;r.tout+=+e.tokens_out||0;r.usd+=+e.spend||0;if(!r.nodes.includes(e.node))r.nodes.push(e.node)});
  const models=Object.values(per).sort((a,b)=>b.usd-a.usd);
  const total=models.reduce((a,r)=>({usd:a.usd+r.usd,calls:a.calls+r.calls,tin:a.tin+r.tin,tout:a.tout+r.tout}),{usd:0,calls:0,tin:0,tout:0});
  const ev=rec.map((e,i)=>({i,ts:+e.ts||0,t:rkClock(e.ts),n:e.node||"run",m:(e.entry!=null&&model[e.node+"#"+e.entry])||e.model||"",l:rkLevel(e,gates),x:rkText(e).slice(0,240),
    tk:e.event==="outcome"&&e.tokens_in?rkTok(e.tokens_in)+" in":"",usd:e.spend?rkUsd(e.spend):""}))
    .sort((a,b)=>b.ts-a.ts||b.i-a.i);
  return{id:row.id,label:row.label||"",live,issues:(row.issues||[]).map(Number).filter(n=>n>0),graph:start.graph_name||"",
    chips:{state:live?"live":"ended",id:row.id,cost:rkUsd(row.spend),el:rkMin(row.dur)},nodes,models,total,ev,split:rkSplit(rec),repeats:rkRepeats(rec)}}
// f: {n, m, l}; an empty key means all. The warn level also shows the errors.
const rkFilter=(ev,f)=>ev.filter(e=>(!f.n||e.n===f.n)&&(!f.m||e.m===f.m)&&(!f.l||e.l===f.l||(f.l==="warn"&&e.l==="error")));
const RK_SN={done:"done",run:"running",fail:"failed",pend:"pending"};
function rkOverview(m){const issues=m.issues.map(n=>`<a href="#" data-o="${n}">#${n}</a>`).join(" ")||"none";
  const nodes=m.nodes.map(x=>`<span class="gn n-${x.st}${x.gate?" gate":""}" data-n="${rkEsc(x.n)}" tabindex="0" role="button" title="${rkEsc(x.n)}: ${RK_SN[x.st]}${x.gate?" · gate":""}; show its events"><i></i>${rkEsc(x.n)}</span>`).join("");
  const rows=m.models.map(r=>`<div class="mrow" data-m="${rkEsc(r.m)}" tabindex="0" role="button" title="show ${rkEsc(r.m)}'s events in the trace"><b>${rkEsc(r.m)}</b><span class="r">${rkUsd(r.usd)}</span><small>${r.calls} call${r.calls===1?"":"s"} · ${rkTok(r.tin)} in · ${rkTok(r.tout)} out · ${rkEsc(r.nodes.join(", "))}</small></div>`).join("");
  const last=m.ev.filter(e=>e.l!=="info").slice(0,2).map(e=>`<span class="lv ${e.l}">${e.l}</span>${rkEsc(e.t)} ${rkEsc(e.x)}`).join("<br>");
  return `<div class=row><b>Issues</b><span>${issues}</span></div>
<div class=row><b>Elapsed</b><span>${rkEsc(m.chips.el)}${m.graph?` · graph ${rkEsc(m.graph)}`:""}</span></div>
<div class=row><b>Spend</b><span>${rkEsc(m.chips.cost)} · ${rkTok(m.total.tin)} in · ${rkTok(m.total.tout)} out</span></div>
<div class=row><b>Wall time</b><span>${[["suite",m.split.suite],["gate wait",m.split.gate_wait],["model",m.split.model],["idle",m.split.idle]].map(([k,v])=>k+" "+rkMin(v/60)).join(" · ")}</span></div>
${m.repeats.length?`<div class=row><b>Repeated</b><span>${m.repeats.map(r=>`${rkEsc(r.suite)} ×${r.count}, ${rkMin(r.seconds/60)}`).join("<br>")}</span></div>`:""}
${last?`<div class=row><b>Last events</b><span>${last} <a href="#" data-rkt="tr">all</a></span></div>`:""}
<div class=sec>Nodes</div><div class=rkn>${nodes||"<span class=m>no node yet</span>"}</div>
<div class=sec>Models</div><div class=mt2>${rows||"<span class=m>no model call yet</span>"}${rows?`<div class="mrow tot"><b>Total</b><span class="r">${rkUsd(m.total.usd)}</span><small>${m.total.calls} calls</small></div>`:""}</div>
<p class=m>A node or a model row filters the trace.</p>`}
function rkTrace(m,f){const nodes=[...new Set([...m.nodes.map(x=>x.n),...m.ev.map(e=>e.n)])],mods=[...new Set(m.ev.map(e=>e.m).filter(Boolean))],rows=rkFilter(m.ev,f);
  const sel=(k,lab,opts)=>`<label>${lab} <select data-rkf="${k}" aria-label="filter by ${lab}"><option value="">all</option>${opts.map(([v,t])=>`<option value="${rkEsc(v)}"${f[k]===v?" selected":""}>${rkEsc(t)}</option>`).join("")}</select></label>`;
  return `<div class=trf>${sel("n","node",nodes.map(n=>[n,n]))}${sel("m","model",mods.map(x=>[x,x]))}${sel("l","level",[["gate","gate"],["warn","warn + error"],["error","error"]])}${f.n||f.m||f.l?`<button data-rkf="clear">clear</button>`:""}<span class=m>${rows.length} of ${m.ev.length} · newest first</span></div>
<div class=trl role=list>${rows.map(e=>`<div class="te ${e.l}" role=listitem><span class=tt>${rkEsc(e.t)}</span><span class=tn>${rkEsc(e.n)}</span><span class=tm>${rkEsc(e.m)}</span><span class=tv>${[e.tk,e.usd].filter(Boolean).join(" · ")}</span><span class=tx><span class="lv ${e.l}">${e.l}</span>${rkEsc(e.x)}</span></div>`).join("")||"<p class=m>No event matches these filters.</p>"}</div>`}
// The sheet's body: the title, the two tabs, and the tab's panel. tab: "ov" or "tr"; f: the trace filters.
function rkHtml(m,tab,f){f=f||{};
  return `<h2>${rkEsc(m.label||m.id)}</h2>
<div class=rkt role=tablist>${[["ov","Overview"],["tr","Trace"]].map(([k,l])=>`<button role="tab" data-rkt="${k}" aria-selected="${tab===k}" class="${tab===k?"on":""}">${l}</button>`).join("")}</div>
<div role=tabpanel>${tab==="tr"?rkTrace(m,f):rkOverview(m)}</div>`}
// The sheet's controls (#984). st: {tab, f}; t: the event target. A link opens its ticket ({o}); a tab, a node, a model
// row or clear answers the new state ({st}); anything else answers null.
function rkAct(st,t){const a=t.closest("a[data-o]");if(a)return{o:+a.dataset.o};
  const k=t.closest("[data-rkt]");if(k)return{st:{tab:k.dataset.rkt,f:st.f||{}}};
  const g=t.closest(".gn[data-n]");if(g)return{st:{tab:"tr",f:{n:g.dataset.n}}};
  const m=t.closest(".mrow[data-m]");if(m)return{st:{tab:"tr",f:{m:m.dataset.m}}};
  if(t.closest("button[data-rkf=clear]"))return{st:{tab:st.tab,f:{}}};
  return null}
// Enter or Space on a focused node or model row acts as its click; any other key does nothing.
const rkKey=(st,t,key)=>(key==="Enter"||key===" ")&&t.matches(".gn[data-n],.mrow[data-m]")?rkAct(st,t):null;
// Escape closes the run sheet first, then the ticket sheet.
const rkEscape=(key,runOpen,ticketOpen)=>key!=="Escape"?null:runOpen?"run":ticketOpen?"ticket":null;
// The control that holds the focus, as a selector that finds it again in a new body.
function rkFocusKey(el){for(const a of["data-n","data-m","data-rkt","data-rkf","data-o"]){const v=el.getAttribute&&el.getAttribute(a);
  if(v!=null)return`${el.tagName.toLowerCase()}[${a}="${String(v).replace(/["\\]/g,"\\$&")}"]`}return null}
// Paint the sheet body b with html (review finding 1 on #984). The 3 s poll paints only when the text changed, so an ended
// run is never repainted; a repaint puts the focus back on the same control; a poll never repaints under an open filter.
// force: the person acted (a tab, a filter, a choice), so the body paints even while a filter has the focus.
function rkRender(b,html,doc,force){if(b.rkHtml===html)return false;
  const ae=doc.activeElement,inside=!!ae&&b.contains(ae);
  if(inside&&!force&&ae.tagName==="SELECT")return false;   // a filter being chosen: the next poll paints
  const key=inside?rkFocusKey(ae):null,top=b.scrollTop;
  b.innerHTML=html;b.rkHtml=html;b.scrollTop=top;
  if(key){const el=b.querySelector(key);if(el&&el.focus)el.focus()}
  return true}
