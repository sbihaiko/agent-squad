"""A stand-in for the emulator that speaks the GUI test hook protocol (ADR-0272) over its Unix socket.

It starts on the Home with Open a ROM focused; a press of A opens the library after three ticks, and the
keyboard's Enter brings it back (the two paths a #1780 run drives). Ticks are real time (50 ms), like the
application's pad bridge. FAKE_HOOK_MODE=crash makes it exit on the first inject.

Hook version 2 (#1779): the chord and the release are answered without moving anything, and `nav.goal` is a read
of the state and the six pad navigation moves - it injects nothing. FAKE_HOOK_VERBS names a file every action the
application was asked for is appended to, one JSON object per line, so a test reads back what the adapter sent.
"""
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import zlib

state = {"tick": 0, "screen": "play.home", "focus": "play.home.open-rom", "opens_at": None}
CONTROLS = ["play.home", "play.home.open-rom", "play.library.back"]
# PadNavAction, the six navigation presses the application itself hands a goal-driven run (TestHookProtocol.NavGoal).
NAVIGATION = ["Up", "Down", "Left", "Right", "Confirm", "Back"]
ACTIONS = ["pad.press", "pad.release", "pad.chord", "key.press", "nav.goal"]


def png(w, h, seed):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + bytes([seed % 256, 0, 0]) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def ticker():
    while True:
        time.sleep(0.05)
        state["tick"] += 1
        if state["opens_at"] is not None and state["tick"] >= state["opens_at"]:
            state["screen"], state["focus"], state["opens_at"] = "play.library", None, None


def handle(req, token):
    if req.get("token") != token:
        return {"ok": False, "error": "unauthorized"}
    op = req.get("op")
    if op == "hello":
        # #1779: version 2 names the actions this hook answers, so a run that drives a verb it cannot answer is
        # refused before the step that wrote it.
        return {"ok": True, "hook": 2, "ops": ["hello", "state", "inject", "capture", "quit"],
                "namespaces": ["pad", "key", "nav", "ui"], "actions": list(ACTIONS)}
    if op == "state":
        return {"ok": True, "screen": state["screen"], "dialogs": [], "focus": state["focus"], "tick": state["tick"], "frames": 0,
                # #1282: what a player judges by eye, from the application's own UI state.
                "ring": {"visible": True, "target": state["focus"]},
                "footer": ["confirm", "back"] if state["screen"] == "play.library" else ["confirm"],
                "surface": "play.library" if state["screen"] == "play.library" else "none",
                "paused": False,
                "lamps": {"shown": True, "ports": [{"port": p, "lit": p == 1, "name": ""} for p in (1, 2, 3, 4)]},
                "items": ["play.library.tile.Contra (USA).nes"] if state["screen"] == "play.library" else [],
                "haptics": [{"pad": 1, "count": 2}],
                "controls": [{"id": c, "enabled": True, "visible": state["screen"] == ("play.library" if c == "play.library.back" else "play.home"), "focused": c == state["focus"]} for c in CONTROLS],
                "options": [], "window": {"mode": "windowed", "size": [64, 48]}}
    if op == "inject":
        if os.environ.get("FAKE_HOOK_MODE") == "crash":
            os._exit(139)
        action = req.get("action")
        asked = os.environ.get("FAKE_HOOK_VERBS")
        if asked:
            with open(asked, "a", encoding="utf-8") as f:
                f.write(json.dumps({"action": action, "args": req.get("args") or {}}) + "\n")
        if action not in ACTIONS:
            return {"ok": False, "error": "unknown action " + str(action)}
        if action == "nav.goal":
            # #1779: a read, never a step - the goal it names is not touched, and the six moves it hands are the
            # pad's own navigation presses in the duration family this state's clock uses.
            return {"ok": True, "goal": req["args"].get("goal", ""), "state": dict(state, controls=[]),
                    "family": "ticks",
                    "moves": [{"name": "pad.press", "args": {"pad": 0, "button": b, "ticks": 1}} for b in NAVIGATION]}
        if action == "key.press" and req["args"].get("key") == "Enter" and state["screen"] == "play.library":
            state["screen"], state["focus"], state["opens_at"] = "play.home", "play.home.open-rom", None
        # Confirm is the confirm press the application's own navigation names (PadNavAction.Confirm), the same
        # press as A on the pad: the six moves `nav.goal` hands are navigation names, and one of them has to open
        # the library for a goal-driven run to reach it.
        if action == "pad.press" and req["args"].get("button") in ("A", "Confirm") and state["screen"] == "play.home":
            state["opens_at"] = state["tick"] + 3
        return {"ok": True}
    if op == "capture":
        data = png(64, 48, state["tick"])
        with open(req["path"], "wb") as f:
            f.write(data)
        return {"ok": True, "path": req["path"], "size": [64, 48], "sha256": hashlib.sha256(data).hexdigest()}
    if op == "quit":
        threading.Timer(0.1, lambda: os._exit(0)).start()
        return {"ok": True}
    return {"ok": False, "error": "unknown op " + str(op)}


def main(argv):
    endpoint = next(a.split("=", 1)[1] for a in argv if a.startswith("--test-hook="))
    token = next(a.split("=", 1)[1] for a in argv if a.startswith("--test-hook-token="))
    if os.path.exists(endpoint):
        os.unlink(endpoint)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(endpoint)
    srv.listen(4)
    print("test hook listening on " + endpoint, file=sys.stderr, flush=True)
    threading.Thread(target=ticker, daemon=True).start()
    while True:
        conn, _ = srv.accept()
        with conn, conn.makefile("rw", encoding="utf-8") as f:
            for line in f:
                f.write(json.dumps({"id": json.loads(line).get("id")} | handle(json.loads(line), token)) + "\n")
                f.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
