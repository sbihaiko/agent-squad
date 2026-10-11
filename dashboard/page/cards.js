// --- single-card mode (ticket #1782): the page paints ONE card of the panel, named by its URL ---
// The hub's pip, floating over the maximized board, points a frame at this page with `?card=<name>`, so the second
// view shows the panel's own card instead of a copy that drifts. The page still reads the run (the same push stream
// or reread timer it always uses), so the card follows a live run rather than a snapshot; the mode only cuts what is
// painted. A name the panel does not have is answered with a message inside the card, never an empty frame. The page
// tells whoever framed it that it answered the mode, which is how the window tells an older dashboard (one that does
// not know the parameter at all) from one that does.
const CARD = (new URLSearchParams(location.search).get("card") || "").trim();
// The window is a second page paint of the same run: it must not be told twice about anything, so the card is
// rebuilt only when its own text changed, like the panel's own list.
let cardHTML = null;

function renderCard() {
  const box = $("card"), card = core.cardBody(CARD, S.children);
  const groups = card.groups.map((g) => {
    const rows = g.kids.map((k) => `<div class="crow s-${esc(core.childMark(k).state)}" title="${esc(k.name)}">${esc(core.childMark(k).glyph)} <b>${esc(k.title || k.label || k.name)}</b>${k.running_for ? ` <span class="cmeta">${esc(core.fmt.seconds(k.running_for))}</span>` : ""}</div>`).join("");
    return `<section class="cgroup s-${esc(g.state)}"><h3>${esc(g.name)}</h3>${rows}</section>`;
  }).join("");
  const html = card.ok ? groups || `<p class="cnote">${esc(card.empty)}</p>` : `<p class="cnote">${esc(card.message)}</p>`;
  if (html !== cardHTML) { box.innerHTML = html; cardHTML = html; }
  if (window.parent !== window) window.parent.postMessage({ squadCard: CARD }, "*");   // the window's own handshake: it knows this page answered the mode
}

if (CARD) {
  document.body.classList.add("card-mode");
  $("card").setAttribute("aria-label", `One card of the run panel: ${CARD}`);
  renderCard();
  window.addEventListener("message", (e) => { if (e.data && e.data.card === CARD) renderCard(); });   // the window may ask for a repaint
}
