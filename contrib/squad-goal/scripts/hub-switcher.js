// The project switcher (#876): the projects /api/hub names, painted into one list and repainted every 10 s. Each row
// carries its live run count and its worst alert as text, never by colour alone. The path is joined by concatenation,
// so the mux's path rewrite never prefixes it with a project.
const swEsc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
function switcherMeta(r) {
  const live = r.live.length ? `${r.live.length} live` : "none live";
  const alert = r.alert ? `alert: ${r.alert}` : "no alert";
  return `${live} · ${alert}`;
}
function switcherPaint(box, d, me, suffix) {
  const rows = d.repos.map(r => {
    const now = r.slug === me ? ' aria-current="page"' : "";
    return `<a class="psw-r" href="/${swEsc(r.slug)}${suffix}"${now}><b>${swEsc(r.slug)}</b> <span class="repo">${swEsc(r.repo)}</span> <span class="psw-m">${swEsc(switcherMeta(r))}</span></a>`;
  });
  box.innerHTML = rows.join("") + '<a class="psw-r psw-all" href="/">All projects</a>';
}
function switcherPoll(paint) {
  const run = async () => { try { const r = await fetch("/" + "api/hub", { cache: "no-store" }); if (r.ok) paint(await r.json()); } catch (e) {} };
  run();
  setInterval(run, 10000);
}
