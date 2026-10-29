// Move rules of the mobile Board tab (#829). Pure: no DOM and no network. The hub inlines this text into hub-mobile.html.
// Whether a card may move and the column it goes to are the row's own facts (`x.pm`, `x.pmt`), never a column named here.
const MOVE_MIN = 56;     // px the finger travels right, past the marked threshold, before a release stages the move
const UNDO_MS = 5000;    // ms the Undo stays on screen before the hub writes
const moveArmed = dx => dx >= MOVE_MIN;
const moveDrag = ({ promotable, dx, dy }) => {
  if (!promotable) return null;                       // a card the hub did not mark promotable never stages a move
  if (Math.abs(dy) > Math.abs(dx)) return null;       // a vertical move is the list scrolling, not a move
  return dx >= MOVE_MIN ? { act: "stage" } : null;    // a release before the threshold does nothing
};
// A staged move: the number, the column the row names, and the instant the hub may write. The card never leaves its
// column here: only the hub's read moves it (ADR-0143), and no rule moves it back.
const moveStage = (n, to, at) => ({ n, to, until: at + UNDO_MS, live: true, why: "" });
// The one transition of a staged move. The Undo, the app going hidden and a profile change all cancel it, and a
// cancelled move is never written, however late the write is asked for. The write happens only at or after its delay.
const moveAct = (p, act, at) => {
  if (!p || !p.live) return p;
  if (act === "undo" || act === "hidden" || act === "profile") return { ...p, live: false, why: act };
  if (act === "write") return at >= p.until ? { ...p, live: false, why: "written" } : p;
  return p;
};
