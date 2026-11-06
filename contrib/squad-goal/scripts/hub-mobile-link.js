// The phone's card link (#1459): `#issue=N` names one card, and the board answers with one named result.
// Pure: no DOM, no network, no imports. The hub inlines this text into hub-mobile.html.
const linkHash = h => { const m = /^#issue=(\d+)$/.exec(String(h == null ? "" : h)); return m ? +m[1] : null; };
// The result says what the board does with the named card: the card is drawn and opens, its lane was folded and opens
// first, a filter hides it, no lane holds it (a defect, never the filter's doing), or the board does not hold it. Every
// result but "open" says its line on the board, so a link never lands on a blank board; a card the board draws needs none.
const linkResult = (n, { onBoard, shown, placed, collapsed }) => {
  if (!onBoard) return { why: "missing", n, say: "#" + n + " is not on the board: its issue is read on its own." };
  if (!shown) return { why: "filtered", n, say: "#" + n + " is hidden by the board filters: its issue is read on its own." };
  if (!placed) return { why: "unplaced", n, say: "#" + n + " is on the board but no lane holds it: its issue is read on its own." };
  if (collapsed) return { why: "collapsed", n, say: "#" + n + " was in a folded lane: the lane opened." };
  return { why: "open", n, say: "" };
};
