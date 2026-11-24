#!/usr/bin/env python3
"""Squad hub for every repository: one process on one loopback port, one path per repository (#20).
usage: hubmux.py [port]   default 7700, the live hub. Any other port is a check hub: it keeps its board cache and flow history out of the live one (#1000). `/<profile>` is that profile's hub page (hub.py, loaded once per profile with the
profile's own context from the seam, address.sh); `/` lists the repositories and their live workers that the request may see
(every one on loopback; on the tailnet, those whose own allow list admits the login); a path no profile owns answers 404 with
that list; `/api/hub` names the hub and its repositories, and `?identity=1` answers that name from the loaded profiles
alone, with no per-profile state read, so hubctl.sh can tell this hub from any other listener inside its bound (#1202). A refused
tailnet request answers 403 with its reason. Read-only, as hub.py is: it starts no run."""
import html
import importlib.util
import io
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubstream   # the push stream's path is passed unbuffered, as /dash/ is
CTX = HERE / "address.sh"
LIVE_PORT = 7700
PORT = LIVE_PORT   # the port this process serves; any port but LIVE_PORT is a check hub (#1000)


def _ctx(arg):
    """The repository context the seam resolves for one input, as a dict; empty when it refuses."""
    p = subprocess.run(["bash", str(CTX), arg], capture_output=True, text=True, timeout=30)
    return {k: v for k, _, v in (ln.partition("=") for ln in shlex.split(p.stdout))} if p.returncode == 0 else {}


_SLUGS = {"k": None, "v": None}


def _profiles_key():
    """The profile files and their change times: the list address.sh prints changes only when this does."""
    d = pathlib.Path(os.environ.get("SG_PROFILES_DIR") or HERE / "profiles")
    try:
        return tuple(sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.sh")))
    except OSError:
        return None


def profile_slugs():
    """The profile names address.sh lists now, read again only when a profile file is added, removed or changed (#917):
    the index polls every 10 s, and one list read per request held the single lock long enough that the page's fetches
    and the identity check timed out."""
    key = _profiles_key()
    if key is not None and key == _SLUGS["k"]: return _SLUGS["v"]
    out = subprocess.run(["bash", str(CTX), "--list"], capture_output=True, text=True, timeout=30).stdout
    _SLUGS.update(k=key, v=[ln.split("\t")[0] for ln in out.splitlines() if ln.strip()])
    return _SLUGS["v"]


def load_one(slug):
    """One profile's app: its hub.py instance, imported with its own context, or its error. A broken profile is a named
    entry with its error, never a machine-wide stop."""
    c = _ctx(slug)
    app = {"repo": c.get("SG_REPO", ""), "state": c.get("SG_STATE", "")}
    if not c:
        return {**app, "err": "address.sh refuses this profile"}
    pinned = {"SG_REPO": c.get("SG_REPO", ""), "SG_OWNER": c.get("SG_OWNER", ""), "SG_PROJECT": c.get("SG_PROJECT", ""),
              "SG_CHECKOUT": c.get("SG_CHECKOUT", ""), "SG_STATE": c.get("SG_STATE", ""), "SG_PROFILE": slug, "SG_HUB_DIR": "",
              "SG_PROJECTS": c.get("SG_PROJECTS", ""), "SG_FORK_DIR": c.get("SG_FORK_DIR", ""),   # the profile's own run roots, never the shell's (#859)
              "SG_BOARD_CACHE": c.get("SG_BOARD_CACHE", "") if PORT == LIVE_PORT else "",   # the live board cache is the live hub's (#512, #1000)
              "SG_SHARED": c.get("SG_SHARED", ""),   # the one shared directory the loop reads, never the profile's state (#678)
              "SG_SQUAD": c.get("SG_SQUAD", "")}   # the profile's own squad package root, read at import (#2820); pinned empty when the profile names none, so no profile inherits the mux process's
    before = {k: os.environ.get(k) for k in pinned}
    try:
        os.environ.update(pinned)
        spec = importlib.util.spec_from_file_location(f"hub_{slug}", HERE / "hub.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        app["mod"] = mod
    except (Exception, SystemExit) as e:
        app["err"] = str(e) or type(e).__name__
    finally:
        for k, v in before.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return app


def load_apps():
    """{slug: {"repo", "state", "mod" or "err"}}: one hub.py instance per profile, each imported with its own context."""
    return {slug: load_one(slug) for slug in profile_slugs()}


LOCK = threading.Lock()


def refresh(apps, on_new=None):
    """Bring `apps` to the profile list as it is now: a new profile loads (and goes to `on_new`), a gone one drops, a kept
    one stays as it is. A failed list read keeps the apps as they were, so a read that fails for a moment drops nothing (#917)."""
    try: now = profile_slugs()
    except Exception: return
    for slug in [s for s in apps if s not in now]: apps.pop(slug)
    for slug in now:
        if slug not in apps:
            apps[slug] = load_one(slug)
            if on_new: on_new(apps[slug])


RUNS_TTL = 10.0   # seconds: the index poll's own interval, never a guess (#1848)
CLOCK = time.monotonic   # the age reader; a test injects its own, so freshness is proved by a clock and never by a sleep
_RUNS = {}   # {profile: (read_at, hub module, rows)}: run rows only, one entry per loaded profile; a listing is never cached


def runs_of(profile, app):
    """One profile's run list, read at most once per index poll window (#1848).

    The index lists every profile on every `/api/hub` and every open page's hub-switcher polls that route every 10 s, while
    one `runs()` costs ten process snapshots: uncached the route took 775 ms per request (issue #1848). The window is the
    poll's own interval (INDEX_JS: `setInterval(poll,10000)`), so a page reads each profile's list once per tick and two
    pages polling inside one window share that read. An entry at or past its window is read again, never served as fresh.

    The cache holds run rows only, keyed by profile and bound to the hub module that read them, so a reloaded profile
    reads afresh; it never holds a listing. `admitted()` still runs on every request, because a tailnet request may see
    only the profiles its login admits (ADR-0152), and `?identity=1` still reads no state (#1202)."""
    mod = app.get("mod")
    if mod is None: return []
    hit = _RUNS.get(profile)
    now = CLOCK()
    if hit is not None and hit[1] is mod and now - hit[0] < RUNS_TTL:
        return hit[2]
    rows = mod.runs()   # a failed read raises: the caller answers no live work and this stores nothing
    _RUNS[profile] = (now, mod, rows)
    return rows


def live(app, slug=None):
    """The live workers of one repository: the runs the hub itself sees working (hub.py), not the live-run list, which keeps finished runs on purpose.
    The run list comes from the poll-window cache, keyed by the profile's slug (its repository name when no caller names it)."""
    mod = app.get("mod")
    if mod is None: return []
    try: return mod.working(runs_of(slug if slug is not None else app.get("repo", ""), app))
    except Exception: return []


def alert(app):
    """The worst alert of one repository as text, or "" when none or when its hub cannot say (#876)."""
    mod = app.get("mod")
    if mod is None: return ""
    try: return mod.worst_alert()
    except Exception: return ""


def listing(apps):
    return {"hub": "squad-hub", "repos": [{"slug": s, "repo": a["repo"], "path": f"/{s}", "live": live(a, s), "alert": alert(a), **({"err": a["err"]} if "err" in a else {})} for s, a in apps.items()]}


def identity(apps):
    """The answer hubctl.sh checks before it stops or starts anything: the hub's name and the profiles it loaded. It reads
    no profile's state, so a loaded machine cannot push the answer past the command's bound and turn a real hub into a
    foreign listener (#1202)."""
    return {"hub": "squad-hub", "repos": [{"slug": s, "repo": a["repo"], "path": f"/{s}"} for s, a in apps.items()]}


WEB_TOKENS = (pathlib.Path(__file__).resolve().parent / "web-tokens.css").read_text()
# The index wears the same tokens as every profile page: one card per repository, a status dot, a live pill and the
# alert line, so a reader who opens the hub root reads the same page as one who opens a profile (owner, 2026-11-01).
CSS = ("*{box-sizing:border-box}"
       "body{background:var(--bg);color:var(--fg);font:var(--fs-body)/1.5 var(--font-ui);margin:0;padding:26px 20px 40px}"
       "main{max-width:none}"
       "header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 2px;padding-bottom:12px;"
       "border-bottom:1px solid var(--line)}"
       "h1{font-size:var(--fs-display);font-weight:600;margin:0;letter-spacing:-.01em}"
       ".sub{color:var(--mute);font-size:var(--fs-caption)}"
       ".where{color:var(--warn);font:var(--fs-caption) var(--font-mono);margin:12px 0 0}"
       ".note{color:var(--mute);font-size:var(--fs-caption);margin:6px 0 0}"
       "ul{list-style:none;padding:0;margin:14px 0 0;display:grid;gap:8px}"
       "li{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);padding:11px 14px;"
       "display:flex;align-items:center;gap:10px;flex-wrap:wrap;transition:border-color var(--motion-duration-short) var(--motion-entry-easing)}"
       "li:hover{border-color:var(--line-strong)}li:focus-within{border-color:var(--acc)}"
       ".dot{width:8px;height:8px;border-radius:var(--radius-full);background:var(--mute);flex:none}"
       ".dot.on{background:var(--ok);box-shadow:0 0 0 3px rgb(62 207 142 / .16)}"
       ".dot.bad{background:var(--err);box-shadow:0 0 0 3px rgb(255 107 107 / .16)}"
       "a{color:var(--acc);text-decoration:none;font-weight:600}a:hover{text-decoration:underline}"
       ".repo{color:var(--mute);font:var(--fs-caption) var(--font-mono)}"
       ".badge{margin-left:auto;font-size:var(--fs-caption);color:var(--mute);border:1px solid var(--line);border-radius:var(--radius-xl);"
       "padding:1px 9px;white-space:nowrap}.badge.on{color:var(--ok);border-color:rgb(62 207 142 / .38)}"
       ".badge.bad{color:var(--err);border-color:rgb(255 107 107 / .38)}"
       ".runs,.alert{flex-basis:100%;font-size:var(--fs-caption);overflow-wrap:anywhere}"
       ".runs{color:var(--mute);font-family:var(--font-mono)}.alert{color:var(--warn)}"
       ".empty{color:var(--mute);background:var(--panel);border:1px dashed var(--line);border-radius:var(--radius-lg);padding:18px;text-align:center}"
       "@media (max-width:600px){body{padding:18px 14px 32px}.badge{margin-left:0}}")


def _row(r):
    """One repository as a card: its state dot, its name and owner, its live pill, then its alert and its run ids."""
    slug, repo = html.escape(r["slug"]), html.escape(r["repo"])
    if "err" in r:
        dot, badge = " bad", f'<span class="badge bad">profile broken</span>'
        alert = f'<div class="alert">{html.escape(r["err"])}</div>'
    else:
        n = len(r["live"])
        dot = " on" if r["live"] else ""
        badge = f'<span class="badge on">{n} live</span>' if n else '<span class="badge">idle</span>'
        alert = f'<div class="alert">{html.escape(r["alert"])}</div>' if r.get("alert") else ""
    ids = " ".join(sorted(r["live"]))
    runs = f'<div class="runs">{html.escape(ids)}</div>' if ids else ""
    return f'<li><i class="dot{dot}"></i><a href="/{slug}">{slug}</a><span class="repo">{repo}</span>{badge}{alert}{runs}</li>'


def root_page(apps, title, note=None):
    """The index: ONE card per repository -- its link, its owner/name, its live count and its worst alert, with the live
    run ids in a muted block of their own. A repository holding twenty runs must not bury the list under its own ids,
    which is what the old single-line row did when an operator opened `/test` and got every repository's runs in one
    paragraph."""
    repos = listing(apps)["repos"]
    nlive = sum(1 for r in repos if r["live"])
    rows = "".join(_row(r) for r in repos) or '<li class="empty">no profile known: no repository answers on this hub</li>'
    return (f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
            f'<title>squad hub</title><style>{WEB_TOKENS}{CSS}</style><body><main>'
            f'<header><h1>squad hub</h1><span id=hcount class=sub>{len(repos)} '
            f'{"repository" if len(repos) == 1 else "repositories"} · {nlive} live</span></header>'
            f'{"" if not title or title == "squad hub" else "<p class=where>" + html.escape(title) + "</p>"}{note or ""}'
            f'<ul id="repos">{rows}</ul></main>'
            f'<script>{INDEX_JS}</script>').encode()


# The index repaints its list from /api/hub every 10 s, so a profile added or gone and the live counts show with no reload
# (#917). The rows take the same markup as root_page's first paint.
INDEX_JS = """
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function row(r){
  const dot=r.err?" bad":(r.live.length?" on":""), badge=r.err?'<span class="badge bad">profile broken</span>'
    :(r.live.length?`<span class="badge on">${r.live.length} live</span>`:'<span class="badge">idle</span>');
  const alert=r.err?`<div class="alert">${esc(r.err)}</div>`:(r.alert?`<div class="alert">${esc(r.alert)}</div>`:"");
  const ids=[...r.live].sort().join(" ");
  return `<li><i class="dot${dot}"></i><a href="/${esc(r.slug)}">${esc(r.slug)}</a><span class="repo">${esc(r.repo)}</span>${badge}${alert}${ids?`<div class="runs">${esc(ids)}</div>`:""}</li>`;
}
function paint(h){
  const nlive=h.repos.filter(r=>r.live.length).length;
  document.getElementById("repos").innerHTML=h.repos.map(row).join("")||'<li class="empty">no profile known: no repository answers on this hub</li>';
  const s=document.getElementById("hcount");
  if(s)s.textContent=`${h.repos.length} ${h.repos.length===1?"repository":"repositories"} · ${nlive} live`;
}
async function poll(){try{const r=await fetch("/api/hub",{cache:"no-store"});if(r.ok)paint(await r.json());}catch(e){}}
setInterval(poll,10000);
"""


def test_note(slug, apps):
    """What a path the hub does not own is told: a test log belongs to a repository, so name where it lives instead of
    leaving the operator at a list. `/test/<id>` read as a repository named `test` is the case that sent them here."""
    if slug != "test" or not apps:
        return None
    first = sorted(apps)[0]
    return (f'<p class="note">A test log belongs to a repository: open <code>/{html.escape(first)}/test/&lt;id&gt;</code>. '
            "The repositories and their live runs are:</p>")


REWRITTEN = (b"text/html", b"application/json", b"application/manifest+json", b"text/javascript")
VERBATIM = ()   # a route whose body the routing rewrite never touches


def rewrite(body, slug):
    """A repository's page calls its own API: every absolute /api/, /test/, /dash/, /held/, /ended/ and /mobile/ reference gains the /<slug> prefix, in the page,
    the JSON it fetches, the web app manifest and the service worker (a test card's link is `/test/<id>` there; without the slug the path read `test` as a repository)."""
    for q in (b'"', b"'", b"`"):
        for p in (b"/api/", b"/test/", b"/dash/", b"/held/", b"/ended/", b"/mobile/"):
            body = body.replace(q + p, q + b"/" + slug.encode() + p)
    return body


def admitted(apps, h, method):
    """(the profiles this request may see, the refusal reason). Loopback sees every profile; a tailnet request sees the
    profiles whose own access rule (hub.py access(), the seam) admits its login, and a refusal names none of them."""
    port = h.server.server_port
    if (h.headers.get("Host") or "") in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return apps, ""
    seen, reasons = {}, []
    for slug, app in apps.items():
        if "mod" not in app: continue
        v = app["mod"].access(h, app["mod"].route_action(method, h.path))
        if v.allowed: seen[slug] = app
        else: reasons.append(v.reason)
    if seen: return seen, ""
    if not reasons: return {}, "no profile loaded on this hub"   # no app to ask: the host was never the reason (#1552)
    return {}, next((r for r in reasons if r != "forbidden host"), "forbidden host")


def server(port, apps, on_new=None):
    class Mux(BaseHTTPRequestHandler):
        profile = None   # the hub.py handler class of the repository this request addresses; set per request

        def log_message(self, *a): pass

        def _send(self, code, body, ct):
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def refuse(self, reason, page=False):
            if page: return self.profile.refuse(self, reason, page)
            self._send(403, reason.encode(), "text/plain; charset=utf-8")
        def not_found(self): return self.profile.not_found(self)
        def dash(self, method, body=b""): return self.profile.dash(self, method, body)
        def stream(self, a): return self.profile.stream(self, a)
        def answer(self, code, out): return self.profile.answer(self, code, out)
        def loop_post(self, a): return self.profile.loop_post(self, a)   # the loop switch's write (#923)
        def profiler_post(self, a): return self.profile.profiler_post(self, a)   # the Loop tab's switch write (#1743)

        def _route(self, method):
            path, _, query = self.path.partition("?")
            if path == "/api/hub" and query == "identity=1":
                # The identity check reads the apps loaded at startup and never their state, so a loaded machine cannot
                # time it out (#1202). The access rule still applies: a tailnet request sees only what it may see.
                seen, reason = admitted(dict(apps), self, method)
                if reason: return self.refuse(reason)
                return self._send(200, json.dumps(identity(seen)).encode(), "application/json")
            if path in ("/", "/api/hub"):
                with LOCK:   # the index reads the profile list now, so a profile added or gone shows at once (#917)
                    refresh(apps, on_new)
                    view = dict(apps)
                seen, reason = admitted(view, self, method)
                if reason: return self.refuse(reason)
                touch_listed(seen)   # the index lists every repository it may see (#1790)
                if path == "/": return self._send(200, root_page(seen, "squad hub"), "text/html; charset=utf-8")
                return self._send(200, json.dumps(listing(seen)).encode(), "application/json")
            slug = path.split("/")[1]
            app = apps.get(slug)   # a deep link reads the apps already loaded; only the index rereads the list (#917)
            view = apps
            if not app or "mod" not in app:
                seen, reason = admitted(view, self, method)
                if reason: return self.refuse(reason)
                touch_listed(seen)   # the 404 page lists every repository it may see (#1790)
                return self._send(404, root_page(seen, f"no repository at /{slug}", test_note(slug, seen)), "text/html; charset=utf-8")
            touch_profile(app)   # a page reads this profile: its background reads run until it stops (#1790)
            self.profile = app["mod"].H
            self.path = self.path[len(slug) + 1:] or "/"
            if self.path.startswith("?"): self.path = "/" + self.path
            streams = self.path.split("?", 1)[0] == hubstream.PATH and hubstream.enabled()
            if self.path.startswith("/dash/") or streams:   # a run's dashboard and the hub's push stream arrive as they come: never buffered, never rewritten
                return getattr(self.profile, "do_" + method)(self)
            real, buf = self.wfile, io.BytesIO()
            self.wfile = buf
            try: getattr(self.profile, "do_" + method)(self)
            finally: self.wfile = real
            head, sep, body = buf.getvalue().partition(b"\r\n\r\n")
            head = re.sub(rb"(?im)^(Location: )/(?!/)", rb"\1/" + slug.encode() + b"/", head)   # a redirect to this repository's own path keeps its slug
            verbatim = self.path.split("?", 1)[0].startswith(VERBATIM)
            real.write(head + sep + (rewrite(body, slug) if not verbatim and any(t in head for t in REWRITTEN) else body))

        def do_GET(self): self._route("GET")
        def do_POST(self): self._route("POST")

    return ThreadingHTTPServer(("127.0.0.1", port), Mux)


SAMPLERS =("refresh_prs", "refresh_quota", "refresh_board", "refresh_history", "load_sampler")


def start_profile(a):
    """Each profile's own background reads, one thread each, a profile added later starts its own too (#917). They are
    parked at boot: a read works only while a page reads that profile (#1790), so a hub with no page open costs its
    listeners and nothing more. The sampler of a profiler switch left on starts from the same read, never at boot
    (#1743)."""
    m = a.get("mod")
    if m is None: return
    a["gate"] = m.ReadGate()
    m.READ_GATE = a["gate"]
    for f in SAMPLERS:
        threading.Thread(target=getattr(m, f), daemon=True, name=f"{a.get('repo') or 'profile'}:{f}").start()


def touch_profile(a):
    """A page read of this profile: its background reads run until READ_IDLE_SECS passes with no read (#1790). The Loop
    tab's own read starts the profiler sampler (hub.py `profiler_view`), so every request here reads no switch file."""
    g = a.get("gate")
    if g is not None: g.touch()


def touch_listed(apps):
    """The index reads each repository's live work and its worst alert, so it reads each of them: its poll keeps their
    readings running (#1790)."""
    for a in apps.values(): touch_profile(a)


if __name__ == "__main__":
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else LIVE_PORT
    apps = load_apps()
    for k in ("SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_CHECKOUT", "SG_STATE", "SG_PROJECTS", "SG_FORK_DIR"):   # each page reads its own profile, never the shell's
        os.environ.pop(k, None)
    def added(a):
        start_profile(a)
    for a in apps.values():
        start_profile(a)
    server(PORT, apps, on_new=added).serve_forever()
