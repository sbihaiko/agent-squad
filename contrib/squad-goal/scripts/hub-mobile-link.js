// The phone's card link (#1459): `#issue=N` names one card, and the board answers by opening it. Pure: no DOM and no
// network. The hub inlines this text into hub-mobile.html (SPEC 2498, #2501).
const linkHash = h => { const m = /^#issue=(\d+)$/.exec(String(h == null ? "" : h)); return m ? +m[1] : null; };
