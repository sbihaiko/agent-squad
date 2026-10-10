"""Pause and resume requests for a launched run.

A request is one JSON line in `control.jsonl` in the run folder: `{"action": "pause"|"resume", "nodes": [...]}`.
No `nodes` means the whole run. The file only grows. The engine folds it at each node entry and holds the nodes
it names. It sits beside the record, never in it, because the engine rewrites the record on every event
 and a second writer would lose a request. A line that is not a valid request is skipped.
"""
import json
import os
import time
from pathlib import Path

FILE = "control.jsonl"
ACTIONS = ("pause", "resume")


class Control:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / FILE

    def requests(self):
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("action") in ACTIONS and isinstance(r.get("nodes", []), list):
                out.append(r)
        return out

    def append(self, action, nodes=None, *, source="cli"):
        """Write one request. A single `O_APPEND` write keeps two writers from tearing each other's line."""
        if action not in ACTIONS:
            raise ValueError(f"action {action!r} is not one of {ACTIONS}")
        req = {"action": action, "at": time.time(), "source": source, **({"nodes": list(nodes)} if nodes else {})}
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(req) + "\n").encode())
        finally:
            os.close(fd)
        return req

    def state(self):
        """Fold the requests to `(whole, held, released)`: the run is held, the nodes held, the nodes released under a whole-run hold."""
        whole, held, released = False, set(), set()
        for r in self.requests():
            nodes = {n for n in r.get("nodes", []) if isinstance(n, str)}
            if r["action"] == "pause":
                if r.get("nodes"):
                    held |= nodes
                    released -= nodes
                else:
                    whole, released = True, set()
            elif r.get("nodes"):
                held -= nodes
                released |= nodes
            else:
                whole, held, released = False, set(), set()
        return whole, held, released

    def held(self, name):
        whole, held, released = self.state()
        return name in held or (whole and name not in released)

    def scope(self):
        """`run` when the whole run is held, else `nodes`."""
        return "run" if self.state()[0] else "nodes"
