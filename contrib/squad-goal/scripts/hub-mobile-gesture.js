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
