"""The phone's one message to a live session (#834). The hub writes the session wire protocol itself
(docs/design/session-wire-protocol.md) and records each send in a durable reservation before it writes a byte.
The session registry and the key read go through sessions.py; nothing here prints or logs the key.
"""
import hashlib, json, os, pathlib, re, socket, struct, sys, time
import sessions

MAX_CHARS = 4000
DEADLINE_S = 5.0
KEEP_S = 24 * 3600
CLIENT_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
FROM_NAME = "squad-hub"
STATES = ("sent", "uncertain", "refused")   # owner, #834: the protocol returns nothing for a hold, so no held, delivered or expired
SOL_LOCAL, LOCAL_PEERPID = 0, 2   # macOS <sys/un.h>


class Refused(Exception):
    """A send that wrote nothing; the message names the cause and no secret."""


class Uncertain(Exception):
    """A send whose frames may have reached the inbox."""


def _open():
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


def _dir(root, profile):
    return pathlib.Path(root) / "session-sends" / profile


def _name(sid, cid):
    return hashlib.sha256(f"{sid}\n{cid}".encode()).hexdigest() + ".json"


def _write(path, body):
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(body)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _reserve(d, sid, cid):
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / _name(sid, cid), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"state": "reserved"})); f.flush(); os.fsync(f.fileno())


def _finish(d, sid, cid, state, reason, cwd):
    _write(d / _name(sid, cid), {"state": state, "reason": reason, "cwd": cwd})


def _stored(path):
    try:
        body = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"state": "reserved"}
    return body if isinstance(body, dict) else {"state": "reserved"}


def _purge(d):
    if not d.is_dir():
        return
    cutoff = time.time() - KEEP_S
    for f in d.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _check(sid, text, cid):
    if not isinstance(sid, str) or not sessions.SID.fullmatch(sid):
        raise Refused("bad session id")
    if not isinstance(text, str) or not text.strip():
        raise Refused("empty message")
    if len(text) > MAX_CHARS:
        raise Refused(f"message over {MAX_CHARS} characters")
    if not isinstance(cid, str) or not CLIENT_ID.fullmatch(cid):
        raise Refused("bad client message id")


def _key_token(pid, sock):
    name = f"{pid}.{hashlib.sha256(os.path.abspath(sock).encode()).hexdigest()}.key"
    try:
        if os.stat(sessions.claude_home() / "sessions" / name, follow_symlinks=False).st_mode & 0o077:
            raise Refused("key file is readable by others")
        token = json.loads(sessions._read_under_home(["sessions", name])[0])["peerToken"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("key file unreadable") from None
    if not (isinstance(token, str) and len(token) == 32):
        raise Refused("key file has no usable token")
    return token


def peer_pid(s):
    if sys.platform != "darwin":
        try:
            return struct.unpack("3i", s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
        except (OSError, AttributeError):
            return None
    try:
        return struct.unpack("i", s.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))[0]
    except OSError:
        return None


def live_session(profile, sid, profiles_of=sessions.profiles_of):
    """The live session of this profile with that id, or None: the one read the hub's `no session` refusal and the
    delivery both use, so a message and its attachment answer for the same registry (#1151)."""
    for rec, _ in sessions.live_sessions(profile, profiles_of):
        if rec["sessionId"] == sid:
            return rec
    return None


def _deliver(profile, sid, text, cid, profiles_of):
    rec = live_session(profile, sid, profiles_of)
    if rec is None:
        raise Refused("no live session of this profile has that id")
    pid, sock = int(rec["pid"]), rec["socket"]
    token = _key_token(pid, sock)
    frames = (json.dumps({"type": "auth", "token": token}) + "\n"
              + json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "from_name": FROM_NAME, "msg_id": cid, "priority": "next",
                            **({"from_mode": m} if (m := sessions.permission_mode(rec["cwd"], sid, pid)) else {})}) + "\n").encode()
    s = _open()
    s.settimeout(DEADLINE_S)
    try:
        try:
            s.connect(sock)
        except OSError:
            raise Refused("no inbox answered") from None
        if peer_pid(s) != pid:
            raise Refused("the socket peer is not the recorded session")
        try:
            s.sendall(frames)
            s.shutdown(socket.SHUT_WR)
            s.recv(1)
        except socket.timeout:
            raise Uncertain("deadline of 5 s passed") from None
        except OSError as exc:
            raise Uncertain(f"socket error ({type(exc).__name__})") from None
    finally:
        s.close()
    return rec["cwd"]


def send(profile, sid, text, cid, profiles_of=sessions.profiles_of, root=None):
    try:
        _check(sid, text, cid)
    except Refused as exc:
        return {"status": "refused", "reason": str(exc)}
    d = _dir(root, profile)
    try:
        _purge(d)
        _reserve(d, sid, cid)
    except FileExistsError:
        return _recorded(d, sid, cid)
    except OSError:
        return {"status": "refused", "reason": "the send could not be recorded"}
    state, reason, cwd = "sent", "", ""
    try:
        cwd = _deliver(profile, sid, text, cid, profiles_of)
    except Refused as exc:
        state, reason = "refused", str(exc)
    except Uncertain as exc:
        state, reason = "uncertain", str(exc)
    _finish(d, sid, cid, state, reason, cwd)
    return {"status": state, "reason": reason}


def _recorded(d, sid, cid):
    body = _stored(d / _name(sid, cid)) or {}
    state = body.get("state")
    return {"status": state if state in ("sent", "refused") else "uncertain", "reason": ""}


def status(profile, sid, cid, profiles_of=sessions.profiles_of, root=None):
    if not sessions.SID.fullmatch(sid or "") or not CLIENT_ID.fullmatch(cid or ""):
        return {"status": "unknown"}
    body = _stored(_dir(root, profile) / _name(sid, cid))
    if body is None:
        return {"status": "unknown"}
    if body.get("state") != "sent":
        return {"status": "refused" if body.get("state") == "refused" else "uncertain"}
    return {"status": "sent"}
