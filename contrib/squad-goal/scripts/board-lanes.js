// The board's swimlanes: one text, inlined by the hub into the desktop board. It is pure: it reads the served rows and the
// page's options, and draws nothing. A spec's lane is headed by its spec card; its cards are the rows whose `pa` names it
// (the parent the read carries, #829). Loop bugs (ADR-0146) sit in the first lane; cards with no spec sit in the last.
// Each column the board draws is a name from the page's own column list, so no column is named here.
const hasLabel=(x,l)=>(x.l||[]).includes(l);
// A spec ticket's title opens with its own tag, "[SPEC]" or "[SPEC 889]": the lane and the card already say it, so the title is read without it (#889).
const plain=t=>String(t==null?"":t).replace(/^\s*\[SPEC(?:\s*#?\d+)?\]\s*/i,"");
// A cell shows every card it holds: the owner reads the whole lane at once, so no cap and no "+N more" (#891).
const cellView=rows=>({shown:rows,more:0});
// A visible column's width (#1994): the weight of its card count, clamped to 1..4, on a track that never goes below
// 320 px. An empty column keeps one share, so it stays visible at the minimum width and never takes room from a lane.
const COL_MIN="320px", COL_WEIGHT_MAX=4;
const colWeight=n=>Math.max(1,Math.min(COL_WEIGHT_MAX,n||0));
const colTracks=counts=>counts.map(n=>`minmax(${COL_MIN},${colWeight(n)}fr)`).join(" ");
const specKey=x=>x.pa==null?"none":String(x.pa);
const numCmp=(a,b)=>a==null?(b==null?0:1):b==null?-1:a-b;
// A card's kind for the Kinds filter (#904): a pull request, a bug (labelled bug or loop-bug), or an issue.
const kindKey=x=>x.kind==="pr"?"pr":(hasLabel(x,"bug")||isLoopBug(x))?"bug":"issue";
const KIND_OPTIONS=[["issue","Issue"],["bug","Bug"],["pr","PR"]];
// The filters (#899, #904): an empty list means all; the lists combine with AND. Keys are the values the bar offers.
// A `wave:NN` label is no filter and no lane (SPEC 1965, #1988).
const boardOk=(x,fs,fk=[])=>(!fs.length||fs.includes(specKey(x)))&&(!fk.length||fk.includes(kindKey(x)));
// A spec the summary names closed or abandoned: finished, so it has no lane and no option (#972).
const goneSpecsOf=(groups,sum={})=>{const ends=new Set(groups.filter(g=>g.end).map(g=>g.name));return new Set((sum.specs||[]).filter(s=>ends.has(s.st)).map(s=>s.n))};
// The Specs options (#981): a spec is one only while it is unfinished and an open ticket sits under it. A group with `end`
// is a closed status (Closed or Abandoned, by role): history, so it holds no option (#969).
function specOptions(groups,sum={}){
  const gone=goneSpecsOf(groups,sum),parents=new Set();let none=false;
  groups.forEach(g=>{if(g.end)return;g.items.forEach(x=>{if(x.sp)return;
    if(x.pa==null){none=true;return}
    if(!gone.has(x.pa))parents.add(String(x.pa))})});
  const keys=[...parents];if(none)keys.push("none");
  return keys.sort((a,b)=>a==="none"?1:b==="none"?-1:+a-+b)}
// A saved hidden set drops every closed status on load: history is never a hidden column (#969).
const keptHidden=(saved,groups)=>{const ends=new Set(groups.filter(g=>g.end).map(g=>g.name));return saved.filter(n=>!ends.has(n))};
// <card-rules>: the spec color rules; the mobile page inlines this block alone (#917).
// A spec's color (#917): the spec number picks a palette slot, number mod 8. A slot already taken goes to the next free
// slot, so two specs differ while the palette has room. Specs are read in number order, so the board order changes nothing.
const SPEC_GREY="var(--mute)";   // the stale board's cue alone (#1640): a board the hub names stale is drawn grey, never a card
const specSlots=nums=>{const m=new Map(),used=new Set();
  [...new Set(nums.map(Number).filter(n=>Number.isFinite(n)))].sort((a,b)=>a-b).forEach(n=>{let s=n%8;
    if(used.size<8)while(used.has(s))s=(s+1)%8;used.add(s);m.set(n,s+1)});return m};
const specColor=(slots,n)=>n==null||!Number.isFinite(Number(n))?SPEC_GREY:`var(--s${slots.get(Number(n))||Number(n)%8+1})`;
const cardSpec=x=>x.sp?x.n:(x.pa??x.n);   // a spec card wears its own color, and a card of no spec its number's: a card is never grey (#1640)
// The loop-bug mark is the prefix rule the dispatcher reads (ADR-0146): bare `loop-bug` or any `loop-bug:<class>`. The
// label itself is returned, so a reader names the class the card carries.
const loopBugLabel=x=>(x.l||[]).find(l=>l==="loop-bug"||String(l).startsWith("loop-bug:"))||null;
const isLoopBug=x=>loopBugLabel(x)!=null;
const cardColor=(slots,x)=>isLoopBug(x)?"var(--err)":specColor(slots,cardSpec(x));   // a loop-bug has no spec: it wears the Loop bugs lane red
// </card-rules>
const SPEC_IN_FLIGHT=["Todo","In Progress","Doing","Code Complete","Reviewing","Batching","Testing","Review","Landing","Gating"];   // a ticket in flight, identical to board_facts.py LIVE_WORK
// groups: the issue columns in board order, each { name, items }. o: { fs, fk, text, lc, hideDone, closed, hidden }.
// `text` is the search predicate; progress counts every row, so a search or a filter never moves a bar.
// Returns { cols, vc, hc, lanes }: vc are the columns drawn, hc the hidden ones with their counts, lanes the shown lanes in order.
function buildBoard(groups,o){
  const ends=new Set(groups.filter(g=>g.end).map(g=>g.name)),cols=groups.filter(g=>!g.end).map(g=>g.name),closed=o.closed,fs=o.fs||[],fk=o.fk||[],text=o.text||(()=>true);
  const hasFilter=!!(fs.length||fk.length||o.search);
  const specs=new Map(),rows=[];
  const goneSpecs=goneSpecsOf(groups,o.sum);   // a spec whose card is closed or abandoned has no work left: no lane (#890, #972)
  const sumSpec=new Map((o.sum?.specs||[]).map(s=>[s.n,s]));
groups.forEach(g=>g.items.forEach(x=>{if(x.sp){if(!ends.has(g.name)&&!specs.has(x.n))specs.set(x.n,{title:plain(x.t),st:g.name,sc:x.sc});   // a spec card carries its own closed/total (#890)
 return}rows.push({...x,col:g.name})}));
  const lanes=new Map(),ensure=(id,meta)=>{if(!lanes.has(id))lanes.set(id,{id,...meta,all:[],vis:[]});return lanes.get(id)};
  const laneOf=x=>{
    if(isLoopBug(x))return["bugs",{kind:"bugs",title:"Loop bugs"}];
    if(x.pa==null)return["none",{kind:"none",title:"No spec"}];
    const sp=specs.get(x.pa)||{title:"",st:""};
    return["s"+x.pa,{kind:"s",num:x.pa,title:sp.title,st:sp.st,sc:sp.sc}]};
  specs.forEach((sp,num)=>ensure("s"+num,{kind:"s",num,title:sp.title,st:sp.st,sc:sp.sc}));   // a spec card on the board is always a lane
  rows.forEach(x=>{if(goneSpecs.has(x.pa)&&ends.has(x.col))return;   // a closed spec keeps no lane: its open card falls to No spec, its closed card is history (#890)
  const[id,meta]=goneSpecs.has(x.pa)?["none",{kind:"none",title:"No spec"}]:laneOf(x),l=ensure(id,meta);l.all.push(x);if(!ends.has(x.col)&&boardOk(x,fs,fk)&&text(x))l.vis.push(x)});   // a closed status is history: its cards count for progress, never for a card, a count or the search (#969)
  const shown=[...lanes.values()].filter(l=>l.vis.length>0||(!hasFilter&&l.kind==="s"));
  shown.forEach(l=>{l.n=l.vis.length;l.total=l.all.length;l.closed=l.all.filter(x=>x.col===closed).length;
    l.doing=o.doing?l.all.filter(x=>x.col===o.doing).length:0;
    l.review=o.review?l.all.filter(x=>x.col===o.review).length:0;   // the review share of the bar, in its own colour after the held share (owner, 2026-11-02)   // the bar's held share: the cards a run claims, drawn in the closed green read through (owner, 2026-11-02)
    const s=l.kind==="s"?sumSpec.get(l.num):l.kind==="none"?o.sum?.none:null;   // No spec: open cards plus the last seven days closed, counted by the hub (owner)   // a closed row is never served: the summary counts it (#972)
    if(s){l.closed=s.closed;l.total=s.total;if(l.kind==="s"){l.far=s.far;l.farN=s.far_n}}   // the tickets' furthest column, a count beside the spec, never its own column (#1906)
    const m=/^(\d+)\/(\d+)$/.exec(String(l.sc||""));   // a closed ticket never reaches the board with its parent, so the count comes from the spec card
    if(l.kind==="s"&&m){l.closed=+m[1];l.total=+m[2]}
    l.done=l.total>0&&l.closed===l.total&&!l.all.some(x=>!ends.has(x.col));l.inFlight=l.all.filter(x=>SPEC_IN_FLIGHT.includes(x.col)).length;l.started=l.kind==="s"&&(l.st==="Todo"||l.inFlight>0);l.counts={};l.vis.forEach(x=>{l.counts[x.col]=(l.counts[x.col]||0)+1})});
  const frac=l=>l.total>0?l.closed/l.total:0;
  const rank=l=>l.kind==="bugs"?0:l.kind==="none"?3:l.started?1:2;
  const order=(a,b)=>a.started?((frac(b)-frac(a))||(b.inFlight-a.inFlight)||((a.num??0)-(b.num??0))):((a.num??0)-(b.num??0));
  const list=shown.filter(l=>!(o.hideDone&&l.done)).sort((a,b)=>rank(a)-rank(b)||order(a,b));
  const lc=o.lc||{};
  list.forEach(l=>{l.collapsed=Object.prototype.hasOwnProperty.call(lc,l.id)?!!lc[l.id]:l.done});   // a done lane starts collapsed (#891)
  const hide=new Set(o.hidden||[]);   // the user's hidden columns: never drawn, named in the pill with their whole total
  // Every column the user leaves visible is drawn, even one holding no card at all (#1994): an empty visible column
  // keeps its minimum width and its label, so a group shows each column the board has and an empty one reads as
  // empty, never as missing. Only the user's hidden set removes a column from the board.
  const vc=cols.filter(c=>!hide.has(c));
  const hc=groups.filter(g=>!g.end&&hide.has(g.name)).map(g=>({name:g.name,count:g.items.length,user:true}));
  list.forEach(l=>{l.cells={};cols.forEach(c=>{l.cells[c]=l.vis.filter(x=>x.col===c)})});
  return{cols,vc,hc,lanes:list};
}

// The lane header takes two rows of flex items (#2026). Row 1 holds the spec number, the title, the column chip, the GitHub
// mark and the Sync press, the title taking the free space. Row 2 holds the progress bar, the fraction and the counts, the
// counts at the right edge.
const laneHeadCols=[{k:"id",al:"flex-end"},{k:"title",al:"flex-start"},{k:"bar",al:"flex-start"},
  {k:"frac",al:"flex-end"},{k:"chip",al:"center"},{k:"gh",al:"flex-end"},{k:"sync",al:"flex-end"},
  {k:"counts",al:"flex-end"}];
// A spec's header carries the same Sync press its tickets do (#1667): the spec is an issue on the same board, and its
// title, column, sub-issue count and tree come from the same read.
const laneHeadRows=[["id","title","chip","gh","sync"],["bar","frac","counts"]];
// v names the three counted columns: { todo, doing, review }. A zero count is an en dash, so its row still stands.
// Every lane keeps its counts row: a collapsed lane's only summary, and a zero dash keeps the row from moving.
function laneHead(l,v){
  const fact=k=>({...laneHeadCols.find(c=>c.k===k)});
  const counted=l.kind==="s"||l.kind==="none";
  const idText=l.kind==="s"?"SPEC "+l.num:l.kind==="bugs"?"Loop bugs":l.title;
  const titleText=l.kind==="s"?l.title:l.kind==="bugs"?"defects of the loop itself: a prefixed label halts dispatch, and loop-bug:landing halts the landing too (ADR-0146)":l.kind==="none"?"bugs and single tickets":"";
  const count=n=>l.counts[n]?String(l.counts[n]):"–";
  // A spec keeps its own column (the chip), and the furthest column its tickets reached stands beside it as one more count
  // of the counts row: the fold of the children never draws as the spec's column (#1906).
  const counts=[v.todo,v.doing,v.review].map(n=>[n,count(n)]);
  if(l.kind==="s"&&l.far)counts.push(["furthest",l.far+(l.farN?` ${l.farN}`:"")]);
  const cell={
    id:{...fact("id"),text:idText},
    title:{...fact("title"),text:titleText},
    bar:{...fact("bar"),on:counted,closed:l.closed,total:l.total,doing:l.doing,review:l.review},
    frac:{...fact("frac"),text:counted?l.closed+"/"+l.total:""},
    chip:{...fact("chip"),text:l.st||""},
    gh:{...fact("gh"),on:l.kind==="s"},
    sync:{...fact("sync"),on:l.kind==="s",n:l.num},
    counts:{...fact("counts"),items:counts}};
  return{rows:laneHeadRows.map(r=>r.map(k=>cell[k]))};
}

// A saved state from a control the board no longer has is dropped on load (#899): the labels filter, the Specs mode,
// maximize, collapse, the pip, the Waves filter (#1988), and the lane mode with the Specs and Kinds filter (owner ask, SPEC 1965).
// The kept keys (the lanes, the view) stay as they are.
function dropRemoved(bo){for(const k of["labels","label","specs","max","off","pip","fw","lmode","fs","fk"])delete bo[k];return bo}
