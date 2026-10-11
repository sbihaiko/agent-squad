"""A fake of the app's Pause overlay and Settings sheet for the pilot's P4 and SET batches (#1194): the behavior the
manual script expects, so the converted steps run end to end with no real window. A deviation is a constructor switch."""
from squad.gui_test_checks import _same, parse
from tests.gui_test_pilot_fake import PilotFake

ROWS = ["resume", "save-states", "pack", "enhancements", "cheats", "settings", "quit-game"]
PAUSE = ["play.pause." + r for r in ROWS]
TABS = ["play.settings.tab-" + t for t in ("display", "look", "audio", "controls", "system")]
SHEETS = {"save-states": "play.save-states", "pack": "play.pack", "enhancements": "play.enhancements",
          "cheats": "play.cheats", "settings": "play.settings"}
CHROME = {"play.settings.done", "play.settings.more-in-options", "play.settings.exit-fullscreen"}
STEPPERS = {"play.settings.interface-size", "play.settings.rumble", "play.settings.deadzone"}


class PauseSettingsFake(PilotFake):
    def __init__(self, out_dir, b_resumes=True, tabs_wrap=False, done_unreachable=False):
        super().__init__(out_dir)
        self.b_resumes, self.tabs_wrap, self.done_unreachable = b_resumes, tabs_wrap, done_unreachable
        self.screen, self.focus, self.confirm, self.sheet_from = "play.home", "play.home.open-rom", False, None
        self.tab = TABS[0]

    def capabilities(self):
        caps = super().capabilities()
        return {**caps, "checks": caps["checks"] + []}

    def _goal(self, what, want):
        if what == "ui.screen":
            self.screen, self.focus = want, {"play.game": None, "play.pause": PAUSE[0], "play.settings": TABS[0],
                                             "play.home": "play.home.open-rom"}.get(want, self.focus)
            return
        self.focus = want
        self.screen = ("play.pause" if want.startswith("play.pause.") else
                       "play.settings" if want.startswith("play.settings.") else
                       "play.cheats" if want.startswith("play.cheats.") else self.screen)
        if want in TABS:
            self.tab = want

    def _page(self):
        return self.focus.startswith("play.settings.") and self.focus not in TABS and self.focus not in CHROME

    def inject(self, name, args):
        self.log.append((name, args))
        if name == "nav.goal":
            return self._goal(*args["goal"].split(" == "))
        if name == "pad.chord":
            if self.screen == "play.game":
                self.screen, self.focus = "play.pause", PAUSE[0]
            elif self.screen == "play.pause":
                self.screen, self.focus = "play.game", None
            return
        button = args["button"]
        handler = getattr(self, "_p_" + button.lower(), None)
        if self.screen in ("play.pause", "play.settings", "play.cheats", "play.save-states", "play.pack",
                           "play.enhancements") or self.keyboard:
            return handler() if handler else None
        return super().inject(name, args)

    def _p_down(self):
        if self.screen == "play.pause":
            self._step(PAUSE, 1)
        elif self.screen == "play.settings" and self.focus in TABS:
            self.focus = "play.settings.interface-size"
        elif self.screen == "play.settings" and self._page() and not self.done_unreachable:
            self.focus = "play.settings.done"

    def _p_up(self):
        if self.screen == "play.pause":
            self._step(PAUSE, -1)
        elif self.screen == "play.settings" and self.focus == "play.settings.done":
            audio_or_controls = self.tab in TABS[2:4]
            self.focus = "play.settings.more-in-options" if audio_or_controls else "play.settings.interface-size"
        elif self.screen == "play.settings" and self.focus == "play.settings.more-in-options":
            self.focus = "play.settings.interface-size"

    def _p_left(self):
        if self.screen == "play.settings" and self.focus in TABS:
            self.focus = self.tab = TABS[max(0, TABS.index(self.focus) - 1)]
        elif self.screen == "play.settings" and self.focus == "play.settings.done":
            self.focus = "play.settings.exit-fullscreen" if self.mode == "fullscreen" else self.focus

    def _p_right(self):
        if self.screen == "play.settings" and self.focus in TABS:
            i = TABS.index(self.focus) + 1
            self.focus = self.tab = TABS[i % len(TABS) if self.tabs_wrap else min(i, len(TABS) - 1)]

    def _p_a(self):
        if self.screen == "play.pause" and self.confirm:
            self.confirm, self.screen, self.focus = False, "play.home", "play.home.open-rom"
        elif self.screen == "play.pause" and self.focus == PAUSE[-1]:
            self.confirm = True
        elif self.screen == "play.pause" and self.focus.split(".")[-1] in SHEETS:
            self.screen = SHEETS[self.focus.split(".")[-1]]
            self.focus = TABS[0] if self.screen == "play.settings" else None
        elif self.screen == "play.cheats" and self.focus == "play.cheats.add-code":
            self.keyboard = True

    def _p_b(self):
        if self.keyboard:
            self.keyboard = False
        elif self.confirm:
            self.confirm = False
        elif self.screen == "play.pause":
            if self.b_resumes:
                self.screen, self.focus = "play.game", None
        elif self.screen in SHEETS.values():
            row = {v: k for k, v in SHEETS.items()}[self.screen]
            self.screen, self.focus = "play.pause", "play.pause." + row

    def observe(self, name, args):
        if name == "ui.focused" and "within" in args:
            host = args["within"]
            inside = self._page() if host == "play.settings.page" else str(self.focus).startswith(host)
            return True if inside else False
        if name == "ui.visible":
            shown = {"play.keyboard": self.keyboard, "play.pause.quit-confirm": self.confirm,
                     "play.pack": self.screen == "play.pack"}
            if args["is"] in PAUSE:
                return args["is"] if self.screen == "play.pause" else ""
            return args["is"] if shown.get(args["is"], False) else ""
        return super().observe(name, args)

    def _holds(self, cond):
        name, args = parse(cond)
        return _same(args["is"], self.observe(name, args))
