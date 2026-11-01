// The board's swimlanes: one text, inlined by the hub into the desktop board. It is pure: it reads the served rows and the
// page's options, and draws nothing. A spec's lane is headed by its spec card; its cards are the rows whose `pa` names it
// (the parent the read carries, #829). Loop bugs (ADR-0146) sit in the first lane; cards with no spec sit in the last.
// Each column the board draws is a name from the page's own column list, so no column is named here.
const hasLabel=(x,l)=>(x.l||[]).includes(l);
// A spec ticket's title opens with its own tag, "[SPEC]" or "[SPEC 889]": the lane and the card already say it, so the title is read without it (#889).
const plain=t=>String(t==null?"":t).replace(/^\s*\[SPEC(?:\s*#?\d+)?\]\s*/i,"");
// A cell shows every card it holds: the owner reads the whole lane at once, so no cap and no "+N more" (#891).
const cellView=rows=>({shown:rows,more:0});
const laneWaveOf=x=>{const w=(x.l||[]).map(l=>(String(l).match(/^wave:(\d+)$/)||[])[1]).filter(v=>v!==undefined).map(Number);return w.length?Math.min(...w):null};
const waveKey=x=>{const w=laneWaveOf(x);return w===null?"none":String(w)};
const specKey=x=>x.pa==null?"none":String(x.pa);
const SPEC_COLOURS=8;
const numCmp=(a,b)=>a==null?(b==null?0:1):b==null?-1:a-b;
// A wave belongs to one spec: the same wave of two specs is two lanes and two Waves options (#980).
const waveOptKey=x=>specKey(x)+"/"+waveKey(x);
const waveLabel=(spec,wave)=>(spec==null?"No spec":"SPEC "+spec)+" · "+(wave==null?"No wave":"Wave "+wave);
const waveLane=(spec,wave)=>{const num=spec==null?null:+spec;return[(num==null?"none":String(num))+"/"+(wave==null?"none":String(wave)),
  {kind:"sw",num,wave,title:waveLabel(num,wave),sid:num==null?"No spec":"SPEC "+num,wl:wave==null?"No wave":"Wave "+wave}]};
// The Waves filter's options: one per spec and wave the rows hold, in spec then wave order, each named for its spec (#980).
function waveOptions(rows){
  const seen=new Map();
  rows.forEach(x=>{if(x.sp)return;const key=waveOptKey(x);if(!seen.has(key))seen.set(key,{key,spec:x.pa==null?null:+x.pa,wave:laneWaveOf(x)})});
  return[...seen.values()].sort((a,b)=>numCmp(a.spec,b.spec)||numCmp(a.wave,b.wave)).map(o=>({key:o.key,label:waveLabel(o.spec,o.wave)}))}
// The filters (#899): an empty list means all; the two lists combine with AND. Keys are the values the bar offers.
const boardOk=(x,fw,fs)=>(!fw.length||fw.includes(waveOptKey(x)))&&(!fs.length||fs.includes(specKey(x)));
// groups: the issue columns in board order, each { name, items }. o: { mode: "spec"|"wave", fw, fs, text, lc, hideDone,
// closed, hidden }. `text` is the search predicate; progress counts every row, so a search or a filter never moves a bar.
// Returns { cols, vc, hc, lanes }: vc are the columns drawn, hc the hidden ones with their counts, lanes the shown lanes in order.
function buildBoard(groups,o){
  const mode=o.mode,cols=groups.map(g=>g.name),closed=o.closed,fw=o.fw||[],fs=o.fs||[],text=o.text||(()=>true);
  const hasFilter=!!(fw.length||fs.length||o.search);
  const specs=new Map(),rows=[];
  const gone=new Set([closed,o.abandoned||"Abandoned"]),goneSpecs=new Set();   // a spec whose card is closed or abandoned has no work left: no lane (#890)
groups.forEach(g=>g.items.forEach(x=>{if(x.sp){if(gone.has(g.name))goneSpecs.add(x.n);else if(!specs.has(x.n))specs.set(x.n,{title:plain(x.t),st:g.name,sc:x.sc});   // a spec card carries its own closed/total (#890)
 return}rows.push({...x,col:g.name})}));
  const lanes=new Map(),ensure=(id,meta)=>{if(!lanes.has(id))lanes.set(id,{id,...meta,all:[],vis:[]});return lanes.get(id)};
  const laneOf=x=>{
    if(hasLabel(x,"loop-bug"))return["bugs",{kind:"bugs",title:"Loop bugs"}];
    if(mode==="wave")return waveLane(x.pa,laneWaveOf(x));
    if(x.pa==null)return["none",{kind:"none",title:"No spec"}];
    const sp=specs.get(x.pa)||{title:"",st:""};
    return["s"+x.pa,{kind:"s",num:x.pa,title:sp.title,st:sp.st,sc:sp.sc}]};
  if(mode!=="wave")specs.forEach((sp,num)=>ensure("s"+num,{kind:"s",num,title:sp.title,st:sp.st,sc:sp.sc}));   // a spec card on the board is always a lane
  rows.forEach(x=>{if(goneSpecs.has(x.pa)&&gone.has(x.col))return;   // a closed spec keeps no lane: its open card falls to No spec, its closed card is history (#890)
  const[id,meta]=goneSpecs.has(x.pa)?(mode==="wave"?waveLane(null,laneWaveOf(x)):["none",{kind:"none",title:"No spec"}]):laneOf(x),l=ensure(id,meta);l.all.push(x);if(boardOk(x,fw,fs)&&text(x))l.vis.push(x)});
  const shown=[...lanes.values()].filter(l=>l.vis.length>0||(!hasFilter&&l.kind==="s"));
  shown.forEach(l=>{l.n=l.vis.length;l.total=l.all.length;l.closed=l.all.filter(x=>x.col===closed).length;
    const m=/^(\d+)\/(\d+)$/.exec(String(l.sc||""));   // a closed ticket never reaches the board with its parent, so the count comes from the spec card
    if(l.kind==="s"&&m){l.closed=+m[1];l.total=+m[2]}
    l.done=l.total>0&&l.closed===l.total;l.live=l.vis.some(x=>!!x.ho);l.counts={};l.vis.forEach(x=>{l.counts[x.col]=(l.counts[x.col]||0)+1})});
  const rank=l=>l.kind==="bugs"?0:(l.kind==="none"||(l.kind==="sw"&&l.num==null))?2:1;
  const order=(a,b)=>mode==="wave"?(numCmp(a.num,b.num)||numCmp(a.wave,b.wave)):((b.live-a.live)||((a.num??0)-(b.num??0)));
  const list=shown.filter(l=>!(o.hideDone&&l.done)).sort((a,b)=>rank(a)-rank(b)||order(a,b));
  const palette=new Map();   // a spec keeps one colour across all its lanes; colours go to specs in lane order
  list.forEach(l=>{if(l.num!=null&&!palette.has(l.num))palette.set(l.num,palette.size%SPEC_COLOURS)});
  list.forEach(l=>{l.pi=l.num==null?null:palette.get(l.num)});
  const lc=o.lc||{};
  list.forEach(l=>{l.collapsed=Object.prototype.hasOwnProperty.call(lc,l.id)?!!lc[l.id]:l.done});   // a done lane starts collapsed (#891)
  const open=list.filter(l=>!l.collapsed);
  const hide=new Set(o.hidden||[]);   // the user's hidden columns: never drawn, named in the pill with their whole total
  const vc=cols.filter(c=>!hide.has(c)&&open.some(l=>l.counts[c]));   // a column with no card in an expanded lane is not drawn
  const hc=cols.filter(c=>!vc.includes(c)&&!hide.has(c)&&list.some(l=>l.counts[c])).map(c=>({name:c,count:list.reduce((s,l)=>s+(l.counts[c]||0),0)}))
    .concat(groups.filter(g=>hide.has(g.name)).map(g=>({name:g.name,count:g.items.length,user:true})));
  const ec=[];   // the end columns stay hidden: no toggle draws them (#968)
  list.forEach(l=>{l.cells={};cols.forEach(c=>{l.cells[c]=l.vis.filter(x=>x.col===c)})});
  return{cols,vc,hc,ec,lanes:list};
}

// A saved state from a control the board no longer has is dropped on load (#899): the labels filter, the Specs mode,
// maximize, collapse and the pip. The kept keys (the filters, the lanes, the view) stay as they are.
function dropRemoved(bo){for(const k of["labels","label","specs","max","off","pip"])delete bo[k];if(bo.fw)bo.fw=bo.fw.filter(k=>String(k).includes("/"));return bo}   // a Waves key is spec/wave now; a bare wave key is from before (#980)
