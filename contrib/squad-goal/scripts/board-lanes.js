// The board's swimlanes: one text, inlined by the hub into the desktop board. It is pure: it reads the served rows and the
// page's options, and draws nothing. A spec's lane is headed by its spec card; its cards are the rows whose `pa` names it
// (the parent the read carries, #829). Loop bugs (ADR-0146) sit in the first lane; cards with no spec sit in the last.
// Each column the board draws is a name from the page's own column list, so no column is named here.
const hasLabel=(x,l)=>(x.l||[]).includes(l);
// A cell shows its first three cards and a "+N more" control for the rest; an open cell shows them all (#891).
const LANE_CELL=3;
const cellView=(rows,open)=>open?{shown:rows,more:0}:{shown:rows.slice(0,LANE_CELL),more:Math.max(0,rows.length-LANE_CELL)};
const laneWaveOf=x=>{const w=(x.l||[]).map(l=>(String(l).match(/^wave:(\d+)$/)||[])[1]).filter(v=>v!==undefined).map(Number);return w.length?Math.min(...w):null};
const waveKey=x=>{const w=laneWaveOf(x);return w===null?"none":String(w)};
const specKey=x=>x.pa==null?"none":String(x.pa);
// The filters (#899): an empty list means all; the two lists combine with AND. Keys are the values the bar offers.
const boardOk=(x,fw,fs)=>(!fw.length||fw.includes(waveKey(x)))&&(!fs.length||fs.includes(specKey(x)));
// groups: the issue columns in board order, each { name, items }. o: { mode: "spec"|"wave", fw, fs, text, lc, hideDone,
// closed }. `text` is the search predicate; progress counts every row, so a search or a filter never moves a bar.
// Returns { cols, vc, hc, lanes }: vc are the columns drawn, hc the hidden ones with their counts, lanes the shown lanes in order.
function buildBoard(groups,o){
  const mode=o.mode,cols=groups.map(g=>g.name),closed=o.closed,fw=o.fw||[],fs=o.fs||[],text=o.text||(()=>true);
  const hasFilter=!!(fw.length||fs.length||o.search);
  const specs=new Map(),rows=[];
  groups.forEach(g=>g.items.forEach(x=>{if(x.sp){if(!specs.has(x.n))specs.set(x.n,{title:x.t,st:g.name});return}rows.push({...x,col:g.name})}));
  const lanes=new Map(),ensure=(id,meta)=>{if(!lanes.has(id))lanes.set(id,{id,...meta,all:[],vis:[]});return lanes.get(id)};
  const laneOf=x=>{
    if(hasLabel(x,"loop-bug"))return["bugs",{kind:"bugs",title:"Loop bugs"}];
    if(mode==="wave"){const w=laneWaveOf(x);return w===null?["nowave",{kind:"nowave",title:"No wave"}]:["w"+w,{kind:"w",num:w,title:"Wave "+w}]}
    if(x.pa==null)return["none",{kind:"none",title:"No spec"}];
    const sp=specs.get(x.pa)||{title:"",st:""};
    return["s"+x.pa,{kind:"s",num:x.pa,title:sp.title,st:sp.st}]};
  if(mode!=="wave")specs.forEach((sp,num)=>ensure("s"+num,{kind:"s",num,title:sp.title,st:sp.st}));   // a spec card on the board is always a lane
  rows.forEach(x=>{const[id,meta]=laneOf(x),l=ensure(id,meta);l.all.push(x);if(boardOk(x,fw,fs)&&text(x))l.vis.push(x)});
  const shown=[...lanes.values()].filter(l=>l.vis.length>0||(!hasFilter&&l.kind==="s"));
  shown.forEach(l=>{l.n=l.vis.length;l.total=l.all.length;l.closed=l.all.filter(x=>x.col===closed).length;
    l.done=l.total>0&&l.closed===l.total;l.live=l.vis.some(x=>!!x.ho);l.counts={};l.vis.forEach(x=>{l.counts[x.col]=(l.counts[x.col]||0)+1})});
  const rank=l=>l.kind==="bugs"?0:(l.kind==="none"||l.kind==="nowave")?2:1;
  const list=shown.filter(l=>!(o.hideDone&&l.done)).sort((a,b)=>rank(a)-rank(b)||(b.live-a.live)||((a.num??0)-(b.num??0)));
  const lc=o.lc||{};
  list.forEach(l=>{l.collapsed=Object.prototype.hasOwnProperty.call(lc,l.id)?!!lc[l.id]:l.done});   // a done lane starts collapsed (#891)
  const open=list.filter(l=>!l.collapsed);
  const vc=cols.filter(c=>open.some(l=>l.counts[c]));   // a column with no card in an expanded lane is not drawn
  const hc=cols.filter(c=>!vc.includes(c)&&list.some(l=>l.counts[c])).map(c=>({name:c,count:list.reduce((s,l)=>s+(l.counts[c]||0),0)}));
  list.forEach(l=>{l.cells={};cols.forEach(c=>{l.cells[c]=l.vis.filter(x=>x.col===c)})});
  return{cols,vc,hc,lanes:list};
}
