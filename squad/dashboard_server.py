"""A loopback server for `squad dashboard`.

One server serves one run folder. It serves the built dashboard page and five read-only paths for that run:
`/run/record.jsonl`, `/run/graph.json`, `/run/info.json`, `/run/control.json` and `/run/events`. It binds 127.0.0.1
only and checks the Host header, so a web page on another origin cannot read a run through DNS rebinding. A request
through the tailnet route (Host equal to the project's `tailnet.host`) passes only with a listed login, decided in `squad/tailnet_access.py`.
It never starts a run. Its one write is `POST /run/control`: a pause or resume request that it appends
to the run's control file and to nothing else. Every other method is refused. It exits when no request
came for the idle time.

A page that asks for `/run/events` gets a push stream: the server watches the record and sends each new event
as it lands, so the page shows a live run without re-reading the record.
"""
import hashlib
import http.client
import json
import os
import re
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from squad import runs, tailnet_access
from squad.control import ACTIONS, Control

IDLE_SECONDS = 2 * 3600
LOOPBACK = "127.0.0.1"
WATCH_SECONDS = 0.2          # how often the watcher stats the record path while a stream is open
HEARTBEAT_SECONDS = 15.0     # a quiet stream still writes this often, so a dead peer is found
STREAM_CTYPE = "text/event-stream; charset=utf-8"
RECORD_RELS = ("record.jsonl", "run/record.jsonl")
CONTROL_PATH = "/run/control"    # the one write route
MAX_BODY = 4096                  # a request is a few names; a larger body is refused unread
MAX_NODES = 100
NODE_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,200}")     # a node or a child, never one `each` item: a hold is by name
BODY_TIMEOUT = 5.0                                    # a client that stalls on its body frees the thread
ENDED = ("completed", "failed", "conflict", "needs-human", "no-repro")


def _inside(path, folder):
    """The file at `path` when it is a regular file whose real path stays inside `folder`, else None."""
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not real.is_file() or (real != folder and folder not in real.parents):
        return None
    return real


def issue_count(folder):
    """The issue set a run folder holds: the .md files under `target/issues`, or under `run/target/issues`, else 0.

    The record carries no issue-set size, so the page reads the count here for its top bar. Read-only:
    a fixed relative folder is read, no part of a request joins a path.
    """
    for rel in ("target/issues", "run/target/issues"):
        d = Path(folder) / rel
        if d.is_dir():
            return sum(1 for p in d.iterdir() if p.is_file() and p.suffix == ".md")
    return 0


def held_count(folder):
    """How many issues the run's start tree already carried: `holds.json`, written by a chained run, else 0."""
    try:
        return len(json.loads((Path(folder) / "holds.json").read_text())["held"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def run_ended(folder):
    """True when the run's last invocation ended with a verdict that a pause or resume cannot change.

    A run that ended on a pause (`stopped`, reason `paused:`) is not ended: a resume request lets its hold go,
    and `squad resume` then continues it. A `stopped` run from `--stop-after` and a `needs-human` run are ended.
    """
    path = record_path(folder)
    if path is None:
        return False
    since, last = 0, None
    for line in path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") in ("run_start", "resume"):
            since, last = ev.get("pos", 0), None
        elif ev.get("event") == "verdict" and ev.get("pos", 0) > since:
            last = ev
    if last is None:
        return False
    return last["verdict"] in ENDED or (last["verdict"] == "stopped" and not any(str(r).startswith("paused:") for r in last.get("reasons", [])))


def parse_control(raw):
    """`(action, nodes)` for a valid request body, else None. `nodes` is None for the whole run."""
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("action") not in ACTIONS or set(body) - {"action", "nodes"}:
        return None
    nodes = body.get("nodes")
    if nodes is None:
        return body["action"], None
    if not isinstance(nodes, list) or not 0 < len(nodes) <= MAX_NODES or not all(isinstance(n, str) and NODE_NAME.fullmatch(n) for n in nodes):
        return None
    return body["action"], nodes


def record_path(folder):
    """The record of this run folder: `record.jsonl`, or a nested `run/record.jsonl`."""
    return next((f for f in (_inside(Path(folder) / rel, Path(folder)) for rel in RECORD_RELS) if f), None)


class RecordFeed:
    """The run record as it grows, read once for every open stream.

    Every append rewrites the whole file by atomic replace, so the feed watches the path: its size
    and its modification time. It holds no descriptor between reads, and the file on that path is never the old
    one. Each new line goes to every open stream, in the order the record holds it.

    The feed stops when nothing more can arrive, and says why: the run's verdict is now on the wire, the record's
    positions hold a gap, or the record left the folder. The reason travels to the page on the stream.
    """

    def __init__(self, folder, step=WATCH_SECONDS):
        self.folder, self.step = Path(folder), step
        self.lock = threading.Condition()
        self.lines = []          # (position, raw line) for every event read so far, in record order
        self.end = None          # the reason nothing more will come, or None while the run lives
        self.reads = 0           # how many times the file was read: one watcher, whatever the client count
        self.clients = 0         # streams open now; the watcher runs only while this is above zero
        self._stamp, self._seen, self._thread = None, False, None

    def attach(self):
        """A stream opened: start the watcher if no stream held it."""
        with self.lock:
            self.clients += 1
            if self._thread is None and not self.end:
                self._thread = threading.Thread(target=self._watch, daemon=True, name="record-feed")
                self._thread.start()
        return self

    def detach(self):
        """A stream closed: the watcher stops at its next step when none is left."""
        with self.lock:
            self.clients -= 1

    def since(self, pos):
        """The (position, line) pairs after `pos`, and the reason the feed ended."""
        with self.lock:
            return [(p, line) for p, line in self.lines if p > pos], self.end

    def wait(self, pos, timeout):
        """True when an event after `pos` is ready or the feed ended; False when the timeout passed first."""
        with self.lock:
            if self.end or any(p > pos for p, _ in self.lines):
                return True
            self.lock.wait(timeout)
            return bool(self.end) or any(p > pos for p, _ in self.lines)

    def _end(self, reason):
        with self.lock:
            self.end = self.end or reason
            self.lock.notify_all()

    def _watch(self):
        while True:
            with self.lock:
                if self.end:
                    return
                if self.clients <= 0:       # no page is following: stop here, and the next stream starts a new watcher
                    self._thread = None
                    return
            path = record_path(self.folder)
            if path is None:
                if self._seen:          # the record was here and is gone: nothing more can arrive
                    return self._end("record gone")
                time.sleep(self.step)
                continue
            self._seen = True
            try:
                st = path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                time.sleep(self.step)
                continue
            if stamp != self._stamp:
                self._stamp = stamp
                self._read(path)
            with self.lock:
                if self.end:
                    return
            time.sleep(self.step)

    def _read(self, path):
        """Read the whole record and hand the lines the feed has not seen yet to every stream."""
        self.reads += 1
        try:
            text = path.read_text()
        except OSError:
            return
        fresh, last, gap = [], self.lines[-1][0] if self.lines else 0, False
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                pos = json.loads(line).get("pos")
            except ValueError:
                pos = None
            if not isinstance(pos, int):
                gap = True                  # a line that names no position cannot be placed, so the page must not paint it
                break
            if pos <= last:
                continue                    # already delivered, or rewritten under a stream that has it
            if pos != last + 1:
                gap = True                  # a position that skips one is a defect, never a state to paint
                break
            last = pos
            fresh.append((pos, line))
        verdict = any(json.loads(line).get("event") == "verdict" for _, line in fresh)
        if fresh:
            with self.lock:
                self.lines.extend(fresh)    # the lines before a gap are sound: they go out, then the stream ends
                self.lock.notify_all()
        if gap:
            self._end("gap")
        elif verdict:
            self._end("verdict")


class RunServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, folder, page=None, port: int = 0, idle: float = IDLE_SECONDS, watch: float = WATCH_SECONDS, clock=time.monotonic,
                 tailnet_host=None, allowed_logins=()):
        self.folder = Path(folder).resolve()
        self.page = Path(page) if page else runs.PAGE
        self.idle = idle
        self.watch = watch
        self.clock = clock      # the idle clock; a test passes a fake one
        self.tailnet_host = (tailnet_host or "").lower() or None
        self.allowed_logins = list(allowed_logins)
        self.last = clock()
        self.port = 0
        self._feed = None
        self._feed_lock = threading.Lock()
        super().__init__((LOOPBACK, port), _Handler)
        self.port = self.server_address[1]
        self.timeout = min(1.0, idle / 4)   # handle_request wakes at least once a second (less for a short idle time), so the idle check runs

    def feed(self):
        """The one feed of this run, shared by every stream. Its watcher runs only while a stream is attached."""
        with self._feed_lock:
            if self._feed is None:
                self._feed = RecordFeed(self.folder, self.watch)
            return self._feed

    def route(self, path):
        """(real file, content type) for an allowed path, or None. The allowlist is fixed: no part of the URL joins a path."""
        if path in ("/", "/index.html"):
            return _inside(self.page, self.page.resolve().parent), "text/html; charset=utf-8"
        if path == "/run/record.jsonl":
            return record_path(self.folder), "text/plain; charset=utf-8"
        if path == "/run/graph.json":
            return _inside(self.folder / "graph.json", self.folder), "application/json; charset=utf-8"
        return None, ""

    def control_state(self):
        whole, held, released = Control(self.folder).state()
        return {"whole": whole, "nodes": sorted(held), "released": sorted(released)}

    def info(self):
        return {"name": self.folder.name, "folder": str(self.folder), "issues": issue_count(self.folder), "held": held_count(self.folder), "ended": run_ended(self.folder), "watch_seconds": self.watch}

    def serve_until_idle(self):
        while self.clock() - self.last < self.idle:
            self.handle_request()
        self.server_close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "agent-squad-dashboard"
    sys_version = ""

    @property
    def _run(self) -> RunServer:
        """The server of this handler: it holds the port, the folder and the idle clock."""
        return cast(RunServer, self.server)

    def log_message(self, format, *args):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _verdict(self, action):
        """Whether this request may pass: the one access decision (squad/tailnet_access.py), made on the action asked for."""
        return tailnet_access.decide(host=(self.headers.get("Host") or "").lower(), origin=self.headers.get("Origin"), action=action,
                                     login=self.headers.get(tailnet_access.LOGIN_HEADER), port=self._run.port,
                                     tailnet_host=self._run.tailnet_host, allowed_logins=self._run.allowed_logins)

    def _is_loopback(self):
        return (self.headers.get("Host") or "").lower() in (f"{LOOPBACK}:{self._run.port}", f"localhost:{self._run.port}")

    def do_GET(self):
        verdict = self._verdict(tailnet_access.VIEW)
        if not verdict.allowed:
            return self._send(403, (verdict.reason + "\n").encode())
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/__ping":   # the check that finds a running server is not use: it must not keep the server alive
            return self._send(200, json.dumps({"folder": str(self._run.folder), "pid": os.getpid()}).encode(), "application/json; charset=utf-8")
        if path == "/run/events":   # the push stream: it holds the connection open, so it owns the idle clock while it lasts
            return self._stream()
        self._run.last = self._run.clock()
        if path == "/run/info.json":
            return self._send(200, json.dumps(self._run.info()).encode(), "application/json; charset=utf-8")
        if path == "/run/control.json":
            return self._send(200, json.dumps(self._run.control_state()).encode(), "application/json; charset=utf-8")
        real, ctype = self._run.route(path)
        if real is None:
            return self._send(404, b"not found\n")
        try:
            body = real.read_bytes()
        except OSError:
            return self._send(404, b"not found\n")
        self._send(200, body, ctype)

    def _chunk(self, body: bytes):
        """One chunk of a chunked response. An empty body closes the body, as HTTP/1.1 says."""
        self.wfile.write(b"%x\r\n" % len(body) + body + b"\r\n")

    def _last_event_id(self):
        try:
            return int(self.headers.get("Last-Event-ID"))
        except (TypeError, ValueError):
            return 0

    def _stream(self):
        """Send the record's new lines as they land, then the reason the run ended.

        The connection answers as a chunked `text/event-stream`, so the client reads each event as it arrives.
        The stream closes when the run ends; a client that went away ends its own handler thread with no noise.
        """
        feed = self._run.feed()
        pos = self._last_event_id()
        self.protocol_version = "HTTP/1.1"      # the body is streamed, so its end must be framed: chunked, or a close
        self.send_response(200)
        self.send_header("Content-Type", STREAM_CTYPE)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        feed.attach()
        try:
            while True:
                fresh, end = feed.since(pos)
                for p, line in fresh:
                    self._chunk(f"id: {p}\ndata: {line}\n\n".encode())
                    pos = p
                if end:
                    self._chunk(("event: end\ndata: " + json.dumps({"reason": end}) + "\n\n").encode())
                    self._chunk(b"")
                    return
                self._run.last = self._run.clock()       # a connected stream keeps the server past its idle window
                if not feed.wait(pos, HEARTBEAT_SECONDS):
                    if self._peer_gone():                # a page that left (its frame was detached) frees the watcher at once
                        return
                    self._chunk(b": ping\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            return
        finally:
            feed.detach()

    def _peer_gone(self):
        """True when the client closed its end: the socket is readable and reads nothing."""
        try:
            if not select.select([self.connection], [], [], 0)[0]:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _refuse(self):
        self._send(405, b"read only\n")

    def do_POST(self):
        """The one write: a pause or resume request goes to the run's control file.

        The page's own origin and a JSON content type are both required, so a form or a script on another site
        cannot send it; the Host check stops DNS rebinding. The body is small and validated before anything is written.
        """
        verdict = self._verdict(tailnet_access.CONTROL)
        if not verdict.allowed:
            return self._send(403, (verdict.reason + "\n").encode())
        if self.path.split("?", 1)[0].split("#", 1)[0] != CONTROL_PATH:
            return self._refuse()
        origin = self.headers.get("Origin")
        if self._is_loopback() and origin is not None and origin not in (f"http://{LOOPBACK}:{self._run.port}", f"http://localhost:{self._run.port}"):
            return self._send(403, b"forbidden origin\n")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._send(415, b"json only\n")
        try:
            n = int(self.headers.get("Content-Length"))
        except (TypeError, ValueError):
            return self._send(411, b"length required\n")
        if not 0 <= n <= MAX_BODY:
            return self._send(413, b"too large\n")
        try:
            self.connection.settimeout(BODY_TIMEOUT)
            raw = self.rfile.read(n)
        except OSError:
            return self._send(408, b"timeout\n")
        req = parse_control(raw)
        if req is None:
            return self._send(400, b"bad request\n")
        self._run.last = self._run.clock()
        if run_ended(self._run.folder):
            return self._send(409, b'{"ok": false, "error": "run_ended"}', "application/json; charset=utf-8")
        done = Control(self._run.folder).append(req[0], req[1], source="dashboard")
        self._send(200, json.dumps({"ok": True, "request": done}).encode(), "application/json; charset=utf-8")

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse


# --- the detached process and its reuse ---

def _state_dir():
    return Path(os.environ.get("SQUAD_DASHBOARD_STATE_DIR") or Path(tempfile.gettempdir()) / "agent-squad-dashboard")


def _state_file(folder):
    return _state_dir() / (hashlib.sha1(str(Path(folder).resolve()).encode()).hexdigest()[:16] + ".json")


def _ping(port, folder):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://{LOOPBACK}:{port}/__ping", headers={"Host": f"{LOOPBACK}:{port}"}), timeout=2) as r:
            return json.loads(r.read()).get("folder") == str(Path(folder).resolve())
    except (OSError, ValueError, http.client.HTTPException):   # a server that exits mid-answer is not running
        return False


def running(folder):
    """The port of a live server for this folder, or None. A stale state file is removed."""
    f = _state_file(folder)
    try:
        port = int(json.loads(f.read_text())["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if _ping(port, folder):
        return port
    f.unlink(missing_ok=True)
    return None


def _server_argv(folder, idle, watch, tailnet_host, allowed_logins):
    argv = [sys.executable, "-m", "squad.dashboard_server", str(Path(folder).resolve()), "--idle", str(idle), "--watch", str(watch)]
    if tailnet_host:
        argv += ["--tailnet-host", tailnet_host]
    for login in allowed_logins:
        argv += ["--tailnet-allow", login]
    return argv


def start(folder, idle=IDLE_SECONDS, wait=15.0, watch=WATCH_SECONDS, tailnet_host=None, allowed_logins=()):
    """The port of a server for this folder: the one that runs, or a new detached one."""
    port = running(folder)
    if port:
        return port
    _state_dir().mkdir(parents=True, exist_ok=True)
    f = _state_file(folder)
    f.unlink(missing_ok=True)
    subprocess.Popen(_server_argv(folder, idle, watch, tailnet_host, allowed_logins),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        port = running(folder)
        if port:
            return port
        time.sleep(0.1)
    raise RuntimeError("the dashboard server did not start")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="squad.dashboard_server")
    ap.add_argument("folder")
    ap.add_argument("--idle", type=float, default=IDLE_SECONDS)
    ap.add_argument("--watch", type=float, default=WATCH_SECONDS)
    ap.add_argument("--tailnet-host", default=None)
    ap.add_argument("--tailnet-allow", action="append", default=[])
    a = ap.parse_args(argv)
    srv = RunServer(a.folder, idle=a.idle, watch=a.watch, tailnet_host=a.tailnet_host, allowed_logins=a.tailnet_allow)
    f = _state_file(a.folder)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": srv.port, "pid": os.getpid(), "folder": str(srv.folder)}))
    tmp.replace(f)
    try:
        srv.serve_until_idle()
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
