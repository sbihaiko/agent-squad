#!/usr/bin/env python3
"""Squad hub for every repository: one process on one loopback port, one path per repository (#20).
usage: hubmux.py [port]   default 7700. `/<profile>` is that profile's hub page (hub.py, loaded once per profile with the
profile's own context from the seam, address.sh); `/` lists the known repositories and their live workers; a path no profile owns
answers 404 with that list; `/api/hub` names the hub and its repositories, so hubctl.sh can tell this hub from any other
listener. Read-only, as hub.py is: it starts no run."""
import html, importlib.util, io, json, os, pathlib, shlex, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
CTX = HERE / "address.sh"


def _ctx(arg):
    """The repository context the seam resolves for one input, as a dict; empty when it refuses."""
    p = subprocess.run(["bash", str(CTX), arg], capture_output=True, text=True, timeout=30)
    return {k: v for k, _, v in (ln.partition("=") for ln in shlex.split(p.stdout))} if p.returncode == 0 else {}


def load_apps():
    """{slug: {"repo", "state", "mod" or "err"}}: one hub.py instance per profile, each imported with its own context. A
    broken profile is a named entry with its error, never a machine-wide stop."""
    out = subprocess.run(["bash", str(CTX), "--list"], capture_output=True, text=True, timeout=30).stdout
    apps = {}
    for slug in [ln.split("\t")[0] for ln in out.splitlines() if ln.strip()]:
        c = _ctx(slug)
        app = {"repo": c.get("SG_REPO", ""), "state": c.get("SG_STATE", "")}
        if not c:
            apps[slug] = {**app, "err": "address.sh refuses this profile"}
            continue
        pinned = {"SG_REPO": c.get("SG_REPO", ""), "SG_OWNER": c.get("SG_OWNER", ""), "SG_PROJECT": c.get("SG_PROJECT", ""),
                  "SG_CHECKOUT": c.get("SG_CHECKOUT", ""), "SG_STATE": c.get("SG_STATE", ""), "SG_PROFILE": slug, "SG_HUB_DIR": "",
                  "SG_BOARD_CACHE": c.get("SG_BOARD_CACHE", ""),   # the profile's own board cache, never the shell's (#512)
                  "SG_SHARED": c.get("SG_SHARED", "")}   # the one shared directory the loop reads, never the profile's state (#678)
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
        apps[slug] = app
    return apps


def live(app):
    """The live workers of one repository: the runs the hub itself sees working (hub.py), not the live-run list, which keeps finished runs on purpose."""
    mod = app.get("mod")
    if mod is None: return []
    try: return mod.working(mod.runs())
    except Exception: return []


def listing(apps):
    return {"hub": "squad-hub", "repos": [{"slug": s, "repo": a["repo"], "path": f"/{s}", "live": live(a), **({"err": a["err"]} if "err" in a else {})} for s, a in apps.items()]}


CSS = ("body{background:#0d1117;color:#c9d1d9;font:14px system-ui;margin:16px}"
       "h1{font-size:18px;margin:0 0 4px}ul{list-style:none;padding:0;margin:12px 0 0;max-width:54em}"
       "li{padding:7px 0;border-bottom:1px solid #21262d}a{color:#5aa9ff;text-decoration:none}"
       "a:hover{text-decoration:underline}.repo{color:#8b9bb3}.live{color:#3ecf8e}.none{color:#8b9bb3}"
       ".broken{color:#ff6b6b}.note{color:#8b9bb3;margin:0}"
       ".runs{color:#8b9bb3;font:11px ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere;margin-top:3px}")


def root_page(apps, title, note=None):
    """The index: ONE line per repository -- its link, its owner/name and its live count, with the live run ids in a
    muted block of their own. A repository holding twenty runs must not bury the list under its own ids, which is what
    the old single-line row did when an operator opened `/test` and got every repository's runs in one paragraph."""
    rows = []
    for r in listing(apps)["repos"]:
        slug, repo = html.escape(r["slug"]), html.escape(r["repo"])
        if "err" in r:
            badge = f'<span class="broken">broken profile: {html.escape(r["err"])}</span>'
        else:
            badge = f'<span class="live">{len(r["live"])} live</span>' if r["live"] else '<span class="none">none live</span>'
        ids = " ".join(sorted(r["live"]))
        runs = f'<div class="runs">{html.escape(ids)}</div>' if ids else ""
        rows.append(f'<li><a href="/{slug}"><b>{slug}</b></a> <span class="repo">{repo}</span> {badge}{runs}</li>')
    return (f'<!doctype html><meta charset=utf-8><title>squad hub</title><style>{CSS}</style><body>'
            f'<h1>{html.escape(title)}</h1>{note or ""}<ul>{"".join(rows) or "<li>no profile known</li>"}</ul>').encode()


def test_note(slug, apps):
    """What a path the hub does not own is told: a test log belongs to a repository, so name where it lives instead of
    leaving the operator at a list. `/test/<id>` read as a repository named `test` is the case that sent them here."""
    if slug != "test" or not apps:
        return None
    first = sorted(apps)[0]
    return (f'<p class="note">A test log belongs to a repository: open <code>/{html.escape(first)}/test/&lt;id&gt;</code>. '
            "The repositories and their live runs are:</p>")


def rewrite(body, slug):
    """A repository's page calls its own API: every absolute /api/, /test/ and /dash/ reference gains the /<slug> prefix, in the page
    and in the JSON it fetches (a test card's link is `/test/<id>` there; without the slug the path read `test` as a repository)."""
    for q in (b'"', b"'", b"`"):
        for p in (b"/api/", b"/test/", b"/dash/"):
            body = body.replace(q + p, q + b"/" + slug.encode() + p)
    return body


def server(port, apps):
    class Mux(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body, ct):
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def _route(self, method):
            path = self.path.split("?", 1)[0]
            if path == "/": return self._send(200, root_page(apps, "squad hub"), "text/html; charset=utf-8")
            if path == "/api/hub": return self._send(200, json.dumps(listing(apps)).encode(), "application/json")
            slug = path.split("/")[1]
            app = apps.get(slug)
            if not app or "mod" not in app:
                return self._send(404, root_page(apps, f"no repository at /{slug}", test_note(slug, apps)), "text/html; charset=utf-8")
            self.path = self.path[len(slug) + 1:] or "/"
            if self.path.startswith("?"): self.path = "/" + self.path
            if self.path.startswith("/dash/"):   # a run's dashboard streams as it arrives: never buffered, never rewritten
                return getattr(app["mod"].H, "do_" + method)(self)
            real, buf = self.wfile, io.BytesIO()
            self.wfile = buf
            try: getattr(app["mod"].H, "do_" + method)(self)
            finally: self.wfile = real
            head, sep, body = buf.getvalue().partition(b"\r\n\r\n")
            real.write(head + sep + (rewrite(body, slug) if b"text/html" in head or b"application/json" in head else body))

        def do_GET(self): self._route("GET")
        def do_POST(self): self._route("POST")

    return ThreadingHTTPServer(("127.0.0.1", port), Mux)


if __name__ == "__main__":
    apps = load_apps()
    for k in ("SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_CHECKOUT", "SG_STATE"):   # each page reads its own profile, never the shell's
        os.environ.pop(k, None)
    for a in apps.values():
        m = a.get("mod")
        if m is None: continue
        for f in ("refresh_prs", "refresh_quota", "refresh_board", "load_sampler"):
            threading.Thread(target=getattr(m, f), daemon=True).start()
    server(int(sys.argv[1]) if len(sys.argv) > 1 else 7700, apps).serve_forever()
