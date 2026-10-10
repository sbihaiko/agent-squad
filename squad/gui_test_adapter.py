"""A fake gui-test/1 target adapter: the reference the shared contract suite runs against.

A press of A on the home screen opens the library after three ticks; wait spends ticks, never host time.
"""
import hashlib
import struct
import zlib
from pathlib import Path

from squad.gui_test_checks import UNKNOWN, parse

SCREENS = {"play.home", "play.library"}
CONTROLS = {"play.home.open-rom"}
OPEN_AFTER = 3


def _png(seed):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    row = b"\x00" + bytes(hashlib.sha256(seed.encode()).digest()[:3])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row)) + chunk(b"IEND", b""))


class FakeAdapter:
    def __init__(self, out_dir):
        self.out_dir, self.log, self.captured_screens = Path(out_dir), [], []
        self.screen, self.focus, self.tick, self._opens_at = "play.home", "play.home.open-rom", 0, None
        self.ram, self.log_lines = {}, []

    def capabilities(self):
        return {"actions": ["pad.press"], "checks": ["ui.focused", "ui.screen"], "input_families": ["ticks"],
                "variants": {"window.mode": ["windowed", "fullscreen"]}}

    def launch(self, fixtures):
        self.screen, self.focus, self.tick, self._opens_at = "play.home", "play.home.open-rom", 0, None
        return self

    def inject(self, name, args):
        self.log.append((name, args))
        if name == "pad.press" and args.get("button") == "A" and self.screen == "play.home":
            self._opens_at = self.tick + OPEN_AFTER

    def _advance(self):
        self.tick += 1
        if self._opens_at is not None and self.tick >= self._opens_at:
            self.screen, self._opens_at = "play.library", None

    def wait(self, condition, timeout):
        for _ in range(timeout + 1):
            if condition is None or self._holds(condition):
                return "met"
            self._advance()
        return "timeout"

    def advance(self):
        self._advance()

    def log_text(self):
        return "\n".join(self.log_lines)

    def observe(self, name, args):
        if name == "emu.ram":
            return self.ram.get(int(args["addr"], 0), 0)
        if args["is"] not in SCREENS | CONTROLS:
            return UNKNOWN
        return {"ui.screen": self.screen, "ui.focused": self.focus}[name]

    def _holds(self, cond):
        name, args = parse(cond)
        got = self.observe(name, args)
        return got == int(args["is"], 0) if name == "emu.ram" else got == args["is"]

    def check(self, name, args):
        got = self.observe(name, args)
        return got if got == UNKNOWN else got == args["is"]

    def capture(self):
        self.captured_screens.append(self.screen)
        path = self.out_dir / f"capture-{len(self.captured_screens)}.png"
        data = _png(self.screen + str(len(self.captured_screens)))
        path.write_bytes(data)
        return {"path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def teardown(self):
        return []
