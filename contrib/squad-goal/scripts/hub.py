#!/usr/bin/env python3
"""Squad hub: one browser page that lists every live run dashboard (agent-squad dashboard_server processes) and embeds them.
usage: hub.py [port]   default 7700, loopback only. Read-only: it discovers servers via ps/lsof and reads record.jsonl."""
import base64, copy, datetime as dt, difflib, glob, hashlib, html, importlib.util, itertools, json, math, os, re, select, shlex, statistics as st, subprocess, sys, tempfile, threading, time, types, pathlib, urllib.parse, urllib.request
import yaml   # the Slack files are YAML: the hub shows their diff and writes them whole (#2694)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
import agyquota, board_priority, chainhealth, ghread, ghthrottle, hubstream, hub_profiler, issue_ledger, modelreg, models_chains, priority_backfill, procscan, provkey, steprec   # the chains and the one registry resolve a model (ADR-0147); ghread is the one GitHub door (ADR-0277); the ledger is the one source of a hold (#1173); procscan keeps one process scan for every profile of a tick (#1746); steprec is the one reader of the dated step records (#1750)
from issue_state import board_state, KINDS, TEST_CASE_TITLE, _utc   # noqa: F401  the board tests read KINDS through the hub
import board_wait, issue_state   # noqa: F401  the board tests read the module and its kinds through the hub
import http.client
import zoneinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

_columns_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_columns_module = importlib.util.module_from_spec(_columns_spec); _columns_spec.loader.exec_module(_columns_module)
_flow_spec = importlib.util.spec_from_file_location("board_flow_history", pathlib.Path(__file__).resolve().parent / "board_flow_history.py")
_flow = importlib.util.module_from_spec(_flow_spec); _flow_spec.loader.exec_module(_flow)
_hist_spec = importlib.util.spec_from_file_location("board_history", pathlib.Path(__file__).resolve().parent / "board_history.py")
_hist = importlib.util.module_from_spec(_hist_spec); _hist_spec.loader.exec_module(_hist)
_cfd_spec = importlib.util.spec_from_file_location("board_cfd", pathlib.Path(__file__).resolve().parent / "board_cfd.py")
_cfd = importlib.util.module_from_spec(_cfd_spec); _cfd_spec.loader.exec_module(_cfd)
_leadtime_spec = importlib.util.spec_from_file_location("board_leadtime", pathlib.Path(__file__).resolve().parent / "board_leadtime.py")
_leadtime = importlib.util.module_from_spec(_leadtime_spec); _leadtime_spec.loader.exec_module(_leadtime)
_fast_spec = importlib.util.spec_from_file_location("board_fast", pathlib.Path(__file__).resolve().parent / "board_fast.py")
board_fast = importlib.util.module_from_spec(_fast_spec); _fast_spec.loader.exec_module(board_fast)
_specstall_spec = importlib.util.spec_from_file_location("specstall", pathlib.Path(__file__).resolve().parent / "specstall.py")
specstall = importlib.util.module_from_spec(_specstall_spec); _specstall_spec.loader.exec_module(specstall)

# The hub is one namespace over several theme files (#2742): a theme holds one subject's code, so two tickets on two
# themes edit two files and mkreq.sh dispatches both at once. Each file is compiled into this module's namespace at its
# place in the original order, so every name, the module-level state and the tests that patch a name on the hub are what
# they were before the split. hub_paths in live-guard.sh publishes every module, and an edit here or in a theme
# re-execs the hub (watch_self).
_THEME_DIR = pathlib.Path(__file__).resolve().parent
_THEMES = (
    "hub_theme_core",
    "hub_theme_settings_limits",
    "hub_theme_settings_communication",
    "hub_theme_now",
    "hub_theme_settings_loop",
    "hub_theme_profiler",
    "hub_theme_trip",
    "hub_theme_runs",
    "hub_theme_stats",
    "hub_theme_board",
    "hub_theme_prs",
    "hub_theme_flow",
    "hub_theme_board_build",
    "hub_theme_promote",
    "hub_theme_page",
    "hub_theme_settings_subscriptions",
    "hub_theme_settings_models",
    "hub_theme_settings_board",
    "hub_theme_settings_access",
    "hub_theme_settings_personas",
)

def _load_theme(name):
    """Compile one theme file into this module's namespace, as the interpreter would run it here."""
    path = _THEME_DIR / (name + ".py")
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), globals())

for _theme in _THEMES:
    _load_theme(_theme)

# ---------- the hub server ----------
CHART_INPUTS = {"cfd": lambda: history_key(), "lead": lambda: (board_cache.get("t"), history_key())}
_board_parsed = {}   # the board body parsed once per ETag: the stream asks every tick, and a thousand cards parsed and dumped each time was most of the hub's CPU (#1790)
def board_subject():
    """The board event's content and its digest, in the shape hubstream.Versions reads without a serialization: the body
    is parsed once per ETag, and the ETag is already the hash of that body, so the stream never walks it again."""
    body, etag = board_served()
    if etag is None: return json.loads(body)   # an error body carries no ETag, so the stream hashes that small content as before
    if _board_parsed.get("etag") != etag: _board_parsed.update(etag=etag, content=json.loads(body))
    return _board_parsed["content"], etag
VERSIONS = hubstream.Versions({"runs": lambda: run_list(), "board": board_subject, **CHART_INPUTS})

_TICKERS = {}
_TICKERS_LOCK = threading.Lock()   # the reader threads are started once, however many pages ask (#1217)

def _ticker(name):
    """One subject's reader thread, alive or started here. The caller holds _TICKERS_LOCK, so two pages opening together
    find one reader and never start two reads of the same subject. It reads only while a page reads this profile: a
    subject streamed to no page has no reader to keep current (#1790). A page read wakes a parked reader at once."""
    t = _TICKERS.get(name)
    if t is not None and t.is_alive(): return t
    def run(n=name):
        fast_until = time.monotonic() + hubstream.FIRST_EVENT_SECONDS
        was_wanted = False
        while True:
            want = read_wanted()
            # (#1875) The fast period is a startup window, opened again when a page comes back: a subject whose reader
            # answers None never publishes, and the period used to stay at 20 ms for as long as a page read the profile,
            # reading fifty times a second and burning a whole core.
            if want and not was_wanted: fast_until = time.monotonic() + hubstream.FIRST_EVENT_SECONDS
            was_wanted = want
            if want:
                try: VERSIONS.refresh_one(n)   # read at once, so a page that just opened is never a tick behind
                except Exception: pass   # a failed read keeps the last version: the next tick retries and the panel never dies
            # a subject with no answer yet is asked again quickly inside that window: its first read may land mid-tick, and
            # the page waits; past the window it rests at the tick like every other subject
            answered = bool(VERSIONS.newer({}).get(n))
            period = hubstream.FIRST_POLL_SECONDS if (not answered and time.monotonic() < fast_until) else hubstream.TICK_SECONDS
            read_pause(period if want else max(period, hubstream.TICK_SECONDS))
    t = _TICKERS[name] = threading.Thread(target=run, daemon=True, name=f"hub-subject-{name}")
    t.start()
    return t

def versions_ticker():
    """One thread per subject reads it; a stream only sends what changed.

    A slow subject read held the stream's own thread and sent neither an event nor a heartbeat while it ran: the page
    looked frozen (ADR-0164, the owner's hub). One thread per subject keeps a slow read from delaying the others, and a
    stream never reads a subject in its own thread."""
    with _TICKERS_LOCK:
        for name in VERSIONS.names(): _ticker(name)
        return dict(_TICKERS)

def stream_origin_ok(h, tailnet):
    """A stream opens for no origin but this hub's own (loopback) or, on the tailnet, the tailnet's; an absent Origin is a non-browser reader."""
    org = h.headers.get("Origin")
    if org is None: return True
    return org == f"https://{tailnet_block().get('host')}" if tailnet else own_origin(h)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def refuse(self, reason, page=False):
        body = mobile_refusal(reason) if page else reason.encode()
        self.send_response(403); self.send_header("Content-Type", "text/html; charset=utf-8" if page else "text/plain; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def not_found(self):
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    def dash(self, method, body=b""):
        """A run's own dashboard, reached on loopback with its own Host and Origin, so its checks stay as they are. The body
        streams back as it arrives. Any other run id, port or host answers 404 and opens no connection."""
        raw = request_target(self)
        target = dash_target(raw)
        port = dash_port(target[0]) if target else None
        if port is None or (method == "POST" and target[1] != "/run/control"): return self.not_found()
        rest = target[1] + ("?" + raw.split("?", 1)[1] if "?" in raw else "")
        headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
        for k in ("Content-Type", "Last-Event-ID"):
            if self.headers.get(k): headers[k] = self.headers[k]
        up = http.client.HTTPConnection("127.0.0.1", port, timeout=DASH_TIMEOUT_SECS)
        try:
            up.request(method, rest, body=body if method == "POST" else None, headers=headers)
            r = up.getresponse()
        except (OSError, http.client.HTTPException):
            up.close(); msg = b"dashboard unreachable\n"
            self.send_response(502); self.send_header("Content-Length", str(len(msg))); self.end_headers(); self.wfile.write(msg); return
        try:
            data = None   # a run's page and its stream pass as they come; neither is held
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() in DASH_REPLY_HEADERS and not (data is not None and k.lower() == "content-length"): self.send_header(k, v)
            if data is not None: self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close"); self.end_headers(); self.close_connection = True
            if data is not None: self.wfile.write(data); return
            while chunk := r.read1(65536):
                self.wfile.write(chunk); self.wfile.flush()
        except (OSError, http.client.HTTPException): pass
        finally: up.close()
    def stream(self, a):
        """The page's push stream: every subject the reader does not hold yet, then a heartbeat while nothing changes.
        Each subject is read once a tick, whatever the pages that watch it."""
        if not stream_origin_ok(self, a.tailnet): return self.refuse("forbidden origin")
        query = self.path.partition("?")[2]
        held = hubstream.held_from(query)
        want = hubstream.want_from(query)
        try:
            self.protocol_version = "HTTP/1.1"   # the body is streamed, so its end is framed: chunked, then the close
            self.send_response(200); self.send_header("Content-Type", hubstream.CTYPE); self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked"); self.send_header("Connection", "close"); self.end_headers()
            self.close_connection = True
            def chunk(data):
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            opened, quiet, painted = time.monotonic(), time.monotonic(), False
            while True:
                versions_ticker()
                sent = False
                for name, (v, body) in sorted(VERSIONS.fresh(held).items()):
                    chunk(hubstream.event(name, v, body if name in want else None)); held[name] = v; sent = True
                now = time.monotonic()
                if sent: quiet, painted = now, True
                elif now - quiet >= hubstream.HEARTBEAT_SECONDS:
                    chunk(hubstream.frame("ping", {})); quiet = now
                # a page not painted yet takes the first reader that answers, never the end of a whole tick
                fast = not painted and now - opened < hubstream.FIRST_EVENT_SECONDS
                time.sleep(hubstream.FIRST_POLL_SECONDS if fast else hubstream.TICK_SECONDS)
        except OSError: return

    def do_GET(self):
        a = access(self, route_action("GET", self.path))
        if not a.allowed: return self.refuse(a.reason, page=self.path.split("?", 1)[0] in ("/mobile", "/mobile/"))
        if self.path.split("?", 1)[0] == hubstream.PATH and hubstream.enabled(): return self.stream(a)
        if request_target(self).startswith(DASH_PREFIXES): return self.dash("GET")
        if self.path.startswith(("/held/", "/ended/")):   # a run the childcc pass holds, or one that ended today: its dashboard (#689, #830)
            name = re.sub(r"[^\w-]", "", self.path.split("/", 2)[2].split("?")[0])
            if (open_held if self.path.startswith("/held/") else open_ended)(name) is None: return self.not_found()
            refresh_runs()   # the run list names the new dashboard, so the forward finds it
            self.send_response(302); self.send_header("Location", f"/dash/{name}/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.path.startswith("/test/"):   # a test.sh pass: its log, refreshed while it runs
            n = re.sub(r"[^\w-]", "", self.path[6:]); T = STATE / "tests"
            try: d = json.loads((T / (n + ".json")).read_text())
            except Exception: d = {}
            try: lg = (T / (n + ".log")).read_text(errors="replace")[-60000:]
            except Exception: lg = "(no output yet)" if d.get("state") == "running" else "no such test"   # test.sh makes the log after the json
            body, ct = test_log_page(d, lg), "text/html; charset=utf-8"
        elif self.path.startswith("/api/runs"):   # the bare route keeps its array; `?groups=1` asks for the Live group too (#1277)
            body, ct = json.dumps(runs_answer() if re.search(r"[?&]groups=1\b", self.path) else run_list()).encode(), "application/json"
        elif self.path.startswith("/api/adapter-errors"): body, ct = json.dumps(adapter_errors()).encode(), "application/json"
        elif self.path.startswith("/api/delivery"): body, ct = json.dumps(delivery()).encode(), "application/json"
        elif self.path.startswith("/api/ended"): body, ct = json.dumps(ended_today()).encode(), "application/json"
        elif self.path.startswith("/api/pending"): body, ct = json.dumps(pending_served()).encode(), "application/json"   # Now's decisions across every date (SPEC 2498, #2500)
        elif self.path.startswith("/api/slack-link"): body, ct = json.dumps(slack_link()).encode(), "application/json"   # the project channel's address, resolved by reads alone (#2500)
        elif self.path.startswith("/api/live"): body, ct = json.dumps(live_served()).encode(), "application/json"   # one compute per poll window, whatever asks (#1933)
        elif self.path.startswith("/api/stats"): body, ct = json.dumps(stats_served((re.search(r"period=(\w+)", self.path) or [0, "day"])[1] if re.search(r"period=(hour|day|month)", self.path) else "day", (re.search(r"src=(sub|api)", self.path) or [0, "both"])[1])).encode(), "application/json"
        elif self.path.startswith("/api/issue"):
            m = re.search(r"[?&]n=(\d{1,6})\b", self.path); rr = re.search(r"[?&]r=([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)", self.path)
            body, ct = json.dumps(issue_detail(int(m[1]), rr[1] if rr else None) if m else {"err": "bad n"}).encode(), "application/json"
        elif self.path.startswith("/api/board"):
            body, etag = board_served(); ct = "application/json"
            if etag and etag in [t.strip().removeprefix("W/") for t in (self.headers.get("If-None-Match") or "").split(",")]:   # the page holds this board (#973)
                self.send_response(304); self.send_header("ETag", etag); self.send_header("Cache-Control", "no-store"); self.end_headers(); return
            self.board_etag = etag
        elif self.path.startswith("/api/chart/cfd"): body, ct = json.dumps(flow_cfd(*cfd_filters(self.path.partition("?")[2]))).encode(), "application/json"
        elif self.path.startswith("/api/chart/lead"): body, ct = json.dumps(flow_lead(*board_filters(self.path.partition("?")[2]))).encode(), "application/json"
        elif self.path.startswith("/api/leadtime"): body, ct = json.dumps(lead_zoom(self.path.partition("?")[2])).encode(), "application/json"   # the control chart over a dragged range (#900)
        elif self.path.startswith("/api/version"): body, ct = json.dumps({"v": BOOT}).encode(), "application/json"
        elif self.path.startswith("/api/subs"): body, ct = json.dumps({**subs_month(), "presets": {k: {i: {"name": n, "usd": p} for i, (n, p) in v.items()} for k, v in PRESETS.items()},
                                                                    "gauges": subs_gauges(), "file": str(PLANS)}).encode(), "application/json"   # the theme draws the sidebar card's own rows, and names its file (#1281)
        elif self.path.startswith("/api/models"): body, ct = json.dumps(models_get()).encode(), "application/json"
        elif self.path.startswith("/api/limits"): body, ct = json.dumps(limits_read()).encode(), "application/json"   # Settings > Spend & limits (#926)
        elif self.path.startswith("/api/columns"): body, ct = json.dumps(columns_read()).encode(), "application/json"   # Settings > Spend & limits · Kanban (#2432)
        elif self.path.startswith("/api/slots"): body, ct = json.dumps(slots_read()).encode(), "application/json"   # Settings > Spend & limits · Machine slots (#2435)
        elif self.path.split("?", 1)[0] == "/api/slack/files": body, ct = json.dumps(slack_overview()).encode(), "application/json"   # Settings > Communication · the read view (SPEC 2682 #2693)
        elif self.path.startswith("/api/slack/decide"): body, ct = json.dumps(slack_decide(dict(urllib.parse.parse_qsl(self.path.partition("?")[2])))).encode(), "application/json"   # Settings > Communication · the sender preview: one decide, no send (SPEC 2682 #2826)
        elif self.path.split("?", 1)[0] == "/api/slack": return self.not_found()   # the old per-profile settings route is retired (#2701)
        elif self.path.split("?", 1)[0] == "/api/slack/migrate": return self.not_found()   # its reader left with the file (#2701)
        elif self.path.startswith("/api/speed"): body, ct = json.dumps(speed()).encode(), "application/json"
        elif self.path.startswith("/api/load"): body, ct = json.dumps(load_payload()).encode(), "application/json"
        elif self.path.startswith("/api/status"): body, ct = json.dumps(status_payload()).encode(), "application/json"
        elif self.path.split("?", 1)[0] == "/api/loop": body, ct = json.dumps(loop_view(a)).encode(), "application/json"
        elif self.path.split("?", 1)[0] == "/api/profiler":   # the Loop tab (#1743); `?w=` is the failed-run table's window (#1751), `&sw=` the strip's (#1765), `&ww=` the wall-time view's (#1748)
            q = re.search(r"[?&]w=(\d{1,9})\b", self.path)
            sw = re.search(r"[?&]sw=(\d{1,9})\b", self.path)
            ww = re.search(r"[?&]ww=(\d{1,9})\b", self.path)
            body, ct = json.dumps(profiler_served(a, q[1] if q else None, sw[1] if sw else None, ww[1] if ww else None)).encode(), "application/json"
        elif self.path.split("?", 1)[0] == "/api/access": body, ct = json.dumps(access_view(a)).encode(), "application/json"   # Settings > Access (#1285)
        elif self.path.split("?", 1)[0] == "/api/personas": body, ct = json.dumps(personas_overview()).encode(), "application/json"   # Settings > Personas (#3010)
        elif self.path.split("?", 1)[0] in ("/mobile", "/mobile/"): body, ct = mobile_html(), "text/html; charset=utf-8"
        elif self.path.split("?", 1)[0] == "/mobile/sw.js": body, ct = mobile_sw(), "text/javascript; charset=utf-8"
        elif (asset := MOBILE_ASSETS.get(self.path.split("?", 1)[0])): body, ct = asset
        else: body, ct = served_page(a.login, a.allowed).encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store")
        if self.path.startswith("/api/board") and getattr(self, "board_etag", None): self.send_header("ETag", self.board_etag)
        if self.path.split("?", 1)[0] in ("/mobile", "/mobile/"): self.send_header("Content-Security-Policy", MOBILE_CSP)
        self.end_headers(); self.wfile.write(body)

    def loop_post(self, a, slug=None):
        """The loop switch's write (#923): the access rule, the tailnet grant, the own or tailnet origin and a JSON body, then
        the switch. `slug` names one project's own route, so a project's page writes that project and no other (#1599)."""
        length = self.headers.get("Content-Length") or "0"
        raw = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
        tn = tailnet_block()
        origin_ok = "json" in (self.headers.get("Content-Type") or "") and ((self.headers.get("Origin") or "") == f"https://{tn.get('host')}" if a.tailnet else own_origin(self))
        try: body = json.loads(raw or b"{}")
        except ValueError: body = None
        code, out = (loop_switch_project(a, slug, body, origin_ok) if slug else loop_switch(a, body, origin_ok))
        if code == 200: live_recompute()   # the page reads its own change at once, never a window-old answer (#1933)
        data = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def profiler_post(self, a):
        """The Loop tab's switch write (#1743), read as the loop switch's write is: the body before any refusal, then the
        own or tailnet origin and a JSON body. It has its own name so the many-repository hub forwards it by that name."""
        length = self.headers.get("Content-Length") or "0"
        raw = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
        tn = tailnet_block()
        origin_ok = "json" in (self.headers.get("Content-Type") or "") and ((self.headers.get("Origin") or "") == f"https://{tn.get('host')}" if a.tailnet else own_origin(self))
        try: body = json.loads(raw or b"{}")
        except ValueError: body = None
        self.answer(*profiler_switch(a, body, origin_ok))

    def priority_post(self, a):
        """The Kanban card's Priority write (#2289): the access rule, the tailnet grant and the origin are checked before the
        body is read. A refusal, or a Content-Length that is not a number, ends the connection with its answer."""
        tn = tailnet_block()
        origin, want = self.headers.get("Origin") or "", tailnet_origin(tn)
        origin_ok = "json" in (self.headers.get("Content-Type") or "") and (bool(want) and origin == want if a.tailnet else own_origin(self))
        why = priority_refusal(a, origin_ok, tn)
        if why:
            self.close_connection = True   # the body stays unread, so the connection ends with this answer
            return self.answer(403, {"err": "forbidden", "reason": why})
        length = self.headers.get("Content-Length") or ""
        if not length.isdigit():
            self.close_connection = True
            return self.answer(400, {"err": "bad request", "reason": "Content-Length: a number"})
        if int(length) > 1 << 20:
            self.close_connection = True
            return self.answer(413, {"err": "too large"})
        self.answer(*priority_route(a, self.rfile.read(int(length))))

    def answer(self, code, out):
        data = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        if self.close_connection: self.send_header("Connection", "close")
        self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        # a cross-site page must not be able to write: a loopback write needs our own origin, a tailnet write the tailnet origin (the seam), and a JSON body
        a = access(self, route_action("POST", self.path))
        if self.path.split("?", 1)[0] in ("/api/slack", "/api/slack/migrate"):
            self.close_connection = True   # the retired route takes no body: the connection ends with this answer
            return self.not_found()
        if request_target(self).startswith(DASH_PREFIXES):   # the body is read before any refusal, as below
            length = self.headers.get("Content-Length") or "0"
            raw = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
            if not a.allowed: return self.refuse(a.reason)
            if not (a.tailnet or own_origin(self)) or "json" not in (self.headers.get("Content-Type") or ""): return self.refuse("forbidden")
            return self.dash("POST", raw)
        if self.path.split("?", 1)[0] == "/api/loop": return self.loop_post(a)
        if self.path.split("?", 1)[0] == "/api/profiler": return self.profiler_post(a)
        if self.path.split("?", 1)[0] == "/api/board/priority": return self.priority_post(a)
        m = PROJECT_LOOP_ROUTE.match(self.path.split("?", 1)[0])
        if m: return self.loop_post(a, m.group(1))   # that project's own loop page writes that project alone (#1599)
        ok = a.allowed and self.path in ("/api/models", "/api/plans", "/api/promote", "/api/limits", "/api/columns", "/api/slots", "/api/slack/files", "/api/personas", "/api/personas/remove", "/api/slack/preview", "/api/slack/sample", "/api/slack/clients", "/api/slack/people", "/api/slack/channels", "/api/slack/owner", "/api/slack/grant", "/api/sync") and (a.tailnet or own_origin(self)) and "json" in (self.headers.get("Content-Type") or "")
        read_only = self.path == "/api/sync"   # a press is a read: its failed read answers an error in a 200, never a request defect (#1668)
        try:
            raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20))   # read before any answer: a refusal that leaves the body unread resets the connection; the models panel posts back ~20 KB of read-only fields (#684)
            if not ok: out = {"err": "forbidden"}
            else:
                body = json.loads(raw or b"{}")
                if self.path == "/api/sync": out = sync_card(body.get("n") if isinstance(body, dict) else None)   # one card from GitHub, now (#1668)
                elif self.path == "/api/promote" and isinstance(body, dict) and body.get("batch") is True: out = promote_batch(body, a.login or "owner")   # the owner's batch move (#1098)
                elif self.path == "/api/promote": out = promote(body.get("issue")) if isinstance(body, dict) else {"err": "bad request"}   # the owner's move (#560)
                elif self.path == "/api/limits": out = limits_set(body)   # Settings > Spend & limits (#926)
                elif self.path == "/api/columns": out = columns_set(body)   # Settings > Spend & limits · Kanban (#2432)
                elif self.path == "/api/slots": out = slots_set(body)   # Settings > Spend & limits · Machine slots (#2435)
                elif self.path == "/api/slack/files": out = slack_files_post(body)   # Settings > Communication · the save (#2694) or one edit (#2696)
                elif self.path == "/api/slack/people": out = slack_people_post(body)   # a member named by email or @name (#2911)
                elif self.path == "/api/slack/channels": out = slack_channels_post(body)   # a channel picked by name (#2918)
                elif self.path == "/api/slack/owner": out = slack_owner_post(body)   # the owner user id of one workspace (#2966)
                elif self.path == "/api/slack/grant": out = slack_grant_post(body)   # one grant of full, kept in the registry (#2971)
                elif self.path == "/api/slack/clients": out = slack_clients_post(body)   # Settings > Communication · a client default's proposed writes (#2827)
                elif self.path == "/api/personas": out = personas_post(body)   # Settings > Personas · the edit (#3011) or the add (#3013)
                elif self.path == "/api/personas/remove": out = personas_remove(body)   # Settings > Personas · the persona removal (#3014)
                elif self.path == "/api/slack/preview": out = slack_preview(body.get("committed"), body.get("local")) if isinstance(body, dict) else slack_preview(None, None)   # the persona editor's reply preview (#2697)
                elif self.path == "/api/slack/sample": out = slack_sample(body)   # the persona editor's voice sample (#2697)
                else: out = (plans_set if self.path == "/api/plans" else models_set)(body)
        except Exception as e: out = {"err": "bad request"}
        if not a.allowed and a.tailnet: return self.refuse(a.reason)
        body = json.dumps(out).encode(); self.send_response(200 if read_only else (400 if "err" in out else 200)); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

SELF = pathlib.Path(__file__).resolve()
# Every file this process compiles: the entry point and each theme it loads. An edit to any of them is a new build, so
# watch_self re-execs on any of them, not on hub.py alone (#2742).
WATCHED = {p: p.stat().st_mtime_ns for p in (SELF, *(_THEME_DIR / (name + ".py") for name in _THEMES))}
def _build_fact(path):
    """One source file's build fact as this process loaded it: its short SHA-256 and its mtime (#1298)."""
    p = pathlib.Path(path)
    return {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:12],
            "mtime": dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")}

# The build a send answers to: the files the hub imported and this process's start, read once at load. A process that predates
# an edit reports the old hashes here, so a stale process is settled from the status route, not from a guess (#1298).
HUB_BUILD = {"hub": _build_fact(SELF),
             "started": dt.datetime.fromtimestamp(BOOT / 1e9).astimezone().isoformat(timespec="seconds")}

def watch_self():
    """Re-exec when this file or a theme it loads changes on disk, so an edit goes live without anyone restarting the hub; open pages reload on the new /api/version."""
    while True:
        time.sleep(2)
        try:
            if any(p.stat().st_mtime_ns != stamp for p, stamp in WATCHED.items()): os.execv(sys.executable, [sys.executable, str(SELF)] + sys.argv[1:])
        except OSError: pass

def boot():
    """What the hub starts before it serves: the run list, each background read, and the sampler of a profiler switch
    left on (#1743); off, that starts nothing."""
    refresh_runs()
    threading.Thread(target=refresh_prs, daemon=True).start(); threading.Thread(target=refresh_quota, daemon=True).start(); threading.Thread(target=watch_self, daemon=True).start(); threading.Thread(target=refresh_board, daemon=True).start(); threading.Thread(target=refresh_history, daemon=True).start(); threading.Thread(target=refresh_fast, daemon=True).start(); threading.Thread(target=load_sampler, daemon=True).start(); threading.Thread(target=watch_runs, daemon=True).start()
    profiler_ensure()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7700
    boot()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
