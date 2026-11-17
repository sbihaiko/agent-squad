// Gesture rules of the mobile Board tab. Pure: no DOM and no network. The hub inlines this text into hub-mobile.html.
const GESTURE_OWNERS = ["sheet", "card", "header", "gap"];
const SWIPE_MIN = 40;
// The thumb zone is the bottom 40% of the viewport: the one write starts there, where the thumb reaches (#1142).
const THUMB_TOP = 0.6;
const inThumbZone = (y, h) => h > 0 && y >= h * THUMB_TOP;
const gestureOwner = roles => roles.find(r => GESTURE_OWNERS.includes(r)) || "none";
// A swipe that starts in a nested scroller or a control of the sheet (`nested`) is that element's, never a card step (#1007).
// A rightward drag on a promotable card is the move's (hub-mobile-move.js); every other horizontal swipe on a card is a column step.
// A sheet closes only on a downward swipe that starts on its grab handle (`handle`), never from the body (#1454).
const gestureAct = ({ owner, dx, dy, atTop, nested, handle, promotable }) => {
  const ax = Math.abs(dx), ay = Math.abs(dy);
  if (Math.max(ax, ay) < SWIPE_MIN) return null;
  if (ax > ay) {
    if (owner === "card" && !(promotable && dx > 0)) return { act: "column", step: dx < 0 ? 1 : -1 };
    if (owner === "header" || owner === "gap") return { act: "column", step: dx < 0 ? 1 : -1 };
    if (owner === "sheet" && dx < 0 && !nested) return { act: "card", step: 1 };
    return null;
  }
  return owner === "sheet" && dy > 0 && atTop && handle ? { act: "close" } : null;
};
const moveIndex = (i, count, step) => count ? Math.min(count - 1, Math.max(0, i + step)) : 0;
const afterGesture = (v, g) => {
  if (g.act === "close") return { ...v, sheet: false };
  if (g.act === "column") {
    const col = moveIndex(v.col, v.cols, g.step);
    return col === v.col ? v : { ...v, col, card: 0 };
  }
  if (g.act === "card") {
    const card = moveIndex(v.card, v.cards, g.step);
    return card === v.card ? v : { ...v, card };
  }
  return v;
};
const afterOpen = (v, card) => ({ ...v, card, sheet: true });
// The sheet steps through the cards the opened card's run holds in this column (#997); a card with no run steps the column.
const sheetCards = (cards, x) => x && x.hr ? cards.filter(r => r.hr === x.hr) : cards;
const sheetStep = (v, cards, g) => {
  if (g.act !== "card") return afterGesture(v, g);
  const set = sheetCards(cards, cards[v.card]);
  const at = afterGesture({ ...v, card: set.indexOf(cards[v.card]), cards: set.length }, g).card;
  return { ...v, card: cards.indexOf(set[at]) };
};
// A board read keeps the open sheet on its issue (#1005): the view holds indexes, so the read finds the card again by its
// repository and number, in its column by name, and follows it if it moved. `cols` is the drawn columns, each with its
// name and its flat card list; `id` is {name, key} taken before the read. A card the board dropped closes the sheet.
const anchorView = (v, cols, id) => {
  if (!v.sheet || !id) return v;
  const at = (c) => c.flat.findIndex(r => (r.r || "") + "#" + r.n === id.key);
  const home = cols.findIndex(c => c.name === id.name);
  if (home >= 0 && at(cols[home]) >= 0) return { ...v, col: home, card: at(cols[home]) };
  const far = cols.findIndex(c => at(c) >= 0);
  return far >= 0 ? { ...v, col: far, card: at(cols[far]) } : { ...v, sheet: false };
};
const PULL_MIN = 60;
const runsGesture = ({ dx, dy, atTop }) => {
  const ax = Math.abs(dx), ay = Math.abs(dy);
  if (atTop && dy >= PULL_MIN && ay > ax) return { act: "refresh" };
  return null;
};
// The carousel's column rule (#877): with the filter on, a column that holds no visible card is left out of the
// carousel and named once, with its zero count. `counts` holds each group's visible card count, in group order.
// A closed status (`end`, set by the board's role) is never a carousel column: it is named once as a total (#969).
const carouselColumns = (groups, counts, hide) => {
  const shown = [], hidden = [], closed = [];
  groups.forEach((g, i) => {
    if (g.end) closed.push({ name: g.name, count: g.n });
    else if (hide && counts[i] === 0) hidden.push({ name: g.name, count: counts[i] });
    else shown.push(g);
  });
  return { shown, hidden, closed };
};
// The empty-column note (#915): a column with no card names the filters only when a filter is set.
const emptyColumnNote = ({ groups, shown, filtered }) =>
  !groups ? "Loading the board." : !shown ? "No column holds a card." : filtered ? "No card in this column with these filters." : "No card in this column.";
// The three list states (#1445): one rule answers the line every list draws above its content. `ever` says a reply
// arrived, `n` counts the rows the list holds, and `error` names the last read's failure. A list says 'loading' until
// its first reply; a successful empty reply draws one sentence naming the missing data; a failed read draws one error
// line and never drops what the list holds. An empty sentence stays beside the error line when there was nothing to keep.
const listState = ({ ever, n, error, loading, empty }) => ({
  line: !ever && !error ? loading : ever && !n ? empty : "",
  error: error || "",
});
// The swimlanes of one carousel column (#893): its visible cards grouped by spec, each lane headed by its number and
// progress. A lane holds the visible cards whose `pa` names its spec, and the spec's own card first when it is visible
// here. Progress counts the spec's children on the whole board, never the column alone. "No spec" is the last lane.
// A `wave:NN` label never makes a lane (SPEC 1965, #1988).
// `lc` holds the saved collapse by lane id; a lane whose spec is done starts collapsed. `flat` is the cards the column
// draws, in order, with the collapsed lanes left out: the card indexes of the page read it.
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
// The cards a board read gives that no earlier read gave (owner, 2026-11-01): only these take the entry fade, so the poll,
// a column switch and a filter change never play it again on a card already seen. The first read marks no card new.
const freshCards = (seen, keys) => { if (seen == null) return []; const s = new Set(seen); return keys.filter(k => !s.has(k)); };
