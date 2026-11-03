"""The hub page's push stream: versioned subjects, retained conversation windows and the events that carry them.

A subject is a value the page shows. Its version rises when its content changes and never falls, not even across a
restart: it is a clock reading, so a reader that holds a version never sees a lower one again. A reader holds the
versions it has painted and sends them back on reconnect, so no version repeats.

A conversation is a window: an append-only byte log of its turns, one JSON line each. An offset names a place in that
log. A reader holds the offset of the last byte it drew and sends it back on reconnect; an offset the window no longer
retains, or one from another epoch (a restart), is answered with the window start and a named gap, never with bytes the
reader cannot place.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse

PATH = "/api/stream"
CTYPE = "text/event-stream; charset=utf-8"
HEARTBEAT_SECONDS = 15.0    # a quiet stream writes this often, so a peer that left is found
TICK_SECONDS = 1.0          # how often the subjects are read
FIRST_POLL_SECONDS = 0.02   # how often a page that holds nothing looks for its first event
FIRST_EVENT_SECONDS = 1.0   # how long it looks that fast: a reader publishes the first event well within this
WINDOW_BYTES = 1 << 20      # the bytes a conversation window keeps; an older offset is a gap
WINDOWS_KEPT = 8            # the conversation windows held at once; an unwatched one is let go first
SID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")


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


class Window:
    """An append-only byte log. Offsets rise and never repeat; once the log passes `keep` bytes, its oldest whole lines go."""
    def __init__(self, keep=WINDOW_BYTES, start=0):
        self._buf = bytearray()
        self._keep = keep
        self.start = start
        self.end = start

    def append(self, data):
        self._buf += data
        self.end += len(data)
        if len(self._buf) > self._keep:
            nl = self._buf.find(b"\n", len(self._buf) - self._keep)
            cut = len(self._buf) if nl < 0 else nl + 1
            del self._buf[:cut]
            self.start += cut

    def since(self, offset):
        """(None, the bytes after offset) when offset is retained; (the window start, every retained byte) when it is not."""
        if offset is None or not self.start <= offset <= self.end:
            return self.start, bytes(self._buf)
        return None, bytes(self._buf[offset - self.start:])


class Transcripts:
    """Each conversation's turns in one window, read once per tick however many pages watch it. A turn is appended once."""
    def __init__(self, read, keep=WINDOW_BYTES, epoch=None):
        self._read = read   # sid -> the session's page (turns, next, activity), or None when it is not a live session
        self._keep = keep
        self.epoch = epoch if epoch is not None else time.time_ns()
        self._windows = {}
        self._floor = 0     # past every window let go, so a successor starts past every offset a reader may hold
        self._last_turn = {}   # sid -> the key of the last turn its window appended; a turn after it is new
        self._last = {}     # sid -> (tick, the page that tick read)
        self._watchers = {}
        self._lock = threading.Lock()

    def watch(self, sid):
        with self._lock: self._watchers[sid] = self._watchers.get(sid, 0) + 1

    def unwatch(self, sid):
        with self._lock:
            n = self._watchers.get(sid, 0) - 1
            if n > 0: self._watchers[sid] = n
            else: self._watchers.pop(sid, None)

    def refresh(self, sid, tick):
        with self._lock:
            held = self._last.get(sid)
            if held is not None and held[0] == tick: return held[1]
            page = self._read(sid)
            out = None if page is None else self._append(sid, page)
            self._last[sid] = (tick, out)
            return out

    def window(self, sid):
        with self._lock: return self._windows[sid]

    def pull(self, sid, epoch, offset):
        """(events, epoch, offset) for a reader that holds (epoch, offset): a gap first when its offset is not retained."""
        with self._lock:
            win = self._windows.get(sid)
            if win is None: return [], epoch, offset
            gap, data = win.since(offset if epoch == self.epoch else None)
            events = []
            if gap is not None:
                events.append(("gap", {"subject": "conversation", "sid": sid, "epoch": self.epoch, "start": gap}))
            if data:
                events.append(("conversation", {"sid": sid, "epoch": self.epoch, "at": win.end - len(data), "end": win.end,
                                                "lines": data.decode()}))
            return events, self.epoch, win.end

    def _append(self, sid, page):
        win = self._window(sid)
        turns = page["turns"]
        keys = [turn["k"] for turn in turns]
        last = self._last_turn.get(sid)
        for turn in turns[keys.index(last) + 1 if last in keys else 0:]:
            win.append((json.dumps(turn, sort_keys=True, separators=(",", ":")) + "\n").encode())
        if turns: self._last_turn[sid] = turns[-1]["k"]
        return {**page, "epoch": self.epoch, "end": win.end}

    def _window(self, sid):
        win = self._windows.get(sid)
        if win is None:
            if len(self._windows) >= WINDOWS_KEPT: self._let_go()
            win = self._windows[sid] = Window(self._keep, self._floor)
        return win

    def _let_go(self):
        unwatched = [sid for sid in self._windows if not self._watchers.get(sid, 0)]
        sid = (unwatched or list(self._windows))[0]
        self._floor = max(self._floor, self._windows.pop(sid).end)
        self._last.pop(sid, None)


def held_from(query):
    """The versions a reader holds, from `held=name:version,...`; a malformed entry is ignored."""
    out = {}
    for part in urllib.parse.parse_qs(query).get("held", [""])[0].split(","):
        name, _, v = part.partition(":")
        if name.isalnum() and v.isdigit():
            out[name] = int(v)
    return out


def conv_from(query):
    """The conversations a reader holds, from `conv=sid:epoch:offset,...`; a malformed entry is ignored."""
    out = {}
    for part in urllib.parse.parse_qs(query).get("conv", [""])[0].split(","):
        sid, _, rest = part.partition(":")
        epoch, _, off = rest.partition(":")
        if SID_RE.fullmatch(sid) and epoch.isdigit() and off.isdigit():
            out[sid] = (int(epoch), int(off))
    return out


def want_from(query):
    """The subjects whose payload a reader names, from `want=name,...`; a subject it does not name is pushed as its version."""
    return set(urllib.parse.parse_qs(query).get("want", [""])[0].split(",")) - {""}
