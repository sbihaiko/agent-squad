// Board rules of the mobile page. Pure: no DOM and no network. The hub inlines this text into hub-mobile.html.
// The swimlanes of one column (#893): its cards grouped by spec, each lane headed by its number and progress (SPEC 2498,
// #2501: spec grouping is the one grouping Work draws). A lane holds its spec's cards, the spec's own card first. Progress
// counts the spec's children on the whole board, never the column alone. "No spec" is the last lane.
// A `wave:NN` label never makes a lane (SPEC 1965, #1988).
// `lc` holds the saved collapse by lane id; a lane whose spec is done starts collapsed. `flat` is the cards the column
// draws, in order, with the collapsed lanes left out.
const cmpNum = (a, b) => a == null ? (b == null ? 0 : 1) : b == null ? -1 : a - b;
const columnLanes = ({ groups, cards, lc, sum }) => {
  const ends = new Set((groups || []).filter(g => g.end).map(g => g.name));
  const specs = (sum && sum.specs) || [];
  const gone = new Set(specs.filter(s => ends.has(s.st)).map(s => s.n));   // a closed spec keeps no lane (#972)
  const specSum = new Map(specs.map(s => [s.n, s]));
  const by = new Map();
  const ensure = spec => {
    const id = spec == null ? "none" : String(spec);
    if (!by.has(id)) by.set(id, { id, spec: spec ?? null, cards: [], headed: false });
    return by.get(id);
  };
  cards.forEach(x => { if (!x.sp) ensure(gone.has(x.pa) ? null : x.pa).cards.push(x); });
  cards.forEach(x => {
    if (!x.sp) return;   // a spec card heads its spec's lane in this column
    const lane = ensure(x.n);
    lane.cards.unshift(x);
    lane.headed = true;
  });
  const lanes = [...by.values()].map(l => {
    const s = l.headed ? specSum.get(l.spec) : null;   // progress is the summary's: a closed ticket is served by no row (#972)
    const t = s ? s.total : 0, d = s ? s.closed : 0;
    const isDone = t > 0 && d === t && !l.cards.some(x => !x.sp);
    const collapsed = Object.prototype.hasOwnProperty.call(lc || {}, l.id) ? !!lc[l.id] : isDone;
    const sid = l.spec == null ? "No spec" : "SPEC " + l.spec;
    return { id: l.id, num: l.spec, sid, title: sid, total: t, closed: d, collapsed, cards: l.cards };
  }).sort((a, b) => cmpNum(a.num, b.num));
  const flat = lanes.flatMap(l => l.collapsed ? [] : l.cards);
  return { lanes, flat };
};
// The cards a board read gives that no earlier read gave (owner, 2026-11-01): only these take the entry fade, so the poll
// and a column switch never play it again on a card already seen. The first read marks no card new.
const freshCards = (seen, keys) => { if (seen == null) return []; const s = new Set(seen); return keys.filter(k => !s.has(k)); };
// The empty-column note (#915): a column with no card says so, and a board with no column says it is loading.
const emptyColumnNote = ({ groups, shown }) =>
  !groups ? "Loading the board." : !shown ? "No column holds a card." : "No card in this column.";
// The three list states (#1445): one rule answers the line every list draws above its content. `ever` says a reply
// arrived, `n` counts the rows the list holds, and `error` names the last read's failure. A list says 'loading' until
// its first reply; a successful empty reply draws one sentence naming the missing data; a failed read draws one error
// line and never drops what the list holds. An empty sentence stays beside the error line when there was nothing to keep.
const listState = ({ ever, n, error, loading, empty }) => ({
  line: !ever && !error ? loading : ever && !n ? empty : "",
  error: error || "",
});
// The pull to refresh the Runs list: a downward swipe of PULL_MIN from the top of the page.
const PULL_MIN = 60;
const runsGesture = ({ dx, dy, atTop }) => {
  const ax = Math.abs(dx), ay = Math.abs(dy);
  if (atTop && dy >= PULL_MIN && ay > ax) return { act: "refresh" };
  return null;
};
