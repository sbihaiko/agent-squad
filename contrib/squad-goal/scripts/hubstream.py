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
FIRST_POLL_SECONDS = 0.02   # how often a page that holds nothing looks for its first event
FIRST_EVENT_SECONDS = 1.0   # how long it looks that fast: a reader publishes the first event well within this


def enabled():
    return os.environ.get("SQUAD_HUB_STREAM") == "1"


def tick():
    return int(time.monotonic() // TICK_SECONDS)


def frame(name, obj):
    return f"event: {name}\ndata: {json.dumps(obj, sort_keys=True)}\n\n".encode()


def event(name, version, body=None):
    data = {"v": version} if body is None else {"v": version, "data": body}
    return frame(name, data)


class Versions:
    def __init__(self, subjects):
        self._subjects = subjects   # {name: zero-argument read returning JSON-able content}
        self._digest = {}
        self._version = {}
        self._body = {}
        self._read_at = None
        self._lock = threading.Lock()

    def names(self):
        return list(self._subjects)

    def refresh(self, tick=None):
        """Every subject in turn, in the calling thread: a test seam, never the stream. A stream sends what its readers
        already hold, so one slow read cannot hold a page's first event."""
        if tick is not None:
            with self._lock:
                if tick == self._read_at: return
                self._read_at = tick
        for name in self.names():
            self.refresh_one(name)

    def refresh_one(self, name):
        """Read one subject outside the lock and publish it when its content changed. Each subject has its own reader, so
        a slow read of `sessions` never delays the `runs` event the page is waiting on. A read that answers None has no
        answer yet and publishes nothing, so a cold subject never paints an empty value."""
        content = self._subjects[name]()
        if content is None: return
        if isinstance(content, tuple) and len(content) == 2 and isinstance(content[1], str):
            content, digest = content   # the subject names its own digest: a body whose reader already costs it a hash is never serialized again to compare it (#1790). A string digest is the tell; a tuple whose second item is not a string is content, as a chart input that answers (read time, history key)
        else:
            digest = hashlib.sha1(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
        with self._lock:
            if name not in self._version or digest != self._digest[name]:
                self._version[name] = max(self._version.get(name, 0) + 1, time.time_ns())
                self._digest[name] = digest
                self._body[name] = content

    def newer(self, held):
        with self._lock:
            return {n: v for n, v in self._version.items() if v > held.get(n, 0)}

    def fresh(self, held):
        with self._lock:
            return {n: (v, self._body[n]) for n, v in self._version.items() if v > held.get(n, 0)}


def held_from(query):
    """The versions a reader holds, from `held=name:version,...`; a malformed entry is ignored."""
    out = {}
    for part in urllib.parse.parse_qs(query).get("held", [""])[0].split(","):
        name, _, v = part.partition(":")
        if name.isalnum() and v.isdigit():
            out[name] = int(v)
    return out


def want_from(query):
    """The subjects whose payload a reader names, from `want=name,...`; a subject it does not name is pushed as its version."""
    return set(urllib.parse.parse_qs(query).get("want", [""])[0].split(",")) - {""}
