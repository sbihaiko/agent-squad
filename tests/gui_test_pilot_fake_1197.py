"""A fake of the app's complete pad-only trip for the pilot's two-pad, feel and JOURNEY batches (#1197): home, library
search, play, the pause overlay, Settings, Save states and Quit, with the full screen switch kept inside the sheet. The
behavior the manual script expects, so the converted steps run end to end with no real window. A deviation is a
constructor switch."""
from tests.gui_test_pilot_fake_1196 import TrapFake

SWITCH, SIZE = "play.settings.fullscreen-switch", "play.settings.interface-size"


class JourneyFake(TrapFake):
    def __init__(self, out_dir, quit_stays=False, switch_drops_sheet=False, **rest):
        super().__init__(out_dir, **rest)
        self.quit_stays, self.switch_drops_sheet = quit_stays, switch_drops_sheet
        self._reset()

    def _reset(self):
        self.size, self.slot_saved, self.full = "standard", False, False

    def launch(self, fixtures):
        self._reset()
        self.keyboard, self.confirm = False, False
        return super().launch(fixtures)

    def inject(self, name, args):
        if name == "pad.release":
            self.log.append((name, args))
            return
        return super().inject(name, args)

    def _p_a(self):
        if self.screen == "play.pause" and self.confirm and self.quit_stays:
            self.confirm = False
        elif self.screen == "play.pause" and self.focus == "play.pause.resume" and not self.confirm:
            self.screen, self.focus = "play.game", None
        elif self.screen == "play.save-states" and not self.keyboard:
            self.slot_saved = True
        elif self.screen == "play.settings" and self.focus == SWITCH:
            self.full = True
            if self.switch_drops_sheet:
                self.screen, self.focus = "play.pause", "play.pause.settings"
        else:
            super()._p_a()

    def _p_up(self):
        if self.screen == "play.settings" and self.focus == SIZE:
            self.focus = SWITCH
        else:
            super()._p_up()

    def _p_down(self):
        if self.screen == "play.settings" and self.focus == SWITCH:
            self.focus = SIZE
        else:
            super()._p_down()

    def _p_right(self):
        if self.screen == "play.settings" and self.focus == SIZE:
            self.size = "large"
        else:
            super()._p_right()

    def observe(self, name, args):
        if name == "ui.visible":
            shown = {"play.settings.interface-size-large": self.size == "large",
                     "play.save-states.slot-saved": self.slot_saved}
            if args["is"] in shown:
                return args["is"] if shown[args["is"]] else ""
        return super().observe(name, args)
