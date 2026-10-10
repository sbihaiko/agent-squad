"""A fake of the app's Controller sheet for the pilot's CTL, LAMP and LOSS batches (#1195): the behavior the manual
script expects, so the converted steps run end to end with no real window. A deviation is a constructor switch."""
from tests.gui_test_pilot_fake_1194 import PauseSettingsFake

ROWS = ["play.controller.row-first", "play.controller.row-second", "play.controller.done"]


class ControllerFake(PauseSettingsFake):
    def __init__(self, out_dir, sheet_opens=True, b_cancels_capture=True, **rest):
        super().__init__(out_dir, **rest)
        self.sheet_opens, self.b_cancels_capture, self.capturing = sheet_opens, b_cancels_capture, False

    def _goal(self, what, want):
        if want.startswith("play.controller."):
            self.screen, self.focus = "play.controller", want
        elif want == "play.settings.more-in-options":
            self.screen, self.focus, self.tab = "play.settings", want, "play.settings.tab-controls"
        else:
            super()._goal(what, want)

    def inject(self, name, args):
        if self.screen == "play.controller" and name == "pad.press":
            self.log.append((name, args))
            handler = getattr(self, "_c_" + args["button"].lower(), None)
            return handler() if handler else None
        return super().inject(name, args)

    def _p_a(self):
        if self.screen == "play.settings" and self.focus == "play.settings.more-in-options" and self.sheet_opens:
            self.screen, self.focus = "play.controller", ROWS[0]
        else:
            super()._p_a()

    def _c_down(self):
        if self.focus in ROWS[:-1]:
            self.focus = ROWS[ROWS.index(self.focus) + 1]

    def _c_a(self):
        if self.focus == "play.controller.done":
            self.screen, self.focus = "play.pause", "play.pause.settings"
        else:
            self.capturing = True

    def _c_b(self):
        if self.capturing and self.b_cancels_capture:
            self.capturing = False
        else:
            self.capturing, self.screen, self.focus = False, "play.pause", "play.pause.settings"

    def observe(self, name, args):
        if name == "ui.visible" and args["is"] == "play.controller.capture-armed":
            return args["is"] if self.capturing else ""
        return super().observe(name, args)
