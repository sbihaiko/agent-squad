// Gesture rules of the mobile Board tab. Pure: no DOM and no network. The hub inlines this text into hub-mobile.html.
const GESTURE_OWNERS = ["sheet", "card", "header", "gap"];
const SWIPE_MIN = 40;
const gestureOwner = roles => roles.find(r => GESTURE_OWNERS.includes(r)) || "none";
const gestureAct = ({ owner, dx, dy, atTop }) => {
  const ax = Math.abs(dx), ay = Math.abs(dy);
  if (Math.max(ax, ay) < SWIPE_MIN) return null;
  if (ax > ay) {
    if (owner === "header" || owner === "gap") return { act: "column", step: dx < 0 ? 1 : -1 };
    if (owner === "sheet" && dx < 0) return { act: "card", step: 1 };
    return null;
  }
  return owner === "sheet" && dy > 0 && atTop ? { act: "close" } : null;
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
const EDGE_PX = 24, PULL_MIN = 60;
const runsGesture = ({ x0, dx, dy, atTop }) => {
  const ax = Math.abs(dx), ay = Math.abs(dy);
  if (x0 <= EDGE_PX && dx >= SWIPE_MIN && ax > ay) return { act: "back" };
  if (atTop && dy >= PULL_MIN && ay > ax) return { act: "refresh" };
  return null;
};
// The carousel's column rule (#877): with the filter on, a column that holds no visible card is left out of the
// carousel and named once, with its zero count. `counts` holds each group's visible card count, in group order.
const carouselColumns = (groups, counts, hide) => {
  const shown = [], hidden = [];
  groups.forEach((g, i) => {
    if (hide && counts[i] === 0) hidden.push({ name: g.name, count: counts[i] });
    else shown.push(g);
  });
  return { shown, hidden };
};
// The empty-column note (#915): a column with no card names the filters only when a filter is set.
const emptyColumnNote = ({ groups, shown, filtered }) =>
  !groups ? "Loading the board." : !shown ? "No column holds a card." : filtered ? "No card in this column with these filters." : "No card in this column.";
// The swimlanes of one carousel column (#893): its visible cards grouped by spec, each lane headed by its number and
// progress. A lane holds the visible cards whose `pa` names its spec, and the spec's own card first when it is visible
// here. Progress counts the spec's children on the whole board, never the column alone. "No spec" is the last lane.
// `lc` holds the saved collapse by lane id; a lane whose spec is done starts collapsed. `flat` is the cards the column
// draws, in order, with the collapsed lanes left out: the card indexes of the page read it.
const laneKey = x => x.pa == null ? "none" : String(x.pa);
const columnLanes = ({ groups, cards, lc }) => {
  const titles = new Map(), total = new Map(), done = new Map();
  (groups || []).forEach(g => g.items.forEach(x => {
    if (x.sp) { titles.set(String(x.n), x.t); return; }
    const k = laneKey(x);
    total.set(k, (total.get(k) || 0) + 1);
    if (x.dn) done.set(k, (done.get(k) || 0) + 1);   // `dn`: the row is done, a fact the hub serves (#893)
  }));
  const by = new Map();
  const ensure = k => { if (!by.has(k)) by.set(k, { id: k, cards: [], spec: null }); return by.get(k); };
  cards.forEach(x => {
    const l = ensure(x.sp ? String(x.n) : laneKey(x));
    if (x.sp) { l.spec = x; l.cards.unshift(x); } else l.cards.push(x);
  });
  const lanes = [...by.values()].map(l => {
    const t = total.get(l.id) || 0, d = done.get(l.id) || 0;
    const isDone = t > 0 && d === t;
    const collapsed = Object.prototype.hasOwnProperty.call(lc || {}, l.id) ? !!lc[l.id] : isDone;
    return { id: l.id, num: l.id === "none" ? null : +l.id, title: l.id === "none" ? "No spec" : (titles.get(l.id) || ""),
             total: t, closed: d, collapsed, cards: l.cards };
  }).sort((a, b) => a.id === "none" ? 1 : b.id === "none" ? -1 : a.num - b.num);
  const flat = lanes.flatMap(l => l.collapsed ? [] : l.cards);
  return { lanes, flat };
};
