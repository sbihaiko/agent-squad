"""A fake of the composition editor's tkinter view for runbook section 1 (#1198): a second target adapter, driven by
pointer clicks and a key, whose work is judged by files under the pack folder (`out_dir`) and by its log. A deviation
is a constructor switch."""
from squad.gui_test_adapter import FakeAdapter

FLOW = {"compose.cell.obj-0": ("compose.seeded", "compose.cell.obj-0"),
        "compose.neighbor.strongest": ("compose.locked", None),
        "compose.band.sprite-0": ("compose.banded", None)}


class ComposeEditorFake(FakeAdapter):
    def __init__(self, out_dir, save_stays_in_auto=False):
        super().__init__(out_dir)
        self.save_stays_in_auto = save_stays_in_auto

    def capabilities(self):
        return {"actions": ["pointer.click", "key.press"], "checks": ["ui.screen", "ui.focused", "fs.exists",
                "log.contains"], "input_families": ["ticks"], "variants": {}}

    def launch(self, fixtures):
        self.screen, self.focus, self.tick, self.log, self.log_lines = "compose.open", None, 0, [], []
        return self

    def inject(self, name, args):
        self.log.append((name, args))
        target = args.get("target")
        if name == "pointer.click" and target in FLOW:
            self.screen, self.focus = FLOW[target]
        elif name == "pointer.click" and target == "compose.export":
            self._write("auto/textures/sheets/usr001.png")
        elif name == "pointer.click" and target == "compose.save":
            self._write(("auto" if self.save_stays_in_auto else "mep") + "/textures/sheets/usr001.png")
            self.screen = "compose.save-dialog"
        elif name == "key.press" and args.get("key") == "Return" and self.screen == "compose.save-dialog":
            self.log_lines.append("saved: promoted to mep/textures/sheets" if not self.save_stays_in_auto else "saved")

    def _write(self, rel):
        p = self.out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"sheet")

    def observe(self, name, args):
        return {"ui.screen": self.screen, "ui.focused": self.focus}[name]
