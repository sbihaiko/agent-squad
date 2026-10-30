#!/usr/bin/env python3
"""Send one cross-session message to a live Claude Code session (#834 probe).

Uses only the wire protocol in docs/design/session-wire-protocol.md:
no Claude client code. It never prints the session key.

Usage: probe-session-send.py <session-id> <text> [--from-name NAME]
Exit 0: the frames were written and the socket closed. Other: an error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import struct
import sys
import uuid
from pathlib import Path

DEADLINE_S = 5.0
SESSIONS = Path.home() / ".claude" / "sessions"
SOL_LOCAL, LOCAL_PEERPID = 0, 2  # macOS <sys/un.h>


class SendError(Exception):
    pass


def find_record(session_id: str) -> dict:
    for f in SESSIONS.glob("*.json"):
        if not f.stem.isdigit():
            continue
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("sessionId") == session_id:
            return rec
    raise SendError("no session record for that session id")


def key_path(pid: int, sock: str) -> Path:
    digest = hashlib.sha256(os.path.abspath(sock).encode()).hexdigest()
    return SESSIONS / f"{pid}.{digest}.key"


def read_token(pid: int, sock: str) -> str:
    p = key_path(pid, sock)
    try:
        if p.stat().st_mode & 0o077:
            raise SendError("key file is readable by others; refusing")
        token = json.loads(p.read_text())["peerToken"]
    except (OSError, ValueError, KeyError) as exc:
        raise SendError(f"key file unreadable ({type(exc).__name__})") from None
    if not (isinstance(token, str) and len(token) == 32):
        raise SendError("key file has no 32-char peerToken")
    return token


def peer_pid(s: socket.socket) -> int | None:
    if sys.platform != "darwin":
        try:
            raw = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            return struct.unpack("3i", raw)[0]
        except (OSError, AttributeError):
            return None
    try:
        return struct.unpack("i", s.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))[0]
    except OSError:
        return None


def send(session_id: str, text: str, from_name: str) -> str:
    rec = find_record(session_id)
    pid, sock = int(rec["pid"]), rec.get("messagingSocketPath") or ""
    if not sock:
        raise SendError("record has no messagingSocketPath")
    os.kill(pid, 0)  # raises if the recorded pid is gone
    token = read_token(pid, sock)
    msg_id = str(uuid.uuid4())
    frames = (
        json.dumps({"type": "auth", "token": token}) + "\n"
        + json.dumps({
            "type": "user",
            "message": {"role": "user", "content": text},
            "from_name": from_name,
            "msg_id": msg_id,
            "priority": "next",
        }) + "\n"
    ).encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(DEADLINE_S)
    try:
        s.connect(sock)
        got = peer_pid(s)
        if got != pid:
            raise SendError(f"socket peer pid {got} is not recorded pid {pid}; refusing")
        s.sendall(frames)
        s.shutdown(socket.SHUT_WR)
        s.recv(1)  # wait for the inbox to close its side
    except socket.timeout:
        raise SendError("deadline of 5 s passed") from None
    except OSError as exc:
        raise SendError(f"socket error ({exc.__class__.__name__}: {exc.strerror})") from None
    finally:
        s.close()
    return msg_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session_id")
    ap.add_argument("text")
    ap.add_argument("--from-name", default="squad-hub")
    a = ap.parse_args()
    try:
        msg_id = send(a.session_id, a.text, a.from_name)
    except (SendError, ProcessLookupError, PermissionError) as exc:
        print(f"send failed: {exc}", file=sys.stderr)
        return 1
    print(f"sent msg_id={msg_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
