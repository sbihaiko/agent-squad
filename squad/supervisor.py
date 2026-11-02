"""One daemon owns spawning, the terminal pair and the attach point of a job."""

import hashlib
import hmac
import json
import os
import platform
import pty
import secrets
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = "supervisor.secret"
PIDFILE = "supervisor.pid"
LEDGER = "sessions.jsonl"
CLIENTS = "clients.json"
AUDIT = "supervisor-audit.log"
LOG = "supervisor.log"
WINDOW = 1 << 16


class Refused(Exception):
    pass


def socket_path(state: Path) -> Path:
    # AF_UNIX paths are capped near 104 bytes on macOS, which a state folder under a temp dir exceeds.
    digest = hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:16]
    folder = Path("/tmp") / f"squad-sup-{os.getuid()}"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder / f"{digest}.sock"


def lstart(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    return out.stdout.strip()


_SO_PEERCRED = 17  # Linux only; the socket module does not expose it on macOS


def peer_cred(sock: socket.socket) -> tuple[int, int]:
    if platform.system() == "Darwin":
        pid = struct.unpack("i", sock.getsockopt(0, 2, struct.calcsize("i")))[0]  # SOL_LOCAL, LOCAL_PEERPID
        xucred = sock.getsockopt(0, 1, 76)  # SOL_LOCAL, LOCAL_PEERCRED; struct xucred is 76 bytes
        uid = struct.unpack("I", xucred[4:8])[0]
        return pid, uid
    pid, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i")))
    return pid, uid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Gate:
    def __init__(self, path: Path, secret: str, uid: int):
        self.path = path
        self.secret = secret
        self.uid = uid
        self.lock = threading.Lock()

    def admit(self, *, presented: str, peer_pid: int, peer_uid: int, peer_lstart: str) -> str | None:
        if not hmac.compare_digest(presented.encode(), self.secret.encode()):
            return "no valid secret"
        if peer_uid != self.uid:
            return "a process of another user"
        with self.lock:
            record = json.loads(self.path.read_text()) if self.path.exists() else {}
            known = record.get(str(peer_pid))
            if known is not None and known != peer_lstart:
                return "start time does not match the record"
            record = {pid: start for pid, start in record.items() if _alive(int(pid))}
            record[str(peer_pid)] = peer_lstart
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record))
            os.replace(tmp, self.path)
        return None


class Session:
    def __init__(self, sid: str, job: str, proc: subprocess.Popen, master: int):
        self.sid = sid
        self.job = job
        self.proc = proc
        self.master = master
        self.buf = bytearray()
        self.base = 0
        self.buf_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.closed = False
        self.stop_requested = False
        self.pump = threading.Thread(target=self._pump, daemon=True)

    def _pump(self) -> None:
        while True:
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            with self.buf_lock:
                self.buf += chunk
                over = len(self.buf) - WINDOW
                if over > 0:
                    del self.buf[:over]
                    self.base += over

    def read(self, offset: int) -> dict:
        with self.buf_lock:
            end = self.base + len(self.buf)
            start = max(offset, self.base)
            data = bytes(self.buf[start - self.base:])
            return {"data": data.decode("utf-8", "replace"), "offset": end, "gap": offset < self.base}


class Supervisor:
    def __init__(self, state: Path):
        self.state = state
        self.secret = secrets.token_hex(32)
        self.gate = Gate(state / CLIENTS, self.secret, os.geteuid())
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self._end_lost_sessions()

    def _ledger(self, event: dict) -> None:
        with (self.state / LEDGER).open("a") as ledger:
            ledger.write(json.dumps({"ts": time.time(), **event}) + "\n")

    def _audit(self, op: str, **fields: object) -> None:
        with self.lock, (self.state / AUDIT).open("a") as audit:
            audit.write(json.dumps({"ts": time.time(), "op": op, **fields}) + "\n")

    def _end_lost_sessions(self) -> None:
        path = self.state / LEDGER
        if not path.exists():
            return
        open_sids = {}
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["event"] == "start":
                open_sids[event["session"]] = True
            elif event["event"] == "end":
                open_sids.pop(event["session"], None)
        for sid in open_sids:
            self._ledger({"event": "end", "session": sid, "reason": "lost"})

    def _live_job(self, job: str) -> bool:
        return any(s.job == job and not s.closed for s in self.sessions.values())

    def start(self, job: str, argv: list[str], cwd: str | None) -> str:
        with self.lock:
            if self._live_job(job):
                raise Refused(f"job {job} already has a live session")
            master, slave = pty.openpty()
            try:
                proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                                        start_new_session=True, close_fds=True)
            finally:
                os.close(slave)
            sid = uuid.uuid4().hex
            session = Session(sid, job, proc, master)
            self.sessions[sid] = session
            self._ledger({"event": "start", "session": sid, "job": job, "pid": proc.pid,
                          "lstart": lstart(proc.pid), "argv": argv})
            session.pump.start()
        threading.Thread(target=self._reap, args=(session,), daemon=True).start()
        return sid

    def _reap(self, session: Session) -> None:
        session.proc.wait()
        session.pump.join(timeout=5)
        with session.write_lock:
            session.closed = True
            os.close(session.master)
        reason = "stopped" if session.stop_requested else "exited"
        with self.lock:
            self._ledger({"event": "end", "session": session.sid, "reason": reason,
                          "returncode": session.proc.returncode})

    def _session(self, sid: str) -> Session:
        session = self.sessions.get(sid)
        if session is None:
            raise Refused("not started by this daemon")
        return session

    def write(self, sid: str, text: str) -> None:
        session = self._session(sid)
        with session.write_lock:
            if session.closed:
                raise Refused("session ended")
            data = text.encode()
            os.write(session.master, data)
        self._audit("write", session=sid, bytes=len(data))

    def read(self, sid: str, offset: int) -> dict:
        self._audit("read", session=sid)
        return self._session(sid).read(offset)

    def stop(self, sid: str) -> None:
        session = self._session(sid)
        session.stop_requested = True
        try:
            os.killpg(session.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self._audit("stop", session=sid)

    def dispatch(self, request: dict, sock: socket.socket) -> dict:
        pid, uid = peer_cred(sock)
        reason = self.gate.admit(presented=str(request.get("secret", "")), peer_pid=pid, peer_uid=uid,
                                 peer_lstart=lstart(pid))
        if reason is not None:
            self._audit("refused", peer_pid=pid, peer_uid=uid, reason=reason)
            return {"ok": False, "reason": reason}
        try:
            return {"ok": True, **self._op(request)}
        except Refused as error:
            self._audit("refused", peer_pid=pid, peer_uid=uid, reason=str(error))
            return {"ok": False, "reason": str(error)}

    def _op(self, request: dict) -> dict:
        op = request.get("op")
        if op == "ping":
            return {}
        if op == "start":
            return {"session": self.start(request["job"], request["argv"], request.get("cwd"))}
        if op == "read":
            return self.read(request["session"], int(request["offset"]))
        if op == "write":
            self.write(request["session"], request["text"])
            return {}
        if op == "stop":
            self.stop(request["session"])
            return {}
        raise Refused(f"unknown op {op}")


class _Handler(socketserver.StreamRequestHandler):
    server: "_Server"

    def handle(self) -> None:
        line = self.rfile.readline(1 << 20)
        try:
            request = json.loads(line)
        except ValueError:
            reply = {"ok": False, "reason": "bad request"}
        else:
            reply = self.server.supervisor.dispatch(request, self.request)
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, supervisor: Supervisor):
        self.supervisor = supervisor
        super().__init__(path, _Handler)


def serve(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    supervisor = Supervisor(state)
    fd = os.open(state / SECRET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(supervisor.secret)
    (state / PIDFILE).write_text(str(os.getpid()))
    path = socket_path(state)
    path.unlink(missing_ok=True)
    previous = os.umask(0o177)
    try:
        server = _Server(str(path), supervisor)
    finally:
        os.umask(previous)
    os.chmod(path, 0o600)
    server.serve_forever()


def call(state: Path, op: str, **fields: object) -> dict:
    secret = (state / SECRET).read_text().strip()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(str(socket_path(state)))
        client.sendall(json.dumps({"secret": secret, "op": op, **fields}).encode() + b"\n")
        reply = json.loads(client.makefile("rb").readline())
    if not reply["ok"]:
        raise Refused(reply["reason"])
    return reply


def _answers(state: Path) -> bool:
    try:
        call(state, "ping")
    except (OSError, ValueError, Refused):
        return False
    return True


def ensure(state: Path, timeout: float = 10.0) -> None:
    if _answers(state):
        return
    state.mkdir(parents=True, exist_ok=True)
    with (state / LOG).open("ab") as log:
        subprocess.Popen([sys.executable, "-m", "squad.supervisor", "serve", str(state)], cwd=ROOT,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.time() + timeout
    while not _answers(state):
        if time.time() > deadline:
            raise RuntimeError(f"supervisor did not answer in {state}")
        time.sleep(0.05)


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        serve(Path(sys.argv[2]))
