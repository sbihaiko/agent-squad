"""The hub page's push stream: versioned subjects and the events that carry them.

A subject is a value the page shows. Its version rises when its content changes and never falls, not even across a
restart: it is a clock reading, so a reader that holds a version never sees a lower one again. A reader holds the
versions it has painted and sends them back on reconnect, so no version repeats.
"""
import hashlib
import json
import os
import threading
import time
import urllib.parse

PATH = "/api/stream"
CTYPE = "text/event-stream; charset=utf-8"
HEARTBEAT_SECONDS = 15.0    # a quiet stream writes this often, so a peer that left is found
TICK_SECONDS = 1.0          # how often the subjects are read


def enabled():
    return os.environ.get("SQUAD_HUB_STREAM") == "1"


class Versions:
    def __init__(self, subjects):
        self._subjects = subjects   # {name: zero-argument read returning JSON-able content}
        self._digest = {}
        self._version = {}
        self._lock = threading.Lock()

    def refresh(self):
        with self._lock:
            for name, read in self._subjects.items():
                digest = hashlib.sha1(json.dumps(read(), sort_keys=True, default=str).encode()).hexdigest()
                if name not in self._version or digest != self._digest[name]:
                    self._version[name] = max(self._version.get(name, 0) + 1, time.time_ns())
                    self._digest[name] = digest

    def newer(self, held):
        with self._lock:
            return {n: v for n, v in self._version.items() if v > held.get(n, 0)}


def held_from(query):
    """The versions a reader holds, from `held=name:version,...`; a malformed entry is ignored."""
    out = {}
    for part in urllib.parse.parse_qs(query).get("held", [""])[0].split(","):
        name, _, v = part.partition(":")
        if name.isalnum() and v.isdigit():
            out[name] = int(v)
    return out


def event(name, version):
    return f"event: {name}\ndata: {json.dumps({'v': version})}\n\n".encode()
