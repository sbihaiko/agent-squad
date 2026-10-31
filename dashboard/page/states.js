// --- loading, empty and error states (SPEC 889) ---
// One painter for every list and panel that reads. A read in flight paints "Loading …"; a list that legitimately holds
// nothing paints its own empty note; a read that failed paints what failed and the message, with a retry where the page
// has one. The state is the class name (state-loading, state-empty, state-error), so the markup itself says the state.
function stateHtml(state, text, retry) {
  const live = state === "loading" ? ' role="status" aria-live="polite" aria-busy="true"' : state === "error" ? ' role="alert"' : "";
  const btn = retry ? ` <button type="button" class="state-retry" data-state-retry="${esc(retry)}">Try again</button>` : "";
  return `<div class="state state-${state}"${live}>${esc(text)}${btn}</div>`;
}
const emptyHtml = (text) => stateHtml("empty", text);
const loadingText = (what) => `Loading ${what} …`;
function paintState(el, state, text, retry) {
  const html = stateHtml(state, text, retry);
  if (el.innerHTML !== html) el.innerHTML = html;
  if (state === "loading") el.setAttribute("aria-busy", "true"); else el.removeAttribute("aria-busy");
}

// What each read of the page is doing: a state while it runs, null once it settled, and the file plus the message when it
// failed. A panel that depends on a read paints that state; a read one panel does not use leaves the rest of the page alone.
const READ = {};
const readStart = (key, what) => { READ[key] = { state: "loading", what }; };
const readSettle = (key) => { delete READ[key]; };
const readFail = (key, what, message) => { READ[key] = { state: "error", what, message }; };
const readText = (r) => (r.state === "loading" ? loadingText(r.what) : `${r.what}: ${r.message}`);
function readHtml(key, retry) { return READ[key] ? stateHtml(READ[key].state, readText(READ[key]), READ[key].state === "error" ? retry : null) : ""; }
// The read a panel should name: a failure first, then a read still in flight; null when every read settled.
const readKey = (...keys) => keys.find((k) => READ[k] && READ[k].state === "error") || keys.find((k) => READ[k]) || null;
// Paint the read of `key` into a container and say whether it painted anything.
function paintRead(el, key, retry) {
  if (!READ[key]) return false;
  const r = READ[key];
  paintState(el, r.state, readText(r), r.state === "error" ? retry : null);
  return true;
}

// A retry starts the read again. The page registers one for each read that a person can start from the page; a state
// with no retry shows the message alone, and the folder's own Connect button stays the way back in.
const RETRY = {};
document.addEventListener("click", (e) => {
  const b = e.target && e.target.closest ? e.target.closest("[data-state-retry]") : null;
  if (b && RETRY[b.dataset.stateRetry]) RETRY[b.dataset.stateRetry]();
});
