"""The one audit line for a hosted terminal: one JSON line per start, attach, detach, stop and end, and per refusal.

A line carries the byte count of input, never the input itself. Input writes add no line of their own: the count of an
attach is the bytes typed under it, and the detach line carries that total.
"""
import json
import time
from pathlib import Path

FILE = "terminal-audit.log"
EVENTS = ("start", "attach", "detach", "stop", "end")


def record(state: Path, event: str, *, job: str, session: str, login: str, transport: str, client_id: str, input_bytes: int) -> None:
    if event not in EVENTS:
        raise ValueError(f"unknown audit event {event!r}")
    line = {"ts": time.time(), "event": event, "job": job, "session": session, "login": login,
            "transport": transport, "client_id": client_id, "input_bytes": int(input_bytes)}
    _append(state, line)


def refused(state: Path, *, reason: str, peer_pid: int | None = None, peer_uid: int | None = None) -> None:
    _append(state, {"ts": time.time(), "event": "refused", "reason": reason, "peer_pid": peer_pid, "peer_uid": peer_uid})


def _append(state: Path, line: dict) -> None:
    with (state / FILE).open("a") as audit:
        audit.write(json.dumps(line) + "\n")
