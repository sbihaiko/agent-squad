"""A fake of the app's Play door for the pilot's Library, Favorites and Game batches (#1193): the behavior the manual
script expects, so the converted steps run end to end with no real window. A deviation is a constructor switch."""
from squad.gui_test_adapter import FakeAdapter
from squad.gui_test_checks import UNKNOWN, _same, parse

HEADER = ["play.library.search", "play.library.folders", "play.library.browse", "play.library.back"]
AFTER_TILE_A = {"rom": "play.game", "zip": "play.select-rom", "fds": "play.bios"}


class PilotFake(FakeAdapter):
    def __init__(self, out_dir, tile="rom", favorites=0, y_focuses_search=True):
        super().__init__(out_dir)
        self.tile, self.favorites, self.y_focuses_search = tile, favorites, y_focuses_search
        self.keyboard, self.mode, self.dialogs = False, None, []

    def capabilities(self):
        return {"actions": ["pad.press", "pad.release", "pad.chord", "nav.goal"],
                "checks": ["ui.screen", "ui.focused", "ui.visible", "ui.dialogs"], "input_families": ["ticks"],
                "variants": {"window.mode": ["windowed", "fullscreen"]}}

    def set_variant(self, axis, value):
        self.mode = value

    def _home_order(self):
        return ["play.home.continue", "play.home.favorite", "play.home.recent", "play.home.open-rom"]

    def inject(self, name, args):
        self.log.append((name, args))
        if name == "nav.goal":
            return self._goal(*args["goal"].split(" == "))
        if name == "pad.press":
            getattr(self, "_" + args["button"].lower(), lambda: None)()

    def _goal(self, what, want):
        if what == "ui.screen":
            self.screen, self.focus = want, "play.library.tile" if want == "play.library" else self.focus
        else:
            self.screen, self.focus = "play.home", want

    def _step(self, order, delta):
        if self.focus in order:
            self.focus = order[max(0, min(len(order) - 1, order.index(self.focus) + delta))]

    def _a(self):
        if self.screen == "play.home" and self.focus == "play.home.open-rom":
            self.screen, self.focus = "play.library", "play.library.tile"
        elif self.screen == "play.library" and self.focus == "play.library.tile":
            self.screen, self.focus = AFTER_TILE_A[self.tile], None
        elif self.screen == "play.library" and self.focus == "play.library.search":
            self.keyboard = True
        elif self.screen == "play.library" and self.focus in HEADER[1:3]:
            self.screen = self.focus.replace("library.", "library.", 1)
        elif self.screen == "play.select-rom":
            self.screen, self.focus = "play.game", None

    def _b(self):
        if self.keyboard:
            self.keyboard, self.focus = False, "play.library.search"
        elif self.screen in ("play.library.folders", "play.library.browse", "play.select-rom", "play.bios"):
            self.focus = self.screen if self.screen.startswith("play.library.") else "play.library.tile"
            self.screen = "play.library"
        elif self.screen == "play.library":
            self.screen, self.focus = "play.home", "play.home.open-rom"

    def _y(self):
        if self.screen == "play.library" and self.y_focuses_search:
            self.focus = "play.library.search"

    def _x(self):
        if self.focus == "play.library.tile":
            self.favorites = 0 if self.favorites else 1
        elif self.focus == "play.home.favorite":
            self.favorites = 0
        elif self.focus == "play.home.continue":
            self.favorites = 1

    def _up(self):
        if self.screen == "play.library" and self.focus == "play.library.tile":
            self.focus = HEADER[0]
        elif self.screen == "play.home":
            self._step(self._home_order(), -1)

    def _down(self):
        if self.screen == "play.library" and self.focus in HEADER:
            self.focus = "play.library.tile"
        elif self.screen == "play.home":
            self._step(self._home_order(), 1)

    def _right(self):
        if self.screen == "play.library" and self.focus in HEADER:
            self._step(HEADER, 1)

    def observe(self, name, args):
        if name == "ui.dialogs":
            return self.dialogs
        if name == "ui.visible":
            shown = {"play.keyboard": self.keyboard, "play.home.favorites": self.favorites > 0}
            return args["is"] if shown.get(args["is"], False) else ""
        return {"ui.screen": self.screen, "ui.focused": self.focus}.get(name, UNKNOWN)

    def _holds(self, cond):
        name, args = parse(cond)
        return _same(args["is"], self.observe(name, args))

    def check(self, name, args):
        got = self.observe(name, args)
        return got if got == UNKNOWN else _same(args["is"], got)
