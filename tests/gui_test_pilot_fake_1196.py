"""A fake of the app's full screen switch, sheets at Extra large and the B-closes-everything sweep for the pilot's FS and
TRAP batches (#1196): the behavior the manual script expects, so the converted steps run end to end with no real window.
A deviation is a constructor switch."""
from tests.gui_test_pilot_fake_1195 import ControllerFake

# surface suffix -> (screen the step opens, screen B must land on, focus the ring lands on)
SURFACES = {
    "library": ("play.library", "play.home", "play.home.open-rom"),
    "library-folders": ("play.library.folders", "play.library", "play.library.folders"),
    "browse-a-file": ("play.library.browse", "play.library", "play.library.browse"),
    "w-p4": ("play.pause", "play.game", None),
    "save-states": ("play.save-states", "play.pause", "play.pause.save-states"),
    "slot-grid": ("play.save-states.slot-grid", "play.save-states", "play.save-states.slot-grid"),
    "pack-picker": ("play.pack", "play.pause", "play.pause.pack"),
    "pack-detail": ("play.pack.detail", "play.pack", "play.pack.detail"),
    "enhancements": ("play.enhancements", "play.pause", "play.pause.enhancements"),
    "cheats": ("play.cheats", "play.pause", "play.pause.cheats"),
    "add-a-code": ("play.cheats.add-code", "play.cheats", "play.cheats.add-code"),
    "settings-display": ("play.settings.tab-display", "play.pause", "play.pause.settings"),
    "settings-look": ("play.settings.tab-look", "play.pause", "play.pause.settings"),
    "settings-audio": ("play.settings.tab-audio", "play.pause", "play.pause.settings"),
    "settings-controls": ("play.settings.tab-controls", "play.pause", "play.pause.settings"),
    "settings-system": ("play.settings.tab-system", "play.pause", "play.pause.settings"),
    "controller-sheet": ("play.controller", "play.pause", "play.pause.settings"),
    "quit-confirm": ("play.pause.quit-confirm", "play.pause", "play.pause.quit-game"),
    "select-rom-sheet": ("play.select-rom", "play.library", "play.library.tile"),
    "bios-sheet": ("play.bios", "play.library", "play.library.tile"),
    "options-audio": ("classic.options.audio", "play.settings", "play.settings.more-in-options"),
    "options-controller": ("classic.options.controller", "play.controller", "play.controller.row-first"),
    "options-look-pixels": ("classic.options.look-pixels", "play.settings", "play.settings.look-pixels"),
}
TRAP_SURFACES = list(SURFACES)
BY_SCREEN = {screen: (parent, focus) for screen, parent, focus in SURFACES.values()}
TABS = {"play.settings.tab-" + t: t for t in ("display", "look", "audio", "controls", "system")}


class TrapFake(ControllerFake):
    def __init__(self, out_dir, stuck=(), native_dialog_on=(), exit_drops_sheet=False, **rest):
        super().__init__(out_dir, **rest)
        self.stuck, self.native_dialog_on, self.exit_drops_sheet = set(stuck), set(native_dialog_on), exit_drops_sheet
        self.opened = None

    def _goal(self, what, want):
        if what == "ui.screen" and want in BY_SCREEN or want in TABS:
            self.opened = want
            self.screen, self.focus = ("play.settings", want) if want in TABS else (want, None)
            self.dialogs = [want] if want in self.native_dialog_on else []
        elif want == "play.settings.fullscreen-switch" or want == "play.settings.exit-fullscreen":
            self.screen, self.focus, self.tab = "play.settings", want, "play.settings.tab-display"
        elif want.startswith("play.library."):
            self.screen, self.focus = "play.library", want
        else:
            super()._goal(what, want)

    def inject(self, name, args):
        if name == "pad.press" and args["button"] == "B" and self.opened and self.opened in BY_SCREEN \
                and (self.screen == self.opened or self.opened in TABS and self.screen == "play.settings"):
            self.log.append((name, args))
            if self.opened not in self.stuck:
                self.screen, self.focus = BY_SCREEN[self.opened]
                self.opened = None
            return
        return super().inject(name, args)

    def _p_a(self):
        if self.screen == "play.settings" and self.focus == "play.settings.exit-fullscreen":
            self.focus = "play.settings.done"
            if self.exit_drops_sheet:
                self.screen = "play.pause"
        else:
            super()._p_a()
